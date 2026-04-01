"""
Hero Insurance (MISP) Playwright flow: **pre_process** ends after KYC **Proceed** (or upload + Proceed)
on the VIN page; **main_process** fills VIN from DB (**``full_chassis``** via ``form_insurance_view``),
**Submit**, **I agree**, then the proposal form. Proposer/vehicle/nominee fields come from the view;
email, add-ons, CPA, HDFC, and registration date use **hardcoded** defaults. **Proposal Review**, then **Issue Policy**; scrape **policy number** and **insurance cost** again and persist via ``update_insurance_master_policy_after_issue``.
Browser reuse uses ``handle_browser_opening.get_or_open_site_page`` with ``match_base`` from **pre_process**.
"""
import logging
import re
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.config import (
    INSURANCE_ACTION_TIMEOUT_MS,
    INSURANCE_BASE_URL,
    INSURANCE_LOGIN_WAIT_MS,
    INSURANCE_POLICY_FILL_TIMEOUT_MS,
)
from app.services.add_sales_commit_service import (
    insert_insurance_master_after_gi,
    update_insurance_master_policy_after_issue,
)
from app.services.handle_browser_opening import get_or_open_site_page
from app.services.insurance_form_values import (
    agent_debug_ndjson_log,
    append_playwright_insurance_line,
    append_playwright_insurance_line_or_dealer_fallback,
    build_insurance_fill_values,
    reset_playwright_insurance_log,
    write_insurance_form_values,
)
from app.services.insurance_kyc_payloads import insurance_kyc_png_payloads
from app.services.utility_functions import fuzzy_best_option_label

logger = logging.getLogger(__name__)


def _t(page, ms: int) -> None:
    try:
        page.wait_for_timeout(min(ms, 15_000))
    except Exception:
        pass


def _hero_insurance_snapshot_visible_controls(ctx) -> list[dict]:
    """
    Collect visible buttons/links/submits on a **Page** or **Frame** (for iframe login forms).
    """
    try:
        raw = ctx.evaluate(
            """() => {
            const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 1 && r.height > 1;
            };
            const out = [];
            const sels = [
                'button', 'input[type="submit"]', 'input[type="button"]',
                'a[href]', '[role="button"]', 'input[type="image"]'
            ];
            const seen = new Set();
            for (const sel of sels) {
                document.querySelectorAll(sel).forEach((el) => {
                    if (out.length >= 45) return;
                    if (!vis(el) || seen.has(el)) return;
                    seen.add(el);
                    const tag = (el.tagName || '').toLowerCase();
                    const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 140);
                    const val = String(el.value || '').trim().slice(0, 100);
                    out.push({
                        tag,
                        type: String(el.type || ''),
                        text: txt,
                        value: val,
                        id: String(el.id || '').slice(0, 80),
                        name: String(el.name || '').slice(0, 60),
                        aria: String(el.getAttribute('aria-label') || '').slice(0, 100),
                        href: String(el.href || '').slice(0, 140),
                    });
                });
            }
            return out;
        }"""
        )
        return list(raw) if isinstance(raw, list) else []
    except Exception:
        return []


def _hero_insurance_snapshot_visible_controls_in_root(ctx) -> list[dict]:
    """Visible buttons/submits/links **inside** ``#root`` (React/Vue mount); empty if no ``#root``."""
    try:
        raw = ctx.evaluate(
            """() => {
            const root = document.getElementById('root');
            if (!root) return [];
            const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 1 && r.height > 1;
            };
            const out = [];
            const sels = [
                'button', 'input[type="submit"]', 'input[type="button"]',
                'a[href]', '[role="button"]', 'input[type="image"]'
            ];
            const seen = new Set();
            for (const sel of sels) {
                root.querySelectorAll(sel).forEach((el) => {
                    if (out.length >= 45) return;
                    if (!vis(el) || seen.has(el)) return;
                    seen.add(el);
                    const tag = (el.tagName || '').toLowerCase();
                    const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 140);
                    const val = String(el.value || '').trim().slice(0, 100);
                    out.push({
                        tag,
                        type: String(el.type || ''),
                        text: txt,
                        value: val,
                        id: String(el.id || '').slice(0, 80),
                        name: String(el.name || '').slice(0, 60),
                        aria: String(el.getAttribute('aria-label') || '').slice(0, 100),
                        href: String(el.href || '').slice(0, 140),
                    });
                });
            }
            return out;
        }"""
        )
        return list(raw) if isinstance(raw, list) else []
    except Exception:
        return []


def _hero_insurance_log_page_diagnostics(
    page,
    *,
    phase: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """Log URL, frame count, and visible control snapshot to logger and ``Playwright_insurance.txt``."""
    # region agent log
    try:
        _nf = len(page.frames)
    except Exception:
        _nf = -1
    agent_debug_ndjson_log(
        "H3",
        "fill_hero_insurance_service._hero_insurance_log_page_diagnostics",
        "entry",
        {
            "phase": phase,
            "has_ocr_output_dir": bool(ocr_output_dir),
            "subfolder_repr": repr((subfolder or "")[:80]),
            "frame_count": _nf,
        },
    )
    # endregion
    lines: list[str] = []
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        url = (page.url or "").strip()
    except Exception:
        url = ""
    lines.append(f"phase={phase!r} url={url[:500]!r} title={title[:200]!r}")
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    lines.append(f"frame_count={len(frames)}")
    for idx, fr in enumerate(frames):
        try:
            fu = (fr.url or "")[:300]
        except Exception:
            fu = "(no url)"
        snap = _hero_insurance_snapshot_visible_controls(fr)
        root_snap = _hero_insurance_snapshot_visible_controls_in_root(fr)
        lines.append(f"--- frame[{idx}] url={fu!r} visible_controls={len(snap)} ---")
        for j, row in enumerate(snap[:35]):
            lines.append(f"  [{j}] {row}")
        if root_snap:
            lines.append(f"--- frame[{idx}] #root only: {len(root_snap)} controls ---")
            for j, row in enumerate(root_snap[:35]):
                lines.append(f"  root[{j}] {row}")
        logger.info(
            "Hero Insurance diagnostics %s frame[%s] url=%s controls=%s #root=%s",
            phase,
            idx,
            fu[:200],
            len(snap),
            len(root_snap),
        )
    blob = "\n".join(lines)
    if len(blob) > 12000:
        blob = blob[:12000] + "\n…(truncated)"
    logger.warning(
        "Hero Insurance DIAG phase=%s (see full snapshot on next logger line or insurance log file)",
        phase,
    )
    logger.info("Hero Insurance diagnostics %s full snapshot:\n%s", phase, blob)
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "DIAG",
        f"login_page_snapshot {phase}: " + blob.replace("\n", " \\n "),
    )
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        fb = (Path(ocr_output_dir).resolve() / "Playwright_insurance_diag_fallback.txt") if ocr_output_dir else None
        logger.warning(
            "Hero Insurance: DIAG also needs subfolder for per-upload Playwright_insurance.txt under ocr_output; "
            "without subfolder, dealer fallback is %s",
            str(fb) if fb else "(no ocr_output_dir — DIAG only in backend logs)",
        )


def _iter_page_and_child_frames(page):
    """Main page first, then each child frame (login may be in an iframe)."""
    yield page
    try:
        for fr in page.frames:
            try:
                if fr != page.main_frame:
                    yield fr
            except Exception:
                continue
    except Exception:
        pass


def _misp_expand_login_as_panel(page) -> bool:
    """
    MISP partner (Radix): **Login as** reveals the password field and primary **Sign In** control.
    Until this opens, ``form:has(password)`` may be empty and **Sign In** may not receive clicks.
    """
    try:
        btn = page.get_by_role("button", name=re.compile(r"^\s*Login\s+as\s*$", re.I))
        if btn.count() > 0:
            try:
                if btn.first.is_visible(timeout=2_500):
                    btn.first.scroll_into_view_if_needed(timeout=4_000)
                    btn.first.click(timeout=10_000)
                    page.wait_for_timeout(900)
                    logger.info("Hero Insurance: clicked 'Login as' to expand partner login panel.")
                    return True
            except Exception:
                try:
                    btn.first.click(timeout=10_000, force=True)
                    page.wait_for_timeout(900)
                    logger.info("Hero Insurance: clicked 'Login as' (force) to expand partner login panel.")
                    return True
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Hero Insurance: Login as expand: %s", exc)
    return False


