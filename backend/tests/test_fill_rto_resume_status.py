"""RTO resume skip mapping from ``rto_queue.rto_status``."""

import re

from app.services.fill_rto_service import (
    RTO_STATUS_AFTER_SCREEN2,
    RTO_STATUS_AFTER_SCREEN3D,
    RTO_STATUS_AFTER_SCREEN5,
    _DEALER_DOC_UPLOAD_ACTION_RE,
    _ENTRY_ACTION_RE,
    _SCREEN5_POST_UPLOAD_SETTLE_MS,
    _VERIFY_ACTION_RE,
    _screen_5_subcategory_is_disabled,
    _resolve_skip_from_rto_status,
    _resume_row_action_priority,
    _screen3_resume_at_3b,
    _screen_4_documents_upload_url,
    _screen_4_office_remarks_needs_none,
    _screen4_skip_verify_on_resume,
    _screen_5_doc_key_for_portal_title,
)


def test_screen5_post_upload_settle_ms() -> None:
    assert _SCREEN5_POST_UPLOAD_SETTLE_MS == 300


def test_screen5_subcategory_disabled_detects_portal_lock() -> None:
    assert _screen_5_subcategory_is_disabled(
        "ui-selectonemenu ui-widget ui-state-default ui-corner-all bottom-space ui-state-disabled"
    )
    assert not _screen_5_subcategory_is_disabled(
        "ui-selectonemenu ui-widget ui-state-default ui-corner-all bottom-space"
    )


def test_resolve_skip_from_rto_status_fresh() -> None:
    assert _resolve_skip_from_rto_status(None) == 0
    assert _resolve_skip_from_rto_status(0) == 0


def test_resolve_skip_from_rto_status_checkpoints() -> None:
    assert _resolve_skip_from_rto_status(RTO_STATUS_AFTER_SCREEN2) == 3
    assert _resolve_skip_from_rto_status(RTO_STATUS_AFTER_SCREEN3D) == 4
    assert _resolve_skip_from_rto_status(RTO_STATUS_AFTER_SCREEN5) == 6


def test_resolve_skip_from_rto_status_unknown() -> None:
    assert _resolve_skip_from_rto_status(99) == 0


def test_screen3_resume_at_3b() -> None:
    assert _screen3_resume_at_3b(use_resume_nav=True, rto_status=RTO_STATUS_AFTER_SCREEN2)
    assert not _screen3_resume_at_3b(use_resume_nav=False, rto_status=RTO_STATUS_AFTER_SCREEN2)
    assert not _screen3_resume_at_3b(use_resume_nav=True, rto_status=RTO_STATUS_AFTER_SCREEN3D)


def test_resume_row_action_priority_status_2_prefers_verify() -> None:
    priority = _resume_row_action_priority(RTO_STATUS_AFTER_SCREEN3D)
    assert priority[0] is _VERIFY_ACTION_RE
    assert priority[1] is _DEALER_DOC_UPLOAD_ACTION_RE
    assert _VERIFY_ACTION_RE.match("Verify")
    assert _DEALER_DOC_UPLOAD_ACTION_RE.match("Dealer-Document-Upload")
    assert _DEALER_DOC_UPLOAD_ACTION_RE.match("Dealer Document Upload")
    assert not priority[0].match("Entry")


def test_resume_row_action_priority_status_1_prefers_entry() -> None:
    priority = _resume_row_action_priority(RTO_STATUS_AFTER_SCREEN2)
    assert priority[0] is _ENTRY_ACTION_RE
    assert _ENTRY_ACTION_RE.match("Entry")


def test_screen4_skip_verify_on_resume_status_2() -> None:
    assert _screen4_skip_verify_on_resume(
        use_resume_nav=True, rto_status=RTO_STATUS_AFTER_SCREEN3D
    )
    assert not _screen4_skip_verify_on_resume(
        use_resume_nav=True, rto_status=RTO_STATUS_AFTER_SCREEN2
    )
    assert not _screen4_skip_verify_on_resume(
        use_resume_nav=False, rto_status=RTO_STATUS_AFTER_SCREEN3D
    )


def test_office_remarks_needs_none_for_placeholder() -> None:
    assert _screen_4_office_remarks_needs_none("")
    assert _screen_4_office_remarks_needs_none("OFFICE REMARK ?")
    assert _screen_4_office_remarks_needs_none("None")
    assert _screen_4_office_remarks_needs_none("NONE")
    assert not _screen_4_office_remarks_needs_none("Proceed to next seat")


def test_save_label_regex_does_not_match_save_options() -> None:
    save_re = re.compile(r"^\s*Save\s*$", re.I)
    assert save_re.match("Save")
    assert not save_re.match("Save-Options")
    assert not save_re.match("Save Options")


def test_documents_upload_url_detection() -> None:
    assert _screen_4_documents_upload_url(
        "https://vahan.parivahan.gov.in/vahan/vahan/ui/form_documents_upload.xhtml"
    )
    assert not _screen_4_documents_upload_url(
        "https://vahan.parivahan.gov.in/vahan/vahan/ui/workbench.xhtml"
    )


def test_screen5_doc_key_for_portal_title() -> None:
    uploaded: set[str] = set()
    assert _screen_5_doc_key_for_portal_title("INSURANCE CERTIFICATE", uploaded=uploaded) == (
        "INSURANCE CERTIFICATE"
    )
    assert _screen_5_doc_key_for_portal_title("FORM 20", uploaded=uploaded) == "FORM 20"
    assert _screen_5_doc_key_for_portal_title("Owner Undertaking Form", uploaded=uploaded) == (
        "OWNER UNDERTAKING FORM"
    )
    assert _screen_5_doc_key_for_portal_title("AFFADEVIT FOR PARKING", uploaded=uploaded) is None
    assert _screen_5_doc_key_for_portal_title("AADHAAR CARD", uploaded=uploaded) == "AADHAAR_FRONT"
    uploaded.add("AADHAAR_FRONT")
    assert _screen_5_doc_key_for_portal_title("AADHAAR CARD", uploaded=uploaded) == "AADHAAR_BACK"
    uploaded.add("AADHAAR_BACK")
    assert _screen_5_doc_key_for_portal_title("AADHAAR CARD", uploaded=uploaded) is None
    assert _screen_5_doc_key_for_portal_title("Proof of address", uploaded=set()) == "AADHAAR_BACK"
    assert _screen_5_doc_key_for_portal_title(
        "Proof of Address (Aadhaar)", uploaded={"AADHAAR_FRONT"}
    ) == "AADHAAR_BACK"


def test_screen5_doc_key_unknown_title() -> None:
    assert _screen_5_doc_key_for_portal_title("SOME NEW PORTAL SLOT", uploaded=set()) is None
    assert _screen_5_doc_key_for_portal_title("", uploaded=set()) is None
