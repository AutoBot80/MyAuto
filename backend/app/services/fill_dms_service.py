"""
Fill DMS flow using Playwright: login, fill enquiry, search vehicle, scrape row, save PDFs.
Optionally fills dummy Vahan registration and returns application_id and rto_fees.
Uses Chromium (faster launch). Requires: pip install playwright && playwright install chromium.
Uses headed browser by default (set DMS_PLAYWRIGHT_HEADED=false for headless).
Writes pulled data to ocr_output/subfolder/Data from DMS.txt for consistency with other OCR outputs.

**Browser lifetime:** This module never calls ``Browser.close()`` or ``Playwright.stop()`` for operator
sessions (including on API process exit and thread switches). Edge/Chrome stays open for the operator;
stale handles are moved to a retain list so GC does not implicitly close windows.

**JS dialogs:** ``run_fill_dms_only`` installs a per-tab ``dialog`` listener so short-lived Siebel
``alert``/``confirm`` dialogs do not crash the Playwright Node driver (CDP race *No dialog is showing*).
"""
import base64
import difflib
import json
import logging
import os
import re
import urllib.parse
import atexit
import threading
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.config import (
    DMS_PLAYWRIGHT_HEADED,
    DMS_REAL_URL_CONTACT,
    DMS_REAL_URL_ENQUIRY,
    DMS_REAL_URL_LINE_ITEMS,
    DMS_REAL_URL_PDI,
    DMS_REAL_URL_PRECHECK,
    DMS_REAL_URL_REPORTS,
    DMS_REAL_URL_VEHICLE,
    DMS_REAL_URL_VEHICLES,
    DMS_SIEBEL_ACTION_TIMEOUT_MS,
    DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
    DMS_SIEBEL_MOBILE_ARIA_HINTS,
    DMS_SIEBEL_NAV_TIMEOUT_MS,
    INSURANCE_ACTION_TIMEOUT_MS,
    INSURANCE_LOGIN_WAIT_MS,
    INSURANCE_POLICY_FILL_TIMEOUT_MS,
    OCR_OUTPUT_DIR,
    PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
    dms_automation_is_real_siebel,
)
from app.services.siebel_dms_playwright import SiebelDmsUrls, Playwright_Hero_DMS_fill
from app.repositories import form_dms as form_dms_repo
from app.repositories import form_vahan as form_vahan_repo
from app.db import get_connection
from app.services.customer_address_infer import enrich_customer_address_from_freeform

logger = logging.getLogger(__name__)

# 1x1 PNG for dummy insurance KYC uploads (when mobile has no on-file KYC).
_MIN_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

def _insurance_kyc_png_payloads() -> list[dict]:
    return [
        {"name": "aadhar_front.png", "mimeType": "image/png", "buffer": _MIN_PNG_BYTES},
        {"name": "aadhar_rear.png", "mimeType": "image/png", "buffer": _MIN_PNG_BYTES},
        {"name": "customer_photo_aadhar_front.png", "mimeType": "image/png", "buffer": _MIN_PNG_BYTES},
    ]


def _normalize_for_fuzzy_match(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _fuzzy_best_option_label(query: str, candidates: list[str], *, min_score: float = 0.42) -> str | None:
    """
    Pick dropdown option label best matching query (insurer from details sheet / OEM name).
    Uses SequenceMatcher + Jaccard on word tokens + substring boost.
    """
    if not candidates:
        return None
    q = _normalize_for_fuzzy_match(query)
    if not q:
        return candidates[0].strip() or candidates[0]
    q_words = set(w for w in q.split() if len(w) >= 2)
    best_label = (candidates[0] or "").strip()
    best_score = 0.0
    for raw in candidates:
        c = (raw or "").strip()
        if not c:
            continue
        cn = _normalize_for_fuzzy_match(c)
        score = difflib.SequenceMatcher(None, q, cn).ratio()
        c_words = set(w for w in cn.split() if len(w) >= 2)
        if q_words and c_words:
            inter = len(q_words & c_words)
            union = len(q_words | c_words) or 1
            score = max(score, (inter / union) * 0.98)
        # Short brand tokens from detail sheets (e.g. SOMPO → Universal Sompo General Insurance)
        if len(q) >= 3 and (q in cn or cn in q):
            score = max(score, 0.92)
        if len(q) >= 3:
            for w in cn.split():
                if len(w) >= len(q) and q in w:
                    score = max(score, 0.9)
                    break
        if score > best_score:
            best_score = score
            best_label = c
    if best_score < min_score:
        return (candidates[0] or "").strip() or None
    return best_label


def _insurance_select_fuzzy(
    page,
    select_selector: str,
    query: str,
    *,
    timeout_ms: int | None = None,
) -> str | None:
    """Set <select> to option whose label best matches query; returns chosen label or None."""
    if not (query or "").strip():
        return None
    opt_loc = page.locator(f"{select_selector} option")
    labels: list[str] = []
    try:
        raw = opt_loc.evaluate_all(
            "els => els.map(e => (e.textContent || '').trim()).filter(t => t)"
        )
        labels = [str(x).strip() for x in (raw or []) if str(x).strip()]
    except Exception:
        try:
            n = opt_loc.count()
            for i in range(n):
                t = (opt_loc.nth(i).inner_text() or "").strip()
                if t:
                    labels.append(t)
        except Exception:
            labels = []
    if not labels:
        return None
    picked = _fuzzy_best_option_label(query, labels)
    if picked:
        try:
            to = timeout_ms if timeout_ms is not None else INSURANCE_ACTION_TIMEOUT_MS
            page.select_option(select_selector, label=picked, timeout=to)
        except Exception:
            logger.warning("Insurance: select_option failed for %s label=%r", select_selector, picked)
    return picked


_PW = None
_PW_THREAD_ID: int | None = None
_KEEP_OPEN_BROWSERS: list = []
_CDP_BROWSERS_BY_URL: dict[str, object] = {}
# Strong refs so dropped Browser objects are not GC-finalized (which could close Edge/Chrome).
_RETAINED_BROWSERS_NO_CLOSE: list = []
# ``id(page)`` for tabs that already have ``page.on("dialog", ...)`` installed.
_PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES: set[int] = set()


def _install_playwright_js_dialog_handler(page) -> None:
    """
    Siebel / Hero Connect sometimes shows ``alert``/``confirm`` that disappear immediately. Playwright's
    built-in auto-dismiss then races Chromium (``Page.handleJavaScriptDialog``: *No dialog is showing*),
    which can crash the Node driver with an uncaught ``ProtocolError``. A single explicit listener that
    calls ``accept()`` inside try/except avoids that race for this tab.
    """
    pid = id(page)
    if pid in _PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES:
        return
    _PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES.add(pid)

    def _on_dialog(dialog):
        try:
            dialog.accept()
        except Exception as exc:
            logger.debug("fill_dms_service: JS dialog accept skipped (already closed?): %s", exc)

    try:
        page.on("dialog", _on_dialog)
    except Exception as exc:
        _PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES.discard(pid)
        logger.warning("fill_dms_service: could not attach JS dialog handler: %s", exc)


def _retain_browsers_without_closing() -> None:
    """Move tracked browsers to a permanent retain list; never call ``Browser.close()``."""
    for b in list(_KEEP_OPEN_BROWSERS):
        _RETAINED_BROWSERS_NO_CLOSE.append(b)
    _KEEP_OPEN_BROWSERS.clear()
    for b in list(_CDP_BROWSERS_BY_URL.values()):
        _RETAINED_BROWSERS_NO_CLOSE.append(b)
    _CDP_BROWSERS_BY_URL.clear()


def _get_playwright():
    """Persistent Playwright instance; thread-affine — new thread gets a new driver without closing browsers."""
    global _PW, _PW_THREAD_ID
    current_thread_id = threading.get_ident()
    # Playwright sync objects are thread-affine; recreate on thread switch.
    if _PW is not None and _PW_THREAD_ID is not None and _PW_THREAD_ID != current_thread_id:
        _retain_browsers_without_closing()
        # Do not _PW.stop() — that can tear down Playwright-launched browser processes.
        _PW = None
        _PW_THREAD_ID = None
    if _PW is None:
        _PW = sync_playwright().start()
        _PW_THREAD_ID = current_thread_id
    return _PW


@atexit.register
def _preserve_browsers_on_process_exit() -> None:
    """
    Intentionally does not call ``Browser.close()`` or ``Playwright.stop()``.
    Operator Edge/Chrome windows stay open when the API process exits (OS may still reap children).
    """
    return


def _candidate_cdp_urls() -> list[str]:
    urls: list[str] = []
    explicit = (os.getenv("PLAYWRIGHT_CDP_URL") or "").strip()
    if explicit:
        urls.append(explicit)
    explicit_many = (os.getenv("PLAYWRIGHT_CDP_URLS") or "").strip()
    if explicit_many:
        urls.extend([u.strip() for u in explicit_many.split(",") if u.strip()])
    # Same port as app-launched browser (PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT, default 9333).
    if PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT:
        urls.append(f"http://127.0.0.1:{PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT}")
    # Common local CDP endpoints used by operators.
    # Edge/Chrome can both run on any port, but these defaults are typical.
    urls.append("http://127.0.0.1:9222")
    urls.append("http://127.0.0.1:9223")
    # Keep order and uniqueness stable.
    seen: set[str] = set()
    unique_urls: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)
    return unique_urls


def _refresh_cdp_browsers() -> None:
    """Connect to CDP endpoints (if available) so we can inspect existing Edge tabs."""
    pw = _get_playwright()
    for cdp_url in _candidate_cdp_urls():
        existing = _CDP_BROWSERS_BY_URL.get(cdp_url)
        if existing is not None:
            try:
                _ = existing.contexts
                continue
            except Exception:
                _CDP_BROWSERS_BY_URL.pop(cdp_url, None)
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            _CDP_BROWSERS_BY_URL[cdp_url] = browser
            logger.info("fill_dms_service: connected to browser CDP at %s", cdp_url)
        except Exception:
            # Endpoint may not be available; retry on next call.
            continue


def _launch_managed_browser_for_site(base_url: str):
    """Launch Edge/Chrome for operators when no debuggable session is currently available."""
    pw = _get_playwright()
    channels = ["msedge", "chrome"]
    headless = not bool(DMS_PLAYWRIGHT_HEADED)
    launch_args: list[str] = []
    if PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT:
        launch_args.append(f"--remote-debugging-port={PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT}")
    for channel in channels:
        try:
            browser = pw.chromium.launch(
                channel=channel,
                headless=headless,
                args=launch_args if launch_args else [],
            )
            _KEEP_OPEN_BROWSERS.append(browser)
            context = browser.new_context()
            page = context.new_page()
            page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            if PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT:
                logger.info(
                    "fill_dms_service: launched managed %s for %s with remote debugging on port %s "
                    "(set PLAYWRIGHT_CDP_URL=http://127.0.0.1:%s to reuse this window after a backend restart "
                    "if the browser process stays alive).",
                    channel,
                    base_url,
                    PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
                    PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
                )
            else:
                logger.info("fill_dms_service: launched managed %s browser for %s", channel, base_url)
            return page, channel
        except Exception as exc:
            logger.warning("fill_dms_service: failed to launch %s browser: %s", channel, exc)
            continue
    return None, None


