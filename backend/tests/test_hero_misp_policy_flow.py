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
            with patch.object(fhi, "insert_insurance_master_after_gi") as ins:
                err, scrape = fhi._hero_misp_final_policy_details_commit(
                    mock_page,
                    {"insurer": "HDFC ERGO"},
                    timeout_ms=5_000,
                    customer_id=1,
                    vehicle_id=2,
                )
    assert err is None
    assert scrape == {}
    ins.assert_not_called()
    notes = " ".join(str(c[0][3]) for c in log.call_args_list)
    assert "non-production" in notes.lower()


def test_final_policy_commit_inserts_in_production(mock_page):
    final_scrape = {
        "policy_num": "P123",
        "policy_from": "01/01/2026",
        "policy_to": "31/12/2026",
        "premium": 1200.0,
        "idv": 50000.0,
    }
    with patch.object(fhi, "ENVIRONMENT_IS_PRODUCTION", True):
        with patch.object(fhi, "_hero_misp_click_issue_policy", return_value=None):
            with patch.object(fhi, "_hero_misp_wait_final_policy_details_page"):
                with patch.object(fhi, "_append_hero_misp_frame_dump"):
                    with patch.object(
                        fhi,
                        "scrape_insurance_policy_preview_before_issue",
                        return_value=final_scrape,
                    ):
                        with patch.object(fhi, "insert_insurance_master_after_gi") as ins:
                            err, scrape = fhi._hero_misp_final_policy_details_commit(
                                mock_page,
                                {"insurer": "HDFC ERGO"},
                                timeout_ms=5_000,
                                customer_id=10,
                                vehicle_id=20,
                                ocr_output_dir=None,
                                subfolder="mob_chas",
                            )
    assert err is None
    assert scrape == final_scrape
    ins.assert_called_once()
    assert ins.call_args.kwargs["preview_scrape"] == final_scrape


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
    assert not hasattr(fhi, "update_insurance_master_policy_after_issue")
