"""CPA Alliance (third-party insurer) portal — Add Sales Playwright helper.

Opens the portal in the dedicated **CPAInsurance** native Chromium profile
(see :mod:`app.services.handle_browser_opening`), streams a trace to
``ocr_output/{dealer_id}/{subfolder}/playwright_cpa_<IST>.txt`` (same sale folder as DMS / OCR),
saves downloads under ``Uploaded scans/{dealer_id}/{subfolder}/``, and syncs to S3 when configured.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from app.config import ENVIRONMENT_IS_PRODUCTION, get_ocr_output_dir, get_uploads_dir
from app.services.dealer_storage import (
    sync_ocr_subfolder_to_s3,
    sync_uploads_subfolder_to_s3,
)
from app.services.fill_hero_dms_service import _safe_subfolder_name
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.services.handle_browser_opening import (
    _is_ready_after_login_page,
    _login_form_visible_any_frame,
    get_or_open_site_page,
    launch_site_background_detached,
)
from app.services.utility_functions import (
    alliance_model_match_score,
    fuzzy_best_alliance_model_label,
    fuzzy_best_option_label,
    fuzzy_option_match_score,
    normalize_dob_for_misp,
)

# After open: poll for manual login / SPA shell before clicking Issue New Certificate (not Hero insurance wait).
CPA_PORTAL_READY_MAX_POLLS = 14
CPA_PORTAL_READY_POLL_MS = 1_000
_ALLIANCE_LOGIN_AUTOFILL_MAX_MS = 14_000
_ALLIANCE_CONTINUE_PAUSE_MS = 500
_ALLIANCE_CONTINUE_MAX_ATTEMPTS = 4
# Alliance ``Plan`` dropdown preset (``master_ref`` / dealer SOP). When selected, plan amounts auto-fill.
ALLIANCE_CPA_PLAN_DEFAULT = "PLAN348 RGI"

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


def _resolve_alliance_cpa_plan_name() -> str:
    """``ALLIANCE_CPA_PLAN`` env override (default ``PLAN348 RGI``)."""
    return (os.getenv("ALLIANCE_CPA_PLAN") or ALLIANCE_CPA_PLAN_DEFAULT).strip() or ALLIANCE_CPA_PLAN_DEFAULT


def _resolve_cpa_portal_url(portal_url: str | None) -> str:
    """``ALLIANCE_CPA_PORTAL_URL`` wins over the URL from ``master_ref`` / UI."""
    env = (os.getenv("ALLIANCE_CPA_PORTAL_URL") or "").strip()
    if env:
        return env
    return (portal_url or "").strip()


def _resolve_cpa_playwright_log_path(dealer_id: int, subfolder: str) -> Path:
    """
    One trace file per sale folder per short window — re-append if CPA is invoked again within
    ``CPA_LOG_REUSE_SEC`` (avoids duplicate ``playwright_cpa_*.txt`` from double clicks / React dev).
    """
    safe = _safe_subfolder_name(subfolder)
    base = get_ocr_output_dir(int(dealer_id)) / safe
    base.mkdir(parents=True, exist_ok=True)
    reuse_sec = float(os.getenv("CPA_LOG_REUSE_SEC", "120"))
    now = time.time()
    try:
        candidates = sorted(base.glob("playwright_cpa_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        candidates = []
    if candidates and now - candidates[0].stat().st_mtime <= reuse_sec:
        return candidates[0]
    ts = datetime.now(_IST).strftime("%d%m%Y_%H%M%S")
    return base / f"playwright_cpa_{ts}.txt"


def _append_cpa_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")


def _alliance_issue_new_certificate_visible(page) -> bool:
    """Alliance Assure post-login home often exposes this before the certificate form."""
    for pat in (
        re.compile(r"Issue\s+New\s+Certificate", re.I),
        re.compile(r"Issue\s+New\s+Policy", re.I),
    ):
        for role in ("button", "link", "menuitem"):
            try:
                loc = page.get_by_role(role, name=pat)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _alliance_logged_in_dashboard_visible(page) -> bool:
    """SPA home after SSO — Issue New Certificate may be off-screen or behind a menu."""
    u = ""
    try:
        u = (page.url or "").lower()
    except Exception:
        pass
    if "allianceassure.in" not in u or "/login" in u or u.rstrip("/").endswith("/login"):
        return False
    for pat in (
        re.compile(r"sign\s*out", re.I),
        re.compile(r"log\s*out", re.I),
        re.compile(r"logout", re.I),
        re.compile(r"welcome", re.I),
        re.compile(r"my\s+certificates", re.I),
        re.compile(r"dashboard", re.I),
    ):
        for role in ("button", "link", "menuitem"):
            try:
                loc = page.get_by_role(role, name=pat)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _alliance_certificate_form_visible(page) -> bool:
    """Already inside issuance flow (deep link or second CPA press)."""
    for pat in (
        re.compile(r"chasis\s*number", re.I),
        re.compile(r"chassis", re.I),
        re.compile(r"frame\s*no", re.I),
        re.compile(r"\bvin\b", re.I),
        re.compile(r"vehicle\s+number", re.I),
    ):
        try:
            lbl = page.get_by_label(pat)
            if lbl.count() > 0 and lbl.first.is_visible():
                return True
        except Exception:
            pass
        try:
            ph = page.get_by_placeholder(pat)
            if ph.count() > 0 and ph.first.is_visible():
                return True
        except Exception:
            pass
    return False


_ALLIANCE_LOGIN_PASSWORD_READY_JS = """() => {
  const vis = (el) => {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const inputs = document.querySelectorAll('input[type="password"]');
  for (const el of inputs) {
    if (!vis(el)) continue;
    if ((el.value || '').trim().length > 0) return true;
    try {
      if (el.matches && el.matches(':-webkit-autofill')) return true;
    } catch (e) {}
  }
  return false;
}"""


def _still_on_alliance_login(page) -> bool:
    try:
        u = (page.url or "").lower()
    except Exception:
        return False
    return "allianceassure.in" in u and ("/login" in u or u.rstrip("/").endswith("/login"))


def _alliance_login_password_ready(page, *, timeout_ms: int) -> bool:
    """Wait until Alliance login password field has autofill or operator value."""
    try:
        page.wait_for_function(
            _ALLIANCE_LOGIN_PASSWORD_READY_JS,
            timeout=max(1, int(timeout_ms)),
        )
        logger.info("Alliance CPA: password field ready — proceeding to Continue.")
        return True
    except PlaywrightTimeout:
        pass
    except Exception:
        pass
    logger.debug(
        "Alliance CPA: no non-empty password field within %s ms — not clicking Continue.",
        timeout_ms,
    )
    return False


def _attempt_alliance_continue_click_once(page, *, timeout_ms: int) -> bool:
    patterns = (
        re.compile(r"^\s*Continue\s*$", re.I),
        re.compile(r"^\s*Sign\s+in\s*$", re.I),
        re.compile(r"^\s*Login\s*$", re.I),
    )
    for pat in patterns:
        try:
            btn = page.get_by_role("button", name=pat)
            if btn.count() > 0 and btn.first.is_visible(timeout=min(1200, timeout_ms)):
                btn.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    try:
        submit = page.locator('button[type="submit"].btn-primary').filter(
            has_text=re.compile(r"Continue", re.I)
        )
        if submit.count() > 0 and submit.first.is_visible(timeout=min(1200, timeout_ms)):
            submit.first.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def _try_click_alliance_continue(page, log_path: Path, *, timeout_ms: int = 8_000) -> bool:
    """
    Click Alliance Assure **Continue** after autofill (mirrors MISP Sign In retries).
    Returns True only when navigation leaves ``/account/login``.
    """
    if not _still_on_alliance_login(page):
        return True
    pwd_ready = _alliance_login_password_ready(page, timeout_ms=_ALLIANCE_LOGIN_AUTOFILL_MAX_MS)
    if not pwd_ready:
        return False
    for attempt in range(1, _ALLIANCE_CONTINUE_MAX_ATTEMPTS + 1):
        clicked = _attempt_alliance_continue_click_once(page, timeout_ms=timeout_ms)
        if clicked:
            _append_cpa_log(
                log_path,
                f"NOTE Alliance Continue clicked (attempt {attempt}/{_ALLIANCE_CONTINUE_MAX_ATTEMPTS}).",
            )
            try:
                page.wait_for_timeout(_ALLIANCE_CONTINUE_PAUSE_MS)
            except Exception:
                time.sleep(_ALLIANCE_CONTINUE_PAUSE_MS / 1000.0)
            if not _still_on_alliance_login(page):
                logger.info(
                    "Alliance CPA: Continue succeeded (left login) on attempt %s/%s.",
                    attempt,
                    _ALLIANCE_CONTINUE_MAX_ATTEMPTS,
                )
                return True
            logger.warning(
                "Alliance CPA: Continue click did not leave login (attempt %s/%s) — retrying.",
                attempt,
                _ALLIANCE_CONTINUE_MAX_ATTEMPTS,
            )
        if attempt < _ALLIANCE_CONTINUE_MAX_ATTEMPTS:
            try:
                page.wait_for_timeout(_ALLIANCE_CONTINUE_PAUSE_MS)
            except Exception:
                time.sleep(_ALLIANCE_CONTINUE_PAUSE_MS / 1000.0)
    _append_cpa_log(log_path, "NOTE Alliance Continue did not leave login after retries.")
    return not _still_on_alliance_login(page)


def _try_alliance_login_autofill_and_continue(page, log_path: Path) -> bool:
    """When on Alliance login with autofill, click Continue; no-op when already past login."""
    if not _still_on_alliance_login(page):
        return True
    return _try_click_alliance_continue(page, log_path)


def _is_cpa_portal_ready(page) -> bool:
    """Past login for CPA: generic probe plus Alliance Assure SPA (``app.allianceassure.in``)."""
    if _alliance_certificate_form_visible(page):
        return True
    if _alliance_issue_new_certificate_visible(page):
        return True
    if _alliance_logged_in_dashboard_visible(page):
        return True
    if _is_ready_after_login_page(page):
        return True
    try:
        u = (page.url or "").lower()
    except Exception:
        u = ""
    if "allianceassure.in" in u and "/login" not in u and not u.rstrip("/").endswith("/login"):
        try:
            if not _login_form_visible_any_frame(page):
                return True
        except Exception:
            return True
    return False


def _wait_cpa_portal_ready(page, log_path: Path) -> str | None:
    """Poll until past login / SPA ready, or give up after ``CPA_PORTAL_READY_MAX_POLLS`` × ``CPA_PORTAL_READY_POLL_MS``."""
    for attempt in range(1, CPA_PORTAL_READY_MAX_POLLS + 1):
        try:
            if page.is_closed():
                return "CPA browser tab closed while waiting for login."
        except Exception:
            return "CPA browser tab closed while waiting for login."
        try:
            if _still_on_alliance_login(page):
                _try_alliance_login_autofill_and_continue(page, log_path)
            if _is_cpa_portal_ready(page):
                _append_cpa_log(
                    log_path,
                    f"NOTE portal session ready (past login surface), poll {attempt}/{CPA_PORTAL_READY_MAX_POLLS}.",
                )
                return None
        except Exception as exc:
            logger.debug("add_alliance_cpa_insurance: readiness probe: %s", exc)
        if attempt < CPA_PORTAL_READY_MAX_POLLS:
            try:
                page.wait_for_timeout(CPA_PORTAL_READY_POLL_MS)
            except Exception:
                time.sleep(CPA_PORTAL_READY_POLL_MS / 1000.0)
    _append_cpa_log(
        log_path,
        f"NOTE portal not ready after {CPA_PORTAL_READY_MAX_POLLS} polls ({CPA_PORTAL_READY_POLL_MS}ms apart).",
    )
    return (
        "CPA portal still shows a login page after waiting. Log in, leave the tab open, "
        "then press CPA Insurance again."
    )


def _try_click_issue_new_certificate_alliance(page, log_path: Path) -> None:
    """Alliance Assure: open the certificate flow from the post-login shell (all environments)."""
    try:
        u = (page.url or "").lower()
    except Exception:
        u = ""
    if "allianceassure.in" not in u:
        return
    if _alliance_certificate_form_visible(page):
        _append_cpa_log(
            log_path,
            "NOTE Alliance certificate/policy form already visible — skipping Issue New Certificate click.",
        )
        return
    patterns = (
        re.compile(r"Issue\s+New\s+Certificate", re.I),
        re.compile(r"Issue\s+New\s+Policy", re.I),
    )
    for pat in patterns:
        for role in ("button", "link", "menuitem"):
            try:
                loc = page.get_by_role(role, name=pat)
                if loc.count() < 1:
                    continue
                first = loc.first
                if not first.is_visible():
                    continue
                try:
                    first.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                first.click(timeout=15_000)
                _append_cpa_log(
                    log_path,
                    f"NOTE clicked Alliance control role={role!r} pattern={pat.pattern!r}",
                )
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=12_000)
                except Exception:
                    try:
                        page.wait_for_timeout(1200)
                    except Exception:
                        time.sleep(1.2)
                return
            except Exception as exc:
                logger.debug(
                    "add_alliance_cpa_insurance: Alliance Issue click role=%s pat=%s: %s",
                    role,
                    getattr(pat, "pattern", pat),
                    exc,
                )
    _append_cpa_log(
        log_path,
        "NOTE Alliance 'Issue New Certificate' / 'Issue New Policy' not visible — field hints run on current page.",
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


def _iter_cpa_page_frames(page):
    """Main document first, then child frames (Alliance may host the form in an iframe)."""
    seen: set[int] = set()
    try:
        mf = page.main_frame
        seen.add(id(mf))
        yield mf
    except Exception:
        yield page
    try:
        for fr in page.frames:
            if id(fr) in seen:
                continue
            seen.add(id(fr))
            yield fr
    except Exception:
        pass


def _alliance_form_fields_visible(page) -> tuple[bool, str]:
    """True when certificate labels or enough visible inputs exist (main + child frames)."""
    if _alliance_certificate_form_visible(page):
        return True, "chassis/VIN labels"
    for root in _iter_cpa_page_frames(page):
        try:
            n = root.locator(
                "input:visible, textarea:visible, select:visible, [contenteditable='true']:visible"
            ).count()
            if n >= 3:
                return True, f"{n} visible inputs"
        except Exception:
            continue
    return False, ""


def _wait_alliance_form_after_issue_click(
    page, log_path: Path, *, max_attempts: int = 5, poll_ms: int = 500
) -> None:
    """After Issue New Certificate — poll up to 5× every 500ms for form fields."""
    for attempt in range(1, max_attempts + 1):
        ready, reason = _alliance_form_fields_visible(page)
        if ready:
            _append_cpa_log(
                log_path,
                f"NOTE Alliance form visible after navigation (poll {attempt}/{max_attempts}: {reason}).",
            )
            return
        if attempt < max_attempts:
            try:
                page.wait_for_timeout(poll_ms)
            except Exception:
                time.sleep(poll_ms / 1000.0)
    _append_cpa_log(
        log_path,
        f"NOTE Alliance form not visible after {max_attempts} polls ({poll_ms}ms apart) — fill will run on current DOM.",
    )


@dataclass
class CpaAllianceFillPayload:
    chassis: str = ""
    engine: str = ""
    make: str = ""
    model: str = ""
    year_of_mfg: str = ""
    vehicle_type: str = "New"
    client_type: str = "Individual"
    customer_name: str = ""
    mobile: str = ""
    gender: str = ""
    date_of_birth: str = ""
    address: str = ""
    state: str = ""
    city: str = ""
    plan_total_amount: str = "5400"
    nominee_name: str = ""
    nominee_relationship: str = ""
    nominee_gender: str = ""
    nominee_age: str = ""


def _split_person_name(full: str | None) -> tuple[str, str]:
    parts = (full or "").strip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _normalize_gender_alliance(raw: str | None) -> str:
    t = (raw or "").strip().lower()
    if t in ("m", "male", "man"):
        return "Male"
    if t in ("f", "female", "woman"):
        return "Female"
    return (raw or "").strip()


def _alliance_control_for_label(root, label_pat: re.Pattern[str]):
    """
    Resolve ``input`` / ``select`` / ``textarea`` for a label.

    Alliance Assure often uses ``<label>Text</label>`` adjacent to the control (siblings), not
    ``for=`` / wrapped controls — ``get_by_label`` then fails. Try sibling and shallow parent scopes.
    """
    labels = root.locator("label").filter(has_text=label_pat)
    try:
        n = labels.count()
    except Exception:
        return None
    for i in range(n):
        lab = labels.nth(i)
        try:
            if not lab.is_visible():
                continue
        except Exception:
            continue

        # Wrapped control (classic <label><input/></label>)
        try:
            inner = lab.locator("input, textarea, select").first
            if inner.count() > 0:
                try:
                    if inner.is_visible():
                        return inner
                except Exception:
                    pass
        except Exception:
            pass

        # Sibling control immediately after label (Alliance issueCertificate layout)
        for xpath in (
            "xpath=following-sibling::*[self::input or self::select or self::textarea][1]",
            "xpath=following-sibling::input[1]",
            "xpath=following-sibling::select[1]",
            "xpath=following-sibling::textarea[1]",
        ):
            try:
                sib = lab.locator(xpath)
                if sib.count() > 0:
                    first = sib.first
                    try:
                        if first.is_visible():
                            return first
                    except Exception:
                        pass
            except Exception:
                continue

        # Sibling wrapper (e.g. <label/><div class=…><select/></div>)
        try:
            wrap = lab.locator("xpath=following-sibling::*[1]")
            if wrap.count() > 0:
                inner2 = wrap.first.locator("input, textarea, select").first
                if inner2.count() > 0 and inner2.is_visible():
                    return inner2
        except Exception:
            pass

        # Same row: parent's direct field children (label + control under one div)
        try:
            par = lab.locator("xpath=parent::*")
            if par.count() > 0:
                row = par.first.locator(":scope > input, :scope > select, :scope > textarea")
                try:
                    rc = row.count()
                except Exception:
                    rc = 0
                for j in range(rc):
                    cand = row.nth(j)
                    try:
                        if cand.is_visible():
                            return cand
                    except Exception:
                        continue
        except Exception:
            pass

        # for= / id association (when present)
        try:
            fid = (lab.get_attribute("for") or "").strip()
            if fid:
                esc = fid.replace("\\", "\\\\").replace('"', '\\"')
                by_id = root.locator(f'[id="{esc}"]')
                if by_id.count() > 0 and by_id.first.is_visible():
                    return by_id.first
        except Exception:
            pass

    return None


def _locate_labeled_control(page, label_pat: re.Pattern[str]):
    for root in _iter_cpa_page_frames(page):
        try:
            gl = root.get_by_label(label_pat)
            if gl.count() > 0:
                first = gl.first
                try:
                    if first.is_visible():
                        return first
                except Exception:
                    pass
        except Exception:
            pass
        ctl = _alliance_control_for_label(root, label_pat)
        if ctl is not None:
            return ctl
    return None


def _select_options(loc) -> list[str]:
    """Visible option labels for a native ``<select>`` (evaluate + DOM fallback)."""
    return [label for label, _val in _select_option_entries(loc)]


def _select_option_entries(loc) -> list[tuple[str, str]]:
    """
    ``(label, value)`` for each ``<option>``. Alliance uses ``value="80: DESTINI …"`` with a
    separate text node — we normalize so fuzzy match can run on the human-readable model name.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        raw = loc.evaluate(
            """el => {
            const out = [];
            const seen = new Set();
            const opts = el.options || [];
            for (let i = 0; i < opts.length; i++) {
              const o = opts[i];
              let t = ((o.label || o.textContent || '') + '').trim();
              const v = ((o.value || '') + '').trim();
              if (!t && v) {
                const idx = v.indexOf(':');
                t = idx >= 0 ? v.slice(idx + 1).trim() : v;
              }
              if (t && !seen.has(t)) { seen.add(t); out.push([t, v]); }
            }
            return out;
        }"""
        )
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                label_t, val_t = str(item[0]).strip(), str(item[1]).strip()
                if label_t and label_t not in seen:
                    seen.add(label_t)
                    pairs.append((label_t, val_t))
    except Exception:
        pass
    if pairs:
        return pairs
    try:
        opts = loc.locator("option")
        n = opts.count()
        for i in range(n):
            opt = opts.nth(i)
            try:
                label_t = (opt.inner_text() or "").strip()
            except Exception:
                label_t = ""
            try:
                val_t = (opt.get_attribute("value") or "").strip()
            except Exception:
                val_t = ""
            if not label_t and val_t:
                label_t = val_t.split(":", 1)[-1].strip() if ":" in val_t else val_t
            if label_t and label_t not in seen:
                seen.add(label_t)
                pairs.append((label_t, val_t))
    except Exception:
        pass
    return pairs


def _wait_alliance_select_options(
    loc,
    log_path: Path,
    field: str,
    *,
    min_count: int = 1,
    timeout_ms: int = 18_000,
) -> bool:
    """Alliance populates ``<option>`` lists after **Make** (and async); poll until options exist."""
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000.0)
    pg = None
    try:
        pg = loc.page
    except Exception:
        pass
    while time.monotonic() < deadline:
        if len(_select_option_entries(loc)) >= min_count:
            return True
        try:
            if pg is not None:
                pg.wait_for_timeout(280)
            else:
                time.sleep(0.28)
        except Exception:
            time.sleep(0.28)
    n = len(_select_option_entries(loc))
    _append_cpa_log(log_path, f"NOTE {field}: waited for <option> list — still {n} option(s).")
    return n >= min_count


