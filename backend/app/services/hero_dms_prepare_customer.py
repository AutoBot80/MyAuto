"""
Siebel Fill DMS — ``prepare_customer`` phase (Find Contact through Add customer payment + collate).

Extracted from ``fill_hero_dms_service.Playwright_Hero_DMS_fill`` for a clear pipeline:
``prepare_vehicle`` → ``prepare_customer`` → ``prepare_order``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Page

from app.services.hero_dms_shared_utilities import (
    SiebelDmsUrls,
    _ordered_frames,
    _safe_page_wait,
    _write_playwright_contact_scrape_section,
)
from app.services.hero_dms_playwright_customer import (
    _add_customer_payment,
    _add_enquiry_opportunity,
    _click_nth_mobile_title_drilldown,
    _contact_find_title_sweep_for_enquiry,
    _contact_mobile_drilldown_plans,
    _contact_view_find_by_mobile_strategy_two,
    _find_contact_mobile_first_grid_counts,
    _siebel_try_click_mobile_search_hit_link,
    _siebel_ui_suggests_contact_match_mobile_first,
    _siebel_video_branch2_address_postal_and_save,
    _siebel_video_path_after_find_go_to_all_enquiries,
)

logger = logging.getLogger(__name__)


def prepare_customer(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    out: dict[str, Any],
    *,
    contact_url: str,
    mobile: str,
    video_first_name: str,
    care_of: str,
    addr: str,
    pin: str,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    note: Callable[..., None],
    step: Callable[..., None],
    form_trace: Callable[..., None] | None,
    ms_done: Callable[[str], None] | None,
    log_fp: Any,
    log_vehicle_snapshot: Callable[[str], None],
) -> bool:
    """
    Contact Find → enquiry sweep / Add Enquiry → Relation's Name → payment → ``dms_customer_master_collated``.
    Returns ``False`` if ``out[\"error\"]`` was set.
    """
    step(
        "Video SOP (Find Contact Enquiry): Find → Contact → mobile + first name → Go; "
        "branch A when N=0 (Add Enquiry) else title sweep for Open enquiry; branch (2) Address+pin "
        "when no Open; Relation's Name → Payments → booking path."
    )
    if callable(form_trace):
        form_trace(
            "v1_find_contact",
            "Global Find → Contact (mobile + exact first name when present, else mobile-only) + Go",
            "goto_contact_find_URL_then_prepare_Find_Contact_fill_mobile_optional_first_FindGo",
            contact_url_truncated=contact_url[:200],
            mobile_phone=mobile,
            first_name=video_first_name,
        )
    ok_find = _contact_view_find_by_mobile_strategy_two(
        page,
        contact_url=contact_url,
        mobile=mobile,
        first_name=video_first_name,
        nav_timeout_ms=nav_timeout_ms,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        mobile_aria_hints=mobile_aria_hints,
        note=note,
        step=step,
        stage_msg_mobile_only="Video SOP: Find customer by mobile (Contact view; first name blank).",
        stage_msg_mobile_and_first="Video SOP: Find customer by mobile + first name (Contact view).",
    )
    if not ok_find:
        step("Stopped: could not complete Find by mobile + first name on contact view.")
        out["error"] = (
            "Siebel: video SOP — could not fill mobile/first name or run Find/Go on the contact view. "
            "Check Find pane, iframe selectors, and DMS_SIEBEL_* tuning."
        )
        return False
    _grid_first_hint = _siebel_ui_suggests_contact_match_mobile_first(
        page, mobile, video_first_name
    )
    note(
        f"DECISION: contact_table_match_mobile_first_after_find={_grid_first_hint!r} "
        "(informational; branch A/B uses drilldown row count)."
    )

    _video_plans_m = _contact_mobile_drilldown_plans(
        page,
        mobile,
        content_frame_selector=content_frame_selector,
        first_name_exact=None,
    )
    n_drilldown = len(_video_plans_m)
    note(
        f"Video path: Contact Find drilldown row count N={n_drilldown} "
        "(mobile-only basis for branch A/B)."
    )

    if n_drilldown == 0:
        note(
            "No contact drilldown rows (branch A) — Add Enquiry with base first name "
            "(vehicle + Opportunities + Ctrl+S)."
        )
        ae_ok, ae_detail, ae_enq_no = _add_enquiry_opportunity(
            page,
            dms_values,
            urls,
            action_timeout_ms=action_timeout_ms,
            nav_timeout_ms=nav_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            form_trace=form_trace,
            vehicle_merge=out.setdefault("vehicle", {}),
        )
        if not ae_ok:
            step("Stopped: Add Enquiry branch failed (zero drilldown contacts).")
            out["error"] = (
                "Siebel: video SOP — no contact drilldown rows and Add Enquiry did not complete. "
                f"{ae_detail or 'See the Playwright DMS execution log [NOTE] lines for the failing step.'}"
            )
            return False
        if not (ae_enq_no or "").strip():
            step("Stopped: Add Enquiry did not return Enquiry#.")
            out["error"] = (
                "Siebel: Add Enquiry details were filled but no Enquiry# was scraped. "
                "Treating as failure to avoid silent partial save."
            )
            return False
        if callable(ms_done):
            ms_done("Add enquiry saved")
        note(f"Add Enquiry saved with Enquiry#={ae_enq_no!r}; re-finding by mobile + first name.")
        out.setdefault("vehicle", {})["enquiry_number"] = ae_enq_no
        log_vehicle_snapshot("video_add_enquiry_saved")
        if callable(form_trace):
            form_trace(
                "v1b_refind_after_add_enquiry",
                "Global Find → Contact (mobile + exact first name when present, else mobile-only) + Go",
                "rerun_find_mobile_optional_first_after_add_enquiry",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
                first_name=video_first_name,
            )
        ok_refind = _contact_view_find_by_mobile_strategy_two(
            page,
            contact_url=contact_url,
            mobile=mobile,
            first_name=video_first_name,
            nav_timeout_ms=nav_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            mobile_aria_hints=mobile_aria_hints,
            note=note,
            step=step,
            stage_msg_mobile_only="Post Add Enquiry: re-find by mobile (Contact view; first name blank).",
            stage_msg_mobile_and_first="Post Add Enquiry: re-find customer by mobile + first name (Contact view).",
        )
        if not ok_refind:
            step("Stopped: Add Enquiry saved but post-save re-find failed.")
            out["error"] = (
                "Siebel: Add Enquiry was saved, but the follow-up Find→Contact mobile+first query "
                "did not complete."
            )
            return False
        _video_plans_m = _contact_mobile_drilldown_plans(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=None,
        )
        n_drilldown = len(_video_plans_m)
        note(f"Video path: after Add Enquiry, drilldown row count N={n_drilldown}.")
        if n_drilldown == 0:
            step("Stopped: Add Enquiry saved but Find still shows no drilldown contact rows.")
            out["error"] = (
                "Siebel: Add Enquiry saved but contact search shows no drillable rows after re-find."
            )
            return False
        strict_m = _siebel_ui_suggests_contact_match_mobile_first(
            page, mobile, video_first_name
        )
        note(f"DECISION: contact_table_match_after_add_enquiry_refind={strict_m!r}")
        if not strict_m:
            note(
                "Post Add Enquiry: strict mobile+first not visible on grid — continuing with "
                "drilldown rows only."
            )

    _video_snap_fn = (video_first_name or "").strip()
    _video_plans_fn = (
        _contact_mobile_drilldown_plans(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=_video_snap_fn or None,
        )
        if _video_snap_fn
        else _video_plans_m
    )
    _video_list_snapshot_counts = _find_contact_mobile_first_grid_counts(
        page,
        mobile,
        _video_snap_fn,
        content_frame_selector=content_frame_selector,
        cached_plans=_video_plans_m,
    )
    _video_strict_first = len(_video_plans_fn)
    note(
        "Find-Contact list snapshot (before Title/enquiry sweep): "
        f"{_video_list_snapshot_counts[0]} row(s) with mobile and drilldown "
        f"(same basis as title sweep ordinals); "
        f"{_video_list_snapshot_counts[1]} with enquiry hint in list text; "
        f"optional strict list row match for first name {_video_snap_fn!r}: {_video_strict_first}."
    )

    sweep_has_open, sweep_enq_no, sweep_enq_rows, _sweep_err = _contact_find_title_sweep_for_enquiry(
        page,
        mobile=mobile,
        first_name=video_first_name,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        mobile_aria_hints=mobile_aria_hints,
        note=note,
        step=step,
        cached_plans_ord0=_video_plans_fn,
        cached_plans_dup=_video_plans_m,
    )
    contacts_with_open = (
        1
        if (
            sweep_has_open
            and ((sweep_enq_no or "").strip() or int(sweep_enq_rows or 0) > 0)
        )
        else 0
    )
    note(
        f"Video path: drilldown_rows_N={n_drilldown}, "
        f"contacts_with_open_enquiry={contacts_with_open} (Siebel rule: 0 or 1)."
    )

    if _sweep_err:
        step(f"Stopped: {_sweep_err}")
        out["error"] = _sweep_err
        return False

    if sweep_has_open and (sweep_enq_no or "").strip():
        out.setdefault("vehicle", {})["enquiry_number"] = (sweep_enq_no or "").strip()
        log_vehicle_snapshot("video_enquiry_found_in_contact_enquiry")

    if not sweep_has_open:
        note(
            "Video branch (2): no open enquiry — re-find and drill first contact "
            "before Relation's Name path."
        )
        if not _contact_view_find_by_mobile_strategy_two(
            page,
            contact_url=contact_url,
            mobile=mobile,
            first_name=video_first_name,
            nav_timeout_ms=nav_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            mobile_aria_hints=mobile_aria_hints,
            note=note,
            step=step,
            stage_msg_mobile_only="Branch (2): re-find for first drilldown contact — mobile (first name blank).",
            stage_msg_mobile_and_first="Branch (2): re-find for first drilldown contact — mobile + first name.",
        ):
            step("Stopped: branch (2) re-find failed.")
            out["error"] = "Siebel: video branch (2) could not re-find contact after sweep."
            return False
        fn0 = (video_first_name or "").strip()
        _dr2 = _click_nth_mobile_title_drilldown(
            page,
            mobile,
            0,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            first_name_exact=fn0 if fn0 else None,
        )
        if not _dr2:
            _dr2 = _siebel_try_click_mobile_search_hit_link(
                page,
                mobile,
                timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
        if not _dr2:
            step("Stopped: branch (2) could not drill first contact row.")
            out["error"] = (
                "Siebel: video branch (2) — no open enquiry; could not open first drilldown contact."
            )
            return False
        _safe_page_wait(page, 2000, log_label="after_title_drilldown_branch2")
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

    if callable(form_trace):
        form_trace(
            "v2_drill_and_nav",
            "Search Results + Contacts detail",
            "Siebel_Find_tab_optional_then_link_hit_then_click_first_name_then_fill_Relations_Name_only",
            mobile_phone=mobile,
            first_name=video_first_name,
            care_of=care_of,
        )
    if not _siebel_video_path_after_find_go_to_all_enquiries(
        page,
        mobile=mobile,
        first_name=video_first_name,
        care_of=care_of,
        address_line_1=addr,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        skip_search_hit_click=True,
        customer_profession=(dms_values.get("profession") or "").strip() or None,
    ):
        step("Stopped: video SOP failed while opening customer record or filling Relation's Name.")
        out["error"] = (
            "Siebel: video SOP — after Find/Go, could not fill Relation's Name from care_of. "
            "Confirm right-pane selectors/labels and iframe scope."
        )
        return False

    if not sweep_has_open:
        _b2_home = (
            (dms_values.get("landline") or dms_values.get("alt_phone_num") or "").strip()
            or mobile
        )
        _b2_email = (dms_values.get("branch2_contact_email") or "NA").strip()
        _b2_city = (dms_values.get("city") or dms_values.get("district") or "").strip()
        if not _siebel_video_branch2_address_postal_and_save(
            page,
            pin_code=pin,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            home_phone=_b2_home,
            contact_email=_b2_email,
            city=_b2_city,
        ):
            step("Stopped: video branch (2) Address / Postal Code / Save failed.")
            out["error"] = (
                "Siebel: no open enquiry path — could not fill Address Postal Code or save."
            )
            return False

    _contact_id = ""
    _cid_js = """() => {
            const vis = (el) => {
              if (!el) return false;
              const st = window.getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
              const r = el.getBoundingClientRect();
              return r.width > 2 && r.height > 2;
            };
            const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const sels = [
              "input[aria-label='Contact Id']",
              "[aria-labelledby='s_1_l_HHML_Contact_Seq_Num']",
              "input[aria-label*='Contact Id' i]",
              "input[name*='Contact_Id' i]",
              "input[name*='HHML_Contact' i]",
              "input[id*='Contact_Id' i]",
            ];
            for (const sel of sels) {
              const el = document.querySelector(sel);
              if (!el || !vis(el)) continue;
              const v = (el.value != null ? String(el.value) : (el.textContent || '')).trim();
              if (v && v.length > 2) return v;
            }
            for (const app of document.querySelectorAll('.siebui-applet')) {
              if (!vis(app)) continue;
              const blob = (app.innerText || '').toLowerCase();
              if (!blob.includes('contact')) continue;
              const table = app.querySelector('table');
              if (!table) continue;
              const heads = Array.from(table.querySelectorAll('thead th, thead td, tr th'));
              let idx = -1;
              heads.forEach((h, i) => {
                const ht = norm(h.innerText || '');
                if (idx < 0 && (ht === 'contact id' || ht.includes('contact id'))) idx = i;
              });
              if (idx < 0) continue;
              const rows = Array.from(table.querySelectorAll('tbody tr, tr')).filter(vis);
              for (const tr of rows) {
                if (!vis(tr)) continue;
                const cells = tr.querySelectorAll('td');
                if (idx >= cells.length) continue;
                const cell = cells[idx];
                if (!vis(cell)) continue;
                const a = cell.querySelector('a');
                const raw = ((a && a.textContent) ? a.textContent : (cell.textContent || '')).trim();
                if (raw && raw.length > 5 && (/scon/i.test(raw) || /^\\d+-\\d+-/i.test(raw))) return raw;
              }
            }
            return '';
        }"""
    for _cr in _ordered_frames(page):
        try:
            _cid = _cr.evaluate(_cid_js)
            if _cid:
                _contact_id = str(_cid).strip()
                break
        except Exception:
            continue
    if _contact_id:
        note(f"Scraped Contact ID={_contact_id!r} from contact detail page.")
        out["contact_id"] = _contact_id
    else:
        note("Contact ID not found on contact detail page (best-effort).")

    _write_playwright_contact_scrape_section(
        log_fp,
        out,
        had_open_enquiry_from_sweep=sweep_has_open,
    )

    if callable(form_trace):
        form_trace(
            "v3_add_customer_payment",
            "Payments tab (current frame)",
            "click_Payments_tab_then_click_plus_icon",
        )
    _pay_ok, _pay_fail = _add_customer_payment(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        vehicle_context=(out.get("vehicle") or {}),
    )
    if not _pay_ok:
        _pay_err_map: dict[str, tuple[str, str]] = {
            "no_payment_lines_root": (
                "Stopped: Payment Lines toolbar not found (cannot scope '+' / Save).",
                "Siebel: video SOP — Add customer payment: Payment Lines toolbar not found.",
            ),
            "payment_lines_frame": (
                "Stopped: could not lock Payment Lines edit frame after '+'.",
                "Siebel: video SOP — Add customer payment: Payment Lines edit frame not detected.",
            ),
            "payment_plus": (
                "Stopped: could not click '+' on Payment Lines (List:New).",
                "Siebel: video SOP — could not click Payment Lines '+' for Add customer payment.",
            ),
            "payment_save": (
                "Stopped: could not submit payment (Save icon and Ctrl+S both failed).",
                "Siebel: video SOP — Add customer payment: save not submitted (Save icon and Ctrl+S).",
            ),
            "payment_verify": (
                "Stopped: payment save ran but Transaction# did not appear in Payment Lines (verification).",
                "Siebel: video SOP — Add customer payment: post-save verification failed (no Transaction# in grid).",
            ),
            "payment_exception": (
                "Stopped: Add customer payment raised an exception (see Playwright_DMS notes).",
                "Siebel: video SOP — Add customer payment failed with an exception.",
            ),
        }
        _step_msg, _err_msg = _pay_err_map.get(
            (_pay_fail or "").strip(),
            (
                "Stopped: Add customer payment did not complete (see Playwright_DMS notes).",
                "Siebel: video SOP — Add customer payment did not complete.",
            ),
        )
        step(_step_msg)
        out["error"] = _err_msg
        return False

    try:
        from app.services.fill_hero_dms_service import collate_customer_master_from_dms_siebel_inputs

        out["dms_customer_master_collated"] = collate_customer_master_from_dms_siebel_inputs(
            dms_values,
            contact_id=out.get("contact_id"),
        )
        _cm = out["dms_customer_master_collated"] or {}
        _nf = len((_cm.get("fields") or {}) if isinstance(_cm, dict) else {})
        _nu = len((_cm.get("mapping_unclear") or []) if isinstance(_cm, dict) else {})
        _nn = len((_cm.get("notes") or {}) if isinstance(_cm, dict) else {})
        note(
            f"Customer master collated for operator/DB review: {_nf} field(s), {_nn} sourcing note(s), {_nu} residual note(s)."
        )
    except Exception as exc:
        logger.warning("siebel_dms: customer_master collate failed: %s", exc)
        out["dms_customer_master_collated"] = {
            "fields": {},
            "notes": {},
            "mapping_unclear": [f"collate failed: {exc!s}"],
            "collate_error": str(exc),
        }

    return True