def _try_dom_click_sign_in_submit(page) -> bool:
    """Click the **Sign In** (or exact **Login**) ``button[type=submit]`` via DOM (bypasses some overlay issues)."""
    try:
        ok = page.evaluate(
            """() => {
            const btns = Array.from(document.querySelectorAll('button[type="submit"]'));
            for (const b of btns) {
                const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                if (/^sign\\s*in$/i.test(t)) {
                    try { b.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                    b.click();
                    return { ok: true, text: t };
                }
                if (/^login$/i.test(t)) {
                    try { b.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                    b.click();
                    return { ok: true, text: t };
                }
            }
            return { ok: false, text: '' };
        }"""
        )
        if isinstance(ok, dict) and ok.get("ok"):
            logger.info(
                "Hero Insurance: Sign In submit clicked via DOM evaluate (text=%r).",
                ok.get("text"),
            )
            return True
    except Exception as exc:
        logger.debug("Hero Insurance: DOM Sign In evaluate: %s", exc)
    return False


def _try_click_sign_in_inside_password_form(scope, *, timeout_ms: int, scope_label: str) -> bool:
    """
    Click **Sign In** / **Login** only inside a ``<form>`` that contains a password field.

    MISP partner landing (e.g. ``/misp-partner-login``) exposes **two** ``button[type="submit"]`` nodes in
    the same view — **Get Price** (hero) and **Sign In** (login). A naive ``button[type=submit]`` scan can
    hit the wrong control or rely on order; scoping to the login form matches runtime DIAG snapshots.
    """
    try:
        login_form = scope.locator(
            'form:has(input[type="password"]), '
            'form:has(input[autocomplete="current-password"]), '
            'form:has(input[name*="password" i])'
        )
        if login_form.count() == 0:
            return False
        patterns = (
            (re.compile(r"^\s*Sign\s*In\s*$", re.I), "Sign In"),
            (re.compile(r"^\s*Login\s*$", re.I), "Login"),
            (re.compile(r"^\s*Log\s+in\s*$", re.I), "Log in"),
        )
        for pat, dbg in patterns:
            for sel in ('button[type="submit"]', 'input[type="submit"]'):
                try:
                    loc = login_form.locator(sel).filter(has_text=pat)
                    if loc.count() <= 0:
                        continue
                    b = loc.first
                    if not b.is_visible(timeout=2_500):
                        continue
                    b.scroll_into_view_if_needed(timeout=4_000)
                    try:
                        b.click(timeout=timeout_ms)
                    except Exception:
                        b.click(timeout=timeout_ms, force=True)
                    logger.info(
                        "Hero Insurance: clicked %s (%s in password form) scope=%s.",
                        dbg,
                        sel,
                        scope_label,
                    )
                    return True
                except Exception:
                    continue
        try:
            rb = login_form.get_by_role(
                "button",
                name=re.compile(r"^\s*(Sign\s*In|Login|Log\s*in)\s*$", re.I),
            )
            if rb.count() > 0 and rb.first.is_visible(timeout=2_000):
                rb.first.scroll_into_view_if_needed(timeout=4_000)
                rb.first.click(timeout=timeout_ms)
                logger.info(
                    "Hero Insurance: clicked login role=button in password form scope=%s.",
                    scope_label,
                )
                return True
        except Exception:
            pass
    except Exception as exc:
        logger.debug("Hero Insurance: password-form Sign In: %s", exc)
    return False


def _click_sign_in_on_scope(scope, *, timeout_ms: int, scope_label: str) -> bool:
    """
    Try login CTA within a **Page**, **Frame**, or **Locator** (e.g. ``#root`` SPA mount).
    """
    label_patterns = (
        (re.compile(r"^\s*Sign In\s*$", re.I), "Sign In"),
        (re.compile(r"^\s*Login\s*$", re.I), "Login"),
        (re.compile(r"^\s*Log\s+in\s*$", re.I), "Log in"),
    )
    try:
        if _try_click_sign_in_inside_password_form(
            scope, timeout_ms=timeout_ms, scope_label=scope_label
        ):
            return True
        # Prefer explicit form submit controls (MISP login: ``<button type="submit">Sign In</button>``).
        for text, dbg in (("Sign In", "Sign In"), ("Login", "Login"), ("Log in", "Log in")):
            try:
                sub = scope.locator('button[type="submit"]').filter(
                    has_text=re.compile(re.escape(text), re.I)
                )
                if sub.count() > 0:
                    for i in range(min(sub.count(), 8)):
                        b = sub.nth(i)
                        if b.is_visible(timeout=2_000):
                            b.scroll_into_view_if_needed(timeout=3_000)
                            b.click(timeout=timeout_ms)
                            logger.info(
                                "Hero Insurance: clicked %s (button[type=submit]) scope=%s.",
                                dbg,
                                scope_label,
                            )
                            return True
            except Exception:
                continue
        for css, dbg in (
            ('input[type="submit"][value*="Sign In" i]', "Sign In"),
            ('input[type="submit"][value*="Login" i]', "Login"),
            ('input[type="submit"][value*="Log in" i]', "Log in"),
        ):
            try:
                loc = scope.locator(css)
                if loc.count() > 0:
                    for i in range(min(loc.count(), 6)):
                        el = loc.nth(i)
                        if el.is_visible(timeout=1_500):
                            el.scroll_into_view_if_needed(timeout=2_000)
                            el.click(timeout=timeout_ms)
                            logger.info(
                                "Hero Insurance: clicked %s (input[type=submit]) scope=%s.",
                                dbg,
                                scope_label,
                            )
                            return True
            except Exception:
                continue

        for pat, _dbg in label_patterns:
            loc = scope.get_by_text(pat)
            n = loc.count()
            if n <= 0:
                continue
            for i in range(min(n, 12)):
                el = loc.nth(i)
                try:
                    if el.is_visible(timeout=2_000):
                        el.click(timeout=timeout_ms)
                        logger.info(
                            "Hero Insurance: clicked login CTA (%s) scope=%s.",
                            pat.pattern,
                            scope_label,
                        )
                        return True
                except Exception:
                    continue
        for role in ("button", "link"):
            for pat in (
                re.compile(r"^\s*(Sign In|Login|Log\s+in)\s*$", re.I),
            ):
                loc = scope.get_by_role(role, name=pat)
                if loc.count() > 0:
                    try:
                        if loc.first.is_visible(timeout=1_500):
                            loc.first.click(timeout=timeout_ms)
                            logger.info(
                                "Hero Insurance: clicked login CTA (role=%s) scope=%s.",
                                role,
                                scope_label,
                            )
                            return True
                    except Exception:
                        continue
        # Prominent anchor to auth route (SPA)
        for sel in ('a[href*="login" i]', 'a[href*="signin" i]', 'a[href*="sign-in" i]'):
            try:
                a = scope.locator(sel).filter(has_text=re.compile(r"login|sign\s*in", re.I))
                if a.count() > 0 and a.first.is_visible(timeout=1_200):
                    a.first.click(timeout=timeout_ms)
                    logger.info(
                        "Hero Insurance: clicked login link (%s) scope=%s.",
                        sel,
                        scope_label,
                    )
                    return True
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Hero Insurance: login CTA click skipped (scope=%s): %s", scope_label, exc)
    return False


def _click_sign_in_on_context(ctx, *, timeout_ms: int) -> bool:
    """Try login CTA on a single **Page** or **Frame**; **#root** first (SPA), then full document."""
    scopes: list[tuple[object, str]] = []
    try:
        root = ctx.locator("#root")
        if root.count() > 0:
            scopes.append((root, "#root"))
    except Exception:
        pass
    scopes.append((ctx, type(ctx).__name__))

    for scope, label in scopes:
        if _click_sign_in_on_scope(scope, timeout_ms=timeout_ms, scope_label=label):
            return True
    return False


def _click_sign_in_if_visible(page, *, timeout_ms: int) -> bool:
    """
    Click landing-page login CTA (``Sign In``, ``Login``, ``Log in``).
    Hero MISP login form uses ``<button type="submit">`` with label **Sign In**; landing pages may use **Login** only.
    Tries the **main document** and each **child frame** (login may render inside an iframe).
    Returns True if a click was attempted.
    """
    expanded = _misp_expand_login_as_panel(page)
    # region agent log
    try:
        agent_debug_ndjson_log(
            "H6",
            "fill_hero_insurance_service._click_sign_in_if_visible",
            "after_login_as_expand",
            {"expanded": expanded},
        )
    except Exception:
        pass
    # endregion
    for ctx in _iter_page_and_child_frames(page):
        if _click_sign_in_on_context(ctx, timeout_ms=timeout_ms):
            # region agent log
            try:
                agent_debug_ndjson_log(
                    "H6",
                    "fill_hero_insurance_service._click_sign_in_if_visible",
                    "playwright_context_click_ok",
                    {"ctx": type(ctx).__name__},
                )
            except Exception:
                pass
            # endregion
            return True
    if _try_dom_click_sign_in_submit(page):
        # region agent log
        try:
            agent_debug_ndjson_log(
                "H6",
                "fill_hero_insurance_service._click_sign_in_if_visible",
                "dom_eval_click_ok",
                {"fallback": True},
            )
        except Exception:
            pass
        # endregion
        return True
    # region agent log
    try:
        agent_debug_ndjson_log(
            "H6",
            "fill_hero_insurance_service._click_sign_in_if_visible",
            "all_sign_in_paths_failed",
            {},
        )
    except Exception:
        pass
    # endregion
    return False


