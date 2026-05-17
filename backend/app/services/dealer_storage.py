"""Dealer file storage: local filesystem with optional S3 sync for cloud deployments."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from app.config import (
    CHALLANS_DIR,
    S3_CHALLANS_PREFIX,
    S3_DATA_BUCKET,
    S3_OCR_LOGS_PREFIX,
    S3_OCR_PREFIX,
    S3_UPLOADS_PREFIX,
    STORAGE_USE_S3,
    get_ocr_logs_dir,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.services import s3_storage

logger = logging.getLogger(__name__)


def uploads_s3_key(dealer_id: int, *relative_parts: str) -> str:
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_UPLOADS_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


def ocr_s3_key(dealer_id: int, *relative_parts: str) -> str:
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_OCR_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


def ocr_logs_s3_key(dealer_id: int, *relative_parts: str) -> str:
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_OCR_LOGS_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


def challans_s3_key(*relative_parts: str) -> str:
    """Global challans prefix (no per-dealer segment)."""
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_CHALLANS_PREFIX}/" + "/".join(parts)


def _relative_under(base: Path, file_path: Path) -> str | None:
    try:
        rel = file_path.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def sync_uploads_file_to_s3(
    dealer_id: int,
    file_path: Path,
    *,
    retries: int = 2,
) -> tuple[bool, str | None]:
    """
    Copy a file already on EC2 disk to S3 (long-term storage).

    Returns ``(True, None)`` when S3 is disabled, skipped, or upload succeeded.
    Returns ``(False, error)`` when S3 is configured but upload failed after retries.
    """
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return True, None
    base = get_uploads_dir(dealer_id)
    rel = _relative_under(base, file_path)
    if rel is None:
        return True, None
    if not file_path.is_file():
        return False, "file not found on disk before S3 sync"
    key = uploads_s3_key(dealer_id, *rel.split("/"))
    last_err: str | None = None
    for attempt in range(max(1, retries)):
        try:
            s3_storage.upload_file(file_path, key)
            return True, None
        except Exception as exc:
            last_err = str(exc).strip() or repr(exc)
            logger.warning(
                "dealer_storage: uploads S3 sync attempt %s/%s failed %s: %s",
                attempt + 1,
                retries,
                file_path,
                last_err,
            )
            if attempt + 1 < retries:
                time.sleep(0.4)
    logger.exception("dealer_storage: failed to sync uploads file to S3: %s", file_path)
    return False, last_err


def sync_ocr_file_to_s3(
    dealer_id: int,
    file_path: Path,
    *,
    retries: int = 2,
) -> tuple[bool, str | None]:
    """Same contract as :func:`sync_uploads_file_to_s3` for ``ocr_output/{dealer_id}/``."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return True, None
    base = get_ocr_output_dir(dealer_id)
    rel = _relative_under(base, file_path)
    if rel is None:
        return True, None
    if not file_path.is_file():
        return False, "file not found on disk before S3 sync"
    key = ocr_s3_key(dealer_id, *rel.split("/"))
    last_err: str | None = None
    for attempt in range(max(1, retries)):
        try:
            s3_storage.upload_file(file_path, key)
            return True, None
        except Exception as exc:
            last_err = str(exc).strip() or repr(exc)
            logger.warning(
                "dealer_storage: OCR S3 sync attempt %s/%s failed %s: %s",
                attempt + 1,
                retries,
                file_path,
                last_err,
            )
            if attempt + 1 < retries:
                time.sleep(0.4)
    logger.exception("dealer_storage: failed to sync OCR file to S3: %s", file_path)
    return False, last_err


def sync_uploads_subfolder_to_s3(dealer_id: int, subfolder: str) -> None:
    """Upload all files under ``Uploaded scans/{dealer_id}/{subfolder}/`` recursively."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = get_uploads_dir(dealer_id) / subfolder.strip().replace("\\", "/")
    if not base.is_dir():
        return
    for f in base.rglob("*"):
        if f.is_file():
            sync_uploads_file_to_s3(dealer_id, f)


def sync_ocr_subfolder_to_s3(dealer_id: int, subfolder: str) -> None:
    """Upload all files under ``ocr_output/{dealer_id}/{subfolder}/`` recursively."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = get_ocr_output_dir(dealer_id) / subfolder.strip().replace("\\", "/")
    if not base.is_dir():
        return
    for f in base.rglob("*"):
        if f.is_file():
            sync_ocr_file_to_s3(dealer_id, f)


def sync_ocr_logs_file_to_s3(dealer_id: int, file_path: Path) -> None:
    """Upload a file under ``get_ocr_logs_dir(dealer_id)`` to S3."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = get_ocr_logs_dir(dealer_id)
    rel = _relative_under(base, file_path)
    if rel is None:
        return
    if not file_path.is_file():
        return
    key = ocr_logs_s3_key(dealer_id, *rel.split("/"))
    try:
        s3_storage.upload_file(file_path, key)
    except Exception:
        logger.exception("dealer_storage: failed to sync ocr_logs file to S3: %s", file_path)


def sync_ocr_logs_subfolder_to_s3(dealer_id: int, subfolder: str) -> None:
    """Upload all files under ``ocr_logs/{dealer_id}/{subfolder}/`` recursively."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = get_ocr_logs_dir(dealer_id) / subfolder.strip().replace("\\", "/")
    if not base.is_dir():
        return
    for f in base.rglob("*"):
        if f.is_file():
            sync_ocr_logs_file_to_s3(dealer_id, f)


