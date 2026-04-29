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
import time
import traceback
import urllib.error
import urllib.request
import uuid
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


_is_frozen = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
_scripts_synced = False


def _repo_backend() -> Path:
    if _is_frozen:
        return Path(sys._MEIPASS) / "backend"
    return Path(__file__).resolve().parents[2] / "backend"


def _script_cache_dir(saathi_base: str) -> Path:
    return Path(saathi_base) / "script_cache"


def _load_dotenv_safe(env_path: Path) -> None:
    """
    Load a .env file; tolerate UTF-8 BOM, plain UTF-8, and Windows ANSI (cp1252).

    Users sometimes save ``Saathi\\.env`` from Notepad as "ANSI" or paste Word
    punctuation — byte 0x97 etc. — which raises UnicodeDecodeError with the
    default UTF-8-only load.
    """
    if not env_path.is_file():
        return
    from dotenv import load_dotenv

    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            load_dotenv(env_path, encoding=enc)
            if enc == "cp1252":
                logging.warning(
                    "Loaded %s as Windows-1252 (ANSI); re-save as UTF-8 (Notepad: Save As → UTF-8) to avoid issues.",
                    env_path,
                )
            return
        except UnicodeDecodeError:
            continue
    load_dotenv(env_path, encoding="latin-1")


