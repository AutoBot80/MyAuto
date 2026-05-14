"""CPA Alliance (third-party insurer) portal — Add Sales Playwright helper.

Opens the portal in the dedicated **CPAInsurance** native Chromium profile
(see :mod:`app.services.handle_browser_opening`), streams a trace to
``ocr_logs/{dealer_id}/{subfolder}/playwright_cpa_<IST>.txt``, saves downloads under
``Uploaded scans/{dealer_id}/{subfolder}/``, and syncs to S3 when configured.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import ENVIRONMENT_IS_PRODUCTION, INSURANCE_LOGIN_WAIT_MS, get_ocr_logs_dir, get_uploads_dir
from app.services.dealer_storage import (
    sync_ocr_logs_subfolder_to_s3,
    sync_uploads_subfolder_to_s3,
)
from app.services.fill_hero_dms_service import _safe_subfolder_name
from app.services.handle_browser_opening import _is_ready_after_login_page, get_or_open_site_page

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


def _resolve_cpa_portal_url(portal_url: str | None) -> str:
    """``ALLIANCE_CPA_PORTAL_URL`` wins over the URL from ``master_ref`` / UI."""
    env = (os.getenv("ALLIANCE_CPA_PORTAL_URL") or "").strip()
    if env:
        return env
    return (portal_url or "").strip()


def _new_cpa_playwright_log_path(dealer_id: int, subfolder: str) -> Path:
    safe = _safe_subfolder_name(subfolder)
    ts = datetime.now(_IST).strftime("%d%m%Y_%H%M%S")
    base = get_ocr_logs_dir(int(dealer_id)) / safe
    base.mkdir(parents=True, exist_ok=True)
    return base / f"playwright_cpa_{ts}.txt"


def _append_cpa_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")


def _wait_cpa_portal_ready(page, log_path: Path, *, wait_ms: int) -> str | None:
    """Return an operator message if login surface is still visible after ``wait_ms``."""
    deadline = time.monotonic() + max(1.0, wait_ms / 1000.0)
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                return "CPA browser tab closed while waiting for login."
        except Exception:
            return "CPA browser tab closed while waiting for login."
        try:
            if _is_ready_after_login_page(page):
                _append_cpa_log(log_path, "NOTE portal session ready (past login surface).")
                return None
        except Exception as exc:
            logger.debug("add_alliance_cpa_insurance: readiness probe: %s", exc)
        try:
            page.wait_for_timeout(450)
        except Exception:
            time.sleep(0.45)
    return (
        "CPA portal still shows a login page after waiting. Log in, leave the tab open, "
        "then press CPA Insurance again."
    )


def _install_download_sink(page, dealer_id: int, safe_sub: str, log_path: Path) -> None:
    uploads_root = get_uploads_dir(int(dealer_id)) / safe_sub

    def _on_download(download) -> None:
        try:
            uploads_root.mkdir(parents=True, exist_ok=True)
            suggested = (download.suggested_filename or "download").strip() or "download"
            dest = uploads_root / Path(suggested).name
            download.save_as(str(dest))
            _append_cpa_log(log_path, f"NOTE download saved uploads/{safe_sub}/{dest.name}")
        except Exception as exc:
            _append_cpa_log(log_path, f"ERROR download save failed: {exc}")
            logger.warning("add_alliance_cpa_insurance: download save failed: %s", exc)

    try:
        page.on("download", _on_download)
    except Exception as exc:
        logger.debug("add_alliance_cpa_insurance: download handler not installed: %s", exc)


def _try_fill_labeled_fields(
    page, log_path: Path, pairs: list[tuple[str, str | None]]
) -> None:
    """Best-effort: fill visible textboxes whose accessible name matches a hint."""
    for hint, raw in pairs:
        val = (raw or "").strip()
        if not val:
            continue
        rx = re.compile(re.escape(hint), re.I)
        for role in ("textbox", "combobox"):
            try:
                loc = page.get_by_role(role, name=rx)
                n = loc.count()
                if n < 1:
                    continue
                first = loc.first
                if not first.is_visible():
                    continue
                first.fill(val[:256], timeout=3_000)
                _append_cpa_log(log_path, f"NOTE filled field hint={hint!r} role={role}")
                break
            except Exception as exc:
                logger.debug("add_alliance_cpa_insurance: fill hint=%r role=%s: %s", hint, role, exc)


def _maybe_click_save_in_production(page, log_path: Path) -> None:
    """In production only, try a single low-risk primary-action click (portal-specific)."""
    if not ENVIRONMENT_IS_PRODUCTION:
        _append_cpa_log(
            log_path,
            "NOTE skipping Save/Submit automation (non-production ENVIRONMENT).",
        )
        return
    if (os.getenv("ALLIANCE_CPA_AUTO_SUBMIT") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "y",
    ):
        _append_cpa_log(
            log_path,
            "NOTE ALLIANCE_CPA_AUTO_SUBMIT not set — skipping Save/Submit click in production.",
        )
        return
    try:
        btn = page.get_by_role("button", name=re.compile(r"save|submit|confirm|issue", re.I)).first
        if btn.is_visible(timeout=2_000):
            btn.click(timeout=8_000)
            _append_cpa_log(log_path, "NOTE clicked primary Save/Submit (ALLIANCE_CPA_AUTO_SUBMIT).")
    except Exception as exc:
        _append_cpa_log(log_path, f"NOTE Save/Submit not clicked or not found: {exc}")


def add_alliance_cpa_insurance(
    *,
    dealer_id: int,
    subfolder: str,
    portal_url: str | None,
    customer_name: str | None = None,
    mobile: str | None = None,
    frame_no: str | None = None,
    engine_no: str | None = None,
) -> dict[str, Any]:
    """
    Open the CPA insurer portal, wait for operator login when needed, best-effort field hints,
    optional production Save (``ALLIANCE_CPA_AUTO_SUBMIT``), then sync uploads / ocr_logs to S3.

    Runs on the Playwright worker thread (Electron sidecar or ``run_playwright_callable_sync``).
    """
    resolved = _resolve_cpa_portal_url(portal_url)
    if not resolved:
        return {
            "success": False,
            "error": "CPA portal URL missing. Set ``master_ref.comments`` (https URL) for the CPA row "
            "or environment ``ALLIANCE_CPA_PORTAL_URL``.",
        }
    safe_sub = _safe_subfolder_name(subfolder)
    if not safe_sub:
        return {"success": False, "error": "subfolder missing or invalid for CPA run."}

    log_path = _new_cpa_playwright_log_path(dealer_id, safe_sub)
    _append_cpa_log(
        log_path,
        f"NOTE add_alliance_cpa_insurance start dealer_id={dealer_id} subfolder={safe_sub} url={resolved[:200]}",
    )

    page, open_err = get_or_open_site_page(
        resolved,
        "CPAInsurance",
        require_login_on_open=False,
    )
    if page is None:
        msg = open_err or "Could not open CPA portal."
        _append_cpa_log(log_path, f"ERROR open: {msg}")
        return {"success": False, "error": str(msg), "page_url": None}

    _install_download_sink(page, dealer_id, safe_sub, log_path)

    wait_err = _wait_cpa_portal_ready(page, log_path, wait_ms=int(INSURANCE_LOGIN_WAIT_MS))
    if wait_err:
        try:
            u = (page.url or "").strip()
        except Exception:
            u = ""
        _append_cpa_log(log_path, f"ERROR {wait_err}")
        return {"success": False, "error": wait_err, "page_url": u or None}

    _try_fill_labeled_fields(
        page,
        log_path,
        [
            ("chassis", frame_no),
            ("frame", frame_no),
            ("vin", frame_no),
            ("engine", engine_no),
            ("mobile", mobile),
            ("phone", mobile),
            ("customer", customer_name),
            ("name", customer_name),
            ("proposer", customer_name),
        ],
    )

    _maybe_click_save_in_production(page, log_path)

    try:
        page_url = (page.url or "").strip()
    except Exception:
        page_url = ""

    _append_cpa_log(log_path, "NOTE add_alliance_cpa_insurance finished (browser left open for operator).")
    try:
        sync_uploads_subfolder_to_s3(int(dealer_id), safe_sub)
        sync_ocr_logs_subfolder_to_s3(int(dealer_id), safe_sub)
    except Exception as exc:
        logger.warning("add_alliance_cpa_insurance: S3 sync: %s", exc)

    return {"success": True, "error": None, "page_url": page_url or None, "playwright_log": str(log_path)}
