"""resolve_sales_id_from_staging: sales_id as JSON int or string."""

from unittest.mock import patch

from app.repositories.add_sales_staging import (
    resolve_sales_id_from_staging,
    sales_id_from_staging_payload,
)


def test_sales_id_from_staging_payload_int() -> None:
    assert sales_id_from_staging_payload({"sales_id": 9042}) == 9042


def test_sales_id_from_staging_payload_str() -> None:
    assert sales_id_from_staging_payload({"sales_id": "9042"}) == 9042


def test_sales_id_from_staging_payload_missing() -> None:
    assert sales_id_from_staging_payload({}) is None
    assert sales_id_from_staging_payload({"sales_id": 0}) is None


def test_resolve_sales_id_from_staging_accepts_int_in_payload() -> None:
    with patch(
        "app.repositories.add_sales_staging.fetch_staging_payload",
        return_value={"sales_id": 777, "customer_id": 1, "vehicle_id": 2},
    ):
        assert resolve_sales_id_from_staging("00000000-0000-0000-0000-000000000001", 100001) == 777
