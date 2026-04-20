"""
Shared version info for the running backend code.

``GIT_COMMIT_SHORT`` is resolved once at import time:
  - In a live checkout: ``git rev-parse --short HEAD``
  - In a PyInstaller frozen bundle: reads ``_build_meta.json`` (stamped at build time)
  - Fallback: empty string
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _BACKEND_ROOT / "VERSION"


def read_backend_semver() -> str:
    """Semver from ``backend/VERSION`` (deploy script); fallback ``0.0.0``."""
    try:
        if _VERSION_FILE.is_file():
            return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return "0.0.0"


BACKEND_SEMVER: str = read_backend_semver()


def _resolve_git_commit() -> str:
    meta = _APP_DIR / "_build_meta.json"
    if meta.is_file():
        try:
            return json.loads(meta.read_text(encoding="utf-8")).get("git_commit", "")
        except Exception:
            pass
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_APP_DIR),
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


GIT_COMMIT_SHORT: str = _resolve_git_commit()
