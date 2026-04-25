"""
Consolidated OCR artifacts under ``ocr_output/{dealer}/{stem}_{ddmmyy}/``:

- ``inputs_scan.pdf`` — copy of the incoming consolidated PDF (fixed name)
- ``pre_ocr_ddt_text.txt`` — Textract DDT / pre-OCR combined page text (replaces legacy ``pre_ocr_tesseract_text.txt`` / ``pre_ocr_text.txt``)
- ``{stem}_AWS_ocr_text.txt`` — merged Amazon Textract extraction text (replaces legacy ``{stem}_ocr_text.txt``, ``Raw_OCR.txt``, ``{stem}_text.txt``)
- ``{stem}_ocr_log.txt`` — single append-only IST diary for **pre**, **ocr**, and **post** phases (line prefix ``[pre]`` / ``[ocr]`` / ``[post]``)
- Legacy ``pre_ocr_log.txt`` (older runs) is merged into ``{stem}_ocr_log.txt`` on rename/consolidate and removed
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
PRE_OCR_DDT_TEXT_TXT = "pre_ocr_ddt_text.txt"
# Older runs used these names; read-fallback supported during rename/consolidate
LEGACY_PRE_OCR_TESSERACT_TEXT_TXT = "pre_ocr_tesseract_text.txt"
LEGACY_PRE_OCR_TEXT_TXT = "pre_ocr_text.txt"
PRE_OCR_LOG_TXT = "pre_ocr_log.txt"

# Legacy filenames (read fallback)
LEGACY_RAW_OCR = "Raw_OCR.txt"
LEGACY_OCR_LOG = "ocr_extraction_log.txt"
LEGACY_SUFFIX_TEXT = "_text.txt"
LEGACY_SUFFIX_LOG = "_log.txt"
LEGACY_SUFFIX_SCAN = "_scan.pdf"


def ocr_text_filename(stem: str) -> str:
    """Amazon Textract merged text artifact (AWS)."""
    return f"{stem}_AWS_ocr_text.txt"


def legacy_merged_ocr_text_filename(stem: str) -> str:
    """Pre-AWS-naming Textract merge file (read fallback)."""
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
        "pre_ocr_text": base / PRE_OCR_DDT_TEXT_TXT,
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
    Append one IST line to ``{stem}_ocr_log.txt`` for all phases (**pre** / **ocr** / **post**).
    Phase is still reflected in the line as ``[{phase}]``.
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
    """Append a legacy ``pre_ocr_log.txt`` file into ``dest_base/{stem}_ocr_log.txt`` (then delete source)."""
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


def merge_peer_stem_ocr_log_into_mobile(mobile_dir: Path, mobile_stem: str, peer_log_file: Path) -> None:
    """Append a peer ``{any_stem}_ocr_log.txt`` (not legacy pre_ocr_log) into ``{mobile_stem}_ocr_log.txt``."""
    if not peer_log_file.is_file() or peer_log_file.name == PRE_OCR_LOG_TXT:
        return
    try:
        mobile_dir.mkdir(parents=True, exist_ok=True)
        dest = mobile_dir / ocr_log_filename(mobile_stem)
        sep = f"\n{_ist_prefix()} [pre] step=peer_stem_ocr_log_merged name={peer_log_file.name}\n"
        chunk = peer_log_file.read_text(encoding="utf-8")
        with dest.open("a", encoding="utf-8") as f:
            f.write(sep)
            f.write(chunk)
        peer_log_file.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("merge_peer_stem_ocr_log_into_mobile: %s", e)


def write_sale_text_artifact(
    ocr_output_dir: Path | None,
    subfolder_leaf: str,
    text: str,
) -> None:
    """Write or overwrite ``pre_ocr_ddt_text.txt`` (pre-OCR combined Textract DDT text)."""
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
    """Copy consolidated scan (PDF or image) to ``inputs_scan.*`` under the artifact folder."""
    if not ocr_output_dir or not source_pdf.is_file():
        return
    try:
        paths = artifact_paths(ocr_output_dir, subfolder_leaf)
        paths["base"].mkdir(parents=True, exist_ok=True)
        dest = paths["inputs_scan"]
        if source_pdf.suffix.lower() != ".pdf":
            dest = dest.with_suffix(source_pdf.suffix.lower())
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
                (f"{old_file_stem}_AWS_ocr_text.txt", f"{new_file_stem}_AWS_ocr_text.txt"),
                (f"{old_file_stem}_ocr_text.txt", f"{new_file_stem}_AWS_ocr_text.txt"),
                (f"{old_file_stem}_ocr_log.txt", f"{new_file_stem}_ocr_log.txt"),
                (f"{old_file_stem}{LEGACY_SUFFIX_TEXT}", f"{new_file_stem}_AWS_ocr_text.txt"),
                (f"{old_file_stem}{LEGACY_SUFFIX_LOG}", f"{new_file_stem}_ocr_log.txt"),
            ]
            for old_name, new_name in renames:
                op = base / old_name
                np = base / new_name
                if op.exists() and not np.exists():
                    op.rename(np)

        new_pre = base / PRE_OCR_DDT_TEXT_TXT
        for legacy_name in (LEGACY_PRE_OCR_TESSERACT_TEXT_TXT, LEGACY_PRE_OCR_TEXT_TXT):
            leg_pre = base / legacy_name
            if leg_pre.is_file() and not new_pre.is_file():
                try:
                    leg_pre.rename(new_pre)
                except OSError as e:
                    logger.warning("rename_sale_artifact_bundle pre-OCR text migrate: %s", e)
                break

        merge_pre_ocr_log_into_stem_ocr_log(base, new_file_stem)
    except OSError as e:
        logger.warning("rename_sale_artifact_bundle: %s", e)


def merged_text_artifact_path(ocr_output_dir: Path, subfolder_leaf: str) -> Path:
    """Path to merged Textract text artifact, preferring ``{stem}_AWS_ocr_text.txt``."""
    safe = _safe_subfolder_name(subfolder_leaf)
    stem = stem_from_subfolder_leaf(safe)
    base = Path(ocr_output_dir).resolve() / safe
    if stem:
        p_aws = base / ocr_text_filename(stem)
        if p_aws.is_file():
            return p_aws
        p_legacy_stem = base / legacy_merged_ocr_text_filename(stem)
        if p_legacy_stem.is_file():
            return p_legacy_stem
        p_old = base / f"{stem}{LEGACY_SUFFIX_TEXT}"
        if p_old.is_file():
            return p_old
    legacy = base / LEGACY_RAW_OCR
    return legacy


def _peer_has_textract_merged_text(peer: Path) -> bool:
    for f in peer.iterdir():
        if not f.is_file():
            continue
        n = f.name
        if n.endswith("_AWS_ocr_text.txt"):
            return True
        # Legacy Textract merge: {stem}_ocr_text.txt (not pre_ocr_*)
        if n.endswith("_ocr_text.txt") and not n.startswith("pre_ocr_"):
            return True
    return False


def _peer_has_consolidatable_artifacts(peer: Path) -> bool:
    if (peer / INPUTS_SCAN_PDF).is_file():
        return True
    if (peer / PRE_OCR_DDT_TEXT_TXT).is_file() or (peer / LEGACY_PRE_OCR_TESSERACT_TEXT_TXT).is_file() or (peer / LEGACY_PRE_OCR_TEXT_TXT).is_file():
        return True
    if (peer / PRE_OCR_LOG_TXT).is_file():
        return True
    if _peer_has_textract_merged_text(peer):
        return True
    return any(f.is_file() for f in peer.glob("*_ocr_log.txt"))


def remove_if_empty_initial_artifact_dir(
    ocr_output_dir: Path | None,
    mobile_subfolder_leaf: str,
    initial_artifact_subfolder_leaf: str,
) -> None:
    """
    If pre-OCR created ``ocr_output/.../initial_artifact`` and ``rename_sale_artifact_bundle`` did not
    merge it into the mobile folder, a stale empty work directory can remain. When the **mobile** sale
    folder for this run exists and ``initial_artifact`` is a different path that is now empty, remove it.

    ``initial_artifact_subfolder_leaf`` must be the same string pre-OCR used (e.g. from
    :func:`initial_artifact_leaf` on the consolidated PDF stem). No globbing.
    """
    if not ocr_output_dir or not str(mobile_subfolder_leaf or "").strip() or not str(
        initial_artifact_subfolder_leaf or ""
    ).strip():
        return
    safe_mobile = _safe_subfolder_name(mobile_subfolder_leaf)
    safe_init = _safe_subfolder_name(initial_artifact_subfolder_leaf)
    if not safe_init or not safe_mobile or safe_init == safe_mobile:
        return
    root = Path(ocr_output_dir).resolve()
    initial_dir = root / safe_init
    mobile_dir = root / safe_mobile
    if not mobile_dir.is_dir():
        return
    if not initial_dir.is_dir() or initial_dir.resolve() == mobile_dir.resolve():
        return
    try:
        if any(initial_dir.iterdir()):
            return
        initial_dir.rmdir()
    except OSError as e:
        logger.debug("remove_if_empty_initial_artifact_dir: %s", e)


def consolidate_peer_pre_ocr_folder_into_mobile(
    ocr_output_dir: Path,
    mobile_subfolder_leaf: str,
) -> None:
    """
    After manual-apply OCR, merge a leftover ``add_sales_*_{ddmmyy}`` (or similar) folder that still holds
    ``inputs_scan.pdf`` / ``pre_ocr_ddt_text.txt`` / legacy ``pre_ocr_log.txt`` / ``{stem}_ocr_log.txt`` into
    ``{mobile}_{ddmmyy}/``, then remove the peer folder.
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
        if not _peer_has_consolidatable_artifacts(peer):
            continue
        if peer.resolve() == mobile_dir.resolve():
            continue
        try:
            mobile_dir.mkdir(parents=True, exist_ok=True)
            scan_p = peer / INPUTS_SCAN_PDF
            if scan_p.is_file():
                dp = mobile_dir / INPUTS_SCAN_PDF
                if not dp.exists():
                    shutil.move(str(scan_p), str(dp))
                else:
                    scan_p.unlink(missing_ok=True)
            dest_pre = mobile_dir / PRE_OCR_DDT_TEXT_TXT
            for src_name in (PRE_OCR_DDT_TEXT_TXT, LEGACY_PRE_OCR_TESSERACT_TEXT_TXT, LEGACY_PRE_OCR_TEXT_TXT):
                sp = peer / src_name
                if not sp.is_file():
                    continue
                if not dest_pre.exists():
                    shutil.move(str(sp), str(dest_pre))
                else:
                    sp.unlink(missing_ok=True)
            for stem_txt in sorted(peer.glob("*_AWS_ocr_text.txt")):
                dp = mobile_dir / stem_txt.name
                if not dp.exists():
                    shutil.move(str(stem_txt), str(dp))
                else:
                    stem_txt.unlink(missing_ok=True)
            for stem_txt in sorted(peer.glob("*_ocr_text.txt")):
                if stem_txt.name.startswith("pre_ocr_"):
                    continue
                if stem_txt.name.endswith("_AWS_ocr_text.txt"):
                    continue
                stem_part = stem_txt.name[: -len("_ocr_text.txt")]
                dp = mobile_dir / ocr_text_filename(stem_part)
                if not dp.exists():
                    shutil.move(str(stem_txt), str(dp))
                else:
                    stem_txt.unlink(missing_ok=True)
            pl = peer / PRE_OCR_LOG_TXT
            if pl.is_file():
                merge_pre_ocr_log_file_into_dest_ocr_log(pl, mobile_dir, mobile_stem)
            for stem_log in sorted(peer.glob("*_ocr_log.txt")):
                if stem_log.name == PRE_OCR_LOG_TXT:
                    continue
                merge_peer_stem_ocr_log_into_mobile(mobile_dir, mobile_stem, stem_log)
            try:
                if peer.is_dir() and not any(peer.iterdir()):
                    peer.rmdir()
                elif peer.is_dir():
                    shutil.rmtree(peer, ignore_errors=True)
            except OSError:
                shutil.rmtree(peer, ignore_errors=True)
        except OSError as e:
            logger.warning("consolidate_peer_pre_ocr_folder_into_mobile: %s", e)
