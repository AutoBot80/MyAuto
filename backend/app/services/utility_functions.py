"""Shared string/fuzzy helpers used by DMS, insurance, and OCR paths (no Playwright)."""
from __future__ import annotations

import difflib
import re


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
