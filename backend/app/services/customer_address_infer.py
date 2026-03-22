"""
Infer customer_master / DMS fields from a single free-form address line (Aadhaar OCR, Textract).

- C/O / C/o → ``care_of`` (Care of); removed from the stored address line.
- UIDAI-style suffix: ``DIST: <District>, <State> - <PIN>`` → district/city, state, pin; text after PIN dropped.
"""

from __future__ import annotations

import re
from typing import Any

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

_REGION_ALT = "|".join(re.escape(r) for r in _INDIA_REGIONS)
_REGION_RE = re.compile(rf"(?i)\b({_REGION_ALT})\b")


def _indian_state_field_trustworthy(state_val: str) -> bool:
    """Reject long OCR/Hindi noise in ``state`` when a real UT/state name was expected."""
    s = (state_val or "").strip()
    if not s or len(s) > 48:
        return False
    m = _REGION_RE.search(s)
    if not m:
        return False
    start, end = m.span()
    if start > 4:
        return False
    after = s[end:].strip()
    if len(after) > 2:
        return False
    return True


def _squish_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# Relation markers on UIDAI English back (same care_of field as C/O for DMS).
_CARE_OF_MARKERS_RE = re.compile(
    r"(?i)\b(?:C\.?\s*/?\s*O\.?|S\.?\s*/?\s*O\.?|W\.?\s*/?\s*O\.?|D\.?\s*/?\s*O\.?)\s*:\s*([^,\n]+)"
)


def _extract_care_of_from_text(text: str) -> str | None:
    """C/O, S/O, W/O, D/O → care-of name (to first comma or end of clause)."""
    if not text:
        return None
    m = _CARE_OF_MARKERS_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip()
    return name if len(name) >= 1 else None


def _strip_care_of_clause(text: str) -> str:
    """Remove ``C/O: …`` / ``S/O: …`` segments from the free-form line (care_of stored separately)."""
    if not text:
        return text
    return _squish_spaces(
        re.sub(
            r"(?i)\b(?:C\.?\s*/?\s*O\.?|S\.?\s*/?\s*O\.?|W\.?\s*/?\s*O\.?|D\.?\s*/?\s*O\.?)\s*:\s*[^,\n]+,?\s*",
            " ",
            text,
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
    state_name = rm.group(1).strip().title() if rm else _squish_spaces(state_seg).title()
    if len(district) < 2 or len(district) > 80:
        district = None
    return district, state_name, pin


_TRAILING_STATE_PIN_RE = re.compile(
    rf"(?i)\b({_REGION_ALT})\s*(?:[-–—]\s*)+(\d{{6}})\b",
)


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
        return rm.group(1).strip().title(), pin
    rm2 = _REGION_RE.search(tail)
    if rm2:
        return rm2.group(1).strip().title(), pin
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
    if not matches:
        return None, None
    m = matches[-1]
    return m.group(1).strip().title(), m.group(2).strip()


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
    if not matches:
        return None
    return matches[-1].group(1).strip().title()


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
                return c
    return None


def normalize_address_freeform(address_line: str) -> dict[str, str]:
    """
    Parse one address string: ``care_of``, ``DIST: district, state - PIN``, strip C/O from body,
    truncate after PIN. Returned ``address`` is the cleaned line suitable for Address Line 1.
    """
    out: dict[str, str] = {}
    if not address_line or not str(address_line).strip():
        return out
    text = str(address_line).strip()

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
    work = _truncate_after_last_pin(work)
    work = _squish_spaces(work)
    if work:
        out["address"] = work

    return out


def enrich_customer_address_from_freeform(customer: dict[str, Any]) -> dict[str, Any]:
    """
    Copy customer dict; parse ``address`` for C/O, DIST/state/PIN, truncate after PIN; fill blanks.
    Sets both ``pin`` and ``pin_code`` when inferring PIN.
    """
    out = dict(customer)
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

    return out
