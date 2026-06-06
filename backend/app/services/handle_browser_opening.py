"""
Shared Playwright browser lifecycle: CDP attach to existing Edge/Chrome, optional **native**
Playwright ``launch_persistent_context`` for **DMS, CPAInsurance, and Vahan** when
``USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS`` is true; **Insurance (MISP / Generate Insurance) always**
uses **managed Edge/Chrome + CDP** (same as v0.7.00 — stable ``.browser-profile`` and
``--remote-debugging-port`` / ``PLAYWRIGHT_CDP_URL``), not native persistent Chromium.

Used by Fill DMS, Vahan, Insurance, and CPA third-party portals — independent of Siebel/DMS business logic.

**Native Playwright (optional):** When ``USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS`` is true,
``site_label="DMS"``, ``site_label="CPAInsurance"``, and ``site_label="Vahan"`` use Playwright-bundled
Chromium with ``launch_persistent_context`` — DMS under ``browser-profile-playwright-chromium``,
CPA under ``browser-profile-playwright-chromium-cpa-insurance``, Vahan under
``browser-profile-playwright-chromium-vahan``. Set the env var to ``false`` to use CDP + managed
Edge/Chrome for those portals too. **Insurance is unaffected by this flag** (always CDP/managed).

Unused helpers for native Insurance (``launch_persistent_context`` + Edge channel) remain in this
module for easier revert; ``get_or_open_site_page`` does not call them.

The DMS native profile is **not** Edge's saved-password vault: Windows Hello / PIN prompts when picking
stored passwords in Edge do not apply; automation uses ``DMS_LOGIN_USER`` / ``DMS_LOGIN_PASSWORD``
or ``operator_dms_login.json`` (written after a successful programmatic or snapshotted login).
Siebel login: ``get_or_open_site_page`` may fill those creds (non-demo),
reuse the JSON cache under that profile after a successful manual login, poll for **browser autofill**
(Chrome saved password) up to ``DMS_BROWSER_AUTOFILL_LOGIN_MAX_MS`` (default 14s) and click **Login** when
both fields are populated, and wait up to ``DMS_LOGIN_MANUAL_WAIT_MS`` for the operator to finish typing before failing.

**Browser persistence policy (CDP / managed Edge):** Insurance and (when the flag is false) DMS/CPA/Vahan
use an independent OS process with a *stable* user-data-dir (``<project>/.browser-profile``) and
``--remote-debugging-port``. It survives backend restarts,
retries, and frontend reloads. On next startup the CDP reconnection logic in ``_refresh_cdp_browsers``
re-attaches to the same process (session cookies, Vahan login, captcha/OTP state all survive). When
native Chromium is on for DMS/CPA/Vahan, those portals use their persistent profile dirs above.
Normal flows do not call ``Browser.close()``; Electron quit sends a
``teardown_local_browsers`` sidecar job that disconnects CDP / closes native context and terminates
the managed debug-port process when applicable.

**No new tabs:** For single-session portals like Vahan, a second tab to the same host would
invalidate the running session.  ``get_or_open_site_page`` never opens an extra tab — it
reuses a matching tab, or navigates an existing tab in-place, or (only when no browser exists)
launches a fresh process with exactly one tab.
"""
from __future__ import annotations

import atexit
import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from app.config import (
    DMS_BROWSER_AUTOFILL_LOGIN_MAX_MS,
    DMS_LOGIN_MANUAL_WAIT_MS,
    DMS_PLAYWRIGHT_HEADED,
    PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
    USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS,
)
from app.services.dms_fill_timing import fill_dms_phase
from app.services.hero_dms_shared_utilities import _is_browser_disconnected_error, _ts_ist_iso
from app.services.playwright_executor import get_playwright_executor, run_playwright_callable_sync

logger = logging.getLogger(__name__)


def _dms_phase(name: str, site_label: str = "", **fields: object) -> None:
    if (site_label or "").strip() == "DMS":
        fill_dms_phase(name, **fields)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ``page`` id → (context, on_dialog, page, on_popup) for Siebel login capture (removed after login gate).
# Dialogs use **context** so ``alert``/``confirm`` on ``window.open`` login helpers are not missed.
_DMS_LOGIN_CAPTURE_HANDLERS: dict[int, tuple[object, object, object, object]] = {}


def _append_playwright_dms_capture_line(
    log_path: Path | str | None, prefix: str, msg: str, *, also_logger: bool = False
) -> None:
    """Append one IST line to ``Playwright_DMS_*.txt`` under ``ocr_output`` (same format as main DMS trace)."""
    if not log_path or not (msg or "").strip():
        return
    p = Path(str(log_path))
    line = f"{_ts_ist_iso()} [{prefix}] {(msg or '').replace(chr(10), ' ').replace(chr(13), ' ')[:14000]}\n"
    if also_logger:
        logger.warning("%s", line.strip())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fp:
            fp.write(line)
            fp.flush()
    except OSError as exc:
        logger.debug("handle_browser_opening: Playwright DMS log append failed: %s", exc)


def _install_dms_login_window_capture(page, log_path: Path) -> None:
    """
    During Siebel **login**: log ``alert``/``confirm``/``prompt`` (Playwright ``dialog`` on **any** page
    in the context — including short-lived ``window.open`` shells), new **popup** pages from this tab
    (URL, body text excerpt, PNG), for review in ``Playwright_DMS_*.txt`` next to other OCR artifacts.
    """
    pid = id(page)
    if pid in _DMS_LOGIN_CAPTURE_HANDLERS:
        return
    log_path = Path(log_path)

    def on_dialog(dialog) -> None:
        try:
            pg = getattr(dialog, "page", None)
            purl = ""
            try:
                if pg is not None:
                    purl = (pg.url or "")[:240]
            except Exception:
                pass
            _append_playwright_dms_capture_line(
                log_path,
                "LOGIN_JS_DIALOG",
                f"type={dialog.type} page_url={purl!r} message={dialog.message}",
                also_logger=True,
            )
        except Exception:
            pass
        try:
            dialog.accept()
        except Exception as exc:
            logger.debug("handle_browser_opening: login dialog accept skipped: %s", exc)

    def on_popup(popup) -> None:
        try:
            _append_playwright_dms_capture_line(
                log_path,
                "LOGIN_POPUP_OPEN",
                f"url={(popup.url or '')[:900]}",
            )
        except Exception:
            pass
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception as exc:
            _append_playwright_dms_capture_line(log_path, "LOGIN_POPUP_LOAD", f"(timeout/early) {exc!s}")
        try:
            body = popup.evaluate(
                """() => {
                  try {
                    const b = document.body;
                    return (b && (b.innerText || b.textContent) || '').replace(/\\s+/g, ' ').trim().slice(0, 10000);
                  } catch (e) { return ''; }
                }"""
            )
            if isinstance(body, str) and body.strip():
                _append_playwright_dms_capture_line(log_path, "LOGIN_POPUP_BODY", body)
        except Exception as exc:
            _append_playwright_dms_capture_line(log_path, "LOGIN_POPUP_BODY_ERR", str(exc))
        try:
            shot = log_path.parent / f"Playwright_DMS_login_capture_{int(time.time() * 1000) % 10_000_000}.png"
            popup.screenshot(path=str(shot), timeout=8000)
            _append_playwright_dms_capture_line(log_path, "LOGIN_POPUP_SCREENSHOT", str(shot))
        except Exception as exc:
            _append_playwright_dms_capture_line(log_path, "LOGIN_POPUP_SCREENSHOT_ERR", str(exc))

    try:
        ctx = page.context
        ctx.on("dialog", on_dialog)
        page.on("popup", on_popup)
        _DMS_LOGIN_CAPTURE_HANDLERS[pid] = (ctx, on_dialog, page, on_popup)
        _append_playwright_dms_capture_line(
            log_path,
            "LOGIN_CAPTURE",
            "Installed BrowserContext dialog + main page popup listeners for Siebel login phase.",
        )
    except Exception as exc:
        logger.warning("handle_browser_opening: could not install login capture listeners: %s", exc)


def _remove_dms_login_window_capture(page) -> None:
    tup = _DMS_LOGIN_CAPTURE_HANDLERS.pop(id(page), None)
    if not tup:
        return
    ctx, on_dialog, pg, on_popup = tup
    try:
        ctx.remove_listener("dialog", on_dialog)
    except Exception:
        pass
    try:
        pg.remove_listener("popup", on_popup)
    except Exception:
        pass


def _capture_login_main_page_snapshot(page, log_path: Path | str | None, tag: str) -> None:
    """Log ``#statusBar`` text after login submit (flash errors). Full-page screenshots removed."""
    if not log_path:
        return
    lp = Path(str(log_path))
    bar = _read_siebel_login_status_bar_any_frame(page)
    if bar:
        _append_playwright_dms_capture_line(lp, f"LOGIN_STATUSBAR_{tag}", bar, also_logger=True)

# Poll Siebel login for Chrome / password-manager autofill before env/json fill (clamped 3s–10m).
_DMS_BROWSER_AUTOFILL_LOGIN_MAX_SEC = max(
    3.0, min(600.0, float(int(DMS_BROWSER_AUTOFILL_LOGIN_MAX_MS)) / 1000.0)
)
_DMS_BROWSER_AUTOFILL_POLL_MS = 400

# Hero Connect / Open UI: Login is ``<a id="s_swepi_22" onclick="SWEExecuteLogin(document.SWEEntryForm,...)">``
# — not a native submit control; ``form.requestSubmit()`` does not run that handler.
_SIEBEL_HERO_SWEENTRY_LOGIN_SUBMIT_JS = """() => {
      const frm = document.forms["SWEEntryForm"] || document.forms.SWEEntryForm || document.SWEEntryForm;
      if (!frm) return false;
      const u = frm.querySelector('input[name="SWEUserName"]');
      const p = frm.querySelector('input[name="SWEPassword"], input[type="password"]');
      if (!u || !p) return false;
      if ((u.value || "").trim().length < 2) return false;
      let passOk = ((p.value || "").trim().length > 0);
      if (!passOk) {
        try { passOk = !!p.matches(":autofill"); } catch (e) {}
      }
      if (!passOk) return false;
      const anchor = document.getElementById("s_swepi_22");
      let path = "";
      try {
        const raw = (frm.getAttribute("action") || "").trim() || (frm.action || "").trim();
        if (raw) {
          if (raw.startsWith("http://") || raw.startsWith("https://")) {
            const url = new URL(raw);
            path = url.pathname + (url.search || "");
          } else {
            path = raw;
          }
        } else {
          path = window.location.pathname + (window.location.search || "");
        }
      } catch (e) {
        path = "/siebel/app/edealerHMCL/enu/";
      }
      if (typeof SWEExecuteLogin === "function") {
        try {
          SWEExecuteLogin(frm, path, "");
          return true;
        } catch (e) {}
      }
      if (anchor) {
        try {
          anchor.click();
          return true;
        } catch (e) {}
      }
      return false;
    }"""

_SIEBEL_PREFILL_DETECT_JS = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const userSels = ['input[name="SWEUserName"]', 'input[type="text"][name*="user" i]',
                        'input[type="text"][name*="login" i]', 'input[type="email"]',
                        'input[name="username"]', 'input[id*="userName" i]', 'input[id*="username" i]',
                        'input[placeholder*="User Name" i]', 'input[placeholder*="User name" i]',
                        'input[aria-label*="User Name" i]', 'input[aria-label*="User name" i]',
                        'input[type="text"]:not([name=""])'];
      const passSels = ['input[name="SWEPassword"]', 'input[type="password"]',
                        'input[name="password"]', 'input[placeholder*="Password" i]'];
      let userEl = null, passEl = null, userFound = false, passFound = false;
      for (const s of userSels) {
        const el = document.querySelector(s);
        if (el && vis(el)) { userFound = true; if ((el.value || '').trim().length > 0) { userEl = el; break; } }
      }
      for (const s of passSels) {
        const el = document.querySelector(s);
        if (el && vis(el)) {
          passFound = true;
          if (!passEl) passEl = el;
          if ((el.value || '').trim().length > 0) { passEl = el; break; }
        }
      }
      let passOk = false;
      if (passEl) {
        if ((passEl.value || '').trim().length > 0) passOk = true;
        else { try { passOk = !!passEl.matches(':autofill'); } catch (e) {} }
      }
      if (!userFound && !passFound) return {status: 'no_form'};
      if (!userEl || !passFound || !passEl) return {status: 'not_prefilled', userFound, passFound,
        userValue: userEl ? userEl.value.trim().substring(0,40) : '',
        passHasValue: passOk};
      if (!passOk) return {status: 'not_prefilled', userFound, passFound,
        userValue: userEl ? userEl.value.trim().substring(0,40) : '', passHasValue: false};
      return {status: 'prefilled', user: userEl.value.trim().substring(0, 40), hasPass: true};
    }"""

_SIEBEL_TRY_LOGIN_SUBMIT_FALLBACK_JS = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const u = document.querySelector('input[name="SWEUserName"]');
      const p = document.querySelector('input[name="SWEPassword"], input[type="password"]');
      if (!u || !vis(u) || (u.value || '').trim().length < 2) return false;
      if (!p || !vis(p)) return false;
      if ((p.value || '').trim().length > 0) return true;
      try { return !!p.matches(':autofill'); } catch (e) { return false; }
    }"""

# ``SWECM=S`` login screen: Chrome may mask password from JS — still try **Login** if user field is set.
_SIEBEL_TRY_LOGIN_SUBMIT_AGGRESSIVE_JS = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const u = document.querySelector('input[name="SWEUserName"]');
      const p = document.querySelector('input[name="SWEPassword"], input[type="password"]');
      if (!u || !vis(u) || (u.value || '').trim().length < 2) return false;
      return !!(p && vis(p));
    }"""

_SIEBEL_THIRD_LEVEL_BAR_WIDE_JS = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const bar = document.getElementById('s_vctrl_div');
      if (!bar || !vis(bar)) return false;
      const r = bar.getBoundingClientRect();
      return r.width >= 100 && r.height >= 12;
    }"""

_SIEBEL_LOGIN_AUTOFILL_NUDGE_JS = """() => {
      const u = document.querySelector('input[name="SWEUserName"]');
      const p = document.querySelector('input[name="SWEPassword"], input[type="password"]');
      try { if (u) { u.focus(); u.click(); } } catch (e) {}
      try { if (p) { p.focus(); } } catch (e) {}
      return !!(u || p);
    }"""


def _path_is_under_onedrive(p: Path) -> bool:
    """Chromium ``--user-data-dir`` under OneDrive silently corrupts (SingletonLock / SQLite locks)."""
    try:
        s = str(p).lower()
    except Exception:
        return False
    return ("\\onedrive\\" in s) or ("/onedrive/" in s) or ("\\onedrive - " in s)


