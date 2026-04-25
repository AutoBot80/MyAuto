"""Shared string/fuzzy helpers used by DMS, insurance, and OCR paths (no Playwright)."""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


def normalize_for_fuzzy_match(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def strip_leading_the_for_master_ref(s: str) -> str:
    """
    After :func:`normalize_for_fuzzy_match`, drop a single leading article **the** so
    *The New India …* and *New India …* align for master_ref scoring. If stripping would
    clear the string, return the original.
    """
    t = re.sub(r"\s+", " ", (s or "").lower().strip())
    if not t:
        return t
    if t == "the":
        return t
    if t.startswith("the "):
        rest = t[4:].strip()
        if rest:
            t = re.sub(r"\s+", " ", rest).strip()
    return t


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
    ``prefer_insurer`` is at least ``min_ratio``.

    :func:`build_insurance_fill_values` and MISP KYC use ``INSURER_PREFER_FUZZY_MIN_RATIO`` (default
    0.80) when comparing merged ``master_ref``-aligned details text to ``prefer_insurer``.
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


_NOMINEE_RELATIONSHIP_CANONICALS = (
    "Son",
    "Daughter",
    "Father",
    "Mother",
    "Brother",
    "Sister",
    "Nephew",
    "Niece",
    "Husband",
    "Wife",
    "Uncle",
)


def normalize_nominee_relationship_value(val: str | None) -> str:
    """
    Strip trailing period OCR often attaches to relation labels (e.g. **Mother.** printed next to **Relation**).

    When the text matches a known relation (**Son**, **Daughter**, **Father**, **Mother**, **Brother**, **Sister**,
    **Nephew**, **Niece**, **Husband**, **Wife**, **Uncle**) with fuzzy score ≥ 0.5 (:func:`fuzzy_best_option_label`), return that canonical label; otherwise
    return the cleaned string (e.g. legacy **Father/Mother** slash rows for gender refinement).
    """
    s = " ".join(clean_text(val).split())
    if not s:
        return ""
    s = s.rstrip(".").strip()
    matched = fuzzy_best_option_label(s, list(_NOMINEE_RELATIONSHIP_CANONICALS), min_score=0.5)
    if matched:
        return matched
    return s


def _fuzzy_composite_pair_strength(q: str, c: str) -> float:
    """``q`` and ``c`` must already be :func:`normalize_for_fuzzy_match` output (lowercase, collapsed spaces)."""
    score = difflib.SequenceMatcher(None, q, c).ratio()
    q_words = set(w for w in q.split() if len(w) >= 2)
    c_words = set(w for w in c.split() if len(w) >= 2)
    if q_words and c_words:
        inter = len(q_words & c_words)
        union = len(q_words | c_words) or 1
        score = max(score, (inter / union) * 0.98)
    if len(q) >= 3 and (q in c or c in q):
        score = max(score, 0.92)
    if len(q) >= 3:
        for w in c.split():
            if len(w) >= len(q) and q in w:
                score = max(score, 0.9)
                break
    return score


# Head blend avoids tail-only collisions (e.g. *India Insurance* on different insurers) vs raw argmax.
_MASTER_REF_HEAD_BLEND = 0.55
_FULL_BLEND = 1.0 - _MASTER_REF_HEAD_BLEND
_CLOSE_CANDIDATE_LOG_EPS = 0.05


def fuzzy_best_master_ref_value(
    query: str, candidates: list[str], *, min_score: float = 0.5
) -> str | None:
    """
    Map OCR/sheet text to a canonical ``master_ref.ref_value`` (INSURER or FINANCER):
    same underlying signals as :func:`fuzzy_best_option_label`, but
    (1) ignores a leading *The* for scoring (people add/drop the article);
    (2) blends with the first **3** content words of query vs candidate so the **brand
    prefix** outweighs a shared generic tail (*India Insurance*, *Ltd.*, *Bank*, etc.).

    Returns the **exact** ``ref_value`` string for the best row, or ``None`` if below ``min_score``.
    """
    if not candidates:
        return None
    qn = normalize_for_fuzzy_match(query)
    if not qn:
        out = (candidates[0] or "").strip()
        return out or None
    qs = strip_leading_the_for_master_ref(qn) or qn
    rows: list[tuple[str, float, float, float, int]] = []
    for i, raw in enumerate(candidates):
        c = (raw or "").strip()
        if not c:
            continue
        cn = normalize_for_fuzzy_match(c)
        cs = strip_leading_the_for_master_ref(cn) or cn
        s_full = _fuzzy_composite_pair_strength(qs, cs)
        wq, wc = qs.split(), cs.split()
        head_q = " ".join(wq[:3]) if wq else qs
        head_c = " ".join(wc[:3]) if wc else cs
        s_head = _fuzzy_composite_pair_strength(head_q, head_c) if head_q and head_c else 0.0
        final = _MASTER_REF_HEAD_BLEND * s_head + _FULL_BLEND * s_full
        rows.append((c, final, s_full, s_head, i))
    if not rows:
        return None
    rows.sort(key=lambda t: (-t[1], -t[3], -t[2], t[4]))  # final, s_head, s_full, stable index
    best = rows[0]
    if best[1] < min_score:
        return None
    if len(rows) > 1:
        second = rows[1]
        if (best[1] - second[1]) <= _CLOSE_CANDIDATE_LOG_EPS:
            logger.info(
                "master_ref fuzzy: close scores (within %.2f) — pick=%r score=%.4f head=%.4f full=%.4f; "
                "next=%r score=%.4f",
                _CLOSE_CANDIDATE_LOG_EPS,
                best[0],
                best[1],
                best[3],
                best[2],
                second[0],
                second[1],
            )
    return best[0]


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
    best_label = (candidates[0] or "").strip()
    best_score = 0.0
    for raw in candidates:
        c = (raw or "").strip()
        if not c:
            continue
        cn = normalize_for_fuzzy_match(c)
        score = _fuzzy_composite_pair_strength(q, cn)
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
