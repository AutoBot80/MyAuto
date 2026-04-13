"""Enforce maximum length on all string values in nested JSON-like structures."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.config import MAX_TEXT_CHARS

# Do not apply the short text limit to absolute portal URLs (often >300 chars).
_SKIP_TEXT_KEYS = frozenset(
    {
        "dms_base_url",
        "insurance_base_url",
        "vahan_base_url",
        "launch_url",
        "url",
    }
)


def _skip_value_for_key(key: str) -> bool:
    k = key.lower()
    if k in _SKIP_TEXT_KEYS:
        return True
    return k.endswith("_url")


def enforce_max_text_depth(
    obj: Any,
    max_len: int | None = None,
    *,
    path: str = "$",
) -> None:
    """
    Raise ``HTTPException(400)`` if any string value exceeds ``max_len`` (default: ``MAX_TEXT_CHARS``).

    Skips ``None``, numbers, and bools. Recurses into dicts and lists.
    Skips values for keys named ``*_url`` or known portal URL fields (Siebel URLs exceed 300 chars).
    """
    ml = max_len if max_len is not None else MAX_TEXT_CHARS
    if obj is None or isinstance(obj, (bool, int, float)):
        return
    if isinstance(obj, str):
        if len(obj) > ml:
            raise HTTPException(
                status_code=400,
                detail=f"Text must be at most {ml} characters (field {path}).",
            )
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if _skip_value_for_key(key):
                continue
            enforce_max_text_depth(v, ml, path=f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            enforce_max_text_depth(v, ml, path=f"{path}[{i}]")
        return
    # sets, etc. — stringify not traversed
    return
