"""
Proxy endpoints for the Electron sidecar.

The sidecar runs Playwright locally on the dealer PC but has no database access.
These endpoints let the sidecar call the cloud API for all DB operations:
  - ``/sidecar/dms/resolve``   → load staging / masters, build DMS fill values
  - ``/sidecar/dms/commit``    → persist masters + finalize staging after Playwright
  - ``/sidecar/insurance/resolve`` → load insurance fill values
  - ``/sidecar/insurance/commit``  → insert/update insurance_master
  - ``/sidecar/vahan/claim-batch`` → claim RTO queue rows for batch processing
  - ``/sidecar/vahan/row-result``  → report per-row result (completed/failed/pending)
  - ``/sidecar/upload-artifacts`` → multipart upload of one file into uploads or ocr tree (syncs to S3)

All endpoints are JWT-protected via ``get_principal`` / ``resolve_dealer_id``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import (
    DMS_BASE_URL,
    INSURANCE_BASE_URL,
    VAHAN_BASE_URL,
    DMS_PLAYWRIGHT_HEADED,
    PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.db import get_connection
from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.services.dealer_storage import sync_ocr_file_to_s3, sync_uploads_file_to_s3

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sidecar", tags=["sidecar-proxy"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class DmsResolveRequest(BaseModel):
    staging_id: str | None = None
    staging_payload: dict[str, Any] | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    subfolder: str | None = None
    dealer_id: int | None = None


class DmsResolveResponse(BaseModel):
    dms_values: dict[str, Any]
    staging_payload: dict[str, Any] | None = None
    dms_base_url: str
    headed: bool
    remote_debug_port: int
    uploads_dir: str
    ocr_output_dir: str


class DmsCommitRequest(BaseModel):
    staging_id: str | None = None
    staging_payload: dict[str, Any] | None = None
    scraped_vehicle: dict[str, Any] = {}
    dealer_id: int | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    sales_id: int | None = None
    masters_committed_via_siebel: bool | None = None


class DmsCommitResponse(BaseModel):
    committed_customer_id: int | None = None
    committed_vehicle_id: int | None = None
    sales_id: int | None = None
    error: str | None = None


class InsuranceResolveRequest(BaseModel):
    staging_id: str | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    subfolder: str | None = None
    dealer_id: int | None = None


class InsuranceResolveResponse(BaseModel):
    insurance_fill_values: dict[str, Any]
    customer_id: int
    vehicle_id: int
    subfolder: str
    insurance_base_url: str
    staging_payload: dict[str, Any] | None = None
    staging_id: str | None = None
    ocr_output_dir: str
    headed: bool
    remote_debug_port: int


class InsuranceCommitRequest(BaseModel):
    customer_id: int
    vehicle_id: int
    fill_values: dict[str, Any]
    staging_payload: dict[str, Any] | None = None
    preview_scrape: dict[str, Any] | None = None
    post_issue_scrape: dict[str, Any] | None = None
    staging_id: str | None = None
    dealer_id: int | None = None
    subfolder: str | None = None


class InsuranceCommitResponse(BaseModel):
    insurance_id: int | None = None
    error: str | None = None


class VahanClaimBatchRequest(BaseModel):
    dealer_id: int | None = None
    limit: int = 7


class VahanClaimBatchResponse(BaseModel):
    rows: list[dict[str, Any]]
    session_id: str
    worker_id: str
    vahan_base_url: str
    headed: bool
    remote_debug_port: int


class VahanRowResultRequest(BaseModel):
    rto_queue_id: int
    sales_id: int
    session_id: str
    worker_id: str
    status: str  # Completed | Failed | Pending
    rto_application_id: str | None = None
    rto_payment_amount: float | None = None
    error: str | None = None


class VahanRowResultResponse(BaseModel):
    ok: bool


class UploadArtifactResponse(BaseModel):
    ok: bool
    rel_path: str
    tree: str


def _sanitize_sidecar_rel_path(raw: str) -> str:
    """Reject path traversal; return posix relative path under dealer root."""
    p = (raw or "").strip().replace("\\", "/")
    parts = [x for x in p.split("/") if x and x != "."]
    if any(x == ".." for x in parts):
        raise HTTPException(status_code=400, detail="Invalid rel_path")
    return "/".join(parts)


# ---------------------------------------------------------------------------
# DMS endpoints
# ---------------------------------------------------------------------------


@router.post("/dms/resolve", response_model=DmsResolveResponse)
async def dms_resolve(
    req: DmsResolveRequest,
    principal: Principal = Depends(get_principal),
) -> DmsResolveResponse:
    did = resolve_dealer_id(principal, req.dealer_id)

    staging_payload = req.staging_payload
    if not staging_payload and req.staging_id:
        from app.repositories.add_sales_staging import fetch_staging_payload

        staging_payload = fetch_staging_payload(req.staging_id, did)
        if not staging_payload:
            raise HTTPException(status_code=404, detail="Staging row not found or not accessible")

    from app.services.fill_hero_dms_service import (
        _build_dms_fill_values,
        _ensure_hero_oem_for_fill_dms,
    )

    try:
        _ensure_hero_oem_for_fill_dms(did)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        dms_values = _build_dms_fill_values(
            req.customer_id,
            req.vehicle_id,
            req.subfolder,
            staging_payload=staging_payload,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Serialise: drop the nested ``row`` dict's non-serialisable values (all are str/None).
    row = dms_values.get("row")
    if row and isinstance(row, dict):
        dms_values["row"] = {k: (str(v) if v is not None else None) for k, v in row.items()}

    return DmsResolveResponse(
        dms_values=dms_values,
        staging_payload=staging_payload,
        dms_base_url=(DMS_BASE_URL or "").strip(),
        headed=bool(DMS_PLAYWRIGHT_HEADED),
        remote_debug_port=int(PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333),
        uploads_dir=str(get_uploads_dir(did)),
        ocr_output_dir=str(get_ocr_output_dir(did)),
    )


@router.post("/dms/commit", response_model=DmsCommitResponse)
async def dms_commit(
    req: DmsCommitRequest,
    principal: Principal = Depends(get_principal),
) -> DmsCommitResponse:
    did = resolve_dealer_id(principal, req.dealer_id)
    sid = (req.staging_id or "").strip()
    sp = req.staging_payload
    scraped = req.scraped_vehicle or {}

    from app.services.add_sales_commit_service import finalize_staging_row_with_master_ids
    from app.services.fill_hero_dms_service import (
        _merge_staging_payload_with_scrape_for_commit,
        invoice_number_ready_for_master_commit,
    )
    from app.services.hero_dms_db_service import persist_staging_masters_after_invoice
    from app.repositories.add_sales_staging import merge_staging_payload_on_cursor

    cid_out: int | None = None
    vid_out: int | None = None
    sid_out: int | None = None
    error: str | None = None

    inv_ready = invoice_number_ready_for_master_commit(scraped)

    _siebel_done = req.masters_committed_via_siebel is True
    _have_ids = req.customer_id is not None and req.vehicle_id is not None

    if sp and sid and inv_ready and _siebel_done and _have_ids:
        try:
            merged = _merge_staging_payload_with_scrape_for_commit(sp, scraped)
            finalize_staging_row_with_master_ids(
                staging_id=sid,
                merged_payload=merged,
                customer_id=int(req.customer_id),
                vehicle_id=int(req.vehicle_id),
                sales_id=int(req.sales_id) if req.sales_id is not None else None,
            )
            cid_out = int(req.customer_id)
            vid_out = int(req.vehicle_id)
            if req.sales_id is not None:
                sid_out = int(req.sales_id)
        except Exception as exc:
            error = f"Database commit after DMS failed: {exc!s}"
            logger.warning("sidecar_proxy dms/commit (finalize staging only): %s", error)
    elif sp and sid and inv_ready:
        try:
            cid_out, vid_out = persist_staging_masters_after_invoice(
                staging_id=sid,
                staging_payload=sp,
                scraped_vehicle=scraped,
            )
        except Exception as exc:
            error = f"Database commit after DMS failed: {exc!s}"
            logger.warning("sidecar_proxy dms/commit: %s", error)

    if not error and sid and sp and cid_out is None and vid_out is None:
        cid_s = req.customer_id
        vid_s = req.vehicle_id
        if cid_s is not None and vid_s is not None:
            try:
                patch: dict[str, Any] = {"customer_id": int(cid_s), "vehicle_id": int(vid_s)}
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        merge_staging_payload_on_cursor(cur, sid, did, patch)
                    conn.commit()
                cid_out = int(cid_s)
                vid_out = int(vid_s)
            except Exception as exc:
                logger.warning("sidecar_proxy dms/commit patch: %s", exc)

    return DmsCommitResponse(
        committed_customer_id=cid_out,
        committed_vehicle_id=vid_out,
        sales_id=sid_out,
        error=error,
    )


# ---------------------------------------------------------------------------
# Insurance endpoints
# ---------------------------------------------------------------------------


@router.post("/insurance/resolve", response_model=InsuranceResolveResponse)
async def insurance_resolve(
    req: InsuranceResolveRequest,
    principal: Principal = Depends(get_principal),
) -> InsuranceResolveResponse:
    did = resolve_dealer_id(principal, req.dealer_id)

    staging_payload = None
    sid = (req.staging_id or "").strip()
    if sid:
        try:
            UUID(sid)
        except ValueError:
            raise HTTPException(status_code=400, detail="staging_id must be a valid UUID") from None
        from app.repositories.add_sales_staging import fetch_staging_payload

        staging_payload = fetch_staging_payload(sid, did)

    cid = req.customer_id
    vid = req.vehicle_id

    if staging_payload is not None:
        if cid is None:
            raw_c = staging_payload.get("customer_id")
            try:
                cid = int(raw_c) if raw_c is not None else None
            except (TypeError, ValueError):
                cid = None
        if vid is None:
            raw_v = staging_payload.get("vehicle_id")
            try:
                vid = int(raw_v) if raw_v is not None else None
            except (TypeError, ValueError):
                vid = None

    if staging_payload is not None and (cid is None or vid is None):
        from app.services.add_sales_natural_key_resolve import (
            natural_keys_from_staging_payload,
            resolve_customer_vehicle_ids_by_natural_keys,
        )
        from app.repositories.add_sales_staging import merge_staging_payload_on_cursor

        keys = natural_keys_from_staging_payload(staging_payload)
        if keys:
            ch, eng, mob = keys
            rk_cid, rk_vid = resolve_customer_vehicle_ids_by_natural_keys(ch, eng, mob)
            if rk_cid is not None and rk_vid is not None:
                cid, vid = rk_cid, rk_vid
                if sid:
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                merge_staging_payload_on_cursor(
                                    cur, sid, did, {"customer_id": int(cid), "vehicle_id": int(vid)}
                                )
                            conn.commit()
                    except Exception:
                        pass

    if cid is None or vid is None:
        raise HTTPException(
            status_code=400,
            detail="customer_id and vehicle_id are required (or resolvable from staging).",
        )

    subfolder = (req.subfolder or "").strip()
    if not subfolder and staging_payload:
        subfolder = (staging_payload.get("file_location") or "").strip()
    if not subfolder:
        raise HTTPException(status_code=400, detail="subfolder is required.")

    from app.services.insurance_form_values import build_insurance_fill_values

    ocr_dir = str(get_ocr_output_dir(did))
    try:
        values = build_insurance_fill_values(
            cid, vid, subfolder,
            ocr_output_dir=Path(ocr_dir),
            staging_payload=staging_payload,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return InsuranceResolveResponse(
        insurance_fill_values=values,
        customer_id=cid,
        vehicle_id=vid,
        subfolder=subfolder,
        insurance_base_url=(INSURANCE_BASE_URL or "").strip(),
        staging_payload=staging_payload,
        staging_id=sid or None,
        ocr_output_dir=ocr_dir,
        headed=bool(DMS_PLAYWRIGHT_HEADED),
        remote_debug_port=int(PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333),
    )


@router.post("/insurance/commit", response_model=InsuranceCommitResponse)
async def insurance_commit(
    req: InsuranceCommitRequest,
    principal: Principal = Depends(get_principal),
) -> InsuranceCommitResponse:
    did = resolve_dealer_id(principal, req.dealer_id)

    from app.services.add_sales_commit_service import (
        insert_insurance_master_after_gi,
        update_insurance_master_policy_after_issue,
    )

    error: str | None = None
    try:
        insert_insurance_master_after_gi(
            req.customer_id,
            req.vehicle_id,
            fill_values=req.fill_values,
            staging_payload=req.staging_payload,
            preview_scrape=req.preview_scrape,
            ocr_output_dir=req.subfolder,
            subfolder=req.subfolder,
            staging_id=req.staging_id,
            dealer_id=did,
        )
    except ValueError as exc:
        error = str(exc)
    except Exception as exc:
        error = f"Insurance master insert failed: {exc!s}"
        logger.warning("sidecar_proxy insurance/commit: %s", exc)

    if not error and req.post_issue_scrape:
        try:
            update_insurance_master_policy_after_issue(
                req.customer_id,
                req.vehicle_id,
                scrape=req.post_issue_scrape,
            )
        except Exception as exc:
            logger.warning("sidecar_proxy insurance/commit update: %s", exc)

    return InsuranceCommitResponse(error=error)


# ---------------------------------------------------------------------------
# Vahan endpoints
# ---------------------------------------------------------------------------


@router.post("/vahan/claim-batch", response_model=VahanClaimBatchResponse)
async def vahan_claim_batch(
    req: VahanClaimBatchRequest,
    principal: Principal = Depends(get_principal),
) -> VahanClaimBatchResponse:
    did = resolve_dealer_id(principal, req.dealer_id)

    from app.repositories import rto_payment_details as repo
    from uuid import uuid4

    session_id = f"sidecar-rto-{uuid4().hex}"
    worker_id = f"sidecar-dealer-{did}:{session_id}"

    rows = repo.claim_oldest_batch(
        dealer_id=did,
        processing_session_id=session_id,
        worker_id=worker_id,
        limit=max(1, min(int(req.limit or 7), 7)),
    )
    serialised = []
    for r in rows:
        serialised.append({k: (str(v) if v is not None else None) for k, v in r.items()})

    return VahanClaimBatchResponse(
        rows=serialised,
        session_id=session_id,
        worker_id=worker_id,
        vahan_base_url=(VAHAN_BASE_URL or "").strip(),
        headed=bool(DMS_PLAYWRIGHT_HEADED),
        remote_debug_port=int(PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333),
    )


@router.post("/vahan/row-result", response_model=VahanRowResultResponse)
async def vahan_row_result(
    req: VahanRowResultRequest,
    principal: Principal = Depends(get_principal),
) -> VahanRowResultResponse:
    from app.repositories import rto_payment_details as repo

    status = (req.status or "").strip()
    if status == "Completed":
        repo.mark_batch_row_completed(
            req.rto_queue_id,
            req.sales_id,
            req.session_id,
            req.worker_id,
            rto_application_id=req.rto_application_id,
            rto_payment_amount=req.rto_payment_amount,
        )
    elif status == "Failed":
        repo.mark_batch_row_failed(
            req.rto_queue_id, req.session_id, req.worker_id, req.error or "Unknown error"
        )
    elif status == "Pending":
        repo.mark_batch_row_pending(
            req.rto_queue_id, req.session_id, req.worker_id, req.error
        )
    else:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    return VahanRowResultResponse(ok=True)


# ---------------------------------------------------------------------------
# Artifact upload (sidecar → EC2 disk + optional S3 sync)
# ---------------------------------------------------------------------------


@router.post("/upload-artifacts", response_model=UploadArtifactResponse)
async def upload_artifacts(
    dealer_id: int = Form(),
    tree: str = Form(),
    rel_path: str = Form(),
    file: UploadFile = File(),
    principal: Principal = Depends(get_principal),
) -> UploadArtifactResponse:
    """
    Multipart upload of one file from the dealer PC Playwright run. Writes under
    ``get_uploads_dir`` or ``get_ocr_output_dir`` and syncs to S3 when configured.
    """
    did = resolve_dealer_id(principal, dealer_id)
    t = (tree or "").strip().lower()
    if t not in ("uploads", "ocr"):
        raise HTTPException(status_code=400, detail='tree must be "uploads" or "ocr"')
    safe_rel = _sanitize_sidecar_rel_path(rel_path)
    root = get_uploads_dir(did) if t == "uploads" else get_ocr_output_dir(did)
    dest = root / safe_rel
    try:
        dest = dest.resolve()
        root_res = root.resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        dest.relative_to(root_res)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Path escapes dealer directory") from e

    dest.parent.mkdir(parents=True, exist_ok=True)
    body = await file.read()
    dest.write_bytes(body)
    if t == "uploads":
        sync_uploads_file_to_s3(did, dest)
    else:
        sync_ocr_file_to_s3(did, dest)
    return UploadArtifactResponse(ok=True, rel_path=safe_rel, tree=t)


# ---------------------------------------------------------------------------
# Subdealer Challan endpoints
# ---------------------------------------------------------------------------


class SubdealerChallanResolveRequest(BaseModel):
    challan_batch_id: str
    dealer_id: int | None = None


class SubdealerChallanResolveResponse(BaseModel):
    challan_batch_id: str
    from_dealer_id: int
    to_dealer_id: int
    challan_date: str | None
    challan_book_num: str | None
    detail_rows: list[dict[str, Any]]
    dms_base_url: str
    headed: bool
    remote_debug_port: int


class SubdealerChallanCommitRequest(BaseModel):
    challan_batch_id: str
    dealer_id: int | None = None
    detail_results: list[dict[str, Any]]
    order_result: dict[str, Any] | None = None


class SubdealerChallanCommitResponse(BaseModel):
    ok: bool
    error: str | None = None
    challan_id: int | None = None


@router.post("/subdealer-challan/resolve", response_model=SubdealerChallanResolveResponse)
async def subdealer_challan_resolve(
    req: SubdealerChallanResolveRequest,
    principal: Principal = Depends(get_principal),
) -> SubdealerChallanResolveResponse:
    """
    Return batch data for local sidecar processing.
    The sidecar runs Playwright locally and calls /commit with results.
    """
    from uuid import UUID as PyUUID

    from app.repositories import challan_details_staging as detail_repo

    did = resolve_dealer_id(principal, req.dealer_id)
    try:
        bid = PyUUID(req.challan_batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid challan_batch_id") from e

    rows = detail_repo.fetch_batch_rows(bid)
    if not rows:
        raise HTTPException(status_code=404, detail="No staging rows for this batch")

    from_dealer_id = int(rows[0]["from_dealer_id"])
    if from_dealer_id != did and not principal.admin:
        raise HTTPException(status_code=403, detail="Not authorized for this batch")

    to_dealer_id = int(rows[0]["to_dealer_id"])
    challan_date = rows[0].get("challan_date")
    challan_book_num = rows[0].get("challan_book_num")

    detail_rows = [
        {
            "challan_staging_id": int(r["challan_staging_id"]),
            "raw_chassis": (r.get("raw_chassis") or "").strip(),
            "raw_engine": (r.get("raw_engine") or "").strip(),
            "status": (r.get("status") or "").strip(),
            "to_dealer_id": int(r["to_dealer_id"]),
        }
        for r in rows
    ]

    return SubdealerChallanResolveResponse(
        challan_batch_id=str(bid),
        from_dealer_id=from_dealer_id,
        to_dealer_id=to_dealer_id,
        challan_date=str(challan_date).strip() if challan_date else None,
        challan_book_num=str(challan_book_num).strip() if challan_book_num else None,
        detail_rows=detail_rows,
        dms_base_url=(DMS_BASE_URL or "").strip(),
        headed=bool(DMS_PLAYWRIGHT_HEADED),
        remote_debug_port=int(PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333),
    )


@router.post("/subdealer-challan/commit", response_model=SubdealerChallanCommitResponse)
async def subdealer_challan_commit(
    req: SubdealerChallanCommitRequest,
    principal: Principal = Depends(get_principal),
) -> SubdealerChallanCommitResponse:
    """
    Persist sidecar results: update detail statuses, inventory, and optionally commit order.
    """
    from uuid import UUID as PyUUID

    from app.repositories import challan_details_staging as detail_repo
    from app.repositories import challan_master_staging as master_repo
    from app.repositories.vehicle_inventory import upsert_from_prepare_vehicle_scrape

    did = resolve_dealer_id(principal, req.dealer_id)
    try:
        bid = PyUUID(req.challan_batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid challan_batch_id") from e

    rows = detail_repo.fetch_batch_rows(bid)
    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")

    from_dealer_id = int(rows[0]["from_dealer_id"])
    if from_dealer_id != did and not principal.admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    for dr in req.detail_results:
        sid = int(dr.get("challan_staging_id", 0))
        status = (dr.get("status") or "").strip()
        error = dr.get("error")
        scraped = dr.get("scraped_vehicle") or {}
        to_dealer_id = int(dr.get("to_dealer_id", 0))

        if status == "Ready" and scraped:
            try:
                iid = upsert_from_prepare_vehicle_scrape(to_dealer_id=to_dealer_id, vehicle=scraped)
                detail_repo.update_detail_status(
                    sid,
                    status="Ready",
                    last_error=None,
                    vehicle_inventory_id=iid,
                )
            except Exception as exc:
                detail_repo.update_detail_status(sid, status="Failed", last_error=str(exc)[:2000])
        elif status == "Failed":
            detail_repo.update_detail_status(sid, status="Failed", last_error=(error or "")[:2000])

    master_repo.refresh_prepared_count(bid)

    challan_id = None
    order_error = None
    if req.order_result:
        challan_id = req.order_result.get("challan_id")
        order_error = req.order_result.get("error")
        if order_error:
            master_repo.set_invoice_state(bid, invoice_status="Failed", invoice_complete=False)
        elif challan_id:
            from app.services.add_subdealer_challan_commit_service import commit_challan_masters

            try:
                commit_challan_masters(bid, int(challan_id))
            except Exception as exc:
                order_error = str(exc)[:2000]
                master_repo.set_invoice_state(bid, invoice_status="Failed", invoice_complete=False)

    master_repo.touch_last_run_at(bid)

    return SubdealerChallanCommitResponse(
        ok=order_error is None,
        error=order_error,
        challan_id=challan_id,
    )


# ---------------------------------------------------------------------------
# Script sync (hot-reload backend/app for thin sidecar)
# ---------------------------------------------------------------------------


@router.get("/scripts/version")
def scripts_version() -> dict:
    """Return semver + git commit of the running server code. Cheap, called often."""
    from app.version import BACKEND_SEMVER, GIT_COMMIT_SHORT

    return {"version": BACKEND_SEMVER, "git_commit": GIT_COMMIT_SHORT}


@router.get("/scripts/bundle")
def scripts_bundle(
    principal: Principal = Depends(get_principal),
):
    """
    Stream a zip of ``backend/app/**/*.py`` so the sidecar can hot-sync
    automation scripts without a full Electron rebuild.
    """
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    from app.version import GIT_COMMIT_SHORT

    app_dir = Path(__file__).resolve().parent.parent  # backend/app/
    backend_root = app_dir.parent
    version_file = backend_root / "VERSION"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in app_dir.rglob("*.py"):
            arc_name = f"backend/app/{f.relative_to(app_dir)}"
            zf.write(f, arc_name)
        if version_file.is_file():
            zf.write(version_file, "backend/VERSION")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "X-Git-Commit": GIT_COMMIT_SHORT,
            "Content-Disposition": "attachment; filename=backend_app.zip",
        },
    )