def _click_2w_icon(page, *, timeout_ms: int) -> None:
    """
    Open **2W** (two-wheeler) product path. Markup varies: ``img[alt]``, tiles, or icon buttons.
    """
    _t(page, 400)

    def _try_click(loc, label: str) -> bool:
        try:
            if loc.count() <= 0:
                return False
            target = loc.first
            target.wait_for(state="visible", timeout=min(timeout_ms, 25_000))
            target.scroll_into_view_if_needed(timeout=5_000)
            try:
                target.click(timeout=timeout_ms)
            except Exception:
                target.click(timeout=timeout_ms, force=True)
            logger.info("Hero Insurance: clicked 2W control (%s).", label)
            return True
        except Exception:
            return False

    try_order = (
        # Hero MISP WebForms: stable tile id (operator-confirmed).
        ('[aid="ctl00_TWO"]', page.locator('[aid="ctl00_TWO"]')),
        ('#ctl00_TWO', page.locator("#ctl00_TWO")),
        ('img[alt="2W Icon"]', page.locator('img[alt="2W Icon"]')),
        ("img[alt*='2W' i]", page.locator("img[alt*='2W' i]")),
        ("img[title*='2W' i]", page.locator("img[title*='2W' i]")),
        ("[aria-label*='2W' i]", page.locator("[aria-label*='2W' i]")),
        ("[aria-label*='two wheel' i]", page.locator("[aria-label*='two wheel' i]")),
        (
            "role=button 2W",
            page.get_by_role("button", name=re.compile(r"2\s*W|two\s*wheel", re.I)),
        ),
        (
            "link 2W",
            page.get_by_role("link", name=re.compile(r"2\s*W|two\s*wheel", re.I)),
        ),
        (
            "tile text",
            page.locator("a, button, [role='button'], div[role='button']").filter(
                has_text=re.compile(r"^\s*2\s*W\s*$", re.I)
            ),
        ),
    )
    for label, loc in try_order:
        if _try_click(loc, label):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(20_000, timeout_ms * 2))
            except Exception:
                pass
            return

    # Last resort: scan visible clickable elements for 2W / two-wheeler copy (Angular/React tiles).
    try:
        hit = page.evaluate(
            """() => {
            const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2;
            };
            const byId = document.querySelector('[aid="ctl00_TWO"]') || document.getElementById('ctl00_TWO');
            if (byId && vis(byId)) {
                try { byId.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                byId.click();
                return 'aid-ctl00_TWO';
            }
            const cand = Array.from(
                document.querySelectorAll('img[alt], [aria-label], button, a, [role="button"]')
            );
            for (const el of cand) {
                if (!vis(el)) continue;
                const alt = (el.getAttribute('alt') || '').trim();
                const ar = (el.getAttribute('aria-label') || '').trim();
                const tx = (el.textContent || '').trim();
                const blob = (alt + ' ' + ar + ' ' + tx).toLowerCase();
                if (/\\b2\\s*w\\b/.test(blob) || /two[-\\s]*wheel/i.test(blob)) {
                    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                    el.click();
                    return 'ok';
                }
            }
            return '';
        }"""
        )
        if hit:
            logger.info("Hero Insurance: clicked 2W control (DOM scan).")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(20_000, timeout_ms * 2))
            except Exception:
                pass
            return
    except Exception as exc:
        logger.warning("Hero Insurance: 2W DOM scan failed: %s", exc)

    raise TimeoutError("2W (two-wheeler) entry control not found or not clickable.")


def _click_new_policy(page, *, timeout_ms: int) -> None:
    loc = page.get_by_text("New Policy", exact=True)
    loc.first.wait_for(state="visible", timeout=timeout_ms)
    loc.first.click(timeout=timeout_ms)
    logger.info("Hero Insurance: clicked New Policy.")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 2))
    except Exception:
        pass


def _select_option_fuzzy_in_select(page, select_locator, query: str, *, timeout_ms: int) -> bool:
    if not (query or "").strip():
        return False
    try:
        sel = select_locator.first
        if sel.count() == 0 or not sel.first.is_visible(timeout=2_000):
            return False
        labels: list[str] = []
        try:
            raw = sel.locator("option").evaluate_all(
                "els => els.map(e => (e.textContent || '').trim()).filter(Boolean)"
            )
            labels = [str(x).strip() for x in (raw or []) if str(x).strip()]
        except Exception:
            n = sel.locator("option").count()
            for i in range(min(n, 400)):
                t = (sel.locator("option").nth(i).inner_text() or "").strip()
                if t:
                    labels.append(t)
        pick = fuzzy_best_option_label(query, labels)
        if not pick:
            return False
        sel.select_option(label=pick, timeout=timeout_ms)
        logger.info("Hero Insurance: selected option %r (fuzzy from %r)", pick, query[:60])
        return True
    except Exception as exc:
        logger.warning("Hero Insurance: fuzzy select failed: %s", exc)
        return False


def _fill_insurance_company_and_ovd_mobile_consent(
    page, values: dict, *, timeout_ms: int
) -> str | None:
    """Returns error message or None on success."""
    insurer = (values.get("insurer") or "").strip()
    mobile = (values.get("mobile_number") or "").strip()

    # --- Insurance Company (dropdown / search; match insurer from details sheet / DB) ---
    if insurer:
        filled = False
        # Native <select> near label text
        for sel_css in (
            'select:near(:text("Insurance Company"))',
            "select[aria-label*='Insurance Company' i]",
            "select[title*='Insurance Company' i]",
        ):
            try:
                loc = page.locator(sel_css)
                if loc.count() > 0 and _select_option_fuzzy_in_select(page, loc, insurer, timeout_ms=timeout_ms):
                    filled = True
                    break
            except Exception:
                continue
        if not filled:
            # Combobox / typeahead: focus field and type query, pick first matching option
            try:
                lab = page.get_by_text(re.compile(r"Insurance\s*Company", re.I)).first
                if lab.count() > 0:
                    lab.click(timeout=5_000)
                    _t(page, 300)
                inp = page.locator(
                    "input[placeholder*='Search' i], input[type='search'], "
                    "[role='combobox'] input, input[aria-autocomplete='list']"
                ).first
                if inp.count() > 0 and inp.is_visible(timeout=3_000):
                    inp.fill("")
                    inp.type(insurer[:80], delay=25)
                    _t(page, 500)
                    opt = page.get_by_role("option", name=re.compile(re.escape(insurer[:24]), re.I))
                    if opt.count() == 0:
                        opt = page.locator("[role='option'], li[role='option']").filter(has_text=re.compile(re.escape(insurer[:16]), re.I))
                    if opt.count() > 0:
                        opt.first.click(timeout=timeout_ms)
                        filled = True
                        logger.info("Hero Insurance: insurer chosen via typeahead.")
            except Exception as exc:
                logger.warning("Hero Insurance: insurer typeahead failed: %s", exc)
        if not filled:
            return (
                "Could not set Insurance Company from details-sheet insurer "
                f"({insurer[:40]!r}). Adjust selectors for this portal build."
            )

    # --- OVD Type: AADHAAR CARD ---
    ovd_ok = False
    for sel_css in (
        'select:near(:text("Officially Valid"))',
        'select:near(:text("OVD"))',
        "select[aria-label*='OVD' i]",
        "select[aria-label*='Officially Valid' i]",
    ):
        try:
            loc = page.locator(sel_css)
            if loc.count() == 0:
                continue
            opts = loc.first.locator("option")
            n = opts.count()
            for i in range(min(n, 200)):
                t = (opts.nth(i).inner_text() or "").strip().upper()
                if "AADHAAR" in t and "CARD" in t:
                    val = opts.nth(i).get_attribute("value")
                    if val:
                        loc.first.select_option(value=val, timeout=timeout_ms)
                    else:
                        loc.first.select_option(label=t, timeout=timeout_ms)
                    ovd_ok = True
                    logger.info("Hero Insurance: OVD set to AADHAAR CARD.")
                    break
            if ovd_ok:
                break
        except Exception:
            continue
    if not ovd_ok:
        try:
            page.get_by_label(re.compile(r"Officially\s*Valid|OVD", re.I)).locator(
                "xpath=following::select[1]"
            ).first.select_option(label=re.compile(r"AADHAAR\s*CARD", re.I), timeout=timeout_ms)
            ovd_ok = True
        except Exception:
            try:
                ok_js = page.evaluate(
                    """() => {
                      const sels = Array.from(document.querySelectorAll('select'));
                      for (const s of sels) {
                        for (const o of s.options) {
                          const t = (o.textContent || '').trim();
                          if (/AADHAAR/i.test(t) && /CARD/i.test(t)) {
                            s.value = o.value;
                            s.dispatchEvent(new Event('input', { bubbles: true }));
                            s.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                          }
                        }
                      }
                      return false;
                    }"""
                )
                if ok_js:
                    ovd_ok = True
                    logger.info("Hero Insurance: OVD set via JS scan for AADHAAR CARD.")
            except Exception:
                pass
    if not ovd_ok:
        return "Could not select Officially Valid Document (OVD) Type = AADHAAR CARD."

    # --- Mobile (customer_master.mobile_number) ---
    if not mobile:
        return "customer_master.mobile_number is empty in DB values."
    mob_filled = False
    for ph in (
        page.get_by_label(re.compile(r"^Mobile\s*(Number|No\.?|Phone)?\s*$", re.I)),
        page.get_by_placeholder(re.compile(r"mobile", re.I)),
        page.locator('input[type="tel"]'),
        page.locator("input[name*='mobile' i]"),
    ):
        try:
            if ph.count() > 0 and ph.first.is_visible(timeout=2_000):
                ph.first.fill("")
                ph.first.fill(mobile[:12], timeout=timeout_ms)
                mob_filled = True
                logger.info("Hero Insurance: filled mobile.")
                break
        except Exception:
            continue
    if not mob_filled:
        return "Could not fill mobile number field."

    # --- Consent checkbox ---
    try:
        cbs = page.get_by_role("checkbox").filter(
            has_text=re.compile(r"consent|agree|i\s+confirm", re.I)
        )
        if cbs.count() == 0:
            cbs = page.locator('input[type="checkbox"]')
        for i in range(min(cbs.count(), 12)):
            cb = cbs.nth(i)
            if cb.is_visible(timeout=1_000) and not cb.is_checked():
                cb.check(timeout=timeout_ms)
                logger.info("Hero Insurance: checked consent.")
                break
    except Exception as exc:
        logger.warning("Hero Insurance: consent checkbox: %s", exc)

    return None


