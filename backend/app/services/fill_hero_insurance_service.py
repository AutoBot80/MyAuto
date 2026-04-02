"""
Hero Insurance (MISP) Playwright flow: **pre_process** ends after KYC **Proceed** (or upload + Proceed)
on the VIN page; **main_process** fills VIN from DB (**``full_chassis``** via ``form_insurance_view``),
**Submit**, **I agree**, then the proposal form. Proposer/vehicle/nominee fields come from the view;
email, add-ons, CPA, HDFC, and registration date use **hardcoded** defaults. **Proposal Review**, then **Issue Policy**; scrape **policy number** and **insurance cost** again and persist via ``update_insurance_master_policy_after_issue``.
Browser reuse uses ``handle_browser_opening.get_or_open_site_page`` with ``match_base`` from **pre_process**.
"""
import difflib
import logging
import re
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.config import (
    INSURANCE_ACTION_TIMEOUT_MS,
    INSURANCE_BASE_URL,
    INSURANCE_DIAG_FULL_CONTROL_SNAPSHOT,
    INSURANCE_KYC_NAV_SCRAPE,
    INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS,
    INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS,
    INSURANCE_LOGIN_WAIT_MS,
    INSURANCE_POLICY_FILL_TIMEOUT_MS,
    KYC_KEYBOARD_INSURER_ARROW_DOWN_MAX,
    KYC_KEYBOARD_OVD_ARROW_DOWN_MAX,
    KYC_KEYBOARD_TABS_INSURER_TO_OVD,
    KYC_KEYBOARD_TABS_MOBILE_TO_CONSENT,
    KYC_KEYBOARD_TABS_OVD_TO_MOBILE,
    KYC_KEYBOARD_TABS_TO_INSURANCE_FIELD,
    KYC_INSURER_DISPLAY_SEQUENCE_MIN,
    KYC_INSURER_FUZZY_MIN_SCORE,
    KYC_USE_KEYBOARD_EKYC_SOP,
    KYC_DEFAULT_KYC_PARTNER_LABEL,
)
from app.services.add_sales_commit_service import (
    insert_insurance_master_after_gi,
    update_insurance_master_policy_after_issue,
)
from app.services.handle_browser_opening import (
    _playwright_page_url_matches_site_base,
    get_or_open_site_page,
)
from app.services.insurance_form_values import (
    append_playwright_insurance_line,
    append_playwright_insurance_line_or_dealer_fallback,
    build_insurance_fill_values,
    reset_playwright_insurance_log,
    write_insurance_form_values,
)
from app.services.insurance_kyc_payloads import insurance_kyc_png_payloads
from app.services.utility_functions import fuzzy_best_option_label, normalize_for_fuzzy_match

logger = logging.getLogger(__name__)

# Native form submit for MISP partner login (in addition to button clicks).
_REQUEST_SUBMIT_PARTNER_PASSWORD_FORM_JS = """() => {
    const forms = document.querySelectorAll('form');
    for (const form of forms) {
        const pw = form.querySelector(
            'input[type="password"], input[autocomplete="current-password"]'
        );
        if (!pw || !String(pw.value || '').trim()) continue;
        const btns = form.querySelectorAll('button[type="submit"]');
        let hasSignIn = false;
        for (const b of btns) {
            const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
            if (/^sign\\s*in$/i.test(t)) { hasSignIn = true; break; }
        }
        if (!hasSignIn) continue;
        try { form.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
        if (typeof form.requestSubmit === 'function') {
            form.requestSubmit();
            return { ok: true, method: 'requestSubmit' };
        }
        return { ok: false, method: 'no_requestSubmit' };
    }
    return { ok: false, method: 'no_form' };
}"""

_PARTNER_LOGIN_POST_SUBMIT_SNAPSHOT_JS = """() => {
    let btn = '';
    const forms = document.querySelectorAll('form');
    for (const form of forms) {
        const pw = form.querySelector(
            'input[type="password"], input[autocomplete="current-password"]'
        );
        if (!pw || !String(pw.value || '').trim()) continue;
        for (const b of form.querySelectorAll('button[type="submit"]')) {
            btn = (b.innerText || '').replace(/\\s+/g, ' ').trim();
            if (btn) break;
        }
        if (btn) break;
    }
    const hints = [];
    const sel = '[role="alert"], .text-danger, .error-message, [class*="invalid"], [class*="error"]';
    document.querySelectorAll(sel).forEach((el) => {
        const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
        if (t && t.length > 2 && t.length < 320) hints.push(t);
    });
    return { btn: btn.slice(0, 120), hints: hints.slice(0, 5) };
}"""


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


def _hero_insurance_snapshots_equivalent(snap: list[dict], root_snap: list[dict]) -> bool:
    """True when #root scan matches the full frame (skip duplicate DIAG blocks on SPAs)."""
    if not root_snap:
        return True
    if len(snap) != len(root_snap):
        return False
    return snap == root_snap


def _hero_insurance_compact_frame_summary(snap: list[dict], *, max_parts: int = 14) -> str:
    """One-line summary for Playwright_insurance.txt (avoids huge per-control dict dumps)."""
    parts: list[str] = []
    for row in snap[:max_parts]:
        if not isinstance(row, dict):
            continue
        tag = (row.get("tag") or "").strip()
        typ = (row.get("type") or "").strip()
        text = (row.get("text") or "").strip().replace("\n", " ")[:48]
        href = (row.get("href") or "").strip()
        href_tail = ""
        if href and len(href) < 120:
            href_tail = href.split("/")[-1][:36]
        if text:
            piece = f"{tag}:{text}"
            if href_tail and href_tail not in text:
                piece += f"→{href_tail}"
            parts.append(piece)
        elif href_tail:
            parts.append(f"{tag}→{href_tail}")
        else:
            parts.append(f"{tag}/{typ or '-'}")
    extra = len(snap) - max_parts
    out = " | ".join(parts)
    if extra > 0:
        out += f" …+{extra} more"
    if len(out) > 900:
        out = out[:897] + "…"
    return out


def _hero_insurance_log_page_diagnostics(
    page,
    *,
    phase: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """Log URL, frame count, and visible control snapshot to logger and ``Playwright_insurance.txt``."""
    lines: list[str] = []
    full_controls = INSURANCE_DIAG_FULL_CONTROL_SNAPSHOT
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
        if full_controls:
            for j, row in enumerate(snap[:35]):
                lines.append(f"  [{j}] {row}")
            if root_snap and not _hero_insurance_snapshots_equivalent(snap, root_snap):
                lines.append(f"--- frame[{idx}] #root only: {len(root_snap)} controls ---")
                for j, row in enumerate(root_snap[:35]):
                    lines.append(f"  root[{j}] {row}")
            elif root_snap and _hero_insurance_snapshots_equivalent(snap, root_snap):
                lines.append(
                    f"  (#root same as frame — {len(root_snap)} controls, duplicate list omitted)"
                )
        else:
            lines.append(f"  summary: {_hero_insurance_compact_frame_summary(snap)}")
            if root_snap and not _hero_insurance_snapshots_equivalent(snap, root_snap):
                lines.append(f"  #root_summary: {_hero_insurance_compact_frame_summary(root_snap)}")
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
        "Hero Insurance DIAG phase=%s (see insurance log file%s)",
        phase,
        " — INSURANCE_DIAG_FULL_CONTROL_SNAPSHOT=1" if full_controls else "",
    )
    logger.info(
        "Hero Insurance diagnostics %s %ssnapshot:\n%s",
        phase,
        "full " if full_controls else "compact ",
        blob,
    )
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