def _browser_profile_dir() -> Path:
    """Stable browser profile so session cookies / saved passwords survive backend restarts.

    Resolution order:
    1. ``SAATHI_BROWSER_PROFILE_DIR`` env override.
    2. ``SAATHI_BASE_DIR/.browser-profile`` (Electron sidecar / installer — e.g. ``D:\\Saathi``).
    3. Windows: ``%LOCALAPPDATA%\\Saathi\\browser-profile`` (outside OneDrive, persistent).
    4. POSIX: ``~/.local/share/saathi/browser-profile``.
    5. Final fallback: ``<tempdir>/saathi-browser-profile`` (matches pre-AWS behavior).

    The repo-root path used previously broke when the repo lived in OneDrive
    (Edge fails to take ``SingletonLock`` and CDP never comes up).
    """
    override = os.environ.get("SAATHI_BROWSER_PROFILE_DIR", "").strip()
    if override:
        return Path(override)

    saathi = os.environ.get("SAATHI_BASE_DIR", "").strip()
    if saathi:
        candidate = Path(saathi) / ".browser-profile"
        if not _path_is_under_onedrive(candidate):
            return candidate
        logger.warning(
            "handle_browser_opening: SAATHI_BASE_DIR %s is under OneDrive — falling back to LOCALAPPDATA",
            saathi,
        )

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            candidate = Path(local_app_data) / "Saathi" / "browser-profile"
            if not _path_is_under_onedrive(candidate):
                return candidate
    else:
        home = Path(os.path.expanduser("~"))
        candidate = home / ".local" / "share" / "saathi" / "browser-profile"
        if not _path_is_under_onedrive(candidate):
            return candidate

    return Path(tempfile.gettempdir()) / "saathi-browser-profile"


def _hostname_for_site_match(url_or_base: str) -> str:
    """Lowercase hostname without leading www., for comparing INSURANCE_BASE_URL to live tabs."""
    try:
        raw = (url_or_base or "").strip()
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw.lstrip("/")
        p = urllib.parse.urlparse(raw)
        h = (p.hostname or "").lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""


def _cpa_alliance_family_registrable(host: str) -> str | None:
    """If ``host`` is any Alliance Assure host, return one shared key for CPA tab matching."""
    h = (host or "").strip().lower()
    if not h:
        return None
    if h == "allianceassure.in" or h.endswith(".allianceassure.in"):
        return "allianceassure.in"
    return None


def _cpa_alliance_hosts_equivalent(host_a: str, host_b: str) -> bool:
    """``app.`` vs ``partner.`` (etc.) share one SSO cookie jar for Alliance Assure."""
    fa = _cpa_alliance_family_registrable(host_a)
    fb = _cpa_alliance_family_registrable(host_b)
    return fa is not None and fa == fb


def _cpa_alliance_skip_goto_logged_in_surface(current_url: str) -> bool:
    """Non-login Alliance URL in CPA profile — avoid ``goto`` that would reload a warm session."""
    u = (current_url or "").strip().lower()
    if "allianceassure.in" not in u:
        return False
    if "/login" in u or u.rstrip("/").endswith("/login"):
        return False
    if "blank" in u or u.startswith("chrome://") or u.startswith("about:") or u.startswith("edge://"):
        return False
    return True


_PW = None
_PW_THREAD_ID: int | None = None
_KEEP_OPEN_BROWSERS: list = []
_CDP_BROWSERS_BY_URL: dict[str, object] = {}
_RETAINED_BROWSERS_NO_CLOSE: list = []
_CDP_REFRESH_LOCK = threading.Lock()
_LAST_CDP_REFRESH_MONO: float = 0.0
_CDP_REFRESH_TTL_SEC = 2.0
_CDP_PROBE_TIMEOUT_SEC = 0.35

# Playwright-bundled Chromium persistent contexts (no CDP attach to Edge/Chrome).
_DMS_NATIVE_PERSISTENT_CONTEXT: object | None = None
_INSURANCE_NATIVE_PERSISTENT_CONTEXT: object | None = None
_CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT: object | None = None
_VAHAN_NATIVE_PERSISTENT_CONTEXT: object | None = None


def _playwright_chromium_profile_dir() -> Path:
    """Sibling profile dir for Playwright Chromium (do not share user-data-dir with Edge/Chrome)."""
    return _browser_profile_dir().resolve().parent / "browser-profile-playwright-chromium"


def _playwright_chromium_insurance_profile_dir() -> Path:
    """Separate user-data-dir for MISP (Generate Insurance) — Edge channel, not DMS/Siebel.

    Uses ``…-msedge-insurance`` so a profile that was ever opened with **bundled Chromium** is not
    reused by **Microsoft Edge** (mixed engines in one dir can break login / cookies).
    """
    return (
        _browser_profile_dir().resolve().parent / "browser-profile-playwright-msedge-insurance"
    )


def _playwright_chromium_cpa_insurance_profile_dir() -> Path:
    """Separate user-data-dir for CPA third-party portals (e.g. Alliance Assure) vs MISP/DMS."""
    return _browser_profile_dir().resolve().parent / "browser-profile-playwright-chromium-cpa-insurance"


def _playwright_chromium_vahan_profile_dir() -> Path:
    """Separate user-data-dir for Vahan (parivahan) so dealer session/cookies do not mix with DMS/Insurance."""
    return _browser_profile_dir().resolve().parent / "browser-profile-playwright-chromium-vahan"


_OPERATOR_DMS_LOGIN_JSON = "operator_dms_login.json"


def _dms_operator_login_store_path() -> Path:
    return _playwright_chromium_profile_dir() / _OPERATOR_DMS_LOGIN_JSON


def _load_stored_dms_operator_login() -> tuple[str, str] | None:
    p = _dms_operator_login_store_path()
    try:
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        u = (data.get("user") or "").strip()
        pw = data.get("password") if isinstance(data.get("password"), str) else ""
        if len(u) >= 2 and len(pw) >= 1:
            return u, pw
    except Exception:
        return None
    return None


def _save_stored_dms_operator_login(user: str, password: str) -> None:
    u = (user or "").strip()
    p = password if isinstance(password, str) else ""
    if len(u) < 2 or len(p) < 1:
        return
    try:
        path = _dms_operator_login_store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"user": u, "password": p}), encoding="utf-8")
        tmp.replace(path)
        logger.info("handle_browser_opening: saved DMS operator login cache to %s", path)
    except Exception as exc:
        logger.debug("handle_browser_opening: could not save DMS login cache: %s", exc)


def _effective_dms_login_creds(login_user: str | None, login_password: str | None) -> tuple[str | None, str | None]:
    """Non-demo credentials from caller kwargs, else Siebel operator JSON next to the Chromium profile."""
    u = (login_user or "").strip()
    p = (login_password or "").strip()
    if u and p and not (u.lower() == "demo" and p == "demo"):
        return u, p
    got = _load_stored_dms_operator_login()
    if got:
        lu, lp = got
        if len((lu or "").strip()) >= 2 and len(lp or "") >= 1:
            return lu.strip(), lp
    return None, None


def _maybe_snapshot_stored_credentials_from_login_page(page) -> None:
    """While the operator types on Siebel login, persist u/p for the next headless-ish automation open."""
    _snap_js = """() => {
      const u = document.querySelector('input[name="SWEUserName"]');
      const p = document.querySelector('input[name="SWEPassword"], input[type="password"]');
      if (!u || !p) return null;
      const user = (u.value || '').trim();
      const pass = (p.value || '');
      if (user.length < 2 || pass.length < 3) return null;
      if (user.toLowerCase() === 'demo' && pass === 'demo') return null;
      return {user, pass};
    }"""
    try:
        data = page.evaluate(_snap_js)
        if data and data.get("user"):
            _save_stored_dms_operator_login(str(data["user"]), str(data.get("pass") or ""))
    except Exception:
        pass


def _ordered_unique_playwright_frames(page):
    """Main frame first, then child frames (deduped) — Siebel login fields often live in an iframe."""
    out = []
    seen: set[int] = set()
    for fr in _iter_page_frames(page):
        k = id(fr)
        if k in seen:
            continue
        seen.add(k)
        out.append(fr)
    return out


def _read_siebel_login_status_bar_any_frame(page) -> str:
    """Trimmed ``#statusBar`` text from any frame (Hero Siebel login surfaces validation errors there briefly)."""
    for fr in _ordered_unique_playwright_frames(page):
        try:
            t = fr.evaluate(
                """() => {
                  const bar = document.getElementById("statusBar");
                  if (!bar) return "";
                  const txt = (bar.innerText || bar.textContent || "").replace(/\\s+/g, " ").trim();
                  return txt.slice(0, 800);
                }"""
            )
            if isinstance(t, str) and t.strip():
                return t.strip()
        except Exception:
            continue
    return ""


def _dms_viewport_click_steal_from_omnibox(page) -> None:
    """One real mouse hit in the page viewport (Siebel form / body center) — more reliable than repeated Escape."""
    _pt_js = """() => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 2 && r.height > 2;
        };
        const el = document.querySelector('#formContent')
            || document.querySelector('input[name="SWEUserName"]')
            || document.body;
        if (!el || !vis(el)) return null;
        const r = el.getBoundingClientRect();
        return { x: r.left + r.width * 0.5, y: r.top + Math.min(r.height * 0.4, 120) };
    }"""
    for fr in _ordered_unique_playwright_frames(page):
        try:
            pt = fr.evaluate(_pt_js)
            if isinstance(pt, dict):
                x = pt.get("x")
                y = pt.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    page.mouse.click(float(x), float(y))
                    try:
                        page.wait_for_timeout(60)
                    except Exception:
                        time.sleep(0.06)
                    return
        except Exception:
            continue


def _try_playwright_siebel_login_anchor_click(page, loc) -> bool:
    """Siebel Login is an ``<a>`` with ``javascript:`` href — try several Playwright strategies (Chromium + omnibox)."""
    try:
        loc.scroll_into_view_if_needed(timeout=2500)
    except Exception:
        pass
    try:
        loc.click(timeout=5000)
        return True
    except Exception:
        pass
    try:
        loc.click(timeout=5000, force=True)
        return True
    except Exception:
        pass
    try:
        box = loc.bounding_box()
        if box and box.get("width", 0) > 1 and box.get("height", 0) > 1:
            cx = float(box["x"] + box["width"] * 0.5)
            cy = float(box["y"] + box["height"] * 0.5)
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.up()
            return True
    except Exception:
        pass
    try:
        loc.focus(timeout=3000)
        page.keyboard.press("Enter")
        return True
    except Exception:
        pass
    try:
        loc.evaluate(
            """(el) => {
                try {
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true }));
                } catch (e) {}
                try { el.click(); } catch (e2) {}
            }"""
        )
        return True
    except Exception:
        pass
    return False


def _try_siebel_login_submit_via_tab_direct(page) -> bool:
    """Tab from current focus to Login and press Enter.

    Siebel login tabindex: User ID (1) -> Password (2) -> Remember (3) -> Login (4).
    After focus steal, we may be on Password (2 tabs to Login) or User ID (3 tabs to Login).
    Try 2 tabs first (common case: focus on Password after JS body click).
    """
    logger.info("handle_browser_opening: _try_siebel_login_submit_via_tab_direct STARTING")
    try:
        # Focus is typically on Password field after JS body/form click, so 2 Tabs reaches Login
        for i in range(2):
            page.keyboard.press("Tab")
            logger.info("handle_browser_opening: Tab press %d sent", i + 1)
            try:
                page.wait_for_timeout(100)
            except Exception:
                time.sleep(0.1)
        logger.info("handle_browser_opening: Sending Enter after 2 Tabs")
        page.keyboard.press("Enter")
        logger.info("handle_browser_opening: Enter sent, returning True")
        return True
    except Exception as exc:
        logger.warning("handle_browser_opening: Tab direct FAILED: %s", exc)
        return False


def _try_siebel_login_submit_via_tab_from_user_id(page) -> bool:
    """Activate Hero Siebel Login using tabindex: User ID (1) -> Password -> Remember -> Login (4).

    Operators report that with focus in the User ID field, **three Tab** then **Enter** reaches
    ``#s_swepi_22`` when mouse-driven clicks are ignored (native Chromium + omnibox quirks).
    """
    _user_sels = ('input[name="SWEUserName"]', "#s_swepi_1", "input#s_swepi_1")
    for fr in _ordered_unique_playwright_frames(page):
        for usel in _user_sels:
            loc = fr.locator(usel).first
            try:
                if loc.count() == 0:
                    continue
                try:
                    loc.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    loc.focus(timeout=3000)
                except Exception:
                    try:
                        loc.click(timeout=3000, force=True)
                    except Exception:
                        continue
            except Exception:
                continue
            try:
                for _ in range(3):
                    page.keyboard.press("Tab")
                    try:
                        page.wait_for_timeout(90)
                    except Exception:
                        time.sleep(0.09)
                page.keyboard.press("Enter")
                return True
            except Exception:
                continue
    return False


def _click_siebel_login_submit(page) -> bool:
    """Click Siebel / generic Login after username+password are already set in the DOM."""
    _sels = (
        'form[name="SWEEntryForm"] a#s_swepi_22',
        'form[name="SWEEntryForm"] #s_swepi_22',
        'a#s_swepi_22[role="button"]',
        '.siebui-login-btn a#s_swepi_22',
        '#s_swepi_22',
        'input[id="s_swepi_22"]',
        'button[id="s_swepi_22"]',
        'input[type="submit"][value*="Login" i]',
        'button[type="submit"]',
        'input[type="submit"]',
        'input[name="s_swepi_22"]',
        'a[href*="Login" i]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'input[type="button"][value*="Login" i]',
    )
    _legacy_submit_js = """() => {
        const vis = (el) => {
          if (!el) return false;
          const st = window.getComputedStyle(el);
          if (st.display === 'none' || st.visibility === 'hidden') return false;
          const r = el.getBoundingClientRect();
          return r.width > 2 && r.height > 2;
        };
        const pwd = document.querySelector('input[name="SWEPassword"], input[type="password"]');
        const frm = (pwd && pwd.form) ? pwd.form : document.querySelector('form');
        if (frm) { try { frm.requestSubmit ? frm.requestSubmit() : frm.submit(); return true; } catch (e) {} }
        const btn = document.querySelector('input[type="submit"], button[type="submit"], input[name="s_swepi_22"]');
        if (btn && vis(btn)) { try { btn.click(); return true; } catch (e) {} }
        return false;
    }"""
    # v0.7.00 (CDP Edge): Playwright ``locator.click`` on #s_swepi_22 *before* any in-page JS submit. On native
    # Chromium, running ``SWEExecuteLogin`` / ``anchor.click()`` from ``evaluate`` first can report success while
    # the UI stays on the login surface (e.g. omnibox focus / trusted-click semantics).
    logger.info("handle_browser_opening: _click_siebel_login_submit STARTING")
    _dms_steal_focus_from_omnibox_for_login(page)
    # After focus steal, we should be in User ID field. Tab 3x + Enter reaches Login (tabindex 4).
    logger.info("handle_browser_opening: Trying Tab direct approach")
    if _try_siebel_login_submit_via_tab_direct(page):
        logger.info("handle_browser_opening: Tab direct returned True")
        return True
    logger.info("handle_browser_opening: Tab direct returned False, trying Tab from User ID")
    # Fallback: explicitly focus User ID then Tab.
    if _try_siebel_login_submit_via_tab_from_user_id(page):
        logger.info("handle_browser_opening: Tab from User ID returned True")
        return True
    logger.info("handle_browser_opening: Tab from User ID returned False, trying locator clicks")
    for fr in _ordered_unique_playwright_frames(page):
        for sel in _sels:
            try:
                loc = fr.locator(sel).first
                if loc.count() == 0:
                    continue
                try:
                    loc.wait_for(state="attached", timeout=800)
                except Exception:
                    continue
                # Do not require ``is_visible`` — Chromium sometimes keeps the anchor "obscured" while the
                # omnibox is active; ``force`` / viewport mouse still need to run.
                if _try_playwright_siebel_login_anchor_click(page, loc):
                    return True
            except Exception:
                continue
    for fr in _ordered_unique_playwright_frames(page):
        try:
            if bool(fr.evaluate(_SIEBEL_HERO_SWEENTRY_LOGIN_SUBMIT_JS)):
                return True
        except Exception:
            pass
    try:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        _nudge_siebel_login_fields_for_autofill(page)
        page.keyboard.press("Enter")
        return True
    except Exception:
        pass
    for fr in _ordered_unique_playwright_frames(page):
        try:
            if bool(fr.evaluate(_legacy_submit_js)):
                return True
        except Exception:
            continue
    return False