def _kyc_proceed_or_upload(page, *, timeout_ms: int) -> str | None:
    """If KYC already done, click Proceed; else upload Aadhaar front/back and photo placeholders."""
    try:
        body = page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
        txt = (body or "").lower()
    except Exception:
        txt = ""

    if re.search(r"kyc\s+is\s+already|already\s+done|kyc\s+already\s+complete", txt):
        try:
            page.get_by_role("button", name=re.compile(r"^\s*Proceed\s*$", re.I)).first.click(
                timeout=timeout_ms
            )
            logger.info("Hero Insurance: KYC already done — clicked Proceed.")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 4))
            except Exception:
                pass
            _t(page, 500)
            return None
        except Exception:
            try:
                page.get_by_text(re.compile(r"^\s*Proceed\s*$", re.I)).first.click(timeout=timeout_ms)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 4))
                except Exception:
                    pass
                _t(page, 500)
                return None
            except Exception as exc:
                return f"KYC already done but Proceed click failed: {exc!s}"

    # Upload paths: Aadhaar front, back, phone (front again) — use minimal PNG payloads like dummy flow
    payloads = insurance_kyc_png_payloads()
    files = page.locator('input[type="file"]')
    n = files.count()
    if n <= 0:
        return (
            "KYC upload expected (no 'already done' message) but no file inputs found. "
            "Complete KYC manually or adjust selectors."
        )
    try:
        for i in range(min(n, len(payloads))):
            files.nth(i).set_input_files(
                {
                    "name": payloads[i]["name"],
                    "mimeType": payloads[i]["mimeType"],
                    "buffer": payloads[i]["buffer"],
                },
                timeout=timeout_ms,
            )
        logger.info("Hero Insurance: attached %s KYC file input(s).", min(n, len(payloads)))
    except Exception as exc:
        return f"KYC file upload failed: {exc!s}"
    _t(page, 500)
    try:
        for name_pat in (r"^\s*Proceed\s*$", r"^\s*Submit\s*$", r"^\s*Continue\s*$"):
            btn = page.get_by_role("button", name=re.compile(name_pat, re.I))
            if btn.count() > 0 and btn.first.is_visible(timeout=2_500):
                btn.first.click(timeout=timeout_ms)
                logger.info("Hero Insurance: clicked %s after KYC upload.", name_pat.strip("^$"))
                break
    except Exception as exc:
        logger.debug("Hero Insurance: post-KYC-upload navigation click: %s", exc)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 4))
    except Exception:
        pass
    _t(page, 400)
    return None