def _kyc_nav_scrape_in_frame(fr) -> dict[str, Any]:
    """
    Collect viewport/document metrics, ``activeElement`` summary, visible interactive controls,
    and **all** native ``<select>`` elements (including hidden / off-screen) with computed style
    and option samples — for mapping OVD, Proposer, insurer ``ddl`` ids on MISP ``ekycpage``.
    """
    try:
        raw = fr.evaluate(
            """() => {
          const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0)
              return false;
            const r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) return false;
            const vw = window.innerWidth || 800;
            const vh = window.innerHeight || 600;
            return r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
          };
          const labelFor = (el) => {
            if (!el || !el.id) return '';
            try {
              const esc = (typeof CSS !== 'undefined' && CSS.escape) ? CSS.escape : (s) => String(s);
              const l = document.querySelector('label[for="' + esc(el.id) + '"]');
              if (l) return (l.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 96);
            } catch (e) {}
            return '';
          };
          const de = document.documentElement;
          const pageInfo = {
            readyState: document.readyState,
            clientW: de.clientWidth,
            clientH: de.clientHeight,
            scrollW: de.scrollWidth,
            scrollH: de.scrollHeight,
            innerW: window.innerWidth,
            innerH: window.innerHeight,
            url: (location.href || '').slice(0, 500),
            title: (document.title || '').slice(0, 220),
            forms: document.querySelectorAll('form').length,
          };
          const ae = document.activeElement;
          const active = ae
            ? {
                tag: (ae.tagName || '').toLowerCase(),
                type: String(ae.type || ''),
                id: String(ae.id || '').slice(0, 88),
                name: String(ae.name || '').slice(0, 64),
                role: String(ae.getAttribute('role') || ''),
                aria: String(ae.getAttribute('aria-label') || '').slice(0, 120),
              }
            : null;
          const sels = [
            'input:not([type="hidden"])',
            'select',
            'textarea',
            'button',
            '[role="button"]',
            '[role="combobox"]',
            '[role="listbox"]',
            '[role="checkbox"]',
            '[role="radio"]',
            'a[href]',
          ];
          const seen = new Set();
          const controls = [];
          for (const sel of sels) {
            document.querySelectorAll(sel).forEach((el) => {
              if (controls.length >= 96) return;
              if (!vis(el) || seen.has(el)) return;
              seen.add(el);
              const r = el.getBoundingClientRect();
              const tag = (el.tagName || '').toLowerCase();
              const typ = String(el.type || '');
              let val = String(el.value || '').trim();
              if (typ === 'password') val = '(password)';
              controls.push({
                tag,
                type: typ,
                role: String(el.getAttribute('role') || ''),
                id: String(el.id || '').slice(0, 80),
                name: String(el.name || '').slice(0, 60),
                placeholder: String(el.placeholder || '').slice(0, 80),
                aria: String(el.getAttribute('aria-label') || '').slice(0, 100),
                label: labelFor(el),
                text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                val: val.slice(0, 48),
                box: [
                  Math.round(r.left),
                  Math.round(r.top),
                  Math.round(r.width),
                  Math.round(r.height),
                ],
              });
            });
          }
          const allSelects = [];
          document.querySelectorAll('select').forEach((el, idx) => {
            if (allSelects.length >= 48) return;
            const st = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            const opts = el.querySelectorAll('option');
            const optLabels = [];
            for (let j = 0; j < Math.min(opts.length, 4); j++) {
              optLabels.push((opts[j].textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 56));
            }
            let formId = '';
            try {
              if (el.form && el.form.id) formId = String(el.form.id).slice(0, 72);
            } catch (e) {}
            allSelects.push({
              domIdx: idx,
              id: String(el.id || '').slice(0, 88),
              name: String(el.name || '').slice(0, 72),
              options: opts.length,
              display: String(st.display || ''),
              visibility: String(st.visibility || ''),
              opacity: String(st.opacity || ''),
              pos: String(st.position || ''),
              zIndex: String(st.zIndex || ''),
              box: [
                Math.round(r.left),
                Math.round(r.top),
                Math.round(r.width),
                Math.round(r.height),
              ],
              label: labelFor(el),
              aria: String(el.getAttribute('aria-label') || '').slice(0, 100),
              selectedValue: String(el.value || '').slice(0, 56),
              optionSample: optLabels.join(' | ').slice(0, 220),
              formId,
            });
          });
          return { pageInfo, active, controls, allSelects };
        }"""
        )
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _hero_insurance_format_kyc_nav_frame_lines(
    frame_idx: int, frame_url: str, data: dict[str, Any]
) -> list[str]:
    lines: list[str] = []
    fu = (frame_url or "")[:360]
    lines.append(f"--- kyc_nav frame[{frame_idx}] url={fu!r} ---")
    pi = data.get("pageInfo") if isinstance(data.get("pageInfo"), dict) else {}
    if pi:
        lines.append(
            "  doc: "
            f"ready={pi.get('readyState')!r} "
            f"client={pi.get('clientW')}x{pi.get('clientH')} "
            f"scroll={pi.get('scrollW')}x{pi.get('scrollH')} "
            f"inner={pi.get('innerW')}x{pi.get('innerH')} "
            f"forms={pi.get('forms')}"
        )
        if pi.get("title"):
            lines.append(f"  title: {str(pi.get('title'))[:200]!r}")
    act = data.get("active")
    if isinstance(act, dict) and act:
        lines.append(
            "  activeElement: "
            f"{act.get('tag')!r} type={act.get('type')!r} id={act.get('id')!r} "
            f"name={act.get('name')!r} role={act.get('role')!r} aria={str(act.get('aria') or '')[:100]!r}"
        )
    else:
        lines.append("  activeElement: (none)")
    ctrls = data.get("controls")
    if not isinstance(ctrls, list):
        lines.append("  visible_controls: (error or empty)")
    else:
        lines.append(f"  visible_controls={len(ctrls)}")
        for j, row in enumerate(ctrls[:96]):
            if not isinstance(row, dict):
                continue
            box = row.get("box") or []
            box_s = ",".join(str(x) for x in box) if isinstance(box, list) else ""
            parts = [
                f"[{j}]",
                str(row.get("tag") or ""),
                str(row.get("type") or ""),
            ]
            if row.get("role"):
                parts.append(f"role={row.get('role')}")
            if row.get("id"):
                parts.append(f"id={row.get('id')!r}")
            if row.get("name"):
                parts.append(f"name={row.get('name')!r}")
            if row.get("label"):
                parts.append(f"label={str(row.get('label'))[:72]!r}")
            if row.get("aria"):
                parts.append(f"aria={str(row.get('aria'))[:64]!r}")
            if row.get("placeholder"):
                parts.append(f"ph={str(row.get('placeholder'))[:48]!r}")
            if row.get("text") and len(str(row.get("text")).strip()) > 0:
                parts.append(f"text={str(row.get('text'))[:56]!r}")
            if row.get("val"):
                parts.append(f"val={str(row.get('val'))[:32]!r}")
            if box_s:
                parts.append(f"box=[{box_s}]")
            lines.append("  " + " ".join(parts))

    all_sel = data.get("allSelects")
    if isinstance(all_sel, list):
        if not all_sel:
            lines.append("  all_selects=0 (no <select> elements in document)")
        else:
            lines.append(
                f"  all_selects={len(all_sel)} "
                "(every native <select>; includes hidden/styled — map OVD/Proposer/Mobile)"
            )
            for j, row in enumerate(all_sel[:48]):
                if not isinstance(row, dict):
                    continue
                box = row.get("box") or []
                box_s = ",".join(str(x) for x in box) if isinstance(box, list) else ""
                parts = [
                    f"  sel[{j}]",
                    f"options={row.get('options')}",
                    f"display={row.get('display')!r}",
                    f"vis={row.get('visibility')!r}",
                    f"opac={row.get('opacity')!r}",
                    f"pos={row.get('pos')!r}",
                ]
                if row.get("id"):
                    parts.append(f"id={row.get('id')!r}")
                if row.get("name"):
                    parts.append(f"name={row.get('name')!r}")
                if row.get("formId"):
                    parts.append(f"formId={row.get('formId')!r}")
                if row.get("label"):
                    parts.append(f"label={str(row.get('label'))[:80]!r}")
                if row.get("aria"):
                    parts.append(f"aria={str(row.get('aria'))[:72]!r}")
                if row.get("selectedValue"):
                    parts.append(f"value={str(row.get('selectedValue'))[:40]!r}")
                if box_s:
                    parts.append(f"box=[{box_s}]")
                sample = row.get("optionSample")
                if sample:
                    parts.append(f"opt_sample={str(sample)[:180]!r}")
                lines.append(" ".join(parts))

    return lines


def _hero_insurance_log_kyc_navigation_scrape(
    page,
    *,
    phase: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """
    Log KYC-focused navigation scrape (page metrics + visible controls per frame) to logger and
    ``Playwright_insurance.txt`` as **DIAG** lines (``kyc_nav_scrape``).
    """
    if not INSURANCE_KYC_NAV_SCRAPE:
        return
    lines: list[str] = []
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        url = (page.url or "").strip()
    except Exception:
        url = ""
    lines.append(f"phase={phase!r} page_url={url[:520]!r} page_title={title[:220]!r}")
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    lines.append(f"frame_count={len(frames)}")
    for idx, fr in enumerate(frames):
        try:
            fu = fr.url or ""
        except Exception:
            fu = ""
        try:
            if fr.is_detached():
                lines.append(f"--- kyc_nav frame[{idx}] (detached) ---")
                continue
        except Exception:
            pass
        data = _kyc_nav_scrape_in_frame(fr)
        lines.extend(_hero_insurance_format_kyc_nav_frame_lines(idx, fu, data))
    blob = "\n".join(lines)
    if len(blob) > 22000:
        blob = blob[:22000] + "\n…(truncated)"
    logger.info(
        "Hero Insurance KYC nav scrape %s:\n%s",
        phase,
        blob[:4000] + ("…" if len(blob) > 4000 else ""),
    )
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "DIAG",
        f"kyc_nav_scrape {phase}: " + blob.replace("\n", " \\n "),
    )


def _hero_insurance_kyc_nav_after_insurer_commit(
    page,
    *,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """
    After insurer is committed (keyboard Enter or DOM ``select_option``), **Tab** off the field
    so the portal commits the value (ASP.NET / postback). This **always** runs — not only when
    DIAG logging is enabled (previously Tab was gated and runs without ``subfolder`` never blurred).

    When ``INSURANCE_KYC_NAV_SCRAPE`` is on and ``ocr_output_dir`` + ``subfolder`` are set, append
    ``kyc_nav_scrape_after_insurer`` / optional ``networkidle`` / ``kyc_nav_scrape_after_insurer_networkidle``.
    """
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _t(page, 450)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=6_000)
    except Exception:
        pass
    _t(page, 200)

    if not INSURANCE_KYC_NAV_SCRAPE or not ocr_output_dir or not str(subfolder or "").strip():
        return
    _hero_insurance_log_kyc_navigation_scrape(
        page,
        phase="kyc_nav_scrape_after_insurer",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    cap = max(0, int(INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS))
    if cap > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=cap)
        except Exception:
            logger.debug(
                "Hero Insurance: networkidle after insurer (timeout=%s ms) — continuing to DIAG scrape.",
                cap,
            )
    _t(page, 300)
    _hero_insurance_log_kyc_navigation_scrape(
        page,
        phase="kyc_nav_scrape_after_insurer_networkidle",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )


def _kyc_body_text_lower(root) -> str:
    try:
        return (root.locator("body").inner_text(timeout=3_000) or "").lower()
    except Exception:
        return ""


def _kyc_text_is_verified_aadhaar_proceed_policy_issuance_banner(text: str) -> bool:
    """
    Portal copy (MISP): ``KYC already verified against AADHAAR CARD No. <digits> , please proceed
    for policy issuance.`` — checkbox + **Proceed**; no document uploads on this branch.
    """
    s = (text or "").lower()
    if "kyc already verified" not in s:
        return False
    if "aadhaar" not in s:
        return False
    # Optional card number token (spacing/punctuation varies)
    if "aadhaar card" not in s and not re.search(r"aadhaar\s+card\s*no", s):
        return False
    if "please proceed" not in s or "policy issuance" not in s:
        # Rare variants
        if not re.search(r"proceed\s+for\s+policy\s+issuance", s):
            return False
    return True


def _kyc_banner_already_verified_aadhaar_visible(page) -> bool:
    """
    True when the verified-banner copy is present. On MISP this text appears **after** mobile number
    is entered (and the primary button label changes from **KYC Verification** to **Proceed**).

    If **not** present, the portal typically shows three **file** inputs (Aadhaar front, back, photo)
    — handled by ``_kyc_proceed_or_upload`` (upload then **Proceed**).
    """
    kyc_fr = _kyc_preferred_kyc_frame(page)
    t = _kyc_body_text_lower(kyc_fr)
    if _kyc_text_is_verified_aadhaar_proceed_policy_issuance_banner(t):
        return True
    try:
        pt = (page.locator("body").inner_text(timeout=2_000) or "").lower()
    except Exception:
        pt = ""
    return _kyc_text_is_verified_aadhaar_proceed_policy_issuance_banner(pt)


