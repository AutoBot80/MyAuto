"""Tests for add_sales_staging deep-merge (commit patch must not wipe nested OCR fields)."""

from app.repositories.add_sales_staging import deep_merge_staging_payload
from app.services.add_sales_commit_service import build_staging_commit_patch


def test_deep_merge_preserves_customer_vehicle_on_commit_patch():
    base = {
        "dealer_id": 100001,
        "file_location": "8278671032_160526",
        "customer": {
            "name": "Govind Singh",
            "address": "Jatoli Thoon, Bharatpur",
            "care_of": "S/O Raman Singh",
            "mobile_number": 8278671032,
            "aadhar_id": "9129",
        },
        "vehicle": {
            "frame_no": "MBLHAW478THD09377",
            "engine_no": "HA11F6THD09898",
            "key_no": "1404",
            "battery_no": "M7CSQ67387",
        },
        "insurance": {"nominee_name": "Nishant", "insurer": "The New India Assurance Co. Ltd"},
    }
    patch = build_staging_commit_patch(
        {
            **base,
            "customer": {**base["customer"], "financier": "Shriram Finance Ltd."},
            "vehicle": {
                **base["vehicle"],
                "order_number": "11870-02-SVSO-0526-494",
                "invoice_number": "11870BE26S412",
                "enquiry_number": "11870-02-SENQ-0526-310",
            },
        },
        customer_id=4,
        vehicle_id=4,
        sales_id=4,
    )
    merged = deep_merge_staging_payload(base, patch)
    assert merged["customer_id"] == 4
    assert merged["customer"]["name"] == "Govind Singh"
    assert merged["customer"]["care_of"] == "S/O Raman Singh"
    assert merged["customer"]["financier"] == "Shriram Finance Ltd."
    assert merged["vehicle"]["frame_no"] == "MBLHAW478THD09377"
    assert merged["vehicle"]["invoice_number"] == "11870BE26S412"
    assert merged["insurance"]["nominee_name"] == "Nishant"


def test_shallow_jsonb_or_would_drop_nested_fields():
    """Document regression: top-level || replaces whole customer/vehicle objects."""
    base = {"customer": {"name": "Govind Singh", "care_of": "S/O Raman Singh"}}
    patch = {"customer_id": 4, "customer": {"financier": "Shriram Finance Ltd."}}
    broken = {**base, **patch}
    assert "name" not in broken["customer"]
    full = deep_merge_staging_payload(base, patch)
    assert full["customer"]["name"] == "Govind Singh"
    assert full["customer"]["financier"] == "Shriram Finance Ltd."
