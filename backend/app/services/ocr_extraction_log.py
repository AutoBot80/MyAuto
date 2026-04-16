"""
Append-only extraction diary: all phases append to ``{stem}_ocr_log.txt`` under ``ocr_output/{dealer}/{stem}_{ddmmyy}/``
(line prefix ``[pre]``, ``[ocr]``, or ``[post]``).

Phases: **pre** (Tesseract pre-OCR / manual prep), **ocr** (Textract + merge), **post** (compress/move).

Legacy ``ocr_extraction_log.txt`` is used only when the subfolder leaf does not match ``{stem}_{ddmmyy}``.

Step tuples: ``(step_id, elapsed_ms | None, detail)`` or ``(step_id, elapsed_ms | None, detail, t_offset_ms)``.
When the 4th element ``t_offset_ms`` is present, each line includes ``T+{t_offset_ms}ms`` showing wall-clock
offset from the request start — makes the timeline easy to read even though lines are batch-flushed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

OCR_EXTRACTION_LOG_FILENAME = "ocr_extraction_log.txt"

PreOcrStep = Union[
    tuple[str, "int | None", str],
    tuple[str, "int | None", str, int],
]


def append_ocr_extraction_log(
    ocr_output_dir: Path | None,
    subfolder: str,
    phase: str,
    message: str,
) -> None:
    """
    Append one line to ``{stem}_ocr_log.txt`` (all phases; ``phase`` is **pre**, **ocr**, or **post** in the line).

    ``phase`` is typically ``pre``, ``ocr``, or ``post`` (informational only).
    Silently no-ops if ``ocr_output_dir`` is missing or the path cannot be written.
    """
    from app.services.ocr_sale_artifacts import append_sale_log_line

    append_sale_log_line(ocr_output_dir, subfolder, phase, message)


def append_pre_ocr_step_lines(
    ocr_output_dir: Path | None,
    subfolder: str,
    steps: list[PreOcrStep],
) -> None:
    """
    Append multiple **pre** lines — typically one Tesseract/classify sub-step per line.

    Each item is ``(step_id, elapsed_ms | None, detail)`` or ``(step_id, elapsed_ms | None, detail, t_offset_ms)``.
    ``t_offset_ms`` prints as ``T+1234ms`` (wall-clock offset from request start).
    """
    if not ocr_output_dir or not str(subfolder or "").strip() or not steps:
        return
    for row in steps:
        step_id = row[0]
        ms = row[1]
        detail = row[2]
        t_off = row[3] if len(row) >= 4 else None  # type: ignore[misc]
        parts: list[str] = []
        if t_off is not None:
            parts.append(f"T+{t_off}ms")
        parts.append(f"step={step_id}")
        if ms is not None:
            parts.append(f"elapsed_ms={ms}")
        d = (detail or "").strip()
        if d:
            parts.append(d)
        append_ocr_extraction_log(ocr_output_dir, subfolder, "pre", " ".join(parts))