def _sync_scripts(api_url: str, jwt: str, saathi_base: str) -> None:
    """
    In frozen (packaged) mode, check whether the local script cache matches
    the server's git commit.  If stale or absent, download the bundle zip and
    extract it.  In dev mode this is a no-op.
    """
    global _scripts_synced
    if _scripts_synced or not _is_frozen:
        return
    _scripts_synced = True

    cache = _script_cache_dir(saathi_base)
    version_file = cache / ".version"
    cached_commit = ""
    if version_file.is_file():
        try:
            cached_commit = version_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    base = api_url.rstrip("/")
    server_commit = ""
    try:
        req = urllib.request.Request(
            f"{base}/sidecar/scripts/version",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            server_commit = json.loads(resp.read().decode("utf-8")).get("git_commit", "")
    except Exception as exc:
        logging.warning("script-sync: version check failed (%s); using cache", exc)
        return

    if server_commit and server_commit == cached_commit:
        logging.info("script-sync: cache up-to-date (commit=%s)", cached_commit)
        return

    logging.info(
        "script-sync: updating cache (server=%s, cached=%s)",
        server_commit or "?", cached_commit or "none",
    )
    try:
        req = urllib.request.Request(
            f"{base}/sidecar/scripts/bundle",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            zip_bytes = resp.read()
    except Exception as exc:
        logging.warning("script-sync: bundle download failed (%s); using cache", exc)
        return

    import io
    import shutil
    import zipfile

    try:
        staging = cache.parent / "script_cache_staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(staging)
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        staging.rename(cache)
        version_file = cache / ".version"
        version_file.write_text(server_commit, encoding="utf-8")
        logging.info("script-sync: cache updated to commit=%s", server_commit)
    except Exception as exc:
        logging.warning("script-sync: extract/swap failed (%s); using stale cache", exc)


def _bootstrap_imports(saathi_base: str, *, api_url: str = "", jwt: str = "") -> None:
    os.environ["SAATHI_BASE_DIR"] = saathi_base
    saathi_path = Path(saathi_base)
    env_file = saathi_path / ".env"
    _load_dotenv_safe(env_file)

    if _is_frozen and api_url:
        _sync_scripts(api_url, jwt, saathi_base)

    if _is_frozen:
        cache = _script_cache_dir(saathi_base)
        if (cache / "backend").is_dir():
            sys.path.insert(0, str(cache / "backend"))
            be_env = cache / "backend" / ".env"
            _load_dotenv_safe(be_env)
            return

    backend = _repo_backend()
    sys.path.insert(0, str(backend))
    be_env = backend / ".env"
    _load_dotenv_safe(be_env)


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


def _multipart_upload_file(
    api_url: str,
    jwt: str,
    dealer_id: int,
    tree: str,
    rel_path: str,
    file_path: Path,
    timeout: int = 300,
) -> None:
    """POST one file to ``/sidecar/upload-artifacts`` (multipart/form-data)."""
    boundary = f"----SaathiFormBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    bnd = boundary.encode("ascii")
    parts: list[bytes] = []
    for name, val in (
        ("dealer_id", str(int(dealer_id))),
        ("tree", tree),
        ("rel_path", rel_path),
    ):
        parts.append(b"--" + bnd + crlf)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode("ascii") + crlf + crlf)
        parts.append(str(val).encode("utf-8") + crlf)
    fname = file_path.name.replace('"', "_")
    data = file_path.read_bytes()
    parts.append(b"--" + bnd + crlf)
    cd = (
        f'Content-Disposition: form-data; name="file"; filename="{fname}"'.encode("ascii")
        + crlf
        + b"Content-Type: application/octet-stream"
        + crlf
        + crlf
    )
    parts.append(cd + data + crlf)
    parts.append(b"--" + bnd + b"--" + crlf)
    body = b"".join(parts)
    url = f"{api_url.rstrip('/')}/sidecar/upload-artifacts"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {jwt}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = body_text[:500]
        raise RuntimeError(f"upload-artifacts failed HTTP {exc.code}: {detail}") from exc


def _upload_tree_under(
    api_url: str,
    jwt: str,
    dealer_id: int,
    tree: str,
    folder: Path,
    anchor: Path,
) -> None:
    if not folder.is_dir():
        return
    anchor_res = anchor.resolve()
    for f in folder.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = f.resolve().relative_to(anchor_res).as_posix()
        except ValueError:
            continue
        try:
            _multipart_upload_file(api_url, jwt, dealer_id, tree, rel, f)
        except Exception as exc:
            logging.warning("sidecar upload %s/%s: %s", tree, rel, exc)


def _upload_rto_row_log_if_present(api_url: str, jwt: str, dealer_id: int, row: dict) -> None:
    """Upload ``*_RTO.txt`` written by :func:`fill_rto_row` to the server."""
    from app.config import get_ocr_output_dir
    from app.services.fill_rto_service import _mobile_digits_for_filename, _rto_action_log_path

    ocr_root = get_ocr_output_dir(dealer_id).resolve()
    mob_raw = row.get("customer_mobile") or row.get("mobile") or ""
    mob_fn = _mobile_digits_for_filename(str(mob_raw))
    log_path = _rto_action_log_path(dealer_id, row, mob_fn)
    if not log_path.is_file():
        return
    try:
        rel = log_path.resolve().relative_to(ocr_root).as_posix()
    except ValueError:
        logging.warning("vahan RTO log path outside ocr root: %s", log_path)
        return
    try:
        _multipart_upload_file(api_url, jwt, dealer_id, "ocr", rel, log_path)
    except Exception as exc:
        logging.warning("vahan RTO log upload: %s", exc)


def _upload_sale_artifacts(
    api_url: str,
    jwt: str,
    dealer_id: int,
    subfolder: str,
    uploads_dir: Path,
    ocr_dir: Path,
) -> None:
    from app.services.fill_hero_dms_service import _safe_subfolder_name

    safe = _safe_subfolder_name(subfolder)
    u_root = uploads_dir.resolve()
    o_root = ocr_dir.resolve()
    _upload_tree_under(api_url, jwt, dealer_id, "uploads", uploads_dir / safe, u_root)
    _upload_tree_under(api_url, jwt, dealer_id, "ocr", ocr_dir / safe, o_root)
    fallback = ocr_dir / "Playwright_insurance_diag_fallback.txt"
    if fallback.is_file():
        try:
            rel = fallback.resolve().relative_to(o_root).as_posix()
            _multipart_upload_file(api_url, jwt, dealer_id, "ocr", rel, fallback)
        except Exception as exc:
            logging.warning("sidecar upload insurance diag fallback: %s", exc)


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


def _dispatch_warm_insurance(params: dict) -> dict:
    from app.config import INSURANCE_BASE_URL
    from app.services.fill_hero_insurance_service import warm_insurance_browser_session

    insurance_base = (params.get("insurance_base_url") or INSURANCE_BASE_URL or "").strip()
    return warm_insurance_browser_session(insurance_base)


def _dispatch_warm_vahan(params: dict) -> dict:
    from app.services.fill_rto_service import warm_vahan_browser_session

    return warm_vahan_browser_session()


# ---------------------------------------------------------------------------
# DMS Create Invoice — PRE (API) → Playwright (local) → POST (API)
# ---------------------------------------------------------------------------


def _dispatch_fill_dms(params: dict) -> dict:
    from app.routers.fill_forms_router import (
        DMS_NO_VEHICLE_ERROR,
        _dms_response_warning_and_mode,
        _has_scraped_vehicle,
        _invoice_dispatch_pdf_paths,
        _mobile_for_invoice_dispatch,
        _normalize_automation_error,
    )
    from app.services.upload_scans_invoice_print import collect_invoice_print_jobs_electron_local

    api_url, jwt = _require_api_credentials(params)
    _client_api_log = (params.get("client_api_base_url") or "").strip() or None
    _http_api_log = str(api_url).strip().rstrip("/")

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
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    uploads_dir = get_uploads_dir(dealer_id)
    ocr_output_dir = get_ocr_output_dir(dealer_id)

    from app.services.fill_hero_dms_service import dms_automation_is_real_siebel
    from app.services.handle_browser_opening import get_or_open_site_page

    if not dms_automation_is_real_siebel():
        return {
            "success": False,
            "error": "DMS_MODE must be real/siebel on the server.",
            "vehicle": {},
            "pdfs_saved": [],
            "print_jobs": [],
        }

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
        else:
            _install_playwright_js_dialog_handler(page)
            _run_fill_dms_real_siebel_playwright(
                page,
                dms_values,
                subfolder,
                ocr_output_dir,
                params.get("customer_id"),
                params.get("vehicle_id"),
                result,
                execution_log_client_api_base_url=_client_api_log,
                execution_log_http_request_base_url=_http_api_log,
            )
    except Exception as e:
        result["error"] = str(e)
        logging.warning("fill_dms sidecar: %s", e)

    try:
        _write_data_from_dms(
            ocr_output_dir,
            subfolder,
            dms_values.get("customer_export") or {},
            result.get("vehicle") or {},
        )
    except Exception as e:
        result["error"] = (result.get("error") or "") + f"; DMS file write: {e!s}"

    scraped = result.get("vehicle") or {}
    skip_nv = result.get("dms_automation_mode") == "real" and not result.get("dms_siebel_forms_filled")
    if result.get("error") is None and not _has_scraped_vehicle(scraped) and not skip_nv:
        result["error"] = DMS_NO_VEHICLE_ERROR

    commit_body = {
        "staging_id": params.get("staging_id"),
        "staging_payload": staging_payload,
        "scraped_vehicle": scraped,
        "dealer_id": params.get("dealer_id"),
        "customer_id": result.get("customer_id") or params.get("customer_id"),
        "vehicle_id": result.get("vehicle_id") or params.get("vehicle_id"),
        "sales_id": result.get("sales_id"),
        "masters_committed_via_siebel": result.get("dms_master_persist_committed") is True,
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

    raw_customer = params.get("customer")
    customer_dict = raw_customer if isinstance(raw_customer, dict) else {}

    warn, dms_mode = _dms_response_warning_and_mode(result)
    cc = result.get("committed_customer_id")
    vv = result.get("committed_vehicle_id")
    if cc is None:
        cc = result.get("customer_id") or params.get("customer_id")
    if vv is None:
        vv = result.get("vehicle_id") or params.get("vehicle_id")

    print_jobs: list = []
    if result.get("error") is None and subfolder:
        print_jobs = collect_invoice_print_jobs_electron_local(
            dealer_id,
            subfolder,
            _mobile_for_invoice_dispatch(staging_payload, customer_dict),
            _invoice_dispatch_pdf_paths(result),
        )

    if subfolder:
        try:
            _upload_sale_artifacts(api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_output_dir)
        except Exception as exc:
            logging.warning("fill_dms sidecar artifact upload: %s", exc)

    pdfs_list = _invoice_dispatch_pdf_paths(result)
    err_norm = _normalize_automation_error(result.get("error"))

    return {
        "success": err_norm is None,
        "vehicle": scraped,
        "pdfs_saved": pdfs_list,
        "application_id": result.get("application_id"),
        "rto_fees": result.get("rto_fees"),
        "error": err_norm,
        "customer_id": int(cc) if cc is not None else None,
        "vehicle_id": int(vv) if vv is not None else None,
        "warning": warn,
        "dms_automation_mode": dms_mode,
        "dms_milestones": list(result.get("dms_milestones") or []),
        "dms_step_messages": list(result.get("dms_step_messages") or []),
        "ready_for_client_create_invoice": result.get("ready_for_client_create_invoice"),
        "hero_dms_form22_print": result.get("hero_dms_form22_print"),
        "print_jobs": print_jobs,
    }


# ---------------------------------------------------------------------------
# Insurance — PRE (API) → Playwright (local) → POST (API)
# ---------------------------------------------------------------------------


def _dispatch_fill_insurance(params: dict) -> dict:
    from app.services.upload_scans_invoice_print import collect_insurance_print_jobs_electron_local

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
    from app.config import get_ocr_output_dir, get_uploads_dir
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    ocr_dir = get_ocr_output_dir(dealer_id)
    uploads_dir = get_uploads_dir(dealer_id)

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

    result: dict = {}
    try:
        from app.services.fill_hero_insurance_service import (
            main_process,
            post_process,
            pre_process,
        )

        try:
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
        except Exception as exc:
            logging.warning("fill_insurance sidecar playwright: %s", exc)
            result = {"success": False, "error": str(exc)}
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

    print_jobs: list = []
    if result.get("success"):
        print_jobs = collect_insurance_print_jobs_electron_local(dealer_id, subfolder)

    if subfolder:
        try:
            _upload_sale_artifacts(api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_dir)
        except Exception as exc:
            logging.warning("fill_insurance sidecar artifact upload: %s", exc)

    return {
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "page_url": result.get("page_url"),
        "login_url": result.get("login_url"),
        "match_base": result.get("match_base"),
        "print_jobs": print_jobs,
    }


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

    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
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
        finally:
            _upload_rto_row_log_if_present(api_url, jwt, dealer_id, row)

    return {
        "success": failed_count == 0,
        "total": len(rows),
        "completed": completed_count,
        "failed": failed_count,
    }


# ---------------------------------------------------------------------------
# Subdealer Challan — resolve (API) → prepare_vehicle (local) → prepare-result (API) → … → order-context → order → finalize (API)
# Same pattern as fill_dms: no local DATABASE_URL.
# ---------------------------------------------------------------------------

_SUBDEALER_MAX_PREP_ROUNDS = 3
_SUBDEALER_RETRY_WAIT_SEC = 3.0


def _dispatch_fill_subdealer_challan(params: dict) -> dict:
    api_url, jwt = _require_api_credentials(params)

    challan_batch_id = params.get("challan_batch_id")
    if not challan_batch_id:
        return {"ok": False, "error": "challan_batch_id required"}

    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    dms_base_url = (params.get("dms_base_url") or "").strip()
    phase_raw = (params.get("phase") or "full").strip().lower()
    phase_is_order_only = phase_raw == "order_only"

    resolve_body = {"challan_batch_id": challan_batch_id, "dealer_id": dealer_id}

    try:
        ctx = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
    except Exception as exc:
        return {"ok": False, "error": f"Resolve failed: {exc}", "challan_id": None, "dms_step_messages": [], "vehicle": {}}

    if not dms_base_url:
        dms_base_url = (ctx.get("dms_base_url") or "").strip()
    if not dms_base_url:
        return {
            "ok": False,
            "error": "dms_base_url is empty (pass from app or set DMS_BASE_URL on server).",
            "challan_id": None,
            "dms_step_messages": [],
            "vehicle": {},
        }

    from app.config import (
        CHALLANS_DIR,
        DMS_REAL_URL_CONTACT,
        DMS_REAL_URL_ENQUIRY,
        DMS_REAL_URL_LINE_ITEMS,
        DMS_REAL_URL_PDI,
        DMS_REAL_URL_PRECHECK,
        DMS_REAL_URL_REPORTS,
        DMS_REAL_URL_VEHICLE,
        DMS_REAL_URL_VEHICLES,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        DMS_SIEBEL_NAV_TIMEOUT_MS,
    )
    from app.services.fill_hero_dms_service import (
        Playwright_Hero_DMS_fill_subdealer_challan_order_only,
        _install_playwright_js_dialog_handler,
        dms_automation_is_real_siebel,
    )
    from app.services.handle_browser_opening import get_or_open_site_page
    from app.services.hero_dms_playwright_vehicle import prepare_vehicle
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls

    if not dms_automation_is_real_siebel():
        return {
            "ok": False,
            "error": "DMS_MODE must be real/siebel on the server.",
            "challan_id": None,
            "dms_step_messages": [],
            "vehicle": {},
        }

    urls_prepare = SiebelDmsUrls(
        contact=DMS_REAL_URL_CONTACT,
        vehicles=DMS_REAL_URL_VEHICLES,
        precheck=DMS_REAL_URL_PRECHECK,
        pdi=DMS_REAL_URL_PDI,
        vehicle=DMS_REAL_URL_VEHICLE,
        enquiry=DMS_REAL_URL_ENQUIRY,
        line_items=DMS_REAL_URL_LINE_ITEMS,
        reports=DMS_REAL_URL_REPORTS,
    )
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None

    page, open_error = get_or_open_site_page(dms_base_url, "DMS", require_login_on_open=True)
    if page is None:
        return {
            "ok": False,
            "error": open_error or "Could not open DMS",
            "challan_id": None,
            "dms_step_messages": [],
            "vehicle": {},
        }
    _install_playwright_js_dialog_handler(page)

    last_scrape: dict = {}

    def _pv_note(msg: str) -> None:
        if msg and ("pdi_scrape_" in msg or ": pdi_decision " in msg):
            logging.info("subdealer_challan prepare: %s", msg)

    if not phase_is_order_only:
        try:
            _api_post(api_url, jwt, "/sidecar/subdealer-challan/requeue-failed", resolve_body, timeout=120)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"requeue-failed: {exc}",
                "challan_id": None,
                "dms_step_messages": [],
                "vehicle": {},
            }

        for round_n in range(_SUBDEALER_MAX_PREP_ROUNDS):
            try:
                ctx = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"Resolve failed (prepare round {round_n + 1}): {exc}",
                    "challan_id": None,
                    "dms_step_messages": [],
                    "vehicle": {},
                }

            pending = [
                r
                for r in (ctx.get("detail_rows") or [])
                if (r.get("status") or "").strip().lower() == "queued"
            ]
            if not pending:
                break

            for row in pending:
                sid = int(row["challan_staging_id"])
                rc = row.get("raw_chassis") or ""
                re_ = row.get("raw_engine") or ""
                to_id = int(row["to_dealer_id"])
                dv = {
                    "frame_partial": rc,
                    "engine_partial": re_,
                    "key_partial": "",
                    "battery_partial": "",
                }
                ok, err, scraped, _in_tr, _crit, _info = prepare_vehicle(
                    page,
                    dv,
                    urls_prepare,
                    nav_timeout_ms=DMS_SIEBEL_NAV_TIMEOUT_MS,
                    action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
                    content_frame_selector=frame_sel,
                    note=_pv_note,
                    form_trace=lambda *_a, **_k: None,
                    ms_done=lambda _l: None,
                    step=lambda _m: None,
                )
                try:
                    _api_post(
                        api_url,
                        jwt,
                        "/sidecar/subdealer-challan/prepare-result",
                        {
                            "challan_batch_id": challan_batch_id,
                            "dealer_id": dealer_id,
                            "challan_staging_id": sid,
                            "to_dealer_id": to_id,
                            "ok": bool(ok),
                            "error": ((err or "")[:2000] if not ok else None),
                            "scraped_vehicle": dict(scraped or {}) if ok else None,
                        },
                        timeout=120,
                    )
                except Exception as exc:
                    logging.warning("subdealer_challan prepare-result API: %s", exc)
                if ok:
                    last_scrape = dict(scraped or {})

            try:
                ctx = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"Resolve failed after prepare: {exc}",
                    "challan_id": None,
                    "dms_step_messages": [],
                    "vehicle": {},
                }
            still_queued = [
                r
                for r in (ctx.get("detail_rows") or [])
                if (r.get("status") or "").strip().lower() == "queued"
            ]
            if not still_queued:
                break
            if round_n < _SUBDEALER_MAX_PREP_ROUNDS - 1:
                time.sleep(_SUBDEALER_RETRY_WAIT_SEC)

        try:
            ctx_final = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Resolve failed (final): {exc}",
                "challan_id": None,
                "dms_step_messages": [],
                "vehicle": {},
            }
        rows = ctx_final.get("detail_rows") or []
        not_ready = [r for r in rows if (r.get("status") or "").strip().lower() != "ready"]
        if not_ready:
            parts: list[str] = []
            for r in not_ready[:16]:
                parts.append(
                    f"id={r.get('challan_staging_id')} status={r.get('status')!r} "
                    f"err={(r.get('last_error') or '')[:160]!r}"
                )
            return {
                "ok": False,
                "error": "One or more vehicles did not reach Ready — " + "; ".join(parts),
                "challan_id": None,
                "dms_step_messages": [],
                "vehicle": {},
            }

    oc_body = {
        "challan_batch_id": challan_batch_id,
        "dealer_id": dealer_id,
        "last_vehicle_scrape": {} if phase_is_order_only else last_scrape,
    }
    try:
        pkg = _api_post(api_url, jwt, "/sidecar/subdealer-challan/order-context", oc_body, timeout=120)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"order-context: {exc}",
            "challan_id": None,
            "dms_step_messages": [],
            "vehicle": {},
        }

    if not pkg.get("ok"):
        return {
            "ok": False,
            "error": str(pkg.get("error") or "order-context failed"),
            "challan_id": None,
            "dms_step_messages": [],
            "vehicle": {},
        }

    leaf = (pkg.get("artifact_leaf") or "").strip()
    log_path = (CHALLANS_DIR / leaf / "playwright_challan.txt").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    dms_values = dict(pkg.get("dms_values") or {})
    dms_values["challan_vin_frame_dump_dir"] = str(log_path.parent.resolve())

    urls_o = SiebelDmsUrls(**{k: str(v or "") for k, v in dict(pkg.get("urls") or {}).items()})

    frag = Playwright_Hero_DMS_fill_subdealer_challan_order_only(
        page,
        dms_values,
        urls_o,
        action_timeout_ms=int(pkg.get("action_timeout_ms") or DMS_SIEBEL_ACTION_TIMEOUT_MS),
        nav_timeout_ms=int(pkg.get("nav_timeout_ms") or DMS_SIEBEL_NAV_TIMEOUT_MS),
        content_frame_selector=pkg.get("content_frame_selector"),
        execution_log_path=log_path,
    )

    try:
        fin = _api_post(
            api_url,
            jwt,
            "/sidecar/subdealer-challan/finalize-order",
            {
                "challan_batch_id": challan_batch_id,
                "dealer_id": dealer_id,
                "playwright_result": frag,
            },
            timeout=120,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"finalize-order: {exc}",
            "challan_id": None,
            "dms_step_messages": list(frag.get("dms_step_messages") or []),
            "vehicle": dict(frag.get("vehicle") or {}),
        }

    return {
        "ok": bool(fin.get("ok")),
        "error": fin.get("error"),
        "challan_id": fin.get("challan_id"),
        "dms_step_messages": list(fin.get("dms_step_messages") or []),
        "vehicle": dict(fin.get("vehicle") or {}),
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
    if job_type == "teardown_local_browsers":
        from app.services.handle_browser_opening import teardown_local_automation_browsers

        data = teardown_local_automation_browsers()
        return {"success": True, "data": data}

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    # Propagate top-level api_url / jwt into params for convenience
    if "api_url" not in params and payload.get("api_url"):
        params["api_url"] = payload["api_url"]
    if "jwt" not in params and payload.get("jwt"):
        params["jwt"] = payload["jwt"]

    if job_type == "warm_browser":
        data = _dispatch_warm_browser(params)
        return {"success": True, "data": data}
    if job_type == "warm_insurance":
        data = _dispatch_warm_insurance(params)
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
    if job_type == "fill_subdealer_challan":
        data = _dispatch_fill_subdealer_challan(params)
        return {"success": True, "data": data}
    return {"success": False, "error": f"Unknown job type: {job_type}"}


def main_daemon() -> None:
    """Read newline-delimited JSON jobs from stdin; write one JSON response line per job (stdout)."""
    logging_initialized = False
    bootstrapped = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"success": False, "error": f"Invalid JSON: {e}"}), flush=True)
            continue
        saathi = str(payload.get("saathi_base_dir") or os.environ.get("SAATHI_BASE_DIR") or r"D:\Saathi")
        saathi_path = Path(saathi)
        saathi_path.mkdir(parents=True, exist_ok=True)
        if not logging_initialized:
            _setup_logging(saathi_path)
            logging_initialized = True
        job_type = payload.get("type") or payload.get("job")
        try:
            if job_type == "ping":
                out = dispatch(payload)
            else:
                if not bootstrapped:
                    _bootstrap_imports(
                        saathi,
                        api_url=payload.get("api_url") or "",
                        jwt=payload.get("jwt") or "",
                    )
                    bootstrapped = True
                logging.info("Daemon job start: %s", job_type)
                out = dispatch(payload)
                logging.info("Daemon job end: success=%s", out.get("success"))
            print(json.dumps(out, default=str), flush=True)
        except Exception:
            logging.exception("Daemon job failed")
            print(
                json.dumps({"success": False, "error": traceback.format_exc()}),
                flush=True,
            )


def main() -> None:
    if "--daemon" in sys.argv:
        main_daemon()
        return

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

        _bootstrap_imports(
            saathi,
            api_url=payload.get("api_url") or "",
            jwt=payload.get("jwt") or "",
        )
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
