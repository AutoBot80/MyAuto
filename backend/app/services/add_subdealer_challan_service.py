"""Orchestrate subdealer challan: staging → per-vehicle prepare_vehicle → discounts → batch prepare_order → DB commit."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.config import (
    CHALLANS_DIR,
    DMS_BASE_URL,
    DMS_LOGIN_PASSWORD,
    DMS_LOGIN_USER,
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
    dms_automation_is_real_siebel,
    get_ocr_output_dir,
)
from app.repositories import challan_staging as challan_staging_repo
from app.repositories.vehicle_inventory import (
    fetch_lines_for_batch_inventory,
    get_by_id,
    get_discount_for_model,
    update_discount_and_ex_showroom,
    upsert_from_prepare_vehicle_scrape,
)
from app.services.add_subdealer_challan_commit_service import (
    commit_challan_masters,
    update_inventory_ex_showroom_from_order_scrape,
)
from app.services.fill_hero_dms_service import (
    Playwright_Hero_DMS_fill_subdealer_challan_order_only,
    _install_playwright_js_dialog_handler,
    playwright_dms_execution_log_filename,
)
from app.services.handle_browser_opening import get_or_open_site_page
from app.services.hero_dms_playwright_customer_challan import prepare_customer_for_challan
from app.services.hero_dms_playwright_vehicle import prepare_vehicle
from app.services.hero_dms_shared_utilities import SiebelDmsUrls, _ts_ist_iso
from app.services.subdealer_challan_ocr_service import challan_artifact_leaf_name
from app.services.utility_functions import safe_subfolder_name

logger = logging.getLogger(__name__)

RETRY_WAIT_SEC = 3.0
MAX_PREP_ROUNDS = 3


def _note(_list: list[str], msg: str) -> None:
    _list.append(msg)
    logger.info("subdealer_challan: %s", msg)


def run_subdealer_challan_batch(
    *,
    challan_batch_id: uuid.UUID,
    dms_base_url: str,
    dealer_id: int,
) -> dict[str, object]:
    """
    Run full challan pipeline for all rows in ``challan_batch_id``.
    Writes a human-readable trace to ``CHALLANS_DIR/<challan>_<ddmmyyyy>/playwright_challan.txt``
    (same folder convention as OCR upload). Returns ``ok``, ``error``, ``challan_id``, ``dms_step_messages``, ``vehicle``.
    """
    steps: list[str] = []
    out: dict[str, object] = {
        "ok": False,
        "error": None,
        "challan_id": None,
        "dms_step_messages": steps,
        "vehicle": {},
        "challan_log_path": None,
    }

    rows = challan_staging_repo.fetch_batch_rows(challan_batch_id)
    if not rows:
        out["error"] = "No staging rows for this batch."
        return out

    from_dealer_id = int(rows[0]["from_dealer_id"])
    to_dealer_id = int(rows[0]["to_dealer_id"])
    challan_date = rows[0].get("challan_date")
    challan_book = rows[0].get("challan_book_num")
    cb = None if challan_book is None else str(challan_book).strip() or None
    cd = None if challan_date is None else str(challan_date).strip() or None

    leaf = challan_artifact_leaf_name(cb, cd)
    log_path = (CHALLANS_DIR / leaf / "playwright_challan.txt").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out["challan_log_path"] = str(log_path)

    def logln(msg: str) -> None:
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(f"{_ts_ist_iso()}  {msg}\n")
            lf.flush()

    logln("=== subdealer challan run start ===")
    logln(f"challan_batch_id={challan_batch_id} log_file={log_path}")
    logln(f"dealer_id(session)={dealer_id} from_dealer_id={from_dealer_id} to_dealer_id={to_dealer_id}")
    logln(f"challan_book_num={challan_book!r} challan_date={challan_date!r} artifact_folder={leaf}")
    logln(f"staging_row_count={len(rows)}")

    if not dms_automation_is_real_siebel():
        logln("ERROR: DMS_MODE must be real/siebel for subdealer challan automation.")
        out["error"] = "DMS_MODE must be real/siebel for subdealer challan automation."
        return out

    base_url = (dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        logln("ERROR: dms_base_url required")
        out["error"] = "dms_base_url required"
        return out

    logln(f"dms_base_url={base_url[:120]!r}")

    ocr_dir = Path(get_ocr_output_dir(dealer_id)).resolve()
    subfolder = safe_subfolder_name(f"challan_{str(challan_batch_id)[:8]}")
    exec_log = ocr_dir / subfolder / playwright_dms_execution_log_filename()

    urls = SiebelDmsUrls(
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

    page = None
    last_vehicle_scrape: dict = {}

    try:
        page, open_error = get_or_open_site_page(
            base_url,
            "DMS",
            require_login_on_open=True,
        )
        if page is None:
            logln(f"ERROR: could not open DMS: {open_error!r}")
            out["error"] = open_error or "Could not open DMS"
            return out
        logln("DMS tab opened (get_or_open_site_page OK)")
        _install_playwright_js_dialog_handler(page)

        def note(msg: str) -> None:
            _note(steps, msg)

        def ms_done(_label: str) -> None:
            pass

        def step_msg(msg: str) -> None:
            _note(steps, msg)

        def form_trace(*_a, **_k) -> None:
            pass

        for round_n in range(MAX_PREP_ROUNDS):
            batch_rows = challan_staging_repo.fetch_batch_rows(challan_batch_id)
            pending = [r for r in batch_rows if (r.get("status") or "").strip() == "Queued"]
            if not pending:
                logln(f"prepare_vehicle round {round_n + 1}: no Queued rows left")
                break
            logln(f"prepare_vehicle round {round_n + 1}/{MAX_PREP_ROUNDS}: {len(pending)} Queued row(s)")
            for row in pending:
                sid = int(row["challan_staging_id"])
                rc = (row.get("raw_chassis") or "").strip()
                re_ = (row.get("raw_engine") or "").strip()
                dv = {
                    "frame_partial": rc,
                    "engine_partial": re_,
                    "key_partial": "",
                    "battery_partial": "",
                }
                logln(f"  sid={sid} raw_chassis={rc!r} raw_engine={re_!r} → prepare_vehicle")
                ok, err, scraped, _in_tr, _crit, _info = prepare_vehicle(
                    page,
                    dv,
                    urls,
                    nav_timeout_ms=DMS_SIEBEL_NAV_TIMEOUT_MS,
                    action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
                    content_frame_selector=frame_sel,
                    note=note,
                    form_trace=form_trace,
                    ms_done=ms_done,
                    step=step_msg,
                )
                if not ok:
                    err_s = (err or "prepare_vehicle failed")[:2000]
                    logln(f"  sid={sid} prepare_vehicle FAILED: {err_s!r}")
                    challan_staging_repo.update_staging_status(sid, status="Failed", last_error=err_s)
                    continue
                sc = dict(scraped or {})
                last_vehicle_scrape = sc
                fc = (sc.get("full_chassis") or sc.get("frame_num") or "")[:32]
                logln(f"  sid={sid} prepare_vehicle OK full_chassis={fc!r} model={sc.get('model')!r}")
                try:
                    iid = upsert_from_prepare_vehicle_scrape(
                        to_dealer_id=int(row["to_dealer_id"]),
                        vehicle=scraped,
                    )
                    challan_staging_repo.update_staging_status(
                        sid,
                        status="Ready",
                        last_error=None,
                        inventory_line_id=iid,
                    )
                    logln(f"  sid={sid} inventory upsert OK inventory_line_id={iid}")
                except Exception as exc:
                    logger.warning("subdealer_challan: inventory upsert failed: %s", exc)
                    es = str(exc)[:2000]
                    logln(f"  sid={sid} inventory upsert FAILED: {es!r}")
                    challan_staging_repo.update_staging_status(sid, status="Failed", last_error=es)

            still_queued = [
                r
                for r in challan_staging_repo.fetch_batch_rows(challan_batch_id)
                if (r.get("status") or "").strip() == "Queued"
            ]
            if not still_queued:
                break
            if round_n < MAX_PREP_ROUNDS - 1:
                wmsg = f"Waiting {RETRY_WAIT_SEC}s before retry for {len(still_queued)} queued row(s)."
                logln(wmsg)
                _note(steps, wmsg)
                time.sleep(RETRY_WAIT_SEC)

        final_rows = challan_staging_repo.fetch_batch_rows(challan_batch_id)
        not_ready = [r for r in final_rows if (r.get("status") or "").strip() not in ("Ready",)]
        if not_ready:
            logln(f"FINAL prepare_vehicle check: {len(not_ready)} row(s) not Ready (need Ready for order phase)")
            for r in not_ready:
                logln(
                    f"  challan_staging_id={r.get('challan_staging_id')} status={r.get('status')!r} "
                    f"last_error={r.get('last_error')!r}"
                )
            ids_s = ", ".join(str(r.get("challan_staging_id")) for r in not_ready[:20])
            hints: list[str] = []
            for r in not_ready[:8]:
                le = (r.get("last_error") or "").strip()
                if le:
                    hints.append(f"id {r.get('challan_staging_id')}: {le[:180]}")
            hint_txt = (" " + " | ".join(hints)) if hints else ""
            out["error"] = (
                "One or more vehicles did not reach Ready: "
                + ids_s
                + hint_txt
                + f" — see full trace: {log_path}"
            )
            return out

        logln("All staging rows Ready — applying discounts and building order lines")
        inv_ids = [int(r["inventory_line_id"]) for r in final_rows if r.get("inventory_line_id")]
        for r in final_rows:
            iid = r.get("inventory_line_id")
            if not iid:
                continue
            inv = get_by_id(int(iid))
            if not inv:
                continue
            model = (inv.get("model") or "").strip()
            if not model:
                continue
            disc = get_discount_for_model(from_dealer_id, model)
            if disc is not None:
                update_discount_and_ex_showroom(int(iid), discount=float(disc))

        inv_rows = fetch_lines_for_batch_inventory(inv_ids)
        order_lines: list[dict] = []
        for ir in inv_rows:
            ch = (ir.get("chassis_no") or "").strip()
            if not ch:
                continue
            d = ir.get("discount")
            disc_s = "" if d is None else f"{float(d):.2f}"
            order_lines.append({"full_chassis": ch, "line_item_discount": disc_s})

        if not order_lines:
            logln("ERROR: No order lines (missing chassis on inventory)")
            out["error"] = "No order lines to attach (missing chassis on inventory)."
            return out

        logln(f"Order phase: {len(order_lines)} line(s); Playwright DMS execution log: {exec_log}")
        dms_values: dict = {}
        prepare_customer_for_challan(
            dms_values,
            to_dealer_id=to_dealer_id,
            from_dealer_id=from_dealer_id,
        )
        dms_values["order_line_vehicles"] = order_lines
        dms_values["_challan_last_vehicle"] = last_vehicle_scrape

        frag = Playwright_Hero_DMS_fill_subdealer_challan_order_only(
            page,
            dms_values,
            urls,
            action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
            nav_timeout_ms=DMS_SIEBEL_NAV_TIMEOUT_MS,
            content_frame_selector=frame_sel,
            execution_log_path=exec_log,
        )
        out["vehicle"] = frag.get("vehicle") or {}
        out["dms_step_messages"] = list(frag.get("dms_step_messages") or steps)
        if frag.get("error"):
            logln(f"ERROR order phase: {frag.get('error')!r}")
            out["error"] = str(frag.get("error"))
            return out

        veh_out = dict(frag.get("vehicle") or {})
        update_inventory_ex_showroom_from_order_scrape(inv_ids, veh_out)

        oid = (veh_out.get("order_number") or "").strip() or None
        iid = (veh_out.get("invoice_number") or "").strip() or None

        cid = commit_challan_masters(
            challan_date=challan_date,
            challan_book_num=challan_book,
            dealer_from=from_dealer_id,
            dealer_to=to_dealer_id,
            inventory_line_ids=inv_ids,
            order_number=oid,
            invoice_number=iid,
        )
        out["challan_id"] = cid
        for r in final_rows:
            if r.get("challan_staging_id"):
                challan_staging_repo.update_staging_status(
                    int(r["challan_staging_id"]),
                    status="Committed",
                    last_error=None,
                )

        logln(f"SUCCESS challan_master_id={cid} order={oid!r} invoice={iid!r}")
        out["ok"] = True
        return out

    except PlaywrightTimeout as e:
        logln(f"EXCEPTION PlaywrightTimeout: {e!s}")
        out["error"] = f"Siebel timeout: {e!s}"
        logger.warning("subdealer_challan: %s", e)
        return out
    except Exception as e:
        logln(f"EXCEPTION: {e!s}")
        out["error"] = str(e)
        logger.warning("subdealer_challan: %s", e, exc_info=True)
        return out


def create_challan_staging_batch(
    *,
    from_dealer_id: int,
    to_dealer_id: int,
    challan_date: str | None,
    challan_book_num: str | None,
    lines: list[dict],
) -> uuid.UUID:
    """Insert Queued rows and return batch id."""
    batch_id = uuid.uuid4()
    challan_staging_repo.insert_staging_rows(
        challan_batch_id=batch_id,
        challan_date=challan_date,
        challan_book_num=challan_book_num,
        from_dealer_id=from_dealer_id,
        to_dealer_id=to_dealer_id,
        lines=lines,
    )
    return batch_id