def _playwright_page_url_matches_site_base(page_url: str, site_base_url: str) -> bool:
    """
    True if ``page_url`` is the same site as ``site_base_url`` (scheme + host + path prefix).
    Siebel/Hero Connect uses many query variants (``SWECmd=Login``, ``GotoView``, …); matching only
    ``startswith(DMS_BASE_URL)`` can fail when bases differ slightly; host+path prefix is reliable.
    """
    pu = (page_url or "").strip()
    bu = (site_base_url or "").strip()
    if not pu or not bu:
        return False
    low = pu.lower()
    if "blank" in low or low.startswith("chrome://") or low.startswith("edge://") or low.startswith("about:"):
        return False
    try:
        pp = urllib.parse.urlparse(pu)
        bp = urllib.parse.urlparse(bu.strip())
        if not bp.netloc and bu.strip().startswith("//"):
            bp = urllib.parse.urlparse(f"https:{bu.strip()}")
        if not bp.netloc or not pp.netloc:
            return pu.startswith(bu.rstrip("/")) or bu.rstrip("/") in pu
        if pp.netloc.lower() != bp.netloc.lower():
            return False

        def norm_path(path: str) -> str:
            p = (path or "/").rstrip("/")
            return p.lower() if p else ""

        ppath = norm_path(pp.path)
        bpath = norm_path(bp.path)
        if not bpath:
            return True
        if ppath == bpath or ppath.startswith(bpath + "/"):
            return True
        # Base stored as .../enu, page path .../enu (already ==). Extra: .../enu vs .../Enu case
        return False
    except Exception:
        t = bu.rstrip("/")
        return pu.startswith(t) or t in pu


def _get_or_open_site_page(base_url: str, site_label: str, *, require_login_on_open: bool = True):
    """
    Try finding an already-open site tab.
    If not found, open a managed browser tab for operator login and return a guidance error.
    """
    page = _find_open_site_page(base_url)
    if page is not None:
        return page, None

    opened_page, channel = _launch_managed_browser_for_site(base_url)
    if opened_page is not None:
        if not require_login_on_open:
            return opened_page, None
        return None, f"{site_label} Opened. Please login. And then press button again"

    return None, (
        f"{site_label} site not open. Please open {site_label} site and keep it logged in. "
        "Start Edge or Chrome with a remote debugging port (for example 9222), or allow the app "
        "to auto-open one and retry."
    )


def _find_open_site_page(base_url: str):
    """Find an already-open tab for the given site base URL (CDP or same-process Playwright launch)."""
    if not (base_url or "").strip():
        return None
    _refresh_cdp_browsers()
    browsers_to_scan = list(_KEEP_OPEN_BROWSERS) + list(_CDP_BROWSERS_BY_URL.values())
    if not browsers_to_scan:
        logger.warning(
            "fill_dms_service: no browser session for tab reuse — Playwright cannot see a normal Edge/Chrome window. "
            "Start Edge with remote debugging, e.g. "
            '"msedge.exe" --remote-debugging-port=9222 '
            "then set PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222 in backend/.env and restart the API. "
            "Or use the browser opened by Fill DMS (default CDP port %s). "
            "Hero Connect login URLs such as "
            "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/?SWECmd=Login… are matched once CDP works.",
            PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333,
        )
        return None

    sample_urls: list[str] = []
    for browser in browsers_to_scan:
        try:
            for context in browser.contexts:
                for page in context.pages:
                    url = (page.url or "").strip()
                    if len(sample_urls) < 15 and url:
                        sample_urls.append(url[:160])
                    if _playwright_page_url_matches_site_base(url, base_url):
                        logger.info("fill_dms_service: reusing open tab for DMS_BASE_URL: %s", url[:140])
                        return page
        except Exception:
            continue

    logger.warning(
        "fill_dms_service: no tab matched DMS_BASE_URL=%r among %s session(s). Open tab URLs (sample): %s",
        (base_url or "")[:100],
        len(browsers_to_scan),
        sample_urls[:8] or "(none readable)",
    )
    return None


