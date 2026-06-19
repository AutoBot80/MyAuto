"""
Local test wrapper: Hero DMS — **Add Enquiry** (Opportunity Form:New → fill → Ctrl+S).

Runs **Contact Find** with mobile + first name (same helper as production), then always calls
``_add_enquiry_opportunity`` — no check for existing contact rows or open enquiries (test-only shortcut).

This is **not** the separate **Generate Booking** button on the sales order flow (that lives in
``hero_dms_shared_utilities._try_click_generate_booking`` / invoice create-order).

Configure ``backend/.env`` like Fill DMS: ``DMS_BASE_URL``, ``DMS_MODE``, login, ``DMS_REAL_URL_CONTACT``,
``DMS_REAL_URL_VEHICLE``, ``DMS_SIEBEL_*``.

Environment:
  ENQUIRY_TEST_SKIP_CONTACT_FIND     default: 0 — if 1, skip Find/Go; you must already be on a Siebel view
                                     where **Enquiry** tab and **Opportunity Form:New** work.
  ENQUIRY_TEST_POST_LOGIN_WAIT_SEC   default: 2 — seconds after browser open before automation.
  ENQUIRY_TEST_PAUSE_BEFORE_EXIT     default: 1 — wait for Enter before exit (keeps browser).
  ENQUIRY_TEST_PLAYWRIGHT_LOG_DIR    optional — directory for ``Playwright_DMS_*.txt`` (default:
                                     ``Testing Wrappers/playwright_dms_logs``).
  ENQUIRY_TEST_PLAYWRIGHT_TRACE      default: 0 — if 1, record a Playwright Trace Viewer ``.zip`` next to
                                     the text log (same stem + ``_trace.zip``).

Writes the same **Playwright_DMS_ddmmyyyy_hhmmss.txt** style as Fill DMS: pre-login header +
``--- login_phase_capture ---`` (appended during open/login), then ``--- automation_trace ---`` with
``[STEP]`` / ``[NOTE]`` / ``[FORM]`` lines and a final ``[END]`` line.

Override any default field with ``ENQUIRY_TEST_<UPPER_SNAKE>`` matching the dict key, e.g.
``ENQUIRY_TEST_MOBILE_PHONE=9999999999``, ``ENQUIRY_TEST_CITY=Bharatpur``.

Double-click ``test_add_enquiry_opportunity.bat`` or:
  python test_add_enquiry_opportunity.py
from this folder (repo root = parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_add_enquiry_opportunity")


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()


def _build_dms_values() -> dict:
    """
    Keys consumed by ``_add_enquiry_opportunity`` / nested helpers (see docstring in that function).

    **Required for the opportunity form:**
      mobile_phone, first_name, last_name, aadhar_id (UIN last 4),
      state, pin_code, address_line_1 (comma form affects parsed Address Line 1),
      city, district (or city used as fallback), tehsil (or city used as fallback),
      age **or** date_of_birth, gender (M/F/O, MALE/FEMALE, etc.),
      finance_required (Y/N) **or** financier_name (defaults finance Y if financier set).

    **Optional / best-effort:**
      landline, alt_phone_num, finance_required, financier_name,
      key_partial / key_num, battery_partial / battery_num / battery,
      frame_partial, engine_partial (logging / traces).

    **vehicle_merge** (separate dict): model, year_of_mfg (YYYY), color required; ``sku`` helps Variant pick.
    """
    defaults: dict[str, str] = {
        "mobile_phone": "8302588348",
        "first_name": "Sunil",
        "last_name": "Kumar",
        "address_line_1": "Dhanota, Bharatpur, Rajasthan- 321303",
        "state": "RAJASTHAN",
        "pin_code": "321303",
        "landline": "9568564536",
        "care_of": "S/o Atar Singh",
        "key_partial": "1360",
        "frame_partial": "83631",
        "engine_partial": "51406",
        "aadhar_id": "9698",
        # Address / demographics (required on opportunity form; not inferred from address_line_1 alone)
        "city": "Bharatpur",
        "district": "Bharatpur",
        "tehsil": "Bharatpur",
        "age": "35",
        "gender": "MALE",
        "finance_required": "N",
        "financier_name": "",  # empty: no financier; keeps Finance Required = N with production logic
    }
    out: dict[str, str] = {}
    for k, dflt in defaults.items():
        env_k = f"ENQUIRY_TEST_{k.upper()}"
        out[k] = _env_str(env_k, dflt)
    # Treat literal NULL/NONE from env as empty (e.g. financier_name=NULL)
    fn = (out.get("financier_name") or "").strip()
    if fn.upper() in ("NULL", "NONE", "NIL"):
        out["financier_name"] = ""
    out["dealer_enquiry_address"] = {
        "city": "Bharatpur",
        "state": "RAJASTHAN",
        "district": "Bharatpur",
    }
    return out


def _build_vehicle_merge() -> dict:
    """Fields aligned with ``prepare_vehicle`` / ``out['vehicle']`` for Add Enquiry."""
    defaults: dict[str, str] = {
        "full_chassis": "MBLHAW488T5B83631",
        "full_engine": "HA11F7T5B51406",
        "raw_key_num": "1360",
        "model": "SPLENDOR +",
        "color": "BHG",
        "variant": "HSPLMDRSCFIBHG",
        "sku": "HSPLMDRSCFIBHG",
        "cubic_capacity": "100",
        "seating_capacity": "2",
        "body_type": "Open",
        "vehicle_type": "MOTORCYCLE WITH GEAR",
        "num_cylinders": "1",
        "year_of_mfg": "2026",
        "in_transit": "False",
        "inventory_location": "11870 DEALER SHOWROOM",
    }
    vm: dict[str, object] = {}
    for k, dflt in defaults.items():
        env_k = f"ENQUIRY_TEST_{k.upper()}"
        raw = _env_str(env_k, dflt)
        if k == "in_transit":
            vm[k] = raw.lower() in ("1", "true", "yes", "y")
        else:
            vm[k] = raw
    return vm


_WRAPPER_DIR = Path(__file__).resolve().parent


class _PlaywrightDmsWrapperLog:
    """Append ``[STEP]`` / ``[NOTE]`` / ``[FORM]`` lines (same style as Fill DMS) to the run log file."""

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

    def end(self, *, exit_code: int, enquiry_number: str | None, detail: str | None) -> None:
        from app.services.hero_dms_shared_utilities import _ts_ist_iso

        fp = self._fp
        if fp is None:
            return
        enq = (enquiry_number or "").strip()
        det = (detail or "").strip()
        try:
            fp.write(
                f"\n{_ts_ist_iso()} [END] exit_code={exit_code} "
                f"enquiry_number={enq!r} detail={det!r}\n"
            )
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
        _add_enquiry_opportunity,
        _contact_view_find_by_mobile_strategy_two,
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

    skip_find = _env_str("ENQUIRY_TEST_SKIP_CONTACT_FIND", "0").lower() in ("1", "true", "yes", "y")
    try:
        post_login_wait = max(0.0, float(os.getenv("ENQUIRY_TEST_POST_LOGIN_WAIT_SEC") or "2"))
    except ValueError:
        post_login_wait = 2.0

    _pause_before_exit = (os.getenv("ENQUIRY_TEST_PAUSE_BEFORE_EXIT") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    dms_values = _build_dms_values()
    vehicle_merge = _build_vehicle_merge()

    log_dir_str = (os.getenv("ENQUIRY_TEST_PLAYWRIGHT_LOG_DIR") or "").strip()
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

    trace_on = _env_str("ENQUIRY_TEST_PLAYWRIGHT_TRACE", "0").lower() in ("1", "true", "yes", "y")
    trace_path = log_path.with_name(log_path.stem + "_trace.zip") if trace_on else None

    _tmo = int(DMS_SIEBEL_ACTION_TIMEOUT_MS or 8000)
    _nav = int(DMS_SIEBEL_NAV_TIMEOUT_MS or 90000)
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
    mobile = (dms_values.get("mobile_phone") or "").strip()
    first_name = (dms_values.get("first_name") or "").strip()

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

    run_log = _PlaywrightDmsWrapperLog()
    exit_code = 1
    run_enq: str | None = None
    run_detail: str | None = None
    page = None
    trace_started = False

    try:
        page, open_err = get_or_open_site_page(
            (DMS_BASE_URL or "").strip(),
            "DMS",
            require_login_on_open=True,
            playwright_dms_execution_log_path=str(log_path),
        )
        if page is None:
            with open(log_path, "a", encoding="utf-8") as fp:
                fp.write("\n--- automation_trace ---\n\n")
                fp.write(f"{_ts_ist_iso()} [NOTE] get_or_open_site_page failed: {open_err!r}\n")
                fp.write(f"{_ts_ist_iso()} [END] exit_code=1 open_error={str(open_err or '')!r}\n")
            logger.error("Could not open DMS: %s", open_err)
            return 1

        fp = open(log_path, "a", encoding="utf-8")
        fp.write("\n--- automation_trace ---\n\n")
        fp.write(
            "Legend: [STEP]/[NOTE]/[MILESTONE] = operator narrative; [FORM] = siebel_step + "
            "Siebel form/screen + action + fields/values being applied on that form.\n\n"
        )
        fp.flush()
        run_log.attach(log_path, fp)

        note = run_log.note
        step = run_log.step
        form_trace = run_log.form_trace

        page.set_default_timeout(_tmo)
        _install_playwright_js_dialog_handler(page)

        if trace_path is not None:
            page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
            trace_started = True
            note(f"Playwright trace enabled; will write {trace_path.name!r} when the run finishes.")

        if post_login_wait > 0:
            logger.info(
                "Waiting %.1f s before automation (complete Siebel login if needed; "
                "set ENQUIRY_TEST_POST_LOGIN_WAIT_SEC to change).",
                post_login_wait,
            )
            time.sleep(post_login_wait)

        if not skip_find:
            step("Contact Find (mobile + first name), then Add Enquiry (test wrapper; no existence checks).")
            ok_find = _contact_view_find_by_mobile_strategy_two(
                page,
                contact_url=contact_url,
                mobile=mobile,
                first_name=first_name,
                nav_timeout_ms=_nav,
                action_timeout_ms=_tmo,
                content_frame_selector=frame_sel,
                mobile_aria_hints=list(DMS_SIEBEL_MOBILE_ARIA_HINTS),
                note=note,
                step=step,
                stage_msg_mobile_only="Test wrapper: Find customer by mobile (Contact view).",
                stage_msg_mobile_and_first="Test wrapper: Find customer by mobile + first name (Contact view).",
            )
            if not ok_find:
                run_detail = "Contact Find/Go did not complete — check .env and Siebel selectors."
                note(run_detail)
                exit_code = 1
            else:
                step("Begin _add_enquiry_opportunity.")
                ok, detail, enq_no = _add_enquiry_opportunity(
                    page,
                    dms_values,
                    urls,
                    action_timeout_ms=_tmo,
                    nav_timeout_ms=_nav,
                    content_frame_selector=frame_sel,
                    note=note,
                    form_trace=form_trace,
                    vehicle_merge=vehicle_merge,
                )
                run_detail = detail
                logger.info("Result: ok=%s enquiry#=%r detail=%r", ok, enq_no, detail)
                if not ok:
                    exit_code = 1
                elif not (enq_no or "").strip():
                    run_detail = "Add Enquiry reported success but Enquiry# is empty."
                    logger.error("%s", run_detail)
                    note(run_detail)
                    exit_code = 1
                else:
                    run_enq = (enq_no or "").strip()
                    note(f"Done. Enquiry#={run_enq!r} — leave the browser open to inspect Siebel.")
                    exit_code = 0
        else:
            note(
                "ENQUIRY_TEST_SKIP_CONTACT_FIND=1 — skipping Find; ensure Enquiry → Opportunity Form:New is reachable."
            )
            step("Begin _add_enquiry_opportunity (skip_find).")
            ok, detail, enq_no = _add_enquiry_opportunity(
                page,
                dms_values,
                urls,
                action_timeout_ms=_tmo,
                nav_timeout_ms=_nav,
                content_frame_selector=frame_sel,
                note=note,
                form_trace=form_trace,
                vehicle_merge=vehicle_merge,
            )
            run_detail = detail
            logger.info("Result: ok=%s enquiry#=%r detail=%r", ok, enq_no, detail)
            if not ok:
                exit_code = 1
            elif not (enq_no or "").strip():
                run_detail = "Add Enquiry reported success but Enquiry# is empty."
                logger.error("%s", run_detail)
                note(run_detail)
                exit_code = 1
            else:
                run_enq = (enq_no or "").strip()
                note(f"Done. Enquiry#={run_enq!r} — leave the browser open to inspect Siebel.")
                exit_code = 0

    except Exception as exc:
        logger.exception("test_add_enquiry_opportunity failed")
        exit_code = 1
        run_detail = str(exc)
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
        run_log.end(exit_code=exit_code, enquiry_number=run_enq, detail=run_detail)

        try:
            retain_automation_browser_for_operator_manual_close()
        except Exception as exc:
            logger.debug("retain_automation_browser_for_operator_manual_close: %s", exc)
        if _pause_before_exit:
            logger.info(
                "Press Enter in this console to exit (browser stays open). "
                "Set ENQUIRY_TEST_PAUSE_BEFORE_EXIT=0 to skip."
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
