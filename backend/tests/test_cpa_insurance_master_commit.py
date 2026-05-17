"""CPA insurance_master row: insurance_type=CPA, premium 348, separate from Main."""

from app.services.add_sales_commit_service import (
    ALLIANCE_CPA_PLAN_PREMIUM_DEFAULT,
    INSURANCE_TYPE_CPA,
    INSURANCE_TYPE_MAIN,
    compute_cpa_insurance_master_insert_snapshot,
)


def test_cpa_snapshot_uses_cpa_type_and_default_premium() -> None:
    snap = compute_cpa_insurance_master_insert_snapshot(
        1,
        2,
        certificate_number="CPA-CERT-123",
        staging_payload={
            "insurance": {
                "nominee_name": "Nom",
                "nominee_age": 30,
                "nominee_relationship": "Spouse",
                "nominee_gender": "Female",
                "insurer": "Alliance",
            }
        },
        cpa_insurer="Alliance CPA",
    )
    ir = snap["insert_row"]
    assert ir["insurance_type"] == INSURANCE_TYPE_CPA
    assert ir["policy_num"] == "CPA-CERT-123"
    assert ir["premium"] == ALLIANCE_CPA_PLAN_PREMIUM_DEFAULT
    assert ir["nominee_name"] == "Nom"
    assert ir["insurer"] == "Alliance CPA"


def test_main_snapshot_still_uses_main_type() -> None:
    from app.services.add_sales_commit_service import compute_insurance_master_insert_snapshot

    snap = compute_insurance_master_insert_snapshot(1, 2, fill_values={"insurer": "HDFC"})
    assert snap["insert_row"]["insurance_type"] == INSURANCE_TYPE_MAIN