def _run_hero_misp_portal_after_open(
    page,
    values: dict | None,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    Login / Sign In → 2W (two-wheeler) → New Policy → (if ``values``) insurer / OVD / mobile / consent → KYC Proceed or upload.
    Returns None on success, else error string.
    """
    _t(page, 500)
    _hero_insurance_log_page_diagnostics(
        page,
        phase="before_sign_in",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    clicked = _click_sign_in_if_visible(page, timeout_ms=timeout_ms)
    # region agent log
    try:
        _sign_url = (page.url or "").strip()[:400]
        _has_root = page.locator("#root").count() > 0
    except Exception as e:
        _sign_url = f"(err:{e!s})"
        _has_root = False
    agent_debug_ndjson_log(
        "H4",
        "fill_hero_insurance_service._run_hero_misp_portal_after_open",
        "after_sign_in_click_attempt",
        {"clicked": bool(clicked), "page_url": _sign_url, "locator_root_count_gt0": _has_root},
    )
    # endregion
    if not clicked:
        _hero_insurance_log_page_diagnostics(
            page,
            phase="sign_in_not_clicked",
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )
        logger.warning(
            "Hero Insurance: no Sign In / Login control was clicked — diagnostics logged (frames + visible controls)."
        )
    try:
        page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass
    _t(page, 1_200)

    try:
        _click_2w_icon(page, timeout_ms=timeout_ms)
    except Exception as exc:
        return f"2W Icon: {exc!s}"

    _t(page, 600)

    try:
        _click_new_policy(page, timeout_ms=timeout_ms)
    except Exception as exc:
        return f"New Policy: {exc!s}"

    if not values:
        logger.info("Hero Insurance: no DB values — stopping after New Policy.")
        return None

    _t(page, 900)
    err = _fill_insurance_company_and_ovd_mobile_consent(page, values, timeout_ms=timeout_ms)
    if err:
        return err

    _t(page, 500)
    err = _kyc_proceed_or_upload(page, timeout_ms=timeout_ms)
    if err:
        return err
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 4))
    except Exception:
        pass
    _t(page, 400)
    return None


def _select_by_label_fuzzy(
    page, label_pattern: str, query: str, *, timeout_ms: int
) -> bool:
    if not (query or "").strip():
        return False
    try:
        loc = page.get_by_label(re.compile(label_pattern, re.I))
        if loc.count() == 0:
            return False
        first = loc.first
        tag = (first.evaluate("el => (el && el.tagName) ? el.tagName : ''") or "").upper()
        if tag == "SELECT":
            return _select_option_fuzzy_in_select(page, loc, query, timeout_ms=timeout_ms)
    except Exception:
        pass
    return False


def _fill_input_by_label_patterns(
    page, label_patterns: tuple[str, ...], value: str, *, timeout_ms: int
) -> bool:
    if not (value or "").strip():
        return False
    for lp in label_patterns:
        try:
            loc = page.get_by_label(re.compile(lp, re.I))
            if loc.count() > 0 and loc.first.is_visible(timeout=2_000):
                loc.first.fill("", timeout=timeout_ms)
                loc.first.fill(value.strip(), timeout=timeout_ms)
                logger.info("Hero Insurance: filled field matching %r", lp[:48])
                return True
        except Exception:
            continue
    return False


def _hero_misp_vin_submit_i_agree(page, values: dict, *, timeout_ms: int) -> str | None:
    """After KYC **Proceed**: VIN page — fill ``full_chassis``, Submit, **I agree** popup."""
    vin = (values.get("full_chassis") or values.get("frame_no") or "").strip()
    if not vin:
        return "vehicle_master full_chassis/frame (VIN) is empty in DB values."

    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 4))
    except Exception:
        pass
    _t(page, 600)

    filled = False
    for loc in (
        page.get_by_label(re.compile(r"VIN|Chassis|Vehicle\s*Identification", re.I)),
        page.locator('input[name*="vin" i]'),
        page.locator('input[id*="vin" i]'),
        page.locator('input[placeholder*="VIN" i]'),
        page.get_by_placeholder(re.compile(r"chassis|vin|frame", re.I)),
    ):
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=3_000):
                loc.first.fill("", timeout=timeout_ms)
                loc.first.fill(vin[:64], timeout=timeout_ms)
                filled = True
                logger.info("Hero Insurance: filled VIN/Chassis field.")
                break
        except Exception:
            continue
    if not filled:
        return "Could not find VIN/Chassis input after KYC Proceed (expected redirect to VIN page)."

    try:
        sub = page.get_by_role("button", name=re.compile(r"^\s*Submit\s*$", re.I))
        if sub.count() > 0 and sub.first.is_visible(timeout=2_000):
            sub.first.click(timeout=timeout_ms)
        else:
            page.get_by_text(re.compile(r"^\s*Submit\s*$", re.I)).first.click(timeout=timeout_ms)
        logger.info("Hero Insurance: clicked Submit on VIN page.")
    except Exception as exc:
        return f"VIN Submit click failed: {exc!s}"

    _t(page, 800)
    agreed = False
    for _ in range(28):
        try:
            for role in ("button", "link"):
                b = page.get_by_role(role, name=re.compile(r"I\s*agree", re.I))
                if b.count() > 0:
                    for i in range(min(b.count(), 8)):
                        if b.nth(i).is_visible(timeout=600):
                            b.nth(i).click(timeout=timeout_ms)
                            agreed = True
                            logger.info("Hero Insurance: clicked I agree (%s).", role)
                            break
                if agreed:
                    break
        except Exception:
            pass
        if agreed:
            break
        _t(page, 350)

    if not agreed:
        return 'Could not find or click "I agree" after VIN Submit (popup/dialog).'

    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(30_000, timeout_ms * 5))
    except Exception:
        pass
    _t(page, 600)
    return None


def _set_checkbox_matching_text(
    page, text_pattern: str, want_checked: bool, *, timeout_ms: int
) -> None:
    """Toggle a checkbox whose label/row text matches ``text_pattern``."""
    try:
        cbs = page.locator('input[type="checkbox"]')
        n = cbs.count()
        rx = re.compile(text_pattern, re.I)
        for i in range(min(n, 120)):
            cb = cbs.nth(i)
            try:
                if not cb.is_visible(timeout=400):
                    continue
                t = ""
                cid = cb.get_attribute("id") or ""
                if cid:
                    lab = page.locator(f'label[for="{cid}"]')
                    if lab.count() > 0:
                        t = (lab.first.inner_text() or "")[:300]
                if not t.strip():
                    t = (cb.evaluate(
                        "e => (e.closest('label, tr, div') && e.closest('label, tr, div').innerText) || ''"
                    ) or "")[:400]
                if not rx.search(t):
                    continue
                if want_checked and not cb.is_checked():
                    cb.check(timeout=timeout_ms)
                    logger.info("Hero Insurance: checked addon matching %r", text_pattern[:40])
                elif not want_checked and cb.is_checked():
                    cb.uncheck(timeout=timeout_ms)
                    logger.info("Hero Insurance: unchecked addon matching %r", text_pattern[:40])
                return
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Hero Insurance: checkbox toggle %r: %s", text_pattern[:40], exc)


def _normalize_policy_num_for_db(raw: str) -> str | None:
    t = (raw or "").strip()
    if not t:
        return None
    t = re.sub(r"\s+", " ", t)
    if len(t) > 24:
        t = t[:24]
    return t or None


def _parse_currency_amount_text(raw: str) -> float | None:
    """Parse amounts like '₹ 4,523.00', 'Rs.1523', '1,234.5' to float."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    s = re.sub(r"^[₹Rs.,\sINR]+", "", s, flags=re.I)
    s = re.sub(r"[₹Rs.\s]+$", "", s, flags=re.I)
    s = s.replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def scrape_insurance_policy_preview_before_issue(page, *, timeout_ms: int) -> dict[str, Any]:
    """
    Read **policy number** and **insurance cost** (total premium) from the proposal/preview or
    post-**Issue Policy** confirmation — dummy IDs ``#ins-preview-policy-num`` /
    ``#ins-preview-insurance-cost``, then label/body heuristics for real MISP.
    """
    out: dict[str, Any] = {"policy_num": None, "insurance_cost": None}
    to = max(2_000, min(int(timeout_ms), 25_000))

    try:
        loc_p = page.locator("#ins-preview-policy-num")
        if loc_p.count() > 0 and loc_p.first.is_visible(timeout=min(4_000, to)):
            t = (loc_p.first.inner_text() or "").strip()
            pn = _normalize_policy_num_for_db(t)
            if pn:
                out["policy_num"] = pn
    except Exception as exc:
        logger.debug("Insurance preview scrape policy (dummy id): %s", exc)

    try:
        loc_c = page.locator("#ins-preview-insurance-cost")
        if loc_c.count() > 0 and loc_c.first.is_visible(timeout=min(4_000, to)):
            t = (loc_c.first.inner_text() or "").strip()
            amt = _parse_currency_amount_text(t)
            if amt is not None:
                out["insurance_cost"] = amt
    except Exception as exc:
        logger.debug("Insurance preview scrape cost (dummy id): %s", exc)

    # Nearby text for MISP-style labels (first matching row / cell)
    if not out["policy_num"]:
        for pat in (
            re.compile(r"Policy\s*(?:Number|No\.?)\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,31})", re.I),
            re.compile(r"Proposal\s*(?:Number|No\.?)\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,31})", re.I),
        ):
            try:
                loc = page.get_by_text(pat)
                if loc.count() > 0 and loc.first.is_visible(timeout=2_000):
                    m = pat.search((loc.first.inner_text() or "")[:300])
                    if m:
                        cand = _normalize_policy_num_for_db(m.group(1).strip())
                        if cand:
                            out["policy_num"] = cand
                            break
            except Exception:
                continue

    body = ""
    try:
        body = (page.locator("body").inner_text(timeout=min(12_000, to)) or "")[:150_000]
    except Exception:
        body = ""

    if not out["policy_num"] and body:
        m = re.search(
            r"(?:Policy|Proposal)\s*(?:Number|No\.?)\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,31})",
            body,
            re.I | re.M,
        )
        if m:
            out["policy_num"] = _normalize_policy_num_for_db(m.group(1))

    if out["insurance_cost"] is None and body:
        m = re.search(
            r"(?:Insurance\s*[Cc]ost|Total\s*(?:Policy\s*)?[Pp]remium|Net\s*[Pp]remium|Final\s*[Pp]remium|"
            r"Premium\s*(?:Amount|Paid|Payable)?|Amount\s*Payable)\s*[:\s]*\s*[₹RsINR.\s]*([\d][\d,]*(?:\.\d{1,2})?)",
            body,
            re.I | re.M,
        )
        if m:
            amt = _parse_currency_amount_text(m.group(1))
            if amt is not None:
                out["insurance_cost"] = amt

    if out["policy_num"] or out["insurance_cost"] is not None:
        logger.info(
            "Insurance policy preview scrape: policy_num=%r insurance_cost=%s",
            out["policy_num"],
            out["insurance_cost"],
        )
    return out


def click_issue_policy_and_scrape_preview(page, *, timeout_ms: int) -> dict[str, Any]:
    """
    Click **Issue Policy** (dummy ``#ins-issue-policy`` or MISP button / text), wait for navigation,
    then scrape ``policy_num`` and ``insurance_cost`` via ``scrape_insurance_policy_preview_before_issue``.
    """
    to = max(2_000, int(timeout_ms))
    clicked = False
    try:
        loc = page.locator("#ins-issue-policy")
        if loc.count() > 0 and loc.first.is_visible(timeout=min(4_000, to)):
            loc.first.click(timeout=to)
            clicked = True
            logger.info("Hero Insurance: clicked Issue Policy (#ins-issue-policy).")
    except Exception as exc:
        logger.debug("Hero Insurance: Issue Policy dummy selector: %s", exc)
    if not clicked:
        try:
            btn = page.get_by_role("button", name=re.compile(r"Issue\s*Policy", re.I))
            if btn.count() > 0 and btn.first.is_visible(timeout=min(4_000, to)):
                btn.first.click(timeout=to)
                clicked = True
                logger.info("Hero Insurance: clicked Issue Policy (role=button).")
        except Exception as exc:
            logger.debug("Hero Insurance: Issue Policy role=button: %s", exc)
    if not clicked:
        try:
            link = page.get_by_role("link", name=re.compile(r"Issue\s*Policy", re.I))
            if link.count() > 0 and link.first.is_visible(timeout=min(4_000, to)):
                link.first.click(timeout=to)
                clicked = True
                logger.info("Hero Insurance: clicked Issue Policy (role=link).")
        except Exception as exc:
            logger.debug("Hero Insurance: Issue Policy role=link: %s", exc)
    if not clicked:
        try:
            page.get_by_text(re.compile(r"^Issue\s*Policy", re.I)).first.click(timeout=min(8_000, to))
            clicked = True
            logger.info("Hero Insurance: clicked Issue Policy (get_by_text).")
        except Exception as exc:
            logger.debug("Hero Insurance: Issue Policy get_by_text: %s", exc)
    _t(page, 600)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, to * 4))
    except Exception:
        pass
    return scrape_insurance_policy_preview_before_issue(page, timeout_ms=to)