def _kyc_click_proceed_after_already_verified_banner(
    page,
    kyc_fr,
    *,
    timeout_ms: int,
) -> str | None:
    """
    Verified-banner path: consent checkbox, then **Proceed** (or equivalent CTA).
    Does not upload documents — uploads run only when this banner is absent (see ``_kyc_proceed_or_upload``).
    """
    _kyc_ensure_consent_checked_before_kyc_cta(page)
    to = min(int(timeout_ms), 45_000)
    # After mobile, portal shows **Proceed** (not **KYC Verification**). Prefer Proceed / policy issuance.
    name_patterns = (
        re.compile(r"^\s*Proceed\s*$", re.I),
        re.compile(r"policy\s*issuance", re.I),
        re.compile(r"^\s*Continue\s*$", re.I),
        re.compile(r"^\s*Submit\s*$", re.I),
    )
    for root in (kyc_fr, page):
        for pat in name_patterns:
            try:
                b = root.get_by_role("button", name=pat)
                if b.count() > 0 and b.first.is_visible(timeout=2_000):
                    b.first.click(timeout=to)
                    logger.info(
                        "Hero Insurance: already-verified branch — clicked button (%s).",
                        pat.pattern[:80],
                    )
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, to))
                    except Exception:
                        pass
                    _t(page, 400)
                    return None
            except Exception:
                continue
            try:
                ln = root.get_by_role("link", name=pat)
                if ln.count() > 0 and ln.first.is_visible(timeout=1_500):
                    ln.first.click(timeout=to)
                    logger.info(
                        "Hero Insurance: already-verified branch — clicked link (%s).",
                        pat.pattern[:80],
                    )
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, to))
                    except Exception:
                        pass
                    _t(page, 400)
                    return None
            except Exception:
                continue
    try:
        inp = kyc_fr.locator(
            'input[type="submit"][value*="Proceed" i], input[type="button"][value*="Proceed" i]'
        )
        if inp.count() > 0 and inp.first.is_visible(timeout=1_500):
            inp.first.click(timeout=to)
            logger.info("Hero Insurance: already-verified branch — clicked input Proceed.")
            return None
    except Exception:
        pass
    return (
        "KYC already verified (AADHAAR) banner visible but no Proceed / policy issuance / "
        "Continue / Submit control found."
    )


def _kyc_post_mobile_entry_branch(
    page,
    kyc_fr,
    *,
    timeout_ms: int,
) -> str | None:
    """
    Run **after** mobile is filled: blur field / wait for postback so the verified statement can appear
    and the CTA can switch to **Proceed**. Then either **consent + Proceed** (banner path) or
    **upload three files + consent + Proceed** (``_kyc_proceed_or_upload``).
    """
    to = min(int(timeout_ms), 120_000)
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _t(page, 500)
    try:
        page.wait_for_load_state("networkidle", timeout=min(12_000, to))
    except Exception:
        pass
    _t(page, 600)
    # Banner may render slightly after network idle; re-check once.
    if not _kyc_banner_already_verified_aadhaar_visible(page):
        _t(page, 900)
    if _kyc_banner_already_verified_aadhaar_visible(page):
        logger.info(
            "Hero Insurance: post-mobile — verified AADHAAR / policy issuance banner; "
            "consent then Proceed (CTA should read Proceed after mobile)."
        )
        return _kyc_click_proceed_after_already_verified_banner(page, kyc_fr, timeout_ms=to)

    logger.info(
        "Hero Insurance: post-mobile — no verified banner yet; three document uploads then Proceed."
    )
    return _kyc_proceed_or_upload(page, timeout_ms=to)


def _kyc_select_kyc_partner_if_available(
    page, kyc_fr, _values: dict, *, timeout_ms: int
) -> None:
    """
    **KYC Partner** is never changed by automation — the portal default (e.g. Signzy) stays selected.
    (Programmatic ``select_option`` caused extra postbacks / wrong next screen on some builds.)
    """
    logger.info(
        "Hero Insurance: KYC Partner left as portal default (automation does not change ddlkycPartner)."
    )


def _kyc_ensure_consent_checked_before_kyc_cta(page) -> None:
    """Ensure a consent / declaration checkbox is checked before **Proceed** (post-mobile KYC CTA)."""
    for root in (_kyc_preferred_kyc_frame(page), page):
        try:
            cbs = root.get_by_role("checkbox").filter(
                has_text=re.compile(
                    r"consent|agree|confirm|i\s*confirm|declare|accept|terms",
                    re.I,
                )
            )
            n = min(cbs.count(), 20)
            for i in range(n):
                cb = cbs.nth(i)
                try:
                    if cb.is_visible(timeout=900) and not cb.is_checked():
                        cb.check(timeout=6_000)
                        logger.info("Hero Insurance: checked consent checkbox before KYC primary CTA.")
                        return
                except Exception:
                    continue
        except Exception:
            pass
        try:
            loc = root.locator('input[type="checkbox"]:visible')
            n2 = min(loc.count(), 24)
            for i in range(n2):
                cb = loc.nth(i)
                try:
                    if not cb.is_visible(timeout=400):
                        continue
                    if cb.is_checked():
                        continue
                    cb.check(timeout=6_000)
                    logger.info(
                        "Hero Insurance: checked visible checkbox index %s before KYC primary CTA.",
                        i,
                    )
                    return
                except Exception:
                    continue
        except Exception:
            pass


