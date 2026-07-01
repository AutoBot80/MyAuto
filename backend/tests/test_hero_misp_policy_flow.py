"""Unit tests for Hero MISP policy commit flow ordering and production gating."""

from __future__ import annotations

import time
from pathlib import Path
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


def test_post_submit_wait_early_exit_on_cert_page(mock_page):
    mock_page.url = "https://misp.example/Policy/PrintPolicy.aspx?PID=123"
    with patch.object(fhi, "_t") as t_mock:
        with patch.object(mock_page, "wait_for_url") as url_wait:
            fhi._hero_misp_wait_post_submit_print_policy_page(mock_page, timeout_ms=5_000)
    t_mock.assert_called_once()
    url_wait.assert_not_called()


def test_post_submit_wait_waits_for_url_when_not_on_print_policy(mock_page):
    mock_page.url = "https://misp.example/Policy/MispProposalPreview.aspx"
    with patch.object(fhi, "_t"):
        with patch.object(mock_page, "wait_for_url") as url_wait:
            fhi._hero_misp_wait_post_submit_print_policy_page(mock_page, timeout_ms=5_000)
    url_wait.assert_called_once()
    assert url_wait.call_args.kwargs.get("timeout") == min(
        max(2_000, int(fhi.HERO_MISP_POST_SUBMIT_WAIT_MS)), 5_000
    )


def test_final_policy_commit_skips_frame_dump_when_policy_num_scraped(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(fhi, "_hero_misp_click_issue_policy", return_value=None):
            with patch.object(fhi, "_hero_misp_wait_post_submit_print_policy_page"):
                with patch.object(
                    fhi,
                    "_hero_misp_parse_policy_num_from_print_policy_cert_page",
                    return_value="90000031260970224155",
                ):
                    with patch.object(fhi, "_append_hero_misp_frame_dump") as dump:
                        fhi._hero_misp_final_policy_details_commit(
                            mock_page,
                            {"insurer": "HDFC ERGO"},
                            timeout_ms=5_000,
                            customer_id=1,
                            vehicle_id=2,
                        )
    dump.assert_not_called()


def test_final_policy_commit_frame_dump_when_policy_num_missing(mock_page):
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(fhi, "_hero_misp_click_issue_policy", return_value=None):
            with patch.object(fhi, "_hero_misp_wait_post_submit_print_policy_page"):
                with patch.object(
                    fhi,
                    "_hero_misp_parse_policy_num_from_print_policy_cert_page",
                    return_value=None,
                ):
                    with patch.object(fhi, "_append_hero_misp_frame_dump") as dump:
                        fhi._hero_misp_final_policy_details_commit(
                            mock_page,
                            {"insurer": "HDFC ERGO"},
                            timeout_ms=5_000,
                            customer_id=1,
                            vehicle_id=2,
                        )
    dump.assert_called_once()


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


def test_main_process_pdf_failure_sets_success_false(mock_page):
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
                            fhi,
                            "run_hero_insure_reports",
                            return_value={"ok": False, "error": "PDF download failed"},
                        ):
                            out = fhi.main_process(
                                pre_result=pre,
                                customer_id=1,
                                vehicle_id=2,
                                subfolder="sale1",
                            )
    assert out["success"] is False
    assert out["hero_insure_reports"]["ok"] is False
    assert "PDF" in (out.get("error") or "")


def test_main_process_resume_uses_staging_policy_hint(mock_page):
    pre = {
        "success": True,
        "hero_resume_at_print_policy": True,
        "match_base": "https://misp.example",
        "login_url": "https://misp.example/login",
        "_insurance_playwright_page": mock_page,
    }
    staging_payload = {"insurance": {"policy_num": "  POL-999  "}}
    with patch.object(fhi, "build_insurance_fill_values", return_value={"insurer": "HDFC ERGO", "frame_no": "VIN1"}):
        with patch.object(fhi, "_hero_misp_fill_proposal_and_review") as prop:
            with patch.object(fhi, "_main_process_run_print_policy_reports", return_value={"ok": True}) as reports:
                with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
                    out = fhi.main_process(
                        pre_result=pre,
                        customer_id=1,
                        vehicle_id=2,
                        subfolder="sale1",
                        staging_payload=staging_payload,
                    )
    assert out["success"] is True
    prop.assert_not_called()
    reports.assert_called_once()
    assert reports.call_args.kwargs.get("policy_num_hint") == "POL-999"


def test_staging_policy_num_hint_from_payload() -> None:
    assert fhi._staging_policy_num_hint_from_payload(None) == ""
    assert fhi._staging_policy_num_hint_from_payload({"insurance": {"policy_num": " ABC123 "}}) == "ABC123"


