"""
Local test wrapper: **Print Forms and Queue RTO** (sidecar + API), matching
``runPrintQueueRtoFlow`` without the Electron renderer.

Default fixture sale (override via CLI/env)::

  dealer_id=100001  subfolder=9057397169_210526  mobile=9057397169
  SAATHI_BASE_DIR=D:\\Saathi
  sale folder: D:\\Saathi\\Uploaded scans\\100001\\9057397169_210526
  sales_id=8  customer_id=8  vehicle_id=8
  api_url=https://api.dealersaathi.co.in

**Pull and push are skipped by default** (local folder is source of truth). Use ``--pull`` / ``--push`` to sync with production.

JWT (required for gate-pass context and queue unless steps skipped)::

  - Set in ``test_print_queue_rto.bat`` (``PRINT_RTO_JWT=...``), or
  - One-line file ``test_print_queue_rto.jwt.local`` (gitignored via ``*.local``), or
  - ``--jwt`` / env ``PRINT_RTO_JWT``

Prerequisites in the sale folder before gate pass: Sale Certificate + Insurance PDFs
(DMS reports + Generate Insurance). Gate Pass.pdf is created by this script.

After gate pass, **prints by default** (same 3 jobs as the app: Sale Certificate, Insurance, Gate Pass).
With dialog mode (default), Electron **auto-clicks Print** and **closes Sumatra** per PDF
(``SAATHI_PRINT_DIALOG_ASSIST=1``). Use ``--skip-print`` to skip; ``--silent-print`` for headless.

Double-click ``test_print_queue_rto.bat`` or::

  python test_print_queue_rto.py

from this folder (repo root = parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
_SIDECAR_SCRIPT = _REPO_ROOT / "electron" / "sidecar" / "job_runner.py"
_ELECTRON = _REPO_ROOT / "electron"
_JWT_LOCAL = Path(__file__).resolve().parent / "test_print_queue_rto.jwt.local"

# --- Fixture defaults (override via CLI / env) ---
DEFAULT_DEALER_ID = 100001
DEFAULT_SUBFOLDER = "9057397169_210526"
DEFAULT_MOBILE = "9057397169"
DEFAULT_API_URL = "https://api.dealersaathi.co.in"
DEFAULT_SAATHI_BASE_DIR = r"D:\Saathi"
DEFAULT_SALES_ID = 8
DEFAULT_CUSTOMER_ID = 8
DEFAULT_VEHICLE_ID = 8

# Hard-coded: local sale folder is authoritative unless --pull / --push
SKIP_PULL_DEFAULT = True
SKIP_PUSH_DEFAULT = True
# Print after gate pass (dialog mode unless --silent-print)
PRINT_AFTER_GATE_PASS_DEFAULT = True

_JWT_PLACEHOLDER = "PASTE_BEARER_TOKEN_HERE"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_print_queue_rto")


def _bootstrap_env(saathi_base: str) -> None:
    os.environ.setdefault("SAATHI_BASE_DIR", saathi_base)


def _resolve_jwt(cli_jwt: str) -> str:
    t = (cli_jwt or os.getenv("PRINT_RTO_JWT") or "").strip()
    if t and t != _JWT_PLACEHOLDER:
        return t
    if _JWT_LOCAL.is_file():
        line = _JWT_LOCAL.read_text(encoding="utf-8").strip()
        if line and line != _JWT_PLACEHOLDER:
            return line
    return ""


def _sale_folder(saathi_base: str, dealer_id: int, subfolder: str) -> Path:
    return Path(saathi_base) / "Uploaded scans" / str(dealer_id) / subfolder


def _ocr_log_path(saathi_base: str, dealer_id: int, subfolder: str) -> Path:
    return Path(saathi_base) / "ocr_output" / str(dealer_id) / subfolder / "Print_RTO_queue.txt"


def _run_sidecar_job(
    job_type: str,
    params: dict[str, Any],
    *,
    api_url: str,
    jwt: str,
    saathi_base_dir: str,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    if not _SIDECAR_SCRIPT.is_file():
        raise FileNotFoundError(f"Sidecar script not found: {_SIDECAR_SCRIPT}")

    payload = {
        "type": job_type,
        "api_url": api_url,
        "jwt": jwt,
        "saathi_base_dir": saathi_base_dir,
        "params": params,
    }
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_BACKEND)
    env["SAATHI_BASE_DIR"] = saathi_base_dir

    logger.info("Sidecar job: %s", job_type)
    proc = subprocess.run(
        [sys.executable, str(_SIDECAR_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        timeout=timeout_sec,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stderr:
        for line in stderr.splitlines()[-20:]:
            logger.debug("sidecar stderr: %s", line)

    if not stdout:
        return {
            "success": False,
            "error": stderr or f"Sidecar exited {proc.returncode} with no stdout",
        }

    try:
        out = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid sidecar JSON: {e}\nstdout: {stdout[:500]}"}

    if proc.returncode != 0 and out.get("success") is not False:
        out["success"] = False
        out.setdefault("error", stderr or f"exit code {proc.returncode}")
    return out


def _sidecar_data(out: dict[str, Any]) -> dict[str, Any]:
    data = out.get("data")
    return data if isinstance(data, dict) else {}


def _step_overlay(
    *,
    dealer_id: int,
    subfolder: str,
    api_url: str,
    jwt: str,
    saathi_base: str,
) -> None:
    out = _run_sidecar_job(
        "dealer_sign_overlay",
        {"dealer_id": dealer_id, "subfolder": subfolder},
        api_url=api_url,
        jwt=jwt,
        saathi_base_dir=saathi_base,
        timeout_sec=120,
    )
    data = _sidecar_data(out)
    logger.info("Dealer signature overlay: success=%s data=%s", out.get("success"), data)


def _step_gate_pass(
    *,
    dealer_id: int,
    subfolder: str,
    mobile: str,
    api_url: str,
    jwt: str,
    saathi_base: str,
    vehicle_id: int | None,
    staging_id: str | None,
) -> tuple[bool, dict[str, Any]]:
    params: dict[str, Any] = {
        "dealer_id": dealer_id,
        "subfolder": subfolder,
        "customer": {"mobile": mobile},
        "vehicle": {},
    }
    if vehicle_id is not None:
        params["vehicle_id"] = vehicle_id
    if staging_id:
        params["staging_id"] = staging_id

    out = _run_sidecar_job(
        "print_gate_pass_local",
        params,
        api_url=api_url,
        jwt=jwt,
        saathi_base_dir=saathi_base,
        timeout_sec=300,
    )
    if not out.get("success"):
        logger.error("Gate pass failed: %s", out.get("error") or out)
        return False, out

    data = _sidecar_data(out)
    jobs = data.get("print_jobs") or []
    logger.info("Gate pass OK. pdfs_saved=%s", data.get("pdfs_saved"))
    if jobs:
        logger.info("Print jobs (%d):", len(jobs))
        for j in jobs:
            if isinstance(j, dict):
                logger.info(
                    "  kind=%s file=%s",
                    j.get("kind"),
                    j.get("filename") or j.get("presigned_url"),
                )
    return True, data


def _post_rto_queue(api_url: str, jwt: str, body: dict[str, Any]) -> dict[str, Any]:
    url = api_url.rstrip("/") + "/rto-queue"
    data = json.dumps(body).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST /rto-queue HTTP {e.code}: {detail}") from e


def _normalize_print_jobs(
    sale_dir: Path, print_jobs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for j in print_jobs:
        if not isinstance(j, dict):
            continue
        fn = str(j.get("filename") or "").strip()
        url = str(j.get("presigned_url") or "").strip()
        path: Path | None = None
        if url:
            p = Path(url)
            if p.is_file():
                path = p.resolve()
            elif fn:
                path = (sale_dir / fn).resolve()
        elif fn:
            path = (sale_dir / fn).resolve()
        if path is None or not path.is_file():
            logger.warning("Print job skipped (file missing): %s", j)
            continue
        items.append(
            {
                "presigned_url": str(path),
                "filename": fn or path.name,
                "kind": j.get("kind"),
            }
        )
    return items


def _run_electron_print(
    sale_dir: Path,
    *,
    silent: bool,
    print_jobs: list[dict[str, Any]] | None,
) -> int:
    if not (_ELECTRON / "package.json").is_file():
        logger.error("electron/package.json not found")
        return 1

    npm = shutil.which("npm")
    if not npm:
        logger.error("npm not on PATH")
        return 1

    env = os.environ.copy()
    env["SAATHI_PRINT_TEST_SILENT"] = "1" if silent else "0"
    # Use SAATHI_PRINT_TEST_DIR - Electron runs print test directly without loading Vite
    env["SAATHI_PRINT_TEST_DIR"] = str(sale_dir)

    if print_jobs:
        items = _normalize_print_jobs(sale_dir, print_jobs)
        logger.info("Electron print: %d file(s) in %s silent=%s", len(items), sale_dir, silent)
        for it in items:
            logger.info("  %s %s", it.get("kind"), it.get("filename"))
    else:
        logger.info("Electron print: all PDFs in %s silent=%s", sale_dir, silent)

    # Run Electron - no Vite needed, SAATHI_PRINT_TEST_DIR triggers direct print test
    return subprocess.run([npm, "run", "dev"], cwd=str(_ELECTRON), env=env).returncode


def main() -> int:
    p = argparse.ArgumentParser(description="Test Print / Queue RTO (sidecar + API).")
    p.add_argument("--dealer-id", type=int, default=DEFAULT_DEALER_ID)
    p.add_argument("--subfolder", default=DEFAULT_SUBFOLDER)
    p.add_argument("--mobile", default=DEFAULT_MOBILE)
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    p.add_argument("--jwt", default="", help="Bearer token (else bat / .jwt.local / PRINT_RTO_JWT)")
    p.add_argument("--saathi-base-dir", default=DEFAULT_SAATHI_BASE_DIR)
    p.add_argument("--sales-id", type=int, default=DEFAULT_SALES_ID)
    p.add_argument("--customer-id", type=int, default=DEFAULT_CUSTOMER_ID)
    p.add_argument("--vehicle-id", type=int, default=DEFAULT_VEHICLE_ID)
    p.add_argument("--staging-id", default="", help="Optional UUID (else use --sales-id)")
    p.add_argument(
        "--pull",
        action="store_true",
        help="Download sale folder from server (default: skip)",
    )
    p.add_argument(
        "--pull-aadhar",
        action="store_true",
        help="Download only Aadhaar JPEGs (Submit Info pull; no Print RTO log)",
    )
    p.add_argument(
        "--push",
        action="store_true",
        help="Upload RTO bundle to server after gate pass (default: skip)",
    )
    p.add_argument("--skip-overlay", action="store_true")
    p.add_argument("--skip-gate-pass", action="store_true")
    p.add_argument("--skip-queue", action="store_true")
    p.add_argument(
        "--skip-print",
        action="store_true",
        help="Do not run Electron print after gate pass (default: print with dialog)",
    )
    p.add_argument(
        "--silent-print",
        action="store_true",
        help="Silent print (no dialog; default is system print dialog)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    saathi_base = (args.saathi_base_dir or DEFAULT_SAATHI_BASE_DIR).strip()
    _bootstrap_env(saathi_base)

    dealer_id = int(args.dealer_id)
    subfolder = (args.subfolder or "").strip()
    if not subfolder:
        logger.error("subfolder is required")
        return 1

    # SKIP_*_DEFAULT True: only sync when --pull / --push passed
    do_pull = bool(args.pull)
    do_pull_aadhar = bool(args.pull_aadhar)
    do_push = bool(args.push)
    jwt = _resolve_jwt(args.jwt)

    needs_api = do_pull or do_pull_aadhar or do_push or not args.skip_gate_pass or not args.skip_queue
    if needs_api and not jwt:
        logger.error(
            "JWT required. Set PRINT_RTO_JWT in test_print_queue_rto.bat, "
            "paste into %s, or pass --jwt",
            _JWT_LOCAL.name,
        )
        return 1

    sale_dir = _sale_folder(saathi_base, dealer_id, subfolder)
    log_path = _ocr_log_path(saathi_base, dealer_id, subfolder)

    logger.info("dealer_id=%s subfolder=%s", dealer_id, subfolder)
    logger.info("SAATHI_BASE_DIR=%s", saathi_base)
    logger.info("Sale folder: %s", sale_dir)
    logger.info("Trace log (when written): %s", log_path)
    do_print = PRINT_AFTER_GATE_PASS_DEFAULT and not args.skip_print
    logger.info("pull=%s pull_aadhar=%s push=%s print=%s", do_pull, do_pull_aadhar, do_push, do_print)

    if args.dry_run:
        logger.info("Dry run — exiting without sidecar/API calls.")
        return 0

    if not _BACKEND.is_dir():
        logger.error("backend/ not found: %s", _BACKEND)
        return 1

    if do_pull_aadhar:
        out = _run_sidecar_job(
            "pull_aadhar_scan_jpegs",
            {"dealer_id": dealer_id, "subfolder": subfolder},
            api_url=args.api_url,
            jwt=jwt,
            saathi_base_dir=saathi_base,
        )
        if not out.get("success"):
            logger.error("Aadhaar pull failed: %s", out.get("error"))
            return 1
        d = _sidecar_data(out)
        logger.info(
            "Aadhaar pull OK: downloaded=%s failed=%s",
            d.get("files_downloaded"),
            d.get("files_failed"),
        )
        if not do_pull:
            return 0

    if do_pull:
        out = _run_sidecar_job(
            "pull_sale_scan_assets",
            {"dealer_id": dealer_id, "subfolder": subfolder},
            api_url=args.api_url,
            jwt=jwt,
            saathi_base_dir=saathi_base,
        )
        if not out.get("success"):
            logger.error("Pull failed: %s", out.get("error"))
            return 1
        d = _sidecar_data(out)
        logger.info(
            "Pull OK: downloaded=%s failed=%s",
            d.get("files_downloaded"),
            d.get("files_failed"),
        )

    if not sale_dir.is_dir():
        logger.error("Sale folder not found: %s", sale_dir)
        return 1

    if not args.skip_overlay:
        try:
            _step_overlay(
                dealer_id=dealer_id,
                subfolder=subfolder,
                api_url=args.api_url,
                jwt=jwt,
                saathi_base=saathi_base,
            )
        except Exception as exc:
            logger.warning("Overlay error (non-fatal): %s", exc)

    gate_data: dict[str, Any] = {}
    if not args.skip_gate_pass:
        ok, gate_data = _step_gate_pass(
            dealer_id=dealer_id,
            subfolder=subfolder,
            mobile=(args.mobile or "").strip() or DEFAULT_MOBILE,
            api_url=args.api_url,
            jwt=jwt,
            saathi_base=saathi_base,
            vehicle_id=int(args.vehicle_id) if args.vehicle_id else None,
            staging_id=(args.staging_id or "").strip() or None,
        )
        if not ok:
            if log_path.is_file():
                logger.info("See trace log: %s", log_path)
            return 1

    if do_print:
        if args.skip_gate_pass:
            logger.warning("Print skipped: gate pass was skipped (no print_jobs)")
        else:
            jobs = gate_data.get("print_jobs")
            if not isinstance(jobs, list):
                jobs = []
            code = _run_electron_print(
                sale_dir,
                silent=bool(args.silent_print),
                print_jobs=jobs if jobs else None,
            )
            if code != 0:
                return code

    if do_push:
        out = _run_sidecar_job(
            "push_sale_artifacts",
            {"dealer_id": dealer_id, "subfolder": subfolder},
            api_url=args.api_url,
            jwt=jwt,
            saathi_base_dir=saathi_base,
        )
        if not out.get("success"):
            logger.error("Push failed: %s", out.get("error"))
            return 1
        d = _sidecar_data(out)
        logger.info(
            "Push OK: uploaded=%s failed=%s",
            d.get("files_uploaded"),
            d.get("files_failed"),
        )

    if not args.skip_queue:
        body: dict[str, Any] = {
            "dealer_id": dealer_id,
            "status": "Queued",
        }
        staging_id = (args.staging_id or "").strip()
        if staging_id:
            body["staging_id"] = staging_id
        elif args.sales_id:
            body["sales_id"] = int(args.sales_id)
            if args.customer_id:
                body["customer_id"] = int(args.customer_id)
            if args.vehicle_id:
                body["vehicle_id"] = int(args.vehicle_id)
        else:
            logger.error("RTO queue: provide --staging-id or --sales-id")
            return 1

        try:
            resp = _post_rto_queue(args.api_url, jwt, body)
            logger.info("RTO queue insert OK: %s", resp)
        except Exception as exc:
            logger.error("RTO queue insert failed: %s", exc)
            return 1

    if log_path.is_file():
        logger.info("Trace log: %s", log_path)
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
