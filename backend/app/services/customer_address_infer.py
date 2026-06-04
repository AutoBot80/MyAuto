"""
Infer customer_master / DMS fields from a single free-form address line (Aadhaar OCR, Textract).

- C/O, S/o, W/o, D/o → ``care_of`` as **``C/o Name``** / **``S/o Name``** / **``W/o Name``** / **``D/o Name``**; that clause is stripped from the body. The composed ``address`` is the **remainder only** (locality / district / state / PIN) so it does not repeat the care-of line when ``care_of`` is set separately.
- UIDAI-style suffix: ``DIST: <District>, <State> - <PIN>`` → district/city, state, pin; text after PIN dropped.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.services.utility_functions import normalize_address_dedupe_repetition

# Longest names first for alternation (word-boundary match).
_INDIA_REGIONS: tuple[str, ...] = (
    "Dadra and Nagar Haveli and Daman and Diu",
    "Andaman and Nicobar Islands",
    "Jammu and Kashmir",
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Himachal Pradesh",
    "Madhya Pradesh",
    "Tamil Nadu",
    "Uttar Pradesh",
    "West Bengal",
    "Uttarakhand",
    "Chhattisgarh",
    "Maharashtra",
    "Meghalaya",
    "Nagaland",
    "Puducherry",
    "Lakshadweep",
    "Telangana",
    "Karnataka",
    "Rajasthan",
    "Gujarat",
    "Haryana",
    "Sikkim",
    "Tripura",
    "Manipur",
    "Mizoram",
    "Assam",
    "Bihar",
    "Delhi",
    "Goa",
    "Kerala",
    "Odisha",
    "Punjab",
    "Ladakh",
)

_INDIA_REGION_BY_LOWER: dict[str, str] = {r.lower(): r for r in _INDIA_REGIONS}

# Standard two-letter codes (vehicle / forms) → exact spelling in ``_INDIA_REGIONS``.
_INDIAN_STATE_TWO_LETTER: dict[str, str] = {
    "AP": "Andhra Pradesh",
    "AR": "Arunachal Pradesh",
    "AS": "Assam",
    "BR": "Bihar",
    "CG": "Chhattisgarh",
    "GA": "Goa",
    "GJ": "Gujarat",
    "HR": "Haryana",
    "HP": "Himachal Pradesh",
    "JK": "Jammu and Kashmir",
    "KA": "Karnataka",
    "KL": "Kerala",
    "LD": "Lakshadweep",
    "MP": "Madhya Pradesh",
    "MH": "Maharashtra",
    "MN": "Manipur",
    "ML": "Meghalaya",
    "MZ": "Mizoram",
    "NL": "Nagaland",
    "OD": "Odisha",
    "OR": "Odisha",
    "PB": "Punjab",
    "RJ": "Rajasthan",
    "SK": "Sikkim",
    "TN": "Tamil Nadu",
    "TS": "Telangana",
    "TR": "Tripura",
    "UP": "Uttar Pradesh",
    "UK": "Uttarakhand",
    "WB": "West Bengal",
    "AN": "Andaman and Nicobar Islands",
    "DL": "Delhi",
    "PY": "Puducherry",
    "LA": "Ladakh",
    "DD": "Dadra and Nagar Haveli and Daman and Diu",
}

# Curated OCR / shorthand → canonical (must match ``_INDIA_REGIONS`` spelling).
_STATE_OCR_SYNONYMS: dict[str, str] = {
    "rajashan": "Rajasthan",
    "orissa": "Odisha",
}

_FUZZY_STATE_MIN_RATIO = 0.86

_REGION_ALT = "|".join(re.escape(r) for r in _INDIA_REGIONS)
_REGION_RE = re.compile(rf"(?i)\b({_REGION_ALT})\b")


def _canonical_region_spelling(name: str) -> str:
    """Return canonical spelling from ``_INDIA_REGIONS`` when case-insensitive match."""
    t = (name or "").strip()
    if not t:
        return t
    got = _INDIA_REGION_BY_LOWER.get(t.lower())
    return got if got else t


def resolve_indian_state_name(
    ocr_token: str | None,
    *,
    allow_la_ladakh: bool = False,
) -> str | None:
    """
    Map OCR / shorthand / two-letter codes to a canonical state or UT name in ``_INDIA_REGIONS``.

    ``allow_la_ladakh``: when False, ``LA`` is not resolved via the two-letter table (avoids
    false positives); fuzzy may still match ``Ladakh`` on longer tokens.
    """
    if not ocr_token or not str(ocr_token).strip():
        return None
    s = re.sub(r"\s+", " ", str(ocr_token).strip())
    s = re.sub(r"(?:\s*[-–—])+\s*$", "", s).strip()
    s = s.rstrip(".,;:")
    if not s:
        return None

    syn = _STATE_OCR_SYNONYMS.get(s.lower())
    if syn:
        return syn

    canon = _INDIA_REGION_BY_LOWER.get(s.lower())
    if canon:
        return canon

    letters_only = re.sub(r"[^A-Za-z]", "", s)
    if len(letters_only) == 2:
        code = letters_only.upper()
        if code == "LA" and not allow_la_ladakh:
            pass
        else:
            hit = _INDIAN_STATE_TWO_LETTER.get(code)
            if hit:
                return hit

    if re.match(r"(?i)raj\.?$", s):
        return "Rajasthan"

    best: str | None = None
    best_score = 0.0
    low = s.lower()
    for region in _INDIA_REGIONS:
        rlow = region.lower()
        score = SequenceMatcher(None, low, rlow).ratio()
        if score > best_score:
            best_score = score
            best = region
    if best and best_score >= _FUZZY_STATE_MIN_RATIO:
        return best
    return None


def strip_junk_between_last_indian_state_and_pin(text: str) -> str:
    """
    UIDAI English backs often read as ``..., State, <OCR noise>, PIN`` at the tail.
    Keep text through the **last** known Indian state/UT token and the **final** 6-digit PIN,
    dropping the span in between when the PIN ends the line (only trailing separators after PIN).

    Does nothing when no PIN, no state before the last PIN, or there is substantive text after the PIN.
    """
    s = (text or "").strip()
    if not s:
        return s
    pin_iter = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", s))
    if not pin_iter:
        return s
    last_pin_m = pin_iter[-1]
    rest = s[last_pin_m.end() :].strip()
    if rest and not re.fullmatch(r"[\s,.;:\-–—]+", rest):
        return s
    pin = last_pin_m.group(1)
    head = s[: last_pin_m.start()]
    matches = list(_REGION_RE.finditer(head))
    if matches:
        st_m = matches[-1]
        canon = _canonical_region_spelling(st_m.group(1))
        lead = head[: st_m.start()].rstrip(" ,.;:-–—")
        prefix = f"{lead}, {canon}" if lead else canon
    else:
        comma_parts = [p.strip() for p in head.split(",") if p.strip()]
        prefix = None
        for i in range(len(comma_parts) - 1, -1, -1):
            seg = comma_parts[i]
            state_raw = re.sub(r"(?:\s*[-–—])+\s*$", "", seg).strip()
            got = resolve_indian_state_name(state_raw, allow_la_ladakh=True)
            if not got:
                continue
            idx = head.rfind(comma_parts[i])
            if idx < 0:
                prefix = ", ".join(comma_parts[: i + 1]).rstrip(" ,.;:-–—")
            else:
                head_before = head[:idx].rstrip(" ,.;:-–—")
                prefix = f"{head_before}, {got}" if head_before else got
            break
        if prefix is None:
            return s
    out = f"{prefix}, {pin}"
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r",\s*,+", ", ", out)
    return out.strip(" ,")


def _indian_state_field_trustworthy(state_val: str) -> bool:
    """Reject long OCR/Hindi noise in ``state`` when a real UT/state name was expected."""
    s = (state_val or "").strip()
    if not s or len(s) > 48:
        return False
    m = _REGION_RE.search(s)
    if m:
        start, end = m.span()
        if start > 4:
            return False
        after = s[end:].strip()
        if len(after) > 2:
            return False
        return True
    r = resolve_indian_state_name(s, allow_la_ladakh=False)
    if r and len(s) <= 3:
        return True
    return False


def _squish_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# Relation markers on UIDAI English back (same care_of field as C/O for DMS).
# Colon is optional because OCR/address input may be like "S/O Madan Lal, ...".
_CARE_OF_MARKERS_RE = re.compile(
    r"(?i)\b(C\.?\s*/?\s*O\.?|S\.?\s*/?\s*O\.?|W\.?\s*/?\s*O\.?|D\.?\s*/?\s*O\.?)\s*:?\s*([^,\n]+)"
)


def _canonical_care_of_prefix(marker_raw: str) -> str:
    """Normalize OCR marker to ``C/O``, ``S/O``, ``W/O``, or ``D/O``."""
    for ch in (marker_raw or "").upper():
        if ch in ("C", "S", "W", "D"):
            return {"C": "C/O", "S": "S/O", "W": "W/O", "D": "D/O"}[ch]
    return "C/O"


def _relation_segment_key(segment: str) -> str | None:
    """Stable key for C/O · S/O · W/O · D/O + name (dedupe repeated relation clauses)."""
    s = _normalize_typographic_slashes(segment.strip())
    if not s:
        return None
    m = re.match(
        r"(?i)^\s*(C\.?\s*/?\s*O\.?|S\.?\s*/?\s*O\.?|W\.?\s*/?\s*O\.?|D\.?\s*/?\s*O\.?)\s*:?\s*(.+)$",
        s,
    )
    if not m:
        return None
    letter = _canonical_care_of_prefix(m.group(1))[0]
    name = re.sub(r"\s+", " ", m.group(2).strip().lower())
    if not name:
        return None
    return f"{letter}:{name}"


def _normalize_typographic_slashes(s: str) -> str:
    """Map common OCR Unicode slashes to ASCII so relation regexes match."""
    if not s:
        return s
    return (
        str(s)
        .replace("／", "/")
        .replace("∕", "/")
        .replace("⁄", "/")
    )


def _strip_leading_care_of_duplicate_from_work(work: str, co_full: str) -> str:
    """
    Remove a leading relation clause that is the same care-of as ``co_full`` (ignores S/o vs S/O,
    spacing). Complements :func:`_strip_care_of_clause` when regex strip misses a variant.
    """
    if not co_full or not work:
        return work or ""
    w = _squish_spaces(_normalize_typographic_slashes(work))
    co = _squish_spaces(_normalize_typographic_slashes(co_full))
    for _ in range(4):
        prev = w
        if "," in w:
            first, rest = w.split(",", 1)
            first, rest = first.strip(), rest.strip()
        else:
            first, rest = w, ""
        k1 = _relation_segment_key(first)
        k2 = _relation_segment_key(co)
        if k1 and k2 and k1 == k2:
            w = _squish_spaces(rest)
        else:
            m = re.match(re.escape(co) + r"\s*,\s*", w, re.I)
            if m:
                w = _squish_spaces(w[m.end() :])
            elif w.lower() == co.lower():
                w = ""
            else:
                break
        if w == prev:
            break
    return w


def _strip_gender_bleed_segments(s: str) -> str:
    """Remove UIDAI-style ``gen/ MALE`` / ``gen/ FEMALE`` fragments merged into the English line."""
    t = re.sub(r"(?i)\s*,\s*gen\s*/\s*(male|female)\b\s*", ", ", s)
    t = re.sub(r"(?i)^gen\s*/\s*(male|female)\b\s*,?\s*", "", t)
    t = re.sub(r"\s+,+", ", ", t)
    t = re.sub(r",\s*,+", ", ", t)
    return t.strip(" ,")


def _dedupe_comma_relation_clauses(s: str) -> str:
    """Drop later comma segments that repeat the same S/O · W/O · D/O · C/O + name as an earlier one."""
    if "," not in s:
        return s
    parts = [p.strip() for p in s.split(",") if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        rk = _relation_segment_key(p)
        if rk:
            if rk in seen:
                continue
            seen.add(rk)
        out.append(p)
    return ", ".join(out)


def _extract_care_of_from_text(text: str) -> str | None:
    """C/O, S/O, W/O, D/O → ``S/O Name`` style (relation kept) up to first comma or newline."""
    if not text:
        return None
    t = _normalize_typographic_slashes(text)
    m = _CARE_OF_MARKERS_RE.search(t)
    if not m:
        return None
    prefix = _canonical_care_of_prefix(m.group(1))
    name = m.group(2).strip()
    if not name:
        return None
    return _squish_spaces(f"{prefix} {name}")


def _strip_care_of_clause(text: str) -> str:
    """Remove ``C/O...`` / ``S/O...`` segments from the free-form line (care_of stored separately)."""
    if not text:
        return text
    t = _normalize_typographic_slashes(text)
    return _squish_spaces(
        re.sub(
            r"(?i)\b(?:C\.?\s*/?\s*O\.?|S\.?\s*/?\s*O\.?|W\.?\s*/?\s*O\.?|D\.?\s*/?\s*O\.?)\s*:?\s*[^,\n]+,?\s*",
            " ",
            t,
        )
    )


def _truncate_after_last_pin(text: str) -> str:
    """Ignore everything after the last 6-digit PIN (Aadhaar-style line ending)."""
    if not text:
        return text
    found = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", text))
    if not found:
        return text.strip()
    return text[: found[-1].end()].strip()


def _parse_dist_state_pin_line(text: str) -> tuple[str | None, str | None, str | None]:
    """
    ``DIST: Bharatpur, Rajasthan - 321001`` → (district, state_name, pin).
    Handles OCR like ``Rajasthan - - 321001`` (double dash) and ``Rajasthan -- 321001``.
    State segment is matched/validated against known region names when possible.
    """
    if not text:
        return None, None, None
    # One or more dash segments before the 6-digit PIN (UIDAI / Textract noise).
    m = re.search(
        r"(?i)\bDIST\s*:\s*([^,]+?)\s*,\s*(.+?)\s*(?:[-–—]\s*)+(\d{6})\b",
        text,
    )
    if not m:
        return None, None, None
    district = m.group(1).strip()
    state_seg = re.sub(r"[-–—\s]+$", "", m.group(2).strip()).strip()
    pin = m.group(3).strip()
    rm = _REGION_RE.search(state_seg)
    if rm:
        state_name = _canonical_region_spelling(rm.group(1))
    else:
        resolved = resolve_indian_state_name(state_seg, allow_la_ladakh=True)
        state_name = resolved if resolved else _squish_spaces(state_seg).title()
    if len(district) < 2 or len(district) > 80:
        district = None
    return district, state_name, pin


_TRAILING_STATE_PIN_RE = re.compile(
    rf"(?i)\b({_REGION_ALT})\s*(?:[-–—]\s*)+(\d{{6}})\b",
)


def _remove_stray_pin_before_state(text: str) -> str:
    """
    Remove OCR noise where an extra 6-digit PIN appears immediately before a state token,
    e.g. ``..., 302001 Rajasthan - 321001`` -> ``..., Rajasthan - 321001``.
    """
    if not text:
        return text
    cleaned = re.sub(
        rf"(?i)(?<!\d)\d{{6}}(?!\d)\s*(?:[,;:.\-–—]\s*)?(?=\b(?:{_REGION_ALT})\b)",
        "",
        text,
    )
    return _squish_spaces(cleaned)


def _parse_state_pin_comma_dash_heuristic(text: str) -> tuple[str | None, str | None]:
    """
    Infer **state** and **PIN** using comma-separated clauses and a final
    ``<State> <dash-run(s)> <PIN>`` tail (UIDAI English back).

    1. Flatten newlines to spaces (Textract line breaks).
    2. Find the **last** 6-digit PIN (word-bounded).
    3. Take the substring **before** that PIN; the segment after the **last comma**
       is the state+dash run (e.g. ``Rajasthan - -``).
    4. Strip trailing dash runs (one or more ``-`` / en-dash / em-dash); the remainder
       should match a known Indian state/UT name.
    """
    if not (text or "").strip():
        return None, None
    flat = _squish_spaces(text.replace("\n", " ").replace("\r", " "))
    pin_matches = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", flat))
    if not pin_matches:
        return None, None
    pin_m = pin_matches[-1]
    pin = pin_m.group(1)
    before_pin = flat[: pin_m.start()].rstrip()
    if not before_pin:
        return None, pin
    # Comma-separated clauses (PO:, DIST:, …); the last clause holds state + dash run(s) before PIN.
    comma_parts = [p.strip() for p in before_pin.split(",") if p.strip()]
    tail = comma_parts[-1] if comma_parts else before_pin
    # Strip one or more trailing dash runs (``- -``, ``--``, en/em dash) before the PIN.
    state_raw = re.sub(r"(?:\s*[-–—])+\s*$", "", tail).strip()
    rm = _REGION_RE.search(state_raw)
    if rm:
        return _canonical_region_spelling(rm.group(1)), pin
    rm2 = _REGION_RE.search(tail)
    if rm2:
        return _canonical_region_spelling(rm2.group(1)), pin
    resolved = resolve_indian_state_name(state_raw, allow_la_ladakh=True)
    if resolved:
        return resolved, pin
    return None, pin


def _parse_trailing_state_pin_freeform(text: str) -> tuple[str | None, str | None]:
    """
    When ``DIST:`` is missing or mangled in OCR, UIDAI backs often end with
    ``<State> - <PIN>`` or ``<State> - - <PIN>``. Use the last match so a state
    name earlier in the line does not win over the true trailing state/PIN.
    """
    if not text:
        return None, None
    matches = list(_TRAILING_STATE_PIN_RE.finditer(text))
    if matches:
        m = matches[-1]
        return _canonical_region_spelling(m.group(1)), m.group(2).strip()
    pin_matches = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", text))
    if not pin_matches:
        return None, None
    pin = pin_matches[-1].group(1)
    before = text[: pin_matches[-1].start()].rstrip()
    comma_parts = [p.strip() for p in before.split(",") if p.strip()]
    if not comma_parts:
        st = resolve_indian_state_name(before, allow_la_ladakh=True)
        return (st, pin) if st else (None, pin)
    tail = comma_parts[-1]
    state_raw = re.sub(r"(?:\s*[-–—])+\s*$", "", tail).strip()
    st = resolve_indian_state_name(state_raw, allow_la_ladakh=True)
    return (st, pin) if st else (None, pin)


def _extract_pin_from_text(text: str) -> str | None:
    if not text:
        return None
    found = re.findall(r"(?<!\d)(\d{6})(?!\d)", text)
    return found[-1] if found else None


def _extract_state_from_text(text: str) -> str | None:
    if not text:
        return None
    m = _REGION_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().title()


def _extract_state_last_from_text(text: str) -> str | None:
    """Prefer the last known state token (often immediately before PIN on Aadhaar back)."""
    if not text:
        return None
    matches = list(_REGION_RE.finditer(text))
    if matches:
        return _canonical_region_spelling(matches[-1].group(1))
    flat = _squish_spaces(text.replace("\n", " ").replace("\r", " "))
    pin_matches = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", flat))
    if pin_matches:
        before = flat[: pin_matches[-1].start()].rstrip()
        comma_parts = [p.strip() for p in before.split(",") if p.strip()]
        for seg in reversed(comma_parts):
            state_raw = re.sub(r"(?:\s*[-–—])+\s*$", "", seg).strip()
            got = resolve_indian_state_name(state_raw, allow_la_ladakh=True)
            if got:
                return got
        got2 = resolve_indian_state_name(before, allow_la_ladakh=True)
        if got2:
            return got2
    return None


def _extract_city_from_text(text: str, state: str | None) -> str | None:
    if not text:
        return None
    for pat in (
        r"(?i)\bDIST\s*:\s*([^,\n]+)",
        r"(?i)\bDistrict\s*:\s*([^,\n]+)",
        r"(?i)\bPO\s*:\s*([^,\n]+)",
    ):
        m = re.search(pat, text)
        if m:
            c = m.group(1).strip()
            if len(c) >= 2 and len(c) <= 60:
                return c
    if state:
        parts = re.split(re.escape(state), text, maxsplit=1, flags=re.IGNORECASE)
        before = parts[0] if parts else ""
        segs = [p.strip() for p in before.split(",") if p.strip()]
        noise = re.compile(r"(?i)^(near|c/o|s/o|w/o|d/o|address|पता)\s*:?\s*")
        for cand in reversed(segs):
            c = noise.sub("", cand).strip()
            if 3 <= len(c) <= 55 and not re.search(r"\d{6}", c) and not _REGION_RE.search(c):
                words = re.findall(r"[A-Za-z][A-Za-z'.-]*", c)
                if len(words) >= 2 and words[-1].lower() == words[-2].lower():
                    return words[-1]
                if len(words) >= 4:
                    return words[-1]
                return c
        # No-comma OCR variant: derive city from token(s) immediately before state.
        flat = _squish_spaces(text.replace("\n", " ").replace("\r", " "))
        state_matches = list(re.finditer(rf"(?i)\b{re.escape(state)}\b", flat))
        if state_matches:
            before_state = flat[: state_matches[-1].start()].strip(" ,;:-")
            if before_state:
                # Drop PINs and punctuation noise before token extraction.
                cleaned = re.sub(r"(?<!\d)\d{6}(?!\d)", " ", before_state)
                words = re.findall(r"[A-Za-z][A-Za-z'.-]*", cleaned)
                if words:
                    relation_noise = {"c", "s", "w", "d", "o", "co", "so", "wo", "do", "near", "address"}
                    city = words[-1]
                    if city.lower() in relation_noise and len(words) >= 2:
                        city = words[-2]
                    # Handle repeated locality token before state: "Bharatpur Bharatpur Rajasthan".
                    if len(words) >= 2 and words[-1].lower() == words[-2].lower():
                        city = words[-1]
                    city = city.strip(" .,'-")
                    if 3 <= len(city) <= 55 and not _REGION_RE.search(city):
                        return city
    return None


def normalize_address_freeform(address_line: str) -> dict[str, str]:
    """
    Parse one address string: ``care_of`` (``C/o``/``S/o``/``W/o``/``D/o`` + name), ``DIST:`` …,
    strip relation clause from the body, truncate after PIN. ``address`` is the **remainder only**
    (no leading care-of repeat when ``care_of`` is populated).
    """
    out: dict[str, str] = {}
    if not address_line or not str(address_line).strip():
        return out
    text = normalize_address_dedupe_repetition(str(address_line).strip())
    text = _normalize_typographic_slashes(text)
    text = _strip_gender_bleed_segments(text)
    text = _dedupe_comma_relation_clauses(text)

    co = _extract_care_of_from_text(text)
    if co:
        out["care_of"] = co

    dist, st_d, pin_d = _parse_dist_state_pin_line(text)
    if dist:
        out["city"] = dist
        out["district"] = dist
    if st_d:
        out["state"] = st_d
    if pin_d:
        out["pin"] = pin_d
        out["pin_code"] = pin_d

    have_pin = bool(pin_d)
    have_state = bool(st_d)

    if not have_pin or not have_state:
        st_cd, pin_cd = _parse_state_pin_comma_dash_heuristic(text)
        if pin_cd and not have_pin:
            out["pin"] = pin_cd
            out["pin_code"] = pin_cd
            have_pin = True
        if st_cd and not have_state:
            out["state"] = st_cd
            have_state = True

    if not have_pin or not have_state:
        st_tr, pin_tr = _parse_trailing_state_pin_freeform(text)
        if pin_tr and not have_pin:
            out["pin"] = pin_tr
            out["pin_code"] = pin_tr
            have_pin = True
        if st_tr and not have_state:
            out["state"] = st_tr
            have_state = True

    work = _strip_care_of_clause(text)
    work = _remove_stray_pin_before_state(work)
    work = _truncate_after_last_pin(work)
    work = _squish_spaces(work)
    co_full = (out.get("care_of") or "").strip()
    if co_full:
        work = _strip_leading_care_of_duplicate_from_work(work, co_full)
    if work:
        out["address"] = work
    elif co_full:
        out["address"] = co_full

    return out


def enrich_customer_address_from_freeform(customer: dict[str, Any]) -> dict[str, Any]:
    """
    Copy customer dict; parse ``address`` for C/O, DIST/state/PIN, truncate after PIN; fill blanks.
    Sets both ``pin`` and ``pin_code`` when inferring PIN.
    """
    out = dict(customer)
    pin_digits = "".join(
        c for c in (str(out.get("pin") or out.get("pin_code") or "")) if c.isdigit()
    )
    if len(pin_digits) >= 6:
        out["pin"] = pin_digits[:6]
        out["pin_code"] = pin_digits[:6]

    addr = (out.get("address") or "").strip()
    if not addr:
        return out

    norm = normalize_address_freeform(addr)

    if norm.get("address"):
        out["address"] = norm["address"]

    pin_existing = (out.get("pin") or out.get("pin_code") or "").strip()
    city_existing = (out.get("city") or "").strip()
    state_existing = (out.get("state") or "").strip()
    care_existing = (out.get("care_of") or "").strip()

    if norm.get("care_of") and not care_existing:
        out["care_of"] = norm["care_of"]

    if norm.get("pin") and not pin_existing:
        out["pin"] = norm["pin"]
        out["pin_code"] = norm["pin_code"] or norm["pin"]

    if norm.get("state"):
        if not state_existing or not _indian_state_field_trustworthy(state_existing):
            out["state"] = norm["state"]

    if norm.get("city") and not city_existing:
        out["city"] = norm["city"]

    if norm.get("district") and not (out.get("district") or "").strip():
        out["district"] = norm["district"]

    # Fallbacks when DIST line was not present
    addr2 = (out.get("address") or "").strip()
    pin_fb = _extract_pin_from_text(addr2)
    if pin_fb and not (out.get("pin") or out.get("pin_code") or "").strip():
        out["pin"] = pin_fb
        out["pin_code"] = pin_fb

    state_cur = (out.get("state") or "").strip()
    state_fb = _extract_state_last_from_text(addr2)
    if state_fb and (not state_cur or not _indian_state_field_trustworthy(state_cur)):
        out["state"] = state_fb

    city_fb = _extract_city_from_text(addr2, (out.get("state") or "").strip() or None)
    if city_fb and not (out.get("city") or "").strip():
        out["city"] = city_fb

    state_cur = (out.get("state") or "").strip()
    if state_cur and not _indian_state_field_trustworthy(state_cur):
        fixed = resolve_indian_state_name(state_cur, allow_la_ladakh=False)
        if fixed:
            out["state"] = fixed

    return out