def _hero_insurance_kyc_nav_after_kyc_partner_commit(
    page,
    *,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """After KYC Partner ``select_option``, wait and append ``kyc_nav_scrape_after_kyc_partner`` DIAG."""
    if not INSURANCE_KYC_NAV_SCRAPE:
        return
    if not ocr_output_dir or not str(subfolder or "").strip():
        return
    _t(page, 280)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8_000)
    except Exception:
        pass
    cap = max(0, int(INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS))
    if cap > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=cap)
        except Exception:
            logger.debug(
                "Hero Insurance: networkidle after KYC partner (timeout=%s ms) — continuing to DIAG scrape.",
                cap,
            )
    _t(page, 250)
    _hero_insurance_log_kyc_navigation_scrape(
        page,
        phase="kyc_nav_scrape_after_kyc_partner",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
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


def _wait_for_partner_login_password_filled(page, *, timeout_ms: int) -> bool:
    """
    Wait until a visible ``input[type="password"]`` has a **non-empty** value (autofill / operator).

    The **Partner Login** panel on ``/misp-partner-login`` is separate from the header **Login as** control;
    do not click **Sign In** until credentials are present.
    """
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        try:
            loc = page.locator('input[type="password"]')
            n = min(loc.count(), 8)
            for i in range(n):
                pw = loc.nth(i)
                try:
                    if not pw.is_visible(timeout=800):
                        continue
                    val = (pw.input_value() or "").strip()
                    if len(val) > 0:
                        logger.info(
                            "Hero Insurance: password field ready (value length=%s) — proceeding to Sign In.",
                            len(val),
                        )
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    logger.warning(
        "Hero Insurance: no non-empty password field within %s ms — not clicking Sign In.",
        timeout_ms,
    )
    return False


def _try_request_submit_partner_password_form(ctx) -> bool:
    """
    Fire ``form.requestSubmit()`` on the partner login form (non-empty password + **Sign In** submit).
    React/controlled forms often bind the submit handler on the form; this path can behave more like a manual submit than a raw ``.click()`` on the button.
    """
    try:
        r = ctx.evaluate(_REQUEST_SUBMIT_PARTNER_PASSWORD_FORM_JS)
        if isinstance(r, dict) and r.get("ok"):
            logger.info(
                "Hero Insurance: Partner login form native submit (%s).",
                r.get("method"),
            )
            return True
        if isinstance(r, dict) and r.get("method") == "no_requestSubmit":
            logger.debug(
                "Hero Insurance: partner login form has Sign In but no requestSubmit (browser path)."
            )
    except Exception as exc:
        logger.debug("Hero Insurance: requestSubmit evaluate: %s", exc)
    return False


def _snapshot_partner_login_frames(page) -> dict:
    """Submit button label + alert-like lines (main + child frames); no PII."""
    samples: list[dict] = []
    for ctx in _iter_page_and_child_frames(page):
        try:
            s = ctx.evaluate(_PARTNER_LOGIN_POST_SUBMIT_SNAPSHOT_JS)
            if isinstance(s, dict) and (s.get("btn") or s.get("hints")):
                samples.append(
                    {
                        "ctx": type(ctx).__name__,
                        "btn": s.get("btn"),
                        "hints": s.get("hints"),
                    }
                )
        except Exception:
            continue
    return {"frames": samples}


def _try_dom_click_sign_in_submit(page) -> bool:
    """Click **Sign In** only inside a ``<form>`` that already has a **non-empty** password value."""
    try:
        ok = page.evaluate(
            """() => {
            function clickSignInInForm(form) {
                const pw = form.querySelector('input[type="password"]');
                if (!pw || !String(pw.value || '').trim()) return false;
                const btns = form.querySelectorAll('button[type="submit"]');
                for (const b of btns) {
                    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (/^sign\\s*in$/i.test(t)) {
                        try { b.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                        b.click();
                        return { ok: true, text: t, via: 'form' };
                    }
                }
                return false;
            }
            const forms = document.querySelectorAll('form');
            for (const form of forms) {
                const r = clickSignInInForm(form);
                if (r) return r;
            }
            const pws = document.querySelectorAll('input[type="password"]');
            for (const pw of pws) {
                if (!String(pw.value || '').trim()) continue;
                let el = pw;
                for (let d = 0; d < 12 && el; d++) {
                    el = el.parentElement;
                    if (!el) break;
                    const btn = el.querySelector('button[type="submit"]');
                    if (btn) {
                        const t = (btn.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (/^sign\\s*in$/i.test(t)) {
                            try { btn.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            btn.click();
                            return { ok: true, text: t, via: 'ancestor' };
                        }
                    }
                }
            }
            return { ok: false, text: '', via: '' };
        }"""
        )
        if isinstance(ok, dict) and ok.get("ok"):
            logger.info(
                "Hero Insurance: Sign In via DOM (%s, text=%r).",
                ok.get("via"),
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
        login_forms = scope.locator(
            'form:has(input[type="password"]), '
            'form:has(input[autocomplete="current-password"]), '
            'form:has(input[name*="password" i])'
        )
        if login_forms.count() == 0:
            return False
        patterns = (
            (re.compile(r"^\s*Sign\s*In\s*$", re.I), "Sign In"),
            (re.compile(r"^\s*Login\s*$", re.I), "Login"),
            (re.compile(r"^\s*Log\s+in\s*$", re.I), "Log in"),
        )
        for fi in range(min(login_forms.count(), 12)):
            frm = login_forms.nth(fi)
            try:
                pwin = frm.locator('input[type="password"]').first
                if pwin.count() == 0:
                    continue
                if not (pwin.input_value() or "").strip():
                    continue
            except Exception:
                continue
            for pat, dbg in patterns:
                for sel in ('button[type="submit"]', 'input[type="submit"]'):
                    try:
                        loc = frm.locator(sel).filter(has_text=pat)
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
                            "Hero Insurance: clicked %s (%s in password form[%s]) scope=%s.",
                            dbg,
                            sel,
                            fi,
                            scope_label,
                        )
                        return True
                    except Exception:
                        continue
            try:
                rb = frm.get_by_role(
                    "button",
                    name=re.compile(r"^\s*(Sign\s*In|Login|Log\s*in)\s*$", re.I),
                )
                if rb.count() > 0 and rb.first.is_visible(timeout=2_000):
                    rb.first.scroll_into_view_if_needed(timeout=4_000)
                    rb.first.click(timeout=timeout_ms)
                    logger.info(
                        "Hero Insurance: clicked login role=button in password form[%s] scope=%s.",
                        fi,
                        scope_label,
                    )
                    return True
            except Exception:
                continue
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


def _attempt_sign_in_click_once(page, *, timeout_ms: int) -> bool:
    """One pass: native form submit per frame, then scoped clicks, then main-document DOM click."""
    for ctx in _iter_page_and_child_frames(page):
        if _try_request_submit_partner_password_form(ctx):
            return True
    for ctx in _iter_page_and_child_frames(page):
        if _click_sign_in_on_context(ctx, timeout_ms=timeout_ms):
            return True
    return bool(_try_dom_click_sign_in_submit(page))


def _still_on_heroinsurance_misp_partner_login(page) -> bool:
    try:
        return "misp-partner-login" in (page.url or "").lower()
    except Exception:
        return False


def _click_sign_in_if_visible(page, *, timeout_ms: int) -> bool:
    """
    Click landing-page login CTA (``Sign In``, ``Login``, ``Log in``).
    Hero MISP login form uses ``<button type="submit">`` with label **Sign In**; landing pages may use **Login** only.
    Tries the **main document** and each **child frame** (login may render inside an iframe).
    Returns True only when navigation off **misp-partner-login** succeeds (or non-MISP URL after click).

    Logs often showed ``clicked: true`` while the user still saw the login screen — up to **4** attempts with
    **500 ms** between tries, and a post-click URL check on the partner login host.
    """
    try:
        u0 = (page.url or "").strip()
        if _tab_url_is_dms_siebel_not_insurance(u0):
            logger.warning(
                "Hero Insurance: not clicking login on this tab — URL looks like Siebel/DMS, not the MISP insurance portal (%s). "
                "Open or switch to the insurance site tab.",
                u0[:180],
            )
            return False
    except Exception:
        pass
    wait_ms = max(int(INSURANCE_LOGIN_WAIT_MS), int(timeout_ms))
    pwd_ready = _wait_for_partner_login_password_filled(page, timeout_ms=wait_ms)
    if not pwd_ready:
        return False

    max_attempts = 4
    pause_ms = 500
    for attempt in range(1, max_attempts + 1):
        clicked = _attempt_sign_in_click_once(page, timeout_ms=timeout_ms)
        try:
            url_snip = (page.url or "")[:160]
        except Exception:
            url_snip = ""
        if clicked:
            try:
                page.wait_for_timeout(pause_ms)
            except Exception:
                time.sleep(pause_ms / 1000.0)
            try:
                url_snip2 = (page.url or "")[:160]
            except Exception:
                url_snip2 = ""
            on_misp = _still_on_heroinsurance_misp_partner_login(page)
            if not on_misp:
                logger.info(
                    "Hero Insurance: Sign In succeeded (left misp-partner-login) on attempt %s/%s.",
                    attempt,
                    max_attempts,
                )
                return True
            try:
                page.wait_for_timeout(2200)
            except Exception:
                time.sleep(2.2)
            try:
                post_ui = _snapshot_partner_login_frames(page)
                hints = []
                for fr in (post_ui.get("frames") or []) if isinstance(post_ui, dict) else []:
                    hints.extend(fr.get("hints") or [])
                if hints:
                    logger.warning(
                        "Hero Insurance: still on misp-partner-login after Sign In — UI hints: %s",
                        "; ".join(hints[:3]),
                    )
            except Exception:
                pass
            logger.warning(
                "Hero Insurance: Sign In reported a click but still on misp-partner-login "
                "(attempt %s/%s) — retrying after %s ms.",
                attempt,
                max_attempts,
                pause_ms,
            )
        if attempt < max_attempts:
            try:
                page.wait_for_timeout(pause_ms)
            except Exception:
                time.sleep(pause_ms / 1000.0)

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


def _expand_misp_policy_issuance_nav_if_collapsed(page, *, timeout_ms: int) -> None:
    """
    MISP vertical nav: **New Policy** sits under **Policy Issuance**, collapsed by default
    (``#navbarVerticalNav``, ``data-tooltip="Policy Issuance"``, ``aria-expanded="false"``).
    Expand that section so **New Policy** is visible.
    """
    to = min(max(3_000, int(timeout_ms)), 35_000)
    try:
        nav = page.locator("#navbarVerticalNav").first
        if nav.count() == 0:
            return
    except Exception:
        return

    trig = nav.locator('[data-tooltip="Policy Issuance"]').first
    if trig.count() == 0:
        trig = nav.locator("[data-tooltip*='Policy Issuance' i]").first
    if trig.count() == 0:
        try:
            alt = nav.get_by_text(re.compile(r"Policy\s*Issuance", re.I)).first
            if alt.count() > 0:
                trig = alt
        except Exception:
            pass

    try:
        if trig.count() == 0:
            logger.debug(
                "Hero Insurance: Policy Issuance nav trigger not found under #navbarVerticalNav — "
                "continuing (New Policy may already be visible)."
            )
            return
    except Exception:
        return

    try:
        aria = trig.get_attribute("aria-expanded")
        if aria is None:
            aria = trig.evaluate(
                """el => {
                    const n = el.closest('[aria-expanded]');
                    return n ? n.getAttribute('aria-expanded') : null;
                }"""
            )
        if (aria or "").strip().lower() == "true":
            logger.info("Hero Insurance: Policy Issuance nav already expanded.")
            return
    except Exception:
        pass

    try:
        trig.scroll_into_view_if_needed(timeout=5_000)
    except Exception:
        pass
    try:
        trig.click(timeout=to)
        logger.info("Hero Insurance: expanded Policy Issuance (navbarVerticalNav) for New Policy.")
    except Exception as exc:
        logger.warning("Hero Insurance: Policy Issuance expand click failed: %s", exc)
        return

    try:
        page.wait_for_timeout(450)
    except Exception:
        time.sleep(0.45)


def _click_new_policy(page, *, timeout_ms: int) -> None:
    _expand_misp_policy_issuance_nav_if_collapsed(page, timeout_ms=timeout_ms)
    loc = page.get_by_text("New Policy", exact=True)
    loc.first.wait_for(state="visible", timeout=timeout_ms)
    loc.first.click(timeout=timeout_ms)
    logger.info("Hero Insurance: clicked New Policy.")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(25_000, timeout_ms * 2))
    except Exception:
        pass


def _misp_snapshot_context_pages(page) -> list:
    """Copy of ``context.pages`` for before/after tab detection (MISP opens 2W / flows in new tabs)."""
    try:
        return list(page.context.pages)
    except Exception:
        return []


def _tab_url_is_dms_siebel_not_insurance(url: str) -> bool:
    """When DMS and Insurance share one browser, never follow Siebel / Hero Connect tabs for MISP steps."""
    u = (url or "").lower()
    return (
        "swecmd=" in u
        or "/siebel/" in u
        or "connect.heromotocorp.biz" in u
        or "heroconnect" in u
        or "edealerhmcl" in u
    )


def _misp_resolve_page_after_possible_new_tab(
    pages_before: list,
    fallback_page,
    *,
    portal_base_url: str,
    timeout_ms: int,
    step_label: str,
):
    """
    Hero MISP often opens the **2W** product path in a **new** tab; **New Policy** and **KYC** follow.
    Prefer a **new** ``Page`` whose URL matches ``portal_base_url`` (insurance/MISP host). **Never** attach
    to a Siebel/DMS tab — those can appear in ``context.pages`` when the operator has both sites open.
    """
    base = (portal_base_url or "").strip()
    if not base:
        base = (INSURANCE_BASE_URL or "").strip()
    ctx = fallback_page.context
    cap_ms = min(max(5_000, int(timeout_ms)), 45_000)
    deadline = time.monotonic() + cap_ms / 1000.0
    before = list(pages_before)
    while time.monotonic() < deadline:
        try:
            for p in ctx.pages:
                if p in before:
                    continue
                try:
                    if p.is_closed():
                        continue
                except Exception:
                    continue
                try:
                    p.wait_for_load_state("domcontentloaded", timeout=min(15_000, cap_ms))
                except Exception:
                    pass
                try:
                    u = (p.url or "").strip()
                except Exception:
                    u = ""
                if _tab_url_is_dms_siebel_not_insurance(u):
                    logger.info(
                        "Hero Insurance: ignoring Siebel/DMS tab after %s — url=%s",
                        step_label,
                        u[:140],
                    )
                    continue
                low = (u or "").lower()
                if not u or "about:blank" in low:
                    continue
                if u and _playwright_page_url_matches_site_base(u, base):
                    logger.info(
                        "Hero Insurance: using new insurance/MISP tab after %s — url=%s",
                        step_label,
                        u[:200],
                    )
                    return p
        except Exception:
            pass
        try:
            time.sleep(0.12)
        except Exception:
            pass
    try:
        if not fallback_page.is_closed():
            fu = ""
            try:
                fu = (fallback_page.url or "").strip()
            except Exception:
                pass
            logger.info(
                "Hero Insurance: no new insurance tab after %s — staying on same page (url=%s).",
                step_label,
                fu[:180],
            )
            return fallback_page
    except Exception:
        pass
    return fallback_page


def _select_option_fuzzy_in_select(
    page,
    select_locator,
    query: str,
    *,
    timeout_ms: int,
    fuzzy_min_score: float = 0.42,
) -> bool:
    if not (query or "").strip():
        return False
    try:
        sel = select_locator.first
        if sel.count() == 0:
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
        if not labels:
            return False
        pick = fuzzy_best_option_label(query, labels, min_score=fuzzy_min_score)
        if not pick:
            return False
        try:
            visible = sel.is_visible(timeout=1_200)
        except Exception:
            visible = False
        try:
            if visible:
                sel.select_option(label=pick, timeout=timeout_ms)
            else:
                # ASP.NET / skinned KYC often hides the native <select> while showing a custom face.
                sel.select_option(label=pick, timeout=timeout_ms, force=True)
        except Exception:
            sel.select_option(label=pick, timeout=timeout_ms, force=True)
        logger.info("Hero Insurance: selected option %r (fuzzy from %r)", pick, query[:60])
        return True
    except Exception as exc:
        logger.warning("Hero Insurance: fuzzy select failed: %s", exc)
        return False


