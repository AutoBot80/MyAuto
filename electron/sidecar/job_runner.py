"""
One-shot Playwright job runner for the Electron desktop app.

Reads a JSON job from stdin, writes one JSON object to stdout, exits.
Logs to ``{SAATHI_BASE_DIR}/logs/sidecar.log`` (default ``D:\\Saathi\\logs``).

DB operations are proxied to the cloud API via HTTP — the sidecar never connects
to the database directly. The Electron client passes ``api_url`` and ``jwt`` in
every job payload so the sidecar can authenticate against the same backend.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
import urllib.request
import urllib.error
from pathlib import Path


def _setup_logging(saathi_base: Path) -> None:
    log_dir = saathi_base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sidecar.log"
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(sh)


def _repo_backend() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "backend"
    return Path(__file__).resolve().parents[2] / "backend"


def _bootstrap_imports(saathi_base: str) -> None:
    os.environ["SAATHI_BASE_DIR"] = saathi_base
    saathi_path = Path(saathi_base)
    env_file = saathi_path / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file)
    backend = _repo_backend()
    sys.path.insert(0, str(backend))
    be_env = backend / ".env"
    if be_env.is_file():
        from dotenv import load_dotenv

        load_dotenv(be_env)


# ---------------------------------------------------------------------------
# Cloud API HTTP helper
# ---------------------------------------------------------------------------


def _api_post(api_url: str, jwt: str, path: str, body: dict, timeout: int = 120) -> dict:
    """POST JSON to the cloud API and return the parsed response dict."""
    url = f"{api_url.rstrip('/')}{path}"
    data = json.dumps(body, default=str).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = ""
        try:
            detail = json.loads(body_text).get("detail", "")
        except Exception:
            detail = body_text[:500]
        raise RuntimeError(f"API {path} returned {exc.code}: {detail}") from exc


def _require_api_credentials(params: dict) -> tuple[str, str]:
    api_url = (params.get("api_url") or "").strip()
    jwt = (params.get("jwt") or "").strip()
    if not api_url or not jwt:
        raise ValueError(
            "api_url and jwt are required in the sidecar payload. "
            "Ensure the Electron client passes these from the logged-in session."
        )
    return api_url, jwt


# ---------------------------------------------------------------------------
# Warm browser (DMS / Vahan) — no DB needed, runs purely local
# ---------------------------------------------------------------------------


def _dispatch_warm_browser(params: dict) -> dict:
    from app.config import DMS_BASE_URL
    from app.services.fill_hero_dms_service import warm_dms_browser_session

    dms_base = (params.get("dms_base_url") or DMS_BASE_URL or "").strip()
    return warm_dms_browser_session(dms_base)


def _dispatch_warm_vahan(params: dict) -> dict:
    from app.services.fill_rto_service import warm_vahan_browser_session

    return warm_vahan_browser_session()


# ---------------------------------------------------------------------------
# DMS Create Invoice — PRE (API) → Playwright (local) → POST (API)
# ---------------------------------------------------------------------------


def _dispatch_fill_dms(params: dict) -> dict:
    api_url, jwt = _require_api_credentials(params)

    resolve_body = {
        "staging_id": params.get("staging_id"),
        "staging_payload": params.get("staging_payload"),
        "customer_id": params.get("customer_id"),
        "vehicle_id": params.get("vehicle_id"),
        "subfolder": params.get("subfolder"),
        "dealer_id": params.get("dealer_id"),
    }
    ctx = _api_post(api_url, jwt, "/sidecar/dms/resolve", resolve_body)

    dms_values = ctx["dms_values"]
    staging_payload = ctx.get("staging_payload")
    dms_base_url = ctx["dms_base_url"]

    # Use LOCAL paths (SAATHI_BASE_DIR on dealer PC), not server-returned Linux paths.
    from app.config import get_uploads_dir, get_ocr_output_dir
    dealer_id = params.get("dealer_id") or int(os.getenv("DEALER_ID", "100001"))
    uploads_dir = get_uploads_dir(int(dealer_id))
    ocr_output_dir = get_ocr_output_dir(int(dealer_id))

    from app.services.fill_hero_dms_service import run_fill_dms_only, dms_automation_is_real_siebel
    from app.services.handle_browser_opening import get_or_open_site_page

    if not dms_automation_is_real_siebel():
        return {"error": "DMS_MODE must be real/siebel on the server.", "vehicle": {}, "pdfs_saved": []}

    subfolder = dms_values.get("subfolder") or params.get("subfolder") or ""
    subfolder_path = uploads_dir / subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)

    from app.services.fill_hero_dms_service import (
        _install_playwright_js_dialog_handler,
        _run_fill_dms_real_siebel_playwright,
        _write_data_from_dms,
    )

    result: dict = {
        "vehicle": {},
        "pdfs_saved": [],
        "error": None,
        "dms_milestones": [],
        "dms_step_messages": [],
    }
    page = None
    try:
        page, open_error = get_or_open_site_page(dms_base_url, "DMS", require_login_on_open=True)
        if page is None:
            result["error"] = open_error
            return result

        _install_playwright_js_dialog_handler(page)
        _run_fill_dms_real_siebel_playwright(
            page,
            dms_values,
            subfolder,
            ocr_output_dir,
            params.get("customer_id"),
            params.get("vehicle_id"),
            result,
        )
    except Exception as e:
        result["error"] = str(e)
        logging.warning("fill_dms sidecar: %s", e)

    try:
        _write_data_from_dms(
            ocr_output_dir, subfolder,
            dms_values.get("customer_export") or {},
            result.get("vehicle") or {},
        )
    except Exception as e:
        result["error"] = (result.get("error") or "") + f"; DMS file write: {e!s}"

    commit_body = {
        "staging_id": params.get("staging_id"),
        "staging_payload": staging_payload,
        "scraped_vehicle": result.get("vehicle") or {},
        "dealer_id": params.get("dealer_id"),
        "customer_id": result.get("customer_id") or params.get("customer_id"),
        "vehicle_id": result.get("vehicle_id") or params.get("vehicle_id"),
    }
    try:
        commit_resp = _api_post(api_url, jwt, "/sidecar/dms/commit", commit_body)
        if commit_resp.get("committed_customer_id"):
            result["committed_customer_id"] = commit_resp["committed_customer_id"]
        if commit_resp.get("committed_vehicle_id"):
            result["committed_vehicle_id"] = commit_resp["committed_vehicle_id"]
        if commit_resp.get("error"):
            result["error"] = commit_resp["error"]
    except Exception as exc:
        logging.warning("fill_dms sidecar commit: %s", exc)
        result["error"] = (result.get("error") or "") + f"; Commit: {exc!s}"

    return result


# ---------------------------------------------------------------------------
# Insurance — PRE (API) → Playwright (local) → POST (API)
# ---------------------------------------------------------------------------


def _dispatch_fill_insurance(params: dict) -> dict:
    api_url, jwt = _require_api_credentials(params)

    resolve_body = {
        "staging_id": params.get("staging_id"),
        "customer_id": params.get("customer_id"),
        "vehicle_id": params.get("vehicle_id"),
        "subfolder": params.get("subfolder"),
        "dealer_id": params.get("dealer_id"),
    }
    ctx = _api_post(api_url, jwt, "/sidecar/insurance/resolve", resolve_body)

    cached_values = ctx["insurance_fill_values"]
    cid = ctx["customer_id"]
    vid = ctx["vehicle_id"]
    subfolder = ctx["subfolder"]
    insurance_base_url = ctx["insurance_base_url"]
    staging_payload = ctx.get("staging_payload")
    staging_id = ctx.get("staging_id")

    # Use LOCAL paths, not server-returned Linux paths.
    from app.config import get_ocr_output_dir
    dealer_id = params.get("dealer_id") or int(os.getenv("DEALER_ID", "100001"))
    ocr_dir = get_ocr_output_dir(int(dealer_id))

    # Monkey-patch DB-dependent functions so pre_process / main_process can run
    # without DATABASE_URL. The sidecar delegates all DB writes to /sidecar/insurance/commit.
    import app.services.insurance_form_values as _ifv
    import app.services.fill_hero_insurance_service as _fhi
    import app.services.add_sales_commit_service as _cs

    _original_build_ifv = _ifv.build_insurance_fill_values
    _original_build_fhi = _fhi.build_insurance_fill_values

    def _build_cached(*_a, **_kw):
        return dict(cached_values)
    _ifv.build_insurance_fill_values = _build_cached
    _fhi.build_insurance_fill_values = _build_cached

    _original_insert_cs = _cs.insert_insurance_master_after_gi
    _original_update_cs = _cs.update_insurance_master_policy_after_issue
    _original_insert_fhi = _fhi.insert_insurance_master_after_gi
    _original_update_fhi = _fhi.update_insurance_master_policy_after_issue
    _captured_insert_args: dict = {}
    _captured_update_args: dict = {}

    def _noop_insert(*_a, **kw):
        _captured_insert_args.update(kw)
    def _noop_update(*_a, **kw):
        _captured_update_args.update(kw)
    _cs.insert_insurance_master_after_gi = _noop_insert
    _cs.update_insurance_master_policy_after_issue = _noop_update
    _fhi.insert_insurance_master_after_gi = _noop_insert
    _fhi.update_insurance_master_policy_after_issue = _noop_update

    try:
        from app.services.fill_hero_insurance_service import (
            pre_process,
            main_process,
            post_process,
        )

        pre = pre_process(
            insurance_base_url=insurance_base_url or None,
            customer_id=cid,
            vehicle_id=vid,
            subfolder=subfolder,
            ocr_output_dir=ocr_dir,
            staging_payload=staging_payload,
            dealer_id=params.get("dealer_id"),
        )
        main = main_process(
            pre_result=pre,
            customer_id=cid,
            vehicle_id=vid,
            subfolder=subfolder,
            ocr_output_dir=ocr_dir,
            staging_payload=staging_payload,
            staging_id=staging_id,
            dealer_id=params.get("dealer_id"),
        )
        result = post_process(pre_result=pre, main_result=main)
    finally:
        _ifv.build_insurance_fill_values = _original_build_ifv
        _fhi.build_insurance_fill_values = _original_build_fhi
        _cs.insert_insurance_master_after_gi = _original_insert_cs
        _cs.update_insurance_master_policy_after_issue = _original_update_cs
        _fhi.insert_insurance_master_after_gi = _original_insert_fhi
        _fhi.update_insurance_master_policy_after_issue = _original_update_fhi

    if result.get("success"):
        commit_body = {
            "customer_id": cid,
            "vehicle_id": vid,
            "fill_values": cached_values,
            "staging_payload": staging_payload,
            "preview_scrape": _captured_insert_args.get("preview_scrape"),
            "post_issue_scrape": _captured_update_args.get("scrape"),
            "staging_id": staging_id,
            "dealer_id": params.get("dealer_id"),
            "subfolder": subfolder,
        }
        try:
            commit_resp = _api_post(api_url, jwt, "/sidecar/insurance/commit", commit_body)
            if commit_resp.get("error"):
                result["error"] = commit_resp["error"]
        except Exception as exc:
            logging.warning("fill_insurance sidecar commit: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Vahan RTO batch — claim (API) → per-row Playwright (local) → result (API)
# ---------------------------------------------------------------------------


def _dispatch_fill_vahan_batch(params: dict) -> dict:
    api_url, jwt = _require_api_credentials(params)

    claim_body = {
        "dealer_id": params.get("dealer_id"),
        "limit": params.get("limit", 7),
    }
    claim_resp = _api_post(api_url, jwt, "/sidecar/vahan/claim-batch", claim_body)

    rows = claim_resp.get("rows") or []
    session_id = claim_resp["session_id"]
    worker_id = claim_resp["worker_id"]

    if not rows:
        return {"success": True, "total": 0, "completed": 0, "failed": 0, "message": "No queued rows"}

    from app.services.fill_rto_service import fill_rto_row

    completed_count = 0
    failed_count = 0

    for row in rows:
        rto_queue_id = int(row["rto_queue_id"])
        sales_id = int(row["sales_id"])

        try:
            batch_result = fill_rto_row(row)
            rto_app_id = (batch_result.get("rto_application_id") or "").strip() or None
            done = bool(batch_result.get("completed"))
            if not done:
                raise RuntimeError("Vahan fill did not reach the target checkpoint")

            _api_post(api_url, jwt, "/sidecar/vahan/row-result", {
                "rto_queue_id": rto_queue_id,
                "sales_id": sales_id,
                "session_id": session_id,
                "worker_id": worker_id,
                "status": "Completed",
                "rto_application_id": rto_app_id,
                "rto_payment_amount": batch_result.get("rto_payment_amount"),
            })
            completed_count += 1
        except Exception as exc:
            logging.warning("vahan sidecar row %s failed: %s", rto_queue_id, exc)
            _api_post(api_url, jwt, "/sidecar/vahan/row-result", {
                "rto_queue_id": rto_queue_id,
                "sales_id": sales_id,
                "session_id": session_id,
                "worker_id": worker_id,
                "status": "Failed",
                "error": str(exc)[:2000],
            })
            failed_count += 1

    return {
        "success": failed_count == 0,
        "total": len(rows),
        "completed": completed_count,
        "failed": failed_count,
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(payload: dict) -> dict:
    job_type = payload.get("type") or payload.get("job")
    if not job_type:
        return {"success": False, "error": "Missing type"}
    if job_type == "ping":
        return {"success": True, "data": {"pong": True}}

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    # Propagate top-level api_url / jwt into params for convenience
    if "api_url" not in params and payload.get("api_url"):
        params["api_url"] = payload["api_url"]
    if "jwt" not in params and payload.get("jwt"):
        params["jwt"] = payload["jwt"]

    if job_type == "warm_browser":
        data = _dispatch_warm_browser(params)
        return {"success": True, "data": data}
    if job_type == "warm_vahan":
        data = _dispatch_warm_vahan(params)
        return {"success": True, "data": data}
    if job_type == "fill_dms":
        data = _dispatch_fill_dms(params)
        return {"success": True, "data": data}
    if job_type == "fill_insurance":
        data = _dispatch_fill_insurance(params)
        return {"success": True, "data": data}
    if job_type == "fill_vahan_batch":
        data = _dispatch_fill_vahan_batch(params)
        return {"success": True, "data": data}
    return {"success": False, "error": f"Unknown job type: {job_type}"}


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"Invalid JSON: {e}"}))
        sys.exit(2)

    saathi = str(payload.get("saathi_base_dir") or os.environ.get("SAATHI_BASE_DIR") or r"D:\Saathi")
    saathi_path = Path(saathi)
    saathi_path.mkdir(parents=True, exist_ok=True)
    _setup_logging(saathi_path)

    job_type = payload.get("type") or payload.get("job")
    try:
        if job_type == "ping":
            out = dispatch(payload)
            print(json.dumps(out, default=str))
            sys.exit(0 if out.get("success") else 1)

        _bootstrap_imports(saathi)
        logging.info("Job start: %s", job_type)
        out = dispatch(payload)
        logging.info("Job end: success=%s", out.get("success"))
        print(json.dumps(out, default=str))
        sys.exit(0 if out.get("success") else 1)
    except Exception:
        logging.exception("Job failed")
        print(
            json.dumps(
                {
                    "success": False,
                    "error": traceback.format_exc(),
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
