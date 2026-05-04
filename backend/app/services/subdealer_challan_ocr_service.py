"""
Parse subdealer Daily Delivery Report scans: challan no, date, engine/chassis lines.
Writes Raw_OCR.txt and OCR_To_be_Used.json under CHALLANS_DIR/<challan>_<ddmmyyyy>/.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import CHALLANS_DIR
from app.services.subdealer_challan_textract import extract_challan_textract

logger = logging.getLogger(__name__)

OCR_JSON_STEM = "OCR_To_be_Used"


def sanitize_challan_line_field(value: str | None) -> str:
    """
    Drop OCR noise: leading/trailing/middle punctuation and non-ID characters.
    Keeps only ASCII letters and digits (strips | . / etc. before, after, or between).
    """
    if not value or not str(value).strip():
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value).strip())


def _safe_folder_segment(s: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join(c for c in (s or "") if c not in bad).strip()
    return (out[:80] if out else "") or "unknown"


def _parse_challan_date_tokens(day: int, month: int, year_short: int) -> tuple[int, int, int]:
    if year_short <= 69:
        year = 2000 + year_short
    else:
        year = 1900 + year_short
    return day, month, year


def parse_challan_date_to_iso(raw: str | None) -> tuple[str | None, str | None]:
    """
    Parse DD/MM/YY or DD/MM/YYYY. Returns (iso_date YYYY-MM-DD, ddmmyyyy) or (None, None).
    """
    if not raw or not str(raw).strip():
        return None, None
    t = raw.strip().strip(".,;:|'\"•·")
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$", t)
    if not m:
        return None, None
    d, mo = int(m.group(1)), int(m.group(2))
    y_s = m.group(3)
    if len(y_s) == 4:
        y = int(y_s)
    else:
        ys = int(y_s)
        _, _, y = _parse_challan_date_tokens(d, mo, ys)
    try:
        datetime(y, mo, d)
    except ValueError:
        return None, None
    iso = f"{y:04d}-{mo:02d}-{d:02d}"
    ddmmyyyy = f"{d:02d}{mo:02d}{y}"
    return iso, ddmmyyyy


def _extract_date_raw_from_text(text: str) -> str | None:
    if not text:
        return None
    for m in re.finditer(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", text):
        candidate = m.group(1).strip().strip(".,;:|'\"•·")
        iso, _ = parse_challan_date_to_iso(candidate)
        if iso:
            return candidate
    return None


def _extract_challan_no(full_text: str, key_value_pairs: list[dict[str, str]]) -> str | None:
    for kv in key_value_pairs:
        k = (kv.get("key") or "").lower()
        v = (kv.get("value") or "").strip()
        if not v:
            continue
        if "challan" in k and re.search(r"\d", v):
            m = re.search(r"(\d{2,6})", v)
            if m:
                return sanitize_challan_line_field(m.group(1)) or m.group(1).strip()
    for pat in (
        r"(?i)challan\s*#?\s*[:\s.-]*\s*(\d{2,6})",
        r"(?i)challan\s+no\.?\s*[:\s.-]*\s*(\d{2,6})",
    ):
        m = re.search(pat, full_text)
        if m:
            return sanitize_challan_line_field(m.group(1)) or m.group(1).strip()
    # Top-of-page standalone 3–4 digit line (common on handwritten forms)
    for line in full_text.splitlines()[:12]:
        line = line.strip()
        if re.fullmatch(r"\d{3,5}", line):
            return line
    return None


def _header_row_engine_chassis_indices(header_row: list[str]) -> tuple[int, int] | None:
    eng_i: int | None = None
    cha_i: int | None = None
    for i, cell in enumerate(header_row):
        low = (cell or "").lower()
        if "engine" in low and "chassis" not in low:
            eng_i = i
        if "chassis" in low:
            cha_i = i
    if eng_i is not None and cha_i is not None:
        return eng_i, cha_i
    return None


def _find_engine_chassis_table(
    tables: list[list[list[str]]],
) -> tuple[list[list[str]], int] | None:
    """Return (table grid, header_row_index) for the grid that has Engine + Chassis headers."""
    for table in tables:
        for hi, row in enumerate(table):
            if not row:
                continue
            joined = " ".join(row).lower()
            if "engine" in joined and "chassis" in joined:
                if _header_row_engine_chassis_indices(row):
                    return table, hi
    return None


def _rows_from_table(table: list[list[str]], header_row_index: int) -> list[dict[str, str]]:
    header = table[header_row_index]
    idx = _header_row_engine_chassis_indices(header)
    if not idx:
        return []
    ei, ci = idx
    out: list[dict[str, str]] = []
    for row in table[header_row_index + 1 :]:
        if ei >= len(row) or ci >= len(row):
            continue
        eng = sanitize_challan_line_field(row[ei] or "")
        cha = sanitize_challan_line_field(row[ci] or "")
        if not eng and not cha:
            continue
        out.append({"engine_no": eng, "chassis_no": cha, "status": "queued"})
    return out


def normalize_challan_vehicle_key(raw_engine: str | None, raw_chassis: str | None) -> tuple[str, str]:
    """Same normalisation as dedupe_challan_lines: strip + upper for engine/chassis pair."""
    e = (raw_engine or "").strip().upper()
    c = (raw_chassis or "").strip().upper()
    return (e, c)


def dedupe_raw_challan_lines(
    lines: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Drop duplicate (raw_engine, raw_chassis) pairs within one request (first wins).
    ``lines`` entries use keys ``raw_engine`` / ``raw_chassis`` (API staging shape).
    Returns (deduped, duplicate_count_dropped).
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    dropped = 0
    for ln in lines:
        key = normalize_challan_vehicle_key(ln.get("raw_engine"), ln.get("raw_chassis"))
        if not key[0] and not key[1]:
            continue
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(ln)
    return out, dropped


def dedupe_challan_lines(lines: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """
    Drop duplicate (engine_no, chassis_no) pairs after normalisation (strip, upper).
    First occurrence wins. Rows with both fields empty are skipped.
    Returns (deduped_lines, duplicate_count_dropped).
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    dropped = 0
    for ln in lines:
        e = (ln.get("engine_no") or "").strip().upper()
        c = (ln.get("chassis_no") or "").strip().upper()
        if not e and not c:
            continue
        key = (e, c)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(ln)
    return out, dropped


