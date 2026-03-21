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


def _squish_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_care_of_from_text(text: str) -> str | None:
    """C/O, C/o, C.O. → care-of name (to first comma or end of clause)."""
    if not text:
        return None
    m = re.search(r"(?i)\bC\.?\s*/?\s*O\.?\s*:\s*([^,\n]+)", text)
    if not m:
        return None
    name = m.group(1).strip()
    return name if len(name) >= 1 else None


def _strip_care_of_clause(text: str) -> str:
    """Remove ``C/O: …`` segment from the free-form line (care_of stored separately)."""
    if not text:
        return text
    return _squish_spaces(re.sub(r"(?i)\bC\.?\s*/?\s*O\.?\s*:\s*[^,\n]+,?\s*", " ", text))


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
    State segment is matched/validated against known region names when possible.
    """
    if not text:
        return None, None, None
    m = re.search(
        r"(?i)\bDIST\s*:\s*([^,]+?)\s*,\s*(.+?)\s*-\s*(\d{6})\b",
        text,
    )
    if not m:
        return None, None, None
    district = m.group(1).strip()
    state_seg = m.group(2).strip()
    pin = m.group(3).strip()
    rm = _REGION_RE.search(state_seg)
    state_name = rm.group(1).strip().title() if rm else _squish_spaces(state_seg).title()
    if len(district) < 2 or len(district) > 80:
        district = None
    return district, state_name, pin


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

    if norm.get("state") and not state_existing:
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

    state_fb = _extract_state_from_text(addr2)
    if state_fb and not (out.get("state") or "").strip():
        out["state"] = state_fb

    city_fb = _extract_city_from_text(addr2, (out.get("state") or "").strip() or None)
    if city_fb and not (out.get("city") or "").strip():
        out["city"] = city_fb

    return out
