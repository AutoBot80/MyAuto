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