def _build_raw_ocr_text(
    full_text: str,
    key_value_pairs: list[dict[str, str]],
    tables: list[list[list[str]]],
) -> str:
    parts = ["=== FULL_TEXT (LINE) ===", full_text, "", "=== KEY_VALUE_PAIRS ==="]
    for kv in key_value_pairs:
        parts.append(f"{kv.get('key', '')}\t{kv.get('value', '')}")
    parts.extend(["", "=== TABLES ==="])
    for ti, table in enumerate(tables):
        parts.append(f"--- table {ti} ---")
        for row in table:
            parts.append(" | ".join(row))
    return "\n".join(parts)


def _challan_folder_name(challan_no: str | None, ddmmyyyy: str | None) -> str:
    cn = _safe_folder_segment(challan_no or "")
    if cn == "unknown" or not ddmmyyyy:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"unknown_{ts}"
    return f"{cn}_{ddmmyyyy}"


def challan_artifact_leaf_name(challan_book_num: str | None, challan_date_stored: str | None) -> str:
    """
    Folder under ``CHALLANS_DIR``, matching OCR artifacts: ``{challan_no}_{ddmmyyyy}``.
    ``challan_date_stored`` may be ``DD/MM/YYYY`` or 8-digit ``ddmmyyyy`` (as from the client).
    """
    raw = (challan_date_stored or "").strip()
    ddmmyyyy: str | None = None
    if len(raw) == 8 and raw.isdigit():
        ddmmyyyy = raw
    else:
        _, ddmmyyyy = parse_challan_date_to_iso(raw if raw else None)
    if not ddmmyyyy:
        d = datetime.now(timezone.utc)
        ddmmyyyy = f"{d.day:02d}{d.month:02d}{d.year}"
    return _challan_folder_name(challan_book_num, ddmmyyyy)


# Cap JSON + Raw_OCR returned for Electron local mirror (parse-scan ?mirror_bodies=true).
_MAX_CHALLAN_MIRROR_COMBINED_BYTES = 2_000_000


