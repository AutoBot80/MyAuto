from pathlib import Path

from app.services.print_rto_queue_log import (
    LOG_FILENAME,
    append_print_rto_queue_line,
    print_rto_queue_log_path,
    reset_print_rto_queue_log,
)


def test_print_rto_queue_log_reset_and_append(tmp_path: Path) -> None:
    ocr = tmp_path / "ocr_output" / "100001"
    sub = "9876543210_160526"
    p = reset_print_rto_queue_log(ocr, sub)
    assert p is not None
    assert p.name == LOG_FILENAME
    assert p.parent.name == sub
    append_print_rto_queue_line(ocr, sub, "SYNC", "pulled 2 file(s)")
    text = p.read_text(encoding="utf-8")
    assert "Print / Queue RTO" in text
    assert "[SYNC] pulled 2 file(s)" in text
    assert print_rto_queue_log_path(ocr, sub) == p
