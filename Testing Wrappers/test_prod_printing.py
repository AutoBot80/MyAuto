"""
Local test wrapper: run the **Electron main-process print pipeline** against PDFs on disk
(same path as ``printPdfsFromPresignedUrls`` for local ``D:\\...`` files).

Hard-coded folder (edit here if needed)::
    D:\\Saathi\\Uploaded scans\\100001\\8905969604_210426

Prerequisites:
  - ``electron/node_modules`` installed (``npm install`` in ``electron/``).
  - PDF files present in the folder above.

Mechanism:
  - Sets ``SAATHI_PRINT_TEST_DIR`` and runs ``npm run dev`` from ``electron/`` (builds TS, then
    starts Electron). Main process detects the env var, prints each ``*.pdf``, shows a dialog,
    and exits (no full UI / no Vite required for the print smoke path).

Double-click ``test_prod_printing.bat`` or run::

  python test_prod_printing.py

from this folder (repo root must be the parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ELECTRON = _REPO_ROOT / "electron"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_prod_printing")

# Hard-coded prod scan folder (Windows).
PRINT_TEST_DIR = r"D:\Saathi\Uploaded scans\100001\8905969604_210426"


def main() -> int:
    pdf_dir = Path(PRINT_TEST_DIR)
    if not pdf_dir.is_dir():
        logger.error("Folder not found: %s", PRINT_TEST_DIR)
        return 1

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        logger.error("No *.pdf files in %s", PRINT_TEST_DIR)
        return 1

    for p in pdfs:
        logger.info("  PDF: %s", p.name)

    if not (_ELECTRON / "package.json").is_file():
        logger.error("electron/package.json not found. Expected repo layout: My Auto.AI/electron/")
        return 1

    npm = shutil.which("npm")
    if not npm:
        logger.error("npm not found on PATH — install Node.js or use a shell where npm is available.")
        return 1

    env = os.environ.copy()
    env["SAATHI_PRINT_TEST_DIR"] = PRINT_TEST_DIR

    logger.info("Starting Electron print smoke (SAATHI_PRINT_TEST_DIR set)...")
    logger.info("Working directory: %s", _ELECTRON)

    r = subprocess.run([npm, "run", "dev"], cwd=str(_ELECTRON), env=env)
    if r.returncode != 0:
        logger.error("Electron exited with code %s", r.returncode)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