def _fill_date_of_registration_today(page, *, timeout_ms: int) -> None:
    """Hardcoded proposal default: **today** (local date)."""
    today = date.today()
    filled = False
    try:
        loc = page.get_by_label(re.compile(r"Date\s*of\s*Regist", re.I))
        if loc.count() > 0 and loc.first.is_visible(timeout=2_000):
            tag = (loc.first.evaluate("el => (el && el.tagName) ? el.tagName : ''") or "").upper()
            if tag == "INPUT":
                inp_type = (loc.first.get_attribute("type") or "").lower()
                if inp_type == "date":
                    loc.first.fill(today.isoformat(), timeout=timeout_ms)
                else:
                    loc.first.fill(today.strftime("%d/%m/%Y"), timeout=timeout_ms)
                filled = True
                logger.info("Hero Insurance: filled Date of registration (today, hardcoded).")
    except Exception:
        pass
    if filled:
        return
    try:
        generic = page.locator('input[type="date"]')
        if generic.count() > 0 and generic.first.is_visible(timeout=1_500):
            generic.first.fill(today.isoformat(), timeout=timeout_ms)
            logger.info("Hero Insurance: filled first date input with today.")
    except Exception:
        pass


def _hero_misp_fill_proposal_and_review(
    page, values: dict, *, timeout_ms: int
) -> tuple[str | None, dict[str, Any]]:
    """Proposal page after **I agree**: … → **Proposal Review** → scrape preview before Issue Policy."""
    pt = max(int(timeout_ms), int(INSURANCE_POLICY_FILL_TIMEOUT_MS))

    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(30_000, pt * 6))
    except Exception:
        pass
    _t(page, 500)

    ms = (values.get("marital_status") or "").strip()
    if ms:
        if not _select_by_label_fuzzy(page, r"Marital\s*Status", ms, timeout_ms=pt):
            _select_by_label_fuzzy(page, r"Marital", ms, timeout_ms=pt)

    prof = (values.get("profession") or "").strip()
    if prof:
        if not _select_by_label_fuzzy(page, r"Occupation\s*Type", prof, timeout_ms=pt):
            _select_by_label_fuzzy(page, r"Occupation", prof, timeout_ms=pt)

    email_fixed = "na@gmail.com"
    try:
        for loc in (
            page.get_by_label(re.compile(r"E-?mail", re.I)),
            page.locator('input[type="email"]'),
            page.locator('input[name*="email" i]'),
        ):
            if loc.count() > 0 and loc.first.is_visible(timeout=1_500):
                loc.first.fill("", timeout=pt)
                loc.first.fill(email_fixed, timeout=pt)
                logger.info("Hero Insurance: filled email (hardcoded fallback).")
                break
    except Exception as exc:
        logger.warning("Hero Insurance: email fill: %s", exc)

    city = (values.get("city") or "").strip()
    rto_query = city if city else "City"
    if not _select_by_label_fuzzy(page, r"RTO", rto_query, timeout_ms=pt):
        _select_by_label_fuzzy(page, r"Registering\s*Authority|R\.?T\.?O", rto_query, timeout_ms=pt)

    mname = (values.get("model_name") or "").strip()
    if mname:
        if not _select_by_label_fuzzy(page, r"Model\s*Name", mname, timeout_ms=pt):
            _select_by_label_fuzzy(page, r"Model", mname, timeout_ms=pt)

    _fill_date_of_registration_today(page, timeout_ms=pt)

    nn = (values.get("nominee_name") or "").strip()
    if nn:
        _fill_input_by_label_patterns(
            page, (r"Nominee\s*Name", r"Name\s*of\s*Nominee"), nn, timeout_ms=pt
        )
    na = (values.get("nominee_age") or "").strip()
    if na:
        _fill_input_by_label_patterns(page, (r"Nominee\s*Age", r"Age\s*of\s*Nominee"), na, timeout_ms=pt)

    ng = (values.get("nominee_gender") or "").strip()
    if ng:
        if not _select_by_label_fuzzy(page, r"Nominee.*Gender|Gender.*Nominee", ng, timeout_ms=pt):
            _select_by_label_fuzzy(page, r"Gender\s*\(?\s*Nominee", ng, timeout_ms=pt)

    rel = (values.get("nominee_relationship") or "").strip()
    if rel:
        if not _select_by_label_fuzzy(page, r"Relation", rel, timeout_ms=pt):
            _fill_input_by_label_patterns(page, (r"Relation\s*with|Nominee\s*Relation",), rel, timeout_ms=pt)

    fin = (values.get("financer_name") or "").strip()
    if fin:
        _fill_input_by_label_patterns(
            page,
            (r"Financier\s*Name", r"Financer\s*Name", r"Name\s*of\s*Financier"),
            fin,
            timeout_ms=pt,
        )

    branch_city = city
    if branch_city:
        _fill_input_by_label_patterns(
            page,
            (
                r"Finance\s*Company\s*Branch",
                r"Financier\s*Branch",
                r"Branch\s*Name",
                r"Finance\s*Branch",
            ),
            branch_city,
            timeout_ms=pt,
        )

    _t(page, 400)
    _set_checkbox_matching_text(page, r"ND\s*Cover", True, timeout_ms=pt)
    _set_checkbox_matching_text(page, r"RTI\s*cover|RTI\s*Cover", True, timeout_ms=pt)
    _set_checkbox_matching_text(page, r"^RSA$|RSA\s*cover|Road\s*Side", False, timeout_ms=pt)
    _set_checkbox_matching_text(page, r"Emergency\s*Medical", False, timeout_ms=pt)

    _fill_input_by_label_patterns(page, (r"CPA\s*Tenure", r"CPA"), "0", timeout_ms=pt)

    _t(page, 400)
    hdfc_ok = False
    try:
        hdfc = page.get_by_role("radio", name=re.compile(r"HDFC", re.I))
        if hdfc.count() > 0:
            for i in range(min(hdfc.count(), 6)):
                if hdfc.nth(i).is_visible(timeout=1_000):
                    hdfc.nth(i).check(timeout=pt)
                    hdfc_ok = True
                    logger.info("Hero Insurance: selected HDFC payment mode (hardcoded).")
                    break
    except Exception:
        pass
    if not hdfc_ok:
        try:
            lab = page.locator("label, span, div").filter(has_text=re.compile(r"HDFC", re.I)).first
            if lab.count() > 0 and lab.is_visible(timeout=1_500):
                lab.click(timeout=pt)
                logger.info("Hero Insurance: clicked HDFC payment option (label).")
        except Exception as exc:
            logger.warning("Hero Insurance: HDFC payment selection: %s", exc)

    _t(page, 500)
    try:
        rev = page.get_by_role("button", name=re.compile(r"Proposal\s*Review", re.I))
        if rev.count() > 0 and rev.first.is_visible(timeout=3_000):
            rev.first.click(timeout=pt)
            logger.info("Hero Insurance: clicked Proposal Review.")
        else:
            page.get_by_text(re.compile(r"Proposal\s*Review", re.I)).first.click(timeout=pt)
            logger.info("Hero Insurance: clicked Proposal Review (text).")
    except Exception as exc:
        return f"Proposal Review click failed: {exc!s}", {}

    _t(page, 600)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, pt * 5))
    except Exception:
        pass
    preview = scrape_insurance_policy_preview_before_issue(page, timeout_ms=pt)
    return None, preview


