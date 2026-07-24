"""Download the Vahan **Dealer Registration Pendency** (HSRP) report as an Excel file.

Standalone report flow (kept separate from the fragile 6-screen RTO fill in
``fill_rto_service``). Reuses the same browser-open primitives the **Fill Vahan** button uses:
opens/attaches the operator Vahan tab, waits for the operator to log in, then:

    Report -> Dealer Registration Pendency -> Get Details -> (confirm) Yes -> Download File -> EXCEL FILE

saves the download to ``ocr_output/{dealer_id}/hsrp/vahan_hsrp_ddmmyyyy.<ext>`` (overwrite same day),
appends rows into ``vahan_hsrp_holding``, and updates ``vehicle_master.plate_num`` from Registration No
when Chassis No matches and the plate is not blank/NEW.

Browser lifetime: never closes the browser/context/page — the operator tab stays open for manual
use or the next run (same policy as Fill DMS / RTO). On any missing element or step failure, a
frame + visible-element dump is written to ``ocr_output/{dealer_id}/hsrp/vahan_hsrp_log.txt`` to aid
the next iteration of selector fixes.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import xlrd
from playwright.sync_api import Page, TimeoutError as PwTimeout

from app.config import DEALER_ID, VAHAN_BASE_URL, get_ocr_output_dir
from app.db import get_connection
from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
from app.services.fill_rto_service import _VAHAN_SESSION_DEAD_PATTERNS
from app.services.handle_browser_opening import get_or_open_site_page

logger = logging.getLogger(__name__)

# Playwright waits (ms). The grid can take a while to compute after Get Details.
_SHORT_TIMEOUT_MS = 4_000
_LONG_TIMEOUT_MS = 30_000
_DOWNLOAD_TIMEOUT_MS = 60_000

_DEALER_PENDENCY_URL_HINT = "formDealerRCPendencyDetails"
_HSRP_SUBDIR = "hsrp"
_DUMP_FILENAME = "vahan_hsrp_log.txt"

# Shown when the script-controlled window is not logged in. The operator often logs into a
# *different* browser (personal Chrome) that this script cannot control — this makes the target clear.
_NEEDS_LOGIN_MESSAGE = (
    "Vahan is NOT logged in in the AUTOMATED browser window. This script controls its OWN Chromium "
    "window (opened by the script, currently on the Vahan LOGIN page) — it has been brought to the "
    "front. Log in THERE, not in your personal Chrome/Edge. Only that window is automated. "
    "Then press Enter to continue."
)

# Excel header -> holding column (Vahan Dealer Registration Pendency export).
_EXCEL_HEADER_TO_COL: dict[str, str] = {
    "srl.no.": "srl_no",
    "srl.no": "srl_no",
    "office name": "office_name",
    "application no": "application_no",
    "registration no": "registration_no",
    "chassis no": "chassis_no",
    "owner name": "owner_name",
    "purpose": "purpose",
    "model name": "model_name",
    "hypothecated": "hypothecation",
    "hypothecation": "hypothecation",
    "status": "status",
    "pending at": "pending_at",
    "pending since": "pending_since",
    "dealer regn no": "dealer_regn_no",
    "vehicle class type": "vehicle_class_type",
    "ownership type": "ownership_type",
    "fuel type": "fuel_type",
    "purchase category": "purchase_category",
}

_HOLDING_INSERT_COLS = (
    "dealer_id",
    "download_date",
    "source_filename",
    "srl_no",
    "office_name",
    "application_no",
    "registration_no",
    "chassis_no",
    "owner_name",
    "purpose",
    "model_name",
    "hypothecation",
    "status",
    "pending_at",
    "pending_since",
    "dealer_regn_no",
    "vehicle_class_type",
    "ownership_type",
    "fuel_type",
    "purchase_category",
)

_PLATE_UPDATE_SQL = """
UPDATE vehicle_master vm
SET plate_num = h.registration_no
FROM (
    SELECT DISTINCT ON (UPPER(BTRIM(chassis_no)))
           UPPER(BTRIM(chassis_no)) AS chassis_key,
           BTRIM(registration_no) AS registration_no
    FROM vahan_hsrp_holding
    WHERE dealer_id = %s
      AND download_date = %s
      AND chassis_no IS NOT NULL AND BTRIM(chassis_no) <> ''
      AND registration_no IS NOT NULL
      AND BTRIM(registration_no) <> ''
      AND UPPER(BTRIM(registration_no)) <> 'NEW'
    ORDER BY UPPER(BTRIM(chassis_no)), holding_id DESC
) h
WHERE UPPER(BTRIM(COALESCE(vm.chassis, vm.raw_frame_num, ''))) = h.chassis_key
"""


# ---------------------------------------------------------------------------
# Paths / logging (self-contained — no contextvar dependency)
# ---------------------------------------------------------------------------

def _hsrp_dir(dealer_id: int) -> Path:
    out = get_ocr_output_dir(dealer_id) / _HSRP_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def _log_path(dealer_id: int) -> Path:
    return _hsrp_dir(dealer_id) / _DUMP_FILENAME


def _log(dealer_id: int, line: str) -> None:
    """Append one timestamped line to the per-dealer HSRP log (and the app logger)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("vahan_hsrp: %s", line)
    try:
        with _log_path(dealer_id).open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {line}\n")
    except Exception as exc:
        logger.debug("vahan_hsrp: could not write log line: %s", exc)


