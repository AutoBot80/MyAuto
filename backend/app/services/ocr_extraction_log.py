"""
Append-only extraction diary: ``pre`` → ``pre_ocr_log.txt``; ``ocr``/``post`` → ``{stem}_ocr_log.txt`` under ``ocr_output/{dealer}/{stem}_{ddmmyy}/``.

Phases: **pre** (Tesseract pre-OCR / manual prep), **ocr** (Textract + merge), **post** (compress/move).

Legacy ``ocr_extraction_log.txt`` is used only when the subfolder leaf does not match ``{stem}_{ddmmyy}``.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

OCR_EXTRACTION_LOG_FILENAME = "ocr_extraction_log.txt"


def append_ocr_extraction_log(
    ocr_output_dir: Path | None,
    subfolder: str,
    phase: str,
    message: str,
) -> None:
    """
    Append one line to ``pre_ocr_log.txt`` (phase **pre**) or ``{stem}_ocr_log.txt`` (other phases).

    ``phase`` is typically ``pre``, ``ocr``, or ``post`` (informational only).
    Silently no-ops if ``ocr_output_dir`` is missing or the path cannot be written.
    """
    from app.services.ocr_sale_artifacts import append_sale_log_line

    append_sale_log_line(ocr_output_dir, subfolder, phase, message)


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