def test_main_process_print_reports_outcome_production() -> None:
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        ok, err = fhi._main_process_print_reports_outcome({"ok": True})
        assert ok is True
        assert err is None
        ok, err = fhi._main_process_print_reports_outcome({"ok": False, "error": "boom"})
        assert ok is False
        assert err == "boom"
        ok, err = fhi._main_process_print_reports_outcome({})
        assert ok is False
        assert err == "Print Policy / PDF download failed"


def test_main_process_print_reports_outcome_dev_empty_hrep_succeeds() -> None:
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", False):
        ok, err = fhi._main_process_print_reports_outcome({})
        assert ok is True
        assert err is None


def test_main_process_print_reports_outcome_dev_failed_hrep_fails() -> None:
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", False):
        ok, err = fhi._main_process_print_reports_outcome({"ok": False, "error": "boom"})
        assert ok is False
        assert err == "boom"


def test_main_process_resume_runs_print_in_dev(mock_page):
    pre = {
        "success": True,
        "hero_resume_at_print_policy": True,
        "match_base": "https://misp.example",
        "login_url": "https://misp.example/login",
        "_insurance_playwright_page": mock_page,
    }
    with patch.object(fhi, "build_insurance_fill_values", return_value={"insurer": "HDFC ERGO", "frame_no": "VIN1"}):
        with patch.object(fhi, "_hero_misp_fill_proposal_and_review") as prop:
            with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", False):
                with patch.object(fhi, "run_hero_insure_reports", return_value={"ok": True}) as print_rep:
                    out = fhi.main_process(
                        pre_result=pre,
                        customer_id=1,
                        vehicle_id=2,
                        subfolder="sale1",
                    )
    assert out["success"] is True
    prop.assert_not_called()
    print_rep.assert_called_once()
    assert out["hero_insure_reports"]["ok"] is True


def test_split_proposer_name_tokens() -> None:
    assert fhi._split_proposer_name_tokens("") == ("", "", "")
    assert fhi._split_proposer_name_tokens("Madonna") == ("Madonna", "", "Madonna")
    assert fhi._split_proposer_name_tokens("ANOOP SINGH") == ("ANOOP", "", "SINGH")
    assert fhi._split_proposer_name_tokens("ANOOP KUMAR SINGH") == (
        "ANOOP",
        "KUMAR",
        "SINGH",
    )


def test_insurance_kyc_proposer_name_matches_anchor() -> None:
    assert fhi._insurance_kyc_proposer_name_matches(
        "ANOOP KUMAR SINGH",
        "ANOOP",
        "",
        "SINGH",
    )
    assert fhi._insurance_kyc_proposer_name_matches(
        "ANOOP KUMAR SINGH",
        "ANOOP",
        "KUMAR",
        "SINGH",
    )
    assert not fhi._insurance_kyc_proposer_name_matches(
        "ANOOP KUMAR SINGH",
        "ANIL",
        "",
        "SINGH",
    )
    assert fhi._insurance_kyc_proposer_name_matches("Madonna", "Madonna", "", "")
    assert not fhi._insurance_kyc_proposer_name_matches(
        "ANOOP SINGH",
        "ANOOP",
        "",
        "KUMAR",
    )


def test_hero_misp_verify_kyc_proposer_name_mismatch(mock_page) -> None:
    with patch.object(
        fhi,
        "_proposal_read_proposer_name_triplet",
        return_value=("ANIL", "", "SINGH", True, True),
    ):
        with patch.object(fhi, "_append_hero_misp_frame_dump") as dump:
            with patch.object(fhi, "append_playwright_insurance_line") as log:
                err = fhi._hero_misp_verify_kyc_proposer_name(
                    mock_page,
                    {"customer_name": "ANOOP SINGH"},
                    ocr_output_dir=Path("/tmp/ocr"),
                    subfolder="sale1",
                )
    assert err == fhi.KYC_PROPOSER_NAME_MISMATCH_ERR
    dump.assert_called_once()
    assert dump.call_args.kwargs.get("reason") == "kyc_proposer_name_mismatch"
    error_lines = [str(c[0][3]) for c in log.call_args_list if c[0][2] == "ERROR"]
    assert any(fhi.KYC_PROPOSER_NAME_MISMATCH_ERR in line for line in error_lines)


def test_hero_misp_verify_kyc_proposer_name_fields_not_found(mock_page) -> None:
    with patch.object(
        fhi,
        "_proposal_read_proposer_name_triplet",
        return_value=("", "", "", False, False),
    ):
        with patch.object(fhi, "_append_hero_misp_frame_dump") as dump:
            with patch.object(fhi, "append_playwright_insurance_line") as log:
                err = fhi._hero_misp_verify_kyc_proposer_name(
                    mock_page,
                    {"customer_name": "ANOOP SINGH"},
                    ocr_output_dir=Path("/tmp/ocr"),
                    subfolder="sale1",
                )
    assert err == fhi.KYC_PROPOSER_NAME_FIELDS_NOT_FOUND_ERR
    dump.assert_called_once()
    assert dump.call_args.kwargs.get("reason") == "kyc_proposer_name_fields_not_found"
    error_lines = [str(c[0][3]) for c in log.call_args_list if c[0][2] == "ERROR"]
    assert any(fhi.KYC_PROPOSER_NAME_FIELDS_NOT_FOUND_ERR in line for line in error_lines)


