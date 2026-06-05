"""Monotonic phase timing for Fill DMS / Create Invoice (grep: fill_dms_phase)."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_anchor: float | None = None
_api_anchor: float | None = None


def fill_dms_phase_reset() -> None:
    global _anchor
    _anchor = time.monotonic()


def _truncate_field(value: object, max_len: int = 80) -> str:
    s = str(value).replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def fill_dms_phase(name: str, **fields: object) -> None:
    global _anchor
    if _anchor is None:
        _anchor = time.monotonic()
    ms = int((time.monotonic() - _anchor) * 1000)
    extra = ""
    if fields:
        parts = [f"{k}={_truncate_field(v)}" for k, v in fields.items()]
        extra = " " + " ".join(parts)
    logger.info("fill_dms_phase +%dms %s%s", ms, name, extra)


def fill_dms_api_phase_reset() -> None:
    """Separate anchor for HTTP handler staging (grep: fill_dms_api_phase)."""
    global _api_anchor
    _api_anchor = time.monotonic()


def fill_dms_api_phase(name: str, **fields: object) -> None:
    global _api_anchor
    if _api_anchor is None:
        _api_anchor = time.monotonic()
    ms = int((time.monotonic() - _api_anchor) * 1000)
    extra = ""
    if fields:
        parts = [f"{k}={_truncate_field(v)}" for k, v in fields.items()]
        extra = " " + " ".join(parts)
    logger.info("fill_dms_api_phase +%dms %s%s", ms, name, extra)