def _fill_insurance_company_fuzzy_any_visible_select(
    page, insurer: str, *, timeout_ms: int
) -> bool:
    """
    Try every ``<select>`` on the page whose option list fuzzy-matches the insurer.
    KYC layouts (e.g. ``ekycpage.aspx``) often break ``select:near(:text(...))``; the Insurance
    Company control may still be a native ``select`` (possibly hidden).
    """
    if not (insurer or "").strip():
        return False
    try:
        selects = page.locator("select")
        n = min(selects.count(), 40)
    except Exception:
        return False
    for i in range(n):
        try:
            loc = selects.nth(i)
            if _select_option_fuzzy_in_select(
                page,
                loc,
                insurer,
                timeout_ms=timeout_ms,
                fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
            ):
                logger.info("Hero Insurance: insurer set via select index %s (fuzzy scan).", i)
                return True
        except Exception:
            continue
    return False


def _insurer_type_query_variants(insurer: str) -> list[str]:
    """Short search strings for typeahead (portal may filter on prefix; DB name may be longer)."""
    s = (insurer or "").strip()
    if not s:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(x: str) -> None:
        t = x.strip()
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)

    add(s[:80])
    words = s.split()
    if len(words) > 3:
        add(" ".join(words[:3]))
    if len(words) > 2:
        add(" ".join(words[:2]))
    if len(words) > 1:
        add(words[0])
    return out


def _kyc_collect_dropdown_option_texts(root) -> list[str]:
    """Collect visible option strings; ``root`` is a **Page** or **Frame** (KYC list may be in an iframe)."""
    texts: list[str] = []
    try:
        root.wait_for_selector(
            "[role='option'], [role='listbox'] [role='option'], li[role='option'], .ui-menu-item, "
            "ul.dropdown-menu li, div.select2-results__option, .chosen-results li",
            timeout=2_800,
        )
    except Exception:
        pass
    for sel in (
        "[role='listbox'] [role='option']",
        "[role='option']",
        "li[role='option']",
        ".ui-menu-item",
        "ul[role='listbox'] li",
        "ul.dropdown-menu li",
        "ul.dropdown-menu a",
        "div.select2-results__option",
        ".chosen-results li",
    ):
        try:
            loc = root.locator(sel)
            n = min(loc.count(), 250)
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if not el.is_visible(timeout=400):
                        continue
                    t = (el.inner_text() or "").strip()
                    if t and t not in texts:
                        texts.append(t)
                except Exception:
                    continue
        except Exception:
            continue
    return texts


def _click_role_option_matching(page, pick: str, *, timeout_ms: int) -> bool:
    """Click a visible listbox option whose text matches the fuzzy-picked label."""
    if not (pick or "").strip():
        return False
    try:
        cand = page.get_by_text(pick, exact=True)
        n_c = min(cand.count(), 8)
        for i in range(n_c):
            try:
                el = cand.nth(i)
                if el.is_visible(timeout=1_200):
                    el.click(timeout=timeout_ms)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    want = normalize_for_fuzzy_match(pick)
    loc = page.locator(
        "[role='option'], li[role='option'], ul.dropdown-menu li, "
        "div.select2-results__option, .chosen-results li"
    )
    n = min(loc.count(), 250)
    for i in range(n):
        try:
            el = loc.nth(i)
            if not el.is_visible(timeout=800):
                continue
            t = (el.inner_text() or "").strip()
            if normalize_for_fuzzy_match(t) == want or t.strip() == pick.strip():
                el.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    pl = pick.lower()
    for i in range(n):
        try:
            el = loc.nth(i)
            if not el.is_visible(timeout=400):
                continue
            t = (el.inner_text() or "").strip()
            tl = t.lower()
            if pl in tl or tl in pl:
                el.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _locator_insurance_company_text_control(page):
    """
    KYC 'Insurance Company' is often the first field; avoid grabbing another combobox
    (e.g. KYC Partner) by using label / combobox name first.
    """
    try:
        by_label = page.get_by_label(re.compile(r"Insurance\s*Company\s*\*?", re.I))
        if by_label.count() > 0 and by_label.first.is_visible(timeout=2_000):
            return by_label.first
    except Exception:
        pass
    try:
        by_role = page.get_by_role("combobox", name=re.compile(r"Insurance\s*Company", re.I))
        if by_role.count() > 0 and by_role.first.is_visible(timeout=2_000):
            return by_role.first
    except Exception:
        pass
    try:
        lab = page.locator("label").filter(has_text=re.compile(r"Insurance\s*Company", re.I)).first
        if lab.count() > 0:
            inp = lab.locator("xpath=following::input[not(@type='hidden')][not(@type='file')][1]")
            if inp.count() > 0 and inp.first.is_visible(timeout=2_000):
                return inp.first
    except Exception:
        pass
    try:
        for narrow in ("tr", ".form-group", ".form-row", "div[class*='field']", "td"):
            sec = page.locator(narrow).filter(has_text=re.compile(r"Insurance\s*Company", re.I)).first
            if sec.count() == 0:
                continue
            inp = sec.locator(
                "input:not([type='hidden']):not([type='file']), [role='combobox'] input"
            ).first
            if inp.count() > 0 and inp.first.is_visible(timeout=1_500):
                return inp.first
    except Exception:
        pass
    return None


def _fill_insurance_company_typeahead_fuzzy(page, insurer: str, *, timeout_ms: int) -> bool:
    """
    Combobox / typeahead: type search variants, then pick the option that best matches
    the full details-sheet insurer string (same as native <select> fuzzy).
    """
    if not (insurer or "").strip():
        return False
    ctrl = _locator_insurance_company_text_control(page)
    if ctrl is None:
        try:
            legacy = page.locator(
                "input[placeholder*='Search' i], input[type='search'], "
                "[role='combobox'] input, input[aria-autocomplete='list']"
            ).first
            if legacy.count() > 0 and legacy.is_visible(timeout=2_000):
                ctrl = legacy
        except Exception:
            pass
    if ctrl is None:
        return False
    try:
        tag = (ctrl.evaluate("el => (el && el.tagName) ? el.tagName : ''") or "").upper()
        if tag == "SELECT":
            return _select_option_fuzzy_in_select(
                page,
                ctrl,
                insurer,
                timeout_ms=timeout_ms,
                fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
            )
    except Exception:
        pass
    try:
        lab = page.get_by_text(re.compile(r"Insurance\s*Company", re.I)).first
        if lab.count() > 0:
            lab.click(timeout=5_000)
            _t(page, 200)
    except Exception:
        pass
    for q in _insurer_type_query_variants(insurer):
        try:
            ctrl.click(timeout=5_000)
            _t(page, 150)
            ctrl.fill("", timeout=timeout_ms)
            ctrl.type(q, delay=25)
            _t(page, 550)
            opts = _kyc_collect_dropdown_option_texts(page)
            if not opts:
                continue
            pick = fuzzy_best_option_label(
                insurer, opts, min_score=KYC_INSURER_FUZZY_MIN_SCORE
            )
            if pick and _click_role_option_matching(page, pick, timeout_ms=timeout_ms):
                logger.info(
                    "Hero Insurance: insurer chosen via typeahead fuzzy (%r → %r).",
                    insurer[:60],
                    pick[:80],
                )
                return True
        except Exception as exc:
            logger.debug("Hero Insurance: insurer typeahead attempt %r: %s", q[:40], exc)
            continue
    return False


def _kyc_url_looks_like_ekyc_page(page) -> bool:
    """
    True when the real MISP KYC step should use the keyboard SOP (click → Tab chain).
    Keep in sync with ``_insurance_kyc_screen_ready_js`` URL hints so we do not wait for
    ``kycpage.aspx`` / ``/ekyc`` and then fill via DOM-only paths.
    """
    try:
        u = (page.url or "").lower()
        if "policy.html" in u or "misppolicy" in u:
            return False
        return (
            "kyc.html" in u
            or "ekycpage" in u
            or "kycpage.aspx" in u
            or "/ekyc" in u
            or "/apps/kyc/" in u
        )
    except Exception:
        return False


def _insurance_page_has_dummy_kyc_training_html(page) -> bool:
    """Training-only markup (``#ins-company``). Real MISP ``ekycpage`` does not expose these ids."""
    try:
        return page.locator("#ins-company").count() > 0
    except Exception:
        return False


def _kyc_preferred_kyc_frame(page):
    """Frame whose document contains the Insurance Company label (KYC may render inside an iframe)."""
    for fr in page.frames:
        try:
            if fr.is_detached():
                continue
            if fr.get_by_text(re.compile(r"Insurance\s*Company", re.I)).count() > 0:
                return fr
        except Exception:
            continue
    return page.main_frame


def _kyc_read_focused_control_text(page) -> str:
    """``activeElement`` text in the KYC form frame (main-only ``evaluate`` misses iframe focus)."""
    fr = _kyc_preferred_kyc_frame(page)
    try:
        raw = fr.evaluate(
            """() => {
          const a = document.activeElement;
          if (!a) return '';
          if (a.tagName === 'SELECT' && a.selectedIndex >= 0)
            return (a.options[a.selectedIndex].textContent || '').trim();
          return (a.value || a.innerText || '').trim().slice(0, 240);
        }"""
        )
        return (raw or "").strip()
    except Exception:
        return ""


