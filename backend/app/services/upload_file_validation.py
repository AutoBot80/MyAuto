"""
Magic-byte validation, chunked reads, and safe filenames for scan uploads.

No external dependencies; JPEG / PNG / PDF only where applicable.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import UploadFile

# Executable / script-like suffixes (case-insensitive)
_BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".dll",
    ".ps1",
    ".vbs",
    ".js",
    ".jar",
    ".scr",
    ".hta",
    ".sh",
    ".app",
)


def detect_image_or_pdf_kind(data: bytes) -> str | None:
    """Return ``jpeg``, ``png``, ``pdf``, or None if unknown / too short."""
    if len(data) < 4:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"%PDF":
        return "pdf"
    return None


def validate_magic_jpeg_or_png(data: bytes, *, label: str) -> None:
    k = detect_image_or_pdf_kind(data)
    if k not in ("jpeg", "png"):
        raise ValueError(f"{label}: expected a JPEG or PNG image (magic-byte check failed).")


def validate_magic_jpeg_png_or_pdf(data: bytes, *, label: str) -> None:
    k = detect_image_or_pdf_kind(data)
    if k not in ("jpeg", "png", "pdf"):
        raise ValueError(f"{label}: expected JPEG, PNG, or PDF (magic-byte check failed).")


def validate_magic_jpeg_png_pdf_legacy(data: bytes, *, label: str) -> None:
    """POST /uploads/scans — allow JPEG, PNG, or PDF per file."""
    k = detect_image_or_pdf_kind(data)
    if k not in ("jpeg", "png", "pdf"):
        raise ValueError(f"{label}: allowed types are JPEG, PNG, or PDF (magic-byte check failed).")


def _blocked_suffix(name: str) -> str | None:
    low = name.lower().strip()
    for suf in _BLOCKED_SUFFIXES:
        if low.endswith(suf):
            return suf
    return None


def sanitize_legacy_upload_filename(name: str | None, *, default: str = "scan.jpg") -> str:
    """
    Single path segment; no directories; block dangerous extensions; trim length.
    """
    raw = (name or "").strip() or default
    base = Path(raw).name
    if not base or base in (".", ".."):
        base = default
    # Allow letters, numbers, space, dot, hyphen, underscore, parentheses
    base = "".join(c for c in base if ord(c) >= 32 and c not in '<>:"|?*\\')
    base = re.sub(r"\s+", " ", base).strip()
    if len(base) > 300:
        p = Path(base)
        base = (p.stem[:260] + p.suffix)[:300]
    bad = _blocked_suffix(base)
    if bad:
        raise ValueError(f"File name not allowed (blocked extension {bad}).")
    if not base:
        base = default
    return base


async def read_upload_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """Read upload in chunks; reject if larger than ``max_bytes``."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    chunks: list[bytes] = []
    total = 0
    chunk_size = 64 * 1024
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            if max_bytes >= 1024 * 1024:
                raise ValueError(
                    f"File exceeds maximum size ({max_bytes / (1024 * 1024):.1f} MB)."
                )
            raise ValueError(f"File exceeds maximum size ({max_bytes // 1024} KB).")
        chunks.append(chunk)
    return b"".join(chunks)