def parse_subdealer_challan(
    document_bytes: bytes,
    *,
    challans_base: Path | None = None,
    write_artifacts: bool = True,
    include_artifact_bodies: bool = False,
) -> dict[str, Any]:
    """
    Textract + parse Daily Delivery Report. Optionally writes CHALLANS_DIR/<challan>_<ddmmyyyy>/.

    Returns:
      challan_no, challan_date_raw, challan_date_iso, challan_ddmmyyyy (folder suffix),
      lines: [{engine_no, chassis_no, status}],
      artifact_dir, raw_ocr_path, ocr_json_path (if written),
      warnings, error
      When ``include_artifact_bodies`` is True: local_artifact_leaf, raw_ocr_text, ocr_json_text
      (for dealer PC mirror under ocr_output/{dealer_id}/…) if under size cap.
    """
    base = Path(challans_base) if challans_base is not None else CHALLANS_DIR
    out: dict[str, Any] = {
        "challan_no": None,
        "challan_date_raw": None,
        "challan_date_iso": None,
        "challan_ddmmyyyy": None,
        "lines": [],
        "artifact_dir": None,
        "raw_ocr_path": None,
        "ocr_json_path": None,
        "warnings": [],
        "error": None,
    }

    tx = extract_challan_textract(document_bytes)
    if tx.get("error"):
        out["error"] = str(tx["error"])
        return out

    full_text = tx.get("full_text") or ""
    kvp = tx.get("key_value_pairs") or []
    tables = tx.get("tables") or []

    challan_no = _extract_challan_no(full_text, kvp)
    date_raw = _extract_date_raw_from_text(full_text)
    iso, ddmmyyyy = parse_challan_date_to_iso(date_raw)

    lines: list[dict[str, str]] = []
    found = _find_engine_chassis_table(tables)
    if found:
        grid, hi = found
        lines = _rows_from_table(grid, hi)
        lines, dup_n = dedupe_challan_lines(lines)
        if dup_n:
            out["warnings"].append(
                f"Removed {dup_n} duplicate row(s) with the same engine and chassis numbers."
            )
    else:
        out["warnings"].append("No table with Engine and Chassis headers found; lines empty.")

    if not challan_no:
        out["warnings"].append("Challan number not detected.")
    if not date_raw:
        out["warnings"].append("Challan date not detected; using today for folder name if needed.")

    if not ddmmyyyy:
        d = datetime.now(timezone.utc)
        ddmmyyyy = f"{d.day:02d}{d.month:02d}{d.year}"

    out["challan_no"] = challan_no
    out["challan_date_raw"] = date_raw
    out["challan_date_iso"] = iso
    out["challan_ddmmyyyy"] = ddmmyyyy
    out["lines"] = lines

    payload = {
        "challan_no": challan_no,
        "challan_date_raw": date_raw,
        "challan_date_iso": iso,
        "lines": lines,
    }

    leaf_name = _challan_folder_name(challan_no, ddmmyyyy)
    raw_file_body = _build_raw_ocr_text(full_text, kvp, tables)
    json_file_body = json.dumps(payload, indent=2, ensure_ascii=False)

    if include_artifact_bodies:
        combined = len(raw_file_body) + len(json_file_body)
        if combined > _MAX_CHALLAN_MIRROR_COMBINED_BYTES:
            out["warnings"].append(
                "OCR mirror bodies omitted (combined text exceeds cap for dealer PC sync)."
            )
        else:
            out["local_artifact_leaf"] = leaf_name
            out["raw_ocr_text"] = raw_file_body
            out["ocr_json_text"] = json_file_body

    if write_artifacts:
        try:
            base.mkdir(parents=True, exist_ok=True)
            dest = (base / leaf_name).resolve()
            dest.mkdir(parents=True, exist_ok=True)
            raw_path = dest / "Raw_OCR.txt"
            json_path = dest / f"{OCR_JSON_STEM}.json"
            raw_path.write_text(raw_file_body, encoding="utf-8")
            json_path.write_text(json_file_body, encoding="utf-8")
            out["artifact_dir"] = str(dest)
            out["raw_ocr_path"] = str(raw_path)
            out["ocr_json_path"] = str(json_path)
        except OSError as e:
            logger.exception("subdealer challan artifact write failed")
            out["warnings"].append(f"Could not write artifacts: {e}")

    return out