def _kyc_insurer_display_matches(insurer: str, displayed: str) -> bool:
    """
    True when details-sheet ``insurer`` matches portal display text (keyboard SOP / focused control).

    Uses a **lower** ``min_score`` than generic dropdown matching (see ``KYC_INSURER_FUZZY_MIN_SCORE``)
    plus a **SequenceMatcher** fallback for DB typos vs full insurer legal names on MISP.
    """
    d = (displayed or "").strip()
    if not d or d.lower().startswith("--select"):
        return False
    pick = fuzzy_best_option_label(
        insurer, [d], min_score=KYC_INSURER_FUZZY_MIN_SCORE
    )
    if pick:
        return True
    qi = normalize_for_fuzzy_match(insurer)
    di = normalize_for_fuzzy_match(d)
    if not qi or not di:
        return False
    if difflib.SequenceMatcher(None, qi, di).ratio() >= KYC_INSURER_DISPLAY_SEQUENCE_MIN:
        return True
    # Short query vs long portal string (concatenated options / legal name): slide a window of ~len(qi).
    if len(qi) >= 6 and len(di) > len(qi):
        wl = min(len(qi) + 24, len(di), 64)
        best = 0.0
        step = max(1, wl // 6)
        for start in range(0, max(1, len(di) - wl + 1), step):
            window = di[start : start + wl]
            best = max(best, difflib.SequenceMatcher(None, qi, window).ratio())
        if best >= KYC_INSURER_DISPLAY_SEQUENCE_MIN:
            return True
    return False


def _kyc_press_tab_n(page, n: int, *, pause_ms: int = 90) -> None:
    for _ in range(max(0, n)):
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass
        _t(page, pause_ms)


def _kyc_frame_active_element_is_editable(fr) -> bool:
    """True when focus is on a control where Ctrl+A selects field text, not the whole page."""
    try:
        return bool(
            fr.evaluate(
                """() => {
          const a = document.activeElement;
          if (!a) return false;
          const t = a.tagName;
          if (t === 'INPUT' || t === 'TEXTAREA') return true;
          if (t === 'SELECT') return true;
          if (a.isContentEditable) return true;
          return false;
        }"""
            )
        )
    except Exception:
        return False


def _kyc_frame_active_element_accepts_mobile_digits(fr) -> bool:
    """True when focus is on a text-like input — not ``<select>``, radio, or checkbox (avoid typing mobile into insurer ddl)."""
    try:
        return bool(
            fr.evaluate(
                """() => {
          const a = document.activeElement;
          if (!a) return false;
          const t = a.tagName;
          if (t === 'TEXTAREA') return true;
          if (t === 'INPUT') {
            const ty = (a.getAttribute('type') || 'text').toLowerCase();
            if (ty === 'radio' || ty === 'checkbox' || ty === 'submit' || ty === 'button' || ty === 'hidden' || ty === 'file')
              return false;
            return true;
          }
          if (a.isContentEditable) return true;
          return false;
        }"""
            )
        )
    except Exception:
        return False


def _kyc_blur_if_insurer_product_select_focused(kyc_fr) -> None:
    """
    MISP often leaves focus on ``#ContentPlaceHolder1_ddlproduct`` after insurer pick; Tab chain then
    fails and ``keyboard.type`` can target the wrong ``<select>``. Blur so downstream DOM steps see a sane focus.
    """
    try:
        kyc_fr.evaluate(
            """() => {
          const a = document.activeElement;
          if (!a || a.tagName !== 'SELECT') return;
          const id = (a.id || '').toLowerCase();
          const nm = (a.name || '').toLowerCase();
          if (id.includes('ddlproduct') || nm.includes('ddlproduct')) {
            if (a.blur) a.blur();
          }
        }"""
        )
    except Exception:
        pass


def _kyc_fill_mobile_digits_in_frame(kyc_fr, digits: str, *, timeout_ms: int) -> bool:
    """
    Fill mobile via locators in the KYC frame (does not rely on focus). Covers ASP.NET ids like
    ``*txt*Mobile*`` when label/placeholder matchers miss after a partial postback.
    """
    to = min(int(timeout_ms), 60_000)
    d = (digits or "").strip()
    if not d:
        return False
    tries = (
        lambda: kyc_fr.get_by_label(re.compile(r"^Mobile\s*(Number|No\.?|Phone)?\s*$", re.I)),
        lambda: kyc_fr.get_by_placeholder(
            re.compile(r"mobile|phone|contact\s*no|mob\.?\s*no", re.I)
        ),
        lambda: kyc_fr.locator('input[type="tel"]'),
        lambda: kyc_fr.locator("input[name*='mobile' i]"),
        lambda: kyc_fr.locator("input[id*='mobile' i]"),
        lambda: kyc_fr.locator("input[id*='Mobile' i]"),
        lambda: kyc_fr.locator("input[id*='txt' i][id*='mob' i]"),
    )
    for get_loc in tries:
        try:
            loc = get_loc()
            if loc.count() == 0:
                continue
            el = loc.first
            if not el.is_visible(timeout=2_000):
                continue
            el.fill("", timeout=to)
            el.fill(d, timeout=to, force=True)
            logger.info("Hero Insurance: KYC — mobile filled via locator in frame.")
            return True
        except Exception:
            continue
    return False


def _kyc_try_click_insurance_company_field(kyc_fr, *, timeout_ms: int) -> bool:
    """Focus the insurer combobox/input inside the KYC frame (avoids Tab landing on body)."""
    to = min(int(timeout_ms), 15_000)
    tries = (
        lambda: kyc_fr.get_by_label(re.compile(r"Insurance\s*Company", re.I)),
        lambda: kyc_fr.get_by_role(
            "combobox", name=re.compile(r"Insurance\s*Company", re.I)
        ),
        lambda: kyc_fr.locator("input[aria-label*='Insurance Company' i]"),
        lambda: kyc_fr.locator('select:near(:text("Insurance Company"))'),
    )
    for get_loc in tries:
        try:
            loc = get_loc()
            if loc.count() > 0 and loc.first.is_visible(timeout=2_500):
                loc.first.click(timeout=to)
                return True
        except Exception:
            continue
    return False


def _kyc_set_ovd_aadhaar_card_in_frame(kyc_fr, *, timeout_ms: int) -> bool:
    """
    Set Officially Valid Document (OVD) to **AADHAAR CARD** via native ``<select>`` in the KYC frame.

    Prefer this over Tab+ArrowDown: ArrowDown was moving the **KYC Partner** ``<select>`` (e.g. to
    Hyperverge) when focus landed there instead of on OVD.
    """
    to = min(int(timeout_ms), 60_000)
    ovd_ok = False
    for sel_css in (
        'select:near(:text("Officially Valid"))',
        'select:near(:text("OVD"))',
        "select[aria-label*='OVD' i]",
        "select[aria-label*='Officially Valid' i]",
    ):
        try:
            loc = kyc_fr.locator(sel_css)
            if loc.count() == 0:
                continue
            opts = loc.first.locator("option")
            n = opts.count()
            for i in range(min(n, 200)):
                t = (opts.nth(i).inner_text() or "").strip().upper()
                if "AADHAAR" in t and "CARD" in t:
                    val = opts.nth(i).get_attribute("value")
                    if val:
                        loc.first.select_option(value=val, timeout=to, force=True)
                    else:
                        loc.first.select_option(label=t, timeout=to, force=True)
                    ovd_ok = True
                    logger.info("Hero Insurance: KYC keyboard path — OVD set to AADHAAR CARD (DOM select).")
                    break
            if ovd_ok:
                break
        except Exception:
            continue
    if not ovd_ok:
        try:
            kyc_fr.get_by_label(re.compile(r"Officially\s*Valid|OVD", re.I)).locator(
                "xpath=following::select[1]"
            ).first.select_option(
                label=re.compile(r"AADHAAR\s*CARD", re.I), timeout=to, force=True
            )
            ovd_ok = True
            logger.info("Hero Insurance: KYC keyboard path — OVD via label+following select.")
        except Exception:
            try:
                ok_js = kyc_fr.evaluate(
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
                    logger.info("Hero Insurance: KYC keyboard path — OVD set via JS select scan.")
            except Exception:
                pass
    return ovd_ok


def _kyc_restore_kyc_partner_to_default_label(kyc_fr, page, *, timeout_ms: int) -> None:
    """
    If the **KYC Partner** ``<select>`` no longer shows the default (e.g. Signzy), set it back.

    Tab/ArrowDown or portal postback can change this control; operators expect the portal default.
    """
    label = (KYC_DEFAULT_KYC_PARTNER_LABEL or "Signzy").strip()
    if not label:
        return
    to = min(int(timeout_ms), 15_000)
    try:
        loc = kyc_fr.locator("#ContentPlaceHolder1_ddlkycPartner")
        if loc.count() == 0:
            loc = kyc_fr.locator("select[id*='ddlkycPartner' i]")
        if loc.count() == 0:
            return
        cur = (
            loc.first.evaluate(
                """el => {
              const i = el.selectedIndex;
              if (i < 0) return '';
              const o = el.options[i];
              return o ? (o.textContent || '').trim() : '';
            }"""
            )
            or ""
        ).strip()
        if not cur or normalize_for_fuzzy_match(cur) == normalize_for_fuzzy_match(label):
            return
        if _select_option_fuzzy_in_select(page, loc, label, timeout_ms=to, fuzzy_min_score=0.22):
            logger.info(
                "Hero Insurance: restored KYC Partner to %r (was %r).",
                label,
                cur[:100],
            )
    except Exception as exc:
        logger.debug("Hero Insurance: KYC Partner restore: %s", exc)


def _kyc_try_click_mobile_field(kyc_fr, *, timeout_ms: int) -> bool:
    """Focus mobile input in the KYC frame before typing."""
    to = min(int(timeout_ms), 15_000)
    tries = (
        lambda: kyc_fr.get_by_label(re.compile(r"^Mobile\s*(Number|No\.?|Phone)?\s*$", re.I)),
        lambda: kyc_fr.get_by_placeholder(re.compile(r"mobile", re.I)),
        lambda: kyc_fr.locator('input[type="tel"]'),
        lambda: kyc_fr.locator("input[name*='mobile' i]"),
    )
    for get_loc in tries:
        try:
            loc = get_loc()
            if loc.count() > 0 and loc.first.is_visible(timeout=2_500):
                loc.first.click(timeout=to)
                return True
        except Exception:
            continue
    return False


def _kyc_ovd_focused_text_is_aadhaar_card(shown: str) -> bool:
    u = (shown or "").strip().upper()
    if not u:
        return False
    if "AADHAAR" in u and "CARD" in u:
        return True
    if u == "AADHAAR CARD" or "आधार" in (shown or ""):
        return True
    return bool(re.search(r"AADHAAR\s*CARD", (shown or ""), re.I))


def _kyc_dom_fill_ovd_mobile_consent_in_frame(kyc_fr, mobile: str, *, timeout_ms: int) -> str | None:
    """
    Select OVD = AADHAAR CARD, fill mobile, check consent — all scoped to the KYC **Frame**
    (same document as the insurer field). Used when Tab/ArrowDown cannot reach the OVD control
    because focus/tab order differs or the control is not a plain ``<select>`` read via
    ``activeElement``.
    """
    pg = kyc_fr.page
    to = min(int(timeout_ms), 60_000)
    mobile = (mobile or "").strip()
    if not mobile:
        return "customer_master.mobile_number is empty for KYC DOM fallback."
    digits = re.sub(r"\D", "", mobile)[:12]

    ovd_ok = False
    for sel_css in (
        'select:near(:text("Officially Valid"))',
        'select:near(:text("OVD"))',
        "select[aria-label*='OVD' i]",
        "select[aria-label*='Officially Valid' i]",
    ):
        try:
            loc = kyc_fr.locator(sel_css)
            if loc.count() == 0:
                continue
            opts = loc.first.locator("option")
            n = opts.count()
            for i in range(min(n, 200)):
                t = (opts.nth(i).inner_text() or "").strip().upper()
                if "AADHAAR" in t and "CARD" in t:
                    val = opts.nth(i).get_attribute("value")
                    if val:
                        loc.first.select_option(value=val, timeout=to, force=True)
                    else:
                        loc.first.select_option(label=t, timeout=to, force=True)
                    ovd_ok = True
                    logger.info("Hero Insurance: KYC DOM (frame) — OVD set to AADHAAR CARD.")
                    break
            if ovd_ok:
                break
        except Exception:
            continue
    if not ovd_ok:
        try:
            kyc_fr.get_by_label(re.compile(r"Officially\s*Valid|OVD", re.I)).locator(
                "xpath=following::select[1]"
            ).first.select_option(
                label=re.compile(r"AADHAAR\s*CARD", re.I), timeout=to, force=True
            )
            ovd_ok = True
            logger.info("Hero Insurance: KYC DOM (frame) — OVD via label+following select.")
        except Exception:
            try:
                ok_js = kyc_fr.evaluate(
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
                    logger.info("Hero Insurance: KYC DOM (frame) — OVD set via JS select scan.")
            except Exception:
                pass
    if not ovd_ok:
        return (
            "KYC keyboard SOP: could not set OVD to AADHAAR CARD (keyboard and DOM in KYC frame). "
            "Try KYC_KEYBOARD_TABS_INSURER_TO_OVD or inspect the Officially Valid control."
        )

    _kyc_restore_kyc_partner_to_default_label(kyc_fr, pg, timeout_ms=to)

    # Mobile often appears or enables only after OVD is set.
    try:
        kyc_fr.locator(
            'input[type="tel"], input[name*="mobile" i], input[id*="mobile" i]'
        ).first.wait_for(state="visible", timeout=min(12_000, to))
    except Exception:
        _t(pg, 450)

    mob_filled = _kyc_fill_mobile_digits_in_frame(kyc_fr, digits, timeout_ms=to)
    if not mob_filled:
        return "KYC keyboard SOP: could not fill mobile in KYC frame after OVD (DOM fallback)."

    _t(pg, 220)
    return _kyc_post_mobile_entry_branch(kyc_fr.page, kyc_fr, timeout_ms=to)


def _fill_kyc_ekyc_keyboard_sop(
    page,
    values: dict,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    Keyboard SOP for ``ekycpage.aspx`` (focus document → Tab to Insurance Company → type to filter →
    ArrowDown until fuzzy match → Enter → Tab to OVD → ArrowDown to Aadhaar → Tab to mobile → type →
    Tab to consent → Space). Tab/down counts: ``KYC_KEYBOARD_*`` env vars.
    """
    insurer = (values.get("insurer") or "").strip()
    mobile = (values.get("mobile_number") or "").strip()
    if not insurer:
        return "insurer is empty for KYC keyboard SOP."
    if not mobile:
        return "customer_master.mobile_number is empty for KYC keyboard SOP."

    cap = min(int(timeout_ms), 120_000)
    kyc_fr = _kyc_preferred_kyc_frame(page)
    logger.info(
        "Hero Insurance: KYC keyboard SOP — focusing document (url=%s).",
        (page.url or "")[:220],
    )
    try:
        page.bring_to_front()
    except Exception:
        pass
    # When KYC is in a child frame, click the hosting <iframe> first so focus enters the frame
    # before body click + Tab chain (main-page Tab order otherwise skips embedded controls).
    if kyc_fr != page.main_frame:
        try:
            kyc_fr.frame_element().click(timeout=min(cap, 8_000))
            _t(page, 160)
        except Exception as exc:
            logger.debug("Hero Insurance: KYC iframe host click: %s", exc)
    # Focus the KYC document (often inside an iframe). Main-frame body click does not move focus there.
    try:
        kyc_fr.locator("body").click(timeout=min(cap, 8_000), position={"x": 160, "y": 220})
    except Exception:
        try:
            page.locator("body").click(timeout=min(cap, 8_000), position={"x": 40, "y": 40})
        except Exception:
            try:
                page.mouse.click(80, 200)
            except Exception:
                pass
    _t(page, 280)

    # Prefer clicking the labelled insurer control so focus is on INPUT/SELECT. Tab alone often
    # leaves focus on body; Control+A then selects the entire page (not field text).
    ic_clicked = _kyc_try_click_insurance_company_field(kyc_fr, timeout_ms=cap)
    if ic_clicked:
        logger.info("Hero Insurance: KYC keyboard — focused Insurance Company via click in frame.")
        _t(page, 200)
    else:
        _kyc_press_tab_n(page, max(0, KYC_KEYBOARD_TABS_TO_INSURANCE_FIELD))

    if _kyc_frame_active_element_is_editable(kyc_fr):
        try:
            page.keyboard.press("Control+A")
        except Exception:
            pass
        _t(page, 50)
        try:
            page.keyboard.press("Backspace")
        except Exception:
            pass
        _t(page, 50)
    else:
        logger.warning(
            "Hero Insurance: KYC keyboard — focus not on an editable control before insurer type; "
            "typing without Select-All (prevents selecting all text on the page)."
        )
    try:
        page.keyboard.type(insurer[:96], delay=34)
    except Exception as exc:
        return f"KYC keyboard SOP: could not type insurer: {exc!s}"
    _t(page, 480)

    opts = _kyc_collect_dropdown_option_texts(kyc_fr)
    pick = (
        fuzzy_best_option_label(insurer, opts, min_score=KYC_INSURER_FUZZY_MIN_SCORE)
        if opts
        else None
    )
    shown = _kyc_read_focused_control_text(page)
    matched = bool(pick) and not (pick or "").strip().lower().startswith("--select") and (
        _kyc_insurer_display_matches(insurer, shown)
        or _kyc_insurer_display_matches(insurer, pick or "")
    )
    if not matched:
        for _ in range(max(1, KYC_KEYBOARD_INSURER_ARROW_DOWN_MAX)):
            try:
                page.keyboard.press("ArrowDown")
            except Exception:
                pass
            _t(page, 115)
            shown = _kyc_read_focused_control_text(page)
            if _kyc_insurer_display_matches(insurer, shown):
                matched = True
                logger.info(
                    "Hero Insurance: KYC keyboard — insurer matched on ArrowDown (%r).",
                    shown[:90],
                )
                break
            opts2 = _kyc_collect_dropdown_option_texts(kyc_fr)
            if opts2:
                cand = fuzzy_best_option_label(
                    insurer, opts2, min_score=KYC_INSURER_FUZZY_MIN_SCORE
                )
                if cand and _kyc_insurer_display_matches(insurer, cand):
                    matched = True
                    logger.info(
                        "Hero Insurance: KYC keyboard — insurer matched from list (%r).",
                        (cand or "")[:90],
                    )
                    break
    if not matched:
        return (
            "KYC keyboard SOP: could not match insurer after typing and ArrowDown. "
            f"insurer={insurer[:48]!r}"
        )

    try:
        page.keyboard.press("Enter")
    except Exception:
        pass
    # Close insurer listbox / commit focus — without this, Tab may not leave the combobox (manual Tab fixed it).
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    _t(page, 280)
    _kyc_blur_if_insurer_product_select_focused(kyc_fr)
    _t(page, 120)
    # Proposer/OVD are often not in the DOM until insurer is chosen (see kyc_nav_scrape before fill).
    # Tab out + re-scrape captures new <select> ids and partner options after commit.
    _hero_insurance_kyc_nav_after_insurer_commit(
        page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
    )
    _kyc_select_kyc_partner_if_available(page, kyc_fr, values, timeout_ms=cap)
    _hero_insurance_kyc_nav_after_kyc_partner_commit(
        page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
    )

    ovd_ok = _kyc_set_ovd_aadhaar_card_in_frame(kyc_fr, timeout_ms=cap)
    last_shown = ""
    if not ovd_ok:
        # Legacy: Tab to OVD then ArrowDown — risky if focus lands on KYC Partner <select> first.
        _kyc_press_tab_n(page, max(0, KYC_KEYBOARD_TABS_INSURER_TO_OVD - 1))
        for _ in range(max(1, KYC_KEYBOARD_OVD_ARROW_DOWN_MAX)):
            shown = _kyc_read_focused_control_text(page)
            last_shown = shown or last_shown
            if _kyc_ovd_focused_text_is_aadhaar_card(shown):
                ovd_ok = True
                logger.info("Hero Insurance: KYC keyboard — OVD shows AADHAAR CARD (ArrowDown).")
                break
            try:
                page.keyboard.press("ArrowDown")
            except Exception:
                pass
            _t(page, 105)
    if not ovd_ok:
        logger.warning(
            "Hero Insurance: KYC keyboard — OVD not set via DOM or ArrowDown (last focus text=%r); "
            "using DOM fills inside KYC frame.",
            (last_shown or "")[:160],
        )
        return _kyc_dom_fill_ovd_mobile_consent_in_frame(kyc_fr, mobile, timeout_ms=timeout_ms)

    _kyc_restore_kyc_partner_to_default_label(kyc_fr, page, timeout_ms=cap)

    _kyc_blur_if_insurer_product_select_focused(kyc_fr)
    _t(page, 280)

    digits = re.sub(r"\D", "", mobile)[:12]
    mob_typed = False
    to_fill = min(int(timeout_ms), 60_000)
    # Prefer locator fill before Tab: portal may already focus mobile after OVD; default
    # KYC_KEYBOARD_TABS_OVD_TO_MOBILE Tabs would move past it (log: activeElement stuck on ddlproduct).
    for attempt in range(4):
        if _kyc_fill_mobile_digits_in_frame(kyc_fr, digits, timeout_ms=to_fill):
            mob_typed = True
            break
        _t(page, 380)
    if not mob_typed:
        _kyc_press_tab_n(page, max(0, KYC_KEYBOARD_TABS_OVD_TO_MOBILE))
        mob_typed = _kyc_fill_mobile_digits_in_frame(kyc_fr, digits, timeout_ms=to_fill)
    if not mob_typed and _kyc_frame_active_element_accepts_mobile_digits(kyc_fr):
        try:
            page.keyboard.press("Control+A")
        except Exception:
            pass
        _t(page, 45)
        try:
            page.keyboard.type(digits, delay=30)
            mob_typed = True
        except Exception as exc:
            return f"KYC keyboard SOP: mobile type failed: {exc!s}"
    if not mob_typed:
        if _kyc_try_click_mobile_field(kyc_fr, timeout_ms=cap):
            logger.info("Hero Insurance: KYC keyboard — focused mobile field via click in frame.")
            _t(page, 160)
            mob_typed = _kyc_fill_mobile_digits_in_frame(kyc_fr, digits, timeout_ms=to_fill)
    if not mob_typed:
        return (
            "KYC keyboard SOP: could not fill mobile after OVD "
            "(portal may use a new control id — check Mobile selectors)."
        )
    _t(page, 220)
    logger.info(
        "Hero Insurance: KYC keyboard — mobile entered; blurring then post-mobile banner / Proceed vs upload."
    )
    return _kyc_post_mobile_entry_branch(page, kyc_fr, timeout_ms=cap)


def _fill_insurance_company_and_ovd_mobile_consent(
    page,
    values: dict,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """Returns error message or None on success."""
    if KYC_USE_KEYBOARD_EKYC_SOP and _kyc_url_looks_like_ekyc_page(page):
        return _fill_kyc_ekyc_keyboard_sop(
            page,
            values,
            timeout_ms=timeout_ms,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )

    insurer = (values.get("insurer") or "").strip()
    mobile = (values.get("mobile_number") or "").strip()

    # --- Insurance Company (dropdown / search; match insurer from details sheet / DB) ---
    if insurer:
        filled = False
        # Label-associated <select> (ASP.NET table / modal layouts often break ``select:near``).
        try:
            loc = page.get_by_label(re.compile(r"Insurance\s*Company\s*\*?", re.I))
            if loc.count() > 0 and _select_option_fuzzy_in_select(
                page,
                loc,
                insurer,
                timeout_ms=timeout_ms,
                fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
            ):
                filled = True
        except Exception:
            pass
        # Native <select> near label text
        if not filled:
            for sel_css in (
                'select:near(:text("Insurance Company"))',
                "select[aria-label*='Insurance Company' i]",
                "select[title*='Insurance Company' i]",
            ):
                try:
                    loc = page.locator(sel_css)
                    if loc.count() > 0 and _select_option_fuzzy_in_select(
                        page,
                        loc,
                        insurer,
                        timeout_ms=timeout_ms,
                        fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
                    ):
                        filled = True
                        break
                except Exception:
                    continue
        # Any <select> whose options fuzzy-match (ekycpage hidden/skinny selects).
        if not filled:
            try:
                if _fill_insurance_company_fuzzy_any_visible_select(
                    page, insurer, timeout_ms=timeout_ms
                ):
                    filled = True
            except Exception as exc:
                logger.debug("Hero Insurance: insurer fuzzy select scan: %s", exc)
        if not filled:
            # Combobox / typeahead: scoped field + fuzzy match on visible options (not substring of typed prefix only)
            try:
                if _fill_insurance_company_typeahead_fuzzy(page, insurer, timeout_ms=timeout_ms):
                    filled = True
            except Exception as exc:
                logger.warning("Hero Insurance: insurer typeahead failed: %s", exc)
        if not filled:
            return (
                "Could not set Insurance Company from details-sheet insurer "
                f"({insurer[:40]!r}). Adjust selectors for this portal build."
            )
        _hero_insurance_kyc_nav_after_insurer_commit(
            page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
        )
        kyc_fr_dom = _kyc_preferred_kyc_frame(page)
        _kyc_select_kyc_partner_if_available(
            page, kyc_fr_dom, values, timeout_ms=timeout_ms
        )
        _hero_insurance_kyc_nav_after_kyc_partner_commit(
            page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
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

    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _t(page, 400)
    return _kyc_post_mobile_entry_branch(
        page, _kyc_preferred_kyc_frame(page), timeout_ms=timeout_ms
    )


def _kyc_proceed_or_upload(page, *, timeout_ms: int) -> str | None:
    """
    Invoked from ``_kyc_post_mobile_entry_branch`` when the verified-banner text is **not** shown
    after mobile entry (same page step where three ``input[type=file]`` typically appear).

    - If legacy "already done" body text matches, consent + **Proceed**.
    - Else attach three placeholder files, consent, then **Proceed** / Submit / Continue.
    """
    try:
        body = page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
        txt = (body or "").lower()
    except Exception:
        txt = ""

    if re.search(r"kyc\s+is\s+already|already\s+done|kyc\s+already\s+complete", txt):
        _kyc_ensure_consent_checked_before_kyc_cta(page)
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
    _kyc_ensure_consent_checked_before_kyc_cta(page)
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
    portal_base_url: str,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    Login / Sign In → 2W (two-wheeler) → New Policy → (if ``values``) insurer / OVD / mobile →
    post-mobile: verified banner → consent + **Proceed**, else three uploads + **Proceed** (see ``_kyc_post_mobile_entry_branch``).
    Returns None on success, else error string.

    ``portal_base_url`` is the insurance site origin (e.g. from ``pre_process`` ``match_base``) so new-tab handoff
    never attaches to a Siebel/DMS tab when both are open.
    """
    _t(page, 500)
    _hero_insurance_log_page_diagnostics(
        page,
        phase="before_sign_in",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    clicked = _click_sign_in_if_visible(page, timeout_ms=timeout_ms)
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

    pages_before_2w = _misp_snapshot_context_pages(page)
    try:
        _click_2w_icon(page, timeout_ms=timeout_ms)
    except Exception as exc:
        return f"2W Icon: {exc!s}"

    page = _misp_resolve_page_after_possible_new_tab(
        pages_before_2w,
        page,
        portal_base_url=portal_base_url,
        timeout_ms=timeout_ms,
        step_label="2W",
    )
    _t(page, 600)

    pages_before_np = _misp_snapshot_context_pages(page)
    try:
        _click_new_policy(page, timeout_ms=timeout_ms)
    except Exception as exc:
        return f"New Policy: {exc!s}"

    page = _misp_resolve_page_after_possible_new_tab(
        pages_before_np,
        page,
        portal_base_url=portal_base_url,
        timeout_ms=timeout_ms,
        step_label="New Policy",
    )

    if not values:
        logger.info("Hero Insurance: no DB values — stopping after New Policy.")
        return None

    _t(page, 900)
    _hero_insurance_log_kyc_navigation_scrape(
        page,
        phase="hero_misp_before_kyc_fill",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    err = _fill_insurance_company_and_ovd_mobile_consent(
        page,
        values,
        timeout_ms=timeout_ms,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    if err:
        return err

    _t(page, 500)
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
    ``build_insurance_fill_values``) and runs: **Login / Sign In** (if visible) → **2W** (two-wheeler) → **New Policy** →
    Insurance Company (fuzzy LIKE insurer) → OVD **AADHAAR CARD** → **mobile_number** →
    **after mobile**, verified-banner path (consent + **Proceed**) or three file uploads + **Proceed**
    (``_kyc_post_mobile_entry_branch``). Stops on the **VIN** entry page; **main_process** fills VIN and the rest.
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
        result["error"] = open_error
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "NOTE", f"pre_process: could not open Insurance tab: {open_error}"
        )
        return result


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
            portal_base_url=match_base,
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


def _insurance_url_looks_like_login_page(page) -> bool:
    """True when still on partner login / generic login — safe to ``goto`` site root to recover."""
    try:
        u = (page.url or "").strip().lower()
        if not u or "about:blank" in u:
            return True
        if "misp-partner-login" in u:
            return True
        if u.rstrip("/").endswith("/login"):
            return True
        return False
    except Exception:
        return True


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
        "Insurance: waiting up to %s ms for KYC screen (dummy #ins-mobile-no or MISP KYC URL)",
        INSURANCE_LOGIN_WAIT_MS,
    )
    # Do not ``goto`` site root when already past login — that often drops SPA session and returns to login.
    if _insurance_url_looks_like_login_page(page):
        try:
            page.goto(f"{base}/", wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            logger.warning("Insurance: goto %s/: %s", base, exc)
            try:
                page.goto(base, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc2:
                logger.warning("Insurance: goto %s: %s", base, exc2)
    else:
        try:
            u_snip = (page.url or "")[:160]
        except Exception:
            u_snip = ""
        logger.info(
            "Insurance: already past login URL — skipping root navigation (current=%s)",
            u_snip,
        )

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
        # Same MISP landing as Hero pre_process: after login, **2W** then **New Policy** before KYC / dummy fields.
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        _t(page, 1_200)
        pages_before_2w = _misp_snapshot_context_pages(page)
        try:
            _click_2w_icon(page, timeout_ms=INSURANCE_ACTION_TIMEOUT_MS)
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", "run_fill_insurance_only: clicked 2W (two-wheeler) entry"
            )
        except Exception as exc:
            err_2w = f"2W (two-wheeler) step failed: {exc!s}"
            logger.warning("Hero Insurance run_fill_insurance_only: %s", err_2w)
            result["error"] = err_2w
            append_playwright_insurance_line(ocr_output_dir, subfolder, "ERROR", err_2w)
            return result
        page = _misp_resolve_page_after_possible_new_tab(
            pages_before_2w,
            page,
            portal_base_url=insurance_base_url.strip(),
            timeout_ms=INSURANCE_ACTION_TIMEOUT_MS,
            step_label="2W",
        )
        try:
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"run_fill_insurance_only: active tab after 2W — url={(page.url or '')[:200]}",
            )
        except Exception:
            pass
        _t(page, 600)
        pages_before_np = _misp_snapshot_context_pages(page)
        try:
            _click_new_policy(page, timeout_ms=INSURANCE_ACTION_TIMEOUT_MS)
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", "run_fill_insurance_only: clicked New Policy"
            )
        except Exception as exc:
            err_np = f"New Policy step failed: {exc!s}"
            logger.warning("Hero Insurance run_fill_insurance_only: %s", err_np)
            result["error"] = err_np
            append_playwright_insurance_line(ocr_output_dir, subfolder, "ERROR", err_np)
            return result
        page = _misp_resolve_page_after_possible_new_tab(
            pages_before_np,
            page,
            portal_base_url=insurance_base_url.strip(),
            timeout_ms=INSURANCE_ACTION_TIMEOUT_MS,
            step_label="New Policy",
        )
        try:
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"run_fill_insurance_only: active tab before KYC wait — url={(page.url or '')[:200]}",
            )
        except Exception:
            pass

        wait_err = _wait_for_insurance_kyc_after_login(page, insurance_base_url)
        if wait_err:
            result["error"] = wait_err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"run_fill_insurance_only: KYC wait failed: {wait_err}"
            )
            return result

        base = (insurance_base_url or "").strip().rstrip("/")

        # Real MISP ``ekycpage`` has no training-only ``#ins-company`` / ``#ins-mobile-no`` markup.
        if not _insurance_page_has_dummy_kyc_training_html(page):
            _hero_insurance_log_kyc_navigation_scrape(
                page,
                phase="fill_insurance_only_before_kyc_fill",
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: real MISP KYC — _fill_insurance_company_and_ovd_mobile_consent "
                "(keyboard SOP on ekycpage when enabled)",
            )
            kyc_fill_err = _fill_insurance_company_and_ovd_mobile_consent(
                page,
                values,
                timeout_ms=INSURANCE_ACTION_TIMEOUT_MS,
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
            if kyc_fill_err:
                result["error"] = kyc_fill_err
                append_playwright_insurance_line(
                    ocr_output_dir, subfolder, "ERROR", f"run_fill_insurance_only: {kyc_fill_err}"
                )
                return result
            try:
                page.wait_for_load_state("domcontentloaded", timeout=25_000)
            except Exception:
                pass
            _t(page, 800)
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: real MISP KYC (post-mobile banner branch + Proceed or uploads) complete",
            )
            result["success"] = True
            result["error"] = None
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
