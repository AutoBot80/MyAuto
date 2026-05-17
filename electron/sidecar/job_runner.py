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
import re
import ssl
import subprocess
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


# ---------------------------------------------------------------------------
# SSL context for PyInstaller-frozen builds
# ---------------------------------------------------------------------------

_ssl_ctx_cache: ssl.SSLContext | None = None


def _get_ssl_context() -> ssl.SSLContext:
    """
    Return an SSL context with proper certificate verification.

    PyInstaller-frozen apps often can't find the system certificate store, so we
    explicitly load certifi's CA bundle. Falls back to default context if certifi
    is unavailable.
    """
    global _ssl_ctx_cache
    if _ssl_ctx_cache is not None:
        return _ssl_ctx_cache

    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
        logging.info("SSL: using certifi CA bundle at %s", certifi.where())
    except ImportError:
        logging.warning("SSL: certifi not available, using system certificates")
    except Exception as e:
        logging.warning("SSL: failed to load certifi bundle: %s", e)

    _ssl_ctx_cache = ctx
    return ctx


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
        with urllib.request.urlopen(req, timeout=10, context=_get_ssl_context()) as resp:
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
        with urllib.request.urlopen(req, timeout=60, context=_get_ssl_context()) as resp:
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


def _sidecar_playwright_chromium_ready(browsers_root: Path) -> bool:
    """True if a Playwright-managed Chromium build exists under ``PLAYWRIGHT_BROWSERS_PATH``."""
    if not browsers_root.is_dir():
        return False
    for leaf in ("chrome-win64", "chrome-win"):
        for exe in browsers_root.glob(f"chromium-*/{leaf}/chrome.exe"):
            if exe.is_file():
                return True
    return False


def _frozen_playwright_browsers_dir(saathi_path: Path) -> Path | None:
    """
    Frozen sidecar: Playwright must not use the PyInstaller ``_MEI*`` temp tree.

    Set ``PLAYWRIGHT_BROWSERS_PATH`` to ``{SAATHI}/playwright-browsers`` unless the env
    already specifies a directory. Chromium itself is installed by the NSIS installer
    (``--install-playwright-browsers``), not on first app run.
    """
    if not _is_frozen:
        return None
    explicit = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if explicit:
        browsers_dir = Path(explicit)
    else:
        browsers_dir = saathi_path / "playwright-browsers"
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
    try:
        browsers_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.error("playwright browsers dir not usable (%s): %s", browsers_dir, exc)
        return None
    return browsers_dir


def _configure_frozen_playwright_browsers(saathi_path: Path) -> None:
    """Ensure persistent browser cache path only (install is installer-time)."""
    _frozen_playwright_browsers_dir(saathi_path)


def _install_playwright_chromium_if_missing(browsers_dir: Path) -> bool:
    """Download Chromium into ``browsers_dir`` via the bundled Playwright driver. ~300MB."""
    if _sidecar_playwright_chromium_ready(browsers_dir):
        return True
    logging.info(
        "playwright: installing Chromium into %s (~300MB download)",
        browsers_dir,
    )
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        driver_exe, driver_cli = compute_driver_executable()
        env = get_driver_env()
        proc = subprocess.run(
            [driver_exe, driver_cli, "install", "chromium"],
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0:
            logging.error(
                "playwright install chromium failed (code=%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout or "")[:2000],
            )
            return False
        if not _sidecar_playwright_chromium_ready(browsers_dir):
            logging.error(
                "playwright install reported success but chrome.exe not found under %s",
                browsers_dir,
            )
            return False
        logging.info("playwright: Chromium install finished OK")
        return True
    except Exception as exc:
        logging.error("playwright install chromium raised: %s", exc)
        return False


def _cli_install_playwright_browsers_main() -> int:
    """
    Invoked by the NSIS installer: ``job_runner.exe --install-playwright-browsers <SAATHI_ROOT>``.

    Writes under ``PLAYWRIGHT_BROWSERS_PATH`` or ``{saathi}/playwright-browsers``.
    """
    try:
        idx = sys.argv.index("--install-playwright-browsers")
    except ValueError:
        return 2
    saathi_arg: str | None = None
    if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
        saathi_arg = sys.argv[idx + 1]
    for a in sys.argv[idx + 1 :]:
        if a.startswith("--saathi-base="):
            saathi_arg = a.split("=", 1)[1]
            break
    saathi_base = (saathi_arg or os.environ.get("SAATHI_BASE_DIR") or r"D:\Saathi").strip()
    saathi_path = Path(saathi_base)
    saathi_path.mkdir(parents=True, exist_ok=True)
    _setup_logging(saathi_path)
    _load_dotenv_safe(saathi_path / ".env")
    if not (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(saathi_path / "playwright-browsers")
    browsers_dir = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    try:
        browsers_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.error("playwright browsers dir not usable (%s): %s", browsers_dir, exc)
        return 1
    if _sidecar_playwright_chromium_ready(browsers_dir):
        logging.info("playwright Chromium already present under %s", browsers_dir)
        return 0
    return 0 if _install_playwright_chromium_if_missing(browsers_dir) else 1


def _prime_gate_pass_template_env(saathi_base: str) -> None:
    """
    Set ``GATE_PASS_TEMPLATE_DOCX`` before ``app.config`` is first imported.

    Packaged sidecar loads ``script_cache/backend`` where the import-time default is
    ``script_cache/templates/...`` (usually missing). A copy under ``{saathi}/templates/word/``
    must be visible via env before any job imports ``app.config``.
    """
    if os.getenv("GATE_PASS_TEMPLATE_DOCX", "").strip():
        return
    cached = Path(saathi_base) / "templates" / "word" / "Gate Pass Template.docx"
    if cached.is_file():
        os.environ["GATE_PASS_TEMPLATE_DOCX"] = str(cached.resolve())


def _bootstrap_imports(saathi_base: str, *, api_url: str = "", jwt: str = "") -> None:
    os.environ["SAATHI_BASE_DIR"] = saathi_base
    saathi_path = Path(saathi_base)
    env_file = saathi_path / ".env"
    _load_dotenv_safe(env_file)
    _prime_gate_pass_template_env(saathi_base)

    if _is_frozen and api_url:
        _sync_scripts(api_url, jwt, saathi_base)

    if _is_frozen:
        cache = _script_cache_dir(saathi_base)
        if (cache / "backend").is_dir():
            sys.path.insert(0, str(cache / "backend"))
            be_env = cache / "backend" / ".env"
            _load_dotenv_safe(be_env)
            _configure_frozen_playwright_browsers(saathi_path)
            return

    backend = _repo_backend()
    sys.path.insert(0, str(backend))
    be_env = backend / ".env"
    _load_dotenv_safe(be_env)
    if _is_frozen:
        _configure_frozen_playwright_browsers(saathi_path)


# ---------------------------------------------------------------------------
# Cloud API HTTP helper
# ---------------------------------------------------------------------------


def _api_get(api_url: str, jwt: str, path: str, timeout: int = 120) -> dict:
    """GET JSON from the cloud API and return the parsed response dict."""
    url = f"{api_url.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context()) as resp:
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
        with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context()) as resp:
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


def _record_process_failure_via_api(api_url: str, jwt: str, body: dict) -> None:
    """POST terminal failure to cloud API; never raises."""
    try:
        _api_post(api_url, jwt, "/sidecar/failure-log", body, timeout=45)
    except Exception as exc:
        logging.warning("failure-log sidecar API: %s", exc)


def _record_print_queue_rto_failure(
    api_url: str,
    jwt: str,
    dealer_id: int,
    subfolder: str,
    error_text: str,
    *,
    customer: dict | None = None,
    rto_queue_id: int | None = None,
) -> None:
    """Admin Failure Logs row for Print / Queue RTO (sidecar steps)."""
    err = (error_text or "").strip()
    if not err:
        return
    try:
        from app.services.process_failure_log_service import (
            PROCESS_LABEL_PRINT_QUEUE_RTO,
            entity_key_print_queue_rto,
            mobile_digits_for_print_rto_subfolder,
        )

        md = mobile_digits_for_print_rto_subfolder(subfolder, customer)
        _record_process_failure_via_api(
            api_url,
            jwt,
            {
                "dealer_id": int(dealer_id),
                "process_label": PROCESS_LABEL_PRINT_QUEUE_RTO,
                "entity_dedupe_key": entity_key_print_queue_rto(
                    subfolder=subfolder, mobile_digits=md
                ),
                "error_text": err,
                "customer_mobile": md,
                "rto_queue_id": int(rto_queue_id) if rto_queue_id is not None else None,
            },
        )
    except Exception:
        logging.exception("print queue rto failure-log")


def _slim_subdealer_challan_finalize_playwright_result(frag: dict) -> dict:
    """
    ``sidecar_finalize_order_playwright_result`` only uses ``error``, ``vehicle``, and
    ``dms_step_messages``. The full order-phase ``frag`` also carries large fields such as
    ``dms_siebel_notes`` (every automation NOTE); posting the full dict can exceed WAF / body
    limits and yield CloudFront-style 403 HTML.
    """
    veh = dict(frag.get("vehicle") or {})
    msgs = list(frag.get("dms_step_messages") or [])
    _max_steps = 400
    if len(msgs) > _max_steps:
        msgs = msgs[-_max_steps:]
    _max_msg_len = 8000
    _out_msgs: list[str] = []
    for m in msgs:
        s = str(m)
        if len(s) > _max_msg_len:
            s = s[: _max_msg_len - 3] + "..."
        _out_msgs.append(s)
    return {
        "error": frag.get("error"),
        "vehicle": veh,
        "dms_step_messages": _out_msgs,
    }


def _run_sidecar_playwright_job(fn):
    """
    Run Playwright / CDP browser work on the dedicated Playwright worker thread (matches the API server).

    The Electron sidecar daemon handles stdin on its own thread; ``handle_browser_opening`` binds
    sync Playwright to one OS thread. Without this wrapper, CDP refresh/teardown can hop threads and
    automation may attach to the wrong browser or never run prefilled login clicks.
    """
    from app.services.playwright_executor import run_playwright_callable_sync

    return run_playwright_callable_sync(fn)


def _multipart_upload_file(
    api_url: str,
    jwt: str,
    dealer_id: int,
    tree: str,
    rel_path: str,
    file_path: Path,
    timeout: int = 300,
) -> dict:
    """POST one file to ``/sidecar/upload-artifacts`` (EC2 disk, then S3 when configured)."""
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
            "User-Agent": "DealerSaathi-Sidecar/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _get_api_download_opener().open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = body_text[:500]
        raise RuntimeError(f"upload-artifacts failed HTTP {exc.code}: {detail}") from exc
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"ok": True}
    if not parsed.get("ok"):
        raise RuntimeError(f"upload-artifacts rejected: {raw[:500]}")
    return parsed


