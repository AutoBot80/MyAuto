"""Unpack a ZIP bundle from the dealer PC into uploads (Print / Queue RTO push)."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def apply_uploads_zip_bundle(
    dealer_id: int,
    subfolder: str,
    zip_bytes: bytes,
    uploads_root: Path,
) -> dict[str, Any]:
    """
    Extract ``{subfolder}/<filename>`` members from ``zip_bytes`` into ``uploads_root``.

    Returns counts and per-file S3 sync status.
    """
    from app.services.fill_hero_dms_service import _safe_subfolder_name
    from app.services.dealer_storage import sync_uploads_file_to_s3

    safe_sub = _safe_subfolder_name((subfolder or "").strip())
    if not safe_sub:
        raise ValueError("subfolder is required")

    prefix = f"{safe_sub}/"
    prefix_lower = prefix.lower()
    written = 0
    failed = 0
    s3_failed = 0
    details: list[dict[str, Any]] = []

    if not zip_bytes or len(zip_bytes) < 4:
        return {
            "ok": False,
            "error": "empty bundle",
            "files_written": 0,
            "files_failed": 0,
            "files_s3_failed": 0,
            "details": details,
        }

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        return {
            "ok": False,
            "error": f"invalid zip: {exc}",
            "files_written": 0,
            "files_failed": 0,
            "files_s3_failed": 0,
            "details": details,
        }

    root_res = uploads_root.resolve()
    members = [n for n in zf.namelist() if n and not n.endswith("/")]

    for name in members:
        norm = name.replace("\\", "/").lstrip("/")
        low = norm.lower()
        if not low.startswith(prefix_lower):
            # Allow flat names inside zip (no subfolder prefix) — attach to safe_sub
            leaf = Path(norm).name
            if not leaf or ".." in norm.split("/"):
                continue
            rel = f"{safe_sub}/{leaf}"
        else:
            rel = norm
        if ".." in rel.split("/"):
            failed += 1
            details.append({"rel_path": rel, "ok": False, "error": "path traversal"})
            continue
        try:
            dest = (uploads_root / rel).resolve()
            dest.relative_to(root_res)
        except ValueError:
            failed += 1
            details.append({"rel_path": rel, "ok": False, "error": "escapes uploads root"})
            continue
        except OSError as exc:
            failed += 1
            details.append({"rel_path": rel, "ok": False, "error": str(exc)})
            continue

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))
            s3_ok, s3_err = sync_uploads_file_to_s3(dealer_id, dest)
            written += 1
            if not s3_ok:
                s3_failed += 1
            details.append(
                {
                    "rel_path": rel,
                    "ok": True,
                    "s3_synced": s3_ok,
                    "s3_error": s3_err,
                }
            )
        except Exception as exc:
            failed += 1
            logger.warning("push-sale-bundle: write %s: %s", rel, exc)
            details.append({"rel_path": rel, "ok": False, "error": str(exc)})

    zf.close()
    ok = written > 0 and failed == 0
    return {
        "ok": ok,
        "files_written": written,
        "files_failed": failed,
        "files_s3_failed": s3_failed,
        "subfolder": safe_sub,
        "details": details,
    }
