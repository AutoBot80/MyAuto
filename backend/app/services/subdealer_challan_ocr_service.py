"""
Parse subdealer Daily Delivery Report scans: challan no, date, engine/chassis lines.
Writes Raw_OCR.txt and OCR_To_be_Used.json under ``challans_base/<challan>_<ddmmyyyy>/``
(default: global CHALLANS_DIR; API parse-scan uses dealer ``ocr_output/.../subdealer_challan/``).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import string
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

from app.config import CHALLANS_DIR  # default artifact root when challans_base is omitted
from app.services.subdealer_challan_textract import extract_challan_textract

logger = logging.getLogger(__name__)

OCR_JSON_STEM = "OCR_To_be_Used"
_DEFAULT_CHALLAN_ALPHABET = string.ascii_uppercase + string.digits


def generate_default_challan_no(dealer_id: int) -> str:
    """Last 4 digits of ``dealer_id`` + 4 random alphanumerics (A-Z, 0-9)."""
    prefix = str(int(dealer_id))[-4:].zfill(4)
    suffix = "".join(secrets.choice(_DEFAULT_CHALLAN_ALPHABET) for _ in range(4))
    return f"{prefix}{suffix}"


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


def _header_cell_is_engine_column(low: str) -> bool:
    """Engine column: Engine No, Eng, Engg, etc. (OCR shortenings and typos)."""
    t = (low or "").strip().lower()
    if not t:
        return False
    if _header_cell_is_chassis_column(t):
        return False
    return bool(re.search(r"\b(?:eng(?:ine|g)?)\b", t))


def _header_cell_is_chassis_column(low: str) -> bool:
    """Chassis / VIN column: Chassis, Chasis, Frame, VIN, Cha, Chas, Chess, etc."""
    t = (low or "").strip().lower()
    if not t:
        return False
    return bool(
        re.search(
            r"\b(?:"
            r"chass?is|"
            r"chasis|"
            r"frame|"
            r"vin|"
            r"chas?|"
            r"chess"
            r")\b",
            t,
        )
    )


def _joined_row_has_engine_signal(joined_lower: str) -> bool:
    return bool(re.search(r"\b(?:eng(?:ine|g)?)\b", joined_lower))


def _joined_row_has_chassis_signal(joined_lower: str) -> bool:
    t = joined_lower or ""
    return bool(
        re.search(
            r"\b(?:"
            r"chass?is|"
            r"chasis|"
            r"frame|"
            r"vin|"
            r"chas?|"
            r"chess"
            r")\b",
            t,
        )
    )


def _header_row_engine_chassis_indices(header_row: list[str]) -> tuple[int, int] | None:
    eng_i: int | None = None
    cha_i: int | None = None
    for i, cell in enumerate(header_row):
        low = (cell or "").lower()
        if _header_cell_is_engine_column(low):
            eng_i = i
        if _header_cell_is_chassis_column(low):
            cha_i = i
    if eng_i is not None and cha_i is not None:
        return eng_i, cha_i
    return None


def _find_engine_chassis_table(
    tables: list[list[list[str]]],
) -> tuple[list[list[str]], int] | None:
    """Return (table grid, header_row_index) for the grid that has Engine + Chassis/Frame/VIN headers."""
    for table in tables:
        for hi, row in enumerate(table):
            if not row:
                continue
            joined = " ".join(str(c or "") for c in row).lower()
            if _joined_row_has_engine_signal(joined) and _joined_row_has_chassis_signal(joined):
                if _header_row_engine_chassis_indices(row):
                    return table, hi
    return None


def _strip_cell_suffix_noise(cell: str) -> str:
    """Textract often glues ``the`` / ``to`` into the invoice cell."""
    s = (cell or "").strip()
    return re.sub(r"\s+(the|to|of)\s*$", "", s, flags=re.I).strip()


def _best_id_token_from_cell(cell: str, *, role: str) -> str:
    """Pick the best token inside a merged / noisy TABLE cell."""
    raw = _strip_cell_suffix_noise(cell or "")
    if not raw:
        return ""
    parts = re.split(r"\s+", raw)
    if role == "inv":
        best = ""
        for part in parts:
            s = sanitize_challan_line_field(part)
            if _is_excise_invoice_like_token(s) and len(s) > len(best):
                best = s
        return best
    if role == "cha":
        for part in parts:
            s = sanitize_challan_line_field(part)
            if _is_hero_chassis_frame_token(s):
                return s
        return ""
    if role == "eng":
        for part in parts:
            s = sanitize_challan_line_field(part)
            if _is_hero_engine_token(s):
                return s
        return ""
    return ""


def _infer_engine_chassis_cols_from_data_row(row: list[str]) -> tuple[int, int] | None:
    """Infer (engine_col, chassis_col) from a row that already looks like Model Details data."""
    cha_i: int | None = None
    eng_i: int | None = None
    for i, cell in enumerate(row):
        if _best_id_token_from_cell(str(cell), role="cha"):
            cha_i = i if cha_i is None else cha_i
        if _best_id_token_from_cell(str(cell), role="eng"):
            eng_i = i if eng_i is None else eng_i
    if cha_i is None or eng_i is None or cha_i == eng_i:
        return None
    return eng_i, cha_i


def _find_loose_model_details_table(
    tables: list[list[list[str]]],
) -> tuple[list[list[str]], int, int, int] | None:
    """
    Textract TABLE with merged / split headers (``Frame`` + ``No`` on different rows) so strict
    header matching never fires. Pick the (table, start_row, engine_i, chassis_i) with the most
    rows that look like ``MB…`` + ``HA…`` data.
    """
    best: tuple[list[list[str]], int, int, int, int] | None = None
    for table in tables:
        if not table:
            continue
        for start in range(len(table)):
            row = table[start]
            if not row or not any(str(c or "").strip() for c in row):
                continue
            inf = _infer_engine_chassis_cols_from_data_row([str(c) for c in row])
            if inf is None:
                continue
            ei, ci = inf
            n_valid = 0
            for r in table[start:]:
                if max(ei, ci) >= len(r):
                    continue
                eng = _best_id_token_from_cell(str(r[ei]) if ei < len(r) else "", role="eng")
                cha = _best_id_token_from_cell(str(r[ci]) if ci < len(r) else "", role="cha")
                if _is_hero_engine_token(eng) and _is_hero_chassis_frame_token(cha):
                    n_valid += 1
            if best is None or n_valid > best[4]:
                best = (table, start, ei, ci, n_valid)
    if best is None or best[4] < 1:
        return None
    return best[0], best[1], best[2], best[3]


def _collect_vehicle_lines_from_tables(
    tables: list[list[list[str]]],
) -> tuple[list[dict[str, str]], bool, bool]:
    """
    Extract vehicle rows from **every** Textract table (multi-page challans).

    Page 1 often has ``Frame No`` / ``Engine No`` headers; page 2+ may be a continuation grid
    with data rows only — those are picked up via loose column inference per table.
    """
    lines: list[dict[str, str]] = []
    used_strict = False
    used_loose = False
    for table in tables:
        if not table:
            continue
        header_row_index: int | None = None
        for hi, row in enumerate(table):
            if not row:
                continue
            joined = " ".join(str(c or "") for c in row).lower()
            if _joined_row_has_engine_signal(joined) and _joined_row_has_chassis_signal(joined):
                if _header_row_engine_chassis_indices(row):
                    header_row_index = hi
                    break
        if header_row_index is not None:
            lines.extend(_rows_from_table(table, header_row_index))
            used_strict = True
            continue
        loose = _find_loose_model_details_table([table])
        if loose is not None:
            grid_l, sr, ei, ci = loose
            lines.extend(_rows_from_table_merged_headers(grid_l, sr, ei, ci))
            used_loose = True
    return lines, used_strict, used_loose


def _extract_vehicle_lines_from_textract(
    tx: dict[str, Any],
) -> tuple[list[dict[str, str]], bool, bool, bool, int, int]:
    """
    Collect vehicle rows per Textract page (TABLE first, then LINE fallback per page).

    Returns ``(lines, used_strict, used_loose, used_line_fallback, continuation_recovered, dup_removed)``.
    """
    per_page = tx.get("per_page")
    if per_page:
        pages: list[dict[str, Any]] = list(per_page)
    else:
        pages = [
            {
                "full_text": (tx.get("full_text") or "").strip(),
                "tables": tx.get("tables") or [],
            }
        ]

    raw_lines: list[dict[str, str]] = []
    used_strict = False
    used_loose = False
    used_line_fallback = False

    for page in pages:
        page_tables = page.get("tables") or []
        page_text = (page.get("full_text") or "").strip()
        table_lines, us, ul = _collect_vehicle_lines_from_tables(page_tables)
        used_strict = used_strict or us
        used_loose = used_loose or ul
        if table_lines:
            raw_lines.extend(table_lines)
            continue
        fb_lines, _ = _fallback_lines_from_full_text(page_text)
        if fb_lines:
            used_line_fallback = True
            raw_lines.extend(fb_lines)

    count_after_pages = len(raw_lines)
    pages_processed = int(tx.get("pages_processed") or 0)
    continuation_recovered = 0

    if pages_processed > 1:
        merged_text = (tx.get("full_text") or "").strip()
        fb_merged, _ = _fallback_lines_from_full_text(merged_text)
        if fb_merged:
            raw_lines.extend(fb_merged)

    lines, dup_n = dedupe_challan_lines(raw_lines)
    if pages_processed > 1:
        continuation_recovered = max(0, len(lines) - count_after_pages)
        if continuation_recovered > 0:
            used_line_fallback = True

    return lines, used_strict, used_loose, used_line_fallback, continuation_recovered, dup_n


def _rows_from_table_merged_headers(
    table: list[list[str]], start_row: int, eng_i: int, cha_i: int
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in table[start_row:]:
        if max(eng_i, cha_i) >= len(r):
            continue
        eng = _best_id_token_from_cell(str(r[eng_i]) if eng_i < len(r) else "", role="eng")
        cha = _best_id_token_from_cell(str(r[cha_i]) if cha_i < len(r) else "", role="cha")
        if not (_is_hero_engine_token(eng) and _is_hero_chassis_frame_token(cha)):
            continue
        out.append({"engine_no": eng, "chassis_no": cha, "status": "queued"})
    return out


def _invoice_from_table_column_zero(table: list[list[str]], start_row: int) -> str | None:
    vals: list[str] = []
    for r in table[start_row:]:
        if not r:
            continue
        inv = _best_id_token_from_cell(str(r[0]), role="inv")
        if inv:
            vals.append(inv)
    return _dominant_invoice_from_tokens(vals)


def _vertical_model_details_field_start(lines: list[str]) -> int | None:
    """First data line index after ``Material`` column header (Textract LINE layout)."""
    for i, raw in enumerate(lines):
        if raw.strip().lower() == "material":
            return i + 1
    return None


def _is_single_cell_field_line(line: str) -> bool:
    """One OCR cell per LINE (no spaces), typical for column-major Textract dumps."""
    raw = line.strip()
    if not raw or " " in raw:
        return False
    if len(raw) < 6:
        return False
    return bool(re.match(r"^[A-Za-z0-9.\-/]+$", raw))


def _is_vertical_noise_line(line: str) -> bool:
    low = line.strip().lower()
    if len(low) <= 3 and low.isalpha():
        return low in frozenset({"the", "to", "of", "or", "a", "an", "in", "on", "at", "by"})
    return False


def _is_hero_chassis_frame_token(san: str) -> bool:
    return 14 <= len(san) <= 19 and san.startswith("MB") and bool(re.match(r"^[A-Z0-9]+$", san))


def _is_hero_engine_token(san: str) -> bool:
    return 8 <= len(san) <= 18 and san.startswith("HA") and bool(re.match(r"^[A-Z0-9]+$", san))


def _is_excise_invoice_like_token(san: str) -> bool:
    if len(san) < 8 or len(san) > 18:
        return False
    if san.startswith("MB") or san.startswith("HA") or san.startswith("HSPL"):
        return False
    if not bool(re.search(r"\d", san)) or not bool(re.search(r"[A-Za-z]", san)):
        return False
    return bool(re.match(r"^[A-Z0-9]+$", san))


def _dominant_invoice_from_tokens(invoices: list[str]) -> str | None:
    """Same excise invoice repeated; tolerate rare OCR variants (e.g. S vs 5) via majority vote."""
    cleaned = [sanitize_challan_line_field(x) for x in invoices if sanitize_challan_line_field(x)]
    if not cleaned:
        return None
    uniq = set(cleaned)
    if len(uniq) == 1:
        return cleaned[0]
    c = Counter(cleaned)
    val, n = c.most_common(1)[0]
    if n >= max(3, int(0.72 * len(cleaned))):
        return val
    return None


def _parse_vertical_model_details_lines(full_text: str) -> tuple[list[dict[str, str]], str | None]:
    """
    Textract often emits **one table cell per LINE** (column-major reading order).

    Pair each ``MB…`` frame/VIN token with the next ``HA…`` engine token; skip invoices, material codes,
    and short noise lines between them.
    """
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    si = _vertical_model_details_field_start(lines)
    if si is None:
        return [], None

    pending_cha: str | None = None
    out_lines: list[dict[str, str]] = []
    invoices: list[str] = []

    for raw in lines[si:]:
        if _is_vertical_noise_line(raw):
            continue
        low = raw.lower()
        if low.startswith("authorised") or low.startswith("authorized"):
            continue
        if not _is_single_cell_field_line(raw):
            continue
        san = sanitize_challan_line_field(raw)
        if not san:
            continue
        if _is_excise_invoice_like_token(san):
            invoices.append(san)
            continue
        if san.startswith("HSPL") and len(san) >= 10:
            continue
        if _is_hero_chassis_frame_token(san):
            pending_cha = san
            continue
        if _is_hero_engine_token(san):
            if pending_cha:
                out_lines.append({"engine_no": san, "chassis_no": pending_cha, "status": "queued"})
                pending_cha = None
            continue
        # Unknown token: drop orphan chassis so a later MB row can pair cleanly
        if pending_cha:
            pending_cha = None

    inv_one = _dominant_invoice_from_tokens(invoices)
    return out_lines, inv_one


def _parse_horizontal_chassis_engine_pair(parts: list[str]) -> dict[str, str] | None:
    """Two-token line: chassis/frame then engine (KAMAN-style continuation pages)."""
    if len(parts) != 2:
        return None
    cha = sanitize_challan_line_field(parts[0] or "")
    eng = sanitize_challan_line_field(parts[1] or "")
    if _is_hero_chassis_frame_token(cha) and _is_hero_engine_token(eng):
        return {"engine_no": eng, "chassis_no": cha, "status": "queued"}
    return None


def _parse_horizontal_model_details_lines(full_text: str) -> tuple[list[dict[str, str]], str | None]:
    """One logical row per LINE: ``<invoice> <frame> <engine> …`` (whitespace-separated)."""
    out_lines: list[dict[str, str]] = []
    invoices: list[str] = []
    for raw in full_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = re.split(r"\s+", line)
        pair = _parse_horizontal_chassis_engine_pair(parts)
        if pair is not None:
            out_lines.append(pair)
            continue
        cha_i: int | None = None
        eng_i: int | None = None
        for i, part in enumerate(parts):
            san = sanitize_challan_line_field(part)
            if cha_i is None and _is_hero_chassis_frame_token(san):
                cha_i = i
            elif eng_i is None and _is_hero_engine_token(san):
                eng_i = i
        if cha_i is None or eng_i is None or cha_i == eng_i:
            continue
        cha = sanitize_challan_line_field(parts[cha_i])
        eng = sanitize_challan_line_field(parts[eng_i])
        for i, part in enumerate(parts):
            if i in (cha_i, eng_i):
                continue
            inv_cand = sanitize_challan_line_field(part)
            if _is_excise_invoice_like_token(inv_cand):
                invoices.append(inv_cand)
        out_lines.append({"engine_no": eng, "chassis_no": cha, "status": "queued"})
    inv_one = _dominant_invoice_from_tokens(invoices)
    return out_lines, inv_one


def _fallback_lines_from_full_text(full_text: str) -> tuple[list[dict[str, str]], str | None]:
    """
    When Textract TABLE blocks are missing or do not match, parse LINE-style ``full_text``.

    Tries **vertical** (one cell per line, Model Details column order) first, then **horizontal**
    (all columns on one LINE).
    """
    v_lines, v_inv = _parse_vertical_model_details_lines(full_text)
    if v_lines:
        return v_lines, v_inv
    return _parse_horizontal_model_details_lines(full_text)


def _invoice_column_index(header_row: list[str]) -> int | None:
    """Excise Invoice, Tax Invoice, or standalone Invoice column (narrow)."""
    for i, cell in enumerate(header_row):
        low = (cell or "").lower()
        if "excise" in low and "invoice" in low:
            return i
    for i, cell in enumerate(header_row):
        low = (cell or "").lower()
        if re.search(r"\binvoice\b", low) and "excise" not in low:
            return i
    return None


_INVOICE_COLUMN_LOOKBACK = 4


def _invoice_column_index_for_table(table: list[list[str]], header_row_index: int) -> int | None:
    """
    Column index for excise invoice values on data rows. Textract often splits labels across rows:
    the Engine/Frame header row may be ``<sample_id> | Frame No | Engine No | Material`` with no
    ``Excise Invoice`` text on that row. Scan upward for a row that names the invoice column, then
    fall back to column 0 when that row is a hybrid (titles + sample ID in the first cell).
    """
    lo = max(0, header_row_index - _INVOICE_COLUMN_LOOKBACK)
    for ri in range(header_row_index, lo - 1, -1):
        if ri < 0 or ri >= len(table):
            continue
        inv_i = _invoice_column_index(table[ri])
        if inv_i is not None:
            return inv_i
    if header_row_index < len(table):
        row = table[header_row_index]
        if not row:
            return None
        joined = " ".join(str(c or "") for c in row).lower()
        if _joined_row_has_chassis_signal(joined) and _joined_row_has_engine_signal(joined):
            v0 = sanitize_challan_line_field(row[0] or "")
            if v0 and _is_excise_invoice_like_token(v0):
                return 0
    return None


def _challan_no_from_repeated_invoice(table: list[list[str]], header_row_index: int) -> str | None:
    """
    Book number from the invoice column on body rows. Resolves the column when labels sit on
    rows above the Frame/Engine header, and uses dominant voting so OCR variants (e.g. ``3U`` vs
    truncated ``U``) still resolve when one token clearly repeats.
    """
    inv_i = _invoice_column_index_for_table(table, header_row_index)
    if inv_i is None:
        return None
    vals: list[str] = []
    for row in table[header_row_index + 1 :]:
        if inv_i >= len(row):
            continue
        v = sanitize_challan_line_field(row[inv_i] or "")
        if v:
            vals.append(v)
    return _dominant_invoice_from_tokens(vals)


def _ist_now() -> datetime:
    return datetime.now(_IST)


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
        ts = _ist_now().strftime("%Y%m%d_%H%M%S")
        return f"unknown_{ts}"
    return f"{cn}_{ddmmyyyy}"


def challan_artifact_leaf_name(challan_book_num: str | None, challan_date_stored: str | None) -> str:
    """
    Folder leaf matching OCR artifacts: ``{challan_no}_{ddmmyyyy}`` (under ``challans_base`` or dealer OCR subtree).
    ``challan_date_stored`` may be ``DD/MM/YYYY`` or 8-digit ``ddmmyyyy`` (as from the client).
    """
    raw = (challan_date_stored or "").strip()
    ddmmyyyy: str | None = None
    if len(raw) == 8 and raw.isdigit():
        ddmmyyyy = raw
    else:
        _, ddmmyyyy = parse_challan_date_to_iso(raw if raw else None)
    if not ddmmyyyy:
        d = _ist_now().date()
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
    dealer_id: int | None = None,
    assign_default_challan_no: bool = True,
) -> dict[str, Any]:
    """
    Textract + parse Daily Delivery Report. Optionally writes under ``challans_base/<challan>_<ddmmyyyy>/``.

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

    pages_processed = int(tx.get("pages_processed") or 0)
    pages_failed = int(tx.get("pages_failed") or 0)
    if pages_processed > 1:
        out["warnings"].append(
            f"Multi-page PDF ({pages_processed} pages) processed page-by-page for OCR."
        )
    if pages_failed:
        out["warnings"].append(
            f"Textract failed on {pages_failed} of {pages_processed} page(s); results use successful pages only."
        )

    full_text = tx.get("full_text") or ""
    kvp = tx.get("key_value_pairs") or []
    tables = tx.get("tables") or []

    challan_no = _extract_challan_no(full_text, kvp)
    date_raw = _extract_date_raw_from_text(full_text)
    ocr_date_missing = not date_raw
    iso, ddmmyyyy = parse_challan_date_to_iso(date_raw)

    fallback_inv: str | None = None

    lines, _used_strict, used_table_loose, used_line_fallback, continuation_recovered, dup_n = (
        _extract_vehicle_lines_from_textract(tx)
    )

    if dup_n:
        out["warnings"].append(
            f"Removed {dup_n} duplicate row(s) with the same engine and chassis numbers."
        )
    if continuation_recovered > 0:
        out["warnings"].append(
            f"Recovered {continuation_recovered} vehicle row(s) from continuation text "
            "not fully captured in TABLE blocks."
        )
    if lines and not challan_no:
        found = _find_engine_chassis_table(tables)
        if found:
            grid, hi = found
            challan_no = _challan_no_from_repeated_invoice(grid, hi)
        elif used_table_loose:
            loose = _find_loose_model_details_table(tables)
            if loose is not None:
                grid_l, sr, _, _ = loose
                challan_no = _invoice_from_table_column_zero(grid_l, sr)
    if not lines:
        fb_lines, fallback_inv = _fallback_lines_from_full_text(full_text)
        if fb_lines:
            used_line_fallback = True
            lines, dup_n_fb = dedupe_challan_lines(fb_lines)
            dup_n += dup_n_fb
            if dup_n_fb:
                out["warnings"].append(
                    f"Removed {dup_n_fb} duplicate row(s) with the same engine and chassis numbers."
                )
        if not challan_no and fallback_inv:
            challan_no = fallback_inv

    if not lines:
        out["warnings"].append(
            "No vehicle rows could be extracted (no matching Textract TABLE and no Model Details LINE layout)."
        )
    elif used_table_loose:
        out["warnings"].append(
            "Vehicle rows parsed from Textract TABLE (merged Model Details headers / cells); verify before submitting."
        )
    elif used_line_fallback:
        out["warnings"].append(
            "Vehicle rows were parsed from LINE text because Textract TABLE blocks did not match; verify before submitting."
        )

    if not challan_no:
        if dealer_id is not None and assign_default_challan_no:
            challan_no = generate_default_challan_no(dealer_id)
            out["warnings"].append(
                f"Challan number not detected; assigned default {challan_no}."
            )
        else:
            out["warnings"].append("Challan number not detected.")
    if ocr_date_missing:
        out["warnings"].append(
            "Challan date not detected on scan; using today (IST) for folder name and pre-fill."
        )
        d_ist = _ist_now().date()
        date_raw = f"{d_ist.day:02d}/{d_ist.month:02d}/{d_ist.year}"
        iso, ddmmyyyy = parse_challan_date_to_iso(date_raw)

    if not ddmmyyyy:
        d_fb = _ist_now().date()
        ddmmyyyy = f"{d_fb.day:02d}{d_fb.month:02d}{d_fb.year}"

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
    out["artifact_leaf"] = leaf_name
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


def save_challan_scan_file(
    artifact_dir: str | Path,
    scan_bytes: bytes,
    original_filename: str | None,
) -> Path:
    """Write uploaded scan bytes under an existing challan artifact folder."""
    from app.services.upload_file_validation import sanitize_legacy_upload_filename

    dest_dir = Path(artifact_dir)
    safe_name = sanitize_legacy_upload_filename(original_filename)
    dest_dir.mkdir(parents=True, exist_ok=True)
    scan_path = dest_dir / safe_name
    scan_path.write_bytes(scan_bytes)
    return scan_path