def _requires_operator_create_invoice(page) -> bool:
    """Detect whether the current DMS page is asking operator to click Create Invoice."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"create\s*invoice", re.IGNORECASE))
        if btn.count() > 0 and btn.first.is_visible():
            return True
        line_btn = page.locator("#dms-line-create-invoice")
        if line_btn.count() > 0 and line_btn.first.is_visible():
            return True
    except Exception:
        return False
    return False


def _fill_vahan_and_scrape(
    page,
    vahan_base_url: str,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
) -> tuple[str | None, float]:
    """
    Fill dummy Vahan registration flow, move the file to worklist, and return (application_id, rto_fees).
    rto_fees = 1% of vehicle_price + 200 (same as dummy Vahan formula).
    """
    base = vahan_base_url.rstrip("/")
    effective_vehicle_price = float(vehicle_price or 0)
    if effective_vehicle_price <= 0:
        raise ValueError("form_vahan_view.vehicle_price must be a positive number")
    effective_rto_dealer_id = (rto_dealer_id or "").strip()
    effective_customer_name = (customer_name or "").strip()
    effective_chassis_no = (chassis_no or "").strip()
    effective_vehicle_model = (vehicle_model or "").strip()
    effective_vehicle_colour = (vehicle_colour or "").strip()
    effective_year_of_mfg = (year_of_mfg or "").strip()
    effective_fuel_type = (fuel_type or "").strip()
    required_pairs = [
        ("form_vahan_view.rto_dealer_id", effective_rto_dealer_id),
        ("form_vahan_view.Owner Name *", effective_customer_name),
        ("form_vahan_view.Chassis No *", effective_chassis_no),
        ("form_vahan_view.vehicle_model", effective_vehicle_model),
        ("form_vahan_view.vehicle_colour", effective_vehicle_colour),
        ("form_vahan_view.fuel_type", effective_fuel_type),
        ("form_vahan_view.year_of_mfg", effective_year_of_mfg),
    ]
    missing = [label for label, value in required_pairs if not value]
    if missing:
        raise ValueError("Missing required Vahan DB values: " + ", ".join(missing))
    page.goto(f"{base}/index.html", wait_until="domcontentloaded", timeout=20000)
    page.locator("#vahan-rto-dealer-id").evaluate("(el, value) => el.value = value", effective_rto_dealer_id)
    page.locator("#vahan-customer-name").evaluate("(el, value) => el.value = value", effective_customer_name)
    page.locator("#vahan-chassis-no").evaluate("(el, value) => el.value = value", effective_chassis_no)
    page.locator("#vahan-vehicle-model").evaluate("(el, value) => el.value = value", effective_vehicle_model)
    page.locator("#vahan-vehicle-colour").evaluate("(el, value) => el.value = value", effective_vehicle_colour)
    page.fill("#vahan-chassis-no-visible", effective_chassis_no)
    engine_tail = effective_chassis_no[-5:]
    page.fill("#vahan-engine-last5-visible", engine_tail)
    page.locator("#vahan-fuel-type").evaluate("(el, value) => el.value = value", effective_fuel_type)
    page.locator("#vahan-year-of-mfg").evaluate("(el, value) => el.value = value", effective_year_of_mfg)
    page.locator("#vahan-total-cost").evaluate("(el, value) => el.value = value", str(int(effective_vehicle_price)))
    page.click("#vahan-reg-submit")
    page.wait_for_url("**/application.html*", timeout=15000)
    page.click("#vahan-save-movement")
    page.wait_for_url("**/search.html*", timeout=15000)
    url = page.url
    application_id = None
    if "application_id=" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        ids = qs.get("application_id", [])
        if ids:
            application_id = ids[0]
    # Wait for search result to render (auto-submit on load), then scrape rto_fees
    rto_fees = None
    try:
        page.wait_for_selector("#vahan-result-section:visible", timeout=8000)
        el = page.locator("#vahan-result-rto-fees")
        if el.count() > 0:
            val = el.get_attribute("data-rto-fees")
            if val and val.strip():
                rto_fees = round(float(val), 2)
            else:
                raise ValueError("Vahan result missing data-rto-fees value")
        else:
            raise ValueError("Vahan result row not found for rto_fees scrape")
    except Exception:
        if rto_fees is None:
            raise
    if rto_fees is None:
        raise ValueError("Vahan rto_fees could not be scraped")
    return (application_id, rto_fees)


def _complete_vahan_upload_step(page) -> bool:
    """Advance the dummy Vahan flow to the files-uploaded / cart checkpoint."""
    page.click("#vahan-upload-btn")
    page.wait_for_selector("#vahan-upload-status[data-uploaded='1']", timeout=8000)
    return True


def _split_name(full_name: str | None) -> tuple[str, str]:
    if not full_name or not full_name.strip():
        return "", ""
    parts = full_name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0], "")


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name (one segment) for ocr_output and uploads."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _write_data_from_dms(ocr_output_dir: Path, subfolder: str, customer: dict, vehicle: dict) -> None:
    """Write all pulled DMS data to ocr_output/subfolder/Data from DMS.txt (subfolder = mobile_ddmmyy)."""
    safe_name = _safe_subfolder_name(subfolder)
    base = Path(ocr_output_dir).resolve()
    dir_path = base / safe_name
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / "Data from DMS.txt"
    lines = ["Data from DMS", ""]

    lines.append("--- Customer (filled on enquiry) ---")
    for label, key in [
        ("Name", "name"),
        ("Address", "address"),
        ("State", "state"),
        ("Pin code", "pin_code"),
    ]:
        val = customer.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")
    mobile = customer.get("mobile_number") or customer.get("mobile")
    lines.append(f"Mobile: {mobile or '—'}")
    rel = customer.get("relation_prefix") or customer.get("dms_relation_prefix")
    if rel:
        lines.append(f"Relation (S/O or W/o): {rel}")
    fath = customer.get("care_of") or customer.get("father_or_husband_name")
    if fath:
        lines.append(f"Care of / Father–Husband (Aadhaar QR): {fath}")

    lines.append("")
    lines.append("--- Vehicle (from DMS search result) ---")
    for label, key in [
        ("Key num", "key_num"),
        ("Frame / Chassis num", "frame_num"),
        ("Engine num", "engine_num"),
        ("Model", "model"),
        ("Color", "color"),
        ("Cubic capacity", "cubic_capacity"),
        ("Seating capacity", "seating_capacity"),
        ("Body type", "body_type"),
        ("Vehicle type", "vehicle_type"),
        ("Num cylinders", "num_cylinders"),
        ("Horsepower", "horse_power"),
        ("Ex-showroom Price (Order Value)", "vehicle_price"),
        ("Year of Mfg", "year_of_mfg"),
    ]:
        val = vehicle.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


# Dummy DMS booking amount (enquiry customer budget) — must match business test default.
DMS_DUMMY_ENQUIRY_BUDGET = "89000"

# UI checklist order (Add Sales banner). Labels must match exactly for sorting.
DMS_MILESTONE_ORDER: tuple[str, ...] = (
    "Customer found",
    "All Enquiries opened",
    "Care of filled",
    "Enquiry created",
    "Booking generated",
    "Vehicle received",
    "Pre check completed",
    "Vehicle inspection done",
    "Vehicle allocated",
    "Allotment view opened",
    "Invoice created",
)


def _dms_milestone(result: dict, label: str) -> None:
    m = result.setdefault("dms_milestones", [])
    if label not in m:
        m.append(label)


def _sort_dms_milestones(result: dict) -> None:
    m = list(result.get("dms_milestones") or [])
    order = {k: i for i, k in enumerate(DMS_MILESTONE_ORDER)}
    result["dms_milestones"] = sorted(m, key=lambda x: order.get(x, 99))


def _fill_playwright_enquiry_contact(page, dms_values: dict) -> None:
    row = dms_values.get("row") or {}
    mr_ms = _clean_text(row.get("Mr/Ms"))
    if mr_ms:
        try:
            page.select_option("#dms-mr-ms", label=mr_ms)
        except Exception:
            try:
                page.select_option("#dms-mr-ms", value=mr_ms.rstrip("."))
            except Exception:
                pass
    page.fill("#dms-contact-first-name", dms_values["first_name"])
    page.fill("#dms-contact-last-name", dms_values["last_name"])
    page.fill("#dms-mobile-phone", dms_values["mobile_phone"])
    landline = dms_values.get("landline") or ""
    if landline:
        page.fill("#dms-landline", landline)
    addr = dms_values["address_line_1"]
    if addr:
        page.fill("#dms-address-line-1", addr)
    state = dms_values["state"]
    if state:
        try:
            page.select_option("#dms-state", label=state)
        except Exception:
            try:
                page.select_option("#dms-state", value=state)
            except Exception:
                pass
    pin = dms_values["pin_code"]
    if pin:
        page.fill("#dms-pin-code", pin)


def _apply_playwright_enquiry_relation_finance(page, dms_values: dict) -> None:
    relation = dms_values.get("relation_prefix") or ""
    if relation:
        try:
            page.select_option("#dms-relation-prefix", value=relation)
        except Exception:
            try:
                page.select_option("#dms-relation-prefix", label=relation)
            except Exception:
                logger.warning("fill_dms_service: could not set DMS relation %s", relation)
    father = dms_values.get("father_husband_name") or ""
    if father:
        page.fill("#dms-father-husband-name", father[:255])
    finance_required = (dms_values.get("finance_required") or "N").strip().upper()
    if finance_required == "Y":
        try:
            page.select_option("#dms-finance-required", value="Y")
        except Exception:
            pass
        fin = dms_values.get("financier_name") or ""
        if fin:
            try:
                page.fill("#dms-financier-name", fin[:255])
            except Exception:
                pass


def _scrape_dms_vehicle_search_row(page) -> dict:
    page.wait_for_selector("#dms-vehicle-results:visible", timeout=8000)
    row = page.locator("#dms-vehicle-results-table tbody tr").first
    if row.count() == 0:
        return {}
    cells = row.locator("td")
    n = cells.count()
    if n < 13:
        return {}
    ex_show = cells.nth(11).inner_text().strip()
    return {
        "key_num": cells.nth(0).inner_text().strip(),
        "frame_num": cells.nth(1).inner_text().strip(),
        "engine_num": cells.nth(2).inner_text().strip(),
        "model": cells.nth(3).inner_text().strip(),
        "color": cells.nth(4).inner_text().strip(),
        "cubic_capacity": cells.nth(5).inner_text().strip(),
        "seating_capacity": cells.nth(6).inner_text().strip(),
        "body_type": cells.nth(7).inner_text().strip(),
        "vehicle_type": cells.nth(8).inner_text().strip(),
        "num_cylinders": cells.nth(9).inner_text().strip(),
        "horse_power": cells.nth(10).inner_text().strip(),
        "vehicle_price": ex_show,
        "ex_showroom_price": ex_show,
        "year_of_mfg": cells.nth(12).inner_text().strip(),
    }


def _parse_vehicle_price(vehicle: dict) -> float:
    """Parse vehicle_price from vehicle (e.g. '72000' or '72,000') for Vahan automation."""
    raw = vehicle.get("vehicle_price")
    if raw is None:
        raw = vehicle.get("total_amount")
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_amount(value: object | None) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value).strip()


def _parse_float_or_zero(value: object | None) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _require_customer_vehicle_ids(customer_id: int | None, vehicle_id: int | None, view_name: str) -> tuple[int, int]:
    if customer_id is None or vehicle_id is None:
        raise ValueError(f"customer_id and vehicle_id are required because automation now reads from {view_name} only")
    return customer_id, vehicle_id


def _load_form_vahan_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    if customer_id is None or vehicle_id is None:
        return {}
    try:
        return form_vahan_repo.get_by_customer_vehicle(customer_id, vehicle_id) or {}
    except Exception as exc:
        logger.warning(
            "fill_dms_service: form_vahan_view lookup failed customer_id=%s vehicle_id=%s: %s",
            customer_id,
            vehicle_id,
            exc,
        )
        return {}


def _load_required_form_vahan_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "form_vahan_view")
    row = _load_form_vahan_row(cid, vid)
    if not row:
        raise ValueError(f"No form_vahan_view row found for customer_id={cid} vehicle_id={vid}")
    return row


def _load_form_dms_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    if customer_id is None or vehicle_id is None:
        return {}
    try:
        return form_dms_repo.get_by_customer_vehicle(customer_id, vehicle_id) or {}
    except Exception as exc:
        logger.warning(
            "fill_dms_service: form_dms_view lookup failed customer_id=%s vehicle_id=%s: %s",
            customer_id,
            vehicle_id,
            exc,
        )
        return {}


def _load_required_form_dms_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "form_dms_view")
    row = _load_form_dms_row(cid, vid)
    if not row:
        raise ValueError(f"No form_dms_view row found for customer_id={cid} vehicle_id={vid}")
    return row


def _normalize_dms_relation_prefix(raw: object | None) -> str:
    s = _clean_text(raw).upper().replace(" ", "")
    if s in ("S/O", "SO"):
        return "S/O"
    if s in ("W/O", "WO", "W/O."):
        return "W/o"
    if "W" in s and "O" in s:
        return "W/o"
    if "S" in s and "O" in s:
        return "S/O"
    return _clean_text(raw)


def _build_dms_fill_values(customer_id: int | None, vehicle_id: int | None, subfolder: str | None = None) -> dict:
    row = _load_required_form_dms_row(customer_id, vehicle_id)
    addr_full = _clean_text(row.get("Address Line 1"))
    pin_raw = _clean_text(row.get("Pin Code"))[:6]
    state_raw = _clean_text(row.get("State"))
    father_raw = _clean_text(row.get("Father or Husband Name"))
    inferred_addr = enrich_customer_address_from_freeform(
        {
            "address": addr_full,
            "pin": pin_raw,
            "state": state_raw,
            "care_of": father_raw,
        }
    )
    pin_e = _clean_text(inferred_addr.get("pin"))[:6] or pin_raw
    state_e = _clean_text(inferred_addr.get("state")) or state_raw
    addr_line = _clean_text(inferred_addr.get("address"))[:80] or addr_full[:80]
    father_e = _clean_text(inferred_addr.get("care_of"))[:255] or father_raw[:255]
    first_name = _clean_text(row.get("Contact First Name"))
    last_name = _clean_text(row.get("Contact Last Name"))
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    effective_subfolder = _clean_text(row.get("subfolder")) or _clean_text(subfolder)
    relation_raw = row.get("Relation (S/O or W/o)")
    relation_prefix = _normalize_dms_relation_prefix(relation_raw) if _clean_text(relation_raw) else ""
    contact_path = (_clean_text(row.get("DMS Contact Path")) or "found").lower()
    if contact_path not in ("found", "new_enquiry", "skip_find"):
        contact_path = "found"
    finance_required = (_clean_text(row.get("Finance Required")) or "N").upper()
    if finance_required not in ("Y", "N"):
        finance_required = "N"
    values = {
        "row": row,
        "subfolder": effective_subfolder,
        "customer_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "mobile_phone": _clean_text(row.get("Mobile Phone #"))[:10],
        "landline": _clean_text(row.get("Landline #"))[:16],
        "address_line_1": addr_line,
        "state": state_e,
        "pin_code": pin_e,
        "key_partial": _clean_text(row.get("Key num (partial)"))[:8],
        "frame_partial": _clean_text(row.get("Frame / Chassis num (partial)"))[:12],
        "engine_partial": _clean_text(row.get("Engine num (partial)"))[:12],
        "relation_prefix": relation_prefix,
        "father_husband_name": father_e,
        "financier_name": _clean_text(row.get("Financier Name"))[:255],
        "finance_required": finance_required,
        "dms_contact_path": contact_path,
        "customer_export": {
            "name": full_name,
            "address": _clean_text(inferred_addr.get("address")) or addr_full,
            "state": state_e,
            "pin_code": pin_e,
            "mobile_number": _clean_text(row.get("Mobile Phone #")),
            "alt_phone_num": _clean_text(row.get("Landline #")),
            "relation_prefix": relation_prefix,
            "care_of": father_e,
            "father_or_husband_name": father_e,
            "finance_required": finance_required,
            "financier_name": _clean_text(row.get("Financier Name")),
        },
    }
    required_keys = [
        ("form_dms_view.Contact First Name", values["first_name"]),
        ("form_dms_view.Mobile Phone #", values["mobile_phone"]),
        ("form_dms_view.State", values["state"]),
        ("form_dms_view.Address Line 1", values["address_line_1"]),
        ("form_dms_view.Pin Code", values["pin_code"]),
        ("form_dms_view.Key num (partial)", values["key_partial"]),
        ("form_dms_view.Frame / Chassis num (partial)", values["frame_partial"]),
        ("form_dms_view.Engine num (partial)", values["engine_partial"]),
    ]
    missing = [label for label, val in required_keys if not val]
    if missing:
        raise ValueError("Missing required DMS DB values: " + ", ".join(missing))
    return values


def _build_vahan_fill_values(customer_id: int | None, vehicle_id: int | None, subfolder: str | None = None) -> dict:
    row = _load_required_form_vahan_row(customer_id, vehicle_id)
    vehicle_price = _parse_float_or_zero(row.get("vehicle_price"))
    if vehicle_price <= 0:
        raise ValueError(
            f"form_vahan_view.vehicle_price is empty for customer_id={customer_id} vehicle_id={vehicle_id}; "
            "run DMS first so vehicle_price is stored in vehicle_master"
        )
    effective_subfolder = _clean_text(row.get("subfolder")) or _clean_text(subfolder)
    values = {
        "row": row,
        "subfolder": effective_subfolder,
        "rto_dealer_id": _clean_text(row.get("rto_dealer_id")),
        "customer_name": _clean_text(row.get("Owner Name *")),
        "chassis_no": _clean_text(row.get("Chassis No *")),
        "vehicle_model": _clean_text(row.get("vehicle_model")),
        "vehicle_colour": _clean_text(row.get("vehicle_colour")),
        "fuel_type": _clean_text(row.get("fuel_type")),
        "year_of_mfg": _clean_text(row.get("year_of_mfg")),
        "vehicle_price": vehicle_price,
    }
    required_keys = [
        ("form_vahan_view.rto_dealer_id", values["rto_dealer_id"]),
        ("form_vahan_view.Owner Name *", values["customer_name"]),
        ("form_vahan_view.Chassis No *", values["chassis_no"]),
        ("form_vahan_view.vehicle_model", values["vehicle_model"]),
        ("form_vahan_view.vehicle_colour", values["vehicle_colour"]),
        ("form_vahan_view.fuel_type", values["fuel_type"]),
        ("form_vahan_view.year_of_mfg", values["year_of_mfg"]),
    ]
    missing = [label for label, val in required_keys if not val]
    if missing:
        raise ValueError("Missing required Vahan DB values: " + ", ".join(missing))
    return values


def _write_dms_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    customer_name: str,
    mobile_number: str,
    alt_phone_num: str,
    address: str,
    state: str,
    pin_code: str,
    key_no: str,
    frame_no: str,
    engine_no: str,
    relation_prefix: str = "",
    father_husband_name: str = "",
    customer_budget: str = "",
    finance_required: str = "",
    financier_name: str = "",
    dms_contact_path: str = "",
) -> None:
    if not subfolder or not str(subfolder).strip():
        return

    row = _load_form_dms_row(customer_id, vehicle_id)
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "DMS_Form_Values.txt"

    row_first_name = _clean_text(row.get("Contact First Name"))
    row_last_name = _clean_text(row.get("Contact Last Name"))
    first_name, last_name = _split_name(customer_name or "")
    effective_first_name = _clean_text(first_name) or row_first_name
    effective_last_name = _clean_text(last_name) or row_last_name
    effective_mobile = _clean_text(mobile_number)[:10] or _clean_text(row.get("Mobile Phone #"))[:10]
    effective_landline = _clean_text(alt_phone_num)[:16] or _clean_text(row.get("Landline #"))[:16]
    effective_address = _clean_text(address)[:80] or _clean_text(row.get("Address Line 1"))[:80]
    effective_state = _clean_text(state) or _clean_text(row.get("State"))
    effective_pin = _clean_text(pin_code)[:6] or _clean_text(row.get("Pin Code"))[:6]
    effective_key = _clean_text(key_no)[:8] or _clean_text(row.get("Key num (partial)"))
    effective_frame = _clean_text(frame_no)[:12] or _clean_text(row.get("Frame / Chassis num (partial)"))
    effective_engine = _clean_text(engine_no)[:12] or _clean_text(row.get("Engine num (partial)"))
    effective_relation = _clean_text(relation_prefix) or _clean_text(row.get("Relation (S/O or W/o)"))
    effective_father = _clean_text(father_husband_name) or _clean_text(row.get("Father or Husband Name"))
    effective_budget = _clean_text(customer_budget)
    effective_fin_req = _clean_text(finance_required) or _clean_text(row.get("Finance Required")) or "N"
    effective_financier = _clean_text(financier_name) or _clean_text(row.get("Financier Name"))
    effective_path = _clean_text(dms_contact_path) or _clean_text(row.get("DMS Contact Path")) or "found"

    label_values: list[tuple[str, str]] = [
        ("Mr/Ms", _clean_text(row.get("Mr/Ms")) or "Mr."),
        ("Contact First Name", effective_first_name),
        ("Contact Last Name", effective_last_name),
        ("Mobile Phone #", effective_mobile),
        ("Landline #", effective_landline),
        ("State", effective_state),
        ("Address Line 1", effective_address),
        ("Pin Code", effective_pin),
        ("Relation (S/O or W/o)", effective_relation),
        ("Father or Husband Name", effective_father),
        ("Customer Budget (dummy enquiry)", effective_budget),
        ("Finance Required", effective_fin_req),
        ("Financier Name", effective_financier),
        ("DMS Contact Path", effective_path),
        ("Key num (partial)", effective_key),
        ("Frame / Chassis num (partial)", effective_frame),
        ("Engine num (partial)", effective_engine),
    ]

    runtime_values: list[tuple[str, str]] = [
        ("sales_id", _clean_text(row.get("sales_id"))),
        ("customer_id", _clean_text(customer_id or row.get("customer_id"))),
        ("vehicle_id", _clean_text(vehicle_id or row.get("vehicle_id"))),
        ("dealer_id", _clean_text(row.get("dealer_id"))),
        ("subfolder", safe_subfolder),
        ("dealer_name", _clean_text(row.get("dealer_name"))),
        ("oem_name", _clean_text(row.get("oem_name"))),
        ("source_customer_name", _clean_text(customer_name)),
        ("source_mobile_number", _clean_text(mobile_number)),
        ("source_alt_phone_num", _clean_text(alt_phone_num)),
        ("source_address", _clean_text(address)),
        ("source_state", _clean_text(state)),
        ("source_pin_code", _clean_text(pin_code)),
        ("source_key_no", _clean_text(key_no)),
        ("source_frame_no", _clean_text(frame_no)),
        ("source_engine_no", _clean_text(engine_no)),
        ("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]

    lines = ["DMS Form Values", "", "--- Values sent to DMS labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")

    lines.extend(["", "--- Runtime values used by Playwright ---"])
    for label, value in runtime_values:
        lines.append(f"{label}: {value or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_vahan_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
    application_id: str | None,
    rto_fees: float | None,
) -> None:
    if not subfolder or not str(subfolder).strip():
        return

    row = _load_form_vahan_row(customer_id, vehicle_id)
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "Vahan_Form_Values.txt"

    effective_rto_dealer_id = _clean_text(rto_dealer_id) or _clean_text(row.get("rto_dealer_id"))
    effective_customer_name = _clean_text(customer_name) or _clean_text(row.get("Owner Name *"))
    effective_chassis_no = _clean_text(chassis_no) or _clean_text(row.get("Chassis No *"))
    effective_vehicle_model = _clean_text(vehicle_model) or _clean_text(row.get("vehicle_model"))
    effective_vehicle_colour = _clean_text(vehicle_colour) or _clean_text(row.get("vehicle_colour"))
    effective_fuel_type = _clean_text(fuel_type) or _clean_text(row.get("fuel_type"))
    effective_year_of_mfg = _clean_text(year_of_mfg) or _clean_text(row.get("year_of_mfg"))
    effective_vehicle_price = float(vehicle_price or 0)
    if effective_vehicle_price <= 0:
        raise ValueError("vehicle_price must be positive for Vahan form values export")

    label_values: list[tuple[str, str]] = [
        ("Registration Type *", _clean_text(row.get("Registration Type *"))),
        ("Chassis No *", effective_chassis_no),
        ("Engine/Motor No (Last 5 Chars)", effective_chassis_no[-5:] if effective_chassis_no else ""),
        ("Purchase Delivery Date", _clean_text(row.get("Purchase Delivery Date"))),
        ("Do You want to Opt Choice Number / Fancy Number / Retention Number", _clean_text(row.get("Do You want to Opt Choice Number / Fancy Number / Retention Number"))),
        ("Owner Name *", effective_customer_name),
        ("Owner Type", _clean_text(row.get("Owner Type"))),
        ("Son/Wife/Daughter of", _clean_text(row.get("Son/Wife/Daughter of"))),
        ("Ownership Serial", _clean_text(row.get("Ownership Serial"))),
        ("Aadhaar Mode", _clean_text(row.get("Aadhaar Mode"))),
        ("Category *", _clean_text(row.get("Category *"))),
        ("Mobile No", _clean_text(row.get("Mobile No"))),
        ("PAN Card", _clean_text(row.get("PAN Card"))),
        ("Voter ID", _clean_text(row.get("Voter ID"))),
        ("Aadhaar No", _clean_text(row.get("Aadhaar No"))),
        ("Permanent Address", _clean_text(row.get("Permanent Address"))),
        ("House No & Street Name", _clean_text(row.get("House No & Street Name"))),
        ("Village/Town/City", _clean_text(row.get("Village/Town/City"))),
        ("Insurance Type", _clean_text(row.get("Insurance Type"))),
        ("Insurer", _clean_text(row.get("Insurer"))),
        ("Policy No", _clean_text(row.get("Policy No"))),
        ("Insurance From (DD-MMM-YYYY)", _clean_text(row.get("Insurance From (DD-MMM-YYYY)"))),
        ("Insurance Upto (DD-MMM-YYYY)", _clean_text(row.get("Insurance Upto (DD-MMM-YYYY)"))),
        ("Insured Declared Value", _clean_text(row.get("Insured Declared Value"))),
        ("Please Select Series Type", _clean_text(row.get("Please Select Series Type"))),
        ("Financier / Bank", _clean_text(row.get("Financier / Bank"))),
        ("Application No", _clean_text(application_id) or _clean_text(row.get("Application No"))),
        ("Assigned Office & Action", _clean_text(row.get("Assigned Office & Action")) or effective_rto_dealer_id),
        ("Registration No", _clean_text(row.get("Registration No"))),
        ("Amount", _format_amount(rto_fees) or _clean_text(row.get("Amount"))),
    ]

    runtime_values: list[tuple[str, str]] = [
        ("sales_id", _clean_text(row.get("sales_id"))),
        ("customer_id", _clean_text(customer_id or row.get("customer_id"))),
        ("vehicle_id", _clean_text(vehicle_id or row.get("vehicle_id"))),
        ("dealer_id", _clean_text(row.get("dealer_id"))),
        ("subfolder", safe_subfolder),
        ("rto_dealer_id", effective_rto_dealer_id),
        ("vehicle_model", effective_vehicle_model),
        ("vehicle_colour", effective_vehicle_colour),
        ("fuel_type", effective_fuel_type),
        ("year_of_mfg", effective_year_of_mfg),
        ("vehicle_price", _format_amount(effective_vehicle_price)),
        ("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]

    lines = ["Vahan Form Values", "", "--- Values sent to Vahan labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")

    lines.extend(["", "--- Runtime values used by Playwright ---"])
    for label, value in runtime_values:
        lines.append(f"{label}: {value or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _load_latest_insurance_values(customer_id: int, vehicle_id: int) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cm.customer_id,
                    vm.vehicle_id,
                    COALESCE(cm.name, '') AS customer_name,
                    COALESCE(cm.gender, '') AS gender,
                    COALESCE(TRIM(cm.date_of_birth), '') AS dob,
                    COALESCE(cm.marital_status, '') AS marital_status,
                    COALESCE(cm.profession, '') AS profession,
                    COALESCE(cm.mobile_number::text, '') AS mobile_number,
                    COALESCE(cm.alt_phone_num, '') AS alt_phone_num,
                    COALESCE(cm.state, '') AS state,
                    COALESCE(cm.city, '') AS city,
                    COALESCE(cm.pin::text, '') AS pin_code,
                    COALESCE(cm.address, '') AS address,
                    COALESCE(vm.chassis, vm.raw_frame_num, '') AS frame_no,
                    COALESCE(vm.engine, vm.raw_engine_num, '') AS engine_no,
                    COALESCE(vm.model, '') AS model_name,
                    COALESCE(vm.fuel_type, '') AS fuel_type,
                    COALESCE(vm.year_of_mfg::text, '') AS year_of_mfg,
                    COALESCE(vm.vehicle_price::text, '') AS vehicle_price,
                    COALESCE(NULLIF(TRIM(vm.oem_name), ''), oem_dealer.oem_name, '') AS oem_name,
                    COALESCE(cm.nominee_gender, '') AS nominee_gender,
                    COALESCE(cm.financier, '') AS financer_name,
                    COALESCE(dr.rto_name, '') AS rto_name,
                    COALESCE(im.insurer, '') AS insurer,
                    COALESCE(im.nominee_name, '') AS nominee_name,
                    COALESCE(im.nominee_age::text, '') AS nominee_age,
                    COALESCE(im.nominee_relationship, '') AS nominee_relationship
                FROM customer_master cm
                JOIN vehicle_master vm ON vm.vehicle_id = %s
                LEFT JOIN sales_master sm
                  ON sm.customer_id = cm.customer_id
                 AND sm.vehicle_id = vm.vehicle_id
                LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                LEFT JOIN oem_ref oem_dealer ON oem_dealer.oem_id = dr.oem_id
                LEFT JOIN LATERAL (
                    SELECT *
                    FROM insurance_master im2
                    WHERE im2.customer_id = cm.customer_id
                      AND im2.vehicle_id = vm.vehicle_id
                    ORDER BY im2.policy_to DESC NULLS LAST, im2.insurance_year DESC NULLS LAST, im2.insurance_id DESC
                    LIMIT 1
                ) im ON TRUE
                WHERE cm.customer_id = %s
                LIMIT 1
                """,
                (vehicle_id, customer_id),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def _read_insurance_insurer_from_ocr_json(ocr_output_dir: Path | None, subfolder: str | None) -> str:
    """Fallback: insurer from Details sheet in OCR_To_be_Used.json when insurance_master.insurer is empty."""
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        return ""
    safe = _safe_subfolder_name(subfolder)
    path = Path(ocr_output_dir).resolve() / safe / "OCR_To_be_Used.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ins = data.get("insurance") if isinstance(data.get("insurance"), dict) else {}
        return _clean_text((ins or {}).get("insurer"))
    except Exception as exc:
        logger.debug("Insurance: could not read insurer from %s: %s", path, exc)
        return ""