def _upload_http_code(exc: Exception) -> int | None:
    m = re.search(r"HTTP (\d{3})", str(exc))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _upload_retry_sleep(attempt: int, http_code: int | None) -> float:
    """Backoff between upload attempts (longer after CloudFront/WAF 403)."""
    if http_code == 403:
        return min(20.0, 2.0 * (2**attempt))
    if http_code in (429, 502, 503, 504):
        return min(10.0, 1.0 * (2**attempt))
    return 0.5 * (attempt + 1)


def _upload_inter_file_sleep(*, print_rto: bool = False) -> float:
    """Pause between successful uploads (WAF-friendly). Override via env."""
    key = "SAATHI_PRINT_RTO_UPLOAD_SLEEP" if print_rto else "SAATHI_UPLOAD_INTER_FILE_SLEEP"
    default = 1.5 if print_rto else 0.45
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return max(0.25, float(raw))
    except ValueError:
        return default


def _upload_one_file_with_retries(
    api_url: str,
    jwt: str,
    dealer_id: int,
    tree: str,
    rel: str,
    file_path: Path,
    *,
    ocr_dir: Path | None,
    log_subfolder: str | None,
    max_attempts: int = 6,
) -> tuple[bool, bool]:
    """
    Upload one artifact. Returns ``(http_ok, s3_sync_failed)``.
    ``http_ok`` False means all attempts exhausted (e.g. CloudFront 403).
    """
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    last_exc: Exception | None = None
    s3_failed = False
    for attempt in range(max_attempts):
        try:
            result = _multipart_upload_file(api_url, jwt, dealer_id, tree, rel, file_path)
            if ocr_dir is not None and log_subfolder:
                if result.get("s3_synced", True):
                    append_print_rto_queue_line(
                        ocr_dir,
                        log_subfolder,
                        "PUSH",
                        f"OK {tree}/{rel} (EC2+S3)",
                    )
                else:
                    s3_failed = True
                    s3_err = str(result.get("s3_error") or "S3 sync failed")[:300]
                    append_print_rto_queue_line(
                        ocr_dir,
                        log_subfolder,
                        "PUSH",
                        f"EC2 OK S3 FAIL {tree}/{rel}: {s3_err}",
                    )
            elif not result.get("s3_synced", True):
                s3_failed = True
            return True, s3_failed
        except Exception as exc:
            last_exc = exc
            http_code = _upload_http_code(exc)
            if attempt < max_attempts - 1:
                delay = _upload_retry_sleep(attempt, http_code)
                if ocr_dir is not None and log_subfolder:
                    append_print_rto_queue_line(
                        ocr_dir,
                        log_subfolder,
                        "PUSH",
                        f"retry {tree}/{rel} in {delay:.0f}s "
                        f"(attempt {attempt + 2}/{max_attempts}, HTTP {http_code or 'err'})",
                    )
                time.sleep(delay)
    if last_exc is not None:
        err_msg = str(last_exc).strip() or repr(last_exc)
        logging.warning("sidecar upload %s/%s: %s", tree, rel, err_msg)
        if ocr_dir is not None and log_subfolder:
            append_print_rto_queue_line(
                ocr_dir,
                log_subfolder,
                "PUSH",
                f"FAIL {tree}/{rel}: {err_msg[:500]}",
            )
    return False, s3_failed


def _upload_tree_under(
    api_url: str,
    jwt: str,
    dealer_id: int,
    tree: str,
    folder: Path,
    anchor: Path,
    *,
    ocr_dir: Path | None = None,
    log_subfolder: str | None = None,
) -> tuple[int, int, int]:
    """Return ``(ec2_uploaded, http_failed, s3_sync_failed)``."""
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    if not folder.is_dir():
        return 0, 0, 0
    anchor_res = anchor.resolve()
    uploaded = 0
    failed = 0
    s3_failed = 0
    for f in sorted(folder.rglob("*")):
        if not f.is_file():
            continue
        try:
            rel = f.resolve().relative_to(anchor_res).as_posix()
        except ValueError:
            continue
        ok, one_s3_fail = _upload_one_file_with_retries(
            api_url,
            jwt,
            dealer_id,
            tree,
            rel,
            f,
            ocr_dir=ocr_dir,
            log_subfolder=log_subfolder,
        )
        if ok:
            uploaded += 1
            if one_s3_fail:
                s3_failed += 1
        else:
            failed += 1
        time.sleep(_upload_inter_file_sleep(print_rto=False))
    return uploaded, failed, s3_failed


def _upload_uploads_file_list(
    api_url: str,
    jwt: str,
    dealer_id: int,
    uploads_root: Path,
    files: list[Path],
    *,
    ocr_dir: Path | None = None,
    log_subfolder: str | None = None,
    print_rto: bool = False,
) -> tuple[int, int, int]:
    """Upload explicit paths under ``uploads`` (relative to ``uploads_root``)."""
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    u_root = uploads_root.resolve()
    uploaded = 0
    failed = 0
    s3_failed = 0
    pending: list[tuple[Path, str]] = []
    pause = _upload_inter_file_sleep(print_rto=print_rto)
    if print_rto and ocr_dir is not None and log_subfolder:
        append_print_rto_queue_line(
            ocr_dir,
            log_subfolder,
            "PUSH",
            f"throttle: {pause:.1f}s between uploads (CloudFront/WAF); "
            "set SAATHI_PRINT_RTO_UPLOAD_SLEEP to override",
        )
        time.sleep(1.0)

    for f in sorted(files, key=lambda p: p.name.lower()):
        if not f.is_file():
            continue
        try:
            rel = f.resolve().relative_to(u_root).as_posix()
        except ValueError:
            continue
        ok, one_s3_fail = _upload_one_file_with_retries(
            api_url,
            jwt,
            dealer_id,
            "uploads",
            rel,
            f,
            ocr_dir=ocr_dir,
            log_subfolder=log_subfolder,
        )
        if ok:
            uploaded += 1
            if one_s3_fail:
                s3_failed += 1
        else:
            failed += 1
            pending.append((f, rel))
        time.sleep(pause)

    if print_rto and pending and ocr_dir is not None and log_subfolder:
        cool = 25.0
        append_print_rto_queue_line(
            ocr_dir,
            log_subfolder,
            "PUSH",
            f"retry round after {cool:.0f}s cooldown for {len(pending)} failed file(s)",
        )
        time.sleep(cool)
        still_failed: list[tuple[Path, str]] = []
        for f, rel in pending:
            ok, one_s3_fail = _upload_one_file_with_retries(
                api_url,
                jwt,
                dealer_id,
                "uploads",
                rel,
                f,
                ocr_dir=ocr_dir,
                log_subfolder=log_subfolder,
            )
            if ok:
                uploaded += 1
                failed -= 1
                if one_s3_fail:
                    s3_failed += 1
            else:
                still_failed.append((f, rel))
            time.sleep(pause)
        pending = still_failed

    return uploaded, failed, s3_failed


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


def _should_skip_server_upload_pull(filename: str) -> bool:
    """Skip OCR JSON artifacts only — pull every other file in the sale subfolder (varied names)."""
    if not (filename or "").strip():
        return True
    base = Path(filename).stem.lower().replace(" ", "_")
    return base == "ocr_to_be_used"


class _PresignedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    S3 presigned GET URLs break when urllib forwards ``Authorization`` from the API redirect.

    Build a fresh request to ``Location`` without inherited JWT headers.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(newurl, method=req.get_method())


_cached_api_download_opener: urllib.request.OpenerDirector | None = None


def _get_api_download_opener() -> urllib.request.OpenerDirector:
    global _cached_api_download_opener
    if _cached_api_download_opener is None:
        _cached_api_download_opener = urllib.request.build_opener(
            _PresignedRedirectHandler(),
            urllib.request.HTTPSHandler(context=_get_ssl_context()),
        )
    return _cached_api_download_opener


def _http_error_detail(exc: urllib.error.HTTPError, url: str) -> str:
    body = ""
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    if body:
        try:
            parsed = json.loads(body)
            detail = parsed.get("detail")
            if detail:
                return f"HTTP {exc.code} {url}: {detail}"
        except Exception:
            pass
        if len(body) > 600:
            body = body[:600] + "…"
        return f"HTTP {exc.code} {url}: {body}"
    return f"HTTP {exc.code} {url}"


