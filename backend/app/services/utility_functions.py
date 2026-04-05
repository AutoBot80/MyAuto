"""Shared string/fuzzy helpers used by DMS, insurance, and OCR paths (no Playwright)."""
from __future__ import annotations

import difflib
import re
import unicodedata


def normalize_for_fuzzy_match(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def normalize_dob_for_misp(dd_raw: str) -> str:
    """
    Normalize ``customer_master`` / view / staging date-of-birth strings to **dd/mm/yyyy** for MISP ``txtDOB``.
    Accepts ISO ``yyyy-mm-dd`` (optional time / timezone suffix), ``dd/mm/yyyy``, ``dd-mm-yyyy``, ``dd.mm.yyyy``
    with 1- or 2-digit day/month; passes through already-normalized slash forms when unambiguous.
    """
    v = (dd_raw or "").strip()
    if not v:
        return ""
    if "T" in v:
        v = v.split("T", 1)[0].strip()
    else:
        v = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", v)
        v = re.sub(r"\s+[+-]\d{2}:?\d{2}.*$", "", v)
    v = v.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", v)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{d:02d}/{mo:02d}/{y}"
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", v)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{d:02d}/{mo:02d}/{y}"
    return v


def insurer_prefer_matches(
    details_insurer: str,
    prefer_insurer: str,
    *,
    min_ratio: float = 0.20,
) -> bool:
    """
    True when normalized ``SequenceMatcher`` ratio between details-sheet insurer and dealer
    ``prefer_insurer`` is at least ``min_ratio`` (default 0.20).
    """
    a = normalize_for_fuzzy_match(details_insurer)
    b = normalize_for_fuzzy_match(prefer_insurer)
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= min_ratio


def sanitize_details_sheet_insurer_value(val: str | None) -> str | None:
    """
    Reject OCR bleed when **Insurer Name (if needed)** is blank but the next line is printed
    consent/SMS boilerplate (e.g. "I agree to receiving periodic SMS updates about registration
    and service status"). Returns ``None`` so the field is treated as blank and
    ``dealer_ref.prefer_insurer`` can apply in ``build_insurance_fill_values``.
    """
    if not val or not str(val).strip():
        return None
    s = " ".join(str(val).split())
    s = unicodedata.normalize("NFKC", s)
    s = s.rstrip(".。．").strip()
    low = s.lower()
    if re.search(r"(?i)i\s+agree\s+to\s+receiving", low):
        return None
    if re.search(r"(?i)periodic\s+sms", low):
        return None
    if re.search(r"(?i)registration\s+and\s+service", low):
        return None
    if re.search(r"(?i)updates\s+about\s+registration", low):
        return None
    if "i agree" in low and ("sms" in low or "periodic" in low):
        return None
    # Whole consent sentence variants (title case, trailing punctuation, odd spaces)
    if "i agree" in low and "registration" in low and ("service" in low or "status" in low):
        return None
    if len(s.split()) > 14:
        return None
    if len(s) > 120:
        return None
    return s


def normalize_nominee_relationship_value(val: str | None) -> str:
    """
    Strip trailing period OCR often attaches to relation labels (e.g. **Mother.** printed next to **Relation**).
    """
    s = " ".join(clean_text(val).split())
    if not s:
        return ""
    return s.rstrip(".").strip()


def fuzzy_best_option_label(query: str, candidates: list[str], *, min_score: float = 0.42) -> str | None:
    """
    Pick dropdown option label best matching query (insurer from details sheet / OEM name).
    Uses SequenceMatcher + Jaccard on word tokens + substring boost.
    """
    if not candidates:
        return None
    q = normalize_for_fuzzy_match(query)
    if not q:
        return candidates[0].strip() or candidates[0]
    q_words = set(w for w in q.split() if len(w) >= 2)
    best_label = (candidates[0] or "").strip()
    best_score = 0.0
    for raw in candidates:
        c = (raw or "").strip()
        if not c:
            continue
        cn = normalize_for_fuzzy_match(c)
        score = difflib.SequenceMatcher(None, q, cn).ratio()
        c_words = set(w for w in cn.split() if len(w) >= 2)
        if q_words and c_words:
            inter = len(q_words & c_words)
            union = len(q_words | c_words) or 1
            score = max(score, (inter / union) * 0.98)
        if len(q) >= 3 and (q in cn or cn in q):
            score = max(score, 0.92)
        if len(q) >= 3:
            for w in cn.split():
                if len(w) >= len(q) and q in w:
                    score = max(score, 0.9)
                    break
        if score > best_score:
            best_score = score
            best_label = c
    if best_score < min_score:
        # Do not fall back to the first option — wrong insurer/OEM is worse than no selection.
        return None
    return best_label


def clean_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_address_dedupe_repetition(raw: str | None) -> str:
    """
    Remove common OCR/merge duplicates in a single address line, e.g.
    ``S/O S/O Brij Gopal`` → ``S/O Brij Gopal``. Collapses consecutive identical
    **C/O**, **S/O**, **W/O**, **D/O** markers (case- and punctuation-insensitive).
    """
    t = clean_text(raw)
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t).strip()
    # Same relation marker twice in a row (slash and dots optional, as on Aadhaar / forms).
    marker = r"(?:C\.?\s*/\s*O\.?|S\.?\s*/\s*O\.?|W\.?\s*/\s*O\.?|D\.?\s*/\s*O\.?)"
    pat = re.compile(rf"(?i)\b({marker})\s+\1\b")
    for _ in range(16):
        n = pat.sub(r"\1", t)
        if n == t:
            break
        t = n
    return t


# When Details-sheet profession is blank or sanitized away (e.g. marital-status bleed), MISP/DMS use this.
DEFAULT_SALES_DETAIL_PROFESSION = "Employed"


def default_profession_if_empty(val: str | None) -> str:
    """Return trimmed ``val``, or ``DEFAULT_SALES_DETAIL_PROFESSION`` when empty."""
    t = clean_text(val)
    return t if t else DEFAULT_SALES_DETAIL_PROFESSION


def safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name (one segment) for ocr_output and uploads."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def require_customer_vehicle_ids(
    customer_id: int | None, vehicle_id: int | None, view_name: str
) -> tuple[int, int]:
    if customer_id is None or vehicle_id is None:
        raise ValueError(
            f"customer_id and vehicle_id are required because automation now reads from {view_name} only"
        )
    return customer_id, vehicle_id
