"""
Hero DMS: persist ``customer_master`` / ``vehicle_master`` / ``sales_master`` after Siebel Fill DMS.

Single-transaction semantics: one commit for the three masters on the Siebel-only insert path
(``insert_dms_masters_from_siebel_scrape`` also sets ``vehicle_inventory_master.sold_date`` when
chassis/engine match full scraped values, before the same commit);
staging path uses ``commit_staging_masters_and_finalize_row`` (masters + staging row in one txn).

``insert_dms_masters_from_siebel_scrape`` is implemented in ``fill_hero_dms_service``; this module
invokes it via lazy import to avoid circular imports at module load time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.config import (
    DATABASE_URL,
    DEALER_ID,
    HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE,
    HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER,
)

logger = logging.getLogger(__name__)


def persist_masters_after_create_order(
    out: dict[str, Any],
    dms_values: dict[str, Any],
    *,
    order_scraped: dict[str, Any],
    preexisting_customer_id: int | None,
    preexisting_vehicle_id: int | None,
    dealer_id: int | None = None,
    log_fp: Any = None,
    note: Callable[[str], None],
) -> None:
    """
    After a successful ``create_order`` scrape, optionally INSERT the three masters (Siebel-only path)
    when Invoice# is present and policy allows. Mutates ``out``; may set ``out[\"error\"]``.
    """
    from app.services.fill_hero_dms_service import (
        append_playwright_dms_masters_committed_log,
        insert_dms_masters_from_siebel_scrape,
        invoice_number_ready_for_master_commit,
    )
    from app.services.hero_dms_shared_utilities import _write_playwright_dms_masters_section

    if not order_scraped:
        return

    _veh0 = out.get("vehicle")
    if isinstance(_veh0, dict):
        _veh0 = dict(_veh0)
        if not str(_veh0.get("invoice_number") or "").strip() and not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
            _veh0["invoice_number"] = HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER
        out["vehicle"] = _veh0

    did = int(dealer_id) if dealer_id is not None else int(DEALER_ID)

    _collate_fields = None
    _cm = out.get("dms_customer_master_collated")
    if isinstance(_cm, dict):
        _cf = _cm.get("fields")
        if isinstance(_cf, dict) and len(_cf) > 0:
            _collate_fields = _cf

    out["dms_sales_master_prep"] = {
        "customer_id": preexisting_customer_id,
        "vehicle_id": preexisting_vehicle_id,
        "dealer_id": did,
        "order_number": str((out.get("vehicle") or {}).get("order_number") or ""),
        "invoice_number": str((out.get("vehicle") or {}).get("invoice_number") or ""),
        "enquiry_number": str((out.get("vehicle") or {}).get("enquiry_number") or ""),
    }
    _atomic_ok = False
    _atomic_err: str | None = None
    _deferred_no_local_db = False
    _cid_out: int | None = None
    _vid_out: int | None = None
    _sid_out: int | None = None

    _inv_ready = invoice_number_ready_for_master_commit(out.get("vehicle"))
    if (
        _inv_ready
        and preexisting_customer_id is None
        and preexisting_vehicle_id is None
    ):
        if not DATABASE_URL:
            # Electron sidecar has no DB; cloud /sidecar/dms/commit persists masters.
            note(
                "Master INSERT skipped locally (no DATABASE_URL); "
                "cloud /sidecar/dms/commit will persist after this run."
            )
            _deferred_no_local_db = True
        else:
            try:
                _cid_out, _vid_out, _sid_out = insert_dms_masters_from_siebel_scrape(
                    dms_values,
                    out.get("vehicle") or {},
                    collated_customer_fields=_collate_fields,
                    dealer_id=did,
                )
                _atomic_ok = True
                if _cid_out is not None:
                    out["customer_id"] = _cid_out
                if _vid_out is not None:
                    out["vehicle_id"] = _vid_out
                if _sid_out is not None:
                    out["sales_id"] = _sid_out
            except Exception as _p_exc:
                _atomic_err = str(_p_exc)
                logger.warning("siebel_dms: master INSERT after Create Invoice failed: %s", _p_exc)
    elif _inv_ready and (preexisting_customer_id is not None or preexisting_vehicle_id is not None):
        note(
            "Invoice# present but customer_id/vehicle_id already set — skipping DB "
            "(policy: no UPDATE during Siebel; refresh ids from DB separately if needed)."
        )
    else:
        note(
            "Invoice# not in scrape yet (Create Invoice not completed or not scraped) — "
            "master INSERT deferred; values are in memory and the Playwright DMS execution log only."
        )
    _prep = dict(out.get("dms_sales_master_prep") or {})
    _prep["customer_id"] = out.get("customer_id")
    _prep["vehicle_id"] = out.get("vehicle_id")
    _prep["sales_id"] = out.get("sales_id")
    out["dms_sales_master_prep"] = _prep
    out["dms_master_persist_committed"] = _atomic_ok
    _attach_ex = str(
        (out.get("vehicle") or {}).get("vehicle_price")
        or (out.get("vehicle") or {}).get("vehicle_ex_showroom_cost")
        or ""
    )
    _log_atomic_err = _atomic_err
    if not _log_atomic_err and _deferred_no_local_db:
        _log_atomic_err = "deferred to server commit (no DATABASE_URL)"
    _write_playwright_dms_masters_section(
        log_fp,
        attach_ex_showroom=_attach_ex,
        sales_master_prep=out.get("dms_sales_master_prep") or {},
        atomic_db_committed=_atomic_ok,
        atomic_db_error=_log_atomic_err,
    )
    if _atomic_ok and _cid_out is not None and _vid_out is not None and log_fp is not None:
        try:
            append_playwright_dms_masters_committed_log(
                log_fp.name,
                customer_id=int(_cid_out),
                vehicle_id=int(_vid_out),
            )
        except Exception as _snap_exc:
            logger.warning("siebel_dms: Playwright DMS masters snapshot append failed: %s", _snap_exc)
    if _atomic_err:
        out["error"] = f"Siebel: database persist failed after create_order: {_atomic_err}"


def persist_staging_masters_after_invoice(
    *,
    staging_id: str,
    staging_payload: dict[str, Any],
    scraped_vehicle: dict[str, Any],
) -> tuple[int, int]:
    """
    Merge staging payload with post-DMS scrape and commit masters + staging row (single transaction).
    """
    from app.services.add_sales_commit_service import commit_staging_masters_and_finalize_row
    from app.services.fill_hero_dms_service import _merge_staging_payload_with_scrape_for_commit

    merged_pl = _merge_staging_payload_with_scrape_for_commit(staging_payload, scraped_vehicle)
    return commit_staging_masters_and_finalize_row(
        staging_id=staging_id,
        merged_payload=merged_pl,
    )
