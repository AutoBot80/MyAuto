"""Rename per-sale upload/OCR folders when in-process mobile changes (folder leaf only)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import STORAGE_USE_S3, get_ocr_output_dir, get_uploaded_scans_sale_subfolder_leaf, get_uploads_dir
from app.repositories.add_sales_staging import normalize_staging_natural_key_mobile
from app.services.dealer_storage import (
    delete_s3_objects_with_prefix,
    ocr_s3_key,
    sync_ocr_subfolder_to_s3,
    sync_uploads_subfolder_to_s3,
    uploads_s3_key,
)
from app.services.ocr_sale_artifacts import _safe_subfolder_name

logger = logging.getLogger(__name__)

OCR_JSON_NAME = "OCR_To_be_Used.json"


def compute_new_subfolder_leaf(old_leaf: str, new_mobile: Any) -> str:
    """
    Build ``{new10digit}_{same_ddmmyy}`` from existing leaf; fall back to today's date suffix.
    """
    new_mob = normalize_staging_natural_key_mobile(new_mobile)
    if not new_mob or len(new_mob) != 10:
        raise ValueError("Mobile must be exactly 10 digits.")
    old = (old_leaf or "").strip()
    m = re.match(r"^(\d{10})_(\d{6})$", old)
    if m:
        return f"{new_mob}_{m.group(2)}"
    m2 = re.match(r"^.+_(\d{6})$", old)
    if m2:
        return f"{new_mob}_{m2.group(1)}"
    return get_uploaded_scans_sale_subfolder_leaf(new_mob)


def _rename_dir_if_exists(parent: Path, old_leaf: str, new_leaf: str) -> bool:
    old_safe = _safe_subfolder_name(old_leaf)
    new_safe = _safe_subfolder_name(new_leaf)
    if old_safe == new_safe:
        return False
    old_dir = parent / old_safe
    new_dir = parent / new_safe
    if not old_dir.is_dir():
        return False
    if new_dir.exists():
        raise ValueError(f"Target folder already exists: {new_safe}")
    old_dir.rename(new_dir)
    return True


def patch_ocr_to_be_used_json(
    ocr_dir: Path,
    *,
    mobile_number: int | None = None,
    alt_phone_num: str | None = None,
    alt_clear: bool = False,
) -> None:
    """Update customer phone fields inside ``OCR_To_be_Used.json`` when present."""
    json_path = ocr_dir / OCR_JSON_NAME
    if not json_path.is_file():
        return
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("patch_ocr_to_be_used_json read failed: %s", exc)
        return
    if not isinstance(data, dict):
        return
    cust = data.get("customer")
    if not isinstance(cust, dict):
        cust = {}
        data["customer"] = cust
    if mobile_number is not None:
        cust["mobile_number"] = mobile_number
    if alt_clear:
        cust["alt_phone_num"] = ""
    elif alt_phone_num is not None:
        cust["alt_phone_num"] = alt_phone_num
    try:
        json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        logger.warning("patch_ocr_to_be_used_json write failed: %s", exc)


def rename_sale_folders_for_mobile_change(
    dealer_id: int,
    old_leaf: str,
    new_leaf: str,
    *,
    mobile_number: int | None = None,
    alt_phone_num: str | None = None,
    alt_clear: bool = False,
) -> None:
    """
    Rename ``Uploaded scans`` and ``ocr_output`` sale subfolders; patch OCR JSON; sync S3.
    In-folder PDF names are left unchanged.
    """
    old_leaf = (old_leaf or "").strip()
    new_leaf = (new_leaf or "").strip()
    if not old_leaf:
        raise ValueError("file_location subfolder is required to rename sale folders.")
    did = int(dealer_id)
    uploads_parent = get_uploads_dir(did)
    ocr_parent = get_ocr_output_dir(did)

    if old_leaf != new_leaf:
        _rename_dir_if_exists(uploads_parent, old_leaf, new_leaf)
        _rename_dir_if_exists(ocr_parent, old_leaf, new_leaf)
        if STORAGE_USE_S3:
            sync_uploads_subfolder_to_s3(did, new_leaf)
            sync_ocr_subfolder_to_s3(did, new_leaf)
            delete_s3_objects_with_prefix(uploads_s3_key(did, old_leaf))
            delete_s3_objects_with_prefix(ocr_s3_key(did, old_leaf))

    ocr_new = ocr_parent / _safe_subfolder_name(new_leaf)
    if mobile_number is not None or alt_phone_num is not None or alt_clear:
        patch_ocr_to_be_used_json(
            ocr_new,
            mobile_number=mobile_number,
            alt_phone_num=alt_phone_num,
            alt_clear=alt_clear,
        )
        if STORAGE_USE_S3 and ocr_new.is_dir():
            from app.services.dealer_storage import sync_ocr_file_to_s3

            json_file = ocr_new / OCR_JSON_NAME
            if json_file.is_file():
                sync_ocr_file_to_s3(did, json_file)
