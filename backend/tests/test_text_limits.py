"""MAX_TEXT_CHARS enforcement on nested payloads."""

import pytest
from fastapi import HTTPException

from app.validation.text_limits import enforce_max_text_depth


def test_enforce_rejects_long_string() -> None:
    with pytest.raises(HTTPException) as ei:
        enforce_max_text_depth({"name": "x" * 301}, max_len=300)
    assert ei.value.status_code == 400


def test_enforce_skips_url_keys() -> None:
    enforce_max_text_depth({"dms_base_url": "https://example.com/" + "x" * 400}, max_len=300)


def test_enforce_skips_underscore_url_suffix() -> None:
    enforce_max_text_depth({"callback_url": "https://x.com/" + "y" * 400}, max_len=300)
