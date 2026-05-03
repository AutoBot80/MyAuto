"""
Local test: headless dealer signature overlay on Form 20 / GST / Sale Certificate PDFs
in a sale folder (same logic as Electron ``dealerSign:overlaySalePdfs``).

Default folder (override with ``--folder``)::
    D:\\Saath\\Dealer Saathi\\Uploaded scans\\100001\\7296967153_290426

Put ``{dealer_id}_sign.jpg`` next to ``.env`` under your **data root** (the parent of ``Dealer Saathi``),
e.g. ``D:\\Saath\\100001_sign.jpg`` when your install is ``D:\\Saath\\Dealer Saathi\\``. Override with
``--signature`` or set ``SAATHI_BASE_DIR``.

Double-click ``test_dealer_sign_overlay.bat`` or::

  python test_dealer_sign_overlay.py

from this folder (repo root = parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_PARENT = _REPO_ROOT / "backend"

DEFAULT_FOLDER = r"D:\Saath\Dealer Saathi\Uploaded scans\100001\7296967153_290426"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_dealer_sign_overlay")


def main() -> int:
    p = argparse.ArgumentParser(description="Test dealer_sign_overlay on a sale folder.")
    p.add_argument("--folder", default=DEFAULT_FOLDER, help="Absolute path to sale subfolder")
    p.add_argument("--dealer-id", type=int, default=100001)
    p.add_argument("--signature", default="", help="Optional path to signature JPEG")
    args = p.parse_args()

    sale_dir = Path(args.folder).resolve()
    if not sale_dir.is_dir():
        logger.error("Folder not found: %s", sale_dir)
        return 1

    if not _BACKEND_PARENT.is_dir():
        logger.error("backend/ not found next to repo root: %s", _BACKEND_PARENT)
        return 1

    sys.path.insert(0, str(_BACKEND_PARENT))
    # Same rule as Electron getSaathiBaseDir: data root contains .env and {dealer_id}_sign.jpg (not inside Dealer Saathi).
    if not (os.getenv("SAATHI_BASE_DIR") or "").strip():
        if Path(r"D:\Saath\.env").is_file():
            os.environ["SAATHI_BASE_DIR"] = r"D:\Saath"
        elif Path(r"D:\Saathi\.env").is_file():
            os.environ["SAATHI_BASE_DIR"] = r"D:\Saathi"
        else:
            os.environ.setdefault("SAATHI_BASE_DIR", r"D:\Saathi")

    from app.services.dealer_sign_overlay import apply_dealer_signatures_to_sale_folder

    sig = Path(args.signature).resolve() if str(args.signature).strip() else None
    extra = []
    sb = os.getenv("SAATHI_BASE_DIR", "").strip()
    if sb:
        extra.append(Path(sb))

    result = apply_dealer_signatures_to_sale_folder(
        sale_dir,
        int(args.dealer_id),
        sig if sig and sig.is_file() else None,
        candidate_dirs_for_signature=extra,
    )
    logger.info("Result: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
