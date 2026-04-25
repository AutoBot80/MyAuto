"""
Shared Playwright browser lifecycle: CDP attach to existing Edge/Chrome, or launch managed browser,
tab matching by site base URL, optional auto-login when credentials are pre-filled.

Used by Fill DMS, Vahan, and Insurance — independent of Siebel/DMS business logic.

**Browser persistence policy:** The browser is launched as an independent OS process with a *stable*
user-data-dir (``<project>/.browser-profile``) and ``--remote-debugging-port``.  It survives backend
restarts, retries, and frontend reloads.  On next startup the CDP reconnection logic in
``_refresh_cdp_browsers`` re-attaches to the same process (session cookies, Vahan login,
captcha/OTP state all survive).  No code path calls ``Browser.close()`` for operator sessions.

**No new tabs:** For single-session portals like Vahan, a second tab to the same host would
invalidate the running session.  ``get_or_open_site_page`` never opens an extra tab — it
reuses a matching tab, or navigates an existing tab in-place, or (only when no browser exists)
launches a fresh process with exactly one tab.
"""
from __future__ import annotations

import atexit
import concurrent.futures
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
from playwright.sync_api import sync_playwright

from app.config import DMS_PLAYWRIGHT_HEADED, PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


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

_PW = None
_PW_THREAD_ID: int | None = None
_KEEP_OPEN_BROWSERS: list = []
_CDP_BROWSERS_BY_URL: dict[str, object] = {}
_RETAINED_BROWSERS_NO_CLOSE: list = []
_CDP_REFRESH_LOCK = threading.Lock()
_LAST_CDP_REFRESH_MONO: float = 0.0
_CDP_REFRESH_TTL_SEC = 2.0
_CDP_PROBE_TIMEOUT_SEC = 0.35


def _retain_browsers_without_closing() -> None:
    for b in list(_KEEP_OPEN_BROWSERS):
        _RETAINED_BROWSERS_NO_CLOSE.append(b)
    _KEEP_OPEN_BROWSERS.clear()
    for b in list(_CDP_BROWSERS_BY_URL.values()):
        _RETAINED_BROWSERS_NO_CLOSE.append(b)
    _CDP_BROWSERS_BY_URL.clear()


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
        _PW = sync_playwright().start()
        _PW_THREAD_ID = current_thread_id
    return _PW


@atexit.register
def _preserve_browsers_on_process_exit() -> None:
    return


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


def _ordered_cdp_urls_for_connect(candidates: list[str], reachable: dict[str, bool]) -> list[str]:
    """Prefer URLs that responded to HTTP probe (parallel), then try the rest in original order."""
    yes = [u for u in candidates if reachable.get(u) is True]
    no = [u for u in candidates if reachable.get(u) is not True]
    return yes + no


def _refresh_cdp_browsers() -> None:
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
        cmd = [exe, "--new-window", "--start-minimized", target]
        creation_flags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creation_flags |= subprocess.DETACHED_PROCESS
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
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


def _launch_managed_browser_for_site(
    base_url: str, *, launch_background: bool = False, site_label: str = ""
):
    pw = _get_playwright()
    headless = not bool(DMS_PLAYWRIGHT_HEADED)
    port = PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT or 9333
    cdp_url = f"http://127.0.0.1:{port}"

    exe, channel = _find_browser_exe()
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
            if launch_background:
                # Stronger hint for Chromium to keep startup non-intrusive.
                cmd.append("--start-minimized")
            cmd.append(base_url)
            creation_flags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creation_flags |= subprocess.DETACHED_PROCESS
            startupinfo = None
            # Warm-browser: start minimized-no-activate on Windows so the Electron SPA keeps focus.
            # SW_SHOWMINNOACTIVE (7) avoids the grey/unresponsive window that SW_MINIMIZE (6) can cause
            # when combined with DETACHED_PROCESS and CDP attachment.
            if launch_background and os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                startupinfo=startupinfo,
            )
            logger.info("handle_browser_opening: launched %s independently (port %s)", channel, port)
            _cdp_t0 = time.monotonic()
            _cdp_deadline = _cdp_t0 + 8.0
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
                        if (site_label or "").strip() == "Insurance" and _url_looks_like_dms_siebel_tab(
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
                        return matched, channel
                    # Edge was started with `base_url` on the CLI — often one tab whose URL is still
                    # blank while CDP attaches. Do not open a second tab (previous behavior).
                    if len(existing) == 1:
                        pg0 = existing[0]
                        try:
                            pg0.wait_for_load_state("domcontentloaded", timeout=10_000)
                        except Exception:
                            pass
                        try:
                            u0 = (pg0.url or "").strip()
                        except Exception:
                            u0 = ""
                        if (site_label or "").strip() == "Insurance" and _url_looks_like_dms_siebel_tab(
                            u0
                        ):
                            logger.info(
                                "handle_browser_opening: sole CDP tab is Siebel/DMS — opening a new tab for Insurance instead of reusing DMS (base=%s).",
                                (base_url or "")[:120],
                            )
                            try:
                                ctx0 = browser.contexts[0] if browser.contexts else browser.new_context()
                                pg_new = ctx0.new_page()
                                pg_new.goto(base_url, wait_until="domcontentloaded", timeout=20_000)
                                return pg_new, channel
                            except Exception as exc:
                                logger.warning(
                                    "handle_browser_opening: could not open Insurance tab while DMS-only tab was present: %s",
                                    exc,
                                )
                                continue
                        logger.info(
                            "handle_browser_opening: reusing single tab from independent %s launch (avoid duplicate insurance/DMS tab).",
                            channel,
                        )
                        return pg0, channel
                    if len(existing) > 1:
                        want_host = _hostname_for_site_match(base_url)
                        for page in existing:
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=3500)
                            except Exception:
                                pass
                            u = (page.url or "").strip()
                            if u and _page_matches_site_for_reuse(u, base_url, site_label):
                                return page, channel
                        if want_host:
                            for page in existing:
                                try:
                                    u = (page.url or "").strip()
                                    if u and _hostname_for_site_match(u) == want_host:
                                        return page, channel
                                except Exception:
                                    continue
                    if launch_background:
                        # In silent warm mode, avoid creating/navigating tabs via CDP here because
                        # those operations can foreground an existing browser window on Windows.
                        continue
                    try:
                        if browser.contexts:
                            ctx0 = browser.contexts[0]
                        else:
                            ctx0 = browser.new_context()
                        page = ctx0.new_page()
                        page.goto(base_url, wait_until="domcontentloaded", timeout=5000)
                        logger.info(
                            "handle_browser_opening: opened target URL in independent %s window via CDP at %s",
                            channel,
                            cdp_url,
                        )
                        return page, channel
                    except Exception:
                        continue
                except Exception:
                    pass
                time.sleep(0.05)
            logger.warning(
                "handle_browser_opening: launched %s but could not connect via CDP at %s within ~8s — "
                "falling back to Playwright-managed launch (Create Invoice still works; session is non-persistent).",
                channel,
                cdp_url,
            )
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
            return page, ch
        except Exception as exc:
            logger.warning("handle_browser_opening: failed to launch %s browser: %s", ch, exc)
            continue
    return None, None


