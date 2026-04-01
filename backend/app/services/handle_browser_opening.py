"""
Shared Playwright browser lifecycle: CDP attach to existing Edge/Chrome, or launch managed browser,
tab matching by site base URL, optional auto-login when credentials are pre-filled.

Used by Fill DMS, Vahan, and Insurance — independent of Siebel/DMS business logic.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from playwright.sync_api import sync_playwright

from app.config import DMS_PLAYWRIGHT_HEADED, PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT

logger = logging.getLogger(__name__)


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


def _refresh_cdp_browsers() -> None:
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
            profile_dir = os.path.join(tempfile.gettempdir(), "myautoai-dms-browser-profile")
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
            cmd.append(base_url)
            creation_flags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creation_flags |= subprocess.DETACHED_PROCESS
            startupinfo = None
            # Warm-browser: start minimized on Windows so the SPA keeps keyboard focus (best-effort).
            if launch_background and os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 6  # SW_MINIMIZE
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
                        if not url or not _playwright_page_url_matches_site_base(url, base_url):
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
                            if u and _playwright_page_url_matches_site_base(u, base_url):
                                _agent_debug_browser_ndjson(
                                    "H9",
                                    "handle_browser_opening._launch_managed_browser_for_site",
                                    "independent_launch_matched_multi_tab",
                                    {"existing_count": len(existing)},
                                )
                                return page, channel
                        if want_host:
                            for page in existing:
                                try:
                                    u = (page.url or "").strip()
                                    if u and _hostname_for_site_match(u) == want_host:
                                        return page, channel
                                except Exception:
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
                _elapsed = time.monotonic() - _cdp_t0
                if _elapsed < 1.2:
                    time.sleep(0.06)
                elif _elapsed < 3.5:
                    time.sleep(0.14)
                else:
                    time.sleep(0.28)
            logger.warning(
                "handle_browser_opening: launched %s but could not connect via CDP at %s within ~8s",
                channel,
                cdp_url,
            )
        except Exception as exc:
            logger.warning("handle_browser_opening: independent launch of %s failed: %s", channel, exc)

    if attempted_independent_launch:
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
    for _attempt in range(6):
        try:
            prefilled = page.evaluate(_detect_js)
        except Exception:
            prefilled = None
        if prefilled and prefilled.get("status") == "prefilled":
            break
        if prefilled and prefilled.get("status") == "no_form":
            break
        time.sleep(1)

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
            if loc.count() > 0 and loc.is_visible(timeout=800):
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
    for _ in range(5):
        if _is_ready_after_login_page(page):
            logger.info("handle_browser_opening: login/session became ready in same request.")
            return page, None
        time.sleep(1)

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

    ``launch_background`` (Windows): start the independently launched edge/chrome **minimized** so the
    operator SPA is less likely to lose focus (used for DMS warm-browser only).
    """
    page = find_open_site_page(base_url, site_label=site_label)
    if page is not None:
        if not require_login_on_open:
            return page, None
        # Reused tab may still be on login (e.g. after warm-browser with no auto-login) — run same gate as new tab.
        auto_login_ok = _try_auto_login_if_prefilled(page)
        if auto_login_ok:
            return page, None
        return _wait_login_or_prompt_after_open(page, site_label)

    open_target = (launch_url or base_url or "").strip()
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
                    if _playwright_page_url_matches_site_base(url, base_url):
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