def _build_insurance_fill_values(
    customer_id: int | None,
    vehicle_id: int | None,
    subfolder: str | None = None,
    ocr_output_dir: Path | None = None,
) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "customer/vehicle/insurance tables")
    row = _load_latest_insurance_values(cid, vid)
    if not row:
        raise ValueError(f"No insurance/customer data found for customer_id={cid} vehicle_id={vid}")
    insurer_db = _clean_text(row.get("insurer"))
    insurer_json = _read_insurance_insurer_from_ocr_json(ocr_output_dir, subfolder)
    insurer_effective = insurer_db or insurer_json
    values = {
        "subfolder": _clean_text(subfolder),
        "insurer": insurer_effective,
        "mobile_number": _clean_text(row.get("mobile_number"))[:10],
        "alt_phone_num": _clean_text(row.get("alt_phone_num"))[:16],
        "customer_name": _clean_text(row.get("customer_name")),
        "gender": _clean_text(row.get("gender")),
        "dob": _clean_text(row.get("dob")),
        "marital_status": _clean_text(row.get("marital_status")),
        "profession": _clean_text(row.get("profession")),
        "state": _clean_text(row.get("state")),
        "city": _clean_text(row.get("city")),
        "pin_code": _clean_text(row.get("pin_code"))[:6],
        "address": _clean_text(row.get("address")),
        "frame_no": _clean_text(row.get("frame_no")),
        "engine_no": _clean_text(row.get("engine_no")),
        "model_name": _clean_text(row.get("model_name")),
        "fuel_type": _clean_text(row.get("fuel_type")),
        "year_of_mfg": _clean_text(row.get("year_of_mfg")),
        "vehicle_price": _clean_text(row.get("vehicle_price")),
        "oem_name": _clean_text(row.get("oem_name")),
        "rto_name": _clean_text(row.get("rto_name")),
        "nominee_name": _clean_text(row.get("nominee_name")),
        "nominee_age": _clean_text(row.get("nominee_age")),
        "nominee_relationship": _clean_text(row.get("nominee_relationship")),
        "nominee_gender": _clean_text(row.get("nominee_gender")),
        "financer_name": _clean_text(row.get("financer_name")),
    }
    required = [
        ("insurance_master.insurer", values["insurer"]),
        ("customer_master.mobile_number", values["mobile_number"]),
        ("customer_master.name", values["customer_name"]),
        ("vehicle_master.chassis", values["frame_no"]),
        ("vehicle_master.engine", values["engine_no"]),
    ]
    missing = [label for label, val in required if not val]
    if missing:
        raise ValueError("Missing required Insurance DB values: " + ", ".join(missing))
    if insurer_json and not insurer_db:
        logger.info(
            "Insurance: using insurer from OCR JSON (%r); insurance_master had no insurer",
            insurer_json[:80],
        )
    return values