def _page_matches_site_for_reuse(page_url: str, site_base_url: str, site_label: str) -> bool:
    """Match an open browser tab to the configured site URL.

    **Vahan** and **Insurance** use hostname-only matching so tabs still match after login
    (paths move away from ``login.xhtml`` / partner-login into SPAs like ``/ekycpage``). Other
    sites keep path-prefix matching via :func:`_playwright_page_url_matches_site_base`.
    """
    sl = (site_label or "").strip()
    if sl == "Vahan":
        ph = _hostname_for_site_match(page_url)
        bh = _hostname_for_site_match(site_base_url)
        return bool(ph and bh and ph == bh)
    if sl == "Insurance":
        low = (page_url or "").strip().lower()
        if "blank" in low or low.startswith("chrome://") or low.startswith("edge://") or low.startswith("about:"):
            return False
        if _url_looks_like_dms_siebel_tab(page_url):
            return False
        ph = _hostname_for_site_match(page_url)
        bh = _hostname_for_site_match(site_base_url)
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


def _try_auto_login_if_prefilled(page) -> bool:
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

    _detect_js = """() => {
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
        if (el && vis(el)) { passFound = true; if ((el.value || '').trim().length > 0) { passEl = el; break; } }
      }
      if (!userFound && !passFound) return {status: 'no_form'};
      if (!userEl || !passEl) return {status: 'not_prefilled', userFound, passFound,
        userValue: userEl ? userEl.value.trim().substring(0,40) : '',
        passHasValue: passEl ? (passEl.value||'').length > 0 : false};
      return {status: 'prefilled', user: userEl.value.trim().substring(0, 40), hasPass: true};
    }"""

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

    prefilled = None
    for _attempt in range(3):
        if _attempt > 0:
            time.sleep(0.25)
        try:
            prefilled = page.evaluate(_detect_js)
        except Exception:
            prefilled = None
        if prefilled and prefilled.get("status") == "prefilled":
            break
        if prefilled and prefilled.get("status") == "no_form":
            break

    if prefilled and prefilled.get("status") == "no_form" and not _is_login_url():
        logger.info("handle_browser_opening: no login form detected; continuing with existing logged-in session.")
        return True

    if not prefilled or prefilled.get("status") != "prefilled":
        return False
    logger.info(
        "handle_browser_opening: login form has pre-filled credentials (user=%s) — clicking Login",
        prefilled.get("user", "?"),
    )
    login_clicked = False
    for sel in (
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
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=200):
                loc.click(timeout=3000)
                login_clicked = True
                break
        except Exception:
            continue
    if not login_clicked:
        try:
            page.keyboard.press("Enter")
            login_clicked = True
        except Exception:
            pass
    if not login_clicked:
        try:
            login_clicked = bool(
                page.evaluate(
                    """() => {
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
                )
            )
        except Exception:
            pass
    if login_clicked:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        for _ in range(16):
            if not _is_login_url():
                logger.info("handle_browser_opening: auto-login submitted; page URL now: %s", (page.url or "")[:120])
                return True
            time.sleep(0.25)
        logger.warning(
            "handle_browser_opening: login submit attempted but still on login URL: %s", (page.url or "")[:120]
        )
        return False
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


def _is_ready_after_login_page(pg) -> bool:
    try:
        u = (pg.url or "").lower()
        on_siebel_login = "swecmd=login" in u
        on_misp_login = "misp-partner-login" in u
        on_path_login = u.rstrip("/").endswith("/login")
        if not on_siebel_login and not on_misp_login and not on_path_login:
            return True
        return not bool(pg.evaluate(_login_form_visible_eval_js()))
    except Exception:
        return False


def _wait_login_or_prompt_after_open(page, site_label: str):
    """
    After opening or reusing a tab, wait briefly for session/login to clear.
    Returns ``(page, None)`` when ready, or ``(None, operator_message)`` when login is still required.
    """
    for _ in range(3):
        if _is_ready_after_login_page(page):
            logger.info("handle_browser_opening: login/session became ready in same request.")
            return page, None
        time.sleep(0.5)

    try:
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


def _navigate_existing_tab_to_site(target_url: str, site_label: str = ""):
    """Navigate an existing tab in a connected CDP browser to ``target_url``.

    For **Insurance**, never navigates a Siebel/DMS tab to MISP — those tabs are skipped
    (same rules as :func:`find_open_site_page`). If every open tab is DMS-only, opens a
    **new** tab in the same context instead of hijacking DMS.

    For other ``site_label`` values, the first tab is still navigated in-place (Vahan, etc.).

    Returns the ``Page`` or ``None`` if no CDP browser / tab is available.
    """
    _refresh_cdp_browsers()
    browsers = list(_CDP_BROWSERS_BY_URL.values()) + list(_KEEP_OPEN_BROWSERS)
    want_insurance = (site_label or "").strip() == "Insurance"

    for browser in browsers:
        try:
            for ctx in browser.contexts:
                pages = list(ctx.pages)
                if not pages:
                    continue

                if want_insurance:
                    navigable: list = []
                    for page in pages:
                        try:
                            u = (page.url or "").strip()
                        except Exception:
                            u = ""
                        if _url_looks_like_dms_siebel_tab(u):
                            logger.info(
                                "handle_browser_opening: skip DMS/Siebel tab for Insurance navigate — %s",
                                u[:120],
                            )
                            continue
                        navigable.append(page)

                    for page in navigable:
                        try:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
                            logger.info(
                                "handle_browser_opening: navigated non-DMS tab to Insurance URL",
                            )
                            return page
                        except Exception as exc:
                            logger.debug(
                                "handle_browser_opening: navigate Insurance tab failed: %s", exc
                            )
                            continue
                    try:
                        pg_new = ctx.new_page()
                        pg_new.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
                        logger.info(
                            "handle_browser_opening: opened new Insurance tab (skipped DMS-only or failed reuses).",
                        )
                        return pg_new
                    except Exception as exc:
                        logger.debug(
                            "handle_browser_opening: new_page/goto Insurance failed: %s", exc
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


def get_or_open_site_page(
    base_url: str,
    site_label: str,
    *,
    require_login_on_open: bool = True,
    launch_url: str | None = None,
    launch_background: bool = False,
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

    ``launch_background`` (Windows): start the independently launched edge/chrome **minimized** so the
    operator SPA is less likely to lose focus (used for DMS warm-browser only).
    """
    page = find_open_site_page(base_url, site_label=site_label)
    if page is not None:
        if not require_login_on_open:
            return page, None
        auto_login_ok = _try_auto_login_if_prefilled(page)
        if auto_login_ok:
            return page, None
        return _wait_login_or_prompt_after_open(page, site_label)

    open_target = (launch_url or base_url or "").strip()

    nav_page = _navigate_existing_tab_to_site(open_target, site_label)
    if nav_page is not None:
        if not require_login_on_open:
            return nav_page, None
        auto_login_ok = _try_auto_login_if_prefilled(nav_page)
        if auto_login_ok:
            return nav_page, None
        return _wait_login_or_prompt_after_open(nav_page, site_label)

    opened_page, channel = _launch_managed_browser_for_site(
        open_target, launch_background=launch_background, site_label=site_label
    )
    if opened_page is not None:
        if not require_login_on_open:
            return opened_page, None
        auto_login_ok = _try_auto_login_if_prefilled(opened_page)
        if auto_login_ok:
            return opened_page, None
        return _wait_login_or_prompt_after_open(opened_page, site_label)

    return None, (
        f"{site_label} site not open. Please open {site_label} site and keep it logged in. "
        "Start Edge or Chrome with a remote debugging port (for example 9222), or allow the app "
        "to auto-open one and retry."
    )


def find_open_site_page(base_url: str, site_label: str = ""):
    """Find an already-open tab for the given site base URL (CDP or same-process Playwright launch)."""
    if not (base_url or "").strip():
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
                        if (site_label or "").strip() == "Insurance" and _url_looks_like_dms_siebel_tab(url):
                            logger.info(
                                "handle_browser_opening: skipping Siebel/DMS tab when matching Insurance — %s",
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
