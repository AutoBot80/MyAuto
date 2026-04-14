"""
Consolidated OCR artifacts under ``ocr_output/{dealer}/{stem}_{ddmmyy}/``:

- ``{stem}_scan.pdf`` — copy of the incoming consolidated PDF
- ``{stem}_text.txt`` — merged extraction text (replaces legacy ``Raw_OCR.txt``)
- ``{stem}_log.txt`` — IST-timestamped diary (replaces ``ocr_extraction_log.txt``)
- ``OCR_To_be_Used.json`` — unchanged (written by :mod:`sales_ocr_service`)

Subfolder starts as ``{file_stem}_{ddmmyy}`` and is renamed to ``{mobile}_{ddmmyy}`` when mobile is known.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Legacy filenames (read fallback)
LEGACY_RAW_OCR = "Raw_OCR.txt"
LEGACY_OCR_LOG = "ocr_extraction_log.txt"


def _safe_subfolder_name(subfolder: str) -> str:
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def safe_file_stem(name: str) -> str:
    """Sanitize PDF/upload stem for folder and file prefixes."""
    return re.sub(r"[^\w\-]", "_", (name or "").strip()) or "scan"


def parse_sale_subfolder_leaf(leaf: str) -> tuple[str, str] | None:
    """
    Parse ``{stem}_{ddmmyy}`` leaf. Returns ``(stem, ddmmyy)`` or None if pattern does not match.
    """
    m = re.match(r"^(.+)_(\d{6})$", (leaf or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def stem_from_subfolder_leaf(leaf: str) -> str | None:
    p = parse_sale_subfolder_leaf(leaf)
    return p[0] if p else None


def initial_artifact_leaf(file_stem: str) -> str:
    """First OCR output folder: ``{safe_stem}_{ddmmyy}`` (calendar date, same as uploads)."""
    return f"{safe_file_stem(file_stem)}_{date.today().strftime('%d%m%y')}"


def mobile_file_stem(mobile: str) -> str:
    """10-digit stem used in ``{mobile}_{ddmmyy}`` filenames (matches :func:`get_uploaded_scans_sale_subfolder_leaf`)."""
    dig = re.sub(r"\D", "", str(mobile or ""))
    if len(dig) >= 10:
        return dig[-10:]
    if dig:
        return dig.zfill(10)[:10]
    return "0000000000"


def artifact_paths(ocr_output_dir: Path, subfolder_leaf: str) -> dict[str, Path]:
    """Resolved paths for scan, text, log, json under the sale subfolder."""
    stem = stem_from_subfolder_leaf(subfolder_leaf)
    if not stem:
        stem = "unknown"
    safe = _safe_subfolder_name(subfolder_leaf)
    base = Path(ocr_output_dir).resolve() / safe
    return {
        "base": base,
        "scan": base / f"{stem}_scan.pdf",
        "text": base / f"{stem}_text.txt",
        "log": base / f"{stem}_log.txt",
        "json": base / "OCR_To_be_Used.json",
    }


def _ist_prefix() -> str:
    from datetime import datetime

    ts = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts} IST]"


def append_sale_log_line(
    ocr_output_dir: Path | None,
    subfolder_leaf: str,
    phase: str,
    message: str,
) -> None:
    """Append one IST line to ``{stem}_log.txt``."""
    if not ocr_output_dir or not str(subfolder_leaf or "").strip():
        return
    safe_leaf = _safe_subfolder_name(subfolder_leaf)
    stem = stem_from_subfolder_leaf(safe_leaf)
    if not stem:
        # Fallback: legacy log file
        try:
            base = Path(ocr_output_dir).resolve() / safe_leaf
            base.mkdir(parents=True, exist_ok=True)
            path = base / LEGACY_OCR_LOG
            line = f"{_ist_prefix()} [{phase}] {message.rstrip()}\n"
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.debug("append_sale_log_line fallback: %s", e)
        return
    try:
        paths = artifact_paths(ocr_output_dir, safe_leaf)
        paths["base"].mkdir(parents=True, exist_ok=True)
        log_path = paths["log"]
        line = f"{_ist_prefix()} [{phase}] {message.rstrip()}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.debug("append_sale_log_line: %s", e)


def write_sale_text_artifact(
    ocr_output_dir: Path | None,
    subfolder_leaf: str,
    text: str,
) -> None:
    """Write or overwrite ``{stem}_text.txt`` (pre-OCR combined text or placeholder)."""
    if not ocr_output_dir or not str(subfolder_leaf or "").strip():
        return
    try:
        paths = artifact_paths(ocr_output_dir, subfolder_leaf)
        paths["base"].mkdir(parents=True, exist_ok=True)
        paths["text"].write_text(text or "", encoding="utf-8")
    except OSError as e:
        logger.warning("write_sale_text_artifact: %s", e)


def copy_incoming_scan_pdf(
    ocr_output_dir: Path | None,
    subfolder_leaf: str,
    source_pdf: Path,
) -> None:
    """Copy consolidated PDF to ``{stem}_scan.pdf`` where stem matches folder leaf prefix."""
    if not ocr_output_dir or not source_pdf.is_file():
        return
    try:
        paths = artifact_paths(ocr_output_dir, subfolder_leaf)
        paths["base"].mkdir(parents=True, exist_ok=True)
        stem = stem_from_subfolder_leaf(_safe_subfolder_name(subfolder_leaf))
        if not stem:
            return
        dest = paths["base"] / f"{stem}_scan.pdf"
        shutil.copy2(source_pdf, dest)
    except OSError as e:
        logger.warning("copy_incoming_scan_pdf: %s", e)


def rename_sale_artifact_bundle(
    ocr_output_dir: Path,
    old_leaf: str,
    new_leaf: str,
    old_file_stem: str,
    new_file_stem: str,
) -> None:
    """
    Rename ``ocr_output/.../old_leaf`` → ``new_leaf``, then rename
    ``old_file_stem_{scan,text,log}`` → ``new_file_stem_*``.
    """
    if old_leaf == new_leaf and old_file_stem == new_file_stem:
        return
    o = Path(ocr_output_dir).resolve()
    old_safe = _safe_subfolder_name(old_leaf)
    new_safe = _safe_subfolder_name(new_leaf)
    old_dir = o / old_safe
    new_dir = o / new_safe
    if not old_dir.is_dir():
        return
    try:
        if old_safe != new_safe:
            if new_dir.exists():
                logger.warning("rename_sale_artifact_bundle: target folder exists %s", new_dir)
                return
            old_dir.rename(new_dir)
        base = new_dir
        if old_file_stem != new_file_stem:
            for suffix in ("_scan.pdf", "_text.txt", "_log.txt"):
                op = base / f"{old_file_stem}{suffix}"
                np = base / f"{new_file_stem}{suffix}"
                if op.exists():
                    op.rename(np)
    except OSError as e:
        logger.warning("rename_sale_artifact_bundle: %s", e)


def merged_text_artifact_path(ocr_output_dir: Path, subfolder_leaf: str) -> Path:
    """Path to merged text artifact, preferring ``{stem}_text.txt``."""
    safe = _safe_subfolder_name(subfolder_leaf)
    stem = stem_from_subfolder_leaf(safe)
    base = Path(ocr_output_dir).resolve() / safe
    if stem:
        p = base / f"{stem}_text.txt"
        if p.is_file():
            return p
    legacy = base / LEGACY_RAW_OCR
    return legacy
