"""
Local test wrapper: Hero DMS — ``prepare_customer`` (``hero_dms_playwright_customer``).

Opens DMS (login if needed), then runs ``prepare_customer`` only — **no** ``prepare_vehicle``.

Defaults match View Customers screenshot (Narayan / 9587946074 / Splendor+ BLA).

Configure ``backend/.env`` like Fill DMS: ``DMS_BASE_URL``, ``DMS_MODE``, login,
``DMS_REAL_URL_CONTACT``, ``DMS_REAL_URL_VEHICLE``, ``DMS_SIEBEL_*``.

Environment:
  CUSTOMER_TEST_MOBILE_PHONE           default: 9587946074
  CUSTOMER_TEST_FIRST_NAME             default: Narayan
  CUSTOMER_TEST_MANUAL_LOGIN           default: 1 — do **not** auto-submit cached/env creds; wait for you
                                       to finish typing Login (up to ``DMS_LOGIN_MANUAL_WAIT_MS``).
  CUSTOMER_TEST_POST_LOGIN_WAIT_SEC    default: 15 — extra settle after login before Find/Go
  CUSTOMER_TEST_PAUSE_BEFORE_EXIT      default: 1
  CUSTOMER_TEST_PLAYWRIGHT_LOG_DIR     optional — default ``Testing Wrappers/playwright_dms_logs``
  CUSTOMER_TEST_PLAYWRIGHT_TRACE       default: 0 — Playwright Trace Viewer ``.zip`` next to log

Override any default field with ``CUSTOMER_TEST_<UPPER_SNAKE>`` matching the dict key.

Double-click ``test_dms_prepare_customer.bat`` or:
  python test_dms_prepare_customer.py
from this folder (repo root = parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_dms_prepare_customer")

DEFAULT_MOBILE = "9587946074"
_WRAPPER_DIR = Path(__file__).resolve().parent


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()


def _build_dms_values() -> dict[str, str]:
    defaults: dict[str, str] = {
        "mobile_phone": DEFAULT_MOBILE,
        "first_name": "Narayan",
        "last_name": "",
        "address_line_1": "Nam, Bharatpur, Rajasthan - 321642, Bharatpur, RAJASTHAN, 321642",
        "state": "RAJASTHAN",
        "pin_code": "321642",
        "landline": "9358691024",
        "care_of": "S/O Dinesh",
        "key_partial": "",
        "frame_partial": "",
        "engine_partial": "",
        "aadhar_id": "",
        "city": "Bharatpur",
        "district": "Bharatpur",
        "tehsil": "Bharatpur",
        "age": "",
        "gender": "MALE",
        "finance_required": "N",
        "financier_name": "",
        "dms_contact_path": "found",
    }
    out: dict[str, str] = {}
    for k, dflt in defaults.items():
        out[k] = _env_str(f"CUSTOMER_TEST_{k.upper()}", dflt)
    fn = (out.get("financier_name") or "").strip()
    if fn.upper() in ("NULL", "NONE", "NIL"):
        out["financier_name"] = ""
    return out


def _build_vehicle_seed() -> dict[str, object]:
    """Seed ``out['vehicle']`` (no ``prepare_vehicle`` in this wrapper)."""
    defaults: dict[str, str] = {
        "model": "SPLENDOR +",
        "color": "BLA",
        "variant": "",
        "sku": "",
        "year_of_mfg": "2026",
        "full_chassis": "",
        "full_engine": "",
        "raw_key_num": "",
        "invoice_number": "11870BE26S473",
    }
    vm: dict[str, object] = {}
    for k, dflt in defaults.items():
        vm[k] = _env_str(f"CUSTOMER_TEST_{k.upper()}", dflt)
    return vm


def _manual_login_mode() -> bool:
    return _env_str("CUSTOMER_TEST_MANUAL_LOGIN", "1").lower() in ("1", "true", "yes", "y")


def _wait_for_dms_login_before_automation(
    page,
    *,
    log_path: Path,
    manual_login: bool,
    note,
) -> tuple[object | None, str | None]:
    """
    Block until Siebel is past the login surface.

    Manual mode skips auto-submit on open and polls up to ``DMS_LOGIN_MANUAL_WAIT_MS``.
    """
    from app.config import DMS_LOGIN_MANUAL_WAIT_MS
    from app.services.handle_browser_opening import (
        _install_dms_login_window_capture,
        _is_ready_after_login_page,
        _wait_login_or_prompt_after_open,
    )

    if manual_login:
        try:
            _install_dms_login_window_capture(page, log_path)
        except Exception:
            pass
        note(
            f"Manual login mode — type user/password and click Login in the browser "
            f"(waiting up to {DMS_LOGIN_MANUAL_WAIT_MS // 1000}s)."
        )
        pg, err = _wait_login_or_prompt_after_open(page, "DMS", log_path=str(log_path))
        if pg is None:
            return None, err or "Siebel login not completed in time."
        page = pg
    elif not _is_ready_after_login_page(page):
        note("Session not ready after open — waiting for Siebel login to finish.")
        pg, err = _wait_login_or_prompt_after_open(page, "DMS", log_path=str(log_path))
        if pg is None:
            return None, err or "Siebel login not completed in time."
        page = pg

    # Require two consecutive ready polls so a brief redirect flash does not start automation early.
    ready_streak = 0
    deadline = time.monotonic() + max(15.0, DMS_LOGIN_MANUAL_WAIT_MS / 1000.0)
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                return None, "DMS browser tab closed while waiting for login."
            if _is_ready_after_login_page(page):
                ready_streak += 1
                if ready_streak >= 2:
                    note("Siebel session ready — starting automation shortly.")
                    return page, None
            else:
                ready_streak = 0
        except Exception:
            ready_streak = 0
        time.sleep(0.45)

    return None, "Timed out waiting for a stable Siebel session after login."


class _PlaywrightDmsWrapperLog:
    def __init__(self) -> None:
        self._path: Path | None = None
        self._fp: object | None = None

    def attach(self, path: Path, fp: object) -> None:
        self._path = path
        self._fp = fp

    def _exec_log(self, prefix: str, msg: str) -> None:
        fp = self._fp
        if fp is None or not (msg or "").strip():
            return
        from app.services.hero_dms_shared_utilities import _ts_ist_iso

        try:
            fp.write(f"{_ts_ist_iso()} [{prefix}] {msg}\n")
            fp.flush()
        except OSError:
            pass

    def form_trace(self, siebel_step: str, form_name: str, action: str, **fields: object) -> None:
        segments = [f"siebel_step={siebel_step}", f"form={form_name}", f"action={action}"]
        for key in sorted(fields.keys()):
            val = fields[key]
            if val is None:
                continue
            if isinstance(val, bool):
                segments.append(f"{key}={val}")
                continue
            v = str(val).replace("\n", " ").strip()
            if v == "":
                continue
            if len(v) > 500:
                v = v[:497] + "..."
            segments.append(f"{key}={v!r}")
        self._exec_log("FORM", " | ".join(segments))

    def step(self, msg: str) -> None:
        logger.info("[step] %s", msg)
        self._exec_log("STEP", msg)

    def note(self, msg: str) -> None:
        logger.info("%s", msg)
        self._exec_log("NOTE", msg)

    def end(self, *, exit_code: int, error: str | None) -> None:
        from app.services.hero_dms_shared_utilities import _ts_ist_iso

        fp = self._fp
        if fp is None:
            return
        det = (error or "").strip()
        try:
            fp.write(f"\n{_ts_ist_iso()} [END] exit_code={exit_code} error={det!r}\n")
            fp.flush()
        except OSError:
            pass
        try:
            fp.close()
        except Exception:
            pass
        self._fp = None


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_REAL_URL_CONTACT,
        DMS_REAL_URL_VEHICLE,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        DMS_SIEBEL_MOBILE_ARIA_HINTS,
        DMS_SIEBEL_NAV_TIMEOUT_MS,
        dms_automation_is_real_siebel,
    )
    from app.services.fill_hero_dms_service import (
        _install_playwright_js_dialog_handler,
        playwright_dms_execution_log_filename,
        write_playwright_dms_execution_log_initial,
    )
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_customer import (
        _validate_contact_find_first_name,
        prepare_customer,
    )
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls, _ts_ist_iso

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set — add it to backend/.env.")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real / siebel / live / production / hero for this wrapper.")
        return 1

    contact_url = (DMS_REAL_URL_CONTACT or "").strip()
    if not contact_url:
        logger.error("DMS_REAL_URL_CONTACT is empty — set it in backend/.env.")
        return 1

    try:
        post_login_wait = max(0.0, float(os.getenv("CUSTOMER_TEST_POST_LOGIN_WAIT_SEC") or "15"))
    except ValueError:
        post_login_wait = 15.0

    manual_login = _manual_login_mode()

    _pause_before_exit = (os.getenv("CUSTOMER_TEST_PAUSE_BEFORE_EXIT") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    dms_values = _build_dms_values()
    mobile = (dms_values.get("mobile_phone") or "").strip()
    first = (dms_values.get("first_name") or "").strip()
    care_of = (dms_values.get("care_of") or "").strip()
    addr = (dms_values.get("address_line_1") or "").strip()
    pin = (dms_values.get("pin_code") or "").strip()

    fn_ok, fn_msg = _validate_contact_find_first_name(first)
    if not fn_ok:
        logger.error("%s Set CUSTOMER_TEST_FIRST_NAME in the environment or .env.", fn_msg)
        return 1

    log_dir_str = (os.getenv("CUSTOMER_TEST_PLAYWRIGHT_LOG_DIR") or "").strip()
    log_dir = Path(log_dir_str) if log_dir_str else _WRAPPER_DIR / "playwright_dms_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / playwright_dms_execution_log_filename()
    write_playwright_dms_execution_log_initial(
        log_path,
        dms_values,
        execution_log_client_api_base_url=None,
        execution_log_http_request_base_url=None,
    )
    logger.info("Playwright DMS execution log (this run): %s", log_path.resolve())
    logger.info("Mobile: %s  First name: %s  manual_login=%s", mobile, first, manual_login)

    trace_on = _env_str("CUSTOMER_TEST_PLAYWRIGHT_TRACE", "0").lower() in ("1", "true", "yes", "y")
    trace_path = log_path.with_name(log_path.stem + "_trace.zip") if trace_on else None

    _tmo = int(DMS_SIEBEL_ACTION_TIMEOUT_MS or 8000)
    _nav = int(DMS_SIEBEL_NAV_TIMEOUT_MS or 90000)
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None

    urls = SiebelDmsUrls(
        contact=contact_url,
        vehicles="",
        precheck="",
        pdi="",
        vehicle=(DMS_REAL_URL_VEHICLE or "").strip(),
        enquiry="",
        line_items="",
        reports="",
    )

    out: dict[str, Any] = {
        "vehicle": _build_vehicle_seed(),
        "error": None,
        "dms_siebel_forms_filled": False,
        "dms_siebel_notes": [],
        "dms_milestones": [],
        "dms_step_messages": [],
    }

    run_log = _PlaywrightDmsWrapperLog()
    exit_code = 1
    run_error: str | None = None
    page = None
    trace_started = False
    log_fp = None

    def ms_done(label: str) -> None:
        m = out["dms_milestones"]
        if label not in m:
            m.append(label)
            run_log._exec_log("MILESTONE", label)

    def log_vehicle_snapshot(stage: str) -> None:
        veh = out.get("vehicle") or {}
        if log_fp is None or not isinstance(veh, dict):
            return
        try:
            log_fp.write(f"\n--- vehicle_snapshot ({stage}) ---\n")
            for k in sorted(veh.keys()):
                v = veh.get(k)
                if v is None:
                    continue
                s = str(v).replace("\n", " ").replace("\r", " ").strip()
                if not s:
                    continue
                if len(s) > 2000:
                    s = s[:1997] + "..."
                log_fp.write(f"{k}={s!r}\n")
            log_fp.flush()
        except OSError:
            pass

    try:
        page, open_err = get_or_open_site_page(
            (DMS_BASE_URL or "").strip(),
            "DMS",
            require_login_on_open=not manual_login,
            playwright_dms_execution_log_path=str(log_path),
        )
        if page is None:
            with open(log_path, "a", encoding="utf-8") as fp:
                fp.write("\n--- automation_trace ---\n\n")
                fp.write(f"{_ts_ist_iso()} [NOTE] get_or_open_site_page failed: {open_err!r}\n")
                fp.write(f"{_ts_ist_iso()} [END] exit_code=1 open_error={str(open_err or '')!r}\n")
            logger.error("Could not open DMS: %s", open_err)
            return 1

        log_fp = open(log_path, "a", encoding="utf-8")
        log_fp.write("\n--- automation_trace ---\n\n")
        log_fp.write(
            "Legend: [STEP]/[NOTE]/[MILESTONE] = operator narrative; [FORM] = siebel_step + "
            "Siebel form/screen + action + fields/values being applied on that form.\n\n"
        )
        log_fp.flush()
        run_log.attach(log_path, log_fp)

        note = run_log.note
        step = run_log.step
        form_trace = run_log.form_trace

        page.set_default_timeout(_tmo)
        _install_playwright_js_dialog_handler(page)

        if trace_path is not None:
            page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
            trace_started = True
            note(f"Playwright trace enabled; will write {trace_path.name!r} when the run finishes.")

        page, login_err = _wait_for_dms_login_before_automation(
            page,
            log_path=log_path,
            manual_login=manual_login,
            note=note,
        )
        if page is None:
            run_error = login_err or "Siebel login wait failed."
            note(run_error)
            logger.error("%s", run_error)
            exit_code = 1
        else:
            from app.services.hero_dms_shared_utilities import _siebel_after_goto_wait

            _siebel_after_goto_wait(page, floor_ms=4500)
            if post_login_wait > 0:
                logger.info(
                    "Post-login settle: waiting %.1f s before Find/Go "
                    "(set CUSTOMER_TEST_POST_LOGIN_WAIT_SEC to change).",
                    post_login_wait,
                )
                time.sleep(post_login_wait)

            step("Test wrapper: prepare_customer only (no prepare_vehicle).")
            note(f"out['vehicle'] seeded with {len(out.get('vehicle') or {})} key(s) from wrapper defaults.")

            step("Begin prepare_customer.")
            ok_customer = prepare_customer(
                page,
                dms_values,
                urls,
                out,
                contact_url=contact_url,
                mobile=mobile,
                video_first_name=first,
                care_of=care_of,
                addr=addr,
                pin=pin,
                action_timeout_ms=_tmo,
                nav_timeout_ms=_nav,
                content_frame_selector=frame_sel,
                mobile_aria_hints=list(DMS_SIEBEL_MOBILE_ARIA_HINTS),
                note=note,
                step=step,
                form_trace=form_trace,
                ms_done=ms_done,
                log_fp=log_fp,
                log_vehicle_snapshot=log_vehicle_snapshot,
                playwright_dms_log_path=log_path,
            )
            run_error = (out.get("error") or "").strip() or None
            if not ok_customer or run_error:
                run_error = run_error or "prepare_customer returned False."
                logger.error("prepare_customer failed: %s", run_error)
                note(run_error)
                exit_code = 1
            else:
                note(
                    "prepare_customer finished — browser left open for inspection "
                    "(no prepare_order in this wrapper)."
                )
                logger.info("Milestones: %s", out.get("dms_milestones"))
                exit_code = 0

    except Exception as exc:
        logger.exception("test_dms_prepare_customer failed")
        exit_code = 1
        run_error = str(exc)
        try:
            run_log.note(f"Unhandled exception: {exc!r}")
        except Exception:
            pass
    finally:
        if trace_started and page is not None and trace_path is not None:
            try:
                if not page.is_closed():
                    page.context.tracing.stop(path=str(trace_path))
                    logger.info("Playwright trace saved: %s", trace_path.resolve())
            except Exception as exc:
                logger.warning("Playwright tracing.stop failed: %s", exc)
        run_log.end(exit_code=exit_code, error=run_error)

        try:
            retain_automation_browser_for_operator_manual_close()
        except Exception as exc:
            logger.debug("retain_automation_browser_for_operator_manual_close: %s", exc)
        if _pause_before_exit:
            logger.info(
                "Press Enter in this console to exit (browser stays open). "
                "Set CUSTOMER_TEST_PAUSE_BEFORE_EXIT=0 to skip."
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
