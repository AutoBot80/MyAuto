"""Shared string/fuzzy helpers used by DMS, insurance, and OCR paths (no Playwright)."""
from __future__ import annotations

import difflib
import re


def normalize_for_fuzzy_match(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


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
