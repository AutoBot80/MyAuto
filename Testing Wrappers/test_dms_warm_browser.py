"""
Local test wrapper: portal warm-browser timing + optional login/autofill gates.

Logs when warm is triggered, interim state (CDP, native context, tab), when warm returns,
and (with ``--with-login``) whether each portal's login gate succeeds.

Configure ``backend/.env`` (``DMS_BASE_URL``, ``INSURANCE_BASE_URL``, ``ALLIANCE_CPA_PORTAL_URL``, etc.).

Run:
  test_dms_warm_browser.bat                                   # all sites + login mirror (Fill DMS parity)
  test_dms_warm_timing.bat                                    # DMS warm timing only (--warm-only)
  test_portal_warm_login.bat                                  # alias for test_dms_warm_browser.bat
  python test_dms_warm_browser.py --warm-only                 # DMS warm only, no login gate
  python test_dms_warm_browser.py --sites misp --with-login --visible
  python test_dms_warm_browser.py --visible                    # foreground window (easier to see)
  python test_dms_warm_browser.py --cold                       # teardown browsers first, then warm
  python test_dms_warm_browser.py --sidecar                    # job_runner subprocess (Electron parity)

**Not the same thing:** ``--cold`` = teardown before warm. **First Chromium profile launch** on a machine
(can take ~5–10s, no window you notice) is **not** ``--cold`` — your log line
``launched Playwright Chromium (persistent)`` means that first-launch path.

Prod warm keeps the window in the background (``launch_background=True``). Use ``--visible`` if you
did not see a browser. The ~6s ``warm_return`` time is wall-clock while the script blocks, even if
no window is obvious.

**DMS login (``--with-login``):** Same gate as Create Invoice — poll for browser autofill on the Siebel
form, click Login when fields are prefilled, then optional env/operator-cache fill, then manual wait.
Default ``DMS_LOGIN_USER``/``DMS_LOGIN_PASSWORD`` (``demo``/``demo``) are **not** typed into the form.

**CPA note:** ``--with-login`` mirrors production — browser autofill on Alliance login, then **Continue**
click (same pattern as MISP Sign In). Valid session cookies still fast-path without clicking.

Env: ``DMS_WARM_TEST_POLL_SEC``, ``DMS_WARM_TEST_COLD=1``, ``DMS_WARM_TEST_KEEP_OPEN=0``, ``SAATHI_BASE_DIR``.

Compare v0.9.32: ``git checkout v0.9.32``, run the same command, ``git checkout main``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_SITE_IDS = ("dms", "misp", "cpa")
LoginMethod = Literal[
    "session_reuse",
    "browser_autofill",
    "env_credentials",
    "sign_in_click",
    "manual_required",
    "failed",
    "skipped",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
_SIDECAR_SCRIPT = _REPO_ROOT / "electron" / "sidecar" / "job_runner.py"

if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_dms_warm_browser")

DEFAULT_POLL_SEC = 120.0
DEFAULT_POLL_INTERVAL = 0.25


@dataclass
class TimelineEvent:
    name: str
    mono: float
    offset_ms: float
    detail: str = ""


@dataclass
class Timeline:
    t0: float = field(default_factory=time.perf_counter)
    events: list[TimelineEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, name: str, detail: str = "") -> TimelineEvent:
        now = time.perf_counter()
        ev = TimelineEvent(
            name=name,
            mono=now,
            offset_ms=(now - self.t0) * 1000.0,
            detail=detail,
        )
        with self._lock:
            self.events.append(ev)
        line = f"[+{ev.offset_ms:8.1f} ms] {name}"
        if detail:
            line += f"  {detail}"
        logger.info("%s", line)
        return ev

    def has(self, name: str) -> bool:
        with self._lock:
            return any(e.name == name for e in self.events)

    def get(self, name: str) -> TimelineEvent | None:
        with self._lock:
            for e in self.events:
                if e.name == name:
                    return e
        return None

    def ordered(self) -> list[TimelineEvent]:
        with self._lock:
            return sorted(self.events, key=lambda e: e.offset_ms)

    def format_summary(self) -> str:
        lines = ["--- DMS warm-browser timeline ---"]
        for e in self.ordered():
            row = f"  {e.name:20s}  +{e.offset_ms:9.1f} ms"
            if e.detail:
                row += f"  {e.detail}"
            lines.append(row)
        warm = self.get("warm_return")
        tab = self.get("tab_detected")
        if warm and tab:
            gap = tab.offset_ms - warm.offset_ms
            if gap > 50:
                lines.append(
                    f"  NOTE: tab_detected {gap:.0f} ms after warm_return "
                    "(detached launch or slow attach)."
                )
            elif tab.offset_ms < warm.offset_ms:
                lines.append("  NOTE: tab visible before warm_return (reuse / parallel poller).")
        elif warm and warm.detail.startswith("success=True") and not tab:
            lines.append("  NOTE: warm succeeded but no tab within poll window.")
        return "\n".join(lines)


@dataclass
class SiteResult:
    site_id: str
    warm_ok: bool = False
    login_ok: bool = False
    login_method: LoginMethod = "skipped"
    warm_error: str = ""
    login_error: str = ""
    final_url: str = ""
    warm_path: str = ""
    timeline: Timeline = field(default_factory=Timeline)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "warm_ok": self.warm_ok,
            "login_ok": self.login_ok,
            "login_method": self.login_method,
            "warm_error": self.warm_error or None,
            "login_error": self.login_error or None,
            "final_url": self.final_url or None,
            "warm_path": self.warm_path or None,
            "timeline": [
                {"name": e.name, "offset_ms": round(e.offset_ms, 1), "detail": e.detail}
                for e in self.timeline.ordered()
            ],
        }


@dataclass
class SiteSpec:
    site_id: str
    site_label: str
    base_url: str
    sidecar_job_type: str
    sidecar_params_key: str


@dataclass
class PollerState:
    cdp_up: bool = False
    cdp_url: str = ""
    native_alive: bool = False
    native_pages: int = 0
    tab_found: bool = False
    tab_url: str = ""
    siebel: bool = False


class WarmPoller:
    """Poll CDP HTTP while warm runs; Playwright tab probes only on the main thread after warm."""

    def __init__(
        self,
        *,
        dms_base_url: str,
        timeline: Timeline,
        poll_sec: float,
        interval_sec: float,
        stop_event: threading.Event,
    ) -> None:
        self._dms_base_url = dms_base_url
        self._timeline = timeline
        self._poll_sec = poll_sec
        self._interval_sec = interval_sec
        self._stop = stop_event
        self._state = PollerState()
        self._thread: threading.Thread | None = None
        self._playwright_ok = threading.Event()

    def start_cdp_only(self) -> None:
        """Background CDP HTTP probes (no Playwright) during ``warm_dms_browser_session``."""
        self._playwright_ok.clear()
        self._thread = threading.Thread(target=self._run_cdp_only, name="dms-warm-cdp-poller", daemon=True)
        self._thread.start()

    def enable_playwright_probes(self) -> None:
        self._playwright_ok.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def poll_playwright_until_done(self) -> None:
        """Main-thread Playwright tab polling after warm returns."""
        from app.services.handle_browser_opening import (
            _url_looks_like_dms_siebel_tab,
            find_open_site_page,
        )

        deadline = time.perf_counter() + self._poll_sec
        while not self._stop.is_set() and time.perf_counter() < deadline:
            st = PollerState()
            page = find_open_site_page(self._dms_base_url, "DMS")
            if page is not None:
                st.tab_found = True
                try:
                    st.tab_url = ((page.url or "").strip())[:200]
                except Exception:
                    st.tab_url = "(url unreadable)"
                st.siebel = _url_looks_like_dms_siebel_tab(st.tab_url)
            self._emit_transitions(st)
            if st.tab_found and st.siebel:
                return
            time.sleep(self._interval_sec)

    def _run_cdp_only(self) -> None:
        from app.services.handle_browser_opening import (
            _candidate_cdp_urls,
            _cdp_url_http_reachable,
        )

        deadline = time.perf_counter() + self._poll_sec
        while not self._stop.is_set() and time.perf_counter() < deadline:
            if self._playwright_ok.is_set():
                break
            st = PollerState()
            for url in _candidate_cdp_urls():
                if _cdp_url_http_reachable(url):
                    st.cdp_up = True
                    st.cdp_url = url
                    break
            self._emit_transitions(st)
            time.sleep(self._interval_sec)

    def _emit_transitions(self, st: PollerState) -> None:
        prev = self._state

        if st.cdp_up and not prev.cdp_up:
            self._timeline.record("cdp_reachable", st.cdp_url)
        elif not st.cdp_up and prev.cdp_up:
            logger.info("[poller] cdp: up -> down")

        if st.native_alive != prev.native_alive or st.native_pages != prev.native_pages:
            if st.native_alive:
                logger.info("[poller] native_ctx: alive pages=%d", st.native_pages)
            elif prev.native_alive:
                logger.info("[poller] native_ctx: dead")

        if st.tab_found and not prev.tab_found:
            self._timeline.record("tab_detected", st.tab_url or "(empty url)")
        elif st.tab_found and prev.tab_found and st.tab_url != prev.tab_url:
            logger.info("[poller] tab url changed: %s", st.tab_url[:120])

        if st.siebel and not prev.siebel:
            self._timeline.record("siebel_url", st.tab_url[:160])

        self._state = st


def _parse_sites(raw: str) -> list[str]:
    text = (raw or "dms").strip().lower()
    if text in ("all", "*"):
        return list(_SITE_IDS)
    out: list[str] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if part not in _SITE_IDS:
            raise ValueError(f"Unknown site {part!r}; expected one of: {', '.join(_SITE_IDS)}, all")
        if part not in out:
            out.append(part)
    return out or ["dms"]


def _resolve_site_specs(site_ids: list[str]) -> tuple[list[SiteSpec], dict[str, Any]]:
    from app.config import DMS_BASE_URL, INSURANCE_BASE_URL, dms_automation_is_real_siebel
    from app.services.add_alliance_cpa_insurance import _resolve_cpa_portal_url

    config: dict[str, Any] = {}
    specs: list[SiteSpec] = []
    for site_id in site_ids:
        if site_id == "dms":
            url = (DMS_BASE_URL or "").strip()
            if not url:
                raise ValueError("DMS_BASE_URL is not set — add it to backend/.env")
            if not dms_automation_is_real_siebel():
                raise ValueError(
                    "DMS_MODE must be real / siebel / live / production / hero for DMS warm-browser."
                )
            specs.append(
                SiteSpec(
                    site_id="dms",
                    site_label="DMS",
                    base_url=url,
                    sidecar_job_type="warm_browser",
                    sidecar_params_key="dms_base_url",
                )
            )
            config["DMS_BASE_URL"] = url[:120]
            config["DMS_MODE_real_siebel"] = True
        elif site_id == "misp":
            url = (INSURANCE_BASE_URL or "").strip()
            if not url:
                raise ValueError("INSURANCE_BASE_URL is not set — add it to backend/.env")
            specs.append(
                SiteSpec(
                    site_id="misp",
                    site_label="Insurance",
                    base_url=url,
                    sidecar_job_type="warm_insurance",
                    sidecar_params_key="insurance_base_url",
                )
            )
            config["INSURANCE_BASE_URL"] = url[:120]
        elif site_id == "cpa":
            url = _resolve_cpa_portal_url(None)
            if not url:
                raise ValueError(
                    "ALLIANCE_CPA_PORTAL_URL is not set — add it to backend/.env for CPA warm-browser."
                )
            specs.append(
                SiteSpec(
                    site_id="cpa",
                    site_label="CPAInsurance",
                    base_url=url,
                    sidecar_job_type="warm_cpa",
                    sidecar_params_key="cpa_portal_url",
                )
            )
            config["ALLIANCE_CPA_PORTAL_URL"] = url[:120]
    return specs, config


def _print_config_snapshot(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.config import (
        DMS_BASE_URL,
        PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
        USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS,
        dms_automation_is_real_siebel,
    )

    snap = {
        "DMS_BASE_URL": (DMS_BASE_URL or "")[:120],
        "DMS_MODE_real_siebel": dms_automation_is_real_siebel(),
        "USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS": bool(USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS),
        "PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT": PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
        "PLAYWRIGHT_CDP_URL": (os.getenv("PLAYWRIGHT_CDP_URL") or "").strip() or None,
    }
    if extra:
        snap.update(extra)
    logger.info("Config: %s", json.dumps(snap, indent=2))
    return snap


def _run_sidecar_warm(
    *,
    job_type: str,
    params: dict[str, Any],
    saathi_base_dir: str,
) -> dict[str, Any]:
    if not _SIDECAR_SCRIPT.is_file():
        raise FileNotFoundError(f"Sidecar script not found: {_SIDECAR_SCRIPT}")

    payload = {
        "type": job_type,
        "api_url": os.getenv("SAATHI_API_URL", "http://127.0.0.1:8000"),
        "jwt": os.getenv("PRINT_RTO_JWT", ""),
        "saathi_base_dir": saathi_base_dir,
        "params": params,
    }
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_BACKEND)
    env["SAATHI_BASE_DIR"] = saathi_base_dir

    logger.info("Sidecar %s (Electron-like subprocess)", job_type)
    proc = subprocess.run(
        [sys.executable, str(_SIDECAR_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        timeout=600,
    )
    stderr = (proc.stderr or "").strip()
    if stderr:
        for line in stderr.splitlines()[-15:]:
            logger.debug("sidecar stderr: %s", line)

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {"success": False, "error": stderr or f"Sidecar exit {proc.returncode}, no stdout"}

    try:
        out = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid sidecar JSON: {e}"}

    data = out.get("data") if isinstance(out.get("data"), dict) else out
    if isinstance(data, dict) and "success" in data:
        return data
    return {"success": bool(out.get("success")), "error": out.get("error")}


@dataclass
class BrowserSnapshot:
    tab_found: bool = False
    tab_url: str = ""
    native_ctx: bool = False
    native_pages: int = 0
    cdp_up: bool = False
    cdp_url: str = ""


def _snapshot_browser_state(dms_base_url: str) -> BrowserSnapshot:
    return _snapshot_site_state(
        SiteSpec("dms", "DMS", dms_base_url, "warm_browser", "dms_base_url")
    )


def _format_snapshot(snap: BrowserSnapshot) -> str:
    return (
        f"tab={snap.tab_found}"
        f" native_ctx={snap.native_ctx} pages={snap.native_pages}"
        f" cdp={snap.cdp_up}"
        + (f" url={snap.tab_url[:80]}" if snap.tab_url else "")
    )


def _infer_warm_path(
    *,
    pre: BrowserSnapshot,
    post: BrowserSnapshot,
    result: dict[str, Any],
    teardown_cold: bool,
) -> str:
    attach = (result.get("attach") or "").strip()
    if attach:
        return attach
    if not result.get("success"):
        return "failed"
    if pre.tab_found:
        return "reuse_existing_tab"
    if not pre.native_ctx and post.native_ctx:
        return "native_persistent_first_launch" + (" (after --cold teardown)" if teardown_cold else "")
    if pre.native_ctx and post.native_ctx:
        return "native_persistent_reuse"
    if post.tab_found and not post.native_ctx:
        return "detached_edge_or_external_browser"
    return "unknown_success"


def _snapshot_site_state(spec: SiteSpec) -> BrowserSnapshot:
    from app.services.handle_browser_opening import (
        _DMS_NATIVE_PERSISTENT_CONTEXT,
        _candidate_cdp_urls,
        _cdp_url_http_reachable,
        find_open_site_page,
    )

    snap = BrowserSnapshot()
    for url in _candidate_cdp_urls():
        if _cdp_url_http_reachable(url):
            snap.cdp_up = True
            snap.cdp_url = url
            break
    ctx = _DMS_NATIVE_PERSISTENT_CONTEXT
    if ctx is not None:
        try:
            snap.native_pages = len(list(ctx.pages))
            snap.native_ctx = True
        except Exception:
            snap.native_ctx = False
    page = find_open_site_page(spec.base_url, spec.site_label)
    if page is not None:
        snap.tab_found = True
        try:
            snap.tab_url = ((page.url or "").strip())[:200]
        except Exception:
            snap.tab_url = "(unreadable)"
    return snap


def _warm_site_direct(
    spec: SiteSpec,
    *,
    visible: bool,
    had_tab_before: bool = False,
) -> dict[str, Any]:
    if visible:
        from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
        from app.services.handle_browser_opening import (
            _try_cdp_focus_page,
            _try_cdp_maximize_browser_window,
            find_open_site_page,
            get_or_open_site_page,
            launch_site_background_detached,
        )

        page, open_error = get_or_open_site_page(
            spec.base_url,
            spec.site_label,
            require_login_on_open=False,
            launch_background=False,
        )
        if page is None:
            launched = launch_site_background_detached(spec.base_url)
            if launched:
                return {
                    "success": True,
                    "error": None,
                    "attach": "detached_edge_chrome",
                    "detached": True,
                }
            return {
                "success": False,
                "error": open_error or f"Could not open {spec.site_label} browser",
                "detached": False,
            }
        if spec.site_id == "dms":
            _install_playwright_js_dialog_handler(page)
        _try_cdp_maximize_browser_window(page)
        _try_cdp_focus_page(page)
        try:
            url = (page.url or "").strip()[:200]
        except Exception:
            url = ""
        return {
            "success": True,
            "error": None,
            "attach": "reuse_existing_tab" if had_tab_before else "visible_native_open",
            "page_url": url,
        }

    if spec.site_id == "dms":
        from app.services.fill_hero_dms_service import warm_dms_browser_session

        return dict(warm_dms_browser_session(spec.base_url))
    if spec.site_id == "misp":
        from app.services.fill_hero_insurance_service import warm_insurance_browser_session

        return dict(warm_insurance_browser_session(spec.base_url))
    from app.services.add_alliance_cpa_insurance import warm_cpa_browser_session

    return dict(warm_cpa_browser_session(spec.base_url))


def _page_final_url(page) -> str:
    try:
        return ((page.url or "").strip())[:200]
    except Exception:
        return ""


def _mirror_app_open_and_login_site(
    spec: SiteSpec,
    *,
    visible: bool,
    cpa_login_wait_sec: float,
    timeline: Timeline,
) -> tuple[bool, LoginMethod, str, str]:
    """
    Production-parity open + login (same calls as Fill DMS / Generate Insurance / CPA Insurance).
    Returns (login_ok, login_method, error_message, active_page_url).
    """
    from app.services.handle_browser_opening import (
        _is_ready_after_login_page,
        get_or_open_site_page,
    )

    launch_bg = not visible
    timeline.record("mirror_open_begin", spec.site_id)

    if spec.site_id == "dms":
        from app.config import DMS_LOGIN_PASSWORD, DMS_LOGIN_USER
        from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler

        # Same kwargs as run_fill_dms_only / Fill DMS API — login gate is autofill-first; demo/demo ignored.
        timeline.record("get_or_open_begin", "require_login_on_open=True autofill-first (run_fill_dms_only)")
        page, open_error = get_or_open_site_page(
            spec.base_url,
            "DMS",
            require_login_on_open=True,
            login_user=DMS_LOGIN_USER,
            login_password=DMS_LOGIN_PASSWORD,
            launch_background=launch_bg,
        )
        if page is None:
            timeline.record("mirror_open_done", f"failed {open_error or ''}"[:120])
            return False, "failed", open_error or "DMS get_or_open_site_page failed.", ""
        _install_playwright_js_dialog_handler(page)
        try:
            ready = _is_ready_after_login_page(page)
        except Exception:
            ready = False
        if ready:
            u = (DMS_LOGIN_USER or "").strip()
            p = (DMS_LOGIN_PASSWORD or "").strip()
            has_non_demo_env = bool(
                u and p and not (u.lower() == "demo" and p == "demo")
            )
            # Autofill (or manual wait) is the normal path; env fill is only a fallback inside the gate.
            method: LoginMethod = "env_credentials" if has_non_demo_env else "browser_autofill"
            timeline.record("mirror_open_done", method)
            return True, method, "", _page_final_url(page)
        timeline.record("mirror_open_done", "failed not ready")
        return False, "failed", open_error or "DMS open returned a tab but session is not ready.", _page_final_url(page)

    if spec.site_id == "misp":
        from app.config import INSURANCE_ACTION_TIMEOUT_MS
        from app.services.fill_hero_insurance_service import (
            _click_sign_in_if_visible,
            _hero_insurance_log_page_diagnostics,
            _insurance_click_settle,
            _misp_post_sign_in_page,
            _misp_snapshot_context_pages,
            _still_on_heroinsurance_misp_partner_login,
        )

        page, open_error = get_or_open_site_page(
            spec.base_url,
            "Insurance",
            require_login_on_open=False,
            launch_background=launch_bg,
        )
        if page is None:
            timeline.record("mirror_open_done", f"failed {open_error or ''}"[:120])
            return False, "failed", open_error or "Insurance get_or_open_site_page failed.", ""
        page.set_default_timeout(INSURANCE_ACTION_TIMEOUT_MS)
        _insurance_click_settle(page)
        _hero_insurance_log_page_diagnostics(
            page,
            phase="mirror_before_sign_in",
            ocr_output_dir=None,
            subfolder=None,
        )
        timeline.record("sign_in_begin", "run_fill_insurance_only parity")
        pages_before = _misp_snapshot_context_pages(page)
        clicked = _click_sign_in_if_visible(page, timeout_ms=INSURANCE_ACTION_TIMEOUT_MS)
        page = _misp_post_sign_in_page(
            page,
            portal_base_url=spec.base_url,
            pages_before=pages_before,
            timeout_ms=INSURANCE_ACTION_TIMEOUT_MS,
        )
        try:
            on_login = _still_on_heroinsurance_misp_partner_login(page)
        except Exception:
            on_login = True
        if not on_login:
            method: LoginMethod = "sign_in_click" if clicked else "session_reuse"
            timeline.record("mirror_open_done", method)
            return True, method, "", _page_final_url(page)
        timeline.record("mirror_open_done", "failed still on partner login")
        return (
            False,
            "failed",
            "MISP Sign In did not leave partner login — password may not have autofill-filled.",
            _page_final_url(page),
        )

    # CPA — same open + ready wait as add_alliance_cpa_insurance
    from app.services.add_alliance_cpa_insurance import (
        CPA_PORTAL_READY_POLL_MS,
        _is_cpa_portal_ready,
        _still_on_alliance_login,
        _try_alliance_login_autofill_and_continue,
    )

    page, open_error = get_or_open_site_page(
        spec.base_url,
        "CPAInsurance",
        require_login_on_open=False,
        launch_background=launch_bg,
    )
    if page is None:
        timeline.record("mirror_open_done", f"failed {open_error or ''}"[:120])
        return False, "failed", open_error or "CPA get_or_open_site_page failed.", ""

    cpa_mirror_log = Path(__file__).resolve().parent / "playwright_cpa_mirror_last.txt"

    try:
        if _is_cpa_portal_ready(page):
            timeline.record("mirror_open_done", "session_reuse")
            return True, "session_reuse", "", _page_final_url(page)
    except Exception:
        pass

    poll_ms = int(CPA_PORTAL_READY_POLL_MS)
    max_polls = max(1, int(round(cpa_login_wait_sec * 1000.0 / poll_ms)))
    timeline.record("cpa_wait_begin", f"polls={max_polls} interval_ms={poll_ms}")
    saw_login_surface = False
    for attempt in range(1, max_polls + 1):
        try:
            if page.is_closed():
                return False, "failed", "CPA browser tab closed while waiting for login.", _page_final_url(page)
        except Exception:
            return False, "failed", "CPA browser tab closed while waiting for login.", ""
        try:
            if _still_on_alliance_login(page):
                saw_login_surface = True
                _try_alliance_login_autofill_and_continue(page, cpa_mirror_log)
            if _is_cpa_portal_ready(page):
                if saw_login_surface:
                    method: LoginMethod = "browser_autofill"
                elif attempt == 1:
                    method = "session_reuse"
                else:
                    method = "manual_required"
                timeline.record("mirror_open_done", method)
                return True, method, "", _page_final_url(page)
        except Exception as exc:
            logger.debug("CPA readiness probe: %s", exc)
        if attempt < max_polls:
            try:
                page.wait_for_timeout(poll_ms)
            except Exception:
                time.sleep(poll_ms / 1000.0)
    timeline.record("mirror_open_done", "failed timeout")
    return (
        False,
        "failed",
        f"CPA portal still shows a login page after {cpa_login_wait_sec:.0f}s — log in manually and re-run.",
        _page_final_url(page),
    )


def _poll_site_tab_ready(
    spec: SiteSpec,
    timeline: Timeline,
    *,
    poll_sec: float,
    interval_sec: float,
) -> bool:
    from app.services.handle_browser_opening import find_open_site_page

    if spec.site_id == "dms":
        from app.services.handle_browser_opening import _url_looks_like_dms_siebel_tab

        deadline = time.perf_counter() + poll_sec
        while time.perf_counter() < deadline:
            page = find_open_site_page(spec.base_url, spec.site_label)
            if page is not None:
                try:
                    url = ((page.url or "").strip())[:200]
                except Exception:
                    url = ""
                if not timeline.has("tab_detected"):
                    timeline.record("tab_detected", url or "(empty url)")
                if _url_looks_like_dms_siebel_tab(url):
                    if not timeline.has("siebel_url"):
                        timeline.record("siebel_url", url[:160])
                    return True
            time.sleep(interval_sec)
        return timeline.has("tab_detected")

    deadline = time.perf_counter() + poll_sec
    while time.perf_counter() < deadline:
        page = find_open_site_page(spec.base_url, spec.site_label)
        if page is not None:
            try:
                url = ((page.url or "").strip())[:200]
            except Exception:
                url = ""
            timeline.record("tab_detected", url or "(empty url)")
            return True
        time.sleep(interval_sec)
    return False


def _run_site_flow_impl(
    spec: SiteSpec,
    *,
    visible: bool,
    sidecar: bool,
    saathi_base_dir: str,
    cold: bool,
    with_login: bool,
    poll_sec: float,
    poll_interval: float,
    cpa_login_wait_sec: float,
) -> SiteResult:
    from app.services.handle_browser_opening import find_open_site_page

    result = SiteResult(site_id=spec.site_id)
    tl = result.timeline
    logger.info("=== Site %s (%s) ===", spec.site_id, spec.site_label)

    if sidecar and with_login:
        logger.warning(
            "--sidecar ignored with --with-login; using Playwright executor (mirror Fill DMS / app)."
        )
        sidecar = False

    pre_snap = _snapshot_site_state(spec)
    tl.record("pre_warm", _format_snapshot(pre_snap))

    if with_login:
        tl.record(
            "trigger_start",
            f"mirror_app visible={visible} (Fill DMS / Insurance / CPA open+login)",
        )
        login_ok, method, login_err, active_url = _mirror_app_open_and_login_site(
            spec,
            visible=visible,
            cpa_login_wait_sec=cpa_login_wait_sec,
            timeline=tl,
        )
        result.warm_ok = login_ok or bool(find_open_site_page(spec.base_url, spec.site_label))
        result.login_ok = login_ok
        result.login_method = method
        result.login_error = login_err or ""
        result.warm_path = "mirror_app_open_and_login"
        post_snap = _snapshot_site_state(spec)
        tl.record("post_open", _format_snapshot(post_snap))
        if visible:
            _foreground_site_if_possible(spec)
        if active_url:
            result.final_url = active_url
        else:
            page = find_open_site_page(spec.base_url, spec.site_label)
            if page is not None:
                result.final_url = _page_final_url(page)
        if not tl.has("tab_detected") and result.final_url:
            tl.record("tab_detected", result.final_url)
        tl.record("poll_end", "mirror_app (no warm poll)")
        return result

    tl.record(
        "trigger_start",
        f"warm_only mode={'sidecar' if sidecar else 'direct'} cold={cold} visible={visible}",
    )

    warm_result: dict[str, Any]
    try:
        if sidecar:
            params = {spec.sidecar_params_key: spec.base_url}
            warm_result = _run_sidecar_warm(
                job_type=spec.sidecar_job_type,
                params=params,
                saathi_base_dir=saathi_base_dir,
            )
        else:
            warm_result = _warm_site_direct(
                spec,
                visible=visible,
                had_tab_before=pre_snap.tab_found,
            )
    except Exception as exc:
        warm_result = {"success": False, "error": str(exc)}
        logger.exception("Warm call failed for %s", spec.site_id)

    post_snap = _snapshot_site_state(spec)
    warm_path = _infer_warm_path(
        pre=pre_snap,
        post=post_snap,
        result=warm_result,
        teardown_cold=cold,
    )
    tl.record("post_warm", _format_snapshot(post_snap))
    tl.record("warm_path", warm_path)
    logger.info("[%s] Warm path: %s", spec.site_id, warm_path)

    success = bool(warm_result.get("success"))
    err = (warm_result.get("error") or "").strip()
    tl.record("warm_return", f"success={success}" + (f" error={err[:200]}" if err else ""))

    result.warm_ok = success
    result.warm_error = err
    result.warm_path = warm_path
    result.login_ok = success
    result.login_method = "skipped"

    if success and not sidecar and visible:
        _foreground_site_if_possible(spec)
    elif success and not sidecar and not visible and spec.site_id == "dms":
        _foreground_dms_if_possible(spec.base_url)

    tab_ok = _poll_site_tab_ready(spec, tl, poll_sec=poll_sec, interval_sec=poll_interval)
    tl.record("poll_end", f"tab_ok={tab_ok} poll_window={poll_sec:.0f}s")

    page = find_open_site_page(spec.base_url, spec.site_label)
    if page is not None:
        result.final_url = _page_final_url(page)

    return result


def _run_site_flow(
    spec: SiteSpec,
    *,
    visible: bool,
    sidecar: bool,
    saathi_base_dir: str,
    cold: bool,
    with_login: bool,
    poll_sec: float,
    poll_interval: float,
    cpa_login_wait_sec: float,
) -> SiteResult:
    from app.services.playwright_executor import run_playwright_callable_sync

    try:
        return run_playwright_callable_sync(
            lambda: _run_site_flow_impl(
                spec,
                visible=visible,
                sidecar=sidecar,
                saathi_base_dir=saathi_base_dir,
                cold=cold,
                with_login=with_login,
                poll_sec=poll_sec,
                poll_interval=poll_interval,
                cpa_login_wait_sec=cpa_login_wait_sec,
            )
        )
    except Exception as exc:
        logger.exception("Site flow failed for %s", spec.site_id)
        result = SiteResult(site_id=spec.site_id)
        result.warm_ok = False
        result.login_ok = False
        result.warm_error = str(exc)
        result.login_error = str(exc)
        result.login_method = "failed"
        result.timeline.record("flow_error", str(exc)[:200])
        return result


def _run_warm_direct(
    dms_base_url: str,
    *,
    visible: bool,
    had_tab_before: bool = False,
) -> dict[str, Any]:
    if not visible:
        from app.services.fill_hero_dms_service import warm_dms_browser_session

        return dict(warm_dms_browser_session(dms_base_url))

    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        _try_cdp_focus_page,
        _try_cdp_maximize_browser_window,
        find_open_site_page,
        get_or_open_site_page,
        launch_site_background_detached,
    )

    page, open_error = get_or_open_site_page(
        dms_base_url,
        "DMS",
        require_login_on_open=False,
        launch_background=False,
    )
    if page is None:
        launched = launch_site_background_detached(dms_base_url)
        if launched:
            return {
                "success": True,
                "error": None,
                "attach": "detached_edge_chrome",
                "detached": True,
            }
        return {"success": False, "error": open_error or "Could not open DMS browser", "detached": False}

    _install_playwright_js_dialog_handler(page)
    _try_cdp_maximize_browser_window(page)
    _try_cdp_focus_page(page)
    try:
        url = (page.url or "").strip()[:200]
    except Exception:
        url = ""
    return {
        "success": True,
        "error": None,
        "attach": "reuse_existing_tab" if had_tab_before else "visible_native_open",
        "page_url": url,
    }


def _foreground_site_if_possible(spec: SiteSpec) -> None:
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        _try_cdp_focus_page,
        _try_cdp_maximize_browser_window,
        find_open_site_page,
    )

    page = find_open_site_page(spec.base_url, spec.site_label)
    if page is None:
        logger.info(
            "No %s page to bring to front (detached path or not attached yet).",
            spec.site_label,
        )
        return
    if spec.site_id == "dms":
        _install_playwright_js_dialog_handler(page)
    _try_cdp_maximize_browser_window(page)
    if _try_cdp_focus_page(page):
        logger.info(
            "Brought %s Playwright window to front (check taskbar if still hidden).",
            spec.site_label,
        )
    else:
        logger.info(
            "Maximized %s window; focus may still be on this console.",
            spec.site_label,
        )


def _foreground_dms_if_possible(dms_base_url: str) -> None:
    _foreground_site_if_possible(
        SiteSpec("dms", "DMS", dms_base_url, "warm_browser", "dms_base_url")
    )


def _print_legacy_warm_only_banner() -> None:
    print()
    print("=" * 72)
    print("NOTE: DMS warm-only mode — MISP/CPA skipped, login gate skipped.")
    print("      For autofill/login on all portals, double-click:")
    print("        test_dms_warm_browser.bat")
    print("      (NOT test_dms_warm_timing.bat)")
    print("=" * 72)
    print()


def _argv_has_flag(argv: list[str], flag: str) -> bool:
    prefix = f"{flag}="
    return flag in argv or any(a.startswith(prefix) for a in argv)


def _resolve_run_mode(
    argv: list[str],
    *,
    warm_only: bool,
    sites_raw: str | None,
    with_login: bool,
    visible: bool,
) -> tuple[list[str], bool, bool, bool]:
    """
    Returns (site_ids, with_login, visible, legacy_dms_only).

    --warm-only forces legacy DMS warm timing (no login, no other sites).
    Bare ``python test_dms_warm_browser.py`` (interactive, no site/login flags) defaults to
    all portals + login + visible foreground.
    """
    if warm_only:
        return ["dms"], False, visible, True

    explicit_sites = _argv_has_flag(argv, "--sites")
    explicit_with_login = _argv_has_flag(argv, "--with-login")
    explicit_warm_only = _argv_has_flag(argv, "--warm-only")

    if not explicit_sites and not explicit_with_login and not explicit_warm_only:
        return list(_SITE_IDS), True, visible or _stdin_is_interactive(), False

    site_ids = _parse_sites(sites_raw or "dms")
    legacy = site_ids == ["dms"] and not with_login
    return site_ids, with_login, visible, legacy


def _wait_warm_thread(warm_thread: threading.Thread, *, warm_t0: float) -> None:
    last_log = 0.0
    while warm_thread.is_alive():
        elapsed = time.perf_counter() - warm_t0
        if elapsed - last_log >= 1.0:
            logger.info(
                "Warming... %.1f s (script blocked here until warm returns; window may be behind other apps)",
                elapsed,
            )
            last_log = elapsed
        time.sleep(0.1)
    warm_thread.join()


def _maybe_teardown_cold() -> None:
    from app.services.handle_browser_opening import teardown_local_automation_browsers

    logger.info("Cold start: teardown_local_automation_browsers()")
    result = teardown_local_automation_browsers()
    logger.info("Teardown result: %s", result)


def _enable_debug_logging() -> None:
    logging.getLogger("app.services.handle_browser_opening").setLevel(logging.DEBUG)
    logging.getLogger("app.services.fill_hero_dms_service").setLevel(logging.DEBUG)
    logging.getLogger("app.services.fill_hero_insurance_service").setLevel(logging.DEBUG)
    logging.getLogger("app.services.add_alliance_cpa_insurance").setLevel(logging.DEBUG)


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _resolve_keep_open(cli_keep_open: bool | None, cli_exit_immediately: bool) -> bool:
    if cli_exit_immediately:
        return False
    if cli_keep_open is not None:
        return cli_keep_open
    env = (os.getenv("DMS_WARM_TEST_KEEP_OPEN") or "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    return _stdin_is_interactive()


def _hold_for_keep_open(timeline: Timeline) -> None:
    logger.info(
        "Keeping this process alive so the DMS browser stays open. "
        "Press Enter to exit (closing the browser). "
        "In Electron, the sidecar process stays running — this wrapper is not the app."
    )
    try:
        input()
    except EOFError:
        logger.info("Non-interactive stdin; waiting 300s before exit (set --exit-immediately to skip).")
        time.sleep(300.0)
    timeline.record("keep_open_end", "user continued or wait elapsed")


def _write_multi_log_file(
    path: Path,
    config: dict[str, Any],
    site_results: list[SiteResult],
    *,
    with_login: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "config": config,
        "with_login": with_login,
        "sites": {r.site_id: r.to_dict() for r in site_results},
    }
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    logger.info("Wrote log file: %s", path)


def _print_site_summaries(site_results: list[SiteResult], *, with_login: bool) -> None:
    print()
    print("--- Portal warm + login summary ---")
    for r in site_results:
        print(f"  [{r.site_id}] warm_ok={r.warm_ok} login_ok={r.login_ok} method={r.login_method}")
        if r.warm_error:
            print(f"    warm_error: {r.warm_error[:160]}")
        if with_login and r.login_error:
            print(f"    login_error: {r.login_error[:160]}")
        if r.final_url:
            print(f"    url: {r.final_url[:120]}")
        print(r.timeline.format_summary())
        print()


def _write_log_file(path: Path, timeline: Timeline, config: dict[str, Any], result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    warm_path = timeline.get("warm_path")
    body = {
        "config": config,
        "warm_result": result,
        "warm_path": warm_path.detail if warm_path else None,
        "timeline": [
            {"name": e.name, "offset_ms": round(e.offset_ms, 1), "detail": e.detail}
            for e in timeline.ordered()
        ],
    }
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    logger.info("Wrote log file: %s", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Portal warm-browser timing + optional login gates")
    parser.add_argument(
        "--cold",
        action="store_true",
        help="Call teardown_local_automation_browsers() before warm (first-open timing)",
    )
    parser.add_argument(
        "--sidecar",
        action="store_true",
        help="Run warm via electron/sidecar/job_runner.py (Electron subprocess parity)",
    )
    parser.add_argument(
        "--warm-only",
        action="store_true",
        help="DMS warm timing only (no MISP/CPA, no login gate) — default for test_dms_warm_browser.bat",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Open with launch_background=False and bring window forward (debug visibility; not prod warm)",
    )
    parser.add_argument("--debug", action="store_true", help="DEBUG logs for browser opening services")
    parser.add_argument(
        "--poll-sec",
        type=float,
        default=float(os.getenv("DMS_WARM_TEST_POLL_SEC", DEFAULT_POLL_SEC)),
        help="Poll for tab/CDP after trigger (default 120)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Poller sleep interval in seconds (default 0.25)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Optional JSON timeline output path",
    )
    parser.add_argument(
        "--allow-no-tab",
        action="store_true",
        help="Exit 0 even if warm succeeded but tab_detected never fired within poll window",
    )
    parser.add_argument(
        "--sites",
        default=None,
        help="Comma-separated sites: dms, misp, cpa, or all (default: all+login when bare script; dms with --warm-only)",
    )
    parser.add_argument(
        "--site-order",
        default="",
        help="Optional run order, e.g. dms,misp,cpa (subset of --sites)",
    )
    parser.add_argument(
        "--with-login",
        action="store_true",
        help="After warm, run each portal's login/autofill gate (Create Invoice / Sign In / CPA session wait)",
    )
    parser.add_argument(
        "--cpa-login-wait-sec",
        type=float,
        default=10.0,
        help="Seconds to poll CPA portal for login/session when --with-login (default 10)",
    )
    keep_group = parser.add_mutually_exclusive_group()
    keep_group.add_argument(
        "--keep-open",
        action="store_true",
        default=None,
        help="Wait for Enter before exit so Playwright keeps the browser (default when interactive)",
    )
    keep_group.add_argument(
        "--exit-immediately",
        action="store_true",
        help="Exit as soon as timing is done (browser closes when this process ends)",
    )
    args = parser.parse_args()
    keep_open = _resolve_keep_open(args.keep_open, args.exit_immediately)

    try:
        site_ids, with_login, visible, legacy_dms_only = _resolve_run_mode(
            sys.argv[1:],
            warm_only=args.warm_only,
            sites_raw=args.sites,
            with_login=args.with_login,
            visible=args.visible,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    if args.site_order.strip():
        try:
            order = _parse_sites(args.site_order)
        except ValueError as exc:
            logger.error("Invalid --site-order: %s", exc)
            return 1
        site_ids = [s for s in order if s in site_ids] + [s for s in site_ids if s not in order]
        legacy_dms_only = site_ids == ["dms"] and not with_login

    if legacy_dms_only:
        _print_legacy_warm_only_banner()
    else:
        logger.info(
            "RUN MODE: sites=%s  with_login=%s  visible=%s  (MISP + CPA will run after DMS)",
            ",".join(site_ids),
            with_login,
            visible,
        )

    cold = args.cold or (os.getenv("DMS_WARM_TEST_COLD", "").strip().lower() in ("1", "true", "yes"))
    if args.debug:
        _enable_debug_logging()

    try:
        specs, site_config = _resolve_site_specs(site_ids)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    from app.config import (
        PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
        USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS,
    )

    config = _print_config_snapshot(
        {
            **site_config,
            "sites": site_ids,
            "with_login": with_login,
            "visible": visible,
            "legacy_dms_only": legacy_dms_only,
            "USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS": bool(USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS),
            "PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT": PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT,
        }
    )
    saathi_base = (os.getenv("SAATHI_BASE_DIR") or r"D:\Saathi").strip()
    log_path = args.log_file or Path(__file__).resolve().parent / "dms_warm_browser_last.json"

    if cold:
        from app.services.playwright_executor import run_playwright_callable_sync

        run_playwright_callable_sync(_maybe_teardown_cold)
        time.sleep(0.5)
    elif legacy_dms_only:
        logger.info(
            "No --cold teardown. If you still see ~5–10s warm_return, that is often "
            "first Playwright Chromium profile launch (native_persistent_first_launch), not --cold."
        )
    if not visible and not args.sidecar and not legacy_dms_only:
        logger.info(
            "Prod-parity warm: launch_background=True — browser may open maximized but behind "
            "this terminal or Electron. Use --visible to force foreground."
        )

    if not legacy_dms_only:
        site_results: list[SiteResult] = []
        for spec in specs:
            site_results.append(
                _run_site_flow(
                    spec,
                    visible=visible,
                    sidecar=args.sidecar,
                    saathi_base_dir=saathi_base,
                    cold=cold,
                    with_login=with_login,
                    poll_sec=args.poll_sec,
                    poll_interval=args.poll_interval,
                    cpa_login_wait_sec=args.cpa_login_wait_sec,
                )
            )
        _print_site_summaries(site_results, with_login=with_login)
        _write_multi_log_file(log_path, config, site_results, with_login=with_login)

        any_warm_fail = any(not r.warm_ok for r in site_results)
        any_login_fail = with_login and any(not r.login_ok for r in site_results)
        any_tab_miss = any(
            not r.timeline.has("tab_detected") and r.warm_ok for r in site_results
        )

        if any_warm_fail or any_login_fail:
            return 1
        if any_tab_miss and not args.allow_no_tab:
            logger.error(
                "One or more sites warmed successfully but no tab detected within %.0fs. "
                "Use --allow-no-tab if testing detached-only path.",
                args.poll_sec,
            )
            return 1

        if keep_open and not args.sidecar:
            _hold_for_keep_open(Timeline())
        elif keep_open and args.sidecar:
            logger.warning(
                "--keep-open with --sidecar: browsers live in sidecar subprocesses; "
                "they may close when jobs finish. Use direct mode to inspect windows."
            )
        return 0

    # Legacy DMS-only warm (no login gate) — CDP poller + sync warm on Playwright worker thread.
    dms_url = specs[0].base_url

    def _legacy_warm_flow() -> tuple[Timeline, dict[str, Any], bool]:
        from app.services.handle_browser_opening import _DMS_NATIVE_PERSISTENT_CONTEXT

        pre_snap = _snapshot_browser_state(dms_url)
        timeline = Timeline()
        stop_poller = threading.Event()
        poller = WarmPoller(
            dms_base_url=dms_url,
            timeline=timeline,
            poll_sec=args.poll_sec,
            interval_sec=args.poll_interval,
            stop_event=stop_poller,
        )
        timeline.record("pre_warm", _format_snapshot(pre_snap))
        timeline.record(
            "trigger_start",
            f"mode={'sidecar' if args.sidecar else 'direct'} cold={cold} visible={visible}",
        )
        poller.start_cdp_only()

        if args.sidecar:
            warm_result = _run_sidecar_warm(
                job_type="warm_browser",
                params={"dms_base_url": dms_url},
                saathi_base_dir=saathi_base,
            )
        else:
            warm_result = _run_warm_direct(
                dms_url,
                visible=visible,
                had_tab_before=pre_snap.tab_found,
            )

        post_snap = _snapshot_browser_state(dms_url)
        warm_path = _infer_warm_path(
            pre=pre_snap,
            post=post_snap,
            result=warm_result,
            teardown_cold=cold,
        )
        timeline.record("post_warm", _format_snapshot(post_snap))
        timeline.record("warm_path", warm_path)
        logger.info("Warm path: %s", warm_path)
        success = bool(warm_result.get("success"))
        err = (warm_result.get("error") or "").strip()
        timeline.record(
            "warm_return",
            f"success={success}" + (f" error={err[:200]}" if err else ""),
        )
        if success and not args.sidecar and not visible:
            _foreground_dms_if_possible(dms_url)

        poller.enable_playwright_probes()
        ctx = _DMS_NATIVE_PERSISTENT_CONTEXT
        if ctx is not None:
            try:
                n = len(list(ctx.pages))
                logger.info("[post-warm] native DMS context alive, pages=%d", n)
            except Exception:
                logger.info("[post-warm] native DMS context alive")
        else:
            logger.info("[post-warm] native DMS context: none")

        stop_poller.clear()
        poller.poll_playwright_until_done()
        stop_poller.set()
        poller.join(timeout=2.0)
        if not timeline.has("poll_end"):
            timeline.record("poll_end", f"after warm (poll window {args.poll_sec:.0f}s)")
        tab_ok = timeline.has("tab_detected")
        return timeline, warm_result, tab_ok and success

    from app.services.playwright_executor import run_playwright_callable_sync

    timeline, result, ok = run_playwright_callable_sync(_legacy_warm_flow)

    print()
    print(timeline.format_summary())
    print()

    if not result.get("success"):
        _write_log_file(log_path, timeline, config, result)
        return 1
    if not timeline.has("tab_detected") and not args.allow_no_tab:
        logger.error(
            "Warm returned success but no DMS tab detected within %.0fs. "
            "Use --allow-no-tab if testing detached-only path.",
            args.poll_sec,
        )
        _write_log_file(log_path, timeline, config, result)
        return 1

    if keep_open and not args.sidecar:
        _hold_for_keep_open(timeline)
    elif keep_open and args.sidecar:
        logger.warning(
            "--keep-open with --sidecar: browser lives in the sidecar subprocess; "
            "it may already close when the job finishes. Use direct mode to inspect the window."
        )

    _write_log_file(log_path, timeline, config, result)
    return 0 if ok or args.allow_no_tab else 1


if __name__ == "__main__":
    raise SystemExit(main())
