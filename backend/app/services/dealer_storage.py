"""Dealer file storage: local filesystem with optional S3 sync for cloud deployments."""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from app.config import (
    S3_CHALLANS_PREFIX,
    S3_DATA_BUCKET,
    S3_OCR_LOGS_PREFIX,
    S3_OCR_PREFIX,
    S3_UPLOADS_PREFIX,
    STORAGE_USE_S3,
    get_challans_dir,
    get_ocr_logs_dir,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.services import s3_storage

logger = logging.getLogger(__name__)

ADMIN_FOLDER_ZIP_MAX_FILES = 500
ADMIN_FOLDER_ZIP_MAX_BYTES = 200 * 1024 * 1024


class AdminFolderZipError(Exception):
    """Base error for admin folder zip export."""


class AdminFolderNotFoundError(AdminFolderZipError):
    pass


class AdminFolderEmptyError(AdminFolderZipError):
    pass


class AdminFolderTooLargeError(AdminFolderZipError):
    pass


def uploads_s3_key(dealer_id: int, *relative_parts: str) -> str:
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_UPLOADS_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


def ocr_s3_key(dealer_id: int, *relative_parts: str) -> str:
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_OCR_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


def ocr_logs_s3_key(dealer_id: int, *relative_parts: str) -> str:
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_OCR_LOGS_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


def challans_s3_key(dealer_id: int, *relative_parts: str) -> str:
    """Dealer-scoped challans prefix: ``challans/{dealer_id}/...``."""
    parts = [p.replace("\\", "/").strip("/") for p in relative_parts if p and str(p).strip()]
    return f"{S3_CHALLANS_PREFIX}/{int(dealer_id)}/" + "/".join(parts)


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


def sync_challans_file_to_s3(dealer_id: int, file_path: Path) -> None:
    """Upload a single file under ``Challans/{dealer_id}/`` to S3."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    base = get_challans_dir(dealer_id).resolve()
    rel = _relative_under(base, file_path)
    if rel is None:
        return
    if not file_path.is_file():
        return
    key = challans_s3_key(dealer_id, *rel.split("/"))
    try:
        s3_storage.upload_file(file_path, key)
    except Exception:
        logger.exception("dealer_storage: failed to sync challans file to S3: %s", file_path)


def sync_challans_subfolder_to_s3(dealer_id: int, artifact_leaf: str) -> None:
    """Upload all files under ``Challans/{dealer_id}/{artifact_leaf}/`` recursively."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return
    from app.config import get_challan_artifacts_dir

    base = get_challan_artifacts_dir(dealer_id, artifact_leaf)
    if not base.is_dir():
        return
    for f in base.rglob("*"):
        if f.is_file():
            sync_challans_file_to_s3(dealer_id, f)


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


def presigned_challans_get_by_rel_path(dealer_id: int, rel_path: str) -> str | None:
    """``rel_path`` relative to ``Challans/{dealer_id}/``."""
    if not STORAGE_USE_S3 or not S3_DATA_BUCKET:
        return None
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts:
        return None
    key = challans_s3_key(dealer_id, *parts)
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
        prefix_base = f"{S3_CHALLANS_PREFIX}/{int(dealer_id)}/"
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


def _admin_s3_prefix(root: str, dealer_id: int, rel_parts: list[str]) -> str:
    if root == "upload_scans":
        prefix_base = f"{S3_UPLOADS_PREFIX}/{int(dealer_id)}/"
    elif root == "ocr_output":
        prefix_base = f"{S3_OCR_PREFIX}/{int(dealer_id)}/"
    elif root == "challans":
        prefix_base = f"{S3_CHALLANS_PREFIX}/{int(dealer_id)}/"
    else:
        raise ValueError(f"Unknown admin S3 folder root: {root!r}")
    return prefix_base + ("/".join(rel_parts) + "/" if rel_parts else "")


def _admin_folder_base_local(root: str, dealer_id: int) -> Path:
    if root == "upload_scans":
        return get_uploads_dir(dealer_id)
    if root == "ocr_output":
        return get_ocr_output_dir(dealer_id)
    if root == "challans":
        return get_challans_dir(dealer_id).resolve()
    raise ValueError(f"Unknown admin folder root: {root!r}")


def _resolve_admin_rel_path(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base``; reject ``..`` and escapes."""
    base_resolved = base.resolve()
    rel = (rel or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("rel_path required")
    parts = [p for p in rel.split("/") if p and p != "."]
    for p in parts:
        if p == "..":
            raise ValueError("Invalid path")
    target = base_resolved.joinpath(*parts)
    target = target.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as e:
        raise ValueError("Invalid path") from e
    return target


def _sanitize_admin_zip_filename(rel_path: str) -> str:
    rel = (rel_path or "").strip().replace("\\", "/").strip("/")
    last = rel.split("/")[-1] if rel else "folder"
    safe = re.sub(r"[^\w.\-]", "_", last).strip("._-")
    return safe or "folder"


def _build_admin_folder_zip_s3(prefix: str, filename: str) -> tuple[bytes, str]:
    objects = s3_storage.list_objects_with_prefix(prefix)
    file_objects = [o for o in objects if not (o.get("Key") or "").endswith("/")]
    if not file_objects:
        raise AdminFolderEmptyError("Folder is empty")
    if len(file_objects) > ADMIN_FOLDER_ZIP_MAX_FILES:
        logger.warning("admin folder zip rejected: %s files under %s", len(file_objects), prefix)
        raise AdminFolderTooLargeError(
            f"Folder has too many files ({len(file_objects)}; max {ADMIN_FOLDER_ZIP_MAX_FILES})"
        )
    total_size = sum(int(o.get("Size") or 0) for o in file_objects)
    if total_size > ADMIN_FOLDER_ZIP_MAX_BYTES:
        logger.warning("admin folder zip rejected: %s bytes under %s", total_size, prefix)
        raise AdminFolderTooLargeError(
            f"Folder is too large ({total_size // (1024 * 1024)} MB; max {ADMIN_FOLDER_ZIP_MAX_BYTES // (1024 * 1024)} MB)"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for obj in file_objects:
            key = obj["Key"]
            arcname = key[len(prefix) :]
            if not arcname or arcname.endswith("/"):
                continue
            data = s3_storage.download_bytes(key)
            zf.writestr(arcname, data)
    buf.seek(0)
    return buf.read(), filename


def _build_admin_folder_zip_local(folder: Path, filename: str) -> tuple[bytes, str]:
    if not folder.is_dir():
        raise AdminFolderNotFoundError("Folder not found")
    files = [p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")]
    if not files:
        raise AdminFolderEmptyError("Folder is empty")
    if len(files) > ADMIN_FOLDER_ZIP_MAX_FILES:
        logger.warning("admin folder zip rejected: %s files under %s", len(files), folder)
        raise AdminFolderTooLargeError(
            f"Folder has too many files ({len(files)}; max {ADMIN_FOLDER_ZIP_MAX_FILES})"
        )
    total_size = sum(p.stat().st_size for p in files)
    if total_size > ADMIN_FOLDER_ZIP_MAX_BYTES:
        logger.warning("admin folder zip rejected: %s bytes under %s", total_size, folder)
        raise AdminFolderTooLargeError(
            f"Folder is too large ({total_size // (1024 * 1024)} MB; max {ADMIN_FOLDER_ZIP_MAX_BYTES // (1024 * 1024)} MB)"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arcname = path.relative_to(folder).as_posix()
            zf.write(path, arcname)
    buf.seek(0)
    return buf.read(), filename


def build_admin_folder_zip_bytes(root: str, dealer_id: int, rel_path: str) -> tuple[bytes, str]:
    """
    Zip all files under an admin folder (recursive). ``rel_path`` must be non-empty.
    Returns ``(zip_bytes, download_stem)`` where ``download_stem`` is the safe filename without ``.zip``.
    """
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    rel_parts = [p for p in rel.split("/") if p and p != "."]
    for p in rel_parts:
        if p == "..":
            raise ValueError("Invalid path")
    if not rel_parts:
        raise ValueError("rel_path required")

    filename = _sanitize_admin_zip_filename(rel)

    if STORAGE_USE_S3:
        prefix = _admin_s3_prefix(root, dealer_id, rel_parts)
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        return _build_admin_folder_zip_s3(prefix, filename)

    base = _admin_folder_base_local(root, dealer_id)
    folder = _resolve_admin_rel_path(base, rel)
    return _build_admin_folder_zip_local(folder, filename)