def test_hero_misp_verify_kyc_proposer_name_match_ok(mock_page) -> None:
    with patch.object(
        fhi,
        "_proposal_read_proposer_name_triplet",
        return_value=("ANOOP", "", "SINGH", True, True),
    ):
        with patch.object(fhi, "_append_hero_misp_frame_dump") as dump:
            err = fhi._hero_misp_verify_kyc_proposer_name(
                mock_page,
                {"customer_name": "ANOOP SINGH"},
            )
    assert err is None
    dump.assert_not_called()


def test_fill_proposal_stops_on_kyc_name_mismatch(mock_page) -> None:
    with patch.object(fhi, "_wait_load_optional"):
        with patch.object(fhi, "_t"):
            with patch.object(mock_page, "wait_for_timeout"):
                with patch.object(fhi, "_hero_misp_dismiss_proposal_overlay_modals"):
                    with patch.object(
                        fhi,
                        "_hero_misp_verify_kyc_proposer_name",
                        return_value=fhi.KYC_PROPOSER_NAME_MISMATCH_ERR,
                    ):
                        with patch.object(fhi, "_proposal_step_select_fuzzy") as sel:
                            err, preview = fhi._hero_misp_fill_proposal_and_review(
                                mock_page,
                                {"customer_name": "ANOOP SINGH"},
                                timeout_ms=5_000,
                            )
    assert err == fhi.KYC_PROPOSER_NAME_MISMATCH_ERR
    assert preview == {}
    sel.assert_not_called()


def test_misp_url_is_login_redirection() -> None:
    assert fhi._misp_url_is_login_redirection(
        "https://misp.heroinsurance.com/PROD/apps/v1/2w/Login_Redirection.html?username=x"
    )
    assert not fhi._misp_url_is_login_redirection(
        "https://misp.heroinsurance.com/prod/apps/V1/2W/welcome/Default.aspx"
    )