def _api_download_uploads_file(
    api_url: str,
    jwt: str,
    dealer_id: int,
    subfolder: str,
    filename: str,
    dest: Path,
    timeout: int = 180,
) -> None:
    """GET ``/documents/{subfolder}/{filename}`` (follows S3 presigned redirect when configured)."""
    from urllib.parse import quote

    safe_sub = quote((subfolder or "").strip(), safe="")
    safe_fn = quote(Path(filename).name, safe="")
    url = f"{api_url.rstrip('/')}/documents/{safe_sub}/{safe_fn}?dealer_id={int(dealer_id)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        method="GET",
    )
    try:
        with _get_api_download_opener().open(req, timeout=timeout) as resp:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_http_error_detail(exc, url)) from exc


def _pull_sale_subfolder_from_server(
    api_url: str,
    jwt: str,
    dealer_id: int,
    subfolder: str,
    uploads_dir: Path,
    *,
    ocr_dir: Path | None = None,
) -> dict[str, int]:
    """
    Download **all** files listed for the sale subfolder on the API host (EC2 disk preferred per file GET).

    Filenames vary (Aadhaar, Insurance, Sale Certificate, etc.) — no pattern filter beyond OCR JSON skip.
    """
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    safe = _safe_subfolder_name(subfolder)
    local_dir = uploads_dir / safe
    local_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    failed = 0
    skipped = 0
    pulled_names: list[str] = []
    failed_names: list[str] = []
    try:
        listing = _api_get(
            api_url,
            jwt,
            f"/documents/{safe}/list?dealer_id={int(dealer_id)}",
            timeout=60,
        )
    except Exception as exc:
        logging.warning("pull sale subfolder: list documents failed: %s", exc)
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "PULL",
            f"list documents failed: {exc}",
        )
        return {"downloads_uploaded": 0, "downloads_failed": 0, "downloads_skipped": 0}

    server_names = [
        str(ent.get("name") or "").strip()
        for ent in (listing.get("files") or [])
        if str(ent.get("name") or "").strip()
    ]
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PULL",
        f"server folder list: {len(server_names)} file(s) under uploads/{safe}",
    )

    for ent in listing.get("files") or []:
        name = str(ent.get("name") or "").strip()
        if _should_skip_server_upload_pull(name):
            skipped += 1
            continue
        dest = local_dir / Path(name).name
        try:
            _api_download_uploads_file(api_url, jwt, dealer_id, safe, name, dest)
            downloaded += 1
            pulled_names.append(name)
            logging.info("pull sale file: %s -> %s", name, dest)
            append_print_rto_queue_line(
                ocr_dir,
                safe,
                "PULL",
                f"OK {name} -> {dest}",
            )
        except Exception as exc:
            failed += 1
            failed_names.append(name)
            err_msg = str(exc).strip() or repr(exc)
            logging.warning("pull sale file failed %s: %s", name, err_msg)
            append_print_rto_queue_line(
                ocr_dir,
                safe,
                "PULL",
                f"FAIL {name}: {err_msg}",
            )
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PULL",
        f"done: downloaded={downloaded} failed={failed} skipped={skipped}",
    )
    if pulled_names:
        append_print_rto_queue_line(ocr_dir, safe, "PULL", f"files: {', '.join(pulled_names)}")
    if failed_names:
        append_print_rto_queue_line(ocr_dir, safe, "PULL", f"failures: {', '.join(failed_names)}")
    return {
        "downloads_uploaded": downloaded,
        "downloads_failed": failed,
        "downloads_skipped": skipped,
    }


def _upload_sale_artifacts(
    api_url: str,
    jwt: str,
    dealer_id: int,
    subfolder: str,
    uploads_dir: Path,
    ocr_dir: Path,
    *,
    print_rto_push: bool = False,
) -> dict[str, int]:
    """Mirror local sale ``uploads`` (+ ``ocr_output`` unless ``print_rto_push``) to EC2/S3."""
    import re

    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    safe = _safe_subfolder_name(subfolder)
    u_root = uploads_dir.resolve()
    o_root = ocr_dir.resolve()
    local_uploads = uploads_dir / safe
    local_ocr = ocr_dir / safe
    upload_names: list[str] = []
    if local_uploads.is_dir():
        upload_names = sorted(p.name for p in local_uploads.rglob("*") if p.is_file())
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "PUSH",
            f"local uploads/{safe}: {len(upload_names)} file(s)",
        )
        if not print_rto_push:
            for name in upload_names[:40]:
                append_print_rto_queue_line(ocr_dir, safe, "PUSH", f"  uploads: {name}")
            if len(upload_names) > 40:
                append_print_rto_queue_line(
                    ocr_dir,
                    safe,
                    "PUSH",
                    f"  … and {len(upload_names) - 40} more under uploads",
                )
    else:
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "PUSH",
            f"local uploads folder missing: {local_uploads}",
        )

    if print_rto_push and local_uploads.is_dir():
        from app.services.fill_rto_service import resolve_print_rto_push_upload_paths

        mm = re.match(r"^(\d{10})", safe)
        mob10 = mm.group(1) if mm else ""
        selected = resolve_print_rto_push_upload_paths(
            local_uploads, subfolder=safe, mobile=mob10
        )
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "PUSH",
            f"print-rto push: {len(selected)} file(s) selected "
            f"(Form 20, Form 22, Sale Certificate, GST invoice, Insurance, optional CPA) "
            f"of {len(upload_names)} in folder",
        )
        for p in selected:
            append_print_rto_queue_line(ocr_dir, safe, "PUSH", f"  selected: {p.name}")
        up_ok, up_http_fail, up_s3_fail = _upload_uploads_file_list(
            api_url,
            jwt,
            dealer_id,
            u_root,
            selected,
            ocr_dir=ocr_dir,
            log_subfolder=safe,
            print_rto=True,
        )
        ocr_ok, ocr_http_fail, ocr_s3_fail = 0, 0, 0
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "PUSH",
            "ocr_output subfolder skipped for print-rto push (trace log uploaded separately)",
        )
    else:
        if local_ocr.is_dir():
            ocr_names = sorted(p.name for p in local_ocr.rglob("*") if p.is_file())
            append_print_rto_queue_line(
                ocr_dir,
                safe,
                "PUSH",
                f"local ocr_output/{safe}: {len(ocr_names)} file(s)",
            )
        up_ok, up_http_fail, up_s3_fail = _upload_tree_under(
            api_url,
            jwt,
            dealer_id,
            "uploads",
            uploads_dir / safe,
            u_root,
            ocr_dir=ocr_dir,
            log_subfolder=safe,
        )
        ocr_ok, ocr_http_fail, ocr_s3_fail = _upload_tree_under(
            api_url,
            jwt,
            dealer_id,
            "ocr",
            ocr_dir / safe,
            o_root,
            ocr_dir=ocr_dir,
            log_subfolder=safe,
        )
    total_s3_fail = int(up_s3_fail) + int(ocr_s3_fail)
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PUSH",
        f"upload-artifacts: uploads ec2_ok={up_ok} http_fail={up_http_fail} s3_fail={up_s3_fail}; "
        f"ocr ec2_ok={ocr_ok} http_fail={ocr_http_fail} s3_fail={ocr_s3_fail}",
    )
    if total_s3_fail > 0 and (up_ok + ocr_ok) > 0:
        try:
            _api_post(
                api_url,
                jwt,
                "/sidecar/sync-sale-folder-s3",
                {"dealer_id": int(dealer_id), "subfolder": safe},
                timeout=600,
            )
            append_print_rto_queue_line(
                ocr_dir,
                safe,
                "PUSH",
                f"S3 resync requested for subfolder ({total_s3_fail} per-file sync failure(s))",
            )
        except Exception as exc:
            append_print_rto_queue_line(
                ocr_dir,
                safe,
                "PUSH",
                f"S3 resync failed: {exc}",
            )
    fallback = ocr_dir / "Playwright_insurance_diag_fallback.txt"
    if fallback.is_file():
        try:
            rel = fallback.resolve().relative_to(o_root).as_posix()
            _multipart_upload_file(api_url, jwt, dealer_id, "ocr", rel, fallback)
            ocr_ok += 1
        except Exception as exc:
            ocr_http_fail += 1
            logging.warning("sidecar upload insurance diag fallback: %s", exc)
    return {
        "uploads_uploaded": up_ok,
        "uploads_failed": up_http_fail,
        "uploads_s3_failed": up_s3_fail,
        "ocr_uploaded": ocr_ok,
        "ocr_failed": ocr_http_fail,
        "ocr_s3_failed": ocr_s3_fail,
    }


def _dispatch_upload_print_rto_queue_log_impl(params: dict) -> dict:
    """Append optional lines to local log and upload ``Print_RTO_queue.txt`` to EC2."""
    from app.config import get_ocr_output_dir
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.print_rto_queue_log import (
        LOG_FILENAME,
        append_print_rto_queue_line,
        print_rto_queue_log_path,
    )

    api_url, jwt = _require_api_credentials(params)
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    subfolder = (params.get("subfolder") or "").strip()
    if not subfolder:
        return {"success": False, "error": "subfolder is required"}
    ocr_dir = get_ocr_output_dir(dealer_id)
    safe = _safe_subfolder_name(subfolder)
    for ent in params.get("lines") or []:
        if not isinstance(ent, dict):
            continue
        prefix = str(ent.get("prefix") or "INFO")
        message = str(ent.get("message") or "").strip()
        if message:
            append_print_rto_queue_line(ocr_dir, safe, prefix, message)
    log_path = print_rto_queue_log_path(ocr_dir, safe)
    if log_path is None or not log_path.is_file():
        return {"success": True, "uploaded": False, "note": f"{LOG_FILENAME} not on disk"}
    o_root = ocr_dir.resolve()
    try:
        rel = log_path.resolve().relative_to(o_root).as_posix()
        _multipart_upload_file(api_url, jwt, dealer_id, "ocr", rel, log_path)
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "LOG",
            f"uploaded {LOG_FILENAME} to server (ocr/{rel})",
        )
        return {"success": True, "uploaded": True, "log_path": str(log_path)}
    except Exception as exc:
        append_print_rto_queue_line(ocr_dir, safe, "LOG", f"upload {LOG_FILENAME} failed: {exc}")
        return {"success": False, "error": str(exc), "uploaded": False}


