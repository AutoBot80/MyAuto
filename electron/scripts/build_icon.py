"""
Try mesh-based icons (needs electron/resources/source/mesh_ring.png + silver_ring.png).
If that fails, fall back to generate_app_icon.py (programmatic PNG + multi-size ICO).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    mesh = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_app_icon.py")],
        cwd=str(ROOT),
    )
    if mesh.returncode == 0:
        return 0
    print(
        "build_app_icon.py skipped or failed (add source PNGs under resources/source/ for mesh/silver branding). "
        "Falling back to generate_app_icon.py.",
        file=sys.stderr,
    )
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_app_icon.py")],
        cwd=str(ROOT),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