def _write_insurance_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    values: dict,
) -> None:
    if not subfolder or not str(subfolder).strip():
        return
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "Insurance_Form_Values.txt"
    label_values: list[tuple[str, str]] = [
        ("Insurance Company (fuzzy-matched to details insurer)", _clean_text(values.get("insurer"))),
        ("Manufacturer / OEM (vehicle_master.oem_name or dealer oem_ref)", _clean_text(values.get("oem_name"))),
        ("Mobile No.", _clean_text(values.get("mobile_number"))),
        ("Alternate / Landline No.", _clean_text(values.get("alt_phone_num"))),
        ("Proposer Name", _clean_text(values.get("customer_name"))),
        ("Gender", _clean_text(values.get("gender"))),
        ("Date of Birth", _clean_text(values.get("dob"))),
        ("Marital Status", _clean_text(values.get("marital_status"))),
        ("Occupation Type", _clean_text(values.get("profession"))),
        ("Proposer State", _clean_text(values.get("state"))),
        ("Proposer City", _clean_text(values.get("city"))),
        ("Pin Code", _clean_text(values.get("pin_code"))),
        ("Address", _clean_text(values.get("address"))),
        ("VIN / Frame No. (Chassis)", _clean_text(values.get("frame_no"))),
        ("Engine No.", _clean_text(values.get("engine_no"))),
        ("Model Name", _clean_text(values.get("model_name"))),
        ("Fuel Type", _clean_text(values.get("fuel_type"))),
        ("Year of Manufacture", _clean_text(values.get("year_of_mfg"))),
        ("Ex-Showroom (DMS cost)", _clean_text(values.get("vehicle_price"))),
        ("RTO", _clean_text(values.get("rto_name"))),
        ("Nominee Name", _clean_text(values.get("nominee_name"))),
        ("Nominee Age", _clean_text(values.get("nominee_age"))),
        ("Relation", _clean_text(values.get("nominee_relationship"))),
        ("Nominee Gender", _clean_text(values.get("nominee_gender"))),
        ("Financer Name", _clean_text(values.get("financer_name"))),
    ]
    lines = ["Insurance Form Values", "", "--- Values sent to Insurance labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")
    lines.extend(
        [
            "",
            "--- Runtime values used by Playwright ---",
            f"customer_id: {customer_id or '—'}",
            f"vehicle_id: {vehicle_id or '—'}",
            f"subfolder: {safe_subfolder}",
            f"generated_at: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _insurance_kyc_screen_ready_js() -> str:
    """Predicate run in the browser: true when KYC step is shown (dummy or typical MISP URLs)."""
    return """() => {
      const u = (window.location.href || '').toLowerCase();
      if (u.includes('policy.html') || u.includes('misppolicy')) return false;
      if (u.includes('kyc.html') || u.includes('ekycpage') || u.includes('kycpage.aspx') || u.includes('/ekyc')) return true;
      const el = document.querySelector('#ins-mobile-no');
      return !!(el && el.offsetParent !== null && el.offsetWidth > 0);
    }"""


def _wait_for_insurance_kyc_after_login(page, insurance_base_url: str) -> str | None:
    """
    Land on the insurance login page if needed, then wait until the operator has signed in
    and the portal shows the KYC step (URL or #ins-mobile-no on dummy).
    Returns an error message, or None on success.
    """
    base = (insurance_base_url or "").rstrip("/")
    if not base:
        return "insurance_base_url required"

    try:
        page.wait_for_timeout(120)
        if page.evaluate(_insurance_kyc_screen_ready_js()):
            return None
    except Exception:
        pass

    logger.info(
        "Insurance: login page — sign in and submit; waiting up to %s ms for KYC screen",
        INSURANCE_LOGIN_WAIT_MS,
    )
    try:
        page.goto(f"{base}/", wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("Insurance: goto %s/: %s", base, exc)
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc2:
            logger.warning("Insurance: goto %s: %s", base, exc2)

    try:
        page.wait_for_function(_insurance_kyc_screen_ready_js(), timeout=INSURANCE_LOGIN_WAIT_MS)
    except PlaywrightTimeout:
        return (
            "Insurance: timed out waiting for the KYC screen after login. "
            "On the login page, enter User ID and Password and click Login (dummy), or complete sign-in on the real portal "
            f"so KYC opens — then press Fill Insurance again (wait limit {INSURANCE_LOGIN_WAIT_MS // 1000}s)."
        )
    return None


def run_fill_insurance_only(
    insurance_base_url: str,
    *,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    ocr_output_dir: Path | None = None,
) -> dict:
    """
    Fill Insurance screens using DB-backed values only.
    Flow: open **login** (index) → operator signs in → wait for **KYC** → fill mobile → **Verify mobile** →
    if `need_docs`, attach three files → **Submit** (or legacy Proceed) → kyc-success → DMS entry → policy details.
    If KYC already on file for the mobile, consent + **Proceed** only.
    Uses ``require_login_on_open=False`` so one Fill Insurance run can wait for manual login (see INSURANCE_LOGIN_WAIT_MS).
    Important behavior:
    - do not click final submit/issue button on the policy page
    - keep browser/tab open for operator review
    """
    result: dict = {"success": False, "error": None}
    if not insurance_base_url or not insurance_base_url.strip():
        result["error"] = "insurance_base_url required"
        return result
    try:
        values = _build_insurance_fill_values(
            customer_id, vehicle_id, subfolder, ocr_output_dir=ocr_output_dir
        )
        page, open_error = _get_or_open_site_page(
            insurance_base_url, "Insurance", require_login_on_open=False
        )
        if page is None:
            result["error"] = open_error
            return result

        base = insurance_base_url.rstrip("/")
        # Snappier than global DMS default; override with INSURANCE_ACTION_TIMEOUT_MS / INSURANCE_POLICY_FILL_TIMEOUT_MS.
        page.set_default_timeout(INSURANCE_ACTION_TIMEOUT_MS)
        wait_err = _wait_for_insurance_kyc_after_login(page, insurance_base_url)
        if wait_err:
            result["error"] = wait_err
            return result
        # Dummy site only: login lands on kyc.html; if predicate matched via URL pattern only, still ok.
        # Real MISP stays on ekycpage.aspx — do not force /kyc.html.
        if "dummy-insurance" in base.lower() and "kyc.html" not in (page.url or "").lower():
            try:
                page.goto(f"{base}/kyc.html", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                logger.warning("Insurance: could not navigate to dummy kyc.html")
        # Insurance company: fuzzy-match details-sheet insurer (`insurance_master.insurer`) to site options.
        _insurance_select_fuzzy(page, "#ins-company", values["insurer"] or "")
        page.select_option("#ins-kyc-partner", label="Signzy")
        # Proposer type / policy tenure: leave site defaults (Individual, etc.).
        page.select_option("#ins-ovd-type", label="AADHAAR EXTRACTION")
        page.fill("#ins-mobile-no", values["mobile_number"] or "")
        if values.get("alt_phone_num"):
            try:
                page.fill("#ins-alt-phone", values["alt_phone_num"])
            except Exception:
                pass
        page.click("#ins-check-mobile")
        page.wait_for_function(
            "() => window.__insKycState === 'found' || window.__insKycState === 'need_docs'",
            timeout=15000,
        )
        kyc_state = page.evaluate("() => window.__insKycState")
        if kyc_state == "need_docs":
            payloads = _insurance_kyc_png_payloads()
            page.locator("#ins-aadhar-front").set_input_files(payloads[0])
            page.locator("#ins-aadhar-rear").set_input_files(payloads[1])
            page.locator("#ins-customer-photo").set_input_files(payloads[2])
        if page.locator("#ins-consent").count() > 0 and not page.is_checked("#ins-consent"):
            page.check("#ins-consent")
        if kyc_state == "need_docs":
            # set_input_files does not always fire "change"; dummy exposes __syncInsuranceKycSubmitState.
            try:
                page.evaluate(
                    "() => { if (typeof window.__syncInsuranceKycSubmitState === 'function') window.__syncInsuranceKycSubmitState(); }"
                )
            except Exception:
                pass
            page.wait_for_timeout(80)
            submit_loc = page.locator("#ins-kyc-submit")
            if submit_loc.count() > 0:
                submit_loc.wait_for(state="attached", timeout=10000)
                try:
                    page.wait_for_function(
                        """() => {
                          const b = document.querySelector('#ins-kyc-submit');
                          if (!b) return false;
                          if (b.hidden) return false;
                          return !b.disabled;
                        }""",
                        timeout=25000,
                    )
                except PlaywrightTimeout:
                    page.evaluate(
                        "() => { if (typeof window.__syncInsuranceKycSubmitState === 'function') window.__syncInsuranceKycSubmitState(); }"
                    )
                    page.wait_for_timeout(80)
                    page.wait_for_function(
                        """() => {
                          const b = document.querySelector('#ins-kyc-submit');
                          return b && !b.hidden && !b.disabled;
                        }""",
                        timeout=15000,
                    )
                submit_loc.click()
            else:
                # Older dummy page: single Proceed after uploads
                page.locator("#ins-proceed").wait_for(state="visible", timeout=5000)
                page.locator("#ins-proceed").wait_for(state="enabled", timeout=15000)
                page.click("#ins-proceed")
        else:
            page.locator("#ins-proceed").wait_for(state="visible", timeout=10000)
            page.locator("#ins-proceed").wait_for(state="enabled", timeout=10000)
            page.click("#ins-proceed")
        page.wait_for_url("**/kyc-success.html*", timeout=10000)
        page.wait_for_timeout(60)
        page.goto(f"{base}/dms-entry.html", wait_until="domcontentloaded", timeout=15000)
        page.fill("#ins-vin", values["frame_no"], timeout=INSURANCE_ACTION_TIMEOUT_MS)
        page.click("a.btn[href='policy.html']", timeout=INSURANCE_ACTION_TIMEOUT_MS)
        page.wait_for_url("**/policy.html*", timeout=10000)
        # Many sequential fields: use a tighter per-action ceiling so the page feels responsive.
        page.set_default_timeout(INSURANCE_POLICY_FILL_TIMEOUT_MS)

        # Fill policy form fields. Do NOT click #ins-issue-policy — operator issues policy manually.
        # Chassis = VIN/Frame from vehicle_master (DMS scrape). Ex-Showroom = vehicle_price (DMS cost).
        # Insurance company: fuzzy-match to details insurer; manufacturer: fuzzy-match vehicle_master.oem_name.
        # Policy tenure & proposer type: keep dummy page defaults (no select_option).
        _insurance_select_fuzzy(
            page,
            "#ins-sel-policy-company",
            values["insurer"] or "",
            timeout_ms=INSURANCE_POLICY_FILL_TIMEOUT_MS,
        )
        if values.get("oem_name"):
            _insurance_select_fuzzy(
                page,
                "#ins-sel-manufacturer",
                values["oem_name"],
                timeout_ms=INSURANCE_POLICY_FILL_TIMEOUT_MS,
            )

        pt = INSURANCE_POLICY_FILL_TIMEOUT_MS
        page.fill("#ins-proposer-name", values["customer_name"], timeout=pt)
        selects = page.locator(".main select")
        if values["gender"]:
            try:
                selects.nth(4).select_option(label=values["gender"].capitalize(), timeout=pt)
            except Exception:
                pass
        if values["dob"]:
            page.fill("#ins-proposer-dob", values["dob"], timeout=pt)
        if values["marital_status"]:
            try:
                selects.nth(5).select_option(label=values["marital_status"], timeout=pt)
            except Exception:
                pass
        if values["profession"]:
            try:
                selects.nth(6).select_option(label=values["profession"], timeout=pt)
            except Exception:
                pass
        page.fill("#ins-policy-mobile", values["mobile_number"], timeout=pt)
        if values.get("alt_phone_num"):
            try:
                page.fill("#ins-alt-phone", values["alt_phone_num"], timeout=pt)
            except Exception:
                pass
        if values["state"]:
            try:
                selects.nth(7).select_option(label=values["state"], timeout=pt)
            except Exception:
                pass
        if values["city"]:
            try:
                selects.nth(8).select_option(label=values["city"], timeout=pt)
            except Exception:
                pass
        if values["pin_code"]:
            page.fill("#ins-proposer-pin", values["pin_code"], timeout=pt)
        if values["address"]:
            page.fill("#ins-proposer-address", values["address"], timeout=pt)
        page.fill("#ins-chassis", values["frame_no"], timeout=pt)
        page.fill("#ins-engine", values["engine_no"], timeout=pt)
        if values["model_name"]:
            page.fill("#ins-model-name", values["model_name"], timeout=pt)
        ex_show = (values.get("vehicle_price") or "").replace(",", "").strip()
        page.fill("#ins-ex-showroom", ex_show, timeout=pt)
        if values["year_of_mfg"]:
            page.fill("#ins-yom", values["year_of_mfg"], timeout=pt)
        if values["fuel_type"]:
            try:
                selects.nth(12).select_option(label=values["fuel_type"], timeout=pt)
            except Exception:
                pass
        if values["nominee_name"]:
            page.fill("#ins-nominee-name", values["nominee_name"], timeout=pt)
        if values["nominee_age"]:
            page.fill("#ins-nominee-age", values["nominee_age"], timeout=pt)
        if values["nominee_gender"]:
            try:
                selects.nth(13).select_option(label=values["nominee_gender"].capitalize(), timeout=pt)
            except Exception:
                pass
        if values["nominee_relationship"]:
            try:
                selects.nth(14).select_option(label=values["nominee_relationship"], timeout=pt)
            except Exception:
                pass
        if values["financer_name"]:
            try:
                page.fill("#ins-financer", values["financer_name"], timeout=pt)
            except Exception:
                pass
        if values.get("rto_name"):
            try:
                selects.nth(11).select_option(label=values["rto_name"], timeout=pt)
            except Exception:
                pass

        logger.info("run_fill_insurance_only: deliberately not clicking #ins-issue-policy")

        try:
            page.set_default_timeout(15_000)
        except Exception:
            pass

        if ocr_output_dir is not None:
            _write_insurance_form_values(
                ocr_output_dir=Path(ocr_output_dir),
                subfolder=values.get("subfolder") or subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                values=values,
            )
        result["success"] = True
        result["error"] = None
        return result
    except PlaywrightTimeout as e:
        _p = locals().get("page")
        if _p is not None:
            try:
                _p.set_default_timeout(15_000)
            except Exception:
                pass
        result["error"] = f"Timeout: {e!s}"
        return result
    except Exception as e:
        _p = locals().get("page")
        if _p is not None:
            try:
                _p.set_default_timeout(15_000)
            except Exception:
                pass
        result["error"] = str(e)
        return result


def _run_vahan_in_context(
    context,
    vahan_base_url: str,
    *,
    customer_id: int | None,
    vehicle_id: int | None,
    subfolder: str | None,
    ocr_output_dir: Path | None,
    complete_upload_step: bool,
) -> dict:
    """Run Vahan using an existing browser context so batches can reuse one session."""
    vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
    effective_subfolder = vahan_values.get("subfolder") or subfolder
    page = context.new_page()
    page.set_default_timeout(15_000)
    try:
        app_id, fees = _fill_vahan_and_scrape(
            page,
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
        )
        added_to_cart = False
        if complete_upload_step:
            added_to_cart = _complete_vahan_upload_step(page)
        if ocr_output_dir is not None and effective_subfolder:
            _write_vahan_form_values(
                ocr_output_dir=ocr_output_dir,
                subfolder=effective_subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                rto_dealer_id=vahan_values["rto_dealer_id"],
                customer_name=vahan_values["customer_name"],
                chassis_no=vahan_values["chassis_no"],
                vehicle_model=vahan_values["vehicle_model"],
                vehicle_colour=vahan_values["vehicle_colour"],
                fuel_type=vahan_values["fuel_type"],
                year_of_mfg=vahan_values["year_of_mfg"],
                vehicle_price=vahan_values["vehicle_price"],
                application_id=app_id,
                rto_fees=fees,
            )
        return {
            "application_id": app_id,
            "rto_fees": fees,
            "added_to_cart": added_to_cart,
            "subfolder": effective_subfolder,
        }
    finally:
        page.close()


def run_fill_vahan_batch_row(
    context,
    vahan_base_url: str,
    *,
    customer_id: int,
    vehicle_id: int,
    subfolder: str | None,
    ocr_output_dir: Path | None,
) -> dict:
    """Batch-safe Vahan helper that reuses one browser/context and stops after cart upload."""
    del context  # Existing open tab mode does not create/reuse server-owned contexts.
    page, open_error = _get_or_open_site_page(vahan_base_url, "Vahan", require_login_on_open=False)
    if page is None:
        raise ValueError(open_error or "Vahan site not open. Please open Vahan site and keep it logged in.")
    vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
    app_id, fees = _fill_vahan_and_scrape(
        page,
        vahan_base_url=vahan_base_url.strip(),
        rto_dealer_id=vahan_values["rto_dealer_id"],
        customer_name=vahan_values["customer_name"],
        chassis_no=vahan_values["chassis_no"],
        vehicle_model=vahan_values["vehicle_model"],
        vehicle_colour=vahan_values["vehicle_colour"],
        fuel_type=vahan_values["fuel_type"],
        year_of_mfg=vahan_values["year_of_mfg"],
        vehicle_price=vahan_values["vehicle_price"],
    )
    added_to_cart = _complete_vahan_upload_step(page)
    if ocr_output_dir is not None and (vahan_values.get("subfolder") or subfolder):
        _write_vahan_form_values(
            ocr_output_dir=ocr_output_dir,
            subfolder=vahan_values.get("subfolder") or subfolder,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
            application_id=app_id,
            rto_fees=fees,
        )
    return {
        "application_id": app_id,
        "rto_fees": fees,
        "added_to_cart": added_to_cart,
        "subfolder": vahan_values.get("subfolder") or subfolder,
    }


def update_vehicle_master_from_dms(vehicle_id: int, scraped: dict) -> None:
    """Update vehicle_master with DMS-scraped data (chassis, engine, key_num, model, colour, seating_capacity, etc.)."""
    from app.db import get_connection

    chassis = (scraped.get("frame_num") or "").strip() or None
    engine = (scraped.get("engine_num") or "").strip() or None
    key_num = (scraped.get("key_num") or "").strip() or None
    model = (scraped.get("model") or "").strip() or None
    colour = (scraped.get("color") or "").strip() or None
    cubic_capacity = scraped.get("cubic_capacity")
    seating_capacity = scraped.get("seating_capacity")
    body_type = (scraped.get("body_type") or "").strip() or None
    vehicle_type = (scraped.get("vehicle_type") or "").strip() or None
    num_cylinders = scraped.get("num_cylinders")
    horse_power = scraped.get("horse_power")
    year_of_mfg = scraped.get("year_of_mfg")
    vehicle_price = scraped.get("vehicle_price")
    if vehicle_price is None:
        vehicle_price = scraped.get("total_amount")
    if cubic_capacity:
        try:
            cubic_capacity = float(str(cubic_capacity).replace(",", ""))
        except (ValueError, TypeError):
            cubic_capacity = None
    if seating_capacity:
        try:
            seating_capacity = int(str(seating_capacity).strip())
        except (ValueError, TypeError):
            seating_capacity = None
    if num_cylinders:
        try:
            num_cylinders = int(str(num_cylinders).strip())
        except (ValueError, TypeError):
            num_cylinders = None
    if horse_power:
        try:
            horse_power = float(str(horse_power).replace(",", ""))
        except (ValueError, TypeError):
            horse_power = None
    if year_of_mfg:
        try:
            year_of_mfg = int(str(year_of_mfg).strip())
        except (ValueError, TypeError):
            year_of_mfg = None
    if vehicle_price:
        try:
            vehicle_price = float(str(vehicle_price).replace(",", ""))
        except (ValueError, TypeError):
            vehicle_price = None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_master SET
                    chassis = COALESCE(%s, chassis),
                    engine = COALESCE(%s, engine),
                    key_num = COALESCE(%s, key_num),
                    model = COALESCE(%s, model),
                    colour = COALESCE(%s, colour),
                    cubic_capacity = COALESCE(%s, cubic_capacity),
                    seating_capacity = COALESCE(%s, seating_capacity),
                    body_type = COALESCE(%s, body_type),
                    vehicle_type = COALESCE(%s, vehicle_type),
                    num_cylinders = COALESCE(%s, num_cylinders),
                    horse_power = COALESCE(%s, horse_power),
                    year_of_mfg = COALESCE(%s, year_of_mfg),
                    vehicle_price = COALESCE(%s, vehicle_price)
                WHERE vehicle_id = %s
                """,
                (chassis, engine, key_num, model, colour, cubic_capacity, seating_capacity, body_type, vehicle_type, num_cylinders, horse_power, year_of_mfg, vehicle_price, vehicle_id),
            )
            conn.commit()
            if cur.rowcount > 0:
                logger.info("fill_dms: updated vehicle_master vehicle_id=%s with DMS data", vehicle_id)
    finally:
        conn.close()


def _run_fill_dms_real_siebel_playwright(
    page,
    dms_values: dict,
    effective_subfolder: str,
    ocr_dir: Path,
    customer_id: int | None,
    vehicle_id: int | None,
    result: dict,
) -> None:
    """
    Hero Connect / Siebel Open UI: ``Playwright_Hero_DMS_fill``. When
    ``siebel_dms_playwright.SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`` is True, only the **Find Contact →
    All Enquiries** path runs, then stops. Otherwise (BRD §6.1a) **always** Contact Find (mobile + Go)
    first; vehicle list scrape + ``in_transit`` branch (receipt/PDI vs booking/allotment).

    ``dms_contact_path=skip_find`` in DB is **ignored** for real Siebel (operators still need Find so the
    correct contact context is loaded even when the customer already exists). Dummy DMS may still use
    ``skip_find`` for training HTML.

    Writes ``DMS_Form_Values`` trace; no dummy ``/downloads/*.pdf``.
    """
    if not (DMS_REAL_URL_CONTACT or "").strip():
        result["error"] = (
            "DMS_MODE is real/siebel but DMS_REAL_URL_CONTACT is not set. "
            "Set the full GotoView URL (e.g. Buyer/CoBuyer) in backend/.env."
        )
        return

    mobile_phone = dms_values["mobile_phone"]
    landline = dms_values.get("landline") or ""
    addr = dms_values["address_line_1"]
    state = dms_values["state"]
    pin = dms_values["pin_code"]
    key_partial = dms_values["key_partial"]
    frame_partial = dms_values["frame_partial"]
    engine_partial = dms_values["engine_partial"]

    _write_dms_form_values(
        ocr_output_dir=ocr_dir,
        subfolder=effective_subfolder,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        customer_name=dms_values["customer_name"],
        mobile_number=mobile_phone,
        alt_phone_num=landline,
        address=addr,
        state=state,
        pin_code=pin,
        key_no=key_partial,
        frame_no=frame_partial,
        engine_no=engine_partial,
        relation_prefix=dms_values.get("relation_prefix") or "",
        father_husband_name=dms_values.get("father_husband_name") or "",
        customer_budget=DMS_DUMMY_ENQUIRY_BUDGET,
        finance_required=dms_values.get("finance_required") or "",
        financier_name=dms_values.get("financier_name") or "",
        dms_contact_path=dms_values.get("dms_contact_path") or "",
    )

    playwright_dms_log = Path(ocr_dir).resolve() / _safe_subfolder_name(effective_subfolder) / "Playwright_DMS.txt"

    urls = SiebelDmsUrls(
        contact=DMS_REAL_URL_CONTACT,
        vehicles=DMS_REAL_URL_VEHICLES,
        precheck=DMS_REAL_URL_PRECHECK,
        pdi=DMS_REAL_URL_PDI,
        vehicle=DMS_REAL_URL_VEHICLE,
        enquiry=DMS_REAL_URL_ENQUIRY,
        line_items=DMS_REAL_URL_LINE_ITEMS,
        reports=DMS_REAL_URL_REPORTS,
    )
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
    frag = Playwright_Hero_DMS_fill(
        page,
        dms_values,
        urls,
        action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
        nav_timeout_ms=DMS_SIEBEL_NAV_TIMEOUT_MS,
        content_frame_selector=frame_sel,
        mobile_aria_hints=list(DMS_SIEBEL_MOBILE_ARIA_HINTS),
        skip_contact_find=False,
        execution_log_path=playwright_dms_log,
    )

    result["vehicle"] = frag.get("vehicle") or {}
    result["error"] = frag.get("error")
    result["dms_siebel_forms_filled"] = bool(frag.get("dms_siebel_forms_filled"))
    result["dms_siebel_notes"] = frag.get("dms_siebel_notes") or []
    result["dms_milestones"] = list(frag.get("dms_milestones") or [])
    result["dms_step_messages"] = list(frag.get("dms_step_messages") or [])
    _sort_dms_milestones(result)
    result["dms_automation_mode"] = "real"
    if result.get("error"):
        result["dms_real_note"] = None
    else:
        notes = "; ".join(result["dms_siebel_notes"]) if result.get("dms_siebel_notes") else ""
        result["dms_real_note"] = notes or "Siebel contact + vehicle automation finished."
    if vehicle_id and result.get("vehicle"):
        try:
            update_vehicle_master_from_dms(vehicle_id, result.get("vehicle") or {})
        except Exception as exc:
            logger.warning(
                "fill_dms_service: vehicle_master update failed (real Siebel) vehicle_id=%s: %s",
                vehicle_id,
                exc,
            )
    logger.info(
        "fill_dms_service: real Siebel flow done error=%s forms_filled=%s vehicle_keys=%s",
        bool(result.get("error")),
        result.get("dms_siebel_forms_filled"),
        list((result.get("vehicle") or {}).keys())[:8],
    )


def _run_fill_dms_dummy_playwright(
    page,
    base: str,
    dms_values: dict,
    effective_subfolder: str,
    subfolder_path: Path,
    ocr_dir: Path,
    customer_id: int | None,
    vehicle_id: int | None,
    result: dict,
) -> None:
    """Repo dummy DMS HTML: enquiry → vehicles → PDI → vehicle scrape → line items → PDFs."""
    page.set_default_timeout(12_000)

    mobile_phone = dms_values["mobile_phone"]
    landline = dms_values.get("landline") or ""
    addr = dms_values["address_line_1"]
    state = dms_values["state"]
    pin = dms_values["pin_code"]
    key_partial = dms_values["key_partial"]
    frame_partial = dms_values["frame_partial"]
    engine_partial = dms_values["engine_partial"]
    contact_path = (dms_values.get("dms_contact_path") or "found").strip().lower()

    def goto(path: str) -> None:
        page.goto(f"{base}/{path}", wait_until="domcontentloaded", timeout=20000)

    goto("enquiry.html")

    if contact_path != "skip_find":
        page.fill("#dms-contact-finder-mobile", mobile_phone)
        if contact_path == "new_enquiry":
            page.evaluate("() => { sessionStorage.setItem('dummy_dms_expect', 'new'); }")
        else:
            page.evaluate("() => { sessionStorage.removeItem('dummy_dms_expect'); }")
        page.click("#dms-contact-finder-go")
        page.wait_for_timeout(200)
        _dms_milestone(result, "Customer found")

        if contact_path == "new_enquiry":
            _fill_playwright_enquiry_contact(page, dms_values)
            page.click("#dms-save-enquiry-quiet")
            page.wait_for_timeout(200)
            page.fill("#dms-contact-finder-mobile", mobile_phone)
            page.evaluate("() => { sessionStorage.removeItem('dummy_dms_expect'); }")
            page.click("#dms-contact-finder-go")
            page.wait_for_timeout(200)

    _fill_playwright_enquiry_contact(page, dms_values)
    _apply_playwright_enquiry_relation_finance(page, dms_values)
    if (_clean_text(dms_values.get("father_husband_name")) or _clean_text(dms_values.get("relation_prefix"))):
        _dms_milestone(result, "Care of filled")
    page.fill("#dms-customer-budget", DMS_DUMMY_ENQUIRY_BUDGET)
    try:
        page.select_option("#dms-booking-order-type", value="Regular")
    except Exception:
        try:
            page.select_option("#dms-booking-order-type", label="Regular")
        except Exception:
            pass
    page.click("#dms-generate-booking")
    page.wait_for_timeout(200)
    _dms_milestone(result, "Enquiry created")

    goto("vehicles.html")
    transit = page.locator("#dms-in-transit-panel")
    try:
        if transit.count() > 0 and transit.first.is_visible():
            recv = page.locator("#dms-receive-vehicle")
            if recv.count() > 0 and recv.first.is_visible():
                recv.first.click()
                page.wait_for_timeout(200)
    except Exception:
        pass
    try:
        pre = page.locator("#dms-precheck-complete")
        if pre.count() > 0 and pre.first.is_visible():
            pre.first.click()
            page.wait_for_timeout(200)
    except Exception:
        pass
    _dms_milestone(result, "Vehicle received")

    goto("pdi.html")
    try:
        pdi_btn = page.locator("#dms-pdi-complete")
        if pdi_btn.count() > 0 and pdi_btn.first.is_visible():
            pdi_btn.first.click()
            page.wait_for_timeout(200)
    except Exception:
        pass
    _dms_milestone(result, "Vehicle inspection done")

    goto("vehicle.html")
    _write_dms_form_values(
        ocr_output_dir=ocr_dir,
        subfolder=effective_subfolder,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        customer_name=dms_values["customer_name"],
        mobile_number=mobile_phone,
        alt_phone_num=landline,
        address=addr,
        state=state,
        pin_code=pin,
        key_no=key_partial,
        frame_no=frame_partial,
        engine_no=engine_partial,
        relation_prefix=dms_values.get("relation_prefix") or "",
        father_husband_name=dms_values.get("father_husband_name") or "",
        customer_budget=DMS_DUMMY_ENQUIRY_BUDGET,
        finance_required=dms_values.get("finance_required") or "",
        financier_name=dms_values.get("financier_name") or "",
        dms_contact_path=dms_values.get("dms_contact_path") or "",
    )
    page.fill("#dms-vehicle-key", key_partial)
    page.fill("#dms-vehicle-frame", frame_partial)
    page.fill("#dms-vehicle-engine", engine_partial)
    page.click("#dms-vehicle-search")
    page.wait_for_timeout(150)

    try:
        result["vehicle"] = _scrape_dms_vehicle_search_row(page)
    except Exception as scrape_exc:
        logger.warning("fill_dms_service: vehicle table scrape failed: %s", scrape_exc)
        result["vehicle"] = {}

    logger.info("fill_dms_service: run_fill_dms_only scraped vehicle=%s", result.get("vehicle"))
    if vehicle_id and result.get("vehicle"):
        try:
            update_vehicle_master_from_dms(vehicle_id, result.get("vehicle") or {})
        except Exception as exc:
            logger.warning("fill_dms_service: vehicle_master update failed vehicle_id=%s: %s", vehicle_id, exc)

    scraped = result.get("vehicle") or {}
    order_val = (scraped.get("vehicle_price") or scraped.get("ex_showroom_price") or "").strip()

    goto("enquiry.html")
    try:
        if scraped.get("frame_num"):
            page.evaluate(
                "(frame) => sessionStorage.setItem('dummy_dms_last_frame', frame)",
                scraped.get("frame_num") or "",
            )
        alloc = page.locator("#dms-allocate-vehicle")
        if alloc.count() > 0 and alloc.first.is_visible():
            alloc.first.click()
            page.wait_for_timeout(200)
    except Exception:
        pass

    goto("line-items.html")
    if order_val:
        try:
            page.fill("#dms-order-value", order_val)
        except Exception:
            pass
    fin_req = (dms_values.get("finance_required") or "N").strip().upper()
    if fin_req == "Y":
        try:
            page.select_option("#dms-line-finance-required", value="Y")
        except Exception:
            pass
        fin_name = dms_values.get("financier_name") or ""
        if fin_name:
            try:
                page.fill("#dms-line-financer", fin_name[:255])
            except Exception:
                pass
    _dms_milestone(result, "Invoice created")

    goto("reports.html")
    path21 = subfolder_path / "form21.pdf"
    path22 = subfolder_path / "form22.pdf"
    path_invoice = subfolder_path / "invoice_details.pdf"
    for url_suffix, path, name in [
        ("form21.pdf", path21, "form21.pdf"),
        ("form22.pdf", path22, "form22.pdf"),
        ("invoice_details.pdf", path_invoice, "invoice_details.pdf"),
    ]:
        try:
            r = page.request.get(f"{base}/downloads/{url_suffix}", timeout=15000)
            if r.ok:
                path.write_bytes(r.body())
                result["pdfs_saved"].append(name)
        except Exception:
            pass

    result["dms_automation_mode"] = "dummy"
    _sort_dms_milestones(result)


def run_fill_dms_only(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Run DMS steps: enquiry (contact find or new-enquiry path, S/O or W/o, booking budget),
    vehicles receive/precheck, PDI, vehicle search + scrape (Order Value / ex-showroom → vehicle_price),
    enquiry allocate, invoicing line fields (no Create Invoice), then Form 21/22 + invoice sheet PDFs.
    When ``DMS_MODE=real`` (see ``backend/.env``), navigates configured Siebel absolute URLs instead of dummy HTML.

    Separate Playwright session. Returns vehicle, pdfs_saved, error.
    """
    result: dict = {
        "vehicle": {},
        "pdfs_saved": [],
        "error": None,
        "dms_milestones": [],
        "dms_step_messages": [],
    }
    if not dms_base_url:
        result["error"] = "DMS_BASE_URL not set"
        return result
    ocr_dir = Path(ocr_output_dir or OCR_OUTPUT_DIR).resolve()
    try:
        dms_values = _build_dms_fill_values(customer_id, vehicle_id, subfolder)
    except Exception as e:
        result["error"] = str(e)
        return result
    effective_subfolder = dms_values.get("subfolder") or subfolder
    subfolder_path = uploads_dir / effective_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)

    try:
        mode = "real" if dms_automation_is_real_siebel() else "dummy"
        logger.info("fill_dms_service: run_fill_dms_only starting mode=%s dms=%s", mode, dms_base_url[:50])
        page, open_error = _get_or_open_site_page(
            dms_base_url,
            "DMS",
            require_login_on_open=not dms_automation_is_real_siebel(),
        )
        if page is None:
            result["error"] = open_error
            return result

        _install_playwright_js_dialog_handler(page)

        # Operator-controlled step: Playwright must not click "Create Invoice".
        # If that action is pending on screen, instruct operator and stop.
        if _requires_operator_create_invoice(page):
            result["error"] = "Please click Create Invoice manually in DMS, then press Fill DMS again."
            return result

        if dms_automation_is_real_siebel():
            _run_fill_dms_real_siebel_playwright(
                page,
                dms_values,
                effective_subfolder,
                ocr_dir,
                customer_id,
                vehicle_id,
                result,
            )
        else:
            _run_fill_dms_dummy_playwright(
                page,
                dms_base_url.rstrip("/"),
                dms_values,
                effective_subfolder,
                subfolder_path,
                ocr_dir,
                customer_id,
                vehicle_id,
                result,
            )
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: run_fill_dms_only PlaywrightTimeout %s", e)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("fill_dms_service: run_fill_dms_only exception %s", e)

    try:
        _write_data_from_dms(ocr_dir, effective_subfolder, dms_values.get("customer_export") or {}, result.get("vehicle") or {})
    except Exception as e:
        result["error"] = (result.get("error") or "") + f"; DMS file write: {e!s}"
    return result


def run_fill_vahan_only(
    vahan_base_url: str,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Run only Vahan step: fill registration form, submit, scrape application_id and rto_fees.
    Separate Playwright process (new browser). Returns application_id, rto_fees, error.
    """
    result: dict = {"application_id": None, "rto_fees": None, "added_to_cart": False, "error": None}
    if not vahan_base_url or not vahan_base_url.strip():
        result["error"] = "vahan_base_url required"
        return result
    try:
        logger.info("fill_dms_service: run_fill_vahan_only starting")
        page, open_error = _get_or_open_site_page(vahan_base_url, "Vahan", require_login_on_open=False)
        if page is None:
            result["error"] = open_error
            return result
        vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
        app_id, fees = _fill_vahan_and_scrape(
            page,
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
        )
        result.update(
            {
                "application_id": app_id,
                "rto_fees": fees,
                "added_to_cart": False,
                "subfolder": vahan_values.get("subfolder") or subfolder,
            }
        )
        if ocr_output_dir is not None and (vahan_values.get("subfolder") or subfolder):
            _write_vahan_form_values(
                ocr_output_dir=ocr_output_dir,
                subfolder=vahan_values.get("subfolder") or subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                rto_dealer_id=vahan_values["rto_dealer_id"],
                customer_name=vahan_values["customer_name"],
                chassis_no=vahan_values["chassis_no"],
                vehicle_model=vahan_values["vehicle_model"],
                vehicle_colour=vahan_values["vehicle_colour"],
                fuel_type=vahan_values["fuel_type"],
                year_of_mfg=vahan_values["year_of_mfg"],
                vehicle_price=vahan_values["vehicle_price"],
                application_id=app_id,
                rto_fees=fees,
            )
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: run_fill_vahan_only PlaywrightTimeout %s", e)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("fill_dms_service: run_fill_vahan_only exception %s", e)
    return result


def run_fill_dms(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    vahan_base_url: str | None = None,
    rto_dealer_id: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    headless: bool | None = None,
) -> dict:
    """
    Run Playwright: same extended DMS flow as `run_fill_dms_only` (enquiry → receive/PDI → vehicle scrape →
    invoicing fields without Create Invoice → Form 21/22 + invoice sheet PDFs), then optional Vahan when
    `vahan_base_url` is set.
    Writes pulled data to ocr_output_dir/subfolder/Data from DMS.txt.
    Returns dict with vehicle details (key_num, frame_num, vehicle_price / ex-showroom, ...), optional application_id, rto_fees, and any error.
    """
    result = run_fill_dms_only(
        dms_base_url=dms_base_url,
        subfolder=subfolder,
        customer=customer,
        vehicle=vehicle,
        login_user=login_user,
        login_password=login_password,
        uploads_dir=uploads_dir,
        ocr_output_dir=ocr_output_dir,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
    )
    dms_mode = result.get("dms_automation_mode")
    siebel_ok = result.get("dms_siebel_forms_filled")
    milestones = list(result.get("dms_milestones") or [])
    step_msgs = list(result.get("dms_step_messages") or [])

    if result.get("error"):
        return {
            "vehicle": result.get("vehicle") or {},
            "pdfs_saved": result.get("pdfs_saved") or [],
            "application_id": None,
            "rto_fees": None,
            "error": result.get("error"),
            "dms_automation_mode": dms_mode,
            "dms_siebel_forms_filled": siebel_ok,
            "dms_milestones": milestones,
            "dms_step_messages": step_msgs,
        }

    if vahan_base_url and vahan_base_url.strip():
        vahan_result = run_fill_vahan_only(
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=rto_dealer_id or "",
            customer_name=str((customer or {}).get("name") or ""),
            chassis_no=str((result.get("vehicle") or {}).get("frame_num") or (vehicle or {}).get("frame_no") or ""),
            vehicle_model=str((result.get("vehicle") or {}).get("model") or ""),
            vehicle_colour=str((result.get("vehicle") or {}).get("color") or ""),
            fuel_type=str((result.get("vehicle") or {}).get("fuel_type") or ""),
            year_of_mfg=str((result.get("vehicle") or {}).get("year_of_mfg") or ""),
            vehicle_price=_parse_vehicle_price(result.get("vehicle") or {}),
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
        )
        if vahan_result.get("error"):
            return {
                "vehicle": result.get("vehicle") or {},
                "pdfs_saved": result.get("pdfs_saved") or [],
                "application_id": None,
                "rto_fees": None,
                "error": vahan_result.get("error"),
                "dms_automation_mode": dms_mode,
                "dms_siebel_forms_filled": siebel_ok,
                "dms_milestones": milestones,
                "dms_step_messages": step_msgs,
            }
        return {
            "vehicle": result.get("vehicle") or {},
            "pdfs_saved": result.get("pdfs_saved") or [],
            "application_id": vahan_result.get("application_id"),
            "rto_fees": vahan_result.get("rto_fees"),
            "error": None,
            "dms_automation_mode": dms_mode,
            "dms_siebel_forms_filled": siebel_ok,
            "dms_milestones": milestones,
            "dms_step_messages": step_msgs,
        }

    return {
        "vehicle": result.get("vehicle") or {},
        "pdfs_saved": result.get("pdfs_saved") or [],
        "application_id": None,
        "rto_fees": None,
        "error": None,
        "dms_automation_mode": dms_mode,
        "dms_siebel_forms_filled": siebel_ok,
        "dms_milestones": milestones,
        "dms_step_messages": step_msgs,
    }