def _saathi_base_dir() -> str:
    return (os.getenv("SAATHI_BASE_DIR") or r"D:\Saathi").strip() or r"D:\Saathi"


def _ensure_gate_pass_template_docx(api_url: str, jwt: str) -> Path:
    """Local Gate Pass .docx for ``generate_gate_pass_pdf_only`` (resolver, repo dev path, or API download)."""
    from app.config import APP_ROOT, resolve_gate_pass_template_docx

    found = resolve_gate_pass_template_docx()
    if found.is_file():
        return found

    repo_template = (_repo_backend().parent / "templates" / "word" / "Gate Pass Template.docx").resolve()
    if repo_template.is_file():
        return repo_template

    base = _saathi_base_dir()
    dest = Path(base) / "templates" / "word" / "Gate Pass Template.docx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{api_url.rstrip('/')}/sidecar/templates/gate-pass-docx"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        method="GET",
    )
    try:
        with _get_api_download_opener().open(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_http_error_detail(exc, url)) from exc
    if not dest.is_file():
        raise FileNotFoundError(f"Gate Pass template download failed: {dest}")
    logging.info("gate pass template cached at %s", dest)
    os.environ["GATE_PASS_TEMPLATE_DOCX"] = str(dest.resolve())
    return dest.resolve()


def _merge_gate_pass_context_from_api(
    api_url: str,
    jwt: str,
    dealer_id: int,
    params: dict,
    customer: dict,
    vehicle: dict,
) -> tuple[dict, dict, dict]:
    """
    Fetch vehicle_master + dealer OEM + staging snapshot from cloud API (no local DB).

    Returns ``(customer, vehicle, dealer)`` dicts for :func:`generate_gate_pass_pdf_only`.
    """
    from urllib.parse import urlencode

    out_c = dict(customer)
    out_v = dict(vehicle)
    out_d: dict = {}
    q: dict[str, str | int] = {"dealer_id": int(dealer_id)}
    vid = params.get("vehicle_id")
    if vid is not None:
        try:
            q["vehicle_id"] = int(vid)
        except (TypeError, ValueError):
            pass
    staging_id = str(params.get("staging_id") or "").strip()
    if staging_id:
        q["staging_id"] = staging_id
    try:
        data = _api_get(
            api_url,
            jwt,
            f"/sidecar/gate-pass-context?{urlencode(q)}",
            timeout=60,
        )
    except Exception as exc:
        logging.warning("gate pass: gate-pass-context API failed: %s", exc)
        return out_c, out_v, out_d

    api_c = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    api_v = data.get("vehicle") if isinstance(data.get("vehicle"), dict) else {}
    api_d = data.get("dealer") if isinstance(data.get("dealer"), dict) else {}
    out_d = dict(api_d)

    for key, val in api_c.items():
        if val is not None and str(val).strip() and not str(out_c.get(key) or "").strip():
            out_c[key] = val

    # Staging + vehicle_master: API wins for Gate Pass placeholders when present.
    for key in (
        "model",
        "colour",
        "color",
        "oem_name",
        "key_num",
        "key_no",
        "chassis",
        "frame_num",
        "frame_no",
        "engine_num",
        "engine_no",
    ):
        val = api_v.get(key)
        if val is not None and str(val).strip():
            out_v[key] = val

    if not str(out_v.get("oem_name") or "").strip() and api_d.get("oem_name"):
        out_v["oem_name"] = api_d["oem_name"]

    return out_c, out_v, out_d


def _mobile_for_print_local(subfolder: str, customer: dict) -> str:
    import re

    m = str(customer.get("mobile_number") or customer.get("mobile") or "").strip()
    if m:
        return m
    leaf = Path(str(subfolder or "").strip().replace("\\", "/")).name
    mm = re.match(r"^(\d{10})_\d{8}$", leaf)
    return mm.group(1) if mm else ""


def _dispatch_pull_sale_scan_assets_impl(params: dict) -> dict:
    """Pull full sale subfolder from server → local; reset ``Print_RTO_queue.txt``."""
    api_url, jwt = _require_api_credentials(params)
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    subfolder = (params.get("subfolder") or "").strip()
    if not subfolder:
        return {"success": False, "error": "subfolder is required"}
    from app.config import get_ocr_output_dir, get_uploads_dir
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.print_rto_queue_log import LOG_FILENAME, append_print_rto_queue_line, reset_print_rto_queue_log

    uploads_dir = get_uploads_dir(dealer_id)
    ocr_dir = get_ocr_output_dir(dealer_id)
    safe = _safe_subfolder_name(subfolder)
    local_sale = uploads_dir / safe
    local_sale.mkdir(parents=True, exist_ok=True)
    reset_print_rto_queue_log(ocr_dir, safe)
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "START",
        f"Print/Queue RTO pull dealer_id={dealer_id} subfolder={safe!r}",
    )
    pull_stats = _pull_sale_subfolder_from_server(
        api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_dir=ocr_dir
    )
    total_down = int(pull_stats["downloads_uploaded"])
    total_fail = int(pull_stats["downloads_failed"])
    attempted = total_down + total_fail
    if attempted > 0 and total_down < 1:
        err = "Pull sale folder from server failed (no files downloaded)."
        _record_print_queue_rto_failure(api_url, jwt, dealer_id, safe, err)
        return {
            "success": False,
            "error": err,
            "subfolder": safe,
            "log_file": LOG_FILENAME,
            **pull_stats,
        }
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PULL",
        f"complete: downloaded={total_down} failed={total_fail}",
    )
    return {
        "success": True,
        "subfolder": safe,
        "files_downloaded": total_down,
        "files_failed": total_fail,
        "log_file": LOG_FILENAME,
        **pull_stats,
    }


def _dispatch_push_sale_artifacts_impl(params: dict) -> dict:
    """Push Print/Queue RTO documents to EC2 (Form 20/22, Sale Certificate, GST, Insurance, optional CPA)."""
    api_url, jwt = _require_api_credentials(params)
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    subfolder = (params.get("subfolder") or "").strip()
    if not subfolder:
        return {"success": False, "error": "subfolder is required"}
    from app.config import get_ocr_output_dir, get_uploads_dir
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    uploads_dir = get_uploads_dir(dealer_id)
    ocr_dir = get_ocr_output_dir(dealer_id)
    safe = _safe_subfolder_name(subfolder)
    stats = _upload_sale_artifacts(
        api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_dir, print_rto_push=True
    )
    total_up = int(stats["uploads_uploaded"]) + int(stats["ocr_uploaded"])
    total_fail = int(stats["uploads_failed"]) + int(stats["ocr_failed"])
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PUSH",
        f"complete: uploaded={total_up} failed={total_fail}",
    )
    customer = params.get("customer") if isinstance(params.get("customer"), dict) else None
    if total_up < 1 and total_fail > 0:
        err = "Push sale folder to server failed."
        _record_print_queue_rto_failure(api_url, jwt, dealer_id, safe, err, customer=customer)
        return {
            "success": False,
            "error": err,
            "subfolder": safe,
            **stats,
        }
    if total_fail > 0:
        _record_print_queue_rto_failure(
            api_url,
            jwt,
            dealer_id,
            safe,
            f"Push: {total_fail} file(s) failed to upload ({total_up} ok). See Print_RTO_queue.txt PUSH lines.",
            customer=customer,
        )
    return {
        "success": True,
        "subfolder": safe,
        "files_uploaded": total_up,
        "files_failed": total_fail,
        **stats,
    }