def _alliance_select_selected_label(loc) -> str:
    """Visible label of the currently selected ``<option>``."""
    try:
        raw = loc.evaluate(
            """el => {
            const o = el.options[el.selectedIndex];
            if (!o) return '';
            let t = ((o.label || o.textContent || '') + '').trim();
            const v = (o.value || '').trim();
            if (!t && v) {
              const i = v.indexOf(':');
              t = i >= 0 ? v.slice(i + 1).trim() : v;
            }
            return t;
        }"""
        )
        return (raw or "").strip()
    except Exception:
        return ""


def _normalize_exact_option_label(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _alliance_exact_option_match(query: str, label: str) -> bool:
    """Case-insensitive exact label match (no fuzzy)."""
    q = _normalize_exact_option_label(query)
    lab = _normalize_exact_option_label(label)
    if not q or not lab:
        return False
    if q == lab:
        return True
    # Manufacturing year: allow ``2026`` vs option text ``2026`` only (not partial state names).
    if re.fullmatch(r"\d{4}", q):
        return lab == q
    return False


def _alliance_exact_select_on_locator(
    loc,
    log_path: Path,
    query: str,
    *,
    field: str,
) -> bool:
    """Select ``<option>`` whose label exactly matches ``query`` (case/space normalized)."""
    q = (query or "").strip()
    if not q:
        return False
    try:
        tag = (loc.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
        if tag != "select":
            return False
    except Exception:
        return False
    try:
        loc.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    if not _wait_alliance_select_options(loc, log_path, field, min_count=1, timeout_ms=12_000):
        _append_cpa_log(log_path, f"NOTE {field}: select has no options (exact match)")
        return False
    entries = _select_option_entries(loc)
    pick = ""
    value_for_pick = ""
    for label, val in entries:
        if _alliance_exact_option_match(q, label):
            pick = label
            value_for_pick = val
            break
    if not pick:
        _append_cpa_log(
            log_path,
            f"NOTE {field}: no exact option for {q!r} among {len(entries)} options",
        )
        return False
    try:
        if value_for_pick:
            loc.select_option(value=value_for_pick, timeout=8_000)
        else:
            loc.select_option(label=pick, timeout=8_000)
        try:
            loc.dispatch_event("change")
            loc.dispatch_event("blur")
        except Exception:
            pass
        try:
            loc.page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
        selected = _alliance_select_selected_label(loc)
        if selected and _alliance_exact_option_match(q, selected):
            _append_cpa_log(log_path, f"NOTE selected Alliance {field}={selected!r} (exact)")
            return True
        _append_cpa_log(
            log_path,
            f"NOTE {field}: exact select did not stick (wanted {q!r}, got {selected!r})",
        )
        return False
    except Exception as exc:
        _append_cpa_log(log_path, f"NOTE {field} exact select failed: {exc}")
        return False


def _select_by_label_exact(
    page, log_path: Path, label_pat: re.Pattern[str], query: str, *, field: str
) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    loc = _locate_labeled_control(page, label_pat)
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no Alliance select for {field} (exact)")
        return False
    return _alliance_exact_select_on_locator(loc, log_path, q, field=field)


def _select_by_formcontrol_exact(
    page,
    log_path: Path,
    formcontrol_name: str,
    query: str,
    *,
    field: str,
) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    loc = None
    for root in _iter_cpa_page_frames(page):
        try:
            cands = root.locator(f'select[formcontrolname="{formcontrol_name}"]')
            if cands.count() > 0:
                first = cands.first
                try:
                    if first.is_visible():
                        loc = first
                        break
                except Exception:
                    loc = first
                    break
        except Exception:
            continue
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no select[formcontrolname={formcontrol_name!r}] for {field} (exact)")
        return False
    return _alliance_exact_select_on_locator(loc, log_path, q, field=field)


def _alliance_model_fuzzy_select_on_locator(
    loc,
    log_path: Path,
    query: str,
    *,
    field: str,
    min_score: float = 0.70,
) -> bool:
    """Model only: best fuzzy match with SPL+/Splendor+ normalization; must stick on the select."""
    q = (query or "").strip()
    if not q:
        return False
    try:
        tag = (loc.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
        if tag != "select":
            return False
    except Exception:
        return False
    try:
        loc.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    if not _wait_alliance_select_options(loc, log_path, field, min_count=1, timeout_ms=18_000):
        try:
            loc.click(timeout=3_000)
            loc.page.wait_for_timeout(350)
        except Exception:
            time.sleep(0.35)
        if not _wait_alliance_select_options(loc, log_path, field, min_count=1, timeout_ms=5_000):
            _append_cpa_log(log_path, f"NOTE {field}: select has no options")
            return False
    entries = _select_option_entries(loc)
    labels = [e[0] for e in entries]
    if not labels:
        _append_cpa_log(log_path, f"NOTE {field}: select has no options")
        return False
    pick = fuzzy_best_alliance_model_label(q, labels, min_score=min_score)
    if not pick:
        _append_cpa_log(
            log_path,
            f"NOTE {field}: no model option at or above {min_score:.0%} for {q!r}",
        )
        return False
    value_for_pick = ""
    for lab, val in entries:
        if lab == pick:
            value_for_pick = val
            break
    pick_score = alliance_model_match_score(q, pick)
    _append_cpa_log(
        log_path,
        f"NOTE {field}: best model match score={pick_score:.2f} label={pick!r}",
    )
    for attempt_value, attempt_label in ((value_for_pick, None), (None, pick)):
        try:
            if attempt_value:
                loc.select_option(value=attempt_value, timeout=8_000)
            else:
                loc.select_option(label=pick, timeout=8_000)
            try:
                loc.dispatch_event("change")
                loc.dispatch_event("blur")
            except Exception:
                pass
            try:
                loc.page.wait_for_timeout(200)
            except Exception:
                time.sleep(0.2)
            selected = _alliance_select_selected_label(loc)
            if selected and alliance_model_match_score(q, selected) >= min_score:
                _append_cpa_log(log_path, f"NOTE selected Alliance {field}={selected!r}")
                return True
        except Exception:
            continue
    stuck = _alliance_select_selected_label(loc)
    _append_cpa_log(
        log_path,
        f"NOTE {field}: model selection did not stick (selected={stuck!r})",
    )
    return False


def _alliance_fuzzy_select_on_locator(
    loc,
    log_path: Path,
    q: str,
    *,
    field: str,
    min_score: float = 0.35,
) -> bool:
    """Pick best option by fuzzy label match and ``select_option`` (label=, then value=)."""
    query = (q or "").strip()
    if not query:
        return False
    try:
        tag = (loc.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
        if tag != "select":
            return False
    except Exception:
        return False
    try:
        loc.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    if not _wait_alliance_select_options(loc, log_path, field, min_count=1, timeout_ms=18_000):
        try:
            loc.click(timeout=3_000)
            pg = loc.page
            pg.wait_for_timeout(350)
        except Exception:
            time.sleep(0.35)
        if not _wait_alliance_select_options(loc, log_path, field, min_count=1, timeout_ms=5_000):
            _append_cpa_log(log_path, f"NOTE {field}: select has no options")
            return False
    entries = _select_option_entries(loc)
    labels = [e[0] for e in entries]
    if not labels:
        _append_cpa_log(log_path, f"NOTE {field}: select has no options")
        return False
    pick = fuzzy_best_option_label(query, labels, min_score=min_score)
    if not pick:
        _append_cpa_log(
            log_path,
            f"NOTE {field}: no option at or above {min_score:.0%} fuzzy match for {query!r} among {len(labels)} options",
        )
        return False
    value_for_pick = ""
    for lab, val in entries:
        if lab == pick:
            value_for_pick = val
            break
    pick_score = fuzzy_option_match_score(query, pick)
    _append_cpa_log(
        log_path,
        f"NOTE {field}: fuzzy match score={pick_score:.2f} label={pick!r}",
    )
    selected_ok = False
    last_exc: Exception | None = None
    for attempt_value, attempt_label in (
        (value_for_pick, None),
        (None, pick),
    ):
        try:
            if attempt_value:
                loc.select_option(value=attempt_value, timeout=8_000)
            else:
                loc.select_option(label=attempt_label or pick, timeout=8_000)
            try:
                loc.dispatch_event("change")
                loc.dispatch_event("blur")
            except Exception:
                pass
            try:
                pg = loc.page
                pg.wait_for_timeout(200)
            except Exception:
                time.sleep(0.2)
            selected = _alliance_select_selected_label(loc)
            if selected and fuzzy_option_match_score(query, selected) >= min_score:
                selected_ok = True
                _append_cpa_log(log_path, f"NOTE selected Alliance {field}={selected!r}")
                break
            if selected and selected != pick:
                _append_cpa_log(
                    log_path,
                    f"NOTE {field}: select_option returned label={selected!r} (expected {pick!r})",
                )
        except Exception as exc:
            last_exc = exc
            continue
    if selected_ok:
        return True
    if last_exc is not None:
        _append_cpa_log(log_path, f"NOTE {field} select_option failed: {last_exc}")
    else:
        stuck = _alliance_select_selected_label(loc)
        _append_cpa_log(
            log_path,
            f"NOTE {field}: selection did not stick (selected={stuck!r}, need score>={min_score:.0%})",
        )
    return False


def _select_by_formcontrol_fuzzy(
    page,
    log_path: Path,
    formcontrol_name: str,
    query: str,
    *,
    field: str,
    min_score: float = 0.35,
) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    loc = None
    for root in _iter_cpa_page_frames(page):
        try:
            cands = root.locator(f'select[formcontrolname="{formcontrol_name}"]')
            if cands.count() > 0:
                first = cands.first
                try:
                    if first.is_visible():
                        loc = first
                        break
                except Exception:
                    loc = first
                    break
        except Exception:
            continue
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no visible select[formcontrolname={formcontrol_name!r}] for {field}")
        return False
    return _alliance_fuzzy_select_on_locator(loc, log_path, q, field=field, min_score=min_score)


def _fill_text_by_label(
    page, log_path: Path, label_pat: re.Pattern[str], val: str, *, field: str
) -> bool:
    v = (val or "").strip()
    if not v:
        return False
    loc = _locate_labeled_control(page, label_pat)
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no Alliance control for {field}")
        return False
    try:
        try:
            loc.scroll_into_view_if_needed(timeout=2_000)
        except Exception:
            pass
        tag = (loc.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
        if tag == "select":
            opts = _select_options(loc)
            pick = fuzzy_best_option_label(v, opts, min_score=0.35) if opts else None
            if not pick:
                _append_cpa_log(log_path, f"NOTE {field}: no fuzzy option for {v!r}")
                return False
            loc.select_option(label=pick, timeout=5_000)
        else:
            loc.fill(v[:256], timeout=4_000)
        _append_cpa_log(log_path, f"NOTE filled Alliance {field}")
        return True
    except Exception as exc:
        _append_cpa_log(log_path, f"NOTE {field} fill failed: {exc}")
        return False


def _select_by_label_fuzzy(
    page, log_path: Path, label_pat: re.Pattern[str], query: str, *, field: str, min_score: float = 0.35
) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    loc = _locate_labeled_control(page, label_pat)
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no Alliance select for {field}")
        return False
    try:
        tag = (loc.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
        if tag != "select":
            return _fill_text_by_label(page, log_path, label_pat, q, field=field)
    except Exception:
        return False
    return _alliance_fuzzy_select_on_locator(loc, log_path, q, field=field, min_score=min_score)


def _select_by_label_model_fuzzy(
    page,
    log_path: Path,
    label_pat: re.Pattern[str],
    query: str,
    *,
    field: str,
    min_score: float = 0.70,
) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    loc = _locate_labeled_control(page, label_pat)
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no Alliance model select for {field}")
        return False
    return _alliance_model_fuzzy_select_on_locator(loc, log_path, q, field=field, min_score=min_score)


def _select_by_formcontrol_model_fuzzy(
    page,
    log_path: Path,
    formcontrol_name: str,
    query: str,
    *,
    field: str,
    min_score: float = 0.70,
) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    loc = None
    for root in _iter_cpa_page_frames(page):
        try:
            cands = root.locator(f'select[formcontrolname="{formcontrol_name}"]')
            if cands.count() > 0:
                first = cands.first
                try:
                    if first.is_visible():
                        loc = first
                        break
                except Exception:
                    loc = first
                    break
        except Exception:
            continue
    if loc is None:
        _append_cpa_log(log_path, f"NOTE no select[formcontrolname={formcontrol_name!r}] for {field}")
        return False
    return _alliance_model_fuzzy_select_on_locator(loc, log_path, q, field=field, min_score=min_score)


def _try_fill_alliance_certificate_form(page, log_path: Path, payload: CpaAllianceFillPayload) -> tuple[int, list[str]]:
    """Alliance ``issueCertificate`` full SOP. Returns (filled_count, missing_required)."""
    filled = 0
    missing_required: list[str] = []
    first_name, last_name = _split_person_name(payload.customer_name)
    dob = normalize_dob_for_misp(payload.date_of_birth)

    steps: list[tuple[str, bool, bool]] = []  # field, ok, required

    # Make must be chosen before Model / Manufacturing Year — Alliance loads dependent <option> lists.
    need_make = bool((payload.model or "").strip() or (payload.year_of_mfg or "").strip())
    make_val = (payload.make or "").strip()
    if need_make and not make_val:
        make_val = (os.getenv("ALLIANCE_CPA_DEFAULT_MAKE") or "Hero MotoCorp Limited").strip()
    if make_val:
        ok = _select_by_label_fuzzy(page, log_path, re.compile(r"^make$", re.I), make_val, field="Make")
        steps.append(("make", ok, False))
        try:
            page.wait_for_timeout(550)
        except Exception:
            time.sleep(0.55)

    if payload.model:
        ok = _select_by_label_model_fuzzy(
            page, log_path, re.compile(r"^model$", re.I), payload.model, field="Model", min_score=0.70
        )
        if not ok:
            ok = _select_by_formcontrol_model_fuzzy(
                page, log_path, "model", payload.model, field="Model", min_score=0.70
            )
        steps.append(("model", ok, False))
    if payload.year_of_mfg:
        year_m = re.search(r"\d{4}", str(payload.year_of_mfg))
        year_s = year_m.group(0) if year_m else str(payload.year_of_mfg).strip()
        ok = _select_by_label_exact(
            page,
            log_path,
            re.compile(r"manufacturing\s*year", re.I),
            year_s,
            field="Manufacturing Year",
        )
        if not ok:
            ok = _select_by_formcontrol_exact(
                page, log_path, "vehicleYear", year_s, field="Manufacturing Year"
            )
        steps.append(("year_of_mfg", ok, False))
    ok = _select_by_label_fuzzy(
        page, log_path, re.compile(r"^type$", re.I), payload.vehicle_type or "New", field="Type"
    )
    steps.append(("vehicle_type", ok, False))

    ok = _fill_text_by_label(page, log_path, re.compile(r"chasis\s*number", re.I), payload.chassis, field="Chasis Number")
    steps.append(("chassis", ok, True))
    ok = _fill_text_by_label(page, log_path, re.compile(r"engine\s*number", re.I), payload.engine, field="Engine Number")
    steps.append(("engine", ok, True))

    ok = _select_by_label_fuzzy(
        page, log_path, re.compile(r"client\s*type", re.I), payload.client_type or "Individual", field="Client Type"
    )
    steps.append(("client_type", ok, False))

    if first_name or payload.customer_name:
        ok = _fill_text_by_label(
            page,
            log_path,
            re.compile(r"first\s*name", re.I),
            first_name or payload.customer_name,
            field="First Name",
        )
        steps.append(("first_name", ok, True))
    if last_name:
        ok = _fill_text_by_label(page, log_path, re.compile(r"last\s*name", re.I), last_name, field="Last Name")
        steps.append(("last_name", ok, False))

    ok = _fill_text_by_label(
        page, log_path, re.compile(r"contact\s*number", re.I), payload.mobile, field="Contact Number"
    )
    steps.append(("mobile", ok, True))

    if payload.gender:
        ok = _select_by_label_fuzzy(
            page,
            log_path,
            re.compile(r"^gender$", re.I),
            _normalize_gender_alliance(payload.gender),
            field="Gender",
        )
        steps.append(("gender", ok, False))
    if dob:
        ok = _fill_text_by_label(page, log_path, re.compile(r"date\s*of\s*birth", re.I), dob, field="Date of Birth")
        steps.append(("date_of_birth", ok, False))

    if payload.address:
        ok = _fill_text_by_label(page, log_path, re.compile(r"^address$", re.I), payload.address, field="Address")
        steps.append(("address", ok, False))
    if payload.state:
        ok = _select_by_label_exact(page, log_path, re.compile(r"^state$", re.I), payload.state, field="State")
        steps.append(("state", ok, False))
        if ok:
            try:
                page.wait_for_timeout(650)
            except Exception:
                time.sleep(0.65)
    if payload.city:
        ok = _select_by_label_exact(page, log_path, re.compile(r"^city$", re.I), payload.city, field="City")
        steps.append(("city", ok, False))

    plan_name = _resolve_alliance_cpa_plan_name()
    plan_selected = False
    if plan_name:
        ok = _select_by_label_fuzzy(
            page,
            log_path,
            re.compile(r"^plan$", re.I),
            plan_name,
            field="Plan",
            min_score=0.72,
        )
        if not ok and "PLAN348" in plan_name.upper():
            ok = _select_by_label_fuzzy(
                page,
                log_path,
                re.compile(r"^plan$", re.I),
                "PLAN348",
                field="Plan",
                min_score=0.65,
            )
        steps.append(("plan", ok, False))
        if ok:
            plan_selected = True
            try:
                page.wait_for_timeout(900)
            except Exception:
                time.sleep(0.9)
            _append_cpa_log(
                log_path,
                f"NOTE Plan preset {plan_name!r} selected — skipping manual Plan Total Amount entry.",
            )

    if not plan_selected:
        plan_amt = (payload.plan_total_amount or "5400").strip() or "5400"
        loc = None
        for root in _iter_cpa_page_frames(page):
            try:
                labels = root.locator("label").filter(has_text=re.compile(r"^total amount$", re.I))
                if labels.count() > 0:
                    loc = labels.last.locator("xpath=following-sibling::input[1]")
                    if loc.count() < 1:
                        loc = labels.last.locator("input")
            except Exception:
                continue
        if loc is not None:
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(plan_amt, timeout=4_000)
                    _append_cpa_log(log_path, f"NOTE filled Alliance Plan Total Amount={plan_amt}")
                    steps.append(("plan_total_amount", True, False))
                else:
                    steps.append(("plan_total_amount", False, False))
            except Exception as exc:
                _append_cpa_log(log_path, f"NOTE Plan Total Amount fill failed: {exc}")
                steps.append(("plan_total_amount", False, False))
        else:
            ok = _fill_text_by_label(
                page,
                log_path,
                re.compile(r"^total amount$", re.I),
                plan_amt,
                field="Plan Total Amount",
            )
            steps.append(("plan_total_amount", ok, False))

    if payload.nominee_name:
        ok = _fill_text_by_label(
            page, log_path, re.compile(r"nominee\s*name", re.I), payload.nominee_name, field="Nominee Name"
        )
        steps.append(("nominee_name", ok, False))
    if payload.nominee_relationship:
        ok = _select_by_label_fuzzy(
            page,
            log_path,
            re.compile(r"nominee\s*relationship", re.I),
            payload.nominee_relationship,
            field="Nominee Relationship",
        )
        steps.append(("nominee_relationship", ok, False))
    if payload.nominee_gender:
        ok = _select_by_label_fuzzy(
            page,
            log_path,
            re.compile(r"nominee\s*gender", re.I),
            _normalize_gender_alliance(payload.nominee_gender),
            field="Nominee Gender",
        )
        steps.append(("nominee_gender", ok, False))
    if payload.nominee_age:
        ok = _fill_text_by_label(
            page, log_path, re.compile(r"nominee\s*age", re.I), str(payload.nominee_age).strip(), field="Nominee Age"
        )
        steps.append(("nominee_age", ok, False))

    for field, ok, required in steps:
        if ok:
            filled += 1
        elif required:
            missing_required.append(field)

    return filled, missing_required


def _scroll_alliance_form_to_bottom(page) -> None:
    """Save sits below Plan / Nominee sections — ensure it is in view before click."""
    try:
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(400)
    except Exception:
        try:
            page.keyboard.press("End")
            page.wait_for_timeout(350)
        except Exception:
            time.sleep(0.35)


def _locate_alliance_save_button(page):
    """Visible **Save** on certificate form (main document + child frames)."""
    save_pat = re.compile(r"^save$", re.I)
    candidates: list = []
    for root in _iter_cpa_page_frames(page):
        locators = (
            root.locator("button").filter(has_text=save_pat),
            root.get_by_role("button", name=save_pat),
        )
        for loc in locators:
            try:
                n = loc.count()
            except Exception:
                continue
            for i in range(n):
                try:
                    btn = loc.nth(i)
                    if btn.is_visible():
                        candidates.append(btn)
                except Exception:
                    continue
    if not candidates:
        return None
    return candidates[-1]


def _click_alliance_save_button(page, log_path: Path) -> bool:
    """Click the bottom **Save** button; return True when click was dispatched."""
    _scroll_alliance_form_to_bottom(page)
    btn = _locate_alliance_save_button(page)
    if btn is None:
        _append_cpa_log(log_path, "ERROR Save button not found (searched all frames).")
        return False
    try:
        btn.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    try:
        if btn.is_disabled():
            _append_cpa_log(log_path, "NOTE Save button is disabled — form may be incomplete.")
            return False
    except Exception:
        pass
    last_exc: Exception | None = None
    for force in (False, True):
        try:
            btn.click(timeout=12_000, force=force)
            try:
                page.wait_for_timeout(1_200)
            except Exception:
                time.sleep(1.2)
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass
            _append_cpa_log(
                log_path,
                f"NOTE clicked Save (production ENVIRONMENT, force={force}).",
            )
            return True
        except Exception as exc:
            last_exc = exc
            continue
    _append_cpa_log(log_path, f"ERROR Save click failed: {last_exc}")
    return False


def _maybe_click_save_alliance(page, log_path: Path) -> bool:
    """Production only: click **Save** on Alliance certificate form."""
    if not ENVIRONMENT_IS_PRODUCTION:
        _append_cpa_log(log_path, "NOTE skipping Save (non-production ENVIRONMENT).")
        return False
    try:
        page.wait_for_timeout(500)
    except Exception:
        time.sleep(0.5)
    if not _click_alliance_save_button(page, log_path):
        _append_cpa_log(log_path, "ERROR Save was not clicked successfully.")
        return False
    return True


def _mobile_fn_for_cpa_download(mobile: str) -> str:
    dig = re.sub(r"\D", "", str(mobile or ""))
    if len(dig) >= 10:
        return dig[-10:]
    if dig:
        return dig.zfill(10)[:10]
    return "0000000000"


def _cpa_ddmmyyyy_from_subfolder(subfolder: str) -> str:
    safe = _safe_subfolder_name(subfolder)
    m = re.search(r"(\d{8})\s*$", safe)
    if m:
        return m.group(1)
    return datetime.now(_IST).strftime("%d%m%Y")


def _collect_cpa_frame_dump_lines(page, *, reason: str) -> list[str]:
    lines = [
        f"timestamp_ist={datetime.now(_IST).isoformat()} reason={reason!r} url={(page.url or '')!r}",
        "--- CPA Alliance frame dump ---",
    ]
    idx = 0
    for root in _iter_cpa_page_frames(page):
        try:
            u = (root.url or "")[:400]
        except Exception:
            u = ""
        lines.append(f"--- frame[{idx}] url={u!r} ---")
        interactive = 0
        try:
            for el in root.locator("button:visible, a:visible, input:visible, select:visible, label:visible").all()[
                :120
            ]:
                tag = ""
                try:
                    tag = (el.evaluate("e => (e.tagName || '').toLowerCase()") or "").strip()
                except Exception:
                    pass
                txt = ""
                try:
                    txt = (el.inner_text(timeout=500) or "").strip().replace("\n", " ")[:80]
                except Exception:
                    pass
                attrs = ""
                try:
                    attrs = el.evaluate(
                        """e => {
                        const a = [];
                        for (const k of ['id','name','type','formcontrolname','placeholder','href','role']) {
                          const v = e.getAttribute(k);
                          if (v) a.push(k + '=' + JSON.stringify(v));
                        }
                        return a.join(' ');
                        }"""
                    )
                except Exception:
                    pass
                lines.append(f"  [{interactive}] <{tag}> txt={txt!r} {attrs}")
                interactive += 1
        except Exception as exc:
            lines.append(f"  enumerate_error={exc!s}")
        idx += 1
    return lines


def _write_cpa_frame_dump(
    page,
    log_path: Path,
    *,
    reason: str,
    ocr_dir: Path,
    subfolder: str,
) -> str | None:
    if not subfolder or not str(subfolder).strip():
        return None
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (reason or "frame_dump").strip())[:80] or "frame_dump"
    ts = datetime.now(_IST).strftime("%d%m%Y_%H%M%S")
    out_dir = Path(ocr_dir) / _safe_subfolder_name(subfolder)
    name = f"cpa_frame_dump_{slug}_{ts}.txt"
    path = out_dir / name
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        body = "\n".join(_collect_cpa_frame_dump_lines(page, reason=reason))
        path.write_text(body, encoding="utf-8")
        _append_cpa_log(log_path, f"NOTE frame dump written {name}")
        return name
    except OSError as exc:
        _append_cpa_log(log_path, f"WARNING frame dump write failed: {exc}")
        return None


def _wait_for_alliance_print_new(page, log_path: Path, *, max_wait_ms: int = 25_000) -> bool:
    deadline = time.time() + max_wait_ms / 1000.0
    while time.time() < deadline:
        try:
            u = (page.url or "").lower()
        except Exception:
            u = ""
        if "printnew" in u or "/printnew" in u:
            _append_cpa_log(log_path, f"NOTE post-save page ready url={u[:120]!r}")
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass
            return True
        try:
            page.wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    _append_cpa_log(log_path, "NOTE post-save: printNew URL not seen within timeout.")
    return False


def _click_alliance_download_certificate(
    page,
    log_path: Path,
    *,
    dealer_id: int,
    ocr_dir: Path,
    subfolder: str,
    mobile: str,
) -> bool:
    patterns = (
        re.compile(r"download\s+certificate", re.I),
        re.compile(r"download\s+cert", re.I),
    )
    btn = None
    for root in _iter_cpa_page_frames(page):
        for pat in patterns:
            try:
                loc = root.get_by_role("button", name=pat)
                if loc.count() > 0 and loc.first.is_visible():
                    btn = loc.first
                    break
                loc = root.locator("button, a").filter(has_text=pat)
                if loc.count() > 0 and loc.first.is_visible():
                    btn = loc.first
                    break
            except Exception:
                continue
        if btn is not None:
            break
    if btn is None:
        _write_cpa_frame_dump(
            page, log_path, reason="download_certificate_not_found", ocr_dir=ocr_dir, subfolder=subfolder
        )
        _append_cpa_log(log_path, "ERROR Download certificate control not found.")
        return False
    mob_fn = _mobile_fn_for_cpa_download(mobile)
    ddmm = _cpa_ddmmyyyy_from_subfolder(subfolder)
    dest_name = f"{mob_fn}_CPA_{ddmm}.pdf"
    uploads_root = get_uploads_dir(int(dealer_id)) / _safe_subfolder_name(subfolder)
    try:
        btn.scroll_into_view_if_needed(timeout=3_000)
        with page.expect_download(timeout=90_000) as dl_info:
            btn.click(timeout=15_000)
        download = dl_info.value
        uploads_root.mkdir(parents=True, exist_ok=True)
        dest = uploads_root / dest_name
        download.save_as(str(dest))
        download.delete()
        _append_cpa_log(log_path, f"NOTE certificate PDF saved uploads/{subfolder}/{dest.name}")
        return True
    except Exception as exc:
        _append_cpa_log(log_path, f"ERROR Download certificate failed: {exc}")
        _write_cpa_frame_dump(
            page, log_path, reason="download_certificate_click_failed", ocr_dir=ocr_dir, subfolder=subfolder
        )
        return False


def _navigate_alliance_print_certificate(page, log_path: Path) -> bool:
    for root in _iter_cpa_page_frames(page):
        for sel in ('a[href="/reports/printCertificate"]', 'a[routerlink="/reports/printCertificate"]'):
            try:
                loc = root.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=12_000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=18_000)
                    except Exception:
                        pass
                    _append_cpa_log(log_path, "NOTE navigated to Print Certificate report.")
                    return True
            except Exception:
                continue
        try:
            loc = root.get_by_role("link", name=re.compile(r"print\s+certificate", re.I))
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=12_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=18_000)
                except Exception:
                    pass
                _append_cpa_log(log_path, "NOTE navigated via Print Certificate nav link.")
                return True
        except Exception:
            continue
    _append_cpa_log(log_path, "ERROR Print Certificate nav link not found.")
    return False


def _alliance_print_certificate_search_chassis(page, log_path: Path, chassis: str) -> bool:
    ch = (chassis or "").strip()
    if not ch:
        _append_cpa_log(log_path, "ERROR Print Certificate search: chassis empty.")
        return False
    filled = False
    for root in _iter_cpa_page_frames(page):
        try:
            loc = root.locator('input[formcontrolname="chasisNumber"]')
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.fill(ch, timeout=5_000)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        filled = _fill_text_by_label(
            page, log_path, re.compile(r"chasis\s*number", re.I), ch, field="Chasis Number"
        )
    if not filled:
        _append_cpa_log(log_path, "ERROR Chasis Number field not found on Print Certificate.")
        return False
    for root in _iter_cpa_page_frames(page):
        try:
            btn = root.get_by_role("button", name=re.compile(r"^search$", re.I))
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=10_000)
                try:
                    page.wait_for_timeout(1_500)
                except Exception:
                    time.sleep(1.5)
                _append_cpa_log(log_path, f"NOTE Print Certificate Search for chassis={ch[:24]!r}")
                return True
        except Exception:
            continue
    _append_cpa_log(log_path, "ERROR Search button not found on Print Certificate.")
    return False


def _scrape_alliance_certificate_number(page, log_path: Path) -> str | None:
    cert_hdr = re.compile(r"certificate\s*number", re.I)
    for root in _iter_cpa_page_frames(page):
        try:
            n_tables = root.locator("table").count()
        except Exception:
            n_tables = 0
        for ti in range(min(n_tables, 6)):
            try:
                table = root.locator("table").nth(ti)
                headers = table.locator("th")
                col_idx: int | None = None
                for hi in range(headers.count()):
                    ht = (headers.nth(hi).inner_text(timeout=1_500) or "").strip()
                    if cert_hdr.search(ht):
                        col_idx = hi
                        break
                if col_idx is None:
                    continue
                rows = table.locator("tbody tr")
                if rows.count() < 1:
                    rows = table.locator("tr")
                body_row = rows.first
                cell = body_row.locator("td").nth(col_idx)
                val = (cell.inner_text(timeout=3_000) or "").strip()
                if val and val not in ("—", "-", "N/A"):
                    clean = re.sub(r"\s+", " ", val)[:24]
                    _append_cpa_log(log_path, f"NOTE scraped Certificate Number={clean!r}")
                    return clean
            except Exception as exc:
                _append_cpa_log(log_path, f"NOTE certificate table scrape: {exc}")
    return None


def _alliance_post_save_certificate_flow(
    page,
    log_path: Path,
    payload: CpaAllianceFillPayload,
    *,
    dealer_id: int,
    safe_sub: str,
    ocr_dir: Path,
) -> dict[str, Any]:
    """After Save → ``/printNew``: download PDF, Print Certificate search, scrape cert #."""
    result: dict[str, Any] = {
        "certificate_number": None,
        "download_ok": False,
        "print_search_ok": False,
    }
    if not _wait_for_alliance_print_new(page, log_path) and "printnew" not in (page.url or "").lower():
        return result
    result["download_ok"] = _click_alliance_download_certificate(
        page,
        log_path,
        dealer_id=dealer_id,
        ocr_dir=ocr_dir,
        subfolder=safe_sub,
        mobile=payload.mobile,
    )
    if not _navigate_alliance_print_certificate(page, log_path):
        _write_cpa_frame_dump(
            page, log_path, reason="print_certificate_nav", ocr_dir=ocr_dir, subfolder=safe_sub
        )
        return result
    result["print_search_ok"] = _alliance_print_certificate_search_chassis(
        page, log_path, payload.chassis
    )
    if not result["print_search_ok"]:
        _write_cpa_frame_dump(
            page, log_path, reason="print_certificate_search", ocr_dir=ocr_dir, subfolder=safe_sub
        )
        return result
    cert = _scrape_alliance_certificate_number(page, log_path)
    result["certificate_number"] = cert
    if not cert:
        _write_cpa_frame_dump(
            page,
            log_path,
            reason="certificate_number_not_found",
            ocr_dir=ocr_dir,
            subfolder=safe_sub,
        )
    return result


def _fill_one_hint_on_root(root, hint: str, val: str) -> bool:
    """Try label, placeholder, role, and name/id selectors on one frame root."""
    rx = re.compile(re.escape(hint), re.I)
    rx_loose = re.compile(hint.replace("_", r"[\s_-]*"), re.I)
    strategies: list[tuple[str, object]] = [
        ("label", root.get_by_label(rx)),
        ("placeholder", root.get_by_placeholder(rx)),
        ("role_textbox", root.get_by_role("textbox", name=rx)),
        ("role_combobox", root.get_by_role("combobox", name=rx)),
        ("css_name", root.locator(f"input[name*='{hint}' i], textarea[name*='{hint}' i], select[name*='{hint}' i]")),
        ("css_id", root.locator(f"input[id*='{hint}' i], textarea[id*='{hint}' i], select[id*='{hint}' i]")),
        ("label_loose", root.locator("label").filter(has_text=rx_loose).locator("input, textarea, select").first),
    ]
    for _kind, loc in strategies:
        try:
            if loc.count() < 1:
                continue
            first = loc.first
            if not first.is_visible():
                continue
            try:
                first.scroll_into_view_if_needed(timeout=2_000)
            except Exception:
                pass
            tag = ""
            try:
                tag = (first.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
            except Exception:
                pass
            if tag == "select":
                first.select_option(label=val[:128])
            else:
                first.fill(val[:256], timeout=4_000)
            return True
        except Exception:
            continue
    return False


def _try_fill_labeled_fields(
    page, log_path: Path, pairs: list[tuple[str, str | None]]
) -> int:
    """
    Best-effort fill across main document and child frames.
    Returns count of hints successfully filled.
    """
    filled = 0
    roots = list(_iter_cpa_page_frames(page))
    for hint, raw in pairs:
        val = (raw or "").strip()
        if not val:
            continue
        matched = False
        for root in roots:
            if _fill_one_hint_on_root(root, hint, val):
                matched = True
                break
        if matched:
            filled += 1
            _append_cpa_log(log_path, f"NOTE filled field hint={hint!r} value_len={len(val)}")
        else:
            _append_cpa_log(log_path, f"NOTE no match for fill hint={hint!r} value_len={len(val)}")
    return filled


def _build_cpa_alliance_payload(
    *,
    customer_name: str | None = None,
    mobile: str | None = None,
    frame_no: str | None = None,
    engine_no: str | None = None,
    full_chassis: str | None = None,
    full_engine: str | None = None,
    make: str | None = None,
    model: str | None = None,
    year_of_mfg: str | None = None,
    vehicle_type: str | None = None,
    client_type: str | None = None,
    gender: str | None = None,
    date_of_birth: str | None = None,
    address: str | None = None,
    state: str | None = None,
    city: str | None = None,
    plan_total_amount: str | None = None,
    nominee_name: str | None = None,
    nominee_relationship: str | None = None,
    nominee_gender: str | None = None,
    nominee_age: str | None = None,
) -> CpaAllianceFillPayload:
    def _s(v: str | None) -> str:
        return (v or "").strip()

    return CpaAllianceFillPayload(
        chassis=_s(full_chassis) or _s(frame_no),
        engine=_s(full_engine) or _s(engine_no),
        make=_s(make),
        model=_s(model),
        year_of_mfg=_s(year_of_mfg),
        vehicle_type=_s(vehicle_type) or "New",
        client_type=_s(client_type) or "Individual",
        customer_name=_s(customer_name),
        mobile=_s(mobile),
        gender=_s(gender),
        date_of_birth=_s(date_of_birth),
        address=_s(address),
        state=_s(state),
        city=_s(city),
        plan_total_amount=_s(plan_total_amount) or "5400",
        nominee_name=_s(nominee_name),
        nominee_relationship=_s(nominee_relationship),
        nominee_gender=_s(nominee_gender),
        nominee_age=_s(nominee_age),
    )


def warm_cpa_browser_session(portal_url: str | None = None) -> dict:
    """
    Pre-open or attach to the CPA Alliance portal without running fill automation.
    On Windows, a new managed browser starts minimized via ``launch_background=True``.
    """
    out: dict = {"success": False, "error": None}
    resolved = _resolve_cpa_portal_url(portal_url)
    if not resolved:
        out["error"] = (
            "CPA portal URL missing. Set ALLIANCE_CPA_PORTAL_URL in backend/.env "
            "or pass portal_url."
        )
        return out
    try:
        page, open_error = get_or_open_site_page(
            resolved,
            "CPAInsurance",
            require_login_on_open=False,
            launch_background=True,
        )
        if page is None:
            launched = launch_site_background_detached(resolved)
            if launched:
                out["success"] = True
                return out
            out["error"] = open_error or "Could not open CPA portal browser"
            return out
        out["success"] = True
    except PlaywrightTimeout as e:
        out["error"] = f"Timeout: {e!s}"
        logger.warning("add_alliance_cpa_insurance: warm_cpa_browser_session PlaywrightTimeout %s", e)
    except Exception as e:
        out["error"] = str(e)
        logger.warning("add_alliance_cpa_insurance: warm_cpa_browser_session %s", e)
    return out


def add_alliance_cpa_insurance(
    *,
    dealer_id: int,
    subfolder: str,
    portal_url: str | None,
    customer_name: str | None = None,
    mobile: str | None = None,
    frame_no: str | None = None,
    engine_no: str | None = None,
    full_chassis: str | None = None,
    full_engine: str | None = None,
    make: str | None = None,
    model: str | None = None,
    year_of_mfg: str | None = None,
    vehicle_type: str | None = None,
    client_type: str | None = None,
    gender: str | None = None,
    date_of_birth: str | None = None,
    address: str | None = None,
    state: str | None = None,
    city: str | None = None,
    plan_total_amount: str | None = None,
    nominee_name: str | None = None,
    nominee_relationship: str | None = None,
    nominee_gender: str | None = None,
    nominee_age: str | None = None,
) -> dict[str, Any]:
    """
    Open the CPA Alliance portal, fill the certificate form when on ``allianceassure.in``,
    click **Save** in production (``ENVIRONMENT`` prod/production), sync artifacts to S3.
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

    log_path = _resolve_cpa_playwright_log_path(dealer_id, safe_sub)
    if log_path.exists() and log_path.stat().st_size > 0:
        _append_cpa_log(log_path, "--- CPA Insurance re-invoke (appended to existing log) ---")
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

    wait_err = _wait_cpa_portal_ready(page, log_path)
    if wait_err:
        try:
            u = (page.url or "").strip()
        except Exception:
            u = ""
        _append_cpa_log(log_path, f"ERROR {wait_err}")
        return {"success": False, "error": wait_err, "page_url": u or None}

    _try_click_issue_new_certificate_alliance(page, log_path)
    _wait_alliance_form_after_issue_click(page, log_path)

    payload = _build_cpa_alliance_payload(
        customer_name=customer_name,
        mobile=mobile,
        frame_no=frame_no,
        engine_no=engine_no,
        full_chassis=full_chassis,
        full_engine=full_engine,
        make=make,
        model=model,
        year_of_mfg=year_of_mfg,
        vehicle_type=vehicle_type,
        client_type=client_type,
        gender=gender,
        date_of_birth=date_of_birth,
        address=address,
        state=state,
        city=city,
        plan_total_amount=plan_total_amount,
        nominee_name=nominee_name,
        nominee_relationship=nominee_relationship,
        nominee_gender=nominee_gender,
        nominee_age=nominee_age,
    )
    _append_cpa_log(
        log_path,
        "NOTE fill payload: "
        + ", ".join(
            f"{k}={'(set)' if v else '(empty)'}"
            for k, v in {
                "chassis": payload.chassis,
                "engine": payload.engine,
                "model": payload.model,
                "year_of_mfg": payload.year_of_mfg,
                "make": payload.make,
                "mobile": payload.mobile,
                "customer_name": payload.customer_name,
                "gender": payload.gender,
                "dob": payload.date_of_birth,
                "address": payload.address,
                "state": payload.state,
                "city": payload.city,
                "nominee_name": payload.nominee_name,
            }.items()
        ),
    )

    try:
        page_host = (page.url or "").lower()
    except Exception:
        page_host = ""
    missing_required: list[str] = []
    post_save: dict[str, Any] = {}
    if "allianceassure.in" in page_host:
        filled_count, missing_required = _try_fill_alliance_certificate_form(page, log_path, payload)
        save_clicked = False
        if filled_count > 0:
            save_clicked = _maybe_click_save_alliance(page, log_path)
        elif ENVIRONMENT_IS_PRODUCTION:
            _append_cpa_log(log_path, "NOTE skipping Save — no fields were filled.")
        if save_clicked or "printnew" in (page.url or "").lower():
            ocr_dir = Path(get_ocr_output_dir(int(dealer_id)))
            post_save = _alliance_post_save_certificate_flow(
                page,
                log_path,
                payload,
                dealer_id=int(dealer_id),
                safe_sub=safe_sub,
                ocr_dir=ocr_dir,
            )
    else:
        fill_pairs: list[tuple[str, str | None]] = [
            ("chasis", payload.chassis),
            ("chassis", payload.chassis),
            ("engine", payload.engine),
            ("mobile", payload.mobile),
            ("contact", payload.mobile),
            ("first name", payload.customer_name),
        ]
        seen_pair: set[tuple[str, str]] = set()
        deduped_pairs: list[tuple[str, str | None]] = []
        for hint, raw in fill_pairs:
            val = (raw or "").strip()
            key = (hint.lower(), val)
            if key in seen_pair:
                continue
            seen_pair.add(key)
            deduped_pairs.append((hint, raw))
        filled_count = _try_fill_labeled_fields(page, log_path, deduped_pairs)

    if filled_count == 0:
        _append_cpa_log(log_path, "ERROR zero fields filled on certificate form.")
    else:
        _append_cpa_log(log_path, f"NOTE filled {filled_count} field(s).")

    try:
        page_url = (page.url or "").strip()
    except Exception:
        page_url = ""

    if missing_required or filled_count == 0:
        if missing_required:
            err = (
                "CPA Alliance form incomplete — required fields not filled: "
                + ", ".join(missing_required)
                + ". Check playwright_cpa_*.txt in ocr_output."
            )
        elif not any(
            [payload.chassis, payload.engine, payload.mobile, payload.customer_name]
        ):
            err = (
                "CPA portal opened but no vehicle/customer data was available to fill. "
                "Complete OCR / Submit Info or run Create Invoice first."
            )
        else:
            err = (
                "CPA portal opened on the certificate form but no fields could be filled. "
                "Check playwright_cpa_*.txt in ocr_output for this sale."
            )
        _append_cpa_log(log_path, f"ERROR {err}")
        _append_cpa_log(log_path, "ERROR add_alliance_cpa_insurance finished with fill failure.")
        try:
            sync_uploads_subfolder_to_s3(int(dealer_id), safe_sub)
            sync_ocr_subfolder_to_s3(int(dealer_id), safe_sub)
        except Exception as exc:
            logger.warning("add_alliance_cpa_insurance: S3 sync: %s", exc)
        return {
            "success": False,
            "error": err,
            "page_url": page_url or None,
            "playwright_log": str(log_path),
        }

    _append_cpa_log(log_path, "NOTE add_alliance_cpa_insurance finished (browser left open for operator).")
    try:
        sync_uploads_subfolder_to_s3(int(dealer_id), safe_sub)
        sync_ocr_subfolder_to_s3(int(dealer_id), safe_sub)
    except Exception as exc:
        logger.warning("add_alliance_cpa_insurance: S3 sync: %s", exc)

    return {
        "success": True,
        "error": None,
        "page_url": page_url or None,
        "playwright_log": str(log_path),
        "certificate_number": post_save.get("certificate_number"),
        "cpa_download_ok": bool(post_save.get("download_ok")),
        "cpa_print_search_ok": bool(post_save.get("print_search_ok")),
    }
