"""Sample / UI placeholder Indian mobiles — must not drive uploads or ocr_output folder names."""

from __future__ import annotations

PLACEHOLDER_INDIAN_MOBILES: frozenset[str] = frozenset({
    "9876543210",
    "9876501234",
})


def is_placeholder_indian_mobile(ten: str) -> bool:
    """True when ``ten`` is exactly 10 digits and a known placeholder."""
    return bool(ten) and ten in PLACEHOLDER_INDIAN_MOBILES