_JS_STATE_SNAPSHOT = """() => {
    function visible(el) {
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        return true;
    }
    const selector = 'input, select, textarea, button, a, [role], label, li, span';
    const nodes = Array.from(document.querySelectorAll(selector));
    const out = [];
    for (const el of nodes) {
        if (out.length >= 220) break;
        if (!visible(el)) continue;
        const tag = el.tagName.toLowerCase();
        const id = el.id || '';
        const name = el.getAttribute('name') || '';
        const type = el.getAttribute('type') || '';
        const role = el.getAttribute('role') || '';
        const title = (el.getAttribute('title') || '').substring(0, 60);
        const href = (el.getAttribute('href') || '').substring(0, 80);
        const onclick = (el.getAttribute('onclick') || '').substring(0, 100);
        const value = (el.value || '').substring(0, 60);
        const text = (el.innerText || '').substring(0, 60).replace(/\\n/g, ' ').trim();
        const cls = (el.className || '').toString().substring(0, 90);
        if (!id && !name && !role && !text && !onclick && !href && !value) continue;
        out.push({ tag, id, name, type, role, title, href, onclick, value, text, cls });
    }
    return out;
}"""


def _dump_state(page: Page, dealer_id: int, context: str) -> None:
    """Write frames + visible interactive elements to the HSRP log for the next fix iteration."""
    _log(dealer_id, f"=== PAGE STATE DUMP ({context}) ===")
    try:
        _log(dealer_id, f"url: {(page.url or '')[:300]}")
    except Exception:
        _log(dealer_id, "url: (could not read)")
    try:
        frames = page.frames
        _log(dealer_id, f"frames: {len(frames)}")
        for i, frame in enumerate(frames):
            try:
                _log(dealer_id, f"  frame[{i}] name={(frame.name or '(main)')!r} url={(frame.url or '')[:200]}")
            except Exception:
                _log(dealer_id, f"  frame[{i}] (could not read)")
    except Exception as exc:
        _log(dealer_id, f"frames: error listing — {exc}")

    try:
        elements = page.evaluate(_JS_STATE_SNAPSHOT) or []
    except Exception as exc:
        _log(dealer_id, f"element snapshot failed: {exc}")
        return
    _log(dealer_id, f"visible interactive elements ({len(elements)}):")
    for el in elements:
        parts = [el.get("tag", "")]
        for key in ("id", "name", "type", "role", "title", "href", "onclick", "value", "text", "cls"):
            val = el.get(key)
            if val:
                parts.append(f"{key}={val!r}")
        _log(dealer_id, "    " + " ".join(parts))
    _log(dealer_id, "=== END PAGE STATE DUMP ===")


# ---------------------------------------------------------------------------
# Login readiness (report-specific — only needs a logged-in session, not RTO Screen 1)
# ---------------------------------------------------------------------------