def pre_process(
    *,
    insurance_base_url: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    subfolder: str | None = None,
    ocr_output_dir: Path | None = None,
    staging_payload: dict | None = None,
) -> dict:
    """
    Open **``INSURANCE_BASE_URL``** (reuse tab / launch browser like Fill DMS).

    When **customer_id** and **vehicle_id** are set, loads insurer (details sheet / DB via
    ``_build_insurance_fill_values``) and runs: **Login / Sign In** (if visible) → **2W** (two-wheeler) → **New Policy** →
    Insurance Company (fuzzy LIKE insurer) → OVD **AADHAAR CARD** → **mobile_number** → consent →
    either **Proceed** if KYC already done, or upload Aadhaar front/back/photo placeholders and **Proceed** /
    Continue. Stops on the **VIN** entry page; **main_process** fills VIN and the rest.
    """
    def _login_url_and_match_base(config_url: str) -> tuple[str, str]:
        u = (config_url or "").strip()
        if not u.startswith("http"):
            u = "https://" + u.lstrip("/")
        p = urllib.parse.urlparse(u)
        if not p.netloc:
            raise ValueError("INSURANCE_BASE_URL must be a valid URL with a host")
        origin = f"{p.scheme}://{p.netloc}".rstrip("/")
        login_full = u.rstrip("/")
        return origin, login_full

    result: dict = {
        "success": False,
        "error": None,
        "page_url": None,
        "login_url": None,
        "match_base": None,
    }
    raw = (insurance_base_url or INSURANCE_BASE_URL or "").strip()
    if not raw:
        result["error"] = "Set INSURANCE_BASE_URL in backend/.env (or pass insurance_base_url)."
        return result
    try:
        match_base, login_url = _login_url_and_match_base(raw)
    except ValueError as e:
        result["error"] = str(e)
        return result

    result["login_url"] = login_url
    result["match_base"] = match_base

    # region agent log
    agent_debug_ndjson_log(
        "H1",
        "fill_hero_insurance_service.pre_process",
        "after_match_base",
        {
            "match_base": (match_base or "")[:160],
            "has_ocr_output_dir": bool(ocr_output_dir),
            "ocr_output_dir_suffix": str(ocr_output_dir)[-120:] if ocr_output_dir else None,
            "subfolder_repr": repr((subfolder or "")[:80]),
        },
    )
    # endregion

    reset_playwright_insurance_log(ocr_output_dir, subfolder)
    append_playwright_insurance_line(
        ocr_output_dir, subfolder, "NOTE", "pre_process: starting Hero Insurance (MISP) flow"
    )

    values: dict | None = None
    if customer_id is not None and vehicle_id is not None:
        try:
            values = build_insurance_fill_values(
                customer_id,
                vehicle_id,
                subfolder,
                ocr_output_dir=ocr_output_dir,
                staging_payload=staging_payload,
            )
        except Exception as exc:
            # region agent log
            agent_debug_ndjson_log(
                "H2",
                "fill_hero_insurance_service.pre_process",
                "build_insurance_fill_values_failed",
                {"exc": str(exc)[:400]},
            )
            # endregion
            result["error"] = str(exc)
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"pre_process: load DB values failed: {exc!s}"
            )
            return result

    page, open_error = get_or_open_site_page(
        match_base,
        "Insurance",
        require_login_on_open=True,
        launch_url=login_url,
    )
    if page is None:
        # region agent log
        agent_debug_ndjson_log(
            "H2",
            "fill_hero_insurance_service.pre_process",
            "get_or_open_site_page_failed",
            {"open_error": (open_error or "")[:300]},
        )
        # endregion
        result["error"] = open_error
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "NOTE", f"pre_process: could not open Insurance tab: {open_error}"
        )
        return result

    # region agent log
    try:
        _pu = (page.url or "").strip()
    except Exception as e:
        _pu = f"(url_error:{e!s})"
    agent_debug_ndjson_log(
        "H2",
        "fill_hero_insurance_service.pre_process",
        "page_opened",
        {"page_url": _pu[:400]},
    )
    # endregion

    # Same Playwright worker thread as main_process — hand off the Page so we do not call
    # ``get_or_open_site_page`` again (a second call may fail tab reuse and launch another window).
    result["_insurance_playwright_page"] = page

    to = INSURANCE_ACTION_TIMEOUT_MS
    page.set_default_timeout(to)
    append_playwright_insurance_line(
        ocr_output_dir, subfolder, "NOTE", "pre_process: browser tab ready, running Login/Sign In → 2W → New Policy → KYC"
    )

    try:
        step_err = _run_hero_misp_portal_after_open(
            page,
            values,
            timeout_ms=to,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )
        if step_err:
            result["error"] = step_err
            result["success"] = False
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"pre_process: stopped with error: {step_err}"
            )
        else:
            result["success"] = True
            result["error"] = None
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", "pre_process: completed — on VIN page (or equivalent)"
            )
    except PlaywrightTimeout as exc:
        result["success"] = False
        result["error"] = f"Timeout: {exc!s}"
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc)
    finally:
        try:
            page.set_default_timeout(15_000)
        except Exception:
            pass

    try:
        result["page_url"] = (page.url or "").strip() or None
    except Exception:
        result["page_url"] = None

    logger.info(
        "pre_process: match_base=%s login_url=%s success=%s url=%s",
        match_base,
        login_url,
        result.get("success"),
        (result.get("page_url") or "")[:160],
    )
    return result


def main_process(
    *,
    pre_result: dict,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    subfolder: str | None = None,
    ocr_output_dir: Path | None = None,
    staging_payload: dict | None = None,
) -> dict:
    """
    After **pre_process** (KYC **Proceed** → VIN page): fill VIN from DB (**``full_chassis``**),
    **Submit** → **I agree** → proposal form. **Customer/vehicle/nominee/financer** fields come from
    ``form_insurance_view`` / ``_build_insurance_fill_values``; **email, add-ons, CPA tenure, payment (HDFC),
    and registration date** use hardcoded defaults for now. **Proposal Review** at the end.
    Reuses the open Insurance tab via ``match_base`` from ``pre_result``.
    """
    out: dict = {
        "success": False,
        "skipped": False,
        "error": None,
        "page_url": None,
    }
    if not pre_result.get("success"):
        out["skipped"] = True
        out["error"] = pre_result.get("error")
        return out

    match_base = (pre_result.get("match_base") or "").strip()
    login_url = (pre_result.get("login_url") or "").strip() or None
    if not match_base:
        out["error"] = "pre_result missing match_base; cannot attach to Insurance tab."
        return out

    if customer_id is None or vehicle_id is None:
        out["error"] = "main_process requires customer_id and vehicle_id to load VIN and proposal fields."
        return out

    try:
        values = build_insurance_fill_values(
            customer_id,
            vehicle_id,
            subfolder,
            ocr_output_dir=ocr_output_dir,
            staging_payload=staging_payload,
        )
    except Exception as exc:
        out["error"] = str(exc)
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "NOTE", f"main_process: build_insurance_fill_values failed: {exc!s}"
        )
        return out

    append_playwright_insurance_line(
        ocr_output_dir, subfolder, "NOTE", "main_process: VIN → Submit → I agree → proposal form"
    )

    to = INSURANCE_ACTION_TIMEOUT_MS
    page = None
    pre_page = pre_result.get("_insurance_playwright_page")
    if pre_page is not None:
        try:
            if not pre_page.is_closed():
                page = pre_page
                logger.info(
                    "Hero Insurance main_process: reusing Playwright page from pre_process (avoids second browser/tab open)."
                )
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "main_process: attached to same browser tab as pre_process",
                )
        except Exception as exc:
            logger.warning("Hero Insurance main_process: could not reuse pre_process page: %s", exc)
            page = None

    if page is None:
        page, open_err = get_or_open_site_page(
            match_base,
            "Insurance",
            require_login_on_open=False,
            launch_url=login_url,
        )
        if page is None:
            out["error"] = open_err or "Insurance site tab not found after pre_process; keep the browser open."
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"main_process: tab not found: {open_err}"
            )
            return out

    try:
        page.set_default_timeout(to)
        err = _hero_misp_vin_submit_i_agree(page, values, timeout_ms=to)
        if err:
            out["error"] = err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"main_process: VIN/I agree step failed: {err}"
            )
            return out
        prop_err, preview = _hero_misp_fill_proposal_and_review(page, values, timeout_ms=to)
        if prop_err:
            out["error"] = prop_err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"main_process: proposal form failed: {prop_err}"
            )
            return out
        try:
            insert_insurance_master_after_gi(
                int(customer_id),
                int(vehicle_id),
                fill_values=values,
                staging_payload=staging_payload,
                preview_policy_num=preview.get("policy_num"),
                preview_insurance_cost=preview.get("insurance_cost"),
            )
        except ValueError as persist_exc:
            out["error"] = str(persist_exc)
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "ERROR", f"main_process: insurance_master insert failed: {persist_exc!s}"
            )
            return out
        except Exception as persist_exc:
            out["error"] = f"insurance_master insert failed: {persist_exc!s}"
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "ERROR", f"main_process: insurance_master insert failed: {persist_exc!s}"
            )
            return out
        post_issue = click_issue_policy_and_scrape_preview(page, timeout_ms=to)
        try:
            update_insurance_master_policy_after_issue(
                int(customer_id),
                int(vehicle_id),
                policy_num=post_issue.get("policy_num"),
                insurance_cost=post_issue.get("insurance_cost"),
            )
        except Exception as upd_exc:
            logger.warning("main_process: insurance_master post-issue update failed: %s", upd_exc)
        out["success"] = True
        out["error"] = None
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "main_process: completed — Proposal Review, insurance_master insert, Issue Policy + scrape",
        )
        try:
            out["page_url"] = (page.url or "").strip() or None
        except Exception:
            out["page_url"] = None
    except PlaywrightTimeout as exc:
        out["error"] = f"Timeout: {exc!s}"
    except Exception as exc:
        out["error"] = str(exc)
    finally:
        try:
            page.set_default_timeout(15_000)
        except Exception:
            pass

    return out


