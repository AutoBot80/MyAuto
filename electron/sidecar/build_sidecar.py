"""
Build ``job_runner.exe`` with PyInstaller (Windows).

``python build_sidecar.py`` installs (via pip) ``requirements.txt`` in this folder and
``backend/requirements.txt``, then runs PyInstaller. Requires network on first run.
Use Python 3.12.x if 3.14 fails on some wheels (match ``backend`` CI).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
BACKEND = REPO / "backend"
ENTRY = ROOT / "job_runner.py"
DIST = ROOT / "dist"
REQ_SIDECAR = ROOT / "requirements.txt"
REQ_BACKEND = BACKEND / "requirements.txt"


def _remove_pyinstaller_artifacts() -> None:
    """
    PyInstaller ``--clean`` uses ``shutil.rmtree``, which often fails on Windows with
    WinError 5 on paths like ``build/.../localpycs`` (AV, indexer, OneDrive locks).
    Remove workdir and spec with ``rmdir /s /q`` first, then best-effort rmtree.
    """
    work = ROOT / "build"
    spec = ROOT / "job_runner.spec"
    if os.name == "nt" and work.exists():
        for _ in range(3):
            subprocess.run(
                ["cmd", "/c", "rmdir", "/s", "/q", str(work)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.3)
            if not work.exists():
                break
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    if spec.is_file():
        try:
            spec.unlink()
        except OSError:
            pass


def _pip_install_requirements(path: Path, label: str) -> None:
    if not path.is_file():
        print(f"skip pip: missing {path}", file=sys.stderr)
        return
    print(f"pip install -r {label} ({path.name}) …")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade-strategy",
            "only-if-needed",
            "-r",
            str(path),
        ],
        cwd=str(ROOT),
    )


def main() -> int:
    if not BACKEND.is_dir():
        print(f"backend not found: {BACKEND}", file=sys.stderr)
        return 1
    if not ENTRY.is_file():
        print(f"job_runner.py not found: {ENTRY}", file=sys.stderr)
        return 1
    DIST.mkdir(parents=True, exist_ok=True)

    _pip_install_requirements(REQ_SIDECAR, "sidecar")
    _pip_install_requirements(REQ_BACKEND, "backend")

    _remove_pyinstaller_artifacts()

    sep = ";" if os.name == "nt" else ":"
    add_data = f"--add-data={BACKEND / 'app'}{sep}backend/app"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        "job_runner",
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build"),
        f"--paths={BACKEND}",
        add_data,
        str(ENTRY),
    ]
    print(" ", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    exe = DIST / ("job_runner.exe" if os.name == "nt" else "job_runner")
    if not exe.is_file():
        print(f"expected output missing: {exe}", file=sys.stderr)
        return 1
    print(f"ok: {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