def test_misp_url_is_2w_app() -> None:
    assert fhi._misp_url_is_2w_app(
        "https://misp.heroinsurance.com/prod/apps/V1/2W/welcome/Default.aspx"
    )
    assert not fhi._misp_url_is_2w_app(
        "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    )
    assert not fhi._misp_url_is_2w_app(
        "https://misp.heroinsurance.com/PROD/apps/v1/2w/Login_Redirection.html?username=x"
    )
    assert not fhi._misp_url_is_2w_app(
        "https://misp.heroinsurance.com/prod/apps/v1/2w/ekycpage.aspx"
    )


def test_misp_page_is_2w_app_landed_false_on_mainindex(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    nav_loc = MagicMock()
    nav_loc.count.return_value = 0
    mock_page.locator.return_value.first = nav_loc
    assert fhi._misp_page_is_2w_app_landed(mock_page) is False


def test_click_2w_icon_does_not_skip_on_mainindex(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    nav_loc = MagicMock()
    nav_loc.count.return_value = 0
    mock_page.locator.return_value.first = nav_loc
    mock_page.locator.return_value.count.return_value = 0
    mock_page.frames = []
    with patch.object(fhi, "_insurance_click_settle"):
        with patch.object(fhi, "_wait_misp_post_login_landing", return_value=mock_page):
            with patch.object(fhi, "_wait_misp_2w_hub_ready", return_value=True) as hub_wait:
                with patch.object(fhi, "append_playwright_insurance_line") as log:
                    with patch.object(fhi, "_append_hero_misp_frame_dump"):
                        with pytest.raises(TimeoutError):
                            fhi._click_2w_icon(mock_page, timeout_ms=100)
    hub_wait.assert_called()
    notes = [str(c[0][3]) for c in log.call_args_list if c[0][2] == "NOTE"]
    assert not any("skip hub click" in n.lower() for n in notes)


def test_click_2w_icon_skips_when_already_on_2w_welcome(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/V1/2W/welcome/Default.aspx"
    with patch.object(fhi, "_insurance_click_settle"):
        with patch.object(fhi, "_wait_misp_post_login_landing", return_value=mock_page):
            with patch.object(fhi, "_wait_misp_2w_hub_ready") as hub_wait:
                with patch.object(fhi, "append_playwright_insurance_line") as log:
                    fhi._click_2w_icon(mock_page, timeout_ms=3_500, subfolder="sale1")
    hub_wait.assert_not_called()
    notes = [str(c[0][3]) for c in log.call_args_list if c[0][2] == "NOTE"]
    assert any("skip hub click" in n.lower() for n in notes)


def test_misp_post_login_loading_visible(mock_page) -> None:
    mock_page.evaluate.return_value = True
    assert fhi._misp_loading_interstitial_visible(mock_page) is True
    assert fhi._misp_post_login_loading_visible(mock_page) is True


def test_misp_policy_nav_shell_ready_on_welcome(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/V1/2W/welcome/Default.aspx"
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_navbar_vertical_nav_visible", return_value=True):
            assert fhi._misp_policy_nav_shell_ready(mock_page) is True


def test_misp_policy_nav_shell_ready_false_on_mainindex_with_policy_text(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    pol_loc = MagicMock()
    pol_loc.count.return_value = 1
    mock_page.get_by_text.return_value.first = pol_loc
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_navbar_vertical_nav_visible", return_value=True):
            assert fhi._post_2w_in_app_shell_ready(mock_page) is False


def test_misp_nav_milestone_hub_ready_on_mainindex(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_misp_2w_hub_present_on_page", return_value=False):
            assert fhi._misp_nav_milestone_ready(mock_page, "hub_ready") is True
            assert fhi._misp_nav_milestone_ready(mock_page, "in_app_shell") is False


def test_misp_nav_milestone_hub_ready_false_while_loading(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=True):
        assert fhi._misp_nav_milestone_ready(mock_page, "hub_ready") is False


def test_wait_misp_post_login_landing_ready_on_mainindex_without_hub_js(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    mock_page.context.pages = [mock_page]
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_misp_2w_hub_present_on_page", return_value=False):
            with patch.object(fhi, "append_playwright_insurance_line"):
                t0 = time.monotonic()
                out = fhi._wait_misp_post_login_landing(mock_page, timeout_ms=2_000)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
    assert out is mock_page
    assert elapsed_ms < 800


def test_leave_login_redirection_waits_while_loading_on_redirection_url(mock_page) -> None:
    poll = {"i": 0}
    login_url = (
        "https://misp.heroinsurance.com/PROD/apps/v1/2w/Login_Redirection.html?username=x"
    )
    hub_url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"

    def _url_getter(_self):
        return login_url if poll["i"] < 3 else hub_url

    type(mock_page).url = property(_url_getter)
    mock_page.context.pages = [mock_page]

    def _loading(_page):
        poll["i"] += 1
        return poll["i"] < 3

    def _milestone(_page, milestone, **kwargs):
        if milestone == "hub_ready":
            return poll["i"] >= 3
        return False

    with patch.object(fhi, "_misp_loading_interstitial_visible", side_effect=_loading):
        with patch.object(fhi, "_misp_nav_milestone_ready", side_effect=_milestone):
            with patch.object(fhi, "append_playwright_insurance_line"):
                with patch.object(mock_page, "wait_for_timeout"):
                    out = fhi._wait_misp_leave_login_redirection(mock_page, timeout_ms=2_000)
    assert out is mock_page
    assert poll["i"] >= 3


def test_wait_misp_after_2w_nav_landing_returns_when_nav_ready(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/V1/2W/welcome/Default.aspx"
    mock_page.context.pages = [mock_page]
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_post_2w_in_app_shell_ready", return_value=True):
            with patch.object(fhi, "append_playwright_insurance_line"):
                out = fhi._wait_misp_after_2w_nav_landing(mock_page, timeout_ms=500)
    assert out is mock_page


def test_wait_misp_after_2w_nav_landing_fails_on_mainindex(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/v1/2w/MainIndex.aspx"
    mock_page.context.pages = [mock_page]
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_post_2w_in_app_shell_ready", return_value=False):
            with patch.object(fhi, "append_playwright_insurance_line"):
                with patch.object(mock_page, "wait_for_timeout"):
                    with pytest.raises(TimeoutError, match="still on MainIndex"):
                        fhi._wait_misp_after_2w_nav_landing(mock_page, timeout_ms=300)


def test_wait_misp_post_login_landing_returns_on_2w_shell(mock_page) -> None:
    mock_page.url = "https://misp.heroinsurance.com/prod/apps/V1/2W/welcome/Default.aspx"
    mock_page.context.pages = [mock_page]
    with patch.object(fhi, "_misp_loading_interstitial_visible", return_value=False):
        with patch.object(fhi, "_misp_page_is_2w_app_landed", return_value=True):
            with patch.object(fhi, "append_playwright_insurance_line"):
                out = fhi._wait_misp_post_login_landing(mock_page, timeout_ms=500)
    assert out is mock_page