def post_process(*, pre_result: dict, main_result: dict) -> dict:
    """
    Finalize the hero-insurance request (logging hooks, response shape for API). Merges pre/main
    into the same contract as the former single-step flow.
    """
    try:
        pre_result.pop("_insurance_playwright_page", None)
    except Exception:
        pass
    ok = bool(pre_result.get("success")) and bool(main_result.get("success", True))
    err = pre_result.get("error") or main_result.get("error")
    if not ok and not err:
        err = main_result.get("error")
    return {
        "success": ok,
        "error": err,
        "page_url": main_result.get("page_url") or pre_result.get("page_url"),
        "login_url": pre_result.get("login_url"),
        "match_base": pre_result.get("match_base"),
    }


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


def _insurance_select_fuzzy(
    page,
    selector: str,
    query: str,
    *,
    timeout_ms: int | None = None,
) -> None:
    """Fuzzy-match ``query`` to a ``<select>`` option label (dummy insurer / OEM fields)."""
    if not (query or "").strip():
        return
    to = timeout_ms if timeout_ms is not None else INSURANCE_ACTION_TIMEOUT_MS
    sel = page.locator(selector).first
    sel.wait_for(state="attached", timeout=to)
    labels: list[str] = []
    try:
        raw = sel.locator("option").evaluate_all(
            "els => els.map(e => (e.textContent || '').trim()).filter(Boolean)"
        )
        labels = [str(x).strip() for x in (raw or []) if str(x).strip()]
    except Exception:
        n = sel.locator("option").count()
        for i in range(min(n, 400)):
            t = (sel.locator("option").nth(i).inner_text() or "").strip()
            if t:
                labels.append(t)
    pick = fuzzy_best_option_label(query, labels)
    if not pick:
        return
    sel.select_option(label=pick, timeout=to)


def run_fill_insurance_only(
    insurance_base_url: str,
    *,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    ocr_output_dir: Path | None = None,
    staging_payload: dict | None = None,
) -> dict:
    """
    Fill Insurance portal from DB-backed values (``INSURANCE_BASE_URL`` = production MISP or partner login).
    Flow: open **login** → operator signs in → wait for **KYC** → fill mobile → **Verify mobile** →
    if `need_docs`, attach three files → **Submit** (or legacy Proceed) → kyc-success → DMS entry → policy details.
    If KYC already on file for the mobile, consent + **Proceed** only.
    Uses ``require_login_on_open=False`` so one Fill Insurance run can wait for manual login (see INSURANCE_LOGIN_WAIT_MS).
    """
    result: dict = {"success": False, "error": None}
    reset_playwright_insurance_log(ocr_output_dir, subfolder)
    append_playwright_insurance_line(
        ocr_output_dir, subfolder, "NOTE", "run_fill_insurance_only: starting Fill Insurance flow"
    )
    if not insurance_base_url or not insurance_base_url.strip():
        result["error"] = "insurance_base_url required"
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "ERROR", "run_fill_insurance_only: insurance_base_url missing"
        )
        return result
    try:
        values = build_insurance_fill_values(
            customer_id,
            vehicle_id,
            subfolder,
            ocr_output_dir=ocr_output_dir,
            staging_payload=staging_payload,
        )
        page, open_error = get_or_open_site_page(
            insurance_base_url, "Insurance", require_login_on_open=False
        )
        if page is None:
            result["error"] = open_error
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"run_fill_insurance_only: could not open tab: {open_error}"
            )
            return result

        page.set_default_timeout(INSURANCE_ACTION_TIMEOUT_MS)
        # Real MISP (and similar): same automated Sign In / DIAG as Hero ``pre_process`` — this endpoint
        # previously only waited for manual login (_wait_for_insurance_kyc_after_login).
        _t(page, 500)
        _hero_insurance_log_page_diagnostics(
            page,
            phase="fill_insurance_only_before_sign_in",
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )
        _ins_clicked = _click_sign_in_if_visible(page, timeout_ms=INSURANCE_ACTION_TIMEOUT_MS)
        # region agent log
        agent_debug_ndjson_log(
            "H4",
            "fill_hero_insurance_service.run_fill_insurance_only",
            "after_sign_in_click_attempt",
            {"clicked": bool(_ins_clicked)},
        )
        # endregion
        if not _ins_clicked:
            _hero_insurance_log_page_diagnostics(
                page,
                phase="fill_insurance_only_sign_in_not_clicked",
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: Sign In not auto-clicked — see DIAG lines; complete login manually if needed.",
            )
        wait_err = _wait_for_insurance_kyc_after_login(page, insurance_base_url)
        if wait_err:
            result["error"] = wait_err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"run_fill_insurance_only: KYC wait failed: {wait_err}"
            )
            return result
        _insurance_select_fuzzy(page, "#ins-company", values["insurer"] or "")
        page.select_option("#ins-kyc-partner", label="Signzy")
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
            payloads = insurance_kyc_png_payloads()
            page.locator("#ins-aadhar-front").set_input_files(payloads[0])
            page.locator("#ins-aadhar-rear").set_input_files(payloads[1])
            page.locator("#ins-customer-photo").set_input_files(payloads[2])
        if page.locator("#ins-consent").count() > 0 and not page.is_checked("#ins-consent"):
            page.check("#ins-consent")
        if kyc_state == "need_docs":
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
        page.set_default_timeout(INSURANCE_POLICY_FILL_TIMEOUT_MS)

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
                page.fill("#ins-alt-phone", values["alt_phone_num"])
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

        preview = scrape_insurance_policy_preview_before_issue(
            page, timeout_ms=INSURANCE_POLICY_FILL_TIMEOUT_MS
        )

        try:
            page.set_default_timeout(15_000)
        except Exception:
            pass

        if ocr_output_dir is not None:
            write_insurance_form_values(
                ocr_output_dir=Path(ocr_output_dir),
                subfolder=values.get("subfolder") or subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                values=values,
            )
        if customer_id is not None and vehicle_id is not None:
            try:
                insert_insurance_master_after_gi(
                    int(customer_id),
                    int(vehicle_id),
                    fill_values=values,
                    staging_payload=staging_payload,
                    preview_policy_num=preview.get("policy_num"),
                    preview_insurance_cost=preview.get("insurance_cost"),
                )
            except ValueError as persist_exc:
                result["error"] = str(persist_exc)
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "ERROR",
                    f"run_fill_insurance_only: insurance_master insert failed: {persist_exc!s}",
                )
                return result
            except Exception as persist_exc:
                result["error"] = f"insurance_master insert failed: {persist_exc!s}"
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "ERROR",
                    f"run_fill_insurance_only: insurance_master insert failed: {persist_exc!s}",
                )
                return result
            post_issue = click_issue_policy_and_scrape_preview(
                page, timeout_ms=INSURANCE_POLICY_FILL_TIMEOUT_MS
            )
            try:
                update_insurance_master_policy_after_issue(
                    int(customer_id),
                    int(vehicle_id),
                    policy_num=post_issue.get("policy_num"),
                    insurance_cost=post_issue.get("insurance_cost"),
                )
            except Exception as upd_exc:
                logger.warning("run_fill_insurance_only: insurance_master post-issue update failed: %s", upd_exc)
        result["success"] = True
        result["error"] = None
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "run_fill_insurance_only: completed (preview insert, Issue Policy clicked, post-issue scrape)",
        )
        return result
    except PlaywrightTimeout as e:
        _p = locals().get("page")
        if _p is not None:
            try:
                _p.set_default_timeout(15_000)
            except Exception:
                pass
        result["error"] = f"Timeout: {e!s}"
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "ERROR", f"run_fill_insurance_only: Timeout: {e!s}"
        )
        return result
    except Exception as e:
        _p = locals().get("page")
        if _p is not None:
            try:
                _p.set_default_timeout(15_000)
            except Exception:
                pass
        result["error"] = str(e)
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "ERROR", f"run_fill_insurance_only: {e!s}"
        )
        return result
