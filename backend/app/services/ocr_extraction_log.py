"""
Append-only extraction diary: ``ocr_output/{dealer}/mobile_ddmmyy/ocr_extraction_log.txt``.

Phases: **pre** (Tesseract pre-OCR / manual prep), **ocr** (Textract + merge), **post** (compress/move).
Timestamps use **Asia/Kolkata (IST)**.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _safe_subfolder_name(subfolder: str) -> str:
    """Match ``sales_ocr_service._safe_subfolder_name`` (avoid circular import)."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"

OCR_EXTRACTION_LOG_FILENAME = "ocr_extraction_log.txt"
_IST = ZoneInfo("Asia/Kolkata")


def _ist_line_prefix() -> str:
    ts = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts} IST]"


def append_ocr_extraction_log(
    ocr_output_dir: Path | None,
    subfolder: str,
    phase: str,
    message: str,
) -> None:
    """
    Append one line to ``ocr_extraction_log.txt`` under the sale's OCR subfolder.

    ``phase`` is typically ``pre``, ``ocr``, or ``post`` (informational only).
    Silently no-ops if ``ocr_output_dir`` is missing or the path cannot be written.
    """
    if not ocr_output_dir or not str(subfolder or "").strip():
        return
    try:
        safe = _safe_subfolder_name(subfolder)
        base = Path(ocr_output_dir).resolve() / safe
        base.mkdir(parents=True, exist_ok=True)
        path = base / OCR_EXTRACTION_LOG_FILENAME
        line = f"{_ist_line_prefix()} [{phase}] {message.rstrip()}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.debug("ocr_extraction_log: could not append %s: %s", phase, e)


def append_pre_ocr_step_lines(
    ocr_output_dir: Path | None,
    subfolder: str,
    steps: list[tuple[str, int | None, str]],
) -> None:
    """
    Append multiple **pre** lines — typically one Tesseract/classify sub-step per line.

    Each item is ``(step_id, elapsed_ms | None, detail)``. When ``elapsed_ms`` is ``None``, it is omitted.
    Every line gets its own IST timestamp when written.
    """
    if not ocr_output_dir or not str(subfolder or "").strip() or not steps:
        return
    for step_id, ms, detail in steps:
        parts: list[str] = [f"step={step_id}"]
        if ms is not None:
            parts.append(f"elapsed_ms={ms}")
        d = (detail or "").strip()
        if d:
            parts.append(d)
        append_ocr_extraction_log(ocr_output_dir, subfolder, "pre", " ".join(parts))