def _dispatch_print_gate_pass_local_impl(params: dict) -> dict:
    """Generate Gate Pass locally and build print_jobs (sale, insurance, optional cpa, gate pass)."""
    api_url, jwt = _require_api_credentials(params)
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    subfolder = (params.get("subfolder") or "").strip()
    if not subfolder:
        return {"success": False, "error": "subfolder is required"}
    customer = params.get("customer") if isinstance(params.get("customer"), dict) else {}
    vehicle = params.get("vehicle") if isinstance(params.get("vehicle"), dict) else {}
    vehicle_id = params.get("vehicle_id")
    try:
        vehicle_id = int(vehicle_id) if vehicle_id is not None else None
    except (TypeError, ValueError):
        vehicle_id = None

    from app.config import get_ocr_output_dir, get_uploads_dir
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.fill_rto_service import build_local_rto_print_jobs
    from app.services.form20_service import generate_gate_pass_pdf_only
    from app.services.print_rto_queue_log import append_print_rto_queue_line

    safe = _safe_subfolder_name(subfolder)
    uploads_dir = get_uploads_dir(dealer_id)
    ocr_dir = get_ocr_output_dir(dealer_id)
    sale_dir = uploads_dir / safe
    mob = _mobile_for_print_local(subfolder, customer)

    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "GATE_PASS",
        f"local gate pass start subfolder={safe!r} mobile={mob!r}",
    )
    try:
        template_path = _ensure_gate_pass_template_docx(api_url, jwt)
        os.environ["GATE_PASS_TEMPLATE_DOCX"] = str(template_path)
        append_print_rto_queue_line(ocr_dir, safe, "GATE_PASS", f"template: {template_path}")

        customer_dict, vehicle_dict, dealer_dict = _merge_gate_pass_context_from_api(
            api_url, jwt, dealer_id, params, dict(customer), dict(vehicle)
        )
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "GATE_PASS",
            "context: "
            f"oem={vehicle_dict.get('oem_name') or dealer_dict.get('oem_name')!r} "
            f"model={vehicle_dict.get('model')!r} "
            f"colour={vehicle_dict.get('colour') or vehicle_dict.get('color')!r}",
        )
        if vehicle_dict.get("key_no") and "key_num" not in vehicle_dict:
            vehicle_dict["key_num"] = vehicle_dict.get("key_no")
        if vehicle_dict.get("frame_no") and "frame_num" not in vehicle_dict:
            vehicle_dict["frame_num"] = vehicle_dict.get("frame_no")
        if vehicle_dict.get("engine_no") and "engine_num" not in vehicle_dict:
            vehicle_dict["engine_num"] = vehicle_dict.get("engine_no")

        gate_pass_path = generate_gate_pass_pdf_only(
            subfolder=subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            vehicle_id=vehicle_id,
            dealer_id=dealer_id,
            uploads_dir=uploads_dir,
            dealer=dealer_dict,
        )
        print_jobs, missing = build_local_rto_print_jobs(
            sale_dir,
            subfolder=safe,
            mobile=mob,
            gate_pass_pdf=gate_pass_path,
        )
        if missing:
            err = "Missing PDFs in sale folder — run Fill DMS (reports) and Generate Insurance first. " + "; ".join(
                missing
            )
            append_print_rto_queue_line(ocr_dir, safe, "GATE_PASS", f"FAIL: {err}")
            _record_print_queue_rto_failure(
                api_url, jwt, dealer_id, safe, f"Gate pass: {err}", customer=customer_dict
            )
            return {"success": False, "error": err, "pdfs_saved": ["Gate Pass.pdf"]}

        kinds = [j.get("kind") for j in print_jobs]
        append_print_rto_queue_line(
            ocr_dir,
            safe,
            "GATE_PASS",
            f"OK print_jobs kinds={kinds}",
        )
        return {
            "success": True,
            "pdfs_saved": ["Gate Pass.pdf"],
            "print_jobs": print_jobs,
            "subfolder": safe,
        }
    except Exception as exc:
        append_print_rto_queue_line(ocr_dir, safe, "GATE_PASS", f"FAIL: {exc}")
        logging.warning("print_gate_pass_local: %s", exc)
        cust = params.get("customer") if isinstance(params.get("customer"), dict) else None
        _record_print_queue_rto_failure(
            api_url, jwt, dealer_id, safe, f"Gate pass: {exc}", customer=cust
        )
        return {"success": False, "error": str(exc), "pdfs_saved": []}


def _dispatch_upload_sale_artifacts_impl(params: dict) -> dict:
    """
    Two-way sale-folder sync for Print / Queue RTO:

    1. Pull full sale subfolder from EC2/S3 → local (all listed uploads files; skip ``OCR_To_be_Used.json`` only).
    2. Push all local ``Uploaded scans`` + ``ocr_output`` files → EC2 via ``/sidecar/upload-artifacts``.
    """
    api_url, jwt = _require_api_credentials(params)
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    subfolder = (params.get("subfolder") or "").strip()
    if not subfolder:
        return {"success": False, "error": "subfolder is required"}
    from app.config import get_ocr_output_dir, get_uploads_dir
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.print_rto_queue_log import LOG_FILENAME, append_print_rto_queue_line, reset_print_rto_queue_log

    uploads_dir = get_uploads_dir(dealer_id)
    ocr_dir = get_ocr_output_dir(dealer_id)

    safe = _safe_subfolder_name(subfolder)
    local_sale = uploads_dir / safe
    local_sale.mkdir(parents=True, exist_ok=True)
    log_path = reset_print_rto_queue_log(ocr_dir, safe)
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "START",
        f"Print/Queue RTO sync dealer_id={dealer_id} subfolder={safe!r}",
    )
    append_print_rto_queue_line(ocr_dir, safe, "PATH", f"local uploads: {local_sale.resolve()}")
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PATH",
        f"local ocr_output: {(ocr_dir / safe).resolve()}",
    )
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "PATH",
        f"log file: {log_path}" if log_path else f"log file: ocr_output/{safe}/{LOG_FILENAME}",
    )
    pull_stats = _pull_sale_subfolder_from_server(
        api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_dir=ocr_dir
    )
    stats = _upload_sale_artifacts(api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_dir)
    total_up = int(stats["uploads_uploaded"]) + int(stats["ocr_uploaded"])
    total_fail = int(stats["uploads_failed"]) + int(stats["ocr_failed"])
    total_down = int(pull_stats["downloads_uploaded"])
    if total_up < 1 and total_fail > 0 and total_down < 1:
        return {
            "success": False,
            "error": "Upload and download both failed (check API URL, JWT, and network).",
            **stats,
            **pull_stats,
            "subfolder": safe,
        }
    append_print_rto_queue_line(
        ocr_dir,
        safe,
        "SYNC",
        f"complete: downloaded={total_down} uploaded={total_up} failed={total_fail + int(pull_stats['downloads_failed'])}",
    )
    return {
        "success": True,
        "subfolder": safe,
        "files_uploaded": total_up,
        "files_downloaded": total_down,
        "files_failed": total_fail + int(pull_stats["downloads_failed"]),
        "log_file": LOG_FILENAME,
        **stats,
        **pull_stats,
    }


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


def _dispatch_fill_dms_impl(params: dict) -> dict:
    from app.routers.fill_forms_router import (
        DMS_NO_VEHICLE_ERROR,
        _dms_response_warning_and_mode,
        _has_scraped_vehicle,
        _invoice_dispatch_pdf_paths,
        _normalize_automation_error,
    )

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

    from app.config import DMS_LOGIN_PASSWORD, DMS_LOGIN_USER
    from app.services.fill_hero_dms_service import dms_automation_is_real_siebel
    from app.services.handle_browser_opening import get_or_open_site_page

    if not dms_automation_is_real_siebel():
        msg = "DMS_MODE must be real/siebel on the server."
        try:
            from app.services.process_failure_log_service import entity_key_fill_dms

            sid_s = (params.get("staging_id") or "").strip() or None
            cid_p = params.get("customer_id")
            vid_p = params.get("vehicle_id")
            ek = entity_key_fill_dms(
                staging_id=sid_s,
                customer_id=int(cid_p) if cid_p is not None else None,
                vehicle_id=int(vid_p) if vid_p is not None else None,
                mobile_digits=None,
            )
            _record_process_failure_via_api(
                api_url,
                jwt,
                {
                    "dealer_id": dealer_id,
                    "process_label": "Create Invoice",
                    "entity_dedupe_key": ek,
                    "error_text": msg,
                },
            )
        except Exception:
            logging.exception("fill_dms sidecar failure-log (early)")
        return {
            "success": False,
            "error": msg,
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
        playwright_dms_execution_log_filename,
        _safe_subfolder_name,
    )

    result: dict = {
        "vehicle": {},
        "pdfs_saved": [],
        "error": None,
        "dms_milestones": [],
        "dms_step_messages": [],
    }

    playwright_dms_log = (
        Path(ocr_output_dir).resolve()
        / _safe_subfolder_name(subfolder)
        / playwright_dms_execution_log_filename()
    )
    result["playwright_dms_execution_log_path"] = str(playwright_dms_log)

    page = None
    try:
        page, open_error = get_or_open_site_page(
            dms_base_url,
            "DMS",
            require_login_on_open=True,
            login_user=DMS_LOGIN_USER,
            login_password=DMS_LOGIN_PASSWORD,
        )
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
                playwright_dms_log,
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

    warn, dms_mode = _dms_response_warning_and_mode(result)
    cc = result.get("committed_customer_id")
    vv = result.get("committed_vehicle_id")
    if cc is None:
        cc = result.get("customer_id") or params.get("customer_id")
    if vv is None:
        vv = result.get("vehicle_id") or params.get("vehicle_id")

    # Match ``fill_forms_router.fill_dms``: keep ``print_jobs`` empty after Create Invoice. Non-empty jobs
    # would make the client call ``dispatchPrintJobsFromApi`` → Electron opens PDF windows and
    # ``webContents.print({ silent: false })`` (system print dialog) for GST / Sale Certificate / Form 20.
    print_jobs: list = []

    if subfolder:
        try:
            _upload_sale_artifacts(api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_output_dir)
        except Exception as exc:
            logging.warning("fill_dms sidecar artifact upload: %s", exc)

    pdfs_list = _invoice_dispatch_pdf_paths(result)
    err_norm = _normalize_automation_error(result.get("error"))
    if err_norm:
        try:
            from app.services.process_failure_log_service import digits_only_mobile, entity_key_fill_dms

            cust = dms_values.get("customer_export") or {}
            mob_raw = ""
            if isinstance(cust, dict):
                mob_raw = str(cust.get("mobile") or cust.get("mobile_number") or "").strip()
            if not mob_raw and isinstance(staging_payload, dict):
                c0 = staging_payload.get("customer")
                if isinstance(c0, dict):
                    mob_raw = str(c0.get("mobile_number") or c0.get("mobile") or "").strip()
            md = digits_only_mobile(mob_raw)
            sid_s = (params.get("staging_id") or "").strip() or None
            ek = entity_key_fill_dms(
                staging_id=sid_s,
                customer_id=int(cc) if cc is not None else None,
                vehicle_id=int(vv) if vv is not None else None,
                mobile_digits=md,
            )
            disp = md if md else (mob_raw[:32] if mob_raw else None)
            _record_process_failure_via_api(
                api_url,
                jwt,
                {
                    "dealer_id": dealer_id,
                    "process_label": "Create Invoice",
                    "entity_dedupe_key": ek,
                    "error_text": err_norm,
                    "customer_mobile": disp,
                },
            )
        except Exception:
            logging.exception("fill_dms sidecar failure-log")

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