def _siebel_still_on_pre_auth_login_surface(page) -> bool:
    """True while we should keep waiting — MISP / generic / Siebel ``SWECM=S`` form (not eDealer ``SWEPL=1`` portal)."""
    try:
        u = (page.url or "").lower()
    except Exception:
        return True
    if "misp-partner-login" in u:
        return True
    if u.rstrip("/").endswith("/login") and "siebel" not in u:
        return True
    if "swecmd=login" not in u:
        return False
    if "swepl=1" in u and "swecm=s" not in u:
        return False
    return True


def _wait_after_siebel_login_submit(page, log_path: Path | str | None = None) -> bool:
    """Return True once Siebel leaves the **pre-auth** login surface (``SWECM=S``), including portal ``SWEPL=1`` URLs."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    _logged_login_status: set[str] = set()
    # Phase 1: short interval so brief ``#statusBar`` validation lines (and similar) are not missed between polls.
    for i in range(50):
        _bar = _read_siebel_login_status_bar_any_frame(page)
        if _bar and _bar not in _logged_login_status:
            _logged_login_status.add(_bar)
            logger.warning(
                "handle_browser_opening: Siebel login #statusBar while waiting for redirect (dense pass %s): %s",
                i,
                _bar,
            )
            _append_playwright_dms_capture_line(
                log_path,
                "LOGIN_STATUSBAR_FLASH",
                f"pass={i} text={_bar}",
                also_logger=True,
            )
        if not _siebel_still_on_pre_auth_login_surface(page):
            logger.info("handle_browser_opening: login submit settled; page URL now: %s", (page.url or "")[:120])
            return True
        try:
            page.wait_for_timeout(80)
        except Exception:
            time.sleep(0.08)
    # Phase 2: legacy coarser tail (~6s) for slow redirects.
    for j in range(24):
        _bar = _read_siebel_login_status_bar_any_frame(page)
        if _bar and _bar not in _logged_login_status:
            _logged_login_status.add(_bar)
            logger.warning(
                "handle_browser_opening: Siebel login #statusBar while waiting for redirect (pass %s): %s",
                j + 50,
                _bar,
            )
            _append_playwright_dms_capture_line(
                log_path,
                "LOGIN_STATUSBAR_WAIT",
                f"pass={j + 50} text={_bar}",
            )
        if not _siebel_still_on_pre_auth_login_surface(page):
            logger.info("handle_browser_opening: login submit settled; page URL now: %s", (page.url or "")[:120])
            return True
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    logger.warning(
        "handle_browser_opening: login submit still on pre-auth login surface after wait: %s",
        (page.url or "")[:120],
    )
    return False


def _wait_after_siebel_login_submit_with_optional_second_click(
    page, log_path: Path | str | None = None
) -> bool:
    """
    After **Login**, Siebel sometimes shows a very short ``alert`` / ``#statusBar`` message and only
    succeeds on a **second** Login — mirror that with one automated retry when still on the login surface.
    """
    ok = _wait_after_siebel_login_submit(page, log_path=log_path)
    if ok:
        return True
    if not _siebel_still_on_pre_auth_login_surface(page):
        return False
    if not _login_form_visible_any_frame(page):
        return False
    _append_playwright_dms_capture_line(
        log_path,
        "LOGIN_SUBMIT_RETRY",
        "Still on login surface after first submit — clicking Login once more (Siebel transient dialog pattern).",
        also_logger=True,
    )
    if not _click_siebel_login_submit(page):
        return False
    _capture_login_main_page_snapshot(page, log_path, "after_retry_submit")
    return _wait_after_siebel_login_submit(page, log_path=log_path)


def _try_fill_siebel_login_and_submit(
    page, user: str, password: str, log_path: Path | str | None = None
) -> bool:
    """Fill Siebel SWEUserName/SWEPassword (or close equivalents) and submit — used for env / cached creds."""
    u = (user or "").strip()
    p = password if isinstance(password, str) else ""
    if len(u) < 2 or len(p) < 1:
        return False
    _dms_steal_focus_from_omnibox_for_login(page)
    _form_deadline = time.monotonic() + 12.0
    while time.monotonic() < _form_deadline:
        if _login_form_visible_any_frame(page):
            break
        try:
            page.wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    _user_sels = (
        'input[name="SWEUserName"]',
        'input[type="text"][name*="user" i]',
        'input[type="text"][name*="login" i]',
        'input[type="email"]',
        'input[name="username"]',
    )
    _pass_sels = ('input[name="SWEPassword"]', 'input[type="password"]', 'input[name="password"]')
    user_filled = False
    user_fr = None
    for fr in _ordered_unique_playwright_frames(page):
        for sel in _user_sels:
            try:
                loc = fr.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1200):
                    loc.click(timeout=2000)
                    loc.fill(u, timeout=5000)
                    user_filled = True
                    user_fr = fr
                    break
            except Exception:
                continue
        if user_filled:
            break
    if not user_filled:
        return False
    pass_filled = False
    _uid = id(user_fr) if user_fr is not None else None
    _pass_frames = []
    if user_fr is not None:
        _pass_frames.append(user_fr)
    for fr in _ordered_unique_playwright_frames(page):
        if _uid is not None and id(fr) == _uid:
            continue
        _pass_frames.append(fr)
    for fr in _pass_frames:
        for sel in _pass_sels:
            try:
                loc = fr.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1200):
                    loc.click(timeout=2000)
                    loc.fill(p, timeout=5000)
                    pass_filled = True
                    break
            except Exception:
                continue
        if pass_filled:
            break
    if not pass_filled:
        return False
    if not _click_siebel_login_submit(page):
        return False
    _capture_login_main_page_snapshot(page, log_path, "after_env_fill_submit")
    ok = _wait_after_siebel_login_submit_with_optional_second_click(page, log_path=log_path)
    if ok:
        _save_stored_dms_operator_login(u, p)
    return ok


def _use_native_pw_chromium_for_site(site_label: str) -> bool:
    """True when native Playwright Chromium is enabled for this portal (DMS, CPAInsurance, Vahan — not Insurance)."""
    if not bool(USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS):
        return False
    sl = (site_label or "").strip()
    return sl in ("DMS", "CPAInsurance", "Vahan")


def _host_matched_portal_skips_dms_siebel(site_label: str) -> bool:
    """MISP and CPA insurer portals match by host; never reuse or navigate a Siebel/DMS tab."""
    return (site_label or "").strip() in ("Insurance", "CPAInsurance")


def _clear_native_dms_persistent_context(reason: str = "") -> None:
    """Forget the native DMS Chromium context so the next open launches or attaches cleanly."""
    global _DMS_NATIVE_PERSISTENT_CONTEXT
    if _DMS_NATIVE_PERSISTENT_CONTEXT is None:
        return
    _DMS_NATIVE_PERSISTENT_CONTEXT = None
    if reason:
        logger.warning("handle_browser_opening: cleared native DMS persistent context (%s).", reason)


def _clear_native_insurance_persistent_context(reason: str = "") -> None:
    global _INSURANCE_NATIVE_PERSISTENT_CONTEXT
    if _INSURANCE_NATIVE_PERSISTENT_CONTEXT is None:
        return
    _INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
    if reason:
        logger.warning(
            "handle_browser_opening: cleared native Insurance persistent context (%s).", reason
        )


def _clear_native_cpa_insurance_persistent_context(reason: str = "") -> None:
    global _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
    if _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT is None:
        return
    _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
    if reason:
        logger.warning(
            "handle_browser_opening: cleared native CPAInsurance persistent context (%s).", reason
        )


def _clear_native_vahan_persistent_context(reason: str = "") -> None:
    global _VAHAN_NATIVE_PERSISTENT_CONTEXT
    if _VAHAN_NATIVE_PERSISTENT_CONTEXT is None:
        return
    _VAHAN_NATIVE_PERSISTENT_CONTEXT = None
    if reason:
        logger.warning(
            "handle_browser_opening: cleared native Vahan persistent context (%s).", reason
        )


def _clear_native_persistent_context_for_site(site_label: str, reason: str = "") -> None:
    sl = (site_label or "").strip()
    if sl == "DMS":
        _clear_native_dms_persistent_context(reason)
    elif sl == "Insurance":
        _clear_native_insurance_persistent_context(reason)
    elif sl == "CPAInsurance":
        _clear_native_cpa_insurance_persistent_context(reason)
    elif sl == "Vahan":
        _clear_native_vahan_persistent_context(reason)


def _refresh_native_dms_context_liveness() -> None:
    """If the persistent Chromium process is gone, drop the stale Playwright context reference."""
    global _DMS_NATIVE_PERSISTENT_CONTEXT
    ctx = _DMS_NATIVE_PERSISTENT_CONTEXT
    if ctx is None:
        return
    try:
        list(ctx.pages)
    except Exception as exc:
        logger.warning(
            "handle_browser_opening: native DMS context no longer valid (%s) — clearing reference.",
            exc,
        )
        _DMS_NATIVE_PERSISTENT_CONTEXT = None
        fill_dms_phase("native_context_cleared", reason=str(exc)[:80])


def _refresh_native_insurance_context_liveness() -> None:
    global _INSURANCE_NATIVE_PERSISTENT_CONTEXT
    ctx = _INSURANCE_NATIVE_PERSISTENT_CONTEXT
    if ctx is None:
        return
    try:
        list(ctx.pages)
    except Exception as exc:
        logger.warning(
            "handle_browser_opening: native Insurance context no longer valid (%s) — clearing reference.",
            exc,
        )
        _INSURANCE_NATIVE_PERSISTENT_CONTEXT = None


def _refresh_native_cpa_insurance_context_liveness() -> None:
    global _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
    ctx = _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
    if ctx is None:
        return
    try:
        list(ctx.pages)
    except Exception as exc:
        logger.warning(
            "handle_browser_opening: native CPAInsurance context no longer valid (%s) — clearing reference.",
            exc,
        )
        _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT = None


def _refresh_native_vahan_context_liveness() -> None:
    global _VAHAN_NATIVE_PERSISTENT_CONTEXT
    ctx = _VAHAN_NATIVE_PERSISTENT_CONTEXT
    if ctx is None:
        return
    try:
        list(ctx.pages)
    except Exception as exc:
        logger.warning(
            "handle_browser_opening: native Vahan context no longer valid (%s) — clearing reference.",
            exc,
        )
        _VAHAN_NATIVE_PERSISTENT_CONTEXT = None


def _refresh_native_context_liveness_for_site(site_label: str) -> None:
    sl = (site_label or "").strip()
    if sl == "DMS":
        _refresh_native_dms_context_liveness()
    elif sl == "Insurance":
        _refresh_native_insurance_context_liveness()
    elif sl == "CPAInsurance":
        _refresh_native_cpa_insurance_context_liveness()
    elif sl == "Vahan":
        _refresh_native_vahan_context_liveness()


def _dms_browser_closed_operator_message(site_label: str) -> str:
    sl = (site_label or "").strip()
    if sl == "Insurance":
        retry = "Press Generate Insurance again to open a new browser window."
    elif sl == "CPAInsurance":
        retry = "Press CPA Insurance again to open a new browser window."
    elif sl == "Vahan":
        retry = (
            "Press Warm Vahan or retry the RTO action to open a new browser window "
            "(same flow as after login: open the site, then press the button again)."
        )
    else:
        retry = "Press Create Invoice again to open a new browser window."
    return (
        f"{site_label}: the automation browser was closed while waiting for login or session setup. "
        f"{retry} "
        "If you already logged in, leave that window open until the run finishes."
    )


def _page_matches_site_for_dms_loose(page_url: str, site_base_url: str) -> bool:
    """Match any Siebel / Hero tab on the same host as ``DMS_BASE_URL`` (operator pre-login path drift)."""
    pu = (page_url or "").strip()
    bu = (site_base_url or "").strip()
    if not pu or not bu:
        return False
    low = pu.lower()
    if "blank" in low or low.startswith("chrome://") or low.startswith("edge://") or low.startswith("about:"):
        return False
    ph, bh = _hostname_for_site_match(pu), _hostname_for_site_match(bu)
    if not ph or not bh or ph != bh:
        return False
    return ("siebel" in low) or ("heroconnect" in low) or ("edealer" in low) or ("swecmd=" in low)


def retain_automation_browser_for_operator_manual_close() -> None:
    """
    Move Playwright browser / persistent-context handles off the active maps into the retain list
    without calling ``Browser.close()``. Use from short-lived CLI test scripts so the operator can
    keep inspecting Siebel after Python exits (best-effort; CDP-attached Edge typically survives;
    Playwright-launched Chromium may still exit with the driver unless the process stays alive).
    """
    _retain_browsers_without_closing()


def _retain_browsers_without_closing() -> None:
    global _DMS_NATIVE_PERSISTENT_CONTEXT, _INSURANCE_NATIVE_PERSISTENT_CONTEXT, _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT, _VAHAN_NATIVE_PERSISTENT_CONTEXT
    for b in list(_KEEP_OPEN_BROWSERS):
        _RETAINED_BROWSERS_NO_CLOSE.append(b)
    _KEEP_OPEN_BROWSERS.clear()
    for b in list(_CDP_BROWSERS_BY_URL.values()):
        _RETAINED_BROWSERS_NO_CLOSE.append(b)
    _CDP_BROWSERS_BY_URL.clear()
    if _DMS_NATIVE_PERSISTENT_CONTEXT is not None:
        _RETAINED_BROWSERS_NO_CLOSE.append(_DMS_NATIVE_PERSISTENT_CONTEXT)
        _DMS_NATIVE_PERSISTENT_CONTEXT = None
    if _INSURANCE_NATIVE_PERSISTENT_CONTEXT is not None:
        _RETAINED_BROWSERS_NO_CLOSE.append(_INSURANCE_NATIVE_PERSISTENT_CONTEXT)
        _INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
    if _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT is not None:
        _RETAINED_BROWSERS_NO_CLOSE.append(_CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT)
        _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
    if _VAHAN_NATIVE_PERSISTENT_CONTEXT is not None:
        _RETAINED_BROWSERS_NO_CLOSE.append(_VAHAN_NATIVE_PERSISTENT_CONTEXT)
        _VAHAN_NATIVE_PERSISTENT_CONTEXT = None


def _get_playwright():
    """
    Lazily start Playwright **Sync** driver on the **current** thread.

    Callers must run browser automation from the **single** Playwright worker thread:
    ``await asyncio.get_running_loop().run_in_executor(get_playwright_executor(), ...)`` or
    ``run_playwright_callable_sync(fn)`` (see ``app.services.playwright_executor``).
    Using a different thread makes ``sync_playwright().start()`` fail (often reported as
    "Sync API inside the asyncio loop").
    Siebel (~14k LOC) remains sync ``Page`` APIs; a full ``async_api`` migration would be separate.
    """
    global _PW, _PW_THREAD_ID
    current_thread_id = threading.get_ident()
    if _PW is not None and _PW_THREAD_ID is not None and _PW_THREAD_ID != current_thread_id:
        _retain_browsers_without_closing()
        _PW = None
        _PW_THREAD_ID = None
    if _PW is None:
        fill_dms_phase("get_playwright_cold_start", thread_id=current_thread_id)
        _PW = sync_playwright().start()
        _PW_THREAD_ID = current_thread_id
    return _PW


@atexit.register
def _preserve_browsers_on_process_exit() -> None:
    """Best-effort: do not actively close automation browsers during interpreter shutdown."""
    try:
        _retain_browsers_without_closing()
    except Exception:
        pass


def _candidate_cdp_urls() -> list[str]:
    urls: list[str] = []
    explicit = (os.getenv("PLAYWRIGHT_CDP_URL") or "").strip()
    if explicit:
        urls.append(explicit)
    explicit_many = (os.getenv("PLAYWRIGHT_CDP_URLS") or "").strip()
    if explicit_many:
        urls.extend([u.strip() for u in explicit_many.split(",") if u.strip()])
    if PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT:
        urls.append(f"http://127.0.0.1:{PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT}")
    if not urls:
        urls.append("http://127.0.0.1:9222")
        urls.append("http://127.0.0.1:9223")
    seen: set[str] = set()
    unique_urls: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)
    return unique_urls


def _cdp_url_http_reachable(cdp_url: str) -> bool:
    """Fast probe: Chrome/Edge CDP serves ``/json/version`` over HTTP without Playwright."""
    base = (cdp_url or "").strip().rstrip("/")
    if not base:
        return False
    probe = f"{base}/json/version"
    try:
        with urllib.request.urlopen(probe, timeout=_CDP_PROBE_TIMEOUT_SEC) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 300
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def force_invalidate_cdp_cache() -> None:
    """Next ``_refresh_cdp_browsers`` runs full reconnect logic (TTL bypass)."""
    global _LAST_CDP_REFRESH_MONO
    _LAST_CDP_REFRESH_MONO = 0.0


def _close_playwright_browser_handle(browser: object) -> None:
    try:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _disconnect_all_playwright_browsers_on_worker_thread() -> None:
    """Must run on the Playwright sync worker thread (via ``run_playwright_callable_sync``)."""
    global _DMS_NATIVE_PERSISTENT_CONTEXT, _INSURANCE_NATIVE_PERSISTENT_CONTEXT, _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT, _VAHAN_NATIVE_PERSISTENT_CONTEXT
    if _DMS_NATIVE_PERSISTENT_CONTEXT is not None:
        _close_playwright_browser_handle(_DMS_NATIVE_PERSISTENT_CONTEXT)
        _DMS_NATIVE_PERSISTENT_CONTEXT = None
    if _INSURANCE_NATIVE_PERSISTENT_CONTEXT is not None:
        _close_playwright_browser_handle(_INSURANCE_NATIVE_PERSISTENT_CONTEXT)
        _INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
    if _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT is not None:
        _close_playwright_browser_handle(_CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT)
        _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
    if _VAHAN_NATIVE_PERSISTENT_CONTEXT is not None:
        _close_playwright_browser_handle(_VAHAN_NATIVE_PERSISTENT_CONTEXT)
        _VAHAN_NATIVE_PERSISTENT_CONTEXT = None
    for b in list(_KEEP_OPEN_BROWSERS):
        _close_playwright_browser_handle(b)
    _KEEP_OPEN_BROWSERS.clear()
    for _url, b in list(_CDP_BROWSERS_BY_URL.items()):
        _close_playwright_browser_handle(b)
    _CDP_BROWSERS_BY_URL.clear()
    for b in list(_RETAINED_BROWSERS_NO_CLOSE):
        _close_playwright_browser_handle(b)
    _RETAINED_BROWSERS_NO_CLOSE.clear()


def _kill_os_processes_listening_on_tcp_port(port: int) -> None:
    """
    Best-effort: terminate listener(s) on ``port`` so Chromium profile/SingletonLock releases.
    Only the managed debug port should be passed (default 9333); never arbitrary operator ports.
    """
    if port <= 0 or port > 65535:
        return
    creationflags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                timeout=15,
                creationflags=creationflags,
            )
            pids: set[int] = set()
            needle = f":{port}"
            for line in out.splitlines():
                line_l = line.strip()
                if "LISTENING" not in line_l.upper():
                    continue
                if needle not in line_l:
                    continue
                parts = line_l.split()
                if len(parts) < 2:
                    continue
                try:
                    pids.add(int(parts[-1]))
                except ValueError:
                    continue
            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=30,
                        check=False,
                        creationflags=creationflags,
                    )
                except Exception as exc:
                    logger.debug("handle_browser_opening: taskkill pid=%s: %s", pid, exc)
        else:
            try:
                out = subprocess.check_output(
                    ["lsof", "-ti", f":{port}"],
                    text=True,
                    timeout=15,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                return
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    pid = int(line)
                except ValueError:
                    continue
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        os.kill(pid, 9)
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("handle_browser_opening: port %s cleanup failed: %s", port, exc)


_TEARDOWN_PLAYWRIGHT_DISCONNECT_TIMEOUT_SEC = 5.0


def teardown_local_automation_browsers() -> dict[str, object]:
    """
    Disconnect Playwright CDP clients and terminate the managed Chromium process (debug port).
    Called from Electron quit (sidecar job) and from the dev SPA on tab-close / Retry-click so
    profile locks and zombie debug-port owners do not persist across runs.

    Order: **port kill first**, then cache invalidation, then Playwright disconnect (bounded by a
    timeout). Killing the OS process first frees a wedged Playwright executor whose previous job
    is still waiting on the now-dead browser; without that, ``run_playwright_callable_sync`` for
    the disconnect step would queue forever behind the stuck job.
    """
    port = int(PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333)
    _kill_os_processes_listening_on_tcp_port(port)
    force_invalidate_cdp_cache()
    playwright_disconnect_ok = False
    try:
        get_playwright_executor().submit(
            _disconnect_all_playwright_browsers_on_worker_thread
        ).result(timeout=_TEARDOWN_PLAYWRIGHT_DISCONNECT_TIMEOUT_SEC)
        playwright_disconnect_ok = True
    except concurrent.futures.TimeoutError:
        logger.warning(
            "handle_browser_opening: Playwright disconnect timed out after %.1fs (executor busy); "
            "port-%s process kill already issued.",
            _TEARDOWN_PLAYWRIGHT_DISCONNECT_TIMEOUT_SEC,
            port,
        )
    except Exception as exc:
        logger.warning("handle_browser_opening: Playwright disconnect failed: %s", exc)
    return {
        "teardown": True,
        "managed_debug_port": port,
        # False when another request still holds the single Playwright worker (e.g. operator closed
        # Edge mid-run). SPA should tell dev to restart uvicorn — new HTTP work cannot queue behind
        # the stuck callable until the process exits.
        "playwright_disconnect_ok": playwright_disconnect_ok,
    }


def _evict_unreachable_cached_cdp_browsers() -> None:
    """Drop stale CDP handles when the HTTP endpoint is gone (operator closed the browser)."""
    for url in list(_CDP_BROWSERS_BY_URL.keys()):
        if _cdp_url_http_reachable(url):
            continue
        stale = _CDP_BROWSERS_BY_URL.pop(url, None)
        if stale is None:
            continue
        try:
            run_playwright_callable_sync(lambda b=stale: _close_playwright_browser_handle(b))
        except Exception:
            pass


def _ordered_cdp_urls_for_connect(candidates: list[str], reachable: dict[str, bool]) -> list[str]:
    """Prefer URLs that responded to HTTP probe (parallel), then try the rest in original order."""
    yes = [u for u in candidates if reachable.get(u) is True]
    no = [u for u in candidates if reachable.get(u) is not True]
    return yes + no


def _refresh_cdp_browsers() -> None:
    _evict_unreachable_cached_cdp_browsers()
    global _LAST_CDP_REFRESH_MONO
    now = time.monotonic()
    with _CDP_REFRESH_LOCK:
        if now - _LAST_CDP_REFRESH_MONO < _CDP_REFRESH_TTL_SEC:
            return
        _LAST_CDP_REFRESH_MONO = time.monotonic()

    candidates = _candidate_cdp_urls()
    reachable: dict[str, bool] = {}
    if len(candidates) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
            future_map = {pool.submit(_cdp_url_http_reachable, u): u for u in candidates}
            for fut in concurrent.futures.as_completed(future_map):
                u = future_map[fut]
                try:
                    reachable[u] = bool(fut.result())
                except Exception:
                    reachable[u] = False
    else:
        for u in candidates:
            reachable[u] = _cdp_url_http_reachable(u)

    connect_order = _ordered_cdp_urls_for_connect(candidates, reachable)

    pw = _get_playwright()
    for cdp_url in connect_order:
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
            logger.info("handle_browser_opening: connected to browser CDP at %s", cdp_url)
        except Exception:
            continue


def _find_browser_exe() -> tuple[str | None, str | None]:
    for name, extra_paths in (
        ("msedge", [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ]),
        ("chrome", [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ]),
    ):
        exe = shutil.which(name)
        if exe:
            return exe, name
        for p in extra_paths:
            if os.path.isfile(p):
                return p, name
    return None, None


def launch_site_background_detached(base_url: str) -> bool:
    """
    Best-effort background browser open that avoids focus steal on Windows.
    Used by warm-browser flows when CDP attach is unavailable.
    """
    target = (base_url or "").strip()
    if not target:
        return False
    exe, _channel = _find_browser_exe()
    if not exe:
        return False
    try:
        cmd = [exe, "--new-window", "--start-maximized", target]
        creation_flags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creation_flags |= subprocess.DETACHED_PROCESS
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # SW_SHOWNOACTIVATE (4): show maximized (see --start-maximized) without activating/focus steal.
            startupinfo.wShowWindow = 4  # SW_SHOWNOACTIVATE
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            startupinfo=startupinfo,
        )
        return True
    except Exception:
        return False


def _url_looks_like_dms_siebel_tab(url: str) -> bool:
    """Hero Connect / Siebel — never treat as the Insurance (MISP) tab when both are open in one browser."""
    u = (url or "").lower()
    return (
        "swecmd=" in u
        or "/siebel/" in u
        or "connect.heromotocorp.biz" in u
        or "heroconnect" in u
        or "edealerhmcl" in u
    )


def _try_cdp_maximize_browser_window(page: object) -> None:
    """Maximize the OS browser window via CDP (``Browser.setWindowBounds``).

    ``--start-maximized`` plus ``SW_SHOWNOACTIVATE`` often leaves Chromium at default size; this
    applies a true maximized state so taskbar restore fills the work area.
    """
    if page is None:
        return
    try:
        ctx = page.context
        session = ctx.new_cdp_session(page)
        win = session.send("Browser.getWindowForTarget", {})
        wid = win.get("windowId")
        if wid is None:
            return
        session.send(
            "Browser.setWindowBounds",
            {"windowId": wid, "bounds": {"windowState": "maximized"}},
        )
    except Exception as exc:
        logger.debug("handle_browser_opening: CDP maximize skipped: %s", exc)


def _try_cdp_focus_page(page: object) -> bool:
    """Use CDP to bring page to front and focus the document body."""
    if page is None:
        return False
    try:
        ctx = page.context
        session = ctx.new_cdp_session(page)
        session.send("Page.bringToFront", {})
        # Get the document and focus the body via DOM.focus
        try:
            doc = session.send("DOM.getDocument", {})
            root_id = doc.get("root", {}).get("nodeId")
            if root_id:
                # Find body element
                body = session.send("DOM.querySelector", {"nodeId": root_id, "selector": "body"})
                body_id = body.get("nodeId")
                if body_id:
                    session.send("DOM.focus", {"nodeId": body_id})
                    logger.info("handle_browser_opening: CDP Page.bringToFront + DOM.focus(body) succeeded")
                    return True
        except Exception as inner_exc:
            logger.debug("handle_browser_opening: CDP DOM.focus failed: %s", inner_exc)
        logger.info("handle_browser_opening: CDP Page.bringToFront succeeded (DOM.focus skipped)")
        return True
    except Exception as exc:
        logger.warning("handle_browser_opening: CDP focus page FAILED: %s", exc)
        return False


def _try_js_click_body_for_focus(page) -> bool:
    """Click body via JavaScript to transfer focus from omnibox to page content."""
    _js = """() => {
        let result = {};
        try {
            result.prevActive = document.activeElement ? document.activeElement.tagName : 'none';
            if (document.activeElement && document.activeElement.blur) {
                document.activeElement.blur();
            }
        } catch (e) { result.blurErr = e.message; }
        try {
            document.body.click();
        } catch (e) { result.bodyClickErr = e.message; }
        try {
            document.body.focus();
        } catch (e) { result.bodyFocusErr = e.message; }
        try {
            const el = document.querySelector('#formContent') || document.querySelector('input[name="SWEUserName"]');
            if (el) { 
                el.click(); 
                el.focus(); 
                result.focusedEl = el.tagName + (el.name ? '#' + el.name : '');
            }
        } catch (e) { result.elFocusErr = e.message; }
        result.newActive = document.activeElement ? (document.activeElement.tagName + (document.activeElement.name ? '#' + document.activeElement.name : '')) : 'none';
        return result;
    }"""
    try:
        result = page.evaluate(_js)
        logger.info("handle_browser_opening: JS body/form focus result: %s", result)
        return True
    except Exception as exc:
        logger.warning("handle_browser_opening: JS body focus FAILED: %s", exc)
        return False


def _find_portal_page_in_cdp_pages(
    pages: list,
    base_url: str,
    site_label: str,
    *,
    include_loading_blank: bool = True,
):
    """Pick an existing CDP tab for ``site_label`` before opening a duplicate tab/window."""
    want_host = _hostname_for_site_match(base_url)
    skip_siebel = _host_matched_portal_skips_dms_siebel(site_label)

    for page in pages:
        try:
            u = (page.url or "").strip()
        except Exception:
            u = ""
        if skip_siebel and _url_looks_like_dms_siebel_tab(u):
            continue
        if u and _page_matches_site_for_reuse(u, base_url, site_label):
            return page

    if want_host:
        for page in pages:
            try:
                u = (page.url or "").strip()
            except Exception:
                u = ""
            if skip_siebel and _url_looks_like_dms_siebel_tab(u):
                continue
            if u and _hostname_for_site_match(u) == want_host:
                return page

    if include_loading_blank:
        loading_candidates: list = []
        for page in pages:
            try:
                u = (page.url or "").strip()
            except Exception:
                u = ""
            if skip_siebel and _url_looks_like_dms_siebel_tab(u):
                continue
            low = u.lower()
            if not u or "blank" in low or low.startswith("about:"):
                loading_candidates.append(page)
        if len(loading_candidates) == 1:
            return loading_candidates[0]
        if loading_candidates and want_host:
            return loading_candidates[0]

    return None


def _try_attach_existing_managed_cdp_page(
    pw,
    cdp_url: str,
    base_url: str,
    site_label: str,
    *,
    headless: bool,
    channel: str,
):
    """Reuse a tab from an already-running managed CDP browser — avoid ``Popen --new-window`` duplicate."""
    _refresh_cdp_browsers()
    if not _cdp_url_http_reachable(cdp_url):
        return None, None
    try:
        browser = _CDP_BROWSERS_BY_URL.get(cdp_url)
        if browser is None:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            _CDP_BROWSERS_BY_URL[cdp_url] = browser
        existing: list = []
        for ctx in browser.contexts:
            for page in ctx.pages:
                existing.append(page)
        matched = _find_portal_page_in_cdp_pages(existing, base_url, site_label)
        if matched is not None:
            logger.info(
                "handle_browser_opening: reused existing CDP tab for %s (skipped independent Popen).",
                (site_label or "").strip() or "portal",
            )
            if not headless:
                _try_cdp_maximize_browser_window(matched)
            return matched, channel
    except Exception as exc:
        logger.debug("handle_browser_opening: CDP-first attach for %s failed: %s", site_label, exc)
    return None, None


def _launch_managed_browser_for_site(
    base_url: str, *, launch_background: bool = False, site_label: str = ""
):
    pw = _get_playwright()
    headless = not bool(DMS_PLAYWRIGHT_HEADED)
    port = PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333
    cdp_url = f"http://127.0.0.1:{port}"

    exe, channel = _find_browser_exe()
    channel = channel or "msedge"
    attached, ch = _try_attach_existing_managed_cdp_page(
        pw,
        cdp_url,
        base_url,
        site_label,
        headless=headless,
        channel=channel,
    )
    if attached is not None:
        return attached, ch

    attempted_independent_launch = False
    if exe:
        attempted_independent_launch = True
        try:
            profile_dir = str(_browser_profile_dir())
            os.makedirs(profile_dir, exist_ok=True)
            cmd = [
                exe,
                "--new-window",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile_dir}",
                f"--remote-debugging-port={port}",
            ]
            if headless:
                cmd.append("--headless=new")
            else:
                # CDP maximize after attach still required when warm launch uses SW_SHOWNOACTIVATE.
                cmd.append("--start-maximized")
            cmd.append(base_url)
            creation_flags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creation_flags |= subprocess.DETACHED_PROCESS
            startupinfo = None
            # Warm-browser: show maximized without activating so the Electron SPA keeps focus.
            # SW_SHOWNOACTIVATE (4) avoids SW_MINIMIZE (6), which can leave a grey/unresponsive shell
            # when combined with DETACHED_PROCESS and CDP attachment.
            if launch_background and os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 4  # SW_SHOWNOACTIVATE
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                startupinfo=startupinfo,
            )
            logger.info("handle_browser_opening: launched %s independently (port %s)", channel, port)
            _cdp_t0 = time.monotonic()
            # After a fresh OS spawn, CDP can take well above 8s on busy Windows; falling through to
            # Playwright-managed launch would leave this Edge running and open a second browser (focus race).
            _cdp_deadline = _cdp_t0 + 22.0
            while time.monotonic() < _cdp_deadline:
                try:
                    browser = pw.chromium.connect_over_cdp(cdp_url)
                    _CDP_BROWSERS_BY_URL[cdp_url] = browser
                    existing: list = []
                    for ctx in browser.contexts:
                        for page in ctx.pages:
                            existing.append(page)
                    matched = None
                    for page in existing:
                        url = (page.url or "").strip()
                        if not url or not _page_matches_site_for_reuse(url, base_url, site_label):
                            continue
                        if _host_matched_portal_skips_dms_siebel(site_label) and _url_looks_like_dms_siebel_tab(
                            url
                        ):
                            continue
                        matched = page
                        break
                    if matched is not None:
                        logger.info(
                            "handle_browser_opening: connected to %s via CDP at %s "
                            "(browser window stays open even on automation errors).",
                            channel,
                            cdp_url,
                        )
                        if not headless:
                            _try_cdp_maximize_browser_window(matched)
                        return matched, channel
                    # Edge was started with `base_url` on the CLI — often one tab whose URL is still
                    # blank while CDP attaches. Do not open a second tab (previous behavior).
                    if len(existing) == 1:
                        pg0 = existing[0]
                        if not launch_background:
                            try:
                                pg0.wait_for_load_state("domcontentloaded", timeout=10_000)
                            except Exception:
                                pass
                        try:
                            u0 = (pg0.url or "").strip()
                        except Exception:
                            u0 = ""
                        if _host_matched_portal_skips_dms_siebel(site_label) and _url_looks_like_dms_siebel_tab(
                            u0
                        ):
                            if launch_background:
                                # Silent warm: new_page + goto would foreground Edge/Chrome on Windows.
                                continue
                            logger.info(
                                "handle_browser_opening: sole CDP tab is Siebel/DMS — opening a new tab for %s instead of reusing DMS (base=%s).",
                                (site_label or "").strip() or "portal",
                                (base_url or "")[:120],
                            )
                            try:
                                ctx0 = browser.contexts[0] if browser.contexts else browser.new_context()
                                pg_new = ctx0.new_page()
                                pg_new.goto(base_url, wait_until="domcontentloaded", timeout=20_000)
                                if not headless:
                                    _try_cdp_maximize_browser_window(pg_new)
                                return pg_new, channel
                            except Exception as exc:
                                logger.warning(
                                    "handle_browser_opening: could not open %s tab while DMS-only tab was present: %s",
                                    (site_label or "").strip() or "portal",
                                    exc,
                                )
                                continue
                        logger.info(
                            "handle_browser_opening: reusing single tab from independent %s launch (avoid duplicate insurance/DMS tab).",
                            channel,
                        )
                        if not headless:
                            _try_cdp_maximize_browser_window(pg0)
                        return pg0, channel
                    if len(existing) > 1:
                        want_host = _hostname_for_site_match(base_url)
                        for page in existing:
                            if not launch_background:
                                try:
                                    page.wait_for_load_state("domcontentloaded", timeout=3500)
                                except Exception:
                                    pass
                            try:
                                u = (page.url or "").strip()
                            except Exception:
                                u = ""
                            if u and _page_matches_site_for_reuse(u, base_url, site_label):
                                if not headless:
                                    _try_cdp_maximize_browser_window(page)
                                return page, channel
                        if want_host:
                            for page in existing:
                                try:
                                    u = (page.url or "").strip()
                                    if u and _hostname_for_site_match(u) == want_host:
                                        if not headless:
                                            _try_cdp_maximize_browser_window(page)
                                        return page, channel
                                except Exception:
                                    continue
                    # Independent Popen passed ``base_url`` on the CLI — never ``ctx.new_page()`` here
                    # (that created a second MISP/Insurance tab while the CLI tab was still loading).
                    if launch_background:
                        continue
                    want_host = _hostname_for_site_match(base_url)
                    for page in existing:
                        try:
                            u = (page.url or "").strip()
                        except Exception:
                            u = ""
                        if _host_matched_portal_skips_dms_siebel(site_label) and _url_looks_like_dms_siebel_tab(
                            u
                        ):
                            continue
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except Exception:
                            pass
                        try:
                            u = (page.url or "").strip()
                        except Exception:
                            u = ""
                        low = u.lower()
                        if not u or "blank" in low or low.startswith("about:"):
                            logger.info(
                                "handle_browser_opening: reusing loading tab from independent %s launch "
                                "(avoid duplicate %s tab).",
                                channel,
                                (site_label or "").strip() or "portal",
                            )
                            if not headless:
                                _try_cdp_maximize_browser_window(page)
                            return page, channel
                        if want_host and u and _hostname_for_site_match(u) == want_host:
                            if not headless:
                                _try_cdp_maximize_browser_window(page)
                            return page, channel
                        if u and _page_matches_site_for_reuse(u, base_url, site_label):
                            if not headless:
                                _try_cdp_maximize_browser_window(page)
                            return page, channel
                    continue
                except Exception:
                    pass
                time.sleep(0.05)
            logger.warning(
                "handle_browser_opening: launched %s but could not attach via CDP at %s within ~22s — "
                "not starting a second Playwright-managed browser (close duplicate Edge if any, wait, retry).",
                channel,
                cdp_url,
            )
            # Avoid chromium.launch fallback: an Edge process was already spawned above; fallback would
            # race two windows for focus and lose the persistent profile session on the first window.
            return None, None
        except Exception as exc:
            logger.warning(
                "handle_browser_opening: independent launch of %s failed: %s — falling back to Playwright-managed launch",
                channel,
                exc,
            )

    if attempted_independent_launch:
        logger.info(
            "handle_browser_opening: independent CDP attach unavailable; trying Playwright-managed launch fallback."
        )

    if launch_background:
        # Silent warm path: do not use Playwright-managed fallback because headed launch
        # frequently steals focus from the operator app on Windows.
        return None, None

    channels = ["msedge", "chrome"]
    launch_args: list[str] = []
    if PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT:
        launch_args.append(f"--remote-debugging-port={PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT}")
    for ch in channels:
        try:
            browser = pw.chromium.launch(
                channel=ch,
                headless=headless,
                args=launch_args if launch_args else [],
            )
            _KEEP_OPEN_BROWSERS.append(browser)
            context = browser.new_context()
            page = context.new_page()
            page.goto(base_url, wait_until="domcontentloaded", timeout=5000)
            logger.warning(
                "handle_browser_opening: fell back to Playwright-managed %s launch (browser may close on errors)",
                ch,
            )
            if not headless:
                _try_cdp_maximize_browser_window(page)
            return page, ch
        except Exception as exc:
            logger.warning("handle_browser_opening: failed to launch %s browser: %s", ch, exc)
            continue
    return None, None


def _page_matches_site_for_reuse(page_url: str, site_base_url: str, site_label: str) -> bool:
    """Match an open browser tab to the configured site URL.

    **Vahan** and **Insurance** / **CPAInsurance** use hostname-only matching so tabs still match after login
    (paths move away from ``login.xhtml`` / partner-login into SPAs like ``/ekycpage``).
    **CPAInsurance** also treats all ``*.allianceassure.in`` hosts as one portal for tab reuse.
    Other sites keep path-prefix matching via :func:`_playwright_page_url_matches_site_base`.
    """
    sl = (site_label or "").strip()
    if sl == "Vahan":
        ph = _hostname_for_site_match(page_url)
        bh = _hostname_for_site_match(site_base_url)
        return bool(ph and bh and ph == bh)
    if sl in ("Insurance", "CPAInsurance"):
        low = (page_url or "").strip().lower()
        if "blank" in low or low.startswith("chrome://") or low.startswith("edge://") or low.startswith("about:"):
            return False
        if _url_looks_like_dms_siebel_tab(page_url):
            return False
        ph = _hostname_for_site_match(page_url)
        bh = _hostname_for_site_match(site_base_url)
        if sl == "CPAInsurance" and ph and bh and _cpa_alliance_hosts_equivalent(ph, bh):
            return True
        return bool(ph and bh and ph == bh)
    return _playwright_page_url_matches_site_base(page_url, site_base_url)


def _playwright_page_url_matches_site_base(page_url: str, site_base_url: str) -> bool:
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
        ph, bh = _hostname_for_site_match(pu), _hostname_for_site_match(bu.strip())
        if ph and bh:
            if ph != bh:
                return False
        elif pp.netloc.lower() != bp.netloc.lower():
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
        return False
    except Exception:
        t = bu.rstrip("/")
        return pu.startswith(t) or t in pu


def _siebel_prefill_status_any_frame(page) -> tuple[str, dict | None]:
    """``prefilled`` | ``not_prefilled`` | ``no_form`` — scans main + child frames for SWE* login fields."""
    any_incomplete = False
    for fr in _ordered_unique_playwright_frames(page):
        try:
            d = fr.evaluate(_SIEBEL_PREFILL_DETECT_JS)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        st = d.get("status")
        if st == "prefilled":
            return "prefilled", d
        if st == "not_prefilled":
            any_incomplete = True
    if any_incomplete:
        return "not_prefilled", None
    return "no_form", None


def _nudge_siebel_login_fields_for_autofill(page) -> None:
    """Focus username/password in each frame so Chrome password manager can inject values."""
    for fr in _ordered_unique_playwright_frames(page):
        try:
            fr.evaluate(_SIEBEL_LOGIN_AUTOFILL_NUDGE_JS)
        except Exception:
            continue


def _dms_steal_focus_from_omnibox_for_login(page) -> None:
    """Move keyboard focus from Chromium's address bar onto the Siebel login surface.

    After ``goto`` or tab reuse (for example Subdealer challan Retry after ``teardown-local-browsers``),
    the browser sometimes leaves focus in the omnibox so **Login** clicks / ``Enter`` never reach
    ``#formContent`` / ``SWEEntryForm`` (typing and submit appear to do nothing).
    """
    logger.info("handle_browser_opening: _dms_steal_focus_from_omnibox_for_login STARTING")
    try:
        page.bring_to_front()
        logger.info("handle_browser_opening: page.bring_to_front() done")
    except Exception as exc:
        logger.warning("handle_browser_opening: bring_to_front failed: %s", exc)
    # Use CDP to focus the page frame (moves focus away from browser chrome/omnibox).
    _try_cdp_focus_page(page)
    try:
        page.wait_for_timeout(100)
    except Exception:
        time.sleep(0.1)
    # JavaScript click on body/form to claim focus in page content.
    _try_js_click_body_for_focus(page)
    try:
        page.wait_for_timeout(80)
    except Exception:
        time.sleep(0.08)
    # F6 to toggle focus between omnibox and page (browser standard).
    try:
        page.keyboard.press("F6")
    except Exception:
        pass
    try:
        page.wait_for_timeout(80)
    except Exception:
        time.sleep(0.08)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        page.wait_for_timeout(40)
    except Exception:
        time.sleep(0.04)
    _dms_viewport_click_steal_from_omnibox(page)
    for fr in _ordered_unique_playwright_frames(page):
        for sel in (
            "#formContent.siebui-login-form",
            "div#formContent",
            ".siebui-login-form",
            "#formContent",
        ):
            try:
                loc = fr.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    try:
                        loc.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass
                    try:
                        loc.click(timeout=2000, force=True)
                    except Exception:
                        try:
                            loc.click(timeout=2000)
                        except Exception:
                            pass
                    try:
                        page.wait_for_timeout(80)
                    except Exception:
                        time.sleep(0.08)
                    return
            except Exception:
                continue
    _nudge_siebel_login_fields_for_autofill(page)


def _siebel_try_login_submit_fallback_any_frame(page) -> bool:
    """True when username has text and password field looks filled (DOM value or Chrome ``:autofill``)."""
    for fr in _ordered_unique_playwright_frames(page):
        try:
            if bool(fr.evaluate(_SIEBEL_TRY_LOGIN_SUBMIT_FALLBACK_JS)):
                return True
        except Exception:
            continue
    return False


def _siebel_try_login_submit_aggressive_any_frame(page) -> bool:
    """True on ``SWECM=S`` shell when username is set and password field is visible (value may be hidden from JS)."""
    for fr in _ordered_unique_playwright_frames(page):
        try:
            if bool(fr.evaluate(_SIEBEL_TRY_LOGIN_SUBMIT_AGGRESSIVE_JS)):
                return True
        except Exception:
            continue
    return False


def _siebel_login_screen_swecm_s(page) -> bool:
    try:
        return "swecm=s" in (page.url or "").lower()
    except Exception:
        return False


def _siebel_portal_logged_in_url(page) -> bool:
    """Siebel keeps ``SWECmd=Login`` on eDealer home — ``SWEPL=1`` without ``SWECM=S`` means past the login form."""
    try:
        ul = (page.url or "").lower()
    except Exception:
        return False
    return "swecmd=login" in ul and "swepl=1" in ul and "swecm=s" not in ul


def _try_auto_login_if_prefilled(page, log_path: Path | str | None = None) -> bool:
    """If Siebel fields are filled (browser autofill or prior typing), click **Login** and wait for redirect."""

    def _is_login_url() -> bool:
        try:
            u = (page.url or "").lower()
            return (
                ("swecmd=login" in u)
                or u.rstrip("/").endswith("/login")
                or ("misp-partner-login" in u)
            )
        except Exception:
            return False

    if not _is_login_url():
        st0, _d0 = _siebel_prefill_status_any_frame(page)
        if st0 == "no_form":
            logger.info("handle_browser_opening: no login form detected; continuing with existing logged-in session.")
            return True
        return False

    logger.info(
        "handle_browser_opening: waiting up to %.1fs for browser autofill on Siebel login (all frames), then Login.",
        _DMS_BROWSER_AUTOFILL_LOGIN_MAX_SEC,
    )
    deadline = time.monotonic() + _DMS_BROWSER_AUTOFILL_LOGIN_MAX_SEC
    poll = int(_DMS_BROWSER_AUTOFILL_POLL_MS)
    iteration = 0
    while time.monotonic() < deadline:
        iteration += 1
        if iteration in (2, 8, 16):
            _nudge_siebel_login_fields_for_autofill(page)
        st, data = _siebel_prefill_status_any_frame(page)
        if st == "no_form" and not _is_login_url():
            logger.info("handle_browser_opening: left login URL while polling autofill — continuing.")
            return True
        if st == "prefilled" and isinstance(data, dict):
            logger.info(
                "handle_browser_opening: login form has credentials in DOM (user=%s) — clicking Login",
                data.get("user", "?"),
            )
            if not _click_siebel_login_submit(page):
                return False
            _capture_login_main_page_snapshot(page, log_path, "after_autofill_submit")
            return _wait_after_siebel_login_submit_with_optional_second_click(page, log_path=log_path)
        try:
            page.wait_for_timeout(poll)
        except Exception:
            time.sleep(poll / 1000.0)

    st_last, data_last = _siebel_prefill_status_any_frame(page)
    if st_last == "prefilled" and isinstance(data_last, dict):
        logger.info(
            "handle_browser_opening: login form prefilled at end of autofill window (user=%s) — clicking Login",
            data_last.get("user", "?"),
        )
        if not _click_siebel_login_submit(page):
            return False
        _capture_login_main_page_snapshot(page, log_path, "after_autofill_window_submit")
        return _wait_after_siebel_login_submit_with_optional_second_click(page, log_path=log_path)
    if _is_login_url():
        _try_submit = False
        _reason = ""
        if _siebel_login_screen_swecm_s(page) and _siebel_try_login_submit_aggressive_any_frame(page):
            _try_submit = True
            _reason = "SWECM=S shell, username set, password field visible (Chrome may hide password value from JS)"
        elif _siebel_try_login_submit_fallback_any_frame(page):
            _try_submit = True
            _reason = "username set and password has value or :autofill"
        if _try_submit:
            logger.info(
                "handle_browser_opening: autofill window ended — attempting Login once (%s).",
                _reason,
            )
            if not _click_siebel_login_submit(page):
                return False
            _capture_login_main_page_snapshot(page, log_path, "after_aggressive_submit")
            return _wait_after_siebel_login_submit_with_optional_second_click(page, log_path=log_path)
    return False


def _login_form_visible_eval_js() -> str:
    return """() => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 2 && r.height > 2;
        };
        const u1 = document.querySelector('input[name="SWEUserName"]');
        const p1 = document.querySelector('input[name="SWEPassword"], input[type="password"]');
        const u2 = document.querySelector(
          'input[name="username"], input[placeholder*="User Name" i], input[aria-label*="User Name" i]'
        );
        const p2 = document.querySelector('input[type="password"]');
        const siebel = (u1 && vis(u1)) || (p1 && vis(p1));
        const generic = (u2 && vis(u2)) && (p2 && vis(p2));
        return siebel || generic;
    }"""


def _iter_page_frames(pg):
    """Main document first, then other frames (Siebel login often lives in an iframe)."""
    yield pg.main_frame
    seen: set[int] = {id(pg.main_frame)}
    try:
        for fr in pg.frames:
            if id(fr) in seen:
                continue
            seen.add(id(fr))
            yield fr
    except Exception:
        pass


def _login_form_visible_any_frame(pg) -> bool:
    for fr in _iter_page_frames(pg):
        try:
            if bool(fr.evaluate(_login_form_visible_eval_js())):
                return True
        except Exception:
            continue
    return False


def _dms_siebel_post_login_shell_any_frame(pg) -> bool:
    """Open UI chrome present (third-level / banner) — strong signal the operator passed login."""
    _js = """() => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 4 && r.height > 4;
        };
        const el = document.querySelector('#s_vctrl_div, .siebui-banner-btn, #_sweview');
        return !!(el && vis(el));
    }"""
    for fr in _iter_page_frames(pg):
        try:
            if bool(fr.evaluate(_js)):
                return True
        except Exception:
            continue
    return False


def _dms_siebel_third_level_bar_wide_any_frame(pg) -> bool:
    """Hero Third Level View Bar painted wide — logged-in shell even if top URL still contains ``SWECmd=Login``."""
    for fr in _iter_page_frames(pg):
        try:
            if bool(fr.evaluate(_SIEBEL_THIRD_LEVEL_BAR_WIDE_JS)):
                return True
        except Exception:
            continue
    return False


def _is_ready_after_login_page(pg) -> bool:
    """True when automation can continue: not on a login surface, or login controls are gone (any frame)."""
    try:
        u = (pg.url or "").lower()
        on_misp = "misp-partner-login" in u
        on_path_login = u.rstrip("/").endswith("/login") and "siebel" not in u
        on_siebel_login = "swecmd=login" in u
        dms_like = "siebel" in u or "heroconnect" in u or "edealer" in u

        form = _login_form_visible_any_frame(pg)
        logger.info(
            "handle_browser_opening: _is_ready_after_login_page: url=%s on_siebel_login=%s dms_like=%s form_visible=%s",
            u[:150], on_siebel_login, dms_like, form
        )

        if on_misp or on_path_login:
            return not form

        # If login form is visible, we're NOT past login regardless of URL
        if form:
            logger.info("handle_browser_opening: _is_ready_after_login_page returning False (login form still visible)")
            return False

        if dms_like and not on_siebel_login:
            logger.info("handle_browser_opening: _is_ready_after_login_page returning True (dms_like, not login URL, no form)")
            return True

        if dms_like and on_siebel_login:
            # eDealer home / portal: URL stays ``SWECmd=Login`` but gains ``SWEPL=1`` and drops ``SWECM=S``.
            if _siebel_portal_logged_in_url(pg):
                return True
            # Siebel may keep ``SWECmd=Login`` while Open UI (e.g. wide ``#s_vctrl_div``) is ready on detail views.
            if _dms_siebel_post_login_shell_any_frame(pg) and not form:
                return True
            if _dms_siebel_third_level_bar_wide_any_frame(pg) and not form:
                return True
            return False

        if not on_siebel_login and not on_misp and not on_path_login:
            return True
        return not form
    except Exception:
        return False


def _dms_manual_login_complete_browser_js() -> str:
    """Predicate for ``wait_for_function`` — mirrors :func:`_is_ready_after_login_page` via ``window.frames``."""
    return """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const loginFormInDoc = (doc) => {
        if (!doc) return false;
        const u1 = doc.querySelector('input[name="SWEUserName"]');
        const p1 = doc.querySelector('input[name="SWEPassword"], input[type="password"]');
        const u2 = doc.querySelector(
          'input[name="username"], input[placeholder*="User Name" i], input[aria-label*="User Name" i]'
        );
        const p2 = doc.querySelector('input[type="password"]');
        const siebel = (u1 && vis(u1)) || (p1 && vis(p1));
        const generic = (u2 && vis(u2)) && (p2 && vis(p2));
        return siebel || generic;
      };
      const shellInDoc = (doc) => {
        if (!doc) return false;
        const el = doc.querySelector('#s_vctrl_div, .siebui-banner-btn, #_sweview');
        return !!(el && vis(el));
      };
      const stripInDoc = (doc) => {
        if (!doc) return false;
        const bar = doc.getElementById('s_vctrl_div');
        if (!bar || !vis(bar)) return false;
        const r = bar.getBoundingClientRect();
        return r.width >= 100 && r.height >= 12;
      };
      const scan = (w) => {
        try {
          const d = w.document;
          return { form: loginFormInDoc(d), shell: shellInDoc(d), strip: stripInDoc(d) };
        } catch (e) {
          return { form: false, shell: false, strip: false };
        }
      };
      let anyForm = false, anyShell = false, anyStrip = false;
      try {
        const t = scan(window);
        anyForm = t.form;
        anyShell = t.shell;
        anyStrip = t.strip;
      } catch (e) {}
      try {
        for (let i = 0; i < window.frames.length; i++) {
          try {
            const r = scan(window.frames[i]);
            if (r.form) anyForm = true;
            if (r.shell) anyShell = true;
            if (r.strip) anyStrip = true;
          } catch (e) {}
        }
      } catch (e) {}
      const u = (location.href || '').toLowerCase();
      if (u.includes('misp-partner-login')) return !anyForm;
      if (!u.includes('swecmd=login')) {
        if (u.includes('siebel') || u.includes('heroconnect') || u.includes('edealer')) return true;
        if (u.endsWith('/login') && !u.includes('siebel')) return !anyForm;
        return true;
      }
      // Still on Siebel SWECmd=Login — eDealer portal uses SWEPL=1 without SWECM=S after successful login.
      if (u.includes('swepl=1') && !u.includes('swecm=s')) return true;
      return !!((anyShell || anyStrip) && !anyForm);
    }"""


def _wait_login_or_prompt_after_open(
    page, site_label: str, log_path: Path | str | None = None
):
    """
    After opening or reusing a tab, wait for session/login to clear.

    **DMS** waits up to ``DMS_LOGIN_MANUAL_WAIT_MS`` while polling (login form is detected in **any**
    frame, and post-login shell markers on Hero Open UI). After that, a final ``wait_for_function``
    window (120s) catches a manual **Login** click so the same Create Invoice run can continue without
    a second app click. Operator credentials may be snapshotted to ``operator_dms_login.json``.

    Other sites keep a short ~1.5s probe for backwards compatibility.

    Returns ``(page, None)`` when ready, or ``(None, operator_message)`` when login is still required.
    """
    sl = (site_label or "").strip()
    max_wait_ms = DMS_LOGIN_MANUAL_WAIT_MS if sl == "DMS" else 1500
    deadline = time.monotonic() + max(0.25, max_wait_ms / 1000.0)
    poll_ms = 450
    last_snap = 0.0
    _last_bar_log = 0.0

    try:
        if page.is_closed():
            _clear_native_persistent_context_for_site(sl, "page closed before login wait")
            return None, _dms_browser_closed_operator_message(sl)
    except Exception:
        pass

    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                _clear_native_persistent_context_for_site(sl, "page closed during login wait")
                return None, _dms_browser_closed_operator_message(sl)
        except Exception:
            pass
        try:
            if _is_ready_after_login_page(page):
                logger.info("handle_browser_opening: login/session became ready in same request.")
                return page, None
        except Exception as e:
            if _is_browser_disconnected_error(e):
                _clear_native_persistent_context_for_site(sl, "disconnect while probing login readiness")
                return None, _dms_browser_closed_operator_message(sl)
            raise
        if sl == "DMS":
            now = time.monotonic()
            if now - last_snap >= 2.0:
                try:
                    _maybe_snapshot_stored_credentials_from_login_page(page)
                except Exception:
                    pass
                last_snap = now
            if log_path and now - _last_bar_log >= 1.1:
                _last_bar_log = now
                try:
                    bar = _read_siebel_login_status_bar_any_frame(page)
                    if bar:
                        _append_playwright_dms_capture_line(
                            log_path, "LOGIN_STATUSBAR_MANUAL_WAIT", bar, also_logger=True
                        )
                except Exception:
                    pass
        try:
            page.wait_for_timeout(poll_ms)
        except Exception as e:
            if _is_browser_disconnected_error(e):
                try:
                    if page.is_closed():
                        _clear_native_persistent_context_for_site(
                            sl, "disconnect during login wait_for_timeout"
                        )
                except Exception:
                    _clear_native_persistent_context_for_site(
                        sl, "disconnect during login wait_for_timeout"
                    )
                return None, _dms_browser_closed_operator_message(sl)
            time.sleep(poll_ms / 1000.0)

    if sl == "DMS":
        try:
            if page.is_closed():
                _clear_native_persistent_context_for_site(sl, "page closed before wait_for_function gate")
                return None, _dms_browser_closed_operator_message(sl)
        except Exception:
            pass
        try:
            page.wait_for_function(_dms_manual_login_complete_browser_js(), timeout=120_000)
        except PlaywrightTimeout:
            pass
        except Exception as e:
            if _is_browser_disconnected_error(e):
                _clear_native_persistent_context_for_site(sl, "disconnect during login wait_for_function")
                return None, _dms_browser_closed_operator_message(sl)
        try:
            if page.is_closed():
                _clear_native_persistent_context_for_site(sl, "page closed after wait_for_function gate")
                return None, _dms_browser_closed_operator_message(sl)
        except Exception:
            pass
        try:
            if _is_ready_after_login_page(page):
                logger.info(
                    "handle_browser_opening: DMS login/session ready after manual login (wait_for_function gate)."
                )
                return page, None
        except Exception as e:
            if _is_browser_disconnected_error(e):
                _clear_native_persistent_context_for_site(sl, "disconnect after wait_for_function gate")
                return None, _dms_browser_closed_operator_message(sl)
            raise

    try:
        if _siebel_portal_logged_in_url(page):
            logger.info(
                "handle_browser_opening: Siebel portal URL (SWEPL=1, no SWECM=S) — continuing (final fallback)."
            )
            return page, None
        ul = (page.url or "").lower()
        if (
            "swecmd=login" not in ul
            and "misp-partner-login" not in ul
            and not ul.rstrip("/").endswith("/login")
        ):
            logger.info("handle_browser_opening: continuing after login transition (final fallback).")
            return page, None
    except Exception:
        pass

    return None, f"{site_label} Opened. Please login. And then press button again"


def _find_matching_page_on_native_dms_context(base_url: str, site_label: str) -> object | None:
    """Backward-compatible name; delegates to native-site finder."""
    return _find_matching_page_on_native_site_context(base_url, site_label)


def _find_matching_page_on_native_site_context(base_url: str, site_label: str) -> object | None:
    sl = (site_label or "").strip()
    if sl == "DMS":
        ctx = _DMS_NATIVE_PERSISTENT_CONTEXT
        clear_ctx = _clear_native_dms_persistent_context
    elif sl == "Insurance":
        ctx = _INSURANCE_NATIVE_PERSISTENT_CONTEXT
        clear_ctx = _clear_native_insurance_persistent_context
    elif sl == "CPAInsurance":
        ctx = _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
        clear_ctx = _clear_native_cpa_insurance_persistent_context
    elif sl == "Vahan":
        ctx = _VAHAN_NATIVE_PERSISTENT_CONTEXT
        clear_ctx = _clear_native_vahan_persistent_context
    else:
        return None
    if ctx is None:
        return None
    try:
        pages = list(ctx.pages)
    except Exception:
        clear_ctx("list pages failed during native tab match")
        return None
    for pg in pages:
        try:
            if pg.is_closed():
                continue
            url = (pg.url or "").strip()
        except Exception:
            continue
        if url and _page_matches_site_for_reuse(url, base_url, site_label):
            return pg
    if sl == "DMS":
        for pg in pages:
            try:
                if pg.is_closed():
                    continue
                url = (pg.url or "").strip()
            except Exception:
                continue
            if url and _page_matches_site_for_dms_loose(url, base_url):
                logger.info(
                    "handle_browser_opening: reusing native DMS tab via loose Siebel/host match url=%r",
                    url[:140],
                )
                return pg
    return None


def _navigate_native_site_persistent_to(target_url: str, site_label: str) -> object | None:
    """Navigate within a native persistent Chromium context (DMS, Insurance, or Vahan)."""
    sl = (site_label or "").strip()
    if sl == "DMS":
        _refresh_native_dms_context_liveness()
        ctx = _DMS_NATIVE_PERSISTENT_CONTEXT
        clear_on_disc = lambda r: _clear_native_dms_persistent_context(r)
    elif sl == "Insurance":
        _refresh_native_insurance_context_liveness()
        ctx = _INSURANCE_NATIVE_PERSISTENT_CONTEXT
        clear_on_disc = lambda r: _clear_native_insurance_persistent_context(r)
    elif sl == "CPAInsurance":
        _refresh_native_cpa_insurance_context_liveness()
        ctx = _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
        clear_on_disc = lambda r: _clear_native_cpa_insurance_persistent_context(r)
    elif sl == "Vahan":
        _refresh_native_vahan_context_liveness()
        ctx = _VAHAN_NATIVE_PERSISTENT_CONTEXT
        clear_on_disc = lambda r: _clear_native_vahan_persistent_context(r)
    else:
        return None
    if ctx is None:
        return None
    t = (target_url or "").strip()
    if not t:
        return None
    try:
        pages = list(ctx.pages)
        target_page = None
        for p in pages:
            try:
                if p.is_closed():
                    continue
                u = (p.url or "").strip()
            except Exception:
                u = ""
            if u and _page_matches_site_for_reuse(u, t, site_label):
                target_page = p
                break
        if target_page is None and sl == "DMS":
            for p in pages:
                try:
                    if p.is_closed():
                        continue
                    u = (p.url or "").strip()
                except Exception:
                    u = ""
                if u and _page_matches_site_for_dms_loose(u, t):
                    target_page = p
                    break
        if target_page is None:
            for p in pages:
                try:
                    if not p.is_closed():
                        target_page = p
                        break
                except Exception:
                    continue
        pg = target_page or ctx.new_page()
        if (
            sl == "CPAInsurance"
            and target_page is not None
            and pg is target_page
        ):
            try:
                cur_u = (target_page.url or "").strip()
            except Exception:
                cur_u = ""
            if (
                cur_u
                and _cpa_alliance_skip_goto_logged_in_surface(cur_u)
                and _cpa_alliance_hosts_equivalent(
                    _hostname_for_site_match(cur_u),
                    _hostname_for_site_match(t),
                )
            ):
                logger.info(
                    "handle_browser_opening: CPAInsurance reusing logged-in Alliance tab; skip goto to %r",
                    (t or "")[:120],
                )
                return target_page
        pg.goto(t, wait_until="domcontentloaded", timeout=20_000)
        logger.info(
            "handle_browser_opening: navigated native %s Chromium tab to target.", sl or "site"
        )
        return pg
    except Exception as exc:
        if _is_browser_disconnected_error(exc):
            clear_on_disc(f"native {sl} navigate lost browser")
        logger.warning("handle_browser_opening: native %s navigate failed: %s", sl, exc)
        return None


def _navigate_native_dms_persistent_to(target_url: str) -> object | None:
    """Navigate within the DMS native persistent Chromium context (no CDP / Edge reuse)."""
    return _navigate_native_site_persistent_to(target_url, "DMS")


def _native_persistent_launch_common_args(*, launch_background: bool) -> list[str]:
    _ = launch_background  # API stable; CDP maximize after attach handles warm vs foreground sizing.
    args = [
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if os.name == "nt":
        args.append("--start-maximized")
    # Do not pass ``--disable-blink-features=AutomationControlled`` or ``--disable-infobars``:
    # Chromium-based Edge shows an "unsupported command-line flag" infobar for them, which alarms
    # dealers. ``ignore_default_args`` still drops ``--enable-automation`` (and ``--no-sandbox`` on Windows).
    return args


def _native_persistent_ignore_default_args() -> list[str]:
    """Defaults Playwright strips from Chromium/Edge launches for operator portals."""
    out: list[str] = ["--enable-automation"]
    # Playwright often injects ``--no-sandbox``; Edge/Chrome then show an infobar ("unsupported
    # command-line flag"). Windows desktops do not need it (unlike many Linux/Docker CI images).
    if os.name == "nt":
        out.append("--no-sandbox")
    return out


def _launch_native_site_persistent_context(
    open_target: str,
    *,
    launch_background: bool,
    site_label: str,
    profile_dir: Path,
) -> tuple[object | None, str]:
    """Launch or reuse ``launch_persistent_context`` for DMS, Insurance, CPA, or Vahan (Insurance: Microsoft Edge channel)."""
    global _DMS_NATIVE_PERSISTENT_CONTEXT, _INSURANCE_NATIVE_PERSISTENT_CONTEXT, _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT, _VAHAN_NATIVE_PERSISTENT_CONTEXT
    sl = (site_label or "").strip()
    _refresh_native_context_liveness_for_site(sl)
    if sl == "DMS":
        ctx_before = "alive" if _DMS_NATIVE_PERSISTENT_CONTEXT is not None else "none"
        _dms_phase("launch_native_begin", site_label=sl, mode="reuse_context" if ctx_before == "alive" else "cold")
        _dms_phase("get_playwright_begin", site_label=sl)
    pw_was_cold = _PW is None
    pw = _get_playwright()
    if sl == "DMS":
        _dms_phase(
            "get_playwright_ready",
            site_label=sl,
            cold_start=pw_was_cold,
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    ot = (open_target or "").strip()
    if not ot:
        return None, ""
    args = _native_persistent_launch_common_args(launch_background=launch_background)
    try:
        if sl == "DMS":
            ctx_ref = _DMS_NATIVE_PERSISTENT_CONTEXT
        elif sl == "Insurance":
            ctx_ref = _INSURANCE_NATIVE_PERSISTENT_CONTEXT
        elif sl == "CPAInsurance":
            ctx_ref = _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
        elif sl == "Vahan":
            ctx_ref = _VAHAN_NATIVE_PERSISTENT_CONTEXT
        else:
            return None, ""

        if ctx_ref is not None:
            try:
                _ = ctx_ref.pages
            except Exception:
                if sl == "DMS":
                    _DMS_NATIVE_PERSISTENT_CONTEXT = None
                elif sl == "Insurance":
                    _INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
                elif sl == "CPAInsurance":
                    _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT = None
                elif sl == "Vahan":
                    _VAHAN_NATIVE_PERSISTENT_CONTEXT = None
                ctx_ref = None

        if sl == "DMS" and _DMS_NATIVE_PERSISTENT_CONTEXT is None:
            _dms_phase(
                "launch_persistent_context_begin",
                site_label=sl,
                profile=str(profile_dir),
            )
            _DMS_NATIVE_PERSISTENT_CONTEXT = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=args,
                ignore_default_args=_native_persistent_ignore_default_args(),
            )
            logger.info(
                "handle_browser_opening: launched Playwright Chromium (persistent) profile=%s",
                profile_dir,
            )
            fill_dms_phase("launch_persistent_context_done", profile=str(profile_dir))
        elif sl == "Insurance" and _INSURANCE_NATIVE_PERSISTENT_CONTEXT is None:
            # Generate Insurance / MISP: installed Microsoft Edge (not bundled Chromium) for vendor
            # bot / API parity with a typical dealer Windows default browser.
            _INSURANCE_NATIVE_PERSISTENT_CONTEXT = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel="msedge",
                args=args,
                ignore_default_args=_native_persistent_ignore_default_args(),
            )
            logger.info(
                "handle_browser_opening: launched Microsoft Edge (Playwright channel) Insurance profile=%s",
                profile_dir,
            )
        elif sl == "CPAInsurance" and _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT is None:
            _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=args,
                ignore_default_args=_native_persistent_ignore_default_args(),
            )
            logger.info(
                "handle_browser_opening: launched Playwright Chromium CPAInsurance profile=%s",
                profile_dir,
            )
        elif sl == "Vahan" and _VAHAN_NATIVE_PERSISTENT_CONTEXT is None:
            _VAHAN_NATIVE_PERSISTENT_CONTEXT = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=args,
                ignore_default_args=_native_persistent_ignore_default_args(),
            )
            logger.info(
                "handle_browser_opening: launched Playwright Chromium Vahan profile=%s",
                profile_dir,
            )

        if sl == "DMS":
            ctx = _DMS_NATIVE_PERSISTENT_CONTEXT
        elif sl == "Insurance":
            ctx = _INSURANCE_NATIVE_PERSISTENT_CONTEXT
        elif sl == "CPAInsurance":
            ctx = _CPA_INSURANCE_NATIVE_PERSISTENT_CONTEXT
        elif sl == "Vahan":
            ctx = _VAHAN_NATIVE_PERSISTENT_CONTEXT
        else:
            return None, ""
        native_launch_tag = "microsoft-edge" if sl == "Insurance" else "playwright-chromium"
        matched = None
        for p in list(ctx.pages):
            try:
                u = (p.url or "").strip()
            except Exception:
                u = ""
            if u and _page_matches_site_for_reuse(u, ot, site_label):
                matched = p
                break
        if matched is not None:
            _dms_phase("launch_native_reuse_tab", site_label=sl, url=(ot or "")[:80])
            _try_cdp_maximize_browser_window(matched)
            return matched, native_launch_tag
        if ctx.pages:
            pg0 = ctx.pages[0]
        else:
            pg0 = ctx.new_page()
        goto_wait = "commit" if launch_background else "domcontentloaded"
        _dms_phase("goto_begin", site_label=sl, wait_until=goto_wait, target=(ot or "")[:80])
        if not launch_background:
            try:
                pg0.goto(ot, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                logger.warning("handle_browser_opening: native %s goto failed: %s", sl, exc)
        else:
            try:
                pg0.goto(ot, wait_until="commit", timeout=30_000)
            except Exception:
                try:
                    pg0.goto(ot, wait_until="domcontentloaded", timeout=15_000)
                except Exception as exc2:
                    logger.debug("handle_browser_opening: native %s warm goto: %s", sl, exc2)
        _try_cdp_maximize_browser_window(pg0)
        # Immediately after goto + maximize, focus the page to move away from omnibox.
        _try_cdp_focus_page(pg0)
        _try_js_click_body_for_focus(pg0)
        _dms_phase("goto_done", site_label=sl)
        return pg0, native_launch_tag
    except Exception as exc:
        logger.warning(
            "handle_browser_opening: native %s persistent launch failed: %s", sl, exc
        )
        return None, ""


def _launch_native_dms_persistent_context(
    open_target: str, *, launch_background: bool
) -> tuple[object | None, str]:
    """Launch or reuse Playwright-bundled Chromium with ``launch_persistent_context`` (DMS)."""
    return _launch_native_site_persistent_context(
        open_target,
        launch_background=launch_background,
        site_label="DMS",
        profile_dir=_playwright_chromium_profile_dir(),
    )


def _launch_native_insurance_persistent_context(
    open_target: str, *, launch_background: bool
) -> tuple[object | None, str]:
    """Launch or reuse **Microsoft Edge** (Playwright ``channel='msedge'``) for MISP (Insurance); separate profile from DMS."""
    return _launch_native_site_persistent_context(
        open_target,
        launch_background=launch_background,
        site_label="Insurance",
        profile_dir=_playwright_chromium_insurance_profile_dir(),
    )


def _launch_native_cpa_insurance_persistent_context(
    open_target: str, *, launch_background: bool
) -> tuple[object | None, str]:
    """Launch or reuse Playwright Chromium for CPA insurer portals (Alliance-style), separate from MISP/DMS."""
    return _launch_native_site_persistent_context(
        open_target,
        launch_background=launch_background,
        site_label="CPAInsurance",
        profile_dir=_playwright_chromium_cpa_insurance_profile_dir(),
    )


def _launch_native_vahan_persistent_context(
    open_target: str, *, launch_background: bool
) -> tuple[object | None, str]:
    """Launch or reuse Playwright-bundled Chromium for Vahan, separate profile from DMS and Insurance."""
    return _launch_native_site_persistent_context(
        open_target,
        launch_background=launch_background,
        site_label="Vahan",
        profile_dir=_playwright_chromium_vahan_profile_dir(),
    )


def _navigate_existing_tab_to_site(target_url: str, site_label: str = ""):
    """Navigate an existing tab in a connected CDP browser to ``target_url``.

    For **Insurance** and **CPAInsurance**, never navigates a Siebel/DMS tab to the portal host —
    those tabs are skipped (same rules as :func:`find_open_site_page`). If every open tab is
    DMS-only, opens a **new** tab in the same context instead of hijacking DMS.

    For other ``site_label`` values, the first tab is still navigated in-place (Vahan, etc.).

    Returns the ``Page`` or ``None`` if no CDP browser / tab is available.
    """
    if _use_native_pw_chromium_for_site(site_label):
        nav_native = _navigate_native_site_persistent_to(target_url, site_label)
        if nav_native is not None:
            return nav_native
        return None
    _refresh_cdp_browsers()
    browsers = list(_CDP_BROWSERS_BY_URL.values()) + list(_KEEP_OPEN_BROWSERS)
    want_skip_siebel = _host_matched_portal_skips_dms_siebel(site_label)

    for browser in browsers:
        try:
            for ctx in browser.contexts:
                pages = list(ctx.pages)
                if not pages:
                    continue

                if want_skip_siebel:
                    navigable: list = []
                    for page in pages:
                        try:
                            u = (page.url or "").strip()
                        except Exception:
                            u = ""
                        if _url_looks_like_dms_siebel_tab(u):
                            logger.info(
                                "handle_browser_opening: skip DMS/Siebel tab for %s navigate — %s",
                                (site_label or "").strip() or "portal",
                                u[:120],
                            )
                            continue
                        navigable.append(page)

                    for page in navigable:
                        try:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
                            logger.info(
                                "handle_browser_opening: navigated non-DMS tab to %s URL",
                                (site_label or "").strip() or "portal",
                            )
                            return page
                        except Exception as exc:
                            logger.debug(
                                "handle_browser_opening: navigate %s tab failed: %s",
                                (site_label or "").strip() or "portal",
                                exc,
                            )
                            continue
                    reuse = _find_portal_page_in_cdp_pages(pages, target_url, site_label)
                    if reuse is not None:
                        try:
                            reuse.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
                            logger.info(
                                "handle_browser_opening: reused existing %s hostname tab for navigate (no new_page).",
                                (site_label or "").strip() or "portal",
                            )
                            return reuse
                        except Exception as exc:
                            logger.debug(
                                "handle_browser_opening: reuse goto %s failed: %s",
                                (site_label or "").strip() or "portal",
                                exc,
                            )
                    try:
                        pg_new = ctx.new_page()
                        pg_new.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
                        logger.info(
                            "handle_browser_opening: opened new %s tab (skipped DMS-only or failed reuses).",
                            (site_label or "").strip() or "portal",
                        )
                        return pg_new
                    except Exception as exc:
                        logger.debug(
                            "handle_browser_opening: new_page/goto %s failed: %s",
                            (site_label or "").strip() or "portal",
                            exc,
                        )
                        continue

                for page in pages:
                    try:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
                        logger.info(
                            "handle_browser_opening: navigated existing tab to %s (no new tab/window)",
                            site_label,
                        )
                        return page
                    except Exception as exc:
                        logger.debug(
                            "handle_browser_opening: navigate existing tab failed: %s", exc
                        )
                        continue
        except Exception:
            continue
    return None


def _login_gate_after_open(
    page,
    site_label: str,
    *,
    require_login_on_open: bool,
    login_user: str | None,
    login_password: str | None,
    playwright_dms_execution_log_path: Path | str | None = None,
):
    """Prefilled click, else DMS env/cache fill+submit, else wait for manual login (long for DMS)."""
    sl = (site_label or "").strip()
    if not require_login_on_open:
        return page, None
    _dms_phase("login_gate_begin", site_label=sl)
    _logp: Path | None = None
    if playwright_dms_execution_log_path and (site_label or "").strip() == "DMS":
        try:
            _logp = Path(str(playwright_dms_execution_log_path))
            _install_dms_login_window_capture(page, _logp)
        except Exception as exc:
            logger.debug("handle_browser_opening: login capture install skipped: %s", exc)
            _logp = Path(str(playwright_dms_execution_log_path))
    try:
        try:
            if page.is_closed():
                _clear_native_persistent_context_for_site(
                    (site_label or "").strip(), "page already closed at login gate"
                )
                return None, _dms_browser_closed_operator_message(site_label)
        except Exception:
            pass
        if sl == "DMS":
            try:
                if _is_ready_after_login_page(page):
                    logger.info(
                        "handle_browser_opening: DMS already past login — continuing automation on this tab."
                    )
                    fill_dms_phase("login_gate_done", already_logged_in=True)
                    return page, None
            except Exception as e:
                if _is_browser_disconnected_error(e):
                    _clear_native_persistent_context_for_site(
                        (site_label or "").strip(), "disconnect while checking DMS already-logged-in"
                    )
                    return None, _dms_browser_closed_operator_message(site_label)
                raise
            _dms_steal_focus_from_omnibox_for_login(page)
        if _try_auto_login_if_prefilled(page, log_path=_logp):
            _dms_phase("login_gate_done", site_label=sl, prefilled_auto_login=True)
            return page, None
        if sl == "DMS":
            eu, ep = _effective_dms_login_creds(login_user, login_password)
            if eu and ep:
                if _try_fill_siebel_login_and_submit(page, eu, ep, log_path=_logp):
                    fill_dms_phase("login_gate_done", auto_submit=True)
                    return page, None
                logger.warning(
                    "handle_browser_opening: DMS automatic login did not complete (fields not found or "
                    "submit did not leave the login URL). Set DMS_LOGIN_USER and DMS_LOGIN_PASSWORD in the "
                    "sidecar .env, or log in once manually so credentials can be saved to operator_dms_login.json "
                    "next to the Playwright Chromium profile. Native Chromium does not read Edge's "
                    "password-manager / Windows Hello flow."
                )
            else:
                logger.info(
                    "handle_browser_opening: no DMS_LOGIN_USER/PASSWORD and no operator_dms_login.json — "
                    "waiting for manual Siebel login (up to DMS_LOGIN_MANUAL_WAIT_MS)."
                )
        out = _wait_login_or_prompt_after_open(page, site_label, log_path=_logp)
        if sl == "DMS":
            fill_dms_phase(
                "login_gate_done",
                manual_wait=True,
                ok=out[0] is not None,
                error=(out[1] or "")[:80],
            )
        return out
    finally:
        if _logp is not None:
            try:
                _remove_dms_login_window_capture(page)
            except Exception:
                pass


def get_or_open_site_page(
    base_url: str,
    site_label: str,
    *,
    require_login_on_open: bool = True,
    launch_url: str | None = None,
    launch_background: bool = False,
    login_user: str | None = None,
    login_password: str | None = None,
    playwright_dms_execution_log_path: Path | str | None = None,
):
    """
    Try finding an already-open site tab (``base_url`` is used for host/path matching).
    If not found, open a managed browser to ``launch_url`` or ``base_url``, then optional auto-login.

    **Persistence priority** (avoids re-login / session invalidation):
    1. Reuse an existing matching tab (session intact — zero network hits).
    2. Navigate an existing tab in the connected browser to the target URL
       (same profile & cookies — session cookie may keep the login alive).
    3. Launch a new browser process with ``base_url`` on the command line
       (stable profile dir preserves cookies from prior runs).

    No step ever opens a **second tab** to the same site; single-session portals
    like Vahan would invalidate the running session.

    ``launch_background`` (Windows): start edge/chrome or native Chromium **maximized** without
    activating the window so the operator SPA keeps focus (warm-browser for DMS/CPA/Vahan native,
    Insurance, and other CDP paths). When true, the
    **navigate** step (``_navigate_existing_tab_to_site``) is **skipped** because ``page.goto`` on a
    connected CDP browser often steals focus; warm-browser may then open a second warm process
    in edge cases (acceptable trade for silent warm). Vahan and other flows use the default
    ``launch_background=False`` and keep the navigate step.

    ``login_user`` / ``login_password`` (Siebel DMS): passed through to the login gate for
    ``site_label="DMS"`` — non-``demo`` values fill and submit; otherwise a profile JSON cache
    (written after a successful automated login or periodically while waiting on the login form)
    is used.

    ``playwright_dms_execution_log_path``: when set (Fill DMS), **login-phase** JS dialogs, popup pages,
    ``#statusBar`` text, and PNG screenshots are appended under the same ``ocr_output/.../Playwright_DMS_*.txt``
    tree as the main automation trace.
    """
    sl = (site_label or "").strip()
    if _use_native_pw_chromium_for_site(site_label):
        _refresh_native_context_liveness_for_site(site_label)
        if sl == "DMS":
            ctx_before = "alive" if _DMS_NATIVE_PERSISTENT_CONTEXT is not None else "none"
            _dms_phase("native_liveness_refresh", site_label=sl, context_before=ctx_before)
    page = find_open_site_page(base_url, site_label=site_label)
    if page is not None:
        _dms_phase("find_open_tab", site_label=sl, result="hit")
        return _login_gate_after_open(
            page,
            site_label,
            require_login_on_open=require_login_on_open,
            login_user=login_user,
            login_password=login_password,
            playwright_dms_execution_log_path=playwright_dms_execution_log_path,
        )

    _dms_phase("find_open_tab", site_label=sl, result="miss")
    open_target = (launch_url or base_url or "").strip()

    if not launch_background:
        nav_page = _navigate_existing_tab_to_site(open_target, site_label)
        if nav_page is not None:
            _dms_phase("navigate_existing_tab", site_label=sl, result="hit")
            return _login_gate_after_open(
                nav_page,
                site_label,
                require_login_on_open=require_login_on_open,
                login_user=login_user,
                login_password=login_password,
                playwright_dms_execution_log_path=playwright_dms_execution_log_path,
            )
        _dms_phase("navigate_existing_tab", site_label=sl, result="miss")

    if _use_native_pw_chromium_for_site(site_label):
        sl_open = (site_label or "").strip()
        if sl_open == "CPAInsurance":
            opened_page, channel = _launch_native_cpa_insurance_persistent_context(
                open_target, launch_background=launch_background
            )
        elif sl_open == "Vahan":
            opened_page, channel = _launch_native_vahan_persistent_context(
                open_target, launch_background=launch_background
            )
        elif sl_open == "DMS":
            opened_page, channel = _launch_native_dms_persistent_context(
                open_target, launch_background=launch_background
            )
        else:
            opened_page, channel = None, ""
        if opened_page is not None:
            return _login_gate_after_open(
                opened_page,
                site_label,
                require_login_on_open=require_login_on_open,
                login_user=login_user,
                login_password=login_password,
                playwright_dms_execution_log_path=playwright_dms_execution_log_path,
            )
        return None, (
            f"{site_label} site could not be opened in Playwright Chromium. "
            "Check disk space, run ``playwright install chromium`` from the backend venv, and retry."
        )

    opened_page, channel = _launch_managed_browser_for_site(
        open_target, launch_background=launch_background, site_label=site_label
    )
    if opened_page is not None:
        return _login_gate_after_open(
            opened_page,
            site_label,
            require_login_on_open=require_login_on_open,
            login_user=login_user,
            login_password=login_password,
            playwright_dms_execution_log_path=playwright_dms_execution_log_path,
        )

    return None, (
        f"{site_label} site not open. Please open {site_label} site and keep it logged in. "
        "Start Edge or Chrome with a remote debugging port (for example 9222), or allow the app "
        "to auto-open one and retry."
    )


def find_open_site_page(base_url: str, site_label: str = ""):
    """Find an already-open tab for the given site base URL (CDP or same-process Playwright launch)."""
    if not (base_url or "").strip():
        return None
    if _use_native_pw_chromium_for_site(site_label):
        pg = _find_matching_page_on_native_site_context(base_url, site_label)
        if pg is not None:
            logger.info(
                "handle_browser_opening: reusing native portal tab for %s base_url=%r",
                (site_label or "").strip() or "site",
                (base_url or "")[:120],
            )
            return pg
        return None
    _refresh_cdp_browsers()
    browsers_to_scan = list(_KEEP_OPEN_BROWSERS) + list(_CDP_BROWSERS_BY_URL.values())
    if not browsers_to_scan:
        logger.warning(
            "handle_browser_opening: no browser session for tab reuse — Playwright cannot see a normal Edge/Chrome window. "
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
                    if _page_matches_site_for_reuse(url, base_url, site_label):
                        if _host_matched_portal_skips_dms_siebel(site_label) and _url_looks_like_dms_siebel_tab(url):
                            logger.info(
                                "handle_browser_opening: skipping Siebel/DMS tab when matching %s — %s",
                                (site_label or "").strip() or "portal",
                                url[:120],
                            )
                            continue
                        logger.info("handle_browser_opening: reusing open tab for base URL: %s", url[:140])
                        return page
        except Exception:
            continue

    logger.warning(
        "handle_browser_opening: no tab matched base_url=%r among %s session(s). Open tab URLs (sample): %s",
        (base_url or "")[:100],
        len(browsers_to_scan),
        sample_urls[:8] or "(none readable)",
    )
    return None
