"""Per-sale trace for **Print / Queue RTO** under ``ocr_output/{dealer_id}/{subfolder}/Print_RTO_queue.txt``."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.utility_functions import safe_subfolder_name

logger = logging.getLogger(__name__)

_IST_TZ = ZoneInfo("Asia/Kolkata")
LOG_FILENAME = "Print_RTO_queue.txt"


def _ts_ist() -> str:
    return datetime.now(_IST_TZ).isoformat(timespec="milliseconds")


def print_rto_queue_log_path(ocr_output_dir: Path | None, subfolder: str | None) -> Path | None:
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        return None
    safe = safe_subfolder_name(subfolder)
    return Path(ocr_output_dir).resolve() / safe / LOG_FILENAME


def reset_print_rto_queue_log(ocr_output_dir: Path | None, subfolder: str | None) -> Path | None:
    """Start a fresh log for this Print / Queue RTO run."""
    path = print_rto_queue_log_path(ocr_output_dir, subfolder)
    if path is None:
        return None
    safe = safe_subfolder_name(subfolder or "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "Print / Queue RTO — execution log (IST / Asia/Kolkata timestamps)",
                    "",
                    f"started_ist={_ts_ist()}",
                    f"subfolder={safe!r}",
                    "",
                    "--- trace ---",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path
    except OSError as exc:
        logger.warning("print_rto_queue: could not reset %s: %s", path, exc)
        return None


def append_print_rto_queue_line(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    prefix: str,
    message: str,
) -> None:
    if not (message or "").strip():
        return
    path = print_rto_queue_log_path(ocr_output_dir, subfolder)
    if path is None:
        return
    safe = safe_subfolder_name(subfolder or "")
    tag = (prefix or "INFO").strip() or "INFO"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.is_file()
        with path.open("a", encoding="utf-8") as fp:
            if is_new:
                fp.write("Print / Queue RTO — execution log (IST / Asia/Kolkata timestamps)\n\n")
                fp.write(f"started_ist={_ts_ist()}\n")
                fp.write(f"subfolder={safe!r}\n\n--- trace ---\n")
            fp.write(f"{_ts_ist()} [{tag}] {message.strip()}\n")
    except OSError as exc:
        logger.warning("print_rto_queue: could not append %s: %s", path, exc)


def append_print_rto_queue_block(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    prefix: str,
    lines: list[str],
) -> None:
    for line in lines:
        append_print_rto_queue_line(ocr_output_dir, subfolder, prefix, line)
