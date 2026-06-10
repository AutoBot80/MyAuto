"""Unit tests for Hero MISP policy commit flow ordering and production gating."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import fill_hero_insurance_service as fhi


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.is_closed.return_value = False
    page.url = "https://misp.example/policy"
    return page


def test_click_issue_policy_skipped_non_production(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", False):
        with patch.object(fhi, "append_playwright_insurance_line") as log:
            err = fhi._hero_misp_click_issue_policy(
                mock_page,
                timeout_ms=5_000,
                ocr_output_dir=None,
                subfolder="sale1",
            )
    assert err is None
    notes = [str(c[0][3]) for c in log.call_args_list if c[0][2] == "NOTE"]
    assert any("non-production" in n.lower() for n in notes)


def test_click_issue_policy_fails_when_not_found_in_production(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(fhi, "_hero_misp_page_and_frame_roots", return_value=[mock_page]):
            with patch.object(
                fhi, "_hero_misp_click_proposal_preview_submit_in_root", return_value=None
            ):
                mock_page.locator.return_value.count.return_value = 0
                mock_page.get_by_role.return_value.count.return_value = 0
                mock_page.get_by_text.return_value.first.click.side_effect = Exception("no button")
                err = fhi._hero_misp_click_issue_policy(mock_page, timeout_ms=5_000)
    assert err is not None
    assert "btnSubmit" in err or "Submit" in err


def test_click_proposal_preview_submit_uses_btnSubmit(mock_page):
    submit_loc = MagicMock()
    submit_loc.count.return_value = 1
    submit_loc.first.is_visible.return_value = True
    submit_loc.first.click.return_value = None
    root = MagicMock()
    with patch.object(fhi, "_proposal_cph1_locator", return_value=submit_loc):
        tag = fhi._hero_misp_click_proposal_preview_submit_in_root(root, timeout_ms=5_000)
    assert tag == "cph1_btnSubmit"
    submit_loc.first.click.assert_called_once()


def test_final_policy_commit_skips_insert_non_production(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", False):
        with patch.object(fhi, "append_playwright_insurance_line") as log:
            err, scrape = fhi._hero_misp_final_policy_details_commit(
                mock_page,
                {"insurer": "HDFC ERGO"},
                timeout_ms=5_000,
                customer_id=1,
                vehicle_id=2,
            )
    assert err is None
    assert scrape == {}
    notes = " ".join(str(c[0][3]) for c in log.call_args_list)
    assert "non-production" in notes.lower()


def test_final_policy_commit_post_submit_only_in_production(mock_page):
    policy_hint = "90000031260970224155"
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(fhi, "_hero_misp_click_issue_policy", return_value=None):
            with patch.object(fhi, "mark_staging_insurance_state") as mark_state:
                with patch.object(fhi, "_hero_misp_wait_post_submit_print_policy_page"):
                    with patch.object(
                        fhi,
                        "_hero_misp_parse_policy_num_from_print_policy_cert_page",
                        return_value=policy_hint,
                    ):
                        with patch.object(fhi, "_append_hero_misp_frame_dump"):
                            err, scrape = fhi._hero_misp_final_policy_details_commit(
                                mock_page,
                                {"insurer": "The New India Assurance Co. Ltd."},
                                timeout_ms=5_000,
                                customer_id=10,
                                vehicle_id=20,
                                ocr_output_dir=None,
                                subfolder="mob_chas",
                                staging_id="00000000-0000-0000-0000-000000000099",
                                dealer_id=100001,
                            )
    assert err is None
    assert scrape == {"policy_num": policy_hint}
    mark_state.assert_called_once_with(
        "00000000-0000-0000-0000-000000000099",
        100001,
        2,
    )


def test_final_policy_commit_marks_state_on_cert_page_fallback(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(
            fhi,
            "_hero_misp_click_issue_policy",
            return_value="Submit not found",
        ):
            with patch.object(fhi, "_hero_misp_on_final_policy_cert_page", return_value=True):
                with patch.object(fhi, "mark_staging_insurance_state") as mark_state:
                    with patch.object(fhi, "_hero_misp_wait_post_submit_print_policy_page"):
                        with patch.object(
                            fhi,
                            "_hero_misp_parse_policy_num_from_print_policy_cert_page",
                            return_value=None,
                        ):
                            with patch.object(fhi, "_append_hero_misp_frame_dump"):
                                err, scrape = fhi._hero_misp_final_policy_details_commit(
                                    mock_page,
                                    {"insurer": "HDFC ERGO"},
                                    timeout_ms=5_000,
                                    customer_id=1,
                                    vehicle_id=2,
                                    staging_id="00000000-0000-0000-0000-000000000088",
                                    dealer_id=100001,
                                )
    assert err is None
    assert scrape == {}
    mark_state.assert_called_once_with(
        "00000000-0000-0000-0000-000000000088",
        100001,
        2,
    )


def test_final_policy_commit_skips_state_without_staging_id(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(fhi, "_hero_misp_click_issue_policy", return_value=None):
            with patch.object(fhi, "mark_staging_insurance_state") as mark_state:
                with patch.object(fhi, "_hero_misp_wait_post_submit_print_policy_page"):
                    with patch.object(
                        fhi,
                        "_hero_misp_parse_policy_num_from_print_policy_cert_page",
                        return_value=None,
                    ):
                        with patch.object(fhi, "_append_hero_misp_frame_dump"):
                            fhi._hero_misp_final_policy_details_commit(
                                mock_page,
                                {"insurer": "HDFC ERGO"},
                                timeout_ms=5_000,
                                customer_id=1,
                                vehicle_id=2,
                            )
    mark_state.assert_not_called()


def test_nominee_name_waits_300ms_before_tab_commit(mock_page):
    el = MagicMock()
    el.is_visible.return_value = True
    el.evaluate.return_value = "INPUT"
    loc = MagicMock()
    loc.count.return_value = 1
    loc.first = el
    with patch.object(fhi, "_hero_misp_page_and_frame_roots", return_value=[mock_page]):
        with patch.object(fhi, "_proposal_cph1_locator", return_value=loc):
            with patch.object(fhi, "_proposal_scroll_visible"):
                with patch.object(fhi, "_proposal_fill_nominee_field") as fill_nom:
                    with patch.object(fhi, "_read_locator_value_snapshot", return_value={"value": "Ravi Kumar"}):
                        with patch.object(fhi, "_proposal_read_input_value_best_effort", return_value="Ravi Kumar"):
                            with patch.object(fhi, "_proposal_log"):
                                err = fhi._proposal_step_fill_input(
                                    mock_page,
                                    (r"Nominee\s*Name",),
                                    "Ravi Kumar",
                                    "nominee_name",
                                    None,
                                    None,
                                    timeout_ms=5_000,
                                    cph1_id_suffix="txtNomineeName",
                                )
    assert err is None
    fill_nom.assert_called_once()
    mock_page.wait_for_timeout.assert_any_call(300)
    el.press.assert_called_with("Tab")


def test_proposal_read_nominee_name_txt_returns_first_visible_value(mock_page):
    el = MagicMock()
    el.is_visible.return_value = True
    loc = MagicMock()
    loc.count.return_value = 1
    loc.nth.return_value = el
    with patch.object(fhi, "_hero_misp_page_and_frame_roots", return_value=[mock_page]):
        with patch.object(fhi, "_proposal_cph1_locator", return_value=loc):
            with patch.object(
                fhi, "_proposal_read_input_value_best_effort", return_value="Priya Sharma"
            ):
                got = fhi._proposal_read_nominee_name_txt(mock_page)
    assert got == "Priya Sharma"


def test_proposal_read_nominee_name_txt_empty_when_no_value(mock_page):
    el = MagicMock()
    el.is_visible.return_value = True
    loc = MagicMock()
    loc.count.return_value = 1
    loc.nth.return_value = el
    with patch.object(fhi, "_hero_misp_page_and_frame_roots", return_value=[mock_page]):
        with patch.object(fhi, "_proposal_cph1_locator", return_value=loc):
            with patch.object(fhi, "_proposal_read_input_value_best_effort", return_value=""):
                got = fhi._proposal_read_nominee_name_txt(mock_page)
    assert got is None


def test_main_process_does_not_call_update_after_issue(mock_page):
    pre = {
        "success": True,
        "match_base": "https://misp.example",
        "login_url": "https://misp.example/login",
        "_insurance_playwright_page": mock_page,
    }
    with patch.object(fhi, "build_insurance_fill_values", return_value={"insurer": "HDFC ERGO"}):
        with patch.object(fhi, "_hero_misp_i_agree_after_vin_submit", return_value=None):
            with patch.object(
                fhi,
                "_hero_misp_fill_proposal_and_review",
                return_value=(None, {}),
            ):
                with patch.object(
                    fhi,
                    "_hero_misp_final_policy_details_commit",
                    return_value=(None, {"policy_num": "P1"}),
                ):
                    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
                        with patch.object(
                            fhi, "run_hero_insure_reports", return_value={"ok": True}
                        ) as print_rep:
                            out = fhi.main_process(
                                pre_result=pre,
                                customer_id=1,
                                vehicle_id=2,
                                subfolder="sale1",
                            )
    assert out["success"] is True
    print_rep.assert_called_once()
    assert print_rep.call_args.kwargs.get("commit_insurance_master") is True
    assert not hasattr(fhi, "update_insurance_master_policy_after_issue")


def test_proposal_fail_with_addon_dump_writes_addon_then_frame_dump(mock_page):
    with patch.object(fhi, "_append_proposal_addon_dom_dump") as addon_dump:
        with patch.object(fhi, "_append_hero_misp_frame_dump") as frame_dump:
            with patch.object(fhi, "append_playwright_insurance_line"):
                err, payload = fhi._proposal_fail_with_addon_dump(
                    "/tmp/ocr",
                    "sale1",
                    "addon_rim_safeguard: checkbox not found",
                    page=mock_page,
                )
    assert err == "addon_rim_safeguard: checkbox not found"
    assert payload == {}
    addon_dump.assert_called_once()
    frame_dump.assert_called_once()


def test_proposal_addon_dom_dump_js_targets_checkboxes():
    assert "input[type=\"checkbox\"]" in fhi._PROPOSAL_ADDON_DOM_DUMP_JS
    assert "checkboxes" in fhi._PROPOSAL_ADDON_DOM_DUMP_JS
    assert "rowText" in fhi._PROPOSAL_ADDON_DOM_DUMP_JS


def test_proposal_addon_checkbox_require_cph1_id_skips_label_fallback(mock_page):
    with patch.object(
        fhi,
        "_proposal_step_checkbox_by_cph1_id",
        return_value=fhi.PROPOSAL_CHECKBOX_ID_NOT_FOUND,
    ):
        with patch.object(fhi, "_proposal_step_checkbox") as label_cb:
            err = fhi._proposal_addon_checkbox_id_or_label(
                mock_page,
                "chkRim",
                True,
                "addon_rim_safeguard",
                r"Rim\s*Safeguard",
                None,
                None,
                timeout_ms=5_000,
                require_cph1_id=True,
            )
    assert err is not None
    assert "required CPH1 checkbox id='chkRim'" in err
    label_cb.assert_not_called()


def test_bajaj_tppd_visibility_defaults_to_chkTPPDLim(mock_page):
    with patch.object(fhi, "_hero_misp_page_and_frame_roots", return_value=[mock_page]):
        with patch.object(fhi, "_proposal_cph1_locator") as loc_fn:
            loc_fn.return_value.count.return_value = 1
            with patch.object(
                fhi,
                "_proposal_first_visible_locator_nth",
                return_value=MagicMock(),
            ):
                assert fhi._proposal_bajaj_tppd_checkbox_visible(
                    mock_page, tppd_label_pat=r"^TPPD"
                )
            loc_fn.assert_called_with(mock_page, "chkTPPDLim")


def test_bajaj_mame_dismiss_skips_when_alert_not_shown(mock_page):
    with patch.object(fhi, "_hero_misp_try_click_bajaj_mame_continue_once", return_value=False):
        with patch.object(fhi, "_hero_misp_bajaj_mame_alert_visible", return_value=False):
            with patch.object(fhi, "_t"):
                with patch.object(fhi, "append_playwright_insurance_line") as log:
                    ok = fhi._hero_misp_dismiss_bajaj_mame_cover_exclusion_alert(
                        mock_page,
                        timeout_ms=500,
                        ocr_output_dir="/tmp/ocr",
                        subfolder="sale1",
                    )
    assert ok is True
    notes = [str(c[0][3]) for c in log.call_args_list if c[0][2] == "NOTE"]
    assert any("skip" in n.lower() and "mame" in n.lower() for n in notes)


def test_bajaj_mame_dismiss_polls_until_click(mock_page):
    side_effects = [False, True]
    with patch.object(
        fhi,
        "_hero_misp_try_click_bajaj_mame_continue_once",
        side_effect=side_effects,
    ) as click_try:
        with patch.object(fhi, "_t"):
            ok = fhi._hero_misp_dismiss_bajaj_mame_cover_exclusion_alert(
                mock_page,
                timeout_ms=5_000,
            )
    assert ok is True
    assert click_try.call_count == 2


def test_run_fill_insurance_only_resumes_after_2w_when_insurance_state_2(mock_page):
    with patch.object(fhi, "reset_playwright_insurance_log"):
        with patch.object(fhi, "append_playwright_insurance_line"):
            with patch.object(
                fhi,
                "build_insurance_fill_values",
                return_value={"insurer": "HDFC ERGO"},
            ):
                with patch.object(
                    fhi,
                    "get_or_open_site_page",
                    return_value=(mock_page, None),
                ):
                    with patch.object(fhi, "_insurance_click_settle"):
                        with patch.object(fhi, "_hero_insurance_log_page_diagnostics"):
                            with patch.object(fhi, "_misp_snapshot_context_pages", return_value=[]):
                                with patch.object(fhi, "_click_sign_in_if_visible", return_value=True):
                                    with patch.object(fhi, "_misp_post_sign_in_page", side_effect=lambda p, **kw: p):
                                        with patch.object(fhi, "_hero_misp_after_sign_in_settle"):
                                            with patch.object(fhi, "_insurance_pre_elapsed_note"):
                                                with patch.object(
                                                    fhi,
                                                    "_misp_click_nav_step",
                                                    side_effect=[(mock_page, None)],
                                                ):
                                                    with patch.object(fhi, "_click_new_policy") as new_pol:
                                                        out = fhi.run_fill_insurance_only(
                                                            "https://misp.example/login",
                                                            subfolder="sale1",
                                                            customer_id=1,
                                                            vehicle_id=2,
                                                            insurance_state_hint=2,
                                                        )
    assert out.get("success") is True
    assert out.get("hero_resume_at_print_policy") is True
    new_pol.assert_not_called()


def test_main_process_resume_skips_proposal_and_runs_reports(mock_page):
    pre = {
        "success": True,
        "hero_resume_at_print_policy": True,
        "match_base": "https://misp.example",
        "login_url": "https://misp.example/login",
        "_insurance_playwright_page": mock_page,
    }
    with patch.object(fhi, "build_insurance_fill_values", return_value={"insurer": "HDFC ERGO", "frame_no": "VIN1"}):
        with patch.object(fhi, "_hero_misp_fill_proposal_and_review") as prop:
            with patch.object(fhi, "_main_process_run_print_policy_reports", return_value={"ok": True}) as reports:
                with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
                    out = fhi.main_process(
                        pre_result=pre,
                        customer_id=1,
                        vehicle_id=2,
                        subfolder="sale1",
                    )
    assert out["success"] is True
    prop.assert_not_called()
    reports.assert_called_once()
    assert reports.call_args.kwargs.get("policy_num_hint") == ""

