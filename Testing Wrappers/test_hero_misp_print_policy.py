"""
Local test wrapper: open MISP like **Generate Insurance** (Sign In + 2W only), then run
``run_hero_insure_reports`` (Policy Issuance → Print Policy → AllPrintPolicy, two Print steps, PDF + dispatch).

Prerequisites:
  - ``backend/.env`` with ``INSURANCE_BASE_URL`` (same as the client / Fill Insurance).
  - Playwright and backend deps installed.
  - If auto **Sign In** does not run, complete partner login in the opened browser and re-run.

Optional:
  - Set ``MISP_GOTO_ALL_PRINT_POLICY`` in the environment to the full **AllPrintPolicy.aspx** URL if
    sidebar **Print Policy** is hard to reach.

This script sets **HERO_MISP_PDF_DEBUG=1** in-process (before importing ``app.config`` / ``load_dotenv``,
so .env does not override) for the frame dump. Post-save print is always scheduled by
``schedule_misp_hero_post_pdf``; after a successful run it briefly **waits** so the print dialog can
appear before the process exits.

Default paths (edit the constants below if your sale folder differs) resolve under the repo root
(``My Auto.AI/Uploaded scans/...`` and ``My Auto.AI/ocr_output/...``).

Double-click ``test_hero_misp_print_policy.bat`` or from this folder::

  python test_hero_misp_print_policy.py

``repo root`` = parent of ``Testing Wrappers``.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_hero_misp_print_policy")

# --- Edit these for your test sale ---
DEALER_FOLDER = "100001"
SALE_SUBFOLDER = "9784542030_250426"  # 10-digit mobile + _ddmmyy; used for logs + PDF naming
INSURER = "ICICI Lombard General Insurance"
POLICY_NUM = "3005/56488939/11870/000"
# Absolute overrides (empty = use repo-relative Uploaded scans / ocr_output)
UPLOADS_DIR_OVERRIDE = ""
OCR_OUTPUT_DIR_OVERRIDE = ""

# After save, allow the system print host a moment to open before the wrapper exits (0 = no wait)
POST_SAVE_PRINT_UI_GRACE_SEC = 2.0


def _path_or_default(override: str, rel_under_repo: Path) -> Path:
    o = (override or "").strip()
    if o:
        return Path(o)
    return (_REPO_ROOT / rel_under_repo).resolve()


def main() -> int:
    # In-process (before ``import app.config`` so ``load_dotenv`` does not override frame dump)
    os.environ["HERO_MISP_PDF_DEBUG"] = "1"
    from app.config import INSURANCE_BASE_URL
    from app.services.fill_hero_insurance_service import open_misp_page_sign_in_and_2w_only
    from app.services.hero_insure_reports_service import run_hero_insure_reports

    base = (INSURANCE_BASE_URL or "").strip()
    if not base:
        logger.error("INSURANCE_BASE_URL is not set — add it to backend/.env (same as Generate Insurance).")
        return 1

    uploads = _path_or_default(
        UPLOADS_DIR_OVERRIDE,
        Path("Uploaded scans") / DEALER_FOLDER / SALE_SUBFOLDER,
    )
    ocr_out = _path_or_default(
        OCR_OUTPUT_DIR_OVERRIDE,
        Path("ocr_output") / DEALER_FOLDER / SALE_SUBFOLDER,
    )
    ocr_out.mkdir(parents=True, exist_ok=True)
    try:
        uploads.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("uploads_dir: %s", exc)
        return 1

    logger.info("INSURANCE_BASE_URL: %s", base[:80] + ("…" if len(base) > 80 else ""))
    logger.info("uploads_dir:  %s", uploads)
    logger.info("ocr_output:   %s", ocr_out)
    logger.info("subfolder:    %s", SALE_SUBFOLDER)
    logger.info("insurer:      %s", INSURER)
    logger.info("policy_num:   %s", POLICY_NUM)

    page, err = open_misp_page_sign_in_and_2w_only(
        base, ocr_output_dir=ocr_out, subfolder=SALE_SUBFOLDER
    )
    if page is None:
        logger.error("open_misp_page_sign_in_and_2w_only: %s", err)
        return 1

    try:
        out = run_hero_insure_reports(
            page,
            insurer=INSURER,
            policy_num=POLICY_NUM,
            uploads_dir=uploads,
            ocr_output_dir=ocr_out,
            subfolder=SALE_SUBFOLDER,
        )
    except Exception as exc:
        logger.exception("run_hero_insure_reports raised: %s", exc)
        return 1

    logger.info("result: %s", out)
    if not out.get("ok"):
        logger.error("run_hero_insure_reports failed: %s", out.get("error"))
        return 1
    logger.info("pdf_path: %s", out.get("pdf_path"))
    n = float(POST_SAVE_PRINT_UI_GRACE_SEC or 0.0)
    if n > 0:
        logger.info(
            "Sleeping %ss so Windows can show the print dialog before exit (set POST_SAVE_PRINT_UI_GRACE_SEC=0 to skip)",
            n,
        )
        time.sleep(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
