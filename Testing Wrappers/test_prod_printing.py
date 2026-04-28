"""
Local test wrapper: run the **Electron main-process print pipeline** against PDFs on disk
(same path as ``printPdfsFromPresignedUrls`` for local ``D:\\...`` files).

Hard-coded folder and single PDF basename (edit here if needed)::
    C:\\Users\\arya_\\OneDrive\\Desktop\\My Auto.AI\\Uploaded scans\\100001\\9784542030_250426
    9784542030_Insurance_27042026  (``.pdf`` added automatically)

Prerequisites:
  - ``electron/node_modules`` installed (``npm install`` in ``electron/``).
  - That PDF present in the folder above.

Mechanism:
  - Sets ``SAATHI_PRINT_TEST_DIR`` and ``SAATHI_PRINT_TEST_ONLY``, runs ``npm run dev`` from ``electron/``.
    Main process prints only the matching ``*.pdf``, shows a dialog, and exits.

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

# Hard-coded scan folder and PDF basename (no extension, or include ``.pdf``).
PRINT_TEST_DIR = r"C:\Users\arya_\OneDrive\Desktop\My Auto.AI\Uploaded scans\100001\9784542030_250426"
PRINT_TEST_ONLY = "9784542030_Insurance_27042026"


def main() -> int:
    pdf_dir = Path(PRINT_TEST_DIR)
    if not pdf_dir.is_dir():
        logger.error("Folder not found: %s", PRINT_TEST_DIR)
        return 1

    target_name = PRINT_TEST_ONLY if PRINT_TEST_ONLY.lower().endswith(".pdf") else f"{PRINT_TEST_ONLY}.pdf"
    target_path = pdf_dir / target_name
    if not target_path.is_file():
        logger.error("PDF not found: %s", target_path)
        return 1

    logger.info("Print target: %s", target_name)

    if not (_ELECTRON / "package.json").is_file():
        logger.error("electron/package.json not found. Expected repo layout: My Auto.AI/electron/")
        return 1

    npm = shutil.which("npm")
    if not npm:
        logger.error("npm not found on PATH — install Node.js or use a shell where npm is available.")
        return 1

    env = os.environ.copy()
    env["SAATHI_PRINT_TEST_DIR"] = PRINT_TEST_DIR
    env["SAATHI_PRINT_TEST_ONLY"] = PRINT_TEST_ONLY

    logger.info("Starting Electron print smoke (SAATHI_PRINT_TEST_DIR + SAATHI_PRINT_TEST_ONLY)...")
    logger.info("Working directory: %s", _ELECTRON)

    r = subprocess.run([npm, "run", "dev"], cwd=str(_ELECTRON), env=env)
    if r.returncode != 0:
        logger.error("Electron exited with code %s", r.returncode)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