def sync_challans_file_to_s3(file_path: Path) -> None:
    """Upload a single file under ``CHALLANS_DIR`` to S3 (``S3_CHALLANS_PREFIX``)."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = CHALLANS_DIR.resolve()
    rel = _relative_under(base, file_path)
    if rel is None:
        return
    if not file_path.is_file():
        return
    key = challans_s3_key(*rel.split("/"))
    try:
        s3_storage.upload_file(file_path, key)
    except Exception:
        logger.exception("dealer_storage: failed to sync challans file to S3: %s", file_path)


def sync_challans_subfolder_to_s3(subfolder: str) -> None:
    """Upload all files under ``CHALLANS_DIR/{subfolder}/`` recursively."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = CHALLANS_DIR / subfolder.strip().replace("\\", "/")
    if not base.is_dir():
        return
    for f in base.rglob("*"):
        if f.is_file():
            sync_challans_file_to_s3(f)


def ensure_uploads_local_file(dealer_id: int, subfolder: str, filename: str) -> Path | None:
    """
    Return a local path to the file, downloading from S3 first if ``STORAGE_USE_S3`` and missing locally.
    """
    safe_sub = re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or None
    if not safe_sub:
        return None
    path = get_uploads_dir(dealer_id) / safe_sub / Path(filename).name
    if path.is_file():
        return path
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return None
    key = uploads_s3_key(dealer_id, safe_sub, Path(filename).name)
    if not s3_storage.object_exists(key):
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        s3_storage.download_to_path(key, path)
        return path
    except Exception:
        logger.exception("dealer_storage: failed to download from S3: %s", key)
        return None


def presigned_uploads_get(dealer_id: int, subfolder: str, filename: str) -> str | None:
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return None
    safe_sub = re.sub(r"[^\w\-]", "_", (subfolder or "").strip())
    if not safe_sub:
        return None
    name = Path(filename).name
    key = uploads_s3_key(dealer_id, safe_sub, name)
    if not s3_storage.object_exists(key):
        return None
    try:
        return s3_storage.generate_presigned_get_url(key)
    except Exception:
        logger.exception("dealer_storage: presigned URL failed for %s", key)
        return None


def presigned_uploads_get_by_rel_path(dealer_id: int, rel_path: str) -> str | None:
    """``rel_path`` is relative to dealer uploads root (e.g. ``mobile_ddmmyy/Form 20.pdf``)."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return None
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts:
        return None
    key = uploads_s3_key(dealer_id, *parts)
    if not s3_storage.object_exists(key):
        return None
    return s3_storage.generate_presigned_get_url(key)


def presigned_challans_get_by_rel_path(rel_path: str) -> str | None:
    """``rel_path`` relative to global challans root (no dealer segment)."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return None
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts:
        return None
    key = f"{S3_CHALLANS_PREFIX}/" + "/".join(parts)
    if not s3_storage.object_exists(key):
        return None
    return s3_storage.generate_presigned_get_url(key)


def presigned_ocr_get_by_rel_path(dealer_id: int, rel_path: str) -> str | None:
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return None
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts:
        return None
    key = ocr_s3_key(dealer_id, *parts)
    if not s3_storage.object_exists(key):
        return None
    return s3_storage.generate_presigned_get_url(key)


def list_uploads_subfolder_s3(dealer_id: int, subfolder: str) -> list[dict[str, Any]]:
    """List files in one sale subfolder (S3)."""
    safe = re.sub(r"[^\w\-]", "_", (subfolder or "").strip())
    if not safe:
        return []
    prefix = uploads_s3_key(dealer_id, safe) + "/"
    _, files = s3_storage.list_one_level_prefix(prefix)
    out: list[dict[str, Any]] = []
    for f in files:
        out.append({"name": f["name"], "size": int(f.get("Size") or 0)})
    return sorted(out, key=lambda x: x["name"])


def list_admin_folder_s3(root: str, dealer_id: int, rel_path: str) -> tuple[str, list[dict]]:
    """
    List one level for admin UI when using S3.
    Returns ``(display_abs_prefix, items)`` where items are dicts with name, kind, size, modified_at.
    """
    from datetime import datetime, timezone

    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    rel_parts = [p for p in rel.split("/") if p and p != "."]
    if root == "upload_scans":
        prefix_base = f"{S3_UPLOADS_PREFIX}/{int(dealer_id)}/"
    elif root == "ocr_output":
        prefix_base = f"{S3_OCR_PREFIX}/{int(dealer_id)}/"
    elif root == "challans":
        prefix_base = f"{S3_CHALLANS_PREFIX}/"
    else:
        raise ValueError(f"Unknown admin S3 folder root: {root!r}")
    prefix = prefix_base + ("/".join(rel_parts) + "/" if rel_parts else "")
    dirs, files = s3_storage.list_one_level_prefix(prefix)
    display = f"s3://{S3_DATA_BUCKET}/{prefix}"
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    dir_mtimes = s3_storage.aggregate_child_last_modified(prefix, dirs)
    items: list[dict] = []
    for d in dirs:
        mt = dir_mtimes.get(d) or epoch
        items.append(
            {
                "name": d,
                "kind": "dir",
                "size": None,
                "modified_at": mt.isoformat(),
                "_sort_mtime": mt,
            }
        )
    for f in files:
        lm = f.get("LastModified")
        mt = lm if lm else epoch
        items.append(
            {
                "name": f["name"],
                "kind": "file",
                "size": int(f.get("Size") or 0),
                "modified_at": mt.isoformat(),
                "_sort_mtime": mt,
            }
        )
    items.sort(key=lambda x: (-x["_sort_mtime"].timestamp(), x["name"].lower()))
    for x in items:
        del x["_sort_mtime"]
    return display, items