def _dispatch_fill_insurance_impl(params: dict) -> dict:
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
    _original_insert_fhi = _fhi.insert_insurance_master_after_gi
    _captured_insert_args: dict = {}

    def _noop_insert(*_a, **kw):
        _captured_insert_args.update(kw)

    _cs.insert_insurance_master_after_gi = _noop_insert
    _fhi.insert_insurance_master_after_gi = _noop_insert

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
        _fhi.insert_insurance_master_after_gi = _original_insert_fhi

    if result.get("success"):
        _final_scrape = _captured_insert_args.get("preview_scrape")
        commit_body = {
            "customer_id": cid,
            "vehicle_id": vid,
            "fill_values": cached_values,
            "staging_payload": staging_payload,
            "preview_scrape": _final_scrape,
            "post_issue_scrape": None,
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

    if result.get("error") or not result.get("success"):
        err_txt = str(result.get("error") or "Generate Insurance failed")
        try:
            from app.services.process_failure_log_service import digits_only_mobile, entity_key_fill_dms

            mob_raw = ""
            if staging_payload and isinstance(staging_payload.get("customer"), dict):
                c = staging_payload["customer"]
                raw = c.get("mobile_number") if c.get("mobile_number") is not None else c.get("mobile")
                mob_raw = str(raw).strip() if raw is not None else ""
            md = digits_only_mobile(mob_raw)
            sid_s = (str(staging_id).strip() if staging_id else "") or None
            if sid_s == "":
                sid_s = (params.get("staging_id") or "").strip() or None
            ek = entity_key_fill_dms(
                staging_id=sid_s,
                customer_id=int(cid),
                vehicle_id=int(vid),
                mobile_digits=md,
            )
            disp = md if md else (mob_raw[:32] if mob_raw else None)
            _record_process_failure_via_api(
                api_url,
                jwt,
                {
                    "dealer_id": dealer_id,
                    "process_label": "Generate Insurance",
                    "entity_dedupe_key": ek,
                    "error_text": err_txt,
                    "customer_mobile": disp,
                },
            )
        except Exception:
            logging.exception("fill_insurance sidecar failure-log")

    return {
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "page_url": result.get("page_url"),
        "login_url": result.get("login_url"),
        "match_base": result.get("match_base"),
        "print_jobs": print_jobs,
    }


def _dispatch_fill_cpa_alliance_insurance_impl(params: dict) -> dict:
    """CPA Alliance portal — resolve (API) → Playwright (local); no local DATABASE_URL."""
    logging.info(
        "fill_cpa_alliance_insurance: start staging_id=%s dealer_id=%s",
        params.get("staging_id"),
        params.get("dealer_id"),
    )
    api_url, jwt = _require_api_credentials(params)

    from app.config import get_ocr_output_dir, get_uploads_dir
    from app.services.add_alliance_cpa_insurance import add_alliance_cpa_insurance
    from app.services.cpa_form_values import write_cpa_form_values_snapshot

    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    portal_url = (params.get("portal_url") or "").strip() or None

    resolve_body = {
        "staging_id": params.get("staging_id"),
        "customer_id": params.get("customer_id"),
        "vehicle_id": params.get("vehicle_id"),
        "subfolder": params.get("subfolder"),
        "dealer_id": params.get("dealer_id"),
    }
    try:
        ctx = _api_post(api_url, jwt, "/sidecar/cpa/resolve", resolve_body)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if not isinstance(ctx, dict):
        return {"success": False, "error": "CPA resolve returned an invalid response."}
    try:
        alliance_kwargs = ctx["alliance_kwargs"]
        full_values = ctx["full_values"]
        subfolder = ctx["subfolder"]
    except KeyError as exc:
        return {
            "success": False,
            "error": f"CPA resolve response missing field ({exc}). Redeploy the API with /sidecar/cpa/resolve.",
        }
    if not isinstance(alliance_kwargs, dict) or not isinstance(full_values, dict):
        return {"success": False, "error": "CPA resolve returned invalid fill payloads."}
    if not str(subfolder or "").strip():
        return {"success": False, "error": "CPA resolve did not return a sale subfolder (file_location)."}

    ocr_dir = Path(get_ocr_output_dir(dealer_id))
    try:
        write_cpa_form_values_snapshot(ocr_dir, subfolder, full_values)
    except Exception as exc:
        logging.warning("fill_cpa_alliance_insurance local CPA_Form_Values snapshot: %s", exc)

    logging.info("fill_cpa_alliance_insurance: resolve ok subfolder=%s — starting Playwright", subfolder)
    result = add_alliance_cpa_insurance(
        dealer_id=dealer_id,
        subfolder=subfolder,
        portal_url=portal_url,
        **alliance_kwargs,
    )
    logging.info(
        "fill_cpa_alliance_insurance: finished success=%s error=%s",
        result.get("success"),
        result.get("error"),
    )

    uploads_dir = get_uploads_dir(dealer_id)
    ocr_dir = get_ocr_output_dir(dealer_id)
    if subfolder:
        try:
            _upload_sale_artifacts(api_url, jwt, dealer_id, subfolder, uploads_dir, ocr_dir)
        except Exception as exc:
            logging.warning("fill_cpa_alliance_insurance sidecar artifact upload: %s", exc)

    if not result.get("success") or result.get("error"):
        err_txt = str(result.get("error") or "CPA Insurance failed")
        try:
            from app.services.process_failure_log_service import digits_only_mobile, entity_key_print_forms

            mob_raw = str(full_values.get("mobile_number") or "").strip()
            md = digits_only_mobile(mob_raw)
            ek = entity_key_print_forms(subfolder=subfolder or "default", mobile_digits=md, suffix="cpa")
            disp = md if md else (mob_raw[:32] or None)
            _record_process_failure_via_api(
                api_url,
                jwt,
                {
                    "dealer_id": dealer_id,
                    "process_label": "CPA Insurance",
                    "entity_dedupe_key": ek,
                    "error_text": err_txt,
                    "customer_mobile": disp,
                },
            )
        except Exception:
            logging.exception("fill_cpa sidecar failure-log")

    return {
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "page_url": result.get("page_url"),
        "playwright_log": result.get("playwright_log"),
    }


# ---------------------------------------------------------------------------
# Vahan RTO batch — claim (API) → per-row Playwright (local) → result (API)
# ---------------------------------------------------------------------------


def _dispatch_fill_vahan_batch_impl(params: dict) -> dict:
    api_url, jwt = _require_api_credentials(params)

    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    claim_body = {
        "dealer_id": params.get("dealer_id"),
        "limit": params.get("limit", 7),
    }
    try:
        claim_resp = _api_post(api_url, jwt, "/sidecar/vahan/claim-batch", claim_body)
    except Exception as exc:
        try:
            _record_process_failure_via_api(
                api_url,
                jwt,
                {
                    "dealer_id": dealer_id,
                    "process_label": "Fill Vahan (batch)",
                    "entity_dedupe_key": f"vahan_batch:{dealer_id}",
                    "error_text": str(exc)[:4000],
                },
            )
        except Exception:
            logging.exception("vahan batch claim failure-log")
        return {"success": False, "total": 0, "completed": 0, "failed": 0, "error": str(exc)}

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


def _safe_challan_artifact_leaf(leaf: str | None) -> str:
    """Single folder name under CHALLANS_DIR (no traversal)."""
    t = (leaf or "").strip().replace("\\", "/").split("/")[-1]
    if not t or ".." in t:
        return "unknown_challan"
    return t


def _mirror_challan_parse_artifacts_impl(params: dict) -> dict:
    """Write parse-scan OCR files under dealer ``ocr_output/.../subdealer_challan/<leaf>/`` (same layout as EC2)."""
    from app.config import get_challan_artifacts_dir
    from app.services.subdealer_challan_ocr_service import OCR_JSON_STEM

    raw_leaf = str(params.get("artifact_leaf") or "").strip()
    if not raw_leaf:
        return {"ok": False, "error": "artifact_leaf required"}
    leaf = _safe_challan_artifact_leaf(raw_leaf)
    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    raw_t = params.get("raw_ocr_text")
    js_t = params.get("ocr_json_text")
    if not (isinstance(raw_t, str) and raw_t.strip()) and not (isinstance(js_t, str) and js_t.strip()):
        return {"ok": False, "error": "raw_ocr_text or ocr_json_text required"}
    base = get_challan_artifacts_dir(dealer_id, leaf)
    try:
        base.mkdir(parents=True, exist_ok=True)
        if isinstance(raw_t, str) and raw_t.strip():
            (base / "Raw_OCR.txt").write_text(raw_t, encoding="utf-8")
        if isinstance(js_t, str) and js_t.strip():
            (base / f"{OCR_JSON_STEM}.json").write_text(js_t, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "local_dir": str(base.resolve())}


def _log_subdealer_challan_sidecar_failure(
    api_url: str,
    jwt: str,
    dealer_id: int,
    challan_batch_id,
    ctx: dict,
    err_msg: str | None,
) -> None:
    if not err_msg:
        return
    try:
        from uuid import UUID as _UUID

        from app.services.process_failure_log_service import entity_key_challan

        bid = _UUID(str(challan_batch_id).strip())
        cb_raw = ctx.get("challan_book_num")
        cd_raw = ctx.get("challan_date")
        cb = str(cb_raw).strip() if cb_raw is not None else None
        cd = str(cd_raw).strip() if cd_raw is not None else None
        if cb == "":
            cb = None
        if cd == "":
            cd = None
        ek = entity_key_challan(challan_book_num=cb, challan_date=cd, batch_id=bid)
        _record_process_failure_via_api(
            api_url,
            jwt,
            {
                "dealer_id": dealer_id,
                "process_label": "Subdealer challan",
                "entity_dedupe_key": ek,
                "error_text": str(err_msg)[:4000],
                "challan_book_num": cb,
                "challan_date": cd,
                "challan_batch_id": str(bid),
            },
        )
    except Exception:
        logging.exception("subdealer challan failure-log (sidecar)")


def _fill_subdealer_challan_impl(params: dict) -> dict:
    api_url, jwt = _require_api_credentials(params)

    challan_batch_id = params.get("challan_batch_id")
    if not challan_batch_id:
        return {"ok": False, "error": "challan_batch_id required"}

    dealer_id = int(params.get("dealer_id") or os.getenv("DEALER_ID", "100001"))
    dms_base_url = (params.get("dms_base_url") or "").strip()
    phase_raw = (params.get("phase") or "full").strip().lower()
    phase_is_order_only = phase_raw == "order_only"

    resolve_body = {"challan_batch_id": challan_batch_id, "dealer_id": dealer_id}
    ctx: dict = {}

    def _ret_challan(d: dict) -> dict:
        if not d.get("ok") and d.get("error"):
            _log_subdealer_challan_sidecar_failure(
                api_url,
                jwt,
                dealer_id,
                challan_batch_id,
                ctx if isinstance(ctx, dict) else {},
                str(d.get("error")),
            )
        return d

    try:
        ctx = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
    except Exception as exc:
        return _ret_challan(
            {"ok": False, "error": f"Resolve failed: {exc}", "challan_id": None, "dms_step_messages": [], "vehicle": {}}
        )

    if not dms_base_url:
        dms_base_url = (ctx.get("dms_base_url") or "").strip()
    if not dms_base_url:
        return _ret_challan(
            {
                "ok": False,
                "error": "dms_base_url is empty (pass from app or set DMS_BASE_URL on server).",
                "challan_id": None,
                "dms_step_messages": [],
                "vehicle": {},
            }
        )

    from app.services.subdealer_challan_ocr_service import challan_artifact_leaf_name

    initial_leaf = challan_artifact_leaf_name(ctx.get("challan_book_num"), ctx.get("challan_date"))

    from app.config import (
        DMS_LOGIN_PASSWORD,
        DMS_LOGIN_USER,
        DMS_REAL_URL_CONTACT,
        DMS_REAL_URL_VEHICLE,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        DMS_SIEBEL_NAV_TIMEOUT_MS,
        CHALLANS_DIR,
        get_challan_artifacts_dir,
    )
    from app.services.fill_hero_dms_service import (
        Playwright_Hero_DMS_fill_subdealer_challan_order_only,
        _install_playwright_js_dialog_handler,
        dms_automation_is_real_siebel,
    )
    from app.services.handle_browser_opening import get_or_open_site_page
    from app.services.hero_dms_playwright_vehicle import prepare_vehicle
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls, _ts_ist_iso

    if not dms_automation_is_real_siebel():
        return _ret_challan(
            {
                "ok": False,
                "error": "DMS_MODE must be real/siebel on the server.",
                "challan_id": None,
                "dms_step_messages": [],
                "vehicle": {},
            }
        )

    urls_prepare = SiebelDmsUrls(
        contact=DMS_REAL_URL_CONTACT,
        # vehicles=DMS_REAL_URL_VEHICLES,
        vehicles="",
        # precheck=DMS_REAL_URL_PRECHECK,
        precheck="",
        # pdi=DMS_REAL_URL_PDI,
        pdi="",
        vehicle=DMS_REAL_URL_VEHICLE,
        # enquiry=DMS_REAL_URL_ENQUIRY,
        enquiry="",
        # line_items=DMS_REAL_URL_LINE_ITEMS,
        line_items="",
        # reports=DMS_REAL_URL_REPORTS,
        reports="",
    )
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None

    prep_leaf = _safe_challan_artifact_leaf(initial_leaf)
    challan_session_base = get_challan_artifacts_dir(dealer_id, prep_leaf)

    page, open_error = get_or_open_site_page(
        dms_base_url,
        "DMS",
        require_login_on_open=True,
        login_user=DMS_LOGIN_USER,
        login_password=DMS_LOGIN_PASSWORD,
    )
    if page is None:
        return _ret_challan(
            {
                "ok": False,
                "error": open_error or "Could not open DMS",
                "challan_id": None,
                "dms_step_messages": [],
                "vehicle": {},
            }
        )
    _install_playwright_js_dialog_handler(page)

    challan_dirs_to_sync: list[Path] = []
    challans_root = CHALLANS_DIR.resolve()

    def _flush_challan_ocr_dirs_to_server() -> None:
        seen: set[Path] = set()
        for d in challan_dirs_to_sync:
            try:
                dr = d.resolve()
            except OSError:
                continue
            if dr in seen or not dr.is_dir():
                continue
            seen.add(dr)
            _upload_tree_under(api_url, jwt, dealer_id, "challans", d, challans_root)

    last_scrape: dict = {}
    try:

        def _pv_note(msg: str) -> None:
            if msg and ("pdi_scrape_" in msg or ": pdi_decision " in msg):
                logging.info("subdealer_challan prepare: %s", msg)
    
        form_trace_pv = lambda *_a, **_k: None
        if not phase_is_order_only:
            challan_session_base.mkdir(parents=True, exist_ok=True)
            challan_dirs_to_sync.append(challan_session_base.resolve())
            challan_prepare_log = challan_session_base / "playwright_challan.txt"
    
            def _form_trace_prepare(siebel_step: str, form_name: str, action: str, **fields: object) -> None:
                segments = [f"siebel_step={siebel_step}", f"form={form_name}", f"action={action}"]
                for key in sorted(fields.keys()):
                    val = fields[key]
                    if val is None:
                        continue
                    v = str(val).replace("\n", " ").strip()
                    if v == "":
                        continue
                    if len(v) > 500:
                        v = v[:497] + "..."
                    segments.append(f"{key}={v!r}")
                line = f"{_ts_ist_iso()} [FORM] " + " | ".join(segments) + "\n"
                try:
                    with challan_prepare_log.open("a", encoding="utf-8") as fp:
                        fp.write(line)
                except OSError:
                    pass
    
            with challan_prepare_log.open("w", encoding="utf-8") as fp:
                fp.write(f"=== subdealer challan (local) batch={challan_batch_id} ===\n")
                fp.write(f"{_ts_ist_iso()} [NOTE] challan_trace_dir={challan_session_base!s}\n")
                fp.write(
                    f"{_ts_ist_iso()} [NOTE] challan_book_num={ctx.get('challan_book_num')!r} "
                    f"challan_date={ctx.get('challan_date')!r}\n"
                )
                fp.write(f"{_ts_ist_iso()} [NOTE] --- prepare_vehicle phase ---\n")
            form_trace_pv = _form_trace_prepare
    
        if not phase_is_order_only:
            try:
                _api_post(api_url, jwt, "/sidecar/subdealer-challan/requeue-failed", resolve_body, timeout=120)
            except Exception as exc:
                return _ret_challan(
                    {
                        "ok": False,
                        "error": f"requeue-failed: {exc}",
                        "challan_id": None,
                        "dms_step_messages": [],
                        "vehicle": {},
                    }
                )
    
            for round_n in range(_SUBDEALER_MAX_PREP_ROUNDS):
                try:
                    ctx = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
                except Exception as exc:
                    return _ret_challan(
                        {
                            "ok": False,
                            "error": f"Resolve failed (prepare round {round_n + 1}): {exc}",
                            "challan_id": None,
                            "dms_step_messages": [],
                            "vehicle": {},
                        }
                    )
    
                pending = [
                    r
                    for r in (ctx.get("detail_rows") or [])
                    if (r.get("status") or "").strip().lower() in ("queued", "failed")
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
                        form_trace=form_trace_pv,
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
                    return _ret_challan(
                        {
                            "ok": False,
                            "error": f"Resolve failed after prepare: {exc}",
                            "challan_id": None,
                            "dms_step_messages": [],
                            "vehicle": {},
                        }
                    )
                still_queued = [
                    r
                    for r in (ctx.get("detail_rows") or [])
                    if (r.get("status") or "").strip().lower() in ("queued", "failed")
                ]
                if not still_queued:
                    break
                if round_n < _SUBDEALER_MAX_PREP_ROUNDS - 1:
                    time.sleep(_SUBDEALER_RETRY_WAIT_SEC)
    
            try:
                ctx_final = _api_post(api_url, jwt, "/sidecar/subdealer-challan/resolve", resolve_body, timeout=120)
            except Exception as exc:
                return _ret_challan(
                    {
                        "ok": False,
                        "error": f"Resolve failed (final): {exc}",
                        "challan_id": None,
                        "dms_step_messages": [],
                        "vehicle": {},
                    }
                )
            ctx = ctx_final
            rows = ctx_final.get("detail_rows") or []
            not_ready = [r for r in rows if (r.get("status") or "").strip().lower() != "ready"]
            if not_ready:
                parts: list[str] = []
                for r in not_ready[:16]:
                    parts.append(
                        f"id={r.get('challan_staging_id')} status={r.get('status')!r} "
                        f"err={(r.get('last_error') or '')[:160]!r}"
                    )
                return _ret_challan(
                    {
                        "ok": False,
                        "error": "One or more vehicles did not reach Ready — " + "; ".join(parts),
                        "challan_id": None,
                        "dms_step_messages": [],
                        "vehicle": {},
                    }
                )
    
        oc_body = {
            "challan_batch_id": challan_batch_id,
            "dealer_id": dealer_id,
            "last_vehicle_scrape": {} if phase_is_order_only else last_scrape,
        }
        try:
            pkg = _api_post(api_url, jwt, "/sidecar/subdealer-challan/order-context", oc_body, timeout=120)
        except Exception as exc:
            return _ret_challan(
                {
                    "ok": False,
                    "error": f"order-context: {exc}",
                    "challan_id": None,
                    "dms_step_messages": [],
                    "vehicle": {},
                }
            )

        if not pkg.get("ok"):
            return _ret_challan(
                {
                    "ok": False,
                    "error": str(pkg.get("error") or "order-context failed"),
                    "challan_id": None,
                    "dms_step_messages": [],
                    "vehicle": {},
                }
            )
    
        leaf = _safe_challan_artifact_leaf((pkg.get("artifact_leaf") or "").strip() or initial_leaf)
        log_path = (get_challan_artifacts_dir(dealer_id, leaf) / "playwright_challan.txt").resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            challan_dirs_to_sync.append(log_path.parent.resolve())
        except OSError:
            challan_dirs_to_sync.append(log_path.parent)
        if phase_is_order_only:
            log_path.write_text("", encoding="utf-8")
        elif not log_path.exists():
            log_path.write_text("", encoding="utf-8")
    
        dms_values = dict(pkg.get("dms_values") or {})
        dms_values["challan_vin_frame_dump_dir"] = str(log_path.parent.resolve())
    
        _urls_merged = {k: str(v or "") for k, v in dict(pkg.get("urls") or {}).items()}
        for _z in ("enquiry", "precheck", "pdi", "line_items", "reports", "vehicles"):
            _urls_merged[_z] = ""
        for _req in ("contact", "vehicles", "precheck", "pdi", "vehicle", "enquiry", "line_items", "reports"):
            _urls_merged.setdefault(_req, "")
        urls_o = SiebelDmsUrls(**_urls_merged)

        def _challan_order_checkpoint_cb(payload: dict) -> None:
            try:
                on = (payload.get("order_number") or "").strip() if isinstance(payload, dict) else ""
                av = payload.get("attached_vin_count") if isinstance(payload, dict) else None
                body: dict = {
                    "challan_batch_id": challan_batch_id,
                    "dealer_id": dealer_id,
                    "order_number": on or None,
                    "attached_vin_count": int(av) if av is not None else None,
                }
                if body["order_number"] is None and body["attached_vin_count"] is None:
                    return
                _api_post(
                    api_url,
                    jwt,
                    "/sidecar/subdealer-challan/order-checkpoint",
                    body,
                    timeout=60,
                )
            except Exception as _exc:
                logging.warning("subdealer challan order-checkpoint: %s", _exc)
    
        frag = Playwright_Hero_DMS_fill_subdealer_challan_order_only(
            page,
            dms_values,
            urls_o,
            action_timeout_ms=int(pkg.get("action_timeout_ms") or DMS_SIEBEL_ACTION_TIMEOUT_MS),
            nav_timeout_ms=int(pkg.get("nav_timeout_ms") or DMS_SIEBEL_NAV_TIMEOUT_MS),
            content_frame_selector=pkg.get("content_frame_selector"),
            execution_log_path=log_path,
            challan_progress_callback=_challan_order_checkpoint_cb,
        )

        def _append_finalize_challan_log(
            fin_result: dict | None,
            *,
            http_exc: BaseException | None = None,
        ) -> None:
            try:
                ts = _ts_ist_iso()
                with log_path.open("a", encoding="utf-8") as lf:
                    lf.write("\n--- subdealer challan finalize_order (API response) ---\n")
                    if http_exc is not None:
                        lf.write(f"{ts} [NOTE] finalize-order request failed: {http_exc!s}\n")
                    elif fin_result is not None:
                        lf.write(
                            f"{ts} [NOTE] ok={fin_result.get('ok')} error={fin_result.get('error')!r} "
                            f"challan_id={fin_result.get('challan_id')!r}\n"
                        )
                        veh = fin_result.get("vehicle") if isinstance(fin_result.get("vehicle"), dict) else {}
                        if isinstance(veh, dict):
                            lf.write(
                                f"{ts} [NOTE] vehicle order_number="
                                f"{str(veh.get('order_number') or '')[:160]!r} "
                                f"invoice_number={str(veh.get('invoice_number') or '')[:160]!r}\n"
                            )
                    lf.flush()
            except OSError:
                pass

        try:
            fin = _api_post(
                api_url,
                jwt,
                "/sidecar/subdealer-challan/finalize-order",
                {
                    "challan_batch_id": challan_batch_id,
                    "dealer_id": dealer_id,
                    "playwright_result": _slim_subdealer_challan_finalize_playwright_result(frag),
                },
                timeout=120,
            )
            _append_finalize_challan_log(fin)
        except Exception as exc:
            _append_finalize_challan_log(None, http_exc=exc)
            return _ret_challan(
                {
                    "ok": False,
                    "error": f"finalize-order: {exc}",
                    "challan_id": None,
                    "dms_step_messages": list(frag.get("dms_step_messages") or []),
                    "vehicle": dict(frag.get("vehicle") or {}),
                }
            )

        return _ret_challan(
            {
                "ok": bool(fin.get("ok")),
                "error": fin.get("error"),
                "challan_id": fin.get("challan_id"),
                "dms_step_messages": list(fin.get("dms_step_messages") or []),
                "vehicle": dict(fin.get("vehicle") or {}),
            }
        )
    finally:
        _flush_challan_ocr_dirs_to_server()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(payload: dict) -> dict:
    job_type = payload.get("type") or payload.get("job")
    if not job_type:
        return {"success": False, "error": "Missing type"}
    logging.info("dispatch: job_type=%s", job_type)
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
        data = _run_sidecar_playwright_job(lambda: _dispatch_warm_browser(params))
        return {"success": True, "data": data}
    if job_type == "warm_insurance":
        data = _run_sidecar_playwright_job(lambda: _dispatch_warm_insurance(params))
        return {"success": True, "data": data}
    if job_type == "warm_vahan":
        data = _run_sidecar_playwright_job(lambda: _dispatch_warm_vahan(params))
        return {"success": True, "data": data}
    if job_type == "fill_dms":
        data = _run_sidecar_playwright_job(lambda: _dispatch_fill_dms_impl(params))
        return {"success": True, "data": data}
    if job_type == "fill_insurance":
        data = _run_sidecar_playwright_job(lambda: _dispatch_fill_insurance_impl(params))
        return {"success": True, "data": data}
    if job_type == "fill_cpa_alliance_insurance":
        data = _run_sidecar_playwright_job(lambda: _dispatch_fill_cpa_alliance_insurance_impl(params))
        ok = isinstance(data, dict) and bool(data.get("success"))
        return {
            "success": ok,
            "data": data,
            "error": data.get("error") if isinstance(data, dict) else None,
        }
    if job_type == "fill_vahan_batch":
        data = _run_sidecar_playwright_job(lambda: _dispatch_fill_vahan_batch_impl(params))
        return {"success": True, "data": data}
    if job_type == "fill_subdealer_challan":
        data = _run_sidecar_playwright_job(lambda: _fill_subdealer_challan_impl(params))
        return {"success": True, "data": data}
    if job_type == "mirror_challan_parse_artifacts":
        data = _mirror_challan_parse_artifacts_impl(params)
        return {"success": bool(data.get("ok")), "data": data, "error": data.get("error")}
    if job_type == "upload_sale_artifacts":
        data = _dispatch_upload_sale_artifacts_impl(params)
        return {
            "success": bool(data.get("success")),
            "data": data,
            "error": data.get("error"),
        }
    if job_type == "pull_sale_scan_assets":
        data = _dispatch_pull_sale_scan_assets_impl(params)
        return {
            "success": bool(data.get("success")),
            "data": data,
            "error": data.get("error"),
        }
    if job_type == "push_sale_artifacts":
        data = _dispatch_push_sale_artifacts_impl(params)
        return {
            "success": bool(data.get("success")),
            "data": data,
            "error": data.get("error"),
        }
    if job_type == "print_gate_pass_local":
        data = _dispatch_print_gate_pass_local_impl(params)
        return {
            "success": bool(data.get("success")),
            "data": data,
            "error": data.get("error"),
        }
    if job_type == "upload_print_rto_queue_log":
        data = _dispatch_upload_print_rto_queue_log_impl(params)
        return {
            "success": bool(data.get("success")),
            "data": data,
            "error": data.get("error"),
        }
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
    if "--install-playwright-browsers" in sys.argv:
        sys.exit(_cli_install_playwright_browsers_main())

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