def _page_url(page: Page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _resolve_logged_in_page(page: Page, dealer_id: int) -> Page:
    """Pick the logged-in tab from the Vahan context.

    ``get_or_open_site_page`` matches by base URL and can return a stale ``login.xhtml`` page even
    though the operator is logged in on another tab (e.g. ``home.xhtml``). Scan every page in the
    context and prefer a clearly logged-in one so a leftover login tab does not mask the session.
    """
    try:
        pages = list(page.context.pages)
    except Exception:
        return page
    _log(dealer_id, f"context has {len(pages)} page(s): " + " | ".join(_page_url(p)[:120] for p in pages))

    best: Page | None = None
    for p in pages:
        u = _page_url(p).lower()
        if not u or "about:blank" in u:
            continue
        if any(pat in u for pat in _VAHAN_SESSION_DEAD_PATTERNS):
            continue
        if "/vahan/" in u:
            if "home.xhtml" in u:
                best = p
                break
            if best is None:
                best = p
    if best is not None and best is not page:
        _log(dealer_id, f"switching to logged-in tab url={_page_url(best)[:200]}")
        try:
            best.bring_to_front()
        except Exception:
            pass
        return best
    return page


def _vahan_logged_in(page: Page, dealer_id: int) -> bool:
    """True when the Vahan tab is past login (any post-login page, e.g. ``home.xhtml``).

    Unlike ``_vahan_dealer_home_ready`` (RTO), this does NOT require the new-registration
    ``div#officeList`` — the HSRP report runs from the top-nav Report menu on any logged-in page.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    for pat in _VAHAN_SESSION_DEAD_PATTERNS:
        if pat in url:
            _log(dealer_id, f"login check: on session-dead/login URL {url[:200]}")
            return False
    for sel in (
        "a:has-text('Logout')",
        "[role='menuitem']:has-text('Report')",
        "a:has-text('Report')",
        "span:has-text('Report')",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                _log(dealer_id, f"login check: logged in (matched {sel!r}) url={url[:200]}")
                return True
        except Exception:
            continue
    if "/vahan/" in url and "login" not in url and "about:blank" not in url:
        _log(dealer_id, f"login check: logged in (URL fallback) url={url[:200]}")
        return True
    _log(dealer_id, f"login check: not logged in url={url[:200]}")
    return False


# ---------------------------------------------------------------------------
# Excel parse → holding table → plate_num
# ---------------------------------------------------------------------------

def _download_date_from_path(path: Path) -> date:
    """Parse ``vahan_hsrp_ddmmyyyy`` from the filename; fall back to today."""
    m = re.search(r"vahan_hsrp_(\d{8})", path.stem, re.I)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d%m%Y").date()
        except ValueError:
            pass
    return date.today()


def _cell_str(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, float):
        if val == int(val):
            s = str(int(val))
        else:
            s = str(val).strip()
    else:
        s = str(val).strip()
    return s or None


def _parse_hsrp_excel(path: Path) -> list[dict[str, str | None]]:
    """Read the Vahan pendency ``.xls`` into row dicts keyed by holding column names."""
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    if sheet.nrows < 2:
        return []
    headers: list[str | None] = []
    for c in range(sheet.ncols):
        raw = _cell_str(sheet.cell_value(0, c))
        key = _EXCEL_HEADER_TO_COL.get((raw or "").lower().strip()) if raw else None
        headers.append(key)
    rows: list[dict[str, str | None]] = []
    for r in range(1, sheet.nrows):
        row: dict[str, str | None] = {
            col: None
            for col in _HOLDING_INSERT_COLS
            if col not in ("dealer_id", "download_date", "source_filename")
        }
        empty = True
        for c, col in enumerate(headers):
            if not col or c >= sheet.ncols:
                continue
            val = _cell_str(sheet.cell_value(r, c))
            row[col] = val
            if val:
                empty = False
        if empty:
            continue
        rows.append(row)
    return rows


def load_hsrp_excel_to_holding(
    dealer_id: int,
    excel_path: Path,
    *,
    download_date: date | None = None,
) -> dict:
    """Append Excel rows to ``vahan_hsrp_holding`` and update ``vehicle_master.plate_num``.

    Returns ``{rows_loaded, plates_updated, download_date, error}``.
    """
    dealer_id = int(dealer_id)
    path = Path(excel_path)
    out: dict = {
        "rows_loaded": 0,
        "plates_updated": 0,
        "download_date": None,
        "error": None,
    }
    if not path.is_file():
        out["error"] = f"Excel file not found: {path}"
        return out

    dl_date = download_date or _download_date_from_path(path)
    out["download_date"] = dl_date.isoformat()

    try:
        rows = _parse_hsrp_excel(path)
    except Exception as exc:
        out["error"] = f"Excel parse failed: {exc}"
        _log(dealer_id, out["error"])
        return out

    if not rows:
        _log(dealer_id, f"Excel had no data rows: {path.name}")
        return out

    placeholders = ", ".join(["%s"] * len(_HOLDING_INSERT_COLS))
    col_list = ", ".join(_HOLDING_INSERT_COLS)
    insert_sql = f"INSERT INTO vahan_hsrp_holding ({col_list}) VALUES ({placeholders})"
    tuples = []
    for row in rows:
        tuples.append(
            tuple(
                dealer_id if c == "dealer_id"
                else dl_date if c == "download_date"
                else path.name if c == "source_filename"
                else row.get(c)
                for c in _HOLDING_INSERT_COLS
            )
        )

    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.executemany(insert_sql, tuples)
                out["rows_loaded"] = len(tuples)
                cur.execute(_PLATE_UPDATE_SQL, (dealer_id, dl_date))
                out["plates_updated"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()
        finally:
            conn.close()
        _log(
            dealer_id,
            f"holding load: rows_loaded={out['rows_loaded']} plates_updated={out['plates_updated']} "
            f"download_date={dl_date} file={path.name}",
        )
    except Exception as exc:
        out["error"] = f"DB load/apply failed: {exc}"
        _log(dealer_id, out["error"])
    return out


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _navigate_to_dealer_pendency(page: Page, dealer_id: int) -> bool:
    """Report menu -> Dealer Registration Pendency. Returns True once on the pendency page."""
    if _DEALER_PENDENCY_URL_HINT.lower() in (page.url or "").lower():
        _log(dealer_id, "already on Dealer Registration Pendency page")
        return True

    report_selectors = (
        "a:has-text('Report')",
        "[role='menuitem']:has-text('Report')",
        "span:has-text('Report')",
    )
    opened = False
    for sel in report_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                try:
                    loc.hover(timeout=_SHORT_TIMEOUT_MS)
                except Exception:
                    pass
                loc.click(timeout=_SHORT_TIMEOUT_MS)
                opened = True
                _log(dealer_id, f"clicked Report menu via {sel!r}")
                break
        except Exception as exc:
            _log(dealer_id, f"Report menu selector {sel!r} failed: {exc}")
    if not opened:
        _log(dealer_id, "could not open Report menu")
        return False

    page.wait_for_timeout(400)
    submenu_re = re.compile(r"Dealer\s+Registration\s+Pendency", re.I)
    submenu_selectors = (
        page.get_by_role("menuitem", name=submenu_re),
        page.get_by_role("link", name=submenu_re),
        page.locator("a:has-text('Dealer Registration Pendency')"),
        page.locator("span:has-text('Dealer Registration Pendency')"),
    )
    for loc in submenu_selectors:
        try:
            item = loc.first
            if item.count() > 0 and item.is_visible():
                item.click(timeout=_SHORT_TIMEOUT_MS)
                _log(dealer_id, "clicked Dealer Registration Pendency submenu")
                break
        except Exception as exc:
            _log(dealer_id, f"submenu selector failed: {exc}")
    else:
        _log(dealer_id, "could not find Dealer Registration Pendency submenu item")
        return False

    deadline_ok = False
    try:
        page.wait_for_url(re.compile(_DEALER_PENDENCY_URL_HINT, re.I), timeout=_LONG_TIMEOUT_MS)
        deadline_ok = True
    except Exception:
        try:
            page.get_by_role("button", name=re.compile(r"Get\s+Details", re.I)).first.wait_for(
                state="visible", timeout=_SHORT_TIMEOUT_MS
            )
            deadline_ok = True
        except Exception:
            deadline_ok = False
    if not deadline_ok:
        _log(dealer_id, "did not land on Dealer Registration Pendency page")
        return False
    _log(dealer_id, f"on Dealer Registration Pendency page: {(page.url or '')[:200]}")
    return True


def _click_get_details_and_confirm(page: Page, dealer_id: int) -> bool:
    """Click Get Details, then confirm the popup (native JS auto-accepted; also click in-page Yes)."""
    getdet_re = re.compile(r"Get\s+Details", re.I)
    clicked = False
    for loc in (
        page.get_by_role("button", name=getdet_re),
        page.locator("button:has-text('Get Details')"),
        page.locator("input[type='submit'][value*='Get Details' i]"),
        page.locator("input[type='button'][value*='Get Details' i]"),
        page.locator("a:has-text('Get Details')"),
    ):
        try:
            item = loc.first
            if item.count() > 0 and item.is_visible():
                item.click(timeout=_SHORT_TIMEOUT_MS)
                clicked = True
                _log(dealer_id, "clicked Get Details")
                break
        except Exception as exc:
            _log(dealer_id, f"Get Details selector failed: {exc}")
    if not clicked:
        _log(dealer_id, "could not find Get Details button")
        return False

    page.wait_for_timeout(500)
    yes_re = re.compile(r"^\s*(Yes|OK|Ok)\s*$", re.I)
    for loc in (
        page.locator(".ui-confirmdialog button:has-text('Yes')"),
        page.locator(".ui-dialog button:has-text('Yes')"),
        page.get_by_role("button", name=yes_re),
        page.locator("button:has-text('Yes')"),
    ):
        try:
            item = loc.first
            if item.count() > 0 and item.is_visible():
                item.click(timeout=_SHORT_TIMEOUT_MS)
                _log(dealer_id, "clicked confirm Yes")
                break
        except Exception as exc:
            _log(dealer_id, f"confirm Yes selector failed: {exc}")
    else:
        _log(dealer_id, "no in-page confirm dialog found (native confirm may have been auto-accepted)")
    return True


def _download_excel(page: Page, dealer_id: int) -> Path | None:
    """Click Download File -> EXCEL FILE in the format popup, capture the download, save it."""
    dl_btn_re = re.compile(r"Download\s+File", re.I)
    download_btn = None
    for loc in (
        page.get_by_role("button", name=dl_btn_re),
        page.locator("button:has-text('Download File')"),
        page.locator("input[type='button'][value*='Download File' i]"),
        page.locator("a:has-text('Download File')"),
    ):
        try:
            item = loc.first
            item.wait_for(state="visible", timeout=_LONG_TIMEOUT_MS)
            if item.count() > 0 and item.is_visible():
                download_btn = item
                break
        except Exception:
            continue
    if download_btn is None:
        _log(dealer_id, "could not find Download File button (grid may not have rendered)")
        return None
    try:
        download_btn.click(timeout=_SHORT_TIMEOUT_MS)
        _log(dealer_id, "clicked Download File")
    except Exception as exc:
        _log(dealer_id, f"Download File click failed: {exc}")
        return None

    page.wait_for_timeout(500)
    excel_re = re.compile(r"EXCEL\s*FILE", re.I)
    excel_candidates = (
        page.get_by_role("link", name=excel_re),
        page.locator("a:has-text('EXCEL FILE')"),
        page.locator("td:has-text('EXCEL FILE') a"),
        page.locator("tr:has-text('EXCEL FILE') a"),
        page.locator("tr:has-text('EXCEL FILE') img"),
        page.locator("td:has-text('EXCEL FILE')"),
        page.locator("*:has-text('EXCEL FILE')"),
    )
    saved: Path | None = None
    for loc in excel_candidates:
        try:
            item = loc.first
            if item.count() == 0 or not item.is_visible():
                continue
            with page.expect_download(timeout=_DOWNLOAD_TIMEOUT_MS) as dl_info:
                item.click(timeout=_SHORT_TIMEOUT_MS)
            download = dl_info.value
            suffix = Path(download.suggested_filename or "").suffix or ".xls"
            dest = _hsrp_dir(dealer_id) / f"vahan_hsrp_{date.today().strftime('%d%m%Y')}{suffix}"
            download.save_as(str(dest))
            _log(dealer_id, f"saved Excel report to {dest}")
            saved = dest
            break
        except PwTimeout:
            _log(dealer_id, "no download event after clicking Excel candidate — trying next selector")
        except Exception as exc:
            _log(dealer_id, f"Excel download attempt failed: {exc}")
    if saved is None:
        _log(dealer_id, "could not download the Excel file from the format popup")
    return saved


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_vahan_hsrp_report(dealer_id: int = DEALER_ID, *, load_to_db: bool = True) -> dict:
    """Download the Dealer Registration Pendency (HSRP) Excel report from Vahan.

    Returns dict: ``{success, error, message, needs_login, saved_path, rows_loaded, plates_updated}``.

    When the operator Vahan tab is not yet logged in, returns ``needs_login=True`` with a message
    (same 2-press UX as Fill Vahan): the caller should have the operator log in, then call again.

    After a successful download, rows are appended to ``vahan_hsrp_holding`` and matching
    ``vehicle_master.plate_num`` values are updated (unless ``load_to_db=False``).
    """
    dealer_id = int(dealer_id)
    out: dict = {
        "success": False,
        "error": None,
        "message": None,
        "needs_login": False,
        "saved_path": None,
        "rows_loaded": 0,
        "plates_updated": 0,
    }

    u = (VAHAN_BASE_URL or "").strip()
    if not u:
        out["error"] = "VAHAN_BASE_URL not set"
        return out

    try:
        page, open_error = get_or_open_site_page(u, "Vahan", require_login_on_open=False)
        if page is None:
            out["error"] = open_error or "Could not open Vahan browser"
            return out
        page = _resolve_logged_in_page(page, dealer_id)
        _install_playwright_js_dialog_handler(page)

        if not _vahan_logged_in(page, dealer_id):
            # Only surface the automated window — never goto/reload the Vahan tab (Vahan's single-session
            # guard treats a forced navigation/refresh as "multiple tab / multiple location" and kills the
            # session, showing warning.xhtml). Let the operator log in on the window as-is.
            try:
                page.bring_to_front()
            except Exception:
                pass
            out["needs_login"] = True
            out["message"] = _NEEDS_LOGIN_MESSAGE
            _log(
                dealer_id,
                "Vahan not logged in in the AUTOMATED window — operator must log into the "
                f"script-controlled window (url={_page_url(page)[:200]}) and run again",
            )
            return out

        _log(dealer_id, f"starting HSRP report download for dealer_id={dealer_id}")

        if not _navigate_to_dealer_pendency(page, dealer_id):
            _dump_state(page, dealer_id, "navigate_to_dealer_pendency_failed")
            out["error"] = "Could not navigate to Dealer Registration Pendency"
            return out

        if not _click_get_details_and_confirm(page, dealer_id):
            _dump_state(page, dealer_id, "get_details_or_confirm_failed")
            out["error"] = "Could not click Get Details / confirm popup"
            return out

        saved = _download_excel(page, dealer_id)
        if saved is None:
            _dump_state(page, dealer_id, "download_excel_failed")
            out["error"] = "Could not download the Excel report"
            return out

        out["saved_path"] = str(saved)

        if load_to_db:
            load_result = load_hsrp_excel_to_holding(dealer_id, saved)
            out["rows_loaded"] = int(load_result.get("rows_loaded") or 0)
            out["plates_updated"] = int(load_result.get("plates_updated") or 0)
            if load_result.get("error"):
                # Download succeeded; surface DB issue without wiping saved_path.
                out["error"] = load_result["error"]
                out["success"] = True
                out["message"] = (
                    f"HSRP report saved to {saved} but DB load/apply failed: {load_result['error']}"
                )
                return out

        out["success"] = True
        out["message"] = (
            f"HSRP report saved to {saved}; "
            f"rows_loaded={out['rows_loaded']} plates_updated={out['plates_updated']}"
        )
        return out
    except Exception as e:
        out["error"] = str(e)
        logger.warning("vahan_hsrp_report_service: get_vahan_hsrp_report %s", e)
        try:
            if "page" in locals() and page is not None:
                _dump_state(page, dealer_id, "unexpected_exception")
        except Exception:
            pass
        return out
