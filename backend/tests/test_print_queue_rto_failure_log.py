"""Print / Queue RTO failure log dedupe keys."""

from app.services.process_failure_log_service import (
    PROCESS_LABEL_PRINT_QUEUE_RTO,
    entity_key_print_queue_rto,
    mobile_digits_for_print_rto_subfolder,
)


def test_mobile_from_subfolder() -> None:
    assert mobile_digits_for_print_rto_subfolder("8278671032_160526", None) == "8278671032"


def test_entity_key_print_queue_rto() -> None:
    ek = entity_key_print_queue_rto(subfolder="8278671032_160526", mobile_digits="8278671032")
    assert ek == "pqtrto:8278671032_160526:8278671032"
    assert PROCESS_LABEL_PRINT_QUEUE_RTO == "Print / Queue RTO"
