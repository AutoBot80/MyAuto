"""
Consolidated OCR artifacts under ``ocr_output/{dealer}/{stem}_{ddmmyy}/``:

- ``inputs_scan.pdf`` — copy of the incoming consolidated PDF (fixed name)
- ``pre_ocr_text.txt`` — Tesseract / pre-OCR combined page text
- ``pre_ocr_log.txt`` — IST pre-OCR step lines; merged into ``{stem}_ocr_log.txt`` when the sale folder is finalized
- ``{stem}_ocr_text.txt`` — merged Textract extraction text (replaces legacy ``Raw_OCR.txt`` / ``{stem}_text.txt``)
- ``{stem}_ocr_log.txt`` — IST diary for OCR/post phases plus merged pre-OCR log
- ``OCR_To_be_Used.json`` — unchanged (written by :mod:`sales_ocr_service`)

Subfolder starts as ``{file_stem}_{ddmmyy}`` and is renamed to ``{mobile}_{ddmmyy}`` when mobile is known.
After manual page assignment, the add-sales artifact folder is consolidated into the mobile folder.
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

# Fixed names (no stem prefix) for the incoming consolidated scan and pre-OCR artifacts
INPUTS_SCAN_PDF = "inputs_scan.pdf"
PRE_OCR_TEXT_TXT = "pre_ocr_text.txt"
PRE_OCR_LOG_TXT = "pre_ocr_log.txt"

# Legacy filenames (read fallback)
LEGACY_RAW_OCR = "Raw_OCR.txt"
LEGACY_OCR_LOG = "ocr_extraction_log.txt"
LEGACY_SUFFIX_TEXT = "_text.txt"
LEGACY_SUFFIX_LOG = "_log.txt"
LEGACY_SUFFIX_SCAN = "_scan.pdf"


def ocr_text_filename(stem: str) -> str:
    return f"{stem}_ocr_text.txt"


def ocr_log_filename(stem: str) -> str:
    return f"{stem}_ocr_log.txt"


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
    """Resolved paths under the sale subfolder (see module docstring)."""
    stem = stem_from_subfolder_leaf(subfolder_leaf)
    if not stem:
        stem = "unknown"
    safe = _safe_subfolder_name(subfolder_leaf)
    base = Path(ocr_output_dir).resolve() / safe
    return {
        "base": base,
        "inputs_scan": base / INPUTS_SCAN_PDF,
        "pre_ocr_text": base / PRE_OCR_TEXT_TXT,
        "pre_ocr_log": base / PRE_OCR_LOG_TXT,
        "ocr_text": base / ocr_text_filename(stem),
        "ocr_log": base / ocr_log_filename(stem),
        "json": base / "OCR_To_be_Used.json",
        # legacy keys for older callers (prefer ocr_text / ocr_log)
        "text": base / ocr_text_filename(stem),
        "log": base / ocr_log_filename(stem),
        "scan": base / INPUTS_SCAN_PDF,
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
    """
    Append one IST line: **pre** phase → ``pre_ocr_log.txt``; **ocr** / **post** → ``{stem}_ocr_log.txt``.
    """
    if not ocr_output_dir or not str(subfolder_leaf or "").strip():
        return
    safe_leaf = _safe_subfolder_name(subfolder_leaf)
    stem = stem_from_subfolder_leaf(safe_leaf)
    if not stem:
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
        if phase == "pre":
            log_path = paths["pre_ocr_log"]
        else:
            log_path = paths["ocr_log"]
        line = f"{_ist_prefix()} [{phase}] {message.rstrip()}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.debug("append_sale_log_line: %s", e)


def merge_pre_ocr_log_into_stem_ocr_log(base: Path, stem: str) -> None:
    """
    Append ``pre_ocr_log.txt`` into ``{stem}_ocr_log.txt`` and remove ``pre_ocr_log.txt``.
    Used after folder rename (auto path) or when consolidating add-sales into mobile folder.
    """
    pre = base / PRE_OCR_LOG_TXT
    dest = base / ocr_log_filename(stem)
    if not pre.is_file():
        return
    try:
        base.mkdir(parents=True, exist_ok=True)
        sep = f"\n{_ist_prefix()} [pre] step=pre_ocr_log_merged_into_ocr_log\n"
        chunk = pre.read_text(encoding="utf-8")
        with dest.open("a", encoding="utf-8") as f:
            f.write(sep)
            f.write(chunk)
        pre.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("merge_pre_ocr_log_into_stem_ocr_log: %s", e)


def merge_pre_ocr_log_file_into_dest_ocr_log(pre_log_path: Path, dest_base: Path, stem: str) -> None:
    """Append a specific ``pre_ocr_log`` file into ``dest_base/{stem}_ocr_log.txt`` (then delete source)."""
    if not pre_log_path.is_file():
        return
    try:
        dest_base.mkdir(parents=True, exist_ok=True)
        dest = dest_base / ocr_log_filename(stem)
        sep = f"\n{_ist_prefix()} [pre] step=pre_ocr_log_merged_from_peer_folder\n"
        chunk = pre_log_path.read_text(encoding="utf-8")
        with dest.open("a", encoding="utf-8") as f:
            f.write(sep)
            f.write(chunk)
        pre_log_path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("merge_pre_ocr_log_file_into_dest_ocr_log: %s", e)


def write_sale_text_artifact(
    ocr_output_dir: Path | None,
    subfolder_leaf: str,
    text: str,
) -> None:
    """Write or overwrite ``pre_ocr_text.txt`` (pre-OCR combined Tesseract text)."""
    if not ocr_output_dir or not str(subfolder_leaf or "").strip():
        return
    try:
        paths = artifact_paths(ocr_output_dir, subfolder_leaf)
        paths["base"].mkdir(parents=True, exist_ok=True)
        paths["pre_ocr_text"].write_text(text or "", encoding="utf-8")
    except OSError as e:
        logger.warning("write_sale_text_artifact: %s", e)


def copy_incoming_scan_pdf(
    ocr_output_dir: Path | None,
    subfolder_leaf: str,
    source_pdf: Path,
) -> None:
    """Copy consolidated PDF to ``inputs_scan.pdf`` under the artifact folder."""
    if not ocr_output_dir or not source_pdf.is_file():
        return
    try:
        paths = artifact_paths(ocr_output_dir, subfolder_leaf)
        paths["base"].mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_pdf, paths["inputs_scan"])
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
    Rename ``ocr_output/.../old_leaf`` → ``new_leaf``, rename stemmed OCR artifacts, merge ``pre_ocr_log``.
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
            renames: list[tuple[str, str]] = [
                (f"{old_file_stem}_ocr_text.txt", f"{new_file_stem}_ocr_text.txt"),
                (f"{old_file_stem}_ocr_log.txt", f"{new_file_stem}_ocr_log.txt"),
                (f"{old_file_stem}{LEGACY_SUFFIX_TEXT}", f"{new_file_stem}_ocr_text.txt"),
                (f"{old_file_stem}{LEGACY_SUFFIX_LOG}", f"{new_file_stem}_ocr_log.txt"),
            ]
            for old_name, new_name in renames:
                op = base / old_name
                np = base / new_name
                if op.exists() and not np.exists():
                    op.rename(np)

        merge_pre_ocr_log_into_stem_ocr_log(base, new_file_stem)
    except OSError as e:
        logger.warning("rename_sale_artifact_bundle: %s", e)


def merged_text_artifact_path(ocr_output_dir: Path, subfolder_leaf: str) -> Path:
    """Path to merged Textract text artifact, preferring ``{stem}_ocr_text.txt``."""
    safe = _safe_subfolder_name(subfolder_leaf)
    stem = stem_from_subfolder_leaf(safe)
    base = Path(ocr_output_dir).resolve() / safe
    if stem:
        p_new = base / ocr_text_filename(stem)
        if p_new.is_file():
            return p_new
        p_old = base / f"{stem}{LEGACY_SUFFIX_TEXT}"
        if p_old.is_file():
            return p_old
    legacy = base / LEGACY_RAW_OCR
    return legacy


def consolidate_peer_pre_ocr_folder_into_mobile(
    ocr_output_dir: Path,
    mobile_subfolder_leaf: str,
) -> None:
    """
    After manual-apply OCR, merge a leftover ``add_sales_*_{ddmmyy}`` (or similar) folder that still holds
    ``inputs_scan.pdf`` / ``pre_ocr_text.txt`` / ``pre_ocr_log.txt`` into ``{mobile}_{ddmmyy}/``, then remove the peer folder.
    """
    safe_mobile = _safe_subfolder_name(mobile_subfolder_leaf)
    mobile_dir = Path(ocr_output_dir).resolve() / safe_mobile
    parsed = parse_sale_subfolder_leaf(safe_mobile)
    if not parsed:
        return
    mobile_stem, ddmmyy = parsed

    root = Path(ocr_output_dir).resolve()
    for peer in root.iterdir():
        if not peer.is_dir() or peer.name == safe_mobile:
            continue
        if not peer.name.endswith(f"_{ddmmyy}"):
            continue
        if not ((peer / INPUTS_SCAN_PDF).is_file() or (peer / PRE_OCR_LOG_TXT).is_file() or (peer / PRE_OCR_TEXT_TXT).is_file()):
            continue
        if peer.resolve() == mobile_dir.resolve():
            continue
        try:
            mobile_dir.mkdir(parents=True, exist_ok=True)
            for fname in (INPUTS_SCAN_PDF, PRE_OCR_TEXT_TXT):
                sp = peer / fname
                if sp.is_file():
                    dp = mobile_dir / fname
                    if not dp.exists():
                        shutil.move(str(sp), str(dp))
                    else:
                        sp.unlink(missing_ok=True)
            pl = peer / PRE_OCR_LOG_TXT
            if pl.is_file():
                merge_pre_ocr_log_file_into_dest_ocr_log(pl, mobile_dir, mobile_stem)
            try:
                if peer.is_dir() and not any(peer.iterdir()):
                    peer.rmdir()
                elif peer.is_dir():
                    shutil.rmtree(peer, ignore_errors=True)
            except OSError:
                shutil.rmtree(peer, ignore_errors=True)
        except OSError as e:
            logger.warning("consolidate_peer_pre_ocr_folder_into_mobile: %s", e)
