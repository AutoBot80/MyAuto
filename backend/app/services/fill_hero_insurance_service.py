"""
Hero Insurance (MISP) Playwright flow: **pre_process** (``run_fill_insurance_only`` on real MISP) runs through KYC,
then fills **VIN** from DB (**``full_chassis``**) and clicks the VIN page **Submit**. **main_process** continues with
**I agree** (if shown), then the proposal form. Proposer/vehicle/nominee fields come from the view;
email, most add-ons, CPA tenure, HDFC, and registration date use **hardcoded** defaults; **Hero CPI** (NIC/CPI row) follows **``form_insurance_view.hero_cpi``** (**``dealer_ref.hero_cpi``**). Proposal fields resolve **ContentPlaceHolder1** ids (**``HERO_MISP_CPH1``**) where applicable, then labels. **insurance_master** INSERT runs after proposal fill (readbacks) and **before** **Proposal Preview** / **Review**; **Proposal Preview** / **Review** (always); **Issue Policy** optional via ``HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY``; scrape **policy_num**, **policy_from**, **policy_to**, **premium**, **idv** from preview and merge via ``update_insurance_master_policy_after_issue`` (preview scrape and post–Issue Policy scrape).
Browser reuse uses ``handle_browser_opening.get_or_open_site_page`` with ``match_base`` from **pre_process**.
"""
import difflib
import json
import logging
import re
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.config import (
    DEALER_ID,
    HERO_MISP_KYC_TAB_AWAY_SIMULATION,
    HERO_MISP_LANDING_WAIT_MS,
    HERO_MISP_UI_SETTLE_MS,
    INSURANCE_ACTION_TIMEOUT_MS,
    INSURANCE_BASE_URL,
    INSURANCE_KYC_POST_MOBILE_DOM_MS,
    INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS,
    INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS,
    INSURANCE_LOGIN_WAIT_MS,
    INSURANCE_POLICY_FILL_TIMEOUT_MS,
    INSURANCE_VIN_POST_URL_DOMCONTENTLOADED_MS,
    INSURANCE_VIN_PRE_DOMCONTENTLOADED_MS,
    KYC_KEYBOARD_INSURER_ARROW_DOWN_MAX,
    KYC_KEYBOARD_INSURER_ARROW_DOWN_STEP_MS,
    KYC_KEYBOARD_INSURER_TYPE_DELAY_MS,
    KYC_KEYBOARD_MOBILE_TYPE_DELAY_MS,
    KYC_KEYBOARD_OVD_ARROW_DOWN_MAX,
    KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS,
    KYC_KEYBOARD_TABS_INSURER_TO_OVD,
    KYC_KEYBOARD_TABS_MOBILE_TO_CONSENT,
    KYC_KEYBOARD_TABS_OVD_TO_MOBILE,
    KYC_KEYBOARD_TABS_TO_INSURANCE_FIELD,
    KYC_INSURER_DISPLAY_SEQUENCE_MIN,
    KYC_INSURER_FUZZY_MIN_SCORE,
    KYC_USE_KEYBOARD_EKYC_SOP,
    KYC_DEFAULT_KYC_PARTNER_LABEL,
    get_uploads_dir,
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
    normalize_hero_cpi_flag,
    reset_playwright_insurance_log,
    write_insurance_form_values,
)
from app.services.insurance_kyc_payloads import insurance_kyc_png_payloads
from app.services.utility_functions import (
    clean_text,
    fuzzy_best_option_label,
    insurer_prefer_matches,
    normalize_dob_for_misp,
    normalize_for_fuzzy_match,
    safe_subfolder_name,
)

# MISP navigation tuning — edit in source (not .env). Optional iframe CSS after trial runs.
# Logs show Hero MISP hub under ``/prod/apps/v1/2w/`` — prefer matching iframes before full frame sweeps.
INSURANCE_CLICK_SETTLE_MS = 35
INSURANCE_KYC_IFRAME_SELECTOR = ""
INSURANCE_VIN_IFRAME_SELECTOR = 'iframe[src*="2w" i]'
INSURANCE_NAV_IFRAME_SELECTOR = 'iframe[src*="2w" i]'

# When True: skip clicking **Issue Policy** only (after proposal review steps); still scrapes preview fields.
# **insurance_master** INSERT is before **Proposal Preview**; **Proposal Preview** / **Proposal Review** is always clicked after proposal fill (not gated by this flag).
HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY = True

# Optional: regex on checkbox **label/row text** (MispPolicy proposal grid) for a **new**
# proposal checkbox MISP added — force **unchecked** when a matching visible checkbox exists. If **empty**, this
# step is skipped (no error). **CPA Tenure** is native ``<select>`` ``ddlCPATenure`` (fuzzy option ``0``), not this hook.
HERO_MISP_PROPOSAL_OPTIONAL_UNCHECK_CHECKBOX_REGEX = ""

# ASP.NET ``ContentPlaceHolder1`` client-id prefix on ``MispPolicy.aspx`` proposal controls (frame scrape).
HERO_MISP_CPH1 = "ctl00_ContentPlaceHolder1"

# Nominee **Relation** ``<select>`` — portal builds vary (try in order per frame root).
HERO_MISP_NOMINEE_RELATION_CPH1_SUFFIXES = (
    "ddlNomineeRelation",
    "ddlNomineeRelationship",
    "ddlRelationWithNominee",
)

# **Agreement Type with Financer** (e.g. **HPA**). Real MISP uses **ddlAggwidFinancer**
# (``name="ctl00$ContentPlaceHolder1$ddlAggwidFinancer"``); older guesses kept as fallbacks.
HERO_MISP_AGREEMENT_TYPE_FINANCER_CPH1_SUFFIXES = (
    "ddlAggwidFinancer",
    "ddlAgreementTypeWithFinancer",
    "ddlAgreementWithFinancer",
    "ddlAgreementTypeFinancer",
)

# Return from ``_proposal_step_checkbox_by_cph1_id`` when no control matched (caller may use label/regex fallback).
PROPOSAL_CHECKBOX_ID_NOT_FOUND = "__proposal_checkbox_id_not_found__"
# After native ``<select>`` insurer commit (non-keyboard DOM path), use ``light`` nav (skip tab-away) — same as keyboard SOP.
HERO_MISP_LIGHT_NAV_AFTER_DOM_INSURER = True

# Persisted KYC insurer automation strategy (see ``_kyc_insurer_strategy_cache_*``).
KYC_INSURER_STRATEGY_DOM_NATIVE = "dom_native"
KYC_INSURER_STRATEGY_KEYBOARD_CHAIN = "keyboard_chain"
KYC_INSURER_STRATEGY_FUZZY_SCAN = "fuzzy_scan"

_LOGIN_LABEL_PATTERNS = (
    (re.compile(r"^\s*Sign\s*In\s*$", re.I), "Sign In"),
    (re.compile(r"^\s*Login\s*$", re.I), "Login"),
    (re.compile(r"^\s*Log\s+in\s*$", re.I), "Log in"),
)


def _kyc_insurer_strategy_cache_host_key() -> str:
    u = (INSURANCE_BASE_URL or "").strip()
    if not u:
        return "default"
    try:
        net = urllib.parse.urlparse(u).netloc.lower()
        return net or "default"
    except Exception:
        return "default"


def _kyc_insurer_strategy_cache_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "kyc_insurer_strategy_cache.json"


def _kyc_insurer_strategy_cache_read() -> str | None:
    path = _kyc_insurer_strategy_cache_path()
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        v = data.get(_kyc_insurer_strategy_cache_host_key())
        if v in (
            KYC_INSURER_STRATEGY_DOM_NATIVE,
            KYC_INSURER_STRATEGY_KEYBOARD_CHAIN,
            KYC_INSURER_STRATEGY_FUZZY_SCAN,
        ):
            return str(v)
    except Exception as exc:
        logger.debug("Hero Insurance: KYC insurer strategy cache read: %s", exc)
    return None


def _kyc_insurer_strategy_cache_write(strategy: str) -> None:
    if strategy not in (
        KYC_INSURER_STRATEGY_DOM_NATIVE,
        KYC_INSURER_STRATEGY_KEYBOARD_CHAIN,
        KYC_INSURER_STRATEGY_FUZZY_SCAN,
    ):
        return
    path = _kyc_insurer_strategy_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}
        data[_kyc_insurer_strategy_cache_host_key()] = strategy
        path.write_text(json.dumps(data, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.debug("Hero Insurance: KYC insurer strategy cache write: %s", exc)


def _kyc_insurer_attempt_order(cached: str | None) -> list[str]:
    """Non-fuzzy strategies permuted with **cached first**; ``fuzzy_scan`` is always last."""
    d, k, f = (
        KYC_INSURER_STRATEGY_DOM_NATIVE,
        KYC_INSURER_STRATEGY_KEYBOARD_CHAIN,
        KYC_INSURER_STRATEGY_FUZZY_SCAN,
    )
    valid = {d, k, f}
    if not cached or cached not in valid or cached == f:
        return [d, k, f]
    others = [x for x in (d, k) if x != cached]
    return [cached] + others + [f]


def _proposal_map_marital_for_misp(raw: str) -> str:
    """Normalize DB / staging text to MISP ``ddlMaritalStatus`` option labels."""
    s = (raw or "").strip()
    if not s:
        return ""
    sl = re.sub(r"\s+", " ", s.lower())
    if sl in ("married", "marrid", "maried", "m.") or sl.startswith("married "):
        return "Married"
    if sl in ("single", "unmarried", "un-married", "unmaried", "un-maried"):
        return "Single"
    if "widow" in sl:
        return "Widow"
    if "divorc" in sl:
        return "Divorced"
    return s


def _proposal_map_occupation_for_misp(raw: str) -> str:
    """
    Map profession text to MISP ``ddlOccupatnType`` labels. **Private** (e.g. vehicle class wording) → **Employed**.
    Empty → **Employed** (portal default expectation).
    """
    s = (raw or "").strip()
    if not s:
        return "Employed"
    sl = re.sub(r"\s+", " ", s.lower())
    if sl in ("private", "pvt", "pvt.") or "private" in sl:
        return "Employed"
    if sl in ("government job", "govt", "govt job", "government"):
        return "Government Job"
    if "self" in sl and "employ" in sl:
        return "Self Employed"
    if "student" in sl:
        return "Student"
    if "agricultur" in sl or "farmer" in sl or "farm" in sl:
        return "Farmer/Farm Related"
    if "business" in sl:
        return "Business"
    if sl in ("employed", "employment", "salaried", "job"):
        return "Employed"
    return s


def _hero_misp_vin_step_timeout_ms(base_action_ms: int | None = None) -> int:
    """
    Budget for KYC **Proceed** → **MispDms.aspx** + ``txtFrameNo`` attach. ``INSURANCE_ACTION_TIMEOUT_MS`` is for
    single actions (~5.5s); postback + redirect + loading overlay on **same** ``ekycpage.aspx`` URL needs longer.
    """
    b = int(base_action_ms if base_action_ms is not None else INSURANCE_ACTION_TIMEOUT_MS)
    return min(120_000, max(60_000, b * 15))


logger = logging.getLogger(__name__)


def _kyc_local_scan_paths_from_uploaded_scans(
    dealer_id: int | None,
    subfolder: str | None,
) -> list[str] | None:
    """
    Resolve KYC upload files from the client **Uploaded scans** tree (``get_uploads_dir``), same layout as
    ``UploadService.save_and_queue_v2``: ``Aadhar.jpg`` (front), ``Aadhar_back.jpg`` (rear). The portal’s
    third slot (customer photo) reuses the front image.

    Returns three absolute file paths, or ``None`` if the folder or required scans are missing.
    """
    if not subfolder or not str(subfolder).strip():
        return None
    did = int(dealer_id) if dealer_id is not None else int(DEALER_ID)
    base = get_uploads_dir(did) / safe_subfolder_name(subfolder)
    front = base / "Aadhar.jpg"
    back = base / "Aadhar_back.jpg"
    if not front.is_file():
        logger.info(
            "Hero Insurance: KYC — no %s under Uploaded scans (expected client upload).",
            front,
        )
        return None
    if not back.is_file():
        logger.info(
            "Hero Insurance: KYC — no %s under Uploaded scans; cannot attach rear scan.",
            back,
        )
        return None
    # Third field: customer photo — same as front (per operator SOP when no separate photo).
    return [
        str(front.resolve()),
        str(back.resolve()),
        str(front.resolve()),
    ]


def _kyc_local_scan_paths_from_values(values: dict | None) -> list[str] | None:
    """``values['kyc_local_scan_paths']`` set in ``run_fill_insurance_only`` from Uploaded scans."""
    if not values:
        return None
    raw = values.get("kyc_local_scan_paths")
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    return [str(x) for x in raw[:3]]


# #region agent log
_DEBUG_INSURER_TAB_NDJSON = Path(__file__).resolve().parents[3] / "debug-d1a375.log"


def _dbg_kyc_insurer_tab_ndjson(
    hypothesis_id: str, location: str, message: str, data: dict[str, Any]
) -> None:
    try:
        payload: dict[str, Any] = {
            "sessionId": "d1a375",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_INSURER_TAB_NDJSON, "a", encoding="utf-8") as _df:
            _df.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _dbg_kyc_focus_snapshot(kyc_fr, page) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    try:
        snap["kyc_is_child"] = kyc_fr != page.main_frame
    except Exception:
        snap["kyc_is_child"] = None
    try:
        snap["in_kyc_frame"] = kyc_fr.evaluate(
            """() => {
              const vis = document.visibilityState;
              const hf = document.hasFocus();
              const a = document.activeElement;
              let active = null;
              if (a) {
                active = {
                  tag: (a.tagName || '').toLowerCase(),
                  id: (a.id || '').slice(0, 96),
                  nm: (a.name || '').slice(0, 96),
                  role: String((a.getAttribute && a.getAttribute('role')) || '').slice(0, 48)
                };
              }
              return { doc_visibilityState: vis, doc_hasFocus: hf, active: active };
            }"""
        )
    except Exception as exc:
        snap["in_kyc_frame_err"] = str(exc)[:120]
    try:
        snap["in_main_frame"] = page.main_frame.evaluate(
            """() => {
              const vis = document.visibilityState;
              const hf = document.hasFocus();
              const a = document.activeElement;
              let active = null;
              if (a) {
                active = {
                  tag: (a.tagName || '').toLowerCase(),
                  id: (a.id || '').slice(0, 96),
                  nm: (a.name || '').slice(0, 96)
                };
              }
              return { doc_visibilityState: vis, doc_hasFocus: hf, active: active };
            }"""
        )
    except Exception as exc:
        snap["in_main_err"] = str(exc)[:120]
    return snap


# #endregion

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

# Maximum duration for `_t` UI micro-settles (single `wait_for_timeout` per call).
_MISP_UI_SETTLE_CAP_MS = 200


def _t(page, ms: int) -> None:
    try:
        page.wait_for_timeout(min(int(ms), _MISP_UI_SETTLE_CAP_MS))
    except Exception:
        try:
            time.sleep(min(int(ms), _MISP_UI_SETTLE_CAP_MS) / 1000.0)
        except Exception:
            pass


def _wait_load_optional(page, timeout: int = 8_000, *, state: str = "domcontentloaded") -> None:
    try:
        page.wait_for_load_state(state, timeout=timeout)
    except Exception:
        pass


def _proposal_fail(
    ocr_output_dir, subfolder, msg: str,
) -> tuple[str | None, dict[str, Any]]:
    append_playwright_insurance_line(
        ocr_output_dir, subfolder, "ERROR", f"main_process proposal form: {msg}",
    )
    return msg, {}


def _collect_select_option_labels(sel, *, skip_select_prefix: bool = False, max_n: int = 400) -> list[str]:
    """Read ``<option>`` text labels from a ``<select>`` locator (evaluate_all, fallback to nth loop)."""
    labels: list[str] = []
    try:
        raw = sel.locator("option").evaluate_all(
            "els => els.map(e => (e.textContent || '').trim()).filter(Boolean)"
        )
        for x in raw or []:
            t = str(x).strip()
            if not t:
                continue
            if skip_select_prefix and t.lower().startswith("--select"):
                continue
            labels.append(t)
    except Exception:
        try:
            n = sel.locator("option").count()
            for i in range(min(n, max_n)):
                t = (sel.locator("option").nth(i).inner_text() or "").strip()
                if not t:
                    continue
                if skip_select_prefix and t.lower().startswith("--select"):
                    continue
                labels.append(t)
        except Exception:
            pass
    return labels


def _insurance_pre_elapsed_note(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    t0: float | None,
    phase: str,
) -> None:
    """Milestone elapsed time since ``run_fill_insurance_only`` start (``Playwright_insurance.txt``)."""
    if t0 is None:
        return
    try:
        ms = int((time.monotonic() - t0) * 1000)
    except Exception:
        return
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"run_fill_insurance_only: phase={phase} elapsed_ms={ms}",
    )


def _insurance_vin_phase_note(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    t0_vin: float | None,
    phase: str,
    detail: str = "",
) -> None:
    """Operator-visible milestones for KYC→VIN (``Playwright_insurance.txt``)."""
    extra = f" {detail}" if detail else ""
    if t0_vin is None:
        msg = f"VIN step: {phase}{extra}"
    else:
        try:
            ms = int((time.monotonic() - t0_vin) * 1000)
        except Exception:
            ms = 0
        msg = f"VIN step: {phase} elapsed_ms={ms}{extra}"
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir, subfolder, "NOTE", msg
    )


def _insurance_kyc_flow_elapsed_note(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    t0_flow: float | None,
    phase: str,
) -> None:
    """KYC sub-phase wall time since ``run_fill_insurance_only`` / Hero portal flow start."""
    if t0_flow is None:
        return
    try:
        ms = int((time.monotonic() - t0_flow) * 1000)
    except Exception:
        return
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"run_fill_insurance_only: kyc_elapsed phase={phase} elapsed_ms={ms}",
    )


def _insurance_tab_resolve_note(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    t0_flow: float | None,
    step_label: str,
    branch: str,
    *,
    resolver_ms: int | None = None,
) -> None:
    if t0_flow is None:
        return
    try:
        ms = int((time.monotonic() - t0_flow) * 1000)
    except Exception:
        return
    extra = f" resolver_ms={resolver_ms}" if resolver_ms is not None else ""
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"run_fill_insurance_only: tab_resolve step={step_label} branch={branch} elapsed_ms={ms}{extra}",
    )


def _insurance_kyc_trace(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    phase: str,
    detail: str,
) -> None:
    """Append ``Playwright_insurance.txt`` NOTE + ``logger.info`` so operators can see KYC phase timing."""
    msg = f"KYC trace [{phase}] {detail}"
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir, subfolder, "NOTE", msg
    )
    logger.info("Hero Insurance: %s", msg[:900])


def _insurance_click_settle(page) -> None:
    """Fixed pause for MISP navigation (Sign In → 2W → New Policy …). ``INSURANCE_CLICK_SETTLE_MS`` (default 35)."""
    _t(page, min(INSURANCE_CLICK_SETTLE_MS, 15_000))


def _misp_click_nav_step(
    page,
    click_fn,
    step_label: str,
    *,
    portal_base_url: str,
    timeout_ms: int,
    ocr_output_dir=None,
    subfolder: str | None = None,
    t0_flow: float | None = None,
):
    """Snapshot pages → click → resolve tab → trace note. Returns ``(page, error_or_None)``."""
    pages_before = _misp_snapshot_context_pages(page)
    try:
        click_fn(page, timeout_ms=timeout_ms)
    except Exception as exc:
        return page, f"{step_label}: {exc!s}"
    t0_res = time.monotonic()
    page, tab_branch = _misp_resolve_page_after_possible_new_tab(
        pages_before,
        page,
        portal_base_url=portal_base_url,
        timeout_ms=timeout_ms,
        step_label=step_label,
    )
    res_ms = int((time.monotonic() - t0_res) * 1000)
    _insurance_tab_resolve_note(
        ocr_output_dir, subfolder, t0_flow, step_label, tab_branch, resolver_ms=res_ms,
    )
    return page, None


def _hero_misp_after_sign_in_settle(page) -> None:
    """
    After Sign In, wait for the landing UI. Prefer **domcontentloaded** over **networkidle** — MISP often
    never reaches network idle (analytics / long polling), which added multi-second delays before 2W.

    The hub SPA may paint **2W** after ``domcontentloaded``; wait for the same tile selectors ``_click_2w_icon``
    tries first (visibility), capped by ``HERO_MISP_LANDING_WAIT_MS``, so the 2W step does not run on a half-ready DOM.
    """
    _wait_load_optional(page, 8_000)
    cap = min(10_000, max(800, int(HERO_MISP_LANDING_WAIT_MS)))
    try:
        page.locator('[aid="ctl00_TWO"], #ctl00_TWO, img[alt="2W Icon"]').first.wait_for(
            state="visible", timeout=cap
        )
    except Exception:
        pass
    _insurance_click_settle(page)


def _hero_insurance_log_page_diagnostics(
    page,
    *,
    phase: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """
    Lightweight context for troubleshooting (debug log only).

    Former **login_page_snapshot** / **kyc_nav_scrape** blocks that evaluated every visible control
    and wrote multi‑kilobyte **[DIAG]** lines to ``Playwright_insurance.txt`` were removed.
    """
    _ = (ocr_output_dir, subfolder)
    try:
        url = (page.url or "").strip()[:500]
    except Exception:
        url = ""
    try:
        title = (page.title() or "").strip()[:200]
    except Exception:
        title = ""
    try:
        nframes = len(page.frames)
    except Exception:
        nframes = 0
    logger.debug(
        "Hero Insurance context phase=%s url=%s title=%s frames=%s",
        phase,
        url,
        title,
        nframes,
    )


def _kyc_simulate_tab_away_and_back(
    page,
    *,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """
    Operators report MISP KYC only advances after leaving the page and returning; synthetic ``blur``
    events are not always enough. Briefly focus another browser tab so the KYC document gets a real
    ``visibilityState`` hidden/visible transition (WebForms / UpdatePanel).
    """
    try:
        ctx = page.context
    except Exception:
        return
    blank = None
    try:
        blank = ctx.new_page()
        blank.goto("about:blank", timeout=8_000)
        blank.bring_to_front()
    except Exception as exc:
        logger.debug("Hero Insurance: KYC tab-away simulation (new page): %s", exc)
        try:
            if blank:
                blank.close()
        except Exception:
            pass
        return
    try:
        _t(page, 220)
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        blank.close()
    except Exception:
        pass
    try:
        _t(page, 120)
    except Exception:
        pass
    logger.info(
        "Hero Insurance: KYC — simulated tab away/back (about:blank) for insurer visibility transition."
    )
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "NOTE",
        "KYC: simulated tab away/back (temporary about:blank tab) to finalize insurer visibility for MISP.",
    )
    # #region agent log
    _dbg_kyc_insurer_tab_ndjson(
        "H8",
        "_kyc_simulate_tab_away_and_back:after",
        "restored KYC tab after temp tab",
        _dbg_kyc_focus_snapshot(_kyc_preferred_kyc_frame(page), page),
    )
    # #endregion


def _hero_insurance_kyc_nav_after_insurer_commit(
    page,
    *,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    light: bool = False,
) -> None:
    """
    After insurer is committed (keyboard Enter or DOM ``select_option``), optionally **Enter** + **Tab**
    so the portal commits the value (ASP.NET / postback). Optional **networkidle**
    (``INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS``) so the KYC pane can settle.
    Then optionally **tab away/back** when ``HERO_MISP_KYC_TAB_AWAY_SIMULATION`` is enabled (default off)
    so the page gets a real visibility transition when operators only unstick by navigating away
    (see ``_kyc_simulate_tab_away_and_back``).

    When ``light`` is True (eKYC keyboard SOP after DOM ``select_option`` or after the full keyboard
    Enter/Tab/Escape chain): skip the extra **Enter**, **Tab**, and tab-away simulation. Those were
    duplicating commits already sent in the keyboard block or moving focus to **KYC Partner** after
    a successful native ``select_option``.
    """
    if not light:
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        _t(page, 60)
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass
        _t(page, 80)
    else:
        _t(page, 80)
    _wait_load_optional(page, 3_000)
    _t(page, 60)

    cap = max(0, int(INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS))
    if cap > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=cap)
        except Exception:
            logger.debug(
                "Hero Insurance: networkidle after insurer (timeout=%s ms) timed out — continuing.",
                cap,
            )
    _t(page, 80)
    if not light and HERO_MISP_KYC_TAB_AWAY_SIMULATION:
        _kyc_simulate_tab_away_and_back(
            page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
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
                    _wait_load_optional(page, min(25_000, to))
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
                    _wait_load_optional(page, min(25_000, to))
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
    post_mobile_recovery_digits: str | None = None,
    kyc_local_scan_paths: list[str] | None = None,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    Run **after** the **first** KYC attempt (OVD = **AADHAAR CARD** only — set upstream), mobile filled,
    blur / short ``domcontentloaded`` so postback can run.

    - If the **verified Aadhaar / policy issuance** banner appears → **consent + Proceed** (no uploads).
    - If that message **does not** appear → optional ``post_mobile_recovery_digits`` runs the **second**
      branch: switch OVD to **AADHAAR EXTRACTION**, re-fill mobile, wait for uploads, then merge with the
      common tail: ``_kyc_proceed_or_upload`` (**three files + consent + Proceed**).

    Uses ``INSURANCE_KYC_POST_MOBILE_DOM_MS`` (default **2000** ms) — not ``networkidle``.
    """
    to = min(int(timeout_ms), 120_000)
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _t(page, HERO_MISP_UI_SETTLE_MS)
    cap_dom = max(200, min(int(INSURANCE_KYC_POST_MOBILE_DOM_MS), 30_000))
    _wait_load_optional(page, min(cap_dom, to))
    _t(page, HERO_MISP_UI_SETTLE_MS)
    if not _kyc_banner_already_verified_aadhaar_visible(page):
        _t(page, HERO_MISP_UI_SETTLE_MS)
    if _kyc_banner_already_verified_aadhaar_visible(page):
        logger.info(
            "Hero Insurance: post-mobile — verified AADHAAR / policy issuance banner; "
            "consent then Proceed (CTA should read Proceed after mobile)."
        )
        return _kyc_click_proceed_after_already_verified_banner(page, kyc_fr, timeout_ms=to)

    logger.info(
        "Hero Insurance: post-mobile — first pass (AADHAAR CARD) did not show verified message; "
        "EXTRACTION branch then shared consent + Proceed."
    )
    kyc_fr_live = _kyc_preferred_kyc_frame(page)
    rec_digits = re.sub(r"\D", "", (post_mobile_recovery_digits or "").strip())[:12]
    if rec_digits:
        logger.info(
            "Hero Insurance: post-mobile — switching OVD to AADHAAR EXTRACTION, re-filling mobile, "
            "then three uploads; merges with consent + Proceed."
        )
        if _kyc_try_aadhaar_extraction_upload_recovery(
            page, kyc_fr_live, rec_digits, timeout_ms=to
        ):
            logger.info(
                "Hero Insurance: AADHAAR EXTRACTION branch — upload inputs ready (or present); "
                "continuing to attach files + CTA."
            )
        else:
            logger.warning(
                "Hero Insurance: AADHAAR EXTRACTION branch did not confirm file inputs after OVD/mobile; "
                "still attempting upload + Proceed."
            )
    else:
        logger.warning(
            "Hero Insurance: post-mobile — no recovery digits for EXTRACTION branch; "
            "upload step may fail."
        )
    return _kyc_proceed_or_upload(
        page,
        timeout_ms=to,
        kyc_local_scan_paths=kyc_local_scan_paths,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )


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
    """After KYC Partner ``select_option``: short settle + optional networkidle (no file scrape)."""
    _ = (ocr_output_dir, subfolder)
    _t(page, 280)
    _wait_load_optional(page, 8_000)
    cap = max(0, int(INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS))
    if cap > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=cap)
        except Exception:
            logger.debug(
                "Hero Insurance: networkidle after KYC partner (timeout=%s ms) timed out — continuing.",
                cap,
            )
    _t(page, 250)


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
        _t(page, 200)
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
    hit the wrong control or rely on order; scoping to the login form matches typical MISP layout.
    """
    try:
        login_forms = scope.locator(
            'form:has(input[type="password"]), '
            'form:has(input[autocomplete="current-password"]), '
            'form:has(input[name*="password" i])'
        )
        if login_forms.count() == 0:
            return False
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
            for pat, dbg in _LOGIN_LABEL_PATTERNS:
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

        for pat, _dbg in _LOGIN_LABEL_PATTERNS:
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
    pause_ms = _MISP_UI_SETTLE_CAP_MS
    for attempt in range(1, max_attempts + 1):
        clicked = _attempt_sign_in_click_once(page, timeout_ms=timeout_ms)
        try:
            url_snip = (page.url or "")[:160]
        except Exception:
            url_snip = ""
        if clicked:
            _t(page, pause_ms)
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
            _t(page, _MISP_UI_SETTLE_CAP_MS)
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
            _t(page, pause_ms)

    return False


def _misp_wait_landing_after_product_nav(page, *, step: str, timeout_ms: int) -> None:
    """
    After **2W** or **New Policy** click: short ``domcontentloaded`` + **UI readiness** instead of a long
    load-state wait (SPAs often do not refire full load).
    ``step``: ``after_2w`` | ``after_new_policy``.
    Max UI readiness wait per step: ``HERO_MISP_LANDING_WAIT_MS`` (default **2500** ms).
    """
    cap = min(max(800, int(timeout_ms)), int(HERO_MISP_LANDING_WAIT_MS))
    short_dom = min(1_500, cap)
    _wait_load_optional(page, short_dom)
    if step == "after_2w":
        try:
            page.locator("#navbarVerticalNav").first.wait_for(state="visible", timeout=cap)
            logger.debug("Hero Insurance: post-2W readiness: #navbarVerticalNav visible")
            return
        except Exception:
            pass
        try:
            page.get_by_text("New Policy", exact=True).first.wait_for(state="visible", timeout=cap)
            logger.debug("Hero Insurance: post-2W readiness: New Policy visible")
            return
        except Exception:
            pass
    elif step == "after_new_policy":
        try:
            page.wait_for_function(
                """() => {
                  const h = (location.href || '').toLowerCase();
                  if (h.includes('ekycpage') || h.includes('kycpage.aspx') || h.includes('/ekyc') || h.includes('kyc.html')) return true;
                  const el = document.querySelector('#ins-mobile-no');
                  if (el && el.offsetParent !== null) return true;
                  const ifr = document.querySelector('iframe[src*="kyc" i], iframe[src*="2w" i]');
                  return !!(ifr && ifr.offsetParent !== null);
                }""",
                timeout=min(cap, 4_500),
            )
            logger.debug("Hero Insurance: post-New Policy readiness: KYC URL/DOM hint")
            return
        except Exception:
            pass
    _wait_load_optional(page, min(2_000, cap))


def _click_2w_icon(page, *, timeout_ms: int) -> None:
    """
    Open **2W** (two-wheeler) product path. Markup varies: ``img[alt]``, tiles, or icon buttons.
    When ``INSURANCE_NAV_IFRAME_SELECTOR`` is set, try 2W locators inside that iframe first.
    """
    _insurance_click_settle(page)

    def _try_click(loc, label: str) -> bool:
        try:
            if loc.count() <= 0:
                return False
            target = loc.first
            target.wait_for(state="visible", timeout=min(timeout_ms, 8_000))
            try:
                target.scroll_into_view_if_needed(timeout=400)
            except Exception:
                pass
            try:
                target.click(timeout=timeout_ms)
            except Exception:
                target.click(timeout=timeout_ms, force=True)
            logger.info("Hero Insurance: clicked 2W control (%s).", label)
            return True
        except Exception:
            return False

    if INSURANCE_NAV_IFRAME_SELECTOR:
        try:
            fl = page.frame_locator(INSURANCE_NAV_IFRAME_SELECTOR)
            nav_try = (
                ('nav iframe [aid="ctl00_TWO"]', fl.locator('[aid="ctl00_TWO"]')),
                ("nav iframe #ctl00_TWO", fl.locator("#ctl00_TWO")),
                ('nav iframe img[alt="2W Icon"]', fl.locator('img[alt="2W Icon"]')),
            )
            for label, loc in nav_try:
                if _try_click(loc, label):
                    _misp_wait_landing_after_product_nav(page, step="after_2w", timeout_ms=timeout_ms)
                    return
        except Exception as exc:
            logger.debug("Hero Insurance: 2W INSURANCE_NAV_IFRAME_SELECTOR: %s", exc)

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
            _misp_wait_landing_after_product_nav(page, step="after_2w", timeout_ms=timeout_ms)
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
            _misp_wait_landing_after_product_nav(page, step="after_2w", timeout_ms=timeout_ms)
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
    to = min(max(100, int(timeout_ms)), 6_000)
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
        trig.scroll_into_view_if_needed(timeout=200)
    except Exception:
        pass
    try:
        trig.click(timeout=to)
        logger.info("Hero Insurance: expanded Policy Issuance (navbarVerticalNav) for New Policy.")
    except Exception as exc:
        logger.warning("Hero Insurance: Policy Issuance expand click failed: %s", exc)
        return

    _t(page, min(50, INSURANCE_CLICK_SETTLE_MS + 15))


def _click_new_policy(page, *, timeout_ms: int) -> None:
    _expand_misp_policy_issuance_nav_if_collapsed(page, timeout_ms=timeout_ms)
    loc = page.get_by_text("New Policy", exact=True)
    loc.first.wait_for(state="visible", timeout=timeout_ms)
    loc.first.click(timeout=timeout_ms)
    logger.info("Hero Insurance: clicked New Policy.")
    _misp_wait_landing_after_product_nav(page, step="after_new_policy", timeout_ms=timeout_ms)


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


def _misp_try_use_insurance_tab_page(
    p,
    before: list,
    base: str,
    cap_ms: int,
    step_label: str,
):
    """
    If ``p`` is not in ``before``, loaded enough to read URL, and matches insurance ``base``, return ``p``.
    Otherwise return ``None`` (Siebel/DMS/blank URLs are rejected).
    """
    if p in before:
        return None
    try:
        if p.is_closed():
            return None
    except Exception:
        return None
    _wait_load_optional(p, min(8_000, cap_ms))
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
        return None
    low = (u or "").lower()
    if not u or "about:blank" in low:
        return None
    if u and _playwright_page_url_matches_site_base(u, base):
        logger.info(
            "Hero Insurance: using new insurance/MISP tab after %s — url=%s",
            step_label,
            u[:200],
        )
        return p
    return None


def _misp_resolve_page_after_possible_new_tab(
    pages_before: list,
    fallback_page,
    *,
    portal_base_url: str,
    timeout_ms: int,
    step_label: str,
) -> tuple[Any, str]:
    """
    Hero MISP often opens the **2W** product path in a **new** tab; **New Policy** and **KYC** follow.
    Prefer a **new** ``Page`` whose URL matches ``portal_base_url`` (insurance/MISP host). **Never** attach
    to a Siebel/DMS tab — those can appear in ``context.pages`` when the operator has both sites open.

    Order: immediate scan of ``context.pages``; then staged ``wait_for_event('page')`` (**400** + **800** + **800** ms,
    total ≤**2** s, capped by ``timeout_ms``); if **no** new ``Page`` object joined the context, return
    ``stayed_on_fallback`` immediately (skip long poll); else legacy polling (120ms) until deadline.

    Returns ``(page, resolution_branch)`` where **branch** is ``immediate_scan`` | ``wait_for_page_event`` |
    ``polling`` | ``stayed_on_fallback`` (no matching new insurance tab in time).

    Callers log **resolver-only** duration to ``Playwright_insurance.txt`` as ``resolver_ms`` on the
    ``tab_resolve`` line (wall time since flow start is separate).
    """
    base = (portal_base_url or "").strip()
    if not base:
        base = (INSURANCE_BASE_URL or "").strip()
    ctx = fallback_page.context
    cap_ms = min(max(5_000, int(timeout_ms)), 45_000)
    before = list(pages_before)

    def _try(p):
        return _misp_try_use_insurance_tab_page(p, before, base, cap_ms, step_label)

    try:
        for p in ctx.pages:
            got = _try(p)
            if got is not None:
                return (got, "immediate_scan")
    except Exception:
        pass

    event_budget_ms = min(2_000, cap_ms)
    event_t0 = time.monotonic()
    for chunk_ms in (400, 800, 800):
        elapsed_ms = (time.monotonic() - event_t0) * 1000
        if elapsed_ms >= event_budget_ms:
            break
        this_chunk = min(
            float(chunk_ms),
            float(event_budget_ms - elapsed_ms),
        )
        if this_chunk < 1.0:
            break
        try:
            new_p = ctx.wait_for_event(
                "page",
                predicate=lambda p: _try(p) is not None,
                timeout=this_chunk,
            )
            if new_p is not None:
                got = _try(new_p)
                if got is not None:
                    return (got, "wait_for_page_event")
        except PlaywrightTimeout:
            pass
        except Exception as exc:
            logger.debug("Hero Insurance: wait_for_event(page) after %s: %s", step_label, exc)

    try:
        before_ids = {id(p) for p in before}
        current_ids = {id(p) for p in ctx.pages}
        if current_ids == before_ids:
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
                    return (fallback_page, "stayed_on_fallback")
            except Exception:
                pass
            return (fallback_page, "stayed_on_fallback")
    except Exception:
        pass

    deadline = time.monotonic() + cap_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            for p in ctx.pages:
                got = _try(p)
                if got is not None:
                    return (got, "polling")
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
            return (fallback_page, "stayed_on_fallback")
    except Exception:
        pass
    return (fallback_page, "stayed_on_fallback")


def _norm_option_label(s: str) -> str:
    """Collapse internal whitespace for matching Playwright label vs fuzzy pick."""
    return re.sub(r"\s+", " ", (s or "").strip())


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
        rows: list[dict[str, Any]] = []
        try:
            raw = sel.locator("option").evaluate_all(
                """els => els.map((e, i) => ({
                    text: (e.textContent || '').trim(),
                    value: (e.value != null ? String(e.value) : ''),
                    index: i
                }))"""
            )
            for x in raw or []:
                if not isinstance(x, dict):
                    continue
                t = str(x.get("text") or "").strip()
                if not t:
                    continue
                rows.append(
                    {
                        "text": t,
                        "value": str(x.get("value") or ""),
                        "index": int(x.get("index", 0)),
                    }
                )
        except Exception:
            n = sel.locator("option").count()
            for i in range(min(n, 400)):
                t = (sel.locator("option").nth(i).inner_text() or "").strip()
                if not t:
                    continue
                try:
                    v = sel.locator("option").nth(i).evaluate("e => String(e.value || '')")
                except Exception:
                    v = ""
                rows.append({"text": t, "value": str(v), "index": i})
        candidates = [
            r["text"]
            for r in rows
            if r["text"] and not r["text"].lower().startswith("--select")
        ]
        if not candidates:
            return False
        q_strip = (query or "").strip()
        chosen_early: dict[str, Any] | None = None
        if q_strip in ("0", "00"):
            for r in rows:
                t = (r.get("text") or "").strip()
                v = (r.get("value") or "").strip()
                if not t or t.lower().startswith("--select"):
                    continue
                if v in ("0", "00"):
                    chosen_early = r
                    break
            if chosen_early is None:
                for r in rows:
                    t = (r.get("text") or "").strip()
                    if not t or t.lower().startswith("--select"):
                        continue
                    if t == "0" or re.match(r"^0(\s|$)", t) or re.match(
                        r"^0\s*(month|year|yr|mo)s?\b", t, re.I
                    ):
                        chosen_early = r
                        break
        pick: str | None
        if chosen_early is not None:
            pick = (chosen_early.get("text") or "").strip() or None
            if not pick:
                pick = fuzzy_best_option_label(query, candidates, min_score=fuzzy_min_score)
        else:
            pick = fuzzy_best_option_label(query, candidates, min_score=fuzzy_min_score)
        if not pick:
            return False
        pick_n = _norm_option_label(pick)
        chosen: dict[str, Any] | None = chosen_early
        if chosen is None or _norm_option_label((chosen.get("text") or "")) != pick_n:
            chosen = None
            for r in rows:
                if r["text"] == pick or _norm_option_label(r["text"]) == pick_n:
                    chosen = r
                    break
            if chosen is None:
                for r in rows:
                    if _norm_option_label(r["text"]) == pick_n:
                        chosen = r
                        break
        if chosen is None:
            return False
        to = min(int(timeout_ms), 12_000)
        short = min(to, 4_000)
        idx = int(chosen["index"])
        val = (chosen.get("value") or "").strip()
        attempts: list[tuple[str, dict[str, Any]]] = [
            ("index", {"index": idx, "timeout": short, "force": True}),
        ]
        if val:
            attempts.append(("value", {"value": val, "timeout": short, "force": True}))
        attempts.append(("label_pick", {"label": chosen["text"], "timeout": short, "force": True}))
        if pick != chosen["text"]:
            attempts.append(("label_fuzzy", {"label": pick, "timeout": short, "force": True}))
        attempts.append(("label_norm", {"label": pick_n, "timeout": to, "force": True}))
        last_exc: Exception | None = None
        for strat_name, kwargs in attempts:
            try:
                sel.select_option(**kwargs)
                logger.info(
                    "Hero Insurance: selected option %r (fuzzy from %r, strategy=%s)",
                    chosen["text"],
                    query[:60],
                    strat_name,
                )
                return True
            except Exception as exc:
                last_exc = exc
                continue
        try:
            visible = sel.is_visible(timeout=800)
        except Exception:
            visible = False
        try:
            if visible:
                sel.select_option(label=chosen["text"], timeout=to)
            else:
                sel.select_option(label=chosen["text"], timeout=to, force=True)
            logger.info("Hero Insurance: selected option %r (fuzzy from %r)", chosen["text"], query[:60])
            return True
        except Exception as exc_final:
            last_exc = exc_final
        logger.warning("Hero Insurance: fuzzy select failed after retries: %s", last_exc)
        return False
    except Exception as exc:
        logger.warning("Hero Insurance: fuzzy select failed: %s", exc)
        return False


def _kyc_find_insurance_company_select_locator(kyc_fr):
    """
    Resolve the Insurance Company native ``<select>`` in the KYC frame (MISP: ``ddlproduct``).
    Label association may point at a non-select wrapper; prefer known ids.
    """
    for css in (
        "#ContentPlaceHolder1_ddlproduct",
        "select#ContentPlaceHolder1_ddlproduct",
        'select[name*="ddlproduct" i]',
        'select[id*="ddlproduct" i]',
    ):
        try:
            loc = kyc_fr.locator(css)
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    try:
        lab = kyc_fr.get_by_label(re.compile(r"Insurance\s*Company\s*\*?", re.I))
        if lab.count() > 0:
            tag = (
                lab.first.evaluate("el => (el && el.tagName) ? el.tagName.toLowerCase() : ''") or ""
            )
            if tag == "select":
                return lab.first
    except Exception:
        pass
    return None


def _kyc_collect_insurer_native_select_option_labels(kyc_fr) -> list[str]:
    """Text of each ``<option>`` under the Insurance Company ``<select>`` (for in-code fuzzy)."""
    sel = _kyc_find_insurance_company_select_locator(kyc_fr)
    if sel is None:
        return []
    return _collect_select_option_labels(sel, skip_select_prefix=True)


def _kyc_try_select_insurer_fuzzy_on_insurance_company_select(
    kyc_fr,
    insurer: str,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> bool:
    """
    ``fuzzy_best_option_label`` + ``select_option`` on the resolved Insurance Company ``<select>``
    (reads ``<option>`` labels inside ``_select_option_fuzzy_in_select``). Prefer this before
    listbox-only scrapers and long ArrowDown loops.
    """
    if not (insurer or "").strip():
        return False
    to = min(int(timeout_ms), 12_000)
    sel = _kyc_find_insurance_company_select_locator(kyc_fr)
    if sel is None:
        return False
    try:
        nopt = sel.locator("option").count()
    except Exception:
        nopt = 0
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "native_select_options",
        f"Insurance Company <select> n_option_nodes={nopt} (fuzzy_best_option_label + select_option)",
    )
    return _select_option_fuzzy_in_select(
        kyc_fr,
        sel,
        insurer,
        timeout_ms=to,
        fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
    )


def _fill_insurance_company_fuzzy_any_visible_select(
    page,
    insurer: str,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
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
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "dom_fuzzy_select_scan",
        f"start n_select_elements={n} (fuzzy match per select)",
    )
    for i in range(n):
        if i > 0 and i % 5 == 0:
            _insurance_kyc_trace(
                ocr_output_dir,
                subfolder,
                "dom_fuzzy_select_scan",
                f"still scanning select index {i}/{n}",
            )
        try:
            loc = selects.nth(i)
            if _select_option_fuzzy_in_select(
                page,
                loc,
                insurer,
                timeout_ms=timeout_ms,
                fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
            ):
                _insurance_kyc_trace(
                    ocr_output_dir,
                    subfolder,
                    "dom_fuzzy_select_scan",
                    f"matched at select index {i}",
                )
                logger.info("Hero Insurance: insurer set via select index %s (fuzzy scan).", i)
                return True
        except Exception:
            continue
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "dom_fuzzy_select_scan",
        "finished no select matched",
    )
    return False


def _kyc_try_set_insurer_via_dom_in_frame(
    kyc_fr,
    insurer: str,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> bool:
    """
    Prefer native ``<select>`` + fuzzy option match on the dedicated Insurance Company control
    (``ddlproduct`` / label-resolved ``<select>``) inside the KYC frame.

    Full-frame ``<select>`` scan and keyboard typing are orchestrated by ``_fill_kyc_ekyc_keyboard_sop``
    via ``_kyc_insurer_attempt_order`` (fuzzy scan last).
    """
    if not (insurer or "").strip():
        return False
    to = min(int(timeout_ms), 12_000)
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "dom_insurer",
        "start native <select> Insurance Company only (ddlproduct/label); no full-frame scan here",
    )
    if _kyc_try_select_insurer_fuzzy_on_insurance_company_select(
        kyc_fr,
        insurer,
        timeout_ms=to,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    ):
        _insurance_kyc_trace(
            ocr_output_dir,
            subfolder,
            "dom_insurer",
            "success via Insurance Company native <select> (in-code fuzzy on <option> labels)",
        )
        logger.info(
            "Hero Insurance: KYC — Insurance Company set via native <select> fuzzy match in frame."
        )
        return True
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "dom_insurer",
        "dedicated native <select> did not commit — keyboard path runs next (scan last resort)",
    )
    return False


def _kyc_force_blur_insurance_company_dropdown(kyc_fr) -> None:
    """Blur active element and click a frame corner so ASP.NET combobox/listbox closes and commits."""
    try:
        kyc_fr.evaluate(
            """() => {
              const a = document.activeElement;
              if (a && typeof a.blur === 'function') a.blur();
            }"""
        )
    except Exception:
        pass
    try:
        kyc_fr.locator("body").click(timeout=300, position={"x": 8, "y": 8})
    except Exception:
        pass


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


def _kyc_collect_dropdown_option_texts(
    root,
    *,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    trace_note: str = "",
) -> list[str]:
    """
    Collect visible option strings; ``root`` is a **Page** or **Frame** (KYC list may be in an iframe).
    When ``trace_note`` is set, logs elapsed ms and option count to ``Playwright_insurance.txt`` (list scan
    + per-option visibility can be slow).
    """
    t0 = time.perf_counter()
    texts: list[str] = []
    try:
        root.wait_for_selector(
            "[role='option'], [role='listbox'] [role='option'], li[role='option'], .ui-menu-item, "
            "ul.dropdown-menu li, div.select2-results__option, .chosen-results li",
            timeout=800,
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
    if trace_note:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _insurance_kyc_trace(
            ocr_output_dir,
            subfolder,
            "dropdown_options",
            f"{trace_note} count={len(texts)} elapsed_ms={elapsed_ms}",
        )
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


def _insurance_frame_from_iframe_selector(page, css: str):
    """Resolve a Playwright ``Frame`` from host-page iframe CSS (``INSURANCE_KYC_IFRAME_SELECTOR`` in this module)."""
    if not (css or "").strip():
        return None
    try:
        loc = page.locator(css).first
        loc.wait_for(state="attached", timeout=8_000)
        handle = loc.element_handle(timeout=5_000)
        if not handle:
            return None
        fr = handle.content_frame()
        return fr
    except Exception:
        return None


def _kyc_preferred_kyc_frame(page):
    """
    Frame whose document contains the Insurance Company label (KYC may render inside an iframe).

    If ``INSURANCE_KYC_IFRAME_SELECTOR`` is set (module constant / trial run), use that iframe first — avoids scanning
    every frame for **Insurance Company** text.
    """
    sel = INSURANCE_KYC_IFRAME_SELECTOR
    if sel:
        fr = _insurance_frame_from_iframe_selector(page, sel)
        if fr:
            try:
                if not fr.is_detached():
                    logger.info(
                        "Hero Insurance: KYC frame from INSURANCE_KYC_IFRAME_SELECTOR (%s).",
                        sel[:100],
                    )
                    return fr
            except Exception:
                pass
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
    # Focus on MISP can return a multi-line blob (label + every option). Do not treat as a single match.
    if d.count("\n") >= 3 and len(d) > 180:
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


def _kyc_blur_insurer_product_select_in_frame(kyc_fr) -> None:
    """Always blur the Insurance Company ``<select>`` by id/name — activeElement can lie after Enter/Tab."""
    try:
        kyc_fr.evaluate(
            """() => {
          const el = document.getElementById('ContentPlaceHolder1_ddlproduct')
            || document.querySelector('select[name="ctl00$ContentPlaceHolder1$ddlproduct"]');
          if (el && el.blur) el.blur();
        }"""
        )
    except Exception:
        pass


def _kyc_aspnet_signal_insurer_committed(kyc_fr, page) -> None:
    """
    WebForms / skinned insurer controls often finalize only after a *real* page/window blur (operators
    report alt-tab fixes stuck state). Must run **before** ``_kyc_force_blur_insurance_company_dropdown``:
    the corner ``body`` click moves focus to ``body``, after which ``change``/``input`` on the combobox
    face no longer runs on the real control (runtime evidence: **H6** snapshot showed ``active`` = ``body``).
    Order: (1) ``input``/``change`` on ``document.activeElement`` if INPUT/TEXTAREA/SELECT, (2) same on
    ``#ContentPlaceHolder1_ddlproduct``, (3) light window ``blur`` events.
    """
    commit_report: dict[str, Any] = {}
    try:
        commit_report["frame"] = kyc_fr.evaluate(
            """() => {
          const r = {
            activeTag: null,
            activeId: null,
            firedActive: false,
            activeCommitTarget: null,
            ddlproduct: null
          };
          const fire = (el) => {
            if (!el) return null;
            try {
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            } catch (e) {}
            return String((el.id || el.name || el.tagName || '')).slice(0, 80);
          };
          const a = document.activeElement;
          if (a && a !== document.body && a !== document.documentElement) {
            const t = (a.tagName || '').toUpperCase();
            r.activeTag = (a.tagName || '').toLowerCase();
            r.activeId = (a.id || '').slice(0, 80);
            if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') {
              r.activeCommitTarget = fire(a);
              r.firedActive = true;
            }
          }
          const sel = document.getElementById('ContentPlaceHolder1_ddlproduct')
            || document.querySelector('select[name*="ddlproduct" i]');
          if (sel) {
            r.ddlproduct = fire(sel) || 'fired';
          } else {
            r.ddlproduct = 'missing';
          }
          return r;
        }"""
        )
    except Exception as exc:
        commit_report["frame_err"] = str(exc)[:120]
    try:
        page.evaluate(
            """() => {
          try { window.dispatchEvent(new Event('blur')); } catch (e) {}
          try { document.dispatchEvent(new Event('blur')); } catch (e) {}
        }"""
        )
    except Exception:
        pass
    # #region agent log
    _dbg_kyc_insurer_tab_ndjson(
        "H6",
        "_kyc_aspnet_signal_insurer_committed:after",
        "post change events (before corner body blur in caller)",
        {**commit_report, **_dbg_kyc_focus_snapshot(kyc_fr, page)},
    )
    # #endregion


def _kyc_tab_out_of_insurer_after_escape(page, kyc_fr) -> None:
    """
    After Enter/Escape on Insurance Company, focus often stays in the combobox until the user Tabs.
    Re-focus the KYC document (iframe click when needed) then send Tab — not configurable via .env.
    """
    # #region agent log
    _dbg_kyc_insurer_tab_ndjson(
        "H3",
        "_kyc_tab_out_of_insurer_after_escape:entry",
        "before iframe/body click and Tab",
        _dbg_kyc_focus_snapshot(kyc_fr, page),
    )
    # #endregion
    try:
        if kyc_fr != page.main_frame:
            try:
                kyc_fr.frame_element().click(timeout=200)
            except Exception:
                pass
            _t(page, 90)
        try:
            kyc_fr.locator("body").click(timeout=200, position={"x": 140, "y": 200})
        except Exception:
            pass
        _t(page, 120)
    except Exception:
        pass
    try:
        page.keyboard.press("Enter")
    except Exception:
        pass
    _t(page, 140)
    _kyc_blur_insurer_product_select_in_frame(kyc_fr)
    _t(page, 80)
    for _ in range(2):
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass
        _t(page, 120)
    # #region agent log
    _dbg_kyc_insurer_tab_ndjson(
        "H5",
        "_kyc_tab_out_of_insurer_after_escape:exit",
        "after Enter blur and 2x Tab",
        _dbg_kyc_focus_snapshot(kyc_fr, page),
    )
    # #endregion


def _kyc_fill_mobile_digits_in_frame(kyc_fr, digits: str, *, timeout_ms: int) -> bool:
    """
    Fill mobile via locators in the KYC frame (does not rely on focus). Covers ASP.NET ids when
    label/placeholder matchers miss after a partial postback. Do not use ``txtFrameNo`` here — that
    control is VIN/Chassis on the post–KYC page, not mobile.
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


def _kyc_file_input_count(root) -> int:
    try:
        n = root.locator('input[type="file"]').count()
        return max(0, int(n))
    except Exception:
        return 0


def _kyc_locator_file_inputs_best(page):
    """
    Prefer ``input[type=file]`` inside the KYC frame; MISP ``ekycpage`` often hosts the form in an iframe
    while ``page.locator`` only sees the main document.
    """
    kyc_fr = _kyc_preferred_kyc_frame(page)
    try:
        loc_fr = kyc_fr.locator('input[type="file"]')
        if loc_fr.count() > 0:
            return loc_fr
    except Exception:
        pass
    return page.locator('input[type="file"]')


# Run inside the KYC **Frame** document after **AADHAAR EXTRACTION** exposes upload rows.
_KYC_FILE_INPUTS_SCRAPE_JS = r"""() => {
  const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
  return inputs.map((el, index) => {
    let bestLabel = '';
    try {
      if (el.labels && el.labels.length) {
        bestLabel = Array.from(el.labels)
          .map((l) => (l.textContent || '').trim())
          .filter(Boolean)
          .join(' ');
      }
    } catch (e) {}
    if (!bestLabel.trim()) {
      let p = el.parentElement;
      for (let d = 0; d < 8 && p; d++, p = p.parentElement) {
        const t = (p.innerText || '').replace(/\s+/g, ' ').trim();
        if (t.length > bestLabel.length && t.length < 500) bestLabel = t;
      }
    }
    return {
      index: index,
      id: el.id || '',
      name: el.name || '',
      className: String(el.className || '').replace(/\s+/g, ' ').trim().slice(0, 120),
      ariaLabel: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      bestLabel: bestLabel.slice(0, 280),
    };
  });
}"""


def _kyc_scrape_file_inputs_metadata(kyc_fr) -> list[dict[str, Any]]:
    """Snapshot each ``input[type=file]`` in the KYC frame (id, name, inferred label text)."""
    try:
        raw = kyc_fr.evaluate(_KYC_FILE_INPUTS_SCRAPE_JS)
        if isinstance(raw, list):
            return [dict(x) for x in raw if isinstance(x, dict)]
    except Exception as exc:
        logger.debug("Hero Insurance: KYC file input scrape evaluate failed: %s", exc)
    return []


def _kyc_meta_match_blob(m: dict[str, Any]) -> str:
    parts = [
        str(m.get("id") or ""),
        str(m.get("name") or ""),
        str(m.get("ariaLabel") or ""),
        str(m.get("title") or ""),
        str(m.get("bestLabel") or ""),
    ]
    return " ".join(p for p in parts if p).lower()


def _kyc_resolve_upload_nth_order(meta: list[dict[str, Any]]) -> tuple[list[int], str]:
    """
    Map upload slots **front → rear → customer photo** to ``Locator.nth`` indices in document order.

    Uses label/id/name text from :func:`_kyc_scrape_file_inputs_metadata`. Falls back to ``[0,1,2]``.
    """
    n = len(meta)
    if n == 0:
        return [], "empty"
    if n < 3:
        return [int(meta[i].get("index", i)) for i in range(n)], "dom_order_partial"

    re_front = re.compile(
        r"aadhaar\s*front|front\s*image|aadhar\s*front|आधार.*front|front\s*side",
        re.I,
    )
    re_rear = re.compile(
        r"aadhaar\s*rear|rear\s*image|aadhar\s*rear|aadhar\s*back|back\s*image|आधार.*rear",
        re.I,
    )
    re_photo = re.compile(
        r"customer\s*photo|photograph|customer\s*picture|customer\s*pic|photo\s*image",
        re.I,
    )

    def find_idx(pat: re.Pattern) -> int | None:
        for m in meta:
            if pat.search(_kyc_meta_match_blob(m)):
                return int(m.get("index", 0))
        return None

    fi = find_idx(re_front)
    ri = find_idx(re_rear)
    pi = find_idx(re_photo)
    if fi is not None and ri is not None and pi is not None and len({fi, ri, pi}) == 3:
        return [fi, ri, pi], "label_three_distinct"

    all_idx = [int(m.get("index", i)) for i, m in enumerate(meta)]
    if fi is not None and ri is not None:
        used = {fi, ri}
        rest = [i for i in all_idx if i not in used]
        if len(rest) == 1:
            return [fi, ri, rest[0]], "label_front_rear_infer_photo"

    return [0, 1, 2], "dom_order_fallback"


def _kyc_note_file_inputs_scrape(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    meta: list[dict[str, Any]],
    order: list[int],
    strategy: str,
) -> None:
    """Append compact JSON to ``Playwright_insurance.txt`` for operator selector work."""
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        return
    try:
        payload = {
            "kyc_file_inputs": meta,
            "upload_nth_order_front_rear_photo": order,
            "upload_order_strategy": strategy,
        }
        line = "kyc_file_inputs_scrape: " + json.dumps(payload, ensure_ascii=False)[:14_000]
        append_playwright_insurance_line_or_dealer_fallback(
            ocr_output_dir,
            subfolder,
            "NOTE",
            line,
        )
    except Exception as exc:
        logger.debug("Hero Insurance: kyc file input scrape NOTE: %s", exc)


def _kyc_dispatch_file_input_dom_events(file_input) -> None:
    """Fire ``input``/``change`` on the file control so ASP.NET / client validators see the new ``FileList``."""
    try:
        file_input.evaluate(
            """el => {
              if (!el || el.tagName !== 'INPUT' || el.type !== 'file') return;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
    except Exception:
        pass


def _kyc_file_input_has_files(file_input) -> bool | None:
    """``True`` / ``False`` if readable; ``None`` if evaluation failed."""
    try:
        v = file_input.evaluate("el => !!(el && el.files && el.files.length > 0)")
        return bool(v)
    except Exception:
        return None


def _kyc_set_one_file_input_chooser_then_direct(
    page,
    file_input,
    *,
    path_str: str | None,
    payload: dict | None,
    timeout_ms: int,
) -> tuple[bool, str | None]:
    """
    Attach one KYC upload file. **Playwright** is most reliable with ``set_input_files`` on the
    ``<input type=file>`` (including hidden ASP.NET controls); iframe + ``expect_file_chooser`` often
    fails or leaves the portal showing *Files could not be loaded*. We **set files first**, dispatch
    ``input``/``change``, verify ``files.length``, then optionally retry via **file chooser** if needed.
    """
    to = min(int(timeout_ms), 60_000)

    def _attach_direct() -> str | None:
        try:
            if path_str:
                file_input.set_input_files(path_str, timeout=to)
            elif payload:
                file_input.set_input_files(
                    {
                        "name": payload["name"],
                        "mimeType": payload["mimeType"],
                        "buffer": payload["buffer"],
                    },
                    timeout=to,
                )
            else:
                return "internal: no path or payload for KYC file input"
        except Exception as exc:
            return str(exc)
        return None

    if not path_str and not payload:
        return False, "internal: no path or payload for KYC file input"

    err = _attach_direct()
    if err:
        return False, err
    _kyc_dispatch_file_input_dom_events(file_input)
    _t(page, 220)
    has = _kyc_file_input_has_files(file_input)
    if has is True:
        return True, None

    # ``files.length`` can read as 0 in some builds even after a good CDP attach; ``None`` = skip chooser.
    if has is False:
        if path_str:
            logger.info(
                "Hero Insurance: KYC file input reports empty after direct attach; "
                "retrying via file chooser + set_files."
            )
            try:
                with page.expect_file_chooser(timeout=min(12_000, to)) as fc_info:
                    file_input.click(timeout=to, force=True)
                fc_info.value.set_files(path_str)
            except Exception as exc:
                return (
                    False,
                    f"file input empty after attach and file chooser failed: {exc!s}",
                )
            _kyc_dispatch_file_input_dom_events(file_input)
            _t(page, 220)
            has2 = _kyc_file_input_has_files(file_input)
            if has2 is False:
                return (
                    False,
                    "portal still shows no file after chooser (use jpg/jpeg/png/img ≤512 KB per MISP).",
                )
        else:
            return (
                False,
                "portal file input shows no file after attach (placeholder may be rejected by MISP).",
            )

    return True, None


def _kyc_js_click_primary_cta_in_document(root) -> bool:
    """
    Last-resort: click the first visible submit/button/link whose label/value looks like KYC CTA.
    MISP often uses **KYC Verification** before redirect and **Proceed** after; values live on ``<input>``.
    """
    try:
        return bool(
            root.evaluate(
                """() => {
                  const re = /^(\\s*)(Proceed|Submit|Continue|Verify|KYC\\s*Verification)(\\s*)$/i;
                  const valRe = /Proceed|Verification|Submit|Continue|Verify|KYC/i;
                  const nodes = Array.from(
                    document.querySelectorAll(
                      'input[type="submit"], input[type="button"], button, a[href], [role="button"]'
                    )
                  );
                  for (const el of nodes) {
                    if (el.disabled) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) continue;
                    const st = window.getComputedStyle(el);
                    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0)
                      continue;
                    const v = (el.value != null && el.value !== '')
                      ? String(el.value).trim()
                      : (el.textContent || '').trim();
                    if (!v) continue;
                    if (re.test(v) || (el.tagName === 'INPUT' && valRe.test(v))) {
                      el.click();
                      return true;
                    }
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


def _kyc_click_proceed_submit_after_kyc_upload(
    page,
    kyc_fr,
    *,
    timeout_ms: int,
) -> str | None:
    """
    Click KYC primary CTA after uploads (KYC frame first, then host page). MISP labels vary:
    **Proceed**, **KYC Verification**, **Submit**, **Continue**, **Verify**; also ``<input>`` ``value=``.
    """
    to = min(int(timeout_ms), 45_000)
    name_patterns = (
        re.compile(r"^\s*Proceed\s*$", re.I),
        re.compile(r"^\s*KYC\s*Verification\s*$", re.I),
        re.compile(r"^\s*Submit\s*$", re.I),
        re.compile(r"^\s*Continue\s*$", re.I),
        re.compile(r"^\s*Verify\s*$", re.I),
    )
    for root in (kyc_fr, page):
        for pat in name_patterns:
            try:
                b = root.get_by_role("button", name=pat)
                if b.count() > 0 and b.first.is_visible(timeout=2_000):
                    b.first.click(timeout=to)
                    logger.info(
                        "Hero Insurance: post-KYC-upload clicked button (%s).",
                        pat.pattern[:60],
                    )
                    return None
            except Exception:
                continue
        try:
            ln = root.get_by_role("link", name=re.compile(r"Proceed|Verification|Submit|Continue", re.I))
            if ln.count() > 0 and ln.first.is_visible(timeout=1_500):
                ln.first.click(timeout=to)
                logger.info("Hero Insurance: post-KYC-upload clicked link CTA.")
                return None
        except Exception:
            pass
        try:
            inp = root.locator(
                'input[type="submit"][value*="Proceed" i], input[type="button"][value*="Proceed" i], '
                'input[type="submit"][value*="Verification" i], input[type="button"][value*="Verification" i], '
                'input[type="submit"][value*="KYC" i], input[type="button"][value*="KYC" i], '
                'input[type="submit"][value*="Submit" i], input[type="button"][value*="Submit" i], '
                'input[type="submit"][value*="Continue" i], input[type="button"][value*="Continue" i], '
                'input[type="submit"][value*="Verify" i], input[type="button"][value*="Verify" i]'
            )
            if inp.count() > 0 and inp.first.is_visible(timeout=2_000):
                inp.first.click(timeout=to)
                logger.info("Hero Insurance: post-KYC-upload clicked input[type=submit|button] CTA.")
                return None
        except Exception:
            pass
    for root in (kyc_fr, page):
        if _kyc_js_click_primary_cta_in_document(root):
            logger.info("Hero Insurance: post-KYC-upload clicked CTA via JS scan.")
            return None
    return (
        "KYC uploads done but no Proceed / Submit / Continue / KYC Verification control was clicked — "
        "complete KYC manually or check CTA visibility."
    )


def _kyc_set_ovd_aadhaar_extraction_in_frame(kyc_fr, *, timeout_ms: int) -> bool:
    """
    Set OVD to **AADHAAR EXTRACTION** in the KYC frame.

    Used only **after** the first pass (**AADHAAR CARD** + mobile) when the verified message did not
    appear — not for the initial OVD selection (initial pass remains **AADHAAR CARD** only).
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
                if "AADHAAR" in t and "EXTRACTION" in t:
                    val = opts.nth(i).get_attribute("value")
                    if val:
                        loc.first.select_option(value=val, timeout=to, force=True)
                    else:
                        loc.first.select_option(label=t, timeout=to, force=True)
                    ovd_ok = True
                    logger.info(
                        "Hero Insurance: KYC — OVD set to AADHAAR EXTRACTION (DOM select in frame)."
                    )
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
                label=re.compile(r"AADHAAR\s*EXTRACTION", re.I), timeout=to, force=True
            )
            ovd_ok = True
            logger.info("Hero Insurance: KYC — OVD AADHAAR EXTRACTION via label+following select.")
        except Exception:
            try:
                ok_js = kyc_fr.evaluate(
                    """() => {
                      const sels = Array.from(document.querySelectorAll('select'));
                      for (const s of sels) {
                        for (const o of s.options) {
                          const t = (o.textContent || '').trim();
                          if (/AADHAAR/i.test(t) && /EXTRACTION/i.test(t)) {
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
                    logger.info("Hero Insurance: KYC — OVD AADHAAR EXTRACTION via JS select scan.")
            except Exception:
                pass
    return ovd_ok


def _kyc_try_aadhaar_extraction_upload_recovery(
    page,
    kyc_fr,
    digits: str,
    *,
    timeout_ms: int,
) -> bool:
    """
    Second KYC branch only: after **AADHAAR CARD** + mobile did **not** show the verified banner,
    switch OVD to **AADHAAR EXTRACTION**, re-enter mobile, and wait for the upload section.

    Returns True when at least one ``input[type=file]`` appears (KYC frame or main page) after the switch.
    """
    to = min(int(timeout_ms), 60_000)
    d = re.sub(r"\D", "", (digits or "").strip())[:12]
    if not d:
        return False
    if not _kyc_set_ovd_aadhaar_extraction_in_frame(kyc_fr, timeout_ms=to):
        return False
    _kyc_restore_kyc_partner_to_default_label(kyc_fr, page, timeout_ms=to)
    try:
        kyc_fr.locator(
            'input[type="tel"], input[name*="mobile" i], input[id*="mobile" i], '
            'input[id*="Mobile" i], input[name*="MobileNo" i]'
        ).first.wait_for(state="visible", timeout=min(8_000, to))
    except Exception:
        _t(page, 400)
    _kyc_fill_mobile_digits_in_frame(kyc_fr, d, timeout_ms=to)
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _t(page, HERO_MISP_UI_SETTLE_MS)
    cap_dom = max(200, min(int(INSURANCE_KYC_POST_MOBILE_DOM_MS), 30_000))
    _wait_load_optional(page, min(cap_dom, to))
    _t(page, HERO_MISP_UI_SETTLE_MS)
    poll_deadline = time.monotonic() + min(12.0, to / 1000.0)
    while time.monotonic() < poll_deadline:
        fr = _kyc_preferred_kyc_frame(page)
        if _kyc_file_input_count(fr) > 0 or _kyc_file_input_count(page) > 0:
            return True
        _t(page, 450)
    fr = _kyc_preferred_kyc_frame(page)
    return _kyc_file_input_count(fr) > 0 or _kyc_file_input_count(page) > 0


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


def _kyc_dom_fill_ovd_mobile_consent_in_frame(
    kyc_fr,
    mobile: str,
    *,
    timeout_ms: int,
    kyc_local_scan_paths: list[str] | None = None,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
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

    # Mobile often appears or enables only after OVD is set. Do **not** wait on ``txtFrameNo`` — that is
    # the VIN/Chassis control on a **later** page (``divtxtFrameNo`` / ``ctl00_ContentPlaceHolder1_txtFrameNo``).
    try:
        kyc_fr.locator(
            'input[type="tel"], input[name*="mobile" i], input[id*="mobile" i], '
            'input[id*="Mobile" i], input[name*="MobileNo" i]'
        ).first.wait_for(state="visible", timeout=min(12_000, to))
    except Exception:
        _t(pg, 450)

    mob_filled = _kyc_fill_mobile_digits_in_frame(kyc_fr, digits, timeout_ms=to)
    if not mob_filled:
        return "KYC keyboard SOP: could not fill mobile in KYC frame after OVD (DOM fallback)."

    _t(pg, 220)
    return _kyc_post_mobile_entry_branch(
        kyc_fr.page,
        kyc_fr,
        timeout_ms=to,
        post_mobile_recovery_digits=digits,
        kyc_local_scan_paths=kyc_local_scan_paths,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )


def _kyc_insurer_label_for_misp(values: dict) -> str:
    """
    Portal label for Insurance Company: dealer ``prefer_insurer`` when it fuzzy-matches the merged
    details-sheet insurer (≥20% ``SequenceMatcher``), else ``insurer`` from ``build_insurance_fill_values``.
    """
    prefer = clean_text(values.get("prefer_insurer"))
    merged = clean_text(values.get("insurer_merged_before_prefer") or values.get("insurer"))
    current = clean_text(values.get("insurer"))
    if prefer and merged and insurer_prefer_matches(merged, prefer, min_ratio=0.20):
        return prefer
    return current


def _kyc_run_insurer_keyboard_match_attempt(
    page,
    kyc_fr,
    insurer_label: str,
    cap: int,
    ad_max: int,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> tuple[str | None, bool, bool]:
    """
    Keyboard focus chain + type + ArrowDown insurer match. Does **not** run full-frame fuzzy select
    (that is a separate strategy step). Returns ``(error_msg_or_none, matched, keyboard_native_select_committed)``.
    """
    keyboard_native_select_committed = False
    matched = False
    logger.debug("Hero Insurance: KYC keyboard SOP — starting (focus chain).")
    try:
        page.bring_to_front()
    except Exception:
        pass
    if kyc_fr != page.main_frame:
        try:
            kyc_fr.frame_element().click(timeout=200)
            _t(page, 100)
        except Exception as exc:
            logger.debug("Hero Insurance: KYC iframe host click: %s", exc)
    try:
        kyc_fr.locator("body").click(timeout=200, position={"x": 160, "y": 220})
    except Exception:
        try:
            page.locator("body").click(timeout=200, position={"x": 40, "y": 40})
        except Exception:
            try:
                page.mouse.click(80, 200)
            except Exception:
                pass
    _t(page, 100)

    ic_clicked = _kyc_try_click_insurance_company_field(kyc_fr, timeout_ms=cap)
    if ic_clicked:
        logger.info("Hero Insurance: KYC keyboard — focused Insurance Company via click in frame.")
        _t(page, 100)
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
        page.keyboard.type(
            insurer_label[:96],
            delay=max(1, int(KYC_KEYBOARD_INSURER_TYPE_DELAY_MS)),
        )
    except Exception as exc:
        return (f"KYC keyboard SOP: could not type insurer: {exc!s}", False, False)
    _t(page, 100)

    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "keyboard_sop",
        "typed insurer text — merge native <select><option> labels + listbox rows for in-code fuzzy",
    )
    native_opts = _kyc_collect_insurer_native_select_option_labels(kyc_fr)
    listbox_opts = _kyc_collect_dropdown_option_texts(
        kyc_fr,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        trace_note="first_collect_after_type_listbox_only",
    )
    seen_m: set[str] = set()
    opts: list[str] = []
    for o in native_opts + listbox_opts:
        if o and o not in seen_m:
            seen_m.add(o)
            opts.append(o)
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "keyboard_sop",
        f"merged options native_n={len(native_opts)} listbox_n={len(listbox_opts)} "
        f"combined_n={len(opts)}",
    )
    pick = (
        fuzzy_best_option_label(insurer_label, opts, min_score=KYC_INSURER_FUZZY_MIN_SCORE)
        if opts
        else None
    )
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "keyboard_sop",
        f"fuzzy_best_option_label on merged list: n_options={len(opts)} pick={pick!r}",
    )
    if pick and not (pick or "").strip().lower().startswith("--select"):
        try:
            sel_apply = _kyc_find_insurance_company_select_locator(kyc_fr)
            if sel_apply is not None:
                sel_apply.select_option(
                    label=pick, timeout=min(cap, 8_000), force=True
                )
                matched = True
                keyboard_native_select_committed = True
                _insurance_kyc_trace(
                    ocr_output_dir,
                    subfolder,
                    "keyboard_sop",
                    f"applied native select_option(label) from in-code fuzzy pick={pick!r}",
                )
        except Exception as exc:
            logger.debug(
                "Hero Insurance: KYC select_option by fuzzy pick failed (will use display/ArrowDown): %s",
                exc,
            )
    if not matched:
        shown = _kyc_read_focused_control_text(page)
        matched = bool(pick) and not (pick or "").strip().lower().startswith("--select") and (
            _kyc_insurer_display_matches(insurer_label, shown)
            or _kyc_insurer_display_matches(insurer_label, pick or "")
        )
    if not matched:
        _insurance_kyc_trace(
            ocr_output_dir,
            subfolder,
            "keyboard_sop",
            f"initial fuzzy/display match failed — ArrowDown loop max={ad_max} "
            f"(each step re-collects options + fuzzy match; can be slow)",
        )
        for ad_i in range(ad_max):
            _insurance_kyc_trace(
                ocr_output_dir,
                subfolder,
                "keyboard_sop",
                f"ArrowDown step {ad_i + 1}/{ad_max} (press ArrowDown; then dropdown_options trace arrow_loop_{ad_i})",
            )
            try:
                page.keyboard.press("ArrowDown")
            except Exception:
                pass
            _t(page, max(30, int(KYC_KEYBOARD_INSURER_ARROW_DOWN_STEP_MS)))
            shown = _kyc_read_focused_control_text(page)
            if _kyc_insurer_display_matches(insurer_label, shown):
                matched = True
                _insurance_kyc_trace(
                    ocr_output_dir,
                    subfolder,
                    "keyboard_sop",
                    f"matched on ArrowDown at iteration {ad_i} shown={shown[:80]!r}",
                )
                logger.info(
                    "Hero Insurance: KYC keyboard — insurer matched on ArrowDown (%r).",
                    shown[:90],
                )
                break
            opts2_lb = _kyc_collect_dropdown_option_texts(
                kyc_fr,
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
                trace_note=f"arrow_loop_{ad_i}",
            )
            seen2: set[str] = set()
            opts2: list[str] = []
            for o in native_opts + opts2_lb:
                if o and o not in seen2:
                    seen2.add(o)
                    opts2.append(o)
            if opts2:
                cand = fuzzy_best_option_label(
                    insurer_label, opts2, min_score=KYC_INSURER_FUZZY_MIN_SCORE
                )
                if cand and _kyc_insurer_display_matches(insurer_label, cand):
                    matched = True
                    _insurance_kyc_trace(
                        ocr_output_dir,
                        subfolder,
                        "keyboard_sop",
                        f"matched from list at iteration {ad_i} cand={cand[:80]!r}",
                    )
                    logger.info(
                        "Hero Insurance: KYC keyboard — insurer matched from list (%r).",
                        (cand or "")[:90],
                    )
                    break
    return (None, matched, keyboard_native_select_committed)


def _fill_kyc_ekyc_keyboard_sop(
    page,
    values: dict,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    t0_flow: float | None = None,
    kyc_local_scan_paths: list[str] | None = None,
) -> str | None:
    """
    Keyboard SOP for ``ekycpage.aspx`` (focus document → Tab to Insurance Company → type to filter →
    ArrowDown until fuzzy match → Enter → Tab to OVD → ArrowDown to Aadhaar → Tab to mobile → type →
    Tab to consent → Space). Tab/down counts: ``KYC_KEYBOARD_*`` env vars.

    When ``t0_flow`` is set, appends ``kyc_elapsed`` ``NOTE`` lines at insurer/KYC-partner/OVD/mobile boundaries.

    **Insurer strategies** (see ``_kyc_insurer_attempt_order``): per-portal cache file prefers the last
    successful strategy (**dom_native**, **keyboard_chain**, or **fuzzy_scan**). Full-frame
    ``_fill_insurance_company_fuzzy_any_visible_select`` is **always** the last step when earlier steps fail.
    """
    insurer_label = _kyc_insurer_label_for_misp(values)
    mobile = (values.get("mobile_number") or "").strip()
    if not insurer_label:
        return "insurer is empty for KYC keyboard SOP."
    if not mobile:
        return "customer_master.mobile_number is empty for KYC keyboard SOP."

    prefer_v = clean_text(values.get("prefer_insurer"))
    merged_v = clean_text(values.get("insurer_merged_before_prefer") or values.get("insurer"))
    type_prefer_skip_dom = bool(
        prefer_v
        and merged_v
        and insurer_prefer_matches(merged_v, prefer_v, min_ratio=0.20)
    )

    cap = min(int(timeout_ms), 120_000)
    kyc_fr = _kyc_preferred_kyc_frame(page)
    ad_max = max(1, KYC_KEYBOARD_INSURER_ARROW_DOWN_MAX)
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "keyboard_sop",
        f"start label_len={len(insurer_label)} arrow_down_max={ad_max} "
        f"prefer_insurer_active={type_prefer_skip_dom} (insurer strategies: see insurer_attempt_order line)",
    )
    if type_prefer_skip_dom:
        logger.info(
            "Hero Insurance: KYC — prefer_insurer matches merged (≥20%%); "
            "portal label %r — trying DOM native <select> before keyboard.",
            (insurer_label[:80] + "…") if len(insurer_label) > 80 else insurer_label,
        )
    cached = _kyc_insurer_strategy_cache_read()
    attempt_order = _kyc_insurer_attempt_order(cached)
    _insurance_kyc_trace(
        ocr_output_dir,
        subfolder,
        "keyboard_sop",
        f"insurer_attempt_order={','.join(attempt_order)} strategy_cache={cached!r}",
    )

    dom_ok = False
    matched = False
    keyboard_native_select_committed = False
    winning: str | None = None

    for strat in attempt_order:
        if strat == KYC_INSURER_STRATEGY_DOM_NATIVE:
            dom_ok = _kyc_try_set_insurer_via_dom_in_frame(
                kyc_fr,
                insurer_label,
                timeout_ms=min(cap, 8_000),
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
            if dom_ok:
                _insurance_kyc_trace(
                    ocr_output_dir,
                    subfolder,
                    "kyc_path",
                    "insurer=DOM (native <select> fuzzy match in frame) — keyboard typing + ArrowDown loop skipped",
                )
                logger.info(
                    "Hero Insurance: KYC — insurer set via DOM in frame (skipped keyboard typing)."
                )
                winning = strat
                break
        elif strat == KYC_INSURER_STRATEGY_KEYBOARD_CHAIN:
            kb_err, matched, keyboard_native_select_committed = _kyc_run_insurer_keyboard_match_attempt(
                page,
                kyc_fr,
                insurer_label,
                cap,
                ad_max,
                ocr_output_dir,
                subfolder,
            )
            if kb_err:
                return kb_err
            if matched:
                kb_reason = (
                    "dom_failed_after_prefer_label"
                    if type_prefer_skip_dom
                    else "keyboard_chain"
                )
                _insurance_kyc_trace(
                    ocr_output_dir,
                    subfolder,
                    "kyc_path",
                    f"insurer=KEYBOARD ({kb_reason}) — type label, merge native <option> + listbox texts, "
                    f"in-code fuzzy; ArrowDown only if still unmatched",
                )
                winning = strat
                break
        elif strat == KYC_INSURER_STRATEGY_FUZZY_SCAN:
            scan_ok = _fill_insurance_company_fuzzy_any_visible_select(
                kyc_fr,
                insurer_label,
                timeout_ms=min(cap, 8_000),
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
            if scan_ok:
                matched = True
                keyboard_native_select_committed = True
                _insurance_kyc_trace(
                    ocr_output_dir,
                    subfolder,
                    "keyboard_sop",
                    "insurer set via dom_fuzzy_select_scan (strategy step; full-frame select last)",
                )
                logger.info(
                    "Hero Insurance: KYC — insurer set via full-frame select scan (ordered last)."
                )
                winning = strat
                break

    # #region agent log
    _dbg_kyc_insurer_tab_ndjson(
        "H1",
        "fill_kyc_ekyc_keyboard_sop:after_insurer_strategies",
        "dom_ok matched winning",
        {
            "dom_ok": bool(dom_ok),
            "matched": bool(matched),
            "winning": winning,
            "type_prefer_skip_dom": bool(type_prefer_skip_dom),
            "will_skip_keyboard_and_maybe_tab_out": bool(dom_ok),
            **_dbg_kyc_focus_snapshot(kyc_fr, page),
        },
    )
    # #endregion

    if not dom_ok and not matched:
        return (
            "KYC keyboard SOP: could not match insurer after typing and ArrowDown. "
            f"insurer={insurer_label[:48]!r}"
        )

    if winning:
        _kyc_insurer_strategy_cache_write(winning)

    if not dom_ok and matched:
        if keyboard_native_select_committed:
            _insurance_kyc_trace(
                ocr_output_dir,
                subfolder,
                "keyboard_sop",
                "insurer set via native select_option — skip Enter/Tab/Escape/tab-out (already committed)",
            )
            _kyc_aspnet_signal_insurer_committed(kyc_fr, page)
            _kyc_force_blur_insurance_company_dropdown(kyc_fr)
            _t(page, 80)
        else:
            _insurance_kyc_trace(
                ocr_output_dir,
                subfolder,
                "keyboard_sop",
                "insurer row matched — commit keys (Enter/Escape) then aspnet/tab-out sequence",
            )
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            _t(page, 100)
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            _t(page, 120)
            try:
                page.keyboard.press("Tab")
            except Exception:
                pass
            _t(page, 100)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            _t(page, 180)
            _kyc_aspnet_signal_insurer_committed(kyc_fr, page)
            _kyc_force_blur_insurance_company_dropdown(kyc_fr)
            _kyc_tab_out_of_insurer_after_escape(page, kyc_fr)
            _kyc_blur_if_insurer_product_select_focused(kyc_fr)
            _kyc_blur_insurer_product_select_in_frame(kyc_fr)
            _t(page, 120)
    elif dom_ok:
        _kyc_aspnet_signal_insurer_committed(kyc_fr, page)
        _kyc_force_blur_insurance_company_dropdown(kyc_fr)
        _t(page, 80)
    # #region agent log
    _dbg_kyc_insurer_tab_ndjson(
        "H1",
        "fill_kyc_ekyc_keyboard_sop:before_nav_after_insurer",
        "pre _hero_insurance_kyc_nav_after_insurer_commit",
        {
            "dom_ok": bool(dom_ok),
            "keyboard_tab_out_ran": not bool(dom_ok),
            **_dbg_kyc_focus_snapshot(kyc_fr, page),
        },
    )
    # #endregion
    # Proposer/OVD are often not in the DOM until insurer is chosen.
    # eKYC keyboard SOP: use light nav — insurer path already sent Enter/Tab/Escape or DOM select_option;
    # extra Enter+Tab+tab-away duplicated commits and moved focus to KYC Partner.
    _hero_insurance_kyc_nav_after_insurer_commit(
        page,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        light=True,
    )
    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "after_insurer_nav"
    )
    _kyc_select_kyc_partner_if_available(page, kyc_fr, values, timeout_ms=cap)
    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "after_kyc_partner_select"
    )
    _hero_insurance_kyc_nav_after_kyc_partner_commit(
        page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
    )
    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "after_kyc_partner_nav"
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
        _t(page, max(40, int(KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS)))
    if not ovd_ok:
        logger.warning(
            "Hero Insurance: KYC keyboard — OVD not set via DOM or ArrowDown (last focus text=%r); "
            "using DOM fills inside KYC frame.",
            (last_shown or "")[:160],
        )
        return _kyc_dom_fill_ovd_mobile_consent_in_frame(
            kyc_fr,
            mobile,
            timeout_ms=timeout_ms,
            kyc_local_scan_paths=kyc_local_scan_paths,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )

    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "after_ovd_ready"
    )

    _kyc_restore_kyc_partner_to_default_label(kyc_fr, page, timeout_ms=cap)

    _kyc_blur_if_insurer_product_select_focused(kyc_fr)
    _t(page, 280)
    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "before_mobile_fill"
    )

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
            page.keyboard.type(
                digits,
                delay=max(1, int(KYC_KEYBOARD_MOBILE_TYPE_DELAY_MS)),
            )
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
    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "before_post_mobile_branch"
    )
    return _kyc_post_mobile_entry_branch(
        page,
        kyc_fr,
        timeout_ms=cap,
        post_mobile_recovery_digits=digits,
        kyc_local_scan_paths=kyc_local_scan_paths,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )


def _fill_insurance_company_and_ovd_mobile_consent(
    page,
    values: dict,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    t0_flow: float | None = None,
) -> str | None:
    """Returns error message or None on success."""
    kyc_local_scan_paths = _kyc_local_scan_paths_from_values(values)
    kyc_insurer_resolved = _kyc_insurer_label_for_misp(values)
    append_playwright_insurance_line_or_dealer_fallback(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"KYC Insurance Company value to use: {kyc_insurer_resolved!r}",
    )
    if KYC_USE_KEYBOARD_EKYC_SOP and _kyc_url_looks_like_ekyc_page(page):
        return _fill_kyc_ekyc_keyboard_sop(
            page,
            values,
            timeout_ms=timeout_ms,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            t0_flow=t0_flow,
            kyc_local_scan_paths=kyc_local_scan_paths,
        )

    insurer = _kyc_insurer_label_for_misp(values)
    mobile = (values.get("mobile_number") or "").strip()

    # --- Insurance Company (dropdown / search; match insurer from details sheet / DB) ---
    if insurer:
        filled = False
        insurer_via_native_select = False
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
                insurer_via_native_select = True
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
                        insurer_via_native_select = True
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
                    insurer_via_native_select = True
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
            page,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            light=bool(
                HERO_MISP_LIGHT_NAV_AFTER_DOM_INSURER and insurer_via_native_select
            ),
        )
        _insurance_kyc_flow_elapsed_note(
            ocr_output_dir, subfolder, t0_flow, "after_insurer_nav"
        )
        kyc_fr_dom = _kyc_preferred_kyc_frame(page)
        _kyc_select_kyc_partner_if_available(
            page, kyc_fr_dom, values, timeout_ms=timeout_ms
        )
        _insurance_kyc_flow_elapsed_note(
            ocr_output_dir, subfolder, t0_flow, "after_kyc_partner_select"
        )
        _hero_insurance_kyc_nav_after_kyc_partner_commit(
            page, ocr_output_dir=ocr_output_dir, subfolder=subfolder
        )
        _insurance_kyc_flow_elapsed_note(
            ocr_output_dir, subfolder, t0_flow, "after_kyc_partner_nav"
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

    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "after_ovd_ready"
    )

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
    _insurance_kyc_flow_elapsed_note(
        ocr_output_dir, subfolder, t0_flow, "before_post_mobile_branch"
    )
    mob_digits = re.sub(r"\D", "", mobile)[:12]
    return _kyc_post_mobile_entry_branch(
        page,
        _kyc_preferred_kyc_frame(page),
        timeout_ms=timeout_ms,
        post_mobile_recovery_digits=mob_digits,
        kyc_local_scan_paths=kyc_local_scan_paths,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )


def _kyc_proceed_or_upload(
    page,
    *,
    timeout_ms: int,
    kyc_local_scan_paths: list[str] | None = None,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    Invoked from ``_kyc_post_mobile_entry_branch`` when the verified-banner text is **not** shown
    after mobile entry (same page step where three ``input[type=file]`` typically appear).

    - If legacy "already done" body text matches, consent + **Proceed**.
    - Else attach three files: prefer paths from **Uploaded scans** (``kyc_local_scan_paths``: front, rear,
      front again for customer photo), else minimal placeholder PNGs; then consent + **Proceed** / Submit / Continue.

    File inputs are resolved by scraping **id** / **name** / label text in the KYC frame after **AADHAAR EXTRACTION**
    (see ``_kyc_scrape_file_inputs_metadata`` / ``_kyc_resolve_upload_nth_order``); order is logged to
    ``Playwright_insurance.txt`` when ``ocr_output_dir`` / ``subfolder`` are set.
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
            _wait_load_optional(page, min(25_000, timeout_ms * 4))
            _t(page, 500)
            return None
        except Exception:
            try:
                page.get_by_text(re.compile(r"^\s*Proceed\s*$", re.I)).first.click(timeout=timeout_ms)
                _wait_load_optional(page, min(25_000, timeout_ms * 4))
                _t(page, 500)
                return None
            except Exception as exc:
                return f"KYC already done but Proceed click failed: {exc!s}"

    payloads = insurance_kyc_png_payloads()
    kyc_fr = _kyc_preferred_kyc_frame(page)
    files = _kyc_locator_file_inputs_best(page)
    n = files.count()
    if n <= 0:
        return (
            "KYC upload expected (no 'already done' message) but no file inputs found. "
            "Complete KYC manually or adjust selectors."
        )
    if n < 3:
        return (
            f"KYC AADHAAR EXTRACTION upload section expected 3 file inputs; found {n}. "
            "Complete KYC manually."
        )

    meta = _kyc_scrape_file_inputs_metadata(kyc_fr)
    nth_order, order_strategy = _kyc_resolve_upload_nth_order(meta)
    _kyc_note_file_inputs_scrape(
        ocr_output_dir, subfolder, meta, nth_order, order_strategy
    )
    if len(nth_order) < 3:
        return (
            f"KYC file input mapping failed (need 3 slots; got {nth_order!r}). "
            "Check NOTE kyc_file_inputs_scrape in Playwright_insurance.txt."
        )

    use_local = False
    if kyc_local_scan_paths and len(kyc_local_scan_paths) >= 3:
        missing = [p for p in kyc_local_scan_paths[:3] if not Path(p).is_file()]
        if missing:
            return (
                "KYC upload requires Aadhar.jpg and Aadhar_back.jpg under Uploaded scans for this subfolder; "
                f"missing or not readable: {', '.join(missing[:3])}"
            )
        use_local = True

    slot_names = ("front (Aadhaar)", "rear (Aadhaar)", "customer photo")
    for slot_i, nth_i in enumerate(nth_order[:3]):
        path_str = kyc_local_scan_paths[slot_i] if use_local else None
        payload = None if use_local else payloads[slot_i]
        ok, err_msg = _kyc_set_one_file_input_chooser_then_direct(
            page,
            files.nth(nth_i),
            path_str=path_str,
            payload=payload,
            timeout_ms=timeout_ms,
        )
        if not ok:
            return (
                f"KYC file upload failed ({slot_names[slot_i]}, nth={nth_i}): {err_msg}"
            )
        logger.info(
            "Hero Insurance: KYC file slot %s (%s) attached via nth=%s (%s).",
            slot_i + 1,
            slot_names[slot_i],
            nth_i,
            "Uploaded scans" if use_local else "placeholder PNG",
        )

    _t(page, 400)
    _kyc_ensure_consent_checked_before_kyc_cta(page)
    proceed_err = _kyc_click_proceed_submit_after_kyc_upload(
        page, kyc_fr, timeout_ms=timeout_ms
    )
    if proceed_err:
        return proceed_err
    _wait_load_optional(page, min(25_000, timeout_ms * 4))
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
    t0_flow = time.monotonic()
    _insurance_click_settle(page)
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
            "Hero Insurance: no Sign In / Login control was clicked — see debug logs for page context."
        )
        if not _still_on_heroinsurance_misp_partner_login(page):
            append_playwright_insurance_line_or_dealer_fallback(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: past partner login URL — Sign In automation skipped",
            )
        else:
            append_playwright_insurance_line_or_dealer_fallback(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: Sign In not auto-clicked — password field not ready or "
                "Sign In did not leave partner login; complete login manually if needed.",
            )
    _hero_misp_after_sign_in_settle(page)

    page, err_2w = _misp_click_nav_step(
        page, _click_2w_icon, "2W Icon",
        portal_base_url=portal_base_url, timeout_ms=timeout_ms,
        ocr_output_dir=ocr_output_dir, subfolder=subfolder, t0_flow=t0_flow,
    )
    if err_2w:
        return err_2w
    _insurance_click_settle(page)

    page, err_np = _misp_click_nav_step(
        page, _click_new_policy, "New Policy",
        portal_base_url=portal_base_url, timeout_ms=timeout_ms,
        ocr_output_dir=ocr_output_dir, subfolder=subfolder, t0_flow=t0_flow,
    )
    if err_np:
        return err_np

    if not values:
        logger.info("Hero Insurance: no DB values — stopping after New Policy.")
        return None

    _insurance_click_settle(page)
    err = _fill_insurance_company_and_ovd_mobile_consent(
        page,
        values,
        timeout_ms=timeout_ms,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        t0_flow=t0_flow,
    )
    if err:
        return err

    _insurance_click_settle(page)
    _wait_load_optional(page, min(25_000, timeout_ms * 4))
    _insurance_click_settle(page)
    return None
def _proposal_log(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    step_id: str,
    detail: str,
) -> None:
    msg = f"proposal step={step_id} {detail}"
    logger.info("Hero Insurance: %s", msg)
    append_playwright_insurance_line(ocr_output_dir, subfolder, "NOTE", msg)


def _read_locator_value_snapshot(locator) -> dict[str, Any]:
    """Read back value/checked/selected text for proposal verification (single-element locator)."""
    try:
        tag = (
            locator.evaluate("el => el && el.tagName ? el.tagName.toLowerCase() : ''") or ""
        ).lower()
    except Exception:
        return {"kind": "unknown", "error": "evaluate_failed"}
    if tag == "select":
        try:
            t = locator.evaluate(
                """el => {
                const i = el.selectedIndex;
                if (i < 0) return '';
                const o = el.options[i];
                return o ? (o.textContent || '').trim() : '';
              }"""
            )
            return {"kind": "select", "selected_text": (t or "").strip()}
        except Exception:
            return {"kind": "select", "selected_text": ""}
    if tag == "input":
        typ = (locator.get_attribute("type") or "").lower()
        if typ in ("checkbox", "radio"):
            try:
                return {"kind": typ, "checked": locator.is_checked()}
            except Exception:
                return {"kind": typ, "checked": None}
        try:
            return {"kind": "input", "value": (locator.input_value() or "").strip()}
        except Exception:
            return {"kind": "input", "value": ""}
    if tag == "textarea":
        try:
            return {"kind": "textarea", "value": (locator.input_value() or "").strip()}
        except Exception:
            return {"kind": "textarea", "value": ""}
    return {"kind": tag, "raw": ""}


def _proposal_read_input_value_best_effort(el) -> str:
    """``input_value()`` then DOM ``.value`` (some MISP fields stay empty on Playwright read until events)."""
    try:
        s = (el.input_value() or "").strip()
        if s:
            return s
    except Exception:
        pass
    try:
        return (el.evaluate("e => (e && e.value != null) ? String(e.value).trim() : ''") or "").strip()
    except Exception:
        return ""


def _proposal_expected_matches_readback(expected: str, readback: str) -> bool:
    e = normalize_for_fuzzy_match(expected)
    r = normalize_for_fuzzy_match(readback)
    if not e or not r:
        return False
    if e == r:
        return True
    if e in r or r in e:
        return True
    pick = fuzzy_best_option_label(
        expected, [readback], min_score=KYC_INSURER_FUZZY_MIN_SCORE
    )
    return bool(pick)


def _proposal_first_label_control_locator(page, label_pattern: str):
    """First visible control matching ``get_by_label`` across proposal roots (page + nav iframe + frames)."""
    rx = re.compile(label_pattern, re.I)
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = root.get_by_label(rx)
            if loc.count() == 0:
                continue
            first = loc.first
            try:
                if first.is_visible(timeout=1_800):
                    return first
            except Exception:
                return first
        except Exception:
            continue
    return None


def _proposal_cph1_locator(root, id_suffix: str):
    """Stable ``#ctl00_ContentPlaceHolder1_<id_suffix>`` or ``[id$=_<suffix>]`` fallback."""
    fid = f"{HERO_MISP_CPH1}_{id_suffix}"
    loc = root.locator(f"#{fid}")
    if loc.count() > 0:
        return loc
    return root.locator(f"[id$='_{id_suffix}']")


def _proposal_scroll_visible(el, *, timeout_ms: int) -> None:
    try:
        el.scroll_into_view_if_needed(timeout=min(3_000, timeout_ms))
    except Exception:
        pass


def _proposal_checkbox_context_text(cb) -> str:
    """
    Walk up from the checkbox through parents so **RTI Cover** / **NIC** match even when grid row index
    (``ctl02`` vs ``ctl03``) or insurer changes layout.
    """
    try:
        return (
            cb.evaluate(
                """e => {
                  const parts = [];
                  let n = e;
                  for (let i = 0; i < 14 && n; i++) {
                    const t = (n.innerText || '').trim();
                    if (t) parts.push(t.slice(0, 500));
                    n = n.parentElement;
                  }
                  return parts.join('\\n');
                }"""
            )
            or ""
        )[:3000]
    except Exception:
        return ""


def _proposal_dob_readback_matches_expected(want_norm: str, got: str) -> bool:
    """``txtDOB`` readback vs expected **dd/mm/yyyy** (same spirit as ``_proposal_step_fill_dob``)."""
    if not want_norm or not got:
        return False
    if normalize_for_fuzzy_match(got) == normalize_for_fuzzy_match(want_norm):
        return True
    if want_norm in got or got in want_norm:
        return True
    g = got.replace("-", "/")
    return any(x in g for x in (want_norm, want_norm.replace("/", "-")))


def _proposal_read_dob_txt(page) -> str | None:
    """Best-effort current value of ``txtDOB`` across proposal roots."""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, "txtDOB")
            if loc.count() == 0:
                continue
            el = loc.first
            if not el.is_visible(timeout=400):
                continue
            got = (el.input_value() or "").strip()
            if got:
                return got
        except Exception:
            continue
    return None


def _proposal_step_fill_dob(
    page,
    dob_raw: str,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """Proposer **Date of Birth** — ``txtDOB`` (dd/mm/yyyy)."""
    v = normalize_dob_for_misp(dob_raw)
    if not v:
        return None
    last = "dob not filled"
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, "txtDOB")
            if loc.count() == 0:
                continue
            el = loc.first
            if not el.is_visible(timeout=800):
                _proposal_scroll_visible(el, timeout_ms=timeout_ms)
            if not el.is_visible(timeout=1_800):
                continue
            el.fill("", timeout=timeout_ms)
            el.fill(v, timeout=timeout_ms)
            got = (el.input_value() or "").strip()
            if not got:
                try:
                    el.evaluate(
                        """(node, val) => {
                          node.value = val;
                          ['input','change','blur'].forEach(k =>
                            node.dispatchEvent(new Event(k, { bubbles: true })));
                        }""",
                        v,
                    )
                    got = (el.input_value() or "").strip()
                except Exception:
                    got = ""
            if not got:
                return f"{step_id}: DOB readback empty after fill"
            if normalize_for_fuzzy_match(got) != normalize_for_fuzzy_match(v) and v not in got and got not in v:
                if not any(
                    x in got.replace("-", "/") for x in (v, v.replace("/", "-"))
                ):
                    return f"{step_id}: DOB readback mismatch want={v!r} got={got!r}"
            # Flatpickr / WebForms: commit value (avoid revert when focus moves to Marital/RTO/etc.).
            try:
                el.press("Tab")
            except Exception:
                try:
                    page.keyboard.press("Tab")
                except Exception:
                    pass
            _t(page, 180)
            got2 = (el.input_value() or "").strip()
            if not got2 or (
                normalize_for_fuzzy_match(got2) != normalize_for_fuzzy_match(v)
                and v not in got2
                and got2 not in v
                and not any(x in got2.replace("-", "/") for x in (v, v.replace("/", "-")))
            ):
                try:
                    el.click(timeout=timeout_ms)
                    el.fill("", timeout=timeout_ms)
                    el.fill(v, timeout=timeout_ms)
                    el.press("Tab")
                except Exception:
                    pass
                _t(page, 150)
            _proposal_log(
                ocr_output_dir,
                subfolder,
                step_id,
                f"fill ok id_suffix=txtDOB readback={(el.input_value() or got)!r}",
            )
            return None
        except Exception as exc:
            last = str(exc)
            continue
    err = _proposal_step_fill_input(
        page,
        (r"Date\s*of\s*Birth", r"DOB", r"Birth\s*Date"),
        v,
        step_id,
        ocr_output_dir,
        subfolder,
        timeout_ms=timeout_ms,
    )
    if err:
        return f"{step_id}: {err} ({last})"
    return None


def _proposal_hdfc_radio_any_checked(page) -> bool:
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, "rdoHdfcCCType")
            n = loc.count()
            for j in range(min(n, 8)):
                try:
                    r = loc.nth(j)
                    if r.is_checked():
                        return True
                    try:
                        if r.evaluate("e => !!(e && e.checked)"):
                            return True
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass
        try:
            hdfc = root.get_by_role("radio", name=re.compile(r"HDFC", re.I))
            for i in range(min(hdfc.count(), 12)):
                try:
                    if hdfc.nth(i).is_checked():
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _proposal_step_select_fuzzy(
    page,
    label_patterns: tuple[str, ...],
    query: str,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
    cph1_id_suffix: str | None = None,
) -> str | None:
    """Set ``<select>`` by optional CPH1 id, then label + fuzzy option; read back selected text."""
    q = (query or "").strip()
    if not q:
        return None
    last = "no select control matched labels"
    if cph1_id_suffix:
        _suffixes: tuple[str, ...] = (cph1_id_suffix,)
        if cph1_id_suffix == "ddlNomineeRelation":
            _suffixes = HERO_MISP_NOMINEE_RELATION_CPH1_SUFFIXES
        elif cph1_id_suffix == "ddlAgreementTypeWithFinancer":
            _suffixes = HERO_MISP_AGREEMENT_TYPE_FINANCER_CPH1_SUFFIXES
        for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
            for _suf in _suffixes:
                try:
                    loc = _proposal_cph1_locator(root, _suf)
                    if loc.count() == 0:
                        continue
                    el = loc.first
                    if not el.is_visible(timeout=800):
                        _proposal_scroll_visible(el, timeout_ms=timeout_ms)
                    if not el.is_visible(timeout=1_500):
                        continue
                    tag = (el.evaluate("e => e && e.tagName ? e.tagName.toUpperCase() : ''") or "").upper()
                    if tag != "SELECT":
                        last = f"id {_suf!r} is {tag}, not SELECT"
                        continue
                    # MISP **Marital Status** — try canonical labels; portal may spell **Single** as **SIngle**.
                    if cph1_id_suffix == "ddlMaritalStatus" and q in (
                        "Married",
                        "Single",
                        "Divorced",
                        "Widow",
                    ):
                        marital_labels = {
                            "Married": ("Married",),
                            "Single": ("Single", "SIngle"),
                            "Divorced": ("Divorced",),
                            "Widow": ("Widow",),
                        }[q]
                        for lbl in marital_labels:
                            try:
                                loc.select_option(label=lbl, timeout=timeout_ms, force=True)
                                snap = _read_locator_value_snapshot(loc)
                                st = (snap.get("selected_text") or "").strip()
                                if _proposal_expected_matches_readback(q, st):
                                    _proposal_log(
                                        ocr_output_dir,
                                        subfolder,
                                        step_id,
                                        f"select ok id_suffix=ddlMaritalStatus label={lbl!r} readback={st!r}",
                                    )
                                    return None
                            except Exception:
                                pass
                    # **Agreement Type with Financer** — product choice **HPA** (short option text; try exact labels).
                    if cph1_id_suffix == "ddlAgreementTypeWithFinancer" and q:
                        qn_at = normalize_for_fuzzy_match(q)
                        if qn_at == "hpa":
                            for lbl in ("HPA", "Hpa"):
                                try:
                                    loc.select_option(label=lbl, timeout=timeout_ms, force=True)
                                    snap = _read_locator_value_snapshot(loc)
                                    st = (snap.get("selected_text") or "").strip()
                                    if _proposal_expected_matches_readback(q, st):
                                        _proposal_log(
                                            ocr_output_dir,
                                            subfolder,
                                            step_id,
                                            f"select ok id_suffix={_suf!r} agreement_type label={lbl!r} readback={st!r}",
                                        )
                                        return None
                                except Exception:
                                    pass
                            try:
                                loc.select_option(
                                    label=re.compile(r"^\s*HPA\s*$", re.I),
                                    timeout=timeout_ms,
                                    force=True,
                                )
                                snap = _read_locator_value_snapshot(loc)
                                st = (snap.get("selected_text") or "").strip()
                                if _proposal_expected_matches_readback(q, st):
                                    _proposal_log(
                                        ocr_output_dir,
                                        subfolder,
                                        step_id,
                                        f"select ok id_suffix={_suf!r} agreement_type label=regex_HPA readback={st!r}",
                                    )
                                    return None
                            except Exception:
                                pass
                            try:
                                loc.select_option(value="1", timeout=timeout_ms, force=True)
                                snap = _read_locator_value_snapshot(loc)
                                st = (snap.get("selected_text") or "").strip()
                                if _proposal_expected_matches_readback(q, st):
                                    _proposal_log(
                                        ocr_output_dir,
                                        subfolder,
                                        step_id,
                                        f"select ok id_suffix={_suf!r} agreement_type value=1 readback={st!r}",
                                    )
                                    return None
                            except Exception:
                                pass
                    # Nominee **Relation** — staging often sends **Mother** / **Father**; MISP option text may differ
                    # in case or wording; try explicit labels before generic fuzzy (same idea as marital).
                    if cph1_id_suffix == "ddlNomineeRelation" and q:
                        qn = normalize_for_fuzzy_match(q)
                        rel_try: tuple[tuple[str, tuple[str, ...]], ...] = (
                            ("mother", ("Mother", "MOTHER")),
                            ("father", ("Father", "FATHER")),
                            ("wife", ("Wife", "WIFE")),
                            ("husband", ("Husband", "HUSBAND")),
                            ("spouse", ("Spouse", "SPOUSE")),
                            ("son", ("Son", "SON")),
                            ("daughter", ("Daughter", "DAUGHTER")),
                            ("brother", ("Brother", "BROTHER")),
                            ("sister", ("Sister", "SISTER")),
                            ("other", ("Other", "OTHER")),
                        )
                        for key, labels in rel_try:
                            if qn != key and key not in qn and qn not in key:
                                continue
                            for lbl in labels:
                                try:
                                    loc.select_option(label=lbl, timeout=timeout_ms, force=True)
                                    snap = _read_locator_value_snapshot(loc)
                                    st = (snap.get("selected_text") or "").strip()
                                    if _proposal_expected_matches_readback(q, st):
                                        _proposal_log(
                                            ocr_output_dir,
                                            subfolder,
                                            step_id,
                                            f"select ok id_suffix={_suf!r} label={lbl!r} readback={st!r}",
                                        )
                                        return None
                                except Exception:
                                    pass
                            if key == "mother":
                                try:
                                    loc.select_option(
                                        label=re.compile(r"^\s*mother\s*$", re.I),
                                        timeout=timeout_ms,
                                        force=True,
                                    )
                                    snap = _read_locator_value_snapshot(loc)
                                    st = (snap.get("selected_text") or "").strip()
                                    if _proposal_expected_matches_readback(q, st):
                                        _proposal_log(
                                            ocr_output_dir,
                                            subfolder,
                                            step_id,
                                            f"select ok id_suffix={_suf!r} label=regex_mother readback={st!r}",
                                        )
                                        return None
                                except Exception:
                                    pass
                            break
                    if not _select_option_fuzzy_in_select(
                        page,
                        loc,
                        q,
                        timeout_ms=timeout_ms,
                        fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
                    ):
                        last = f"fuzzy select failed for id suffix {_suf!r}"
                        continue
                    snap = _read_locator_value_snapshot(loc)
                    st = (snap.get("selected_text") or "").strip()
                    if not _proposal_expected_matches_readback(q, st):
                        return (
                            f"{step_id}: readback mismatch expected={q!r} selected_text={st!r} "
                            f"(id_suffix={_suf!r})"
                        )
                    _proposal_log(
                        ocr_output_dir,
                        subfolder,
                        step_id,
                        f"select ok id_suffix={_suf!r} readback={st!r}",
                    )
                    return None
                except Exception as exc:
                    last = str(exc)
                    continue
    for lp in label_patterns:
        el = _proposal_first_label_control_locator(page, lp)
        if el is None:
            continue
        try:
            tag = (el.evaluate("e => e && e.tagName ? e.tagName.toUpperCase() : ''") or "").upper()
            if tag != "SELECT":
                last = f"label {lp!r} resolved to {tag}, not SELECT"
                continue
        except Exception:
            continue
        loc = el
        if not _select_option_fuzzy_in_select(
            page,
            loc,
            q,
            timeout_ms=timeout_ms,
            fuzzy_min_score=KYC_INSURER_FUZZY_MIN_SCORE,
        ):
            last = f"fuzzy select failed for label pattern {lp!r}"
            continue
        snap = _read_locator_value_snapshot(loc)
        st = (snap.get("selected_text") or "").strip()
        if not _proposal_expected_matches_readback(q, st):
            return (
                f"{step_id}: readback mismatch expected={q!r} selected_text={st!r} "
                f"(after label pattern {lp!r})"
            )
        _proposal_log(
            ocr_output_dir,
            subfolder,
            step_id,
            f"select ok label_pattern={lp[:48]!r} readback={st!r}",
        )
        return None
    return f"{step_id}: {last}"


_NOMINEE_DOM_DISPATCH_JS = """(node, val) => {
  if (!node) return;
  if (!(node.value || '').trim()) {
    node.value = val;
  }
  ['input','change','blur'].forEach(k =>
    node.dispatchEvent(new Event(k, { bubbles: true })));
}"""


def _proposal_fill_nominee_field(page, el, v: str, *, timeout_ms: int, key_delay: int = 40) -> None:
    """Click → clear → press_sequentially (fallback fill) → DOM dispatch for nominee Name/Age inputs."""
    try:
        el.click(timeout=timeout_ms, force=True)
    except Exception:
        pass
    el.fill("", timeout=timeout_ms, force=True)
    try:
        el.press_sequentially(v, delay=key_delay, timeout=timeout_ms)
    except Exception:
        el.fill(v, timeout=timeout_ms, force=True)
    _t(page, 200)
    try:
        el.evaluate(_NOMINEE_DOM_DISPATCH_JS, v)
    except Exception:
        pass
    _t(page, 200)


def _proposal_step_fill_input(
    page,
    label_patterns: tuple[str, ...],
    value: str,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
    cph1_id_suffix: str | None = None,
) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    last = "no input matched labels"
    if cph1_id_suffix:
        _nominee = cph1_id_suffix in ("txtNomineeName", "txtNomineeAge")
        _vis_a = 1_200 if _nominee else 800
        _vis_b = 3_000 if _nominee else 1_500
        for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
            try:
                loc = _proposal_cph1_locator(root, cph1_id_suffix)
                if loc.count() == 0:
                    continue
                el = loc.first
                if not el.is_visible(timeout=_vis_a):
                    _proposal_scroll_visible(el, timeout_ms=timeout_ms)
                if not el.is_visible(timeout=_vis_b):
                    continue
                tag = (el.evaluate("e => e && e.tagName ? e.tagName.toUpperCase() : ''") or "").upper()
                if tag == "SELECT":
                    last = f"id {cph1_id_suffix!r} is SELECT; use proposal_step_select"
                    continue
                if cph1_id_suffix == "txtNomineeAge":
                    _proposal_fill_nominee_field(page, el, v, timeout_ms=timeout_ms, key_delay=40)
                elif cph1_id_suffix == "txtNomineeName":
                    _proposal_fill_nominee_field(page, el, v, timeout_ms=timeout_ms, key_delay=30)
                elif _nominee:
                    el.fill("", timeout=timeout_ms, force=True)
                    el.fill(v, timeout=timeout_ms, force=True)
                else:
                    el.fill("", timeout=timeout_ms)
                    el.fill(v, timeout=timeout_ms)
                snap = _read_locator_value_snapshot(el)
                got = (snap.get("value") or "").strip()
                if not got:
                    got = _proposal_read_input_value_best_effort(el)
                if (
                    not got
                    or (
                        got != v
                        and normalize_for_fuzzy_match(got) != normalize_for_fuzzy_match(v)
                        and not _proposal_expected_matches_readback(v, got)
                    )
                ):
                    try:
                        el.evaluate(
                            """(node, val) => {
                              if (!node) return;
                              node.value = val;
                              ['input','change','blur'].forEach(k =>
                                node.dispatchEvent(new Event(k, { bubbles: true })));
                            }""",
                            v,
                        )
                    except Exception:
                        pass
                    _t(page, 200)
                    got = _proposal_read_input_value_best_effort(el)
                    if not got:
                        got = (_read_locator_value_snapshot(el).get("value") or "").strip()
                if got != v and normalize_for_fuzzy_match(got) != normalize_for_fuzzy_match(v):
                    if not _proposal_expected_matches_readback(v, got):
                        return f"{step_id}: readback mismatch expected={v!r} got={got!r}"
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    f"fill ok id_suffix={cph1_id_suffix!r} readback={got!r}",
                )
                return None
            except Exception as exc:
                last = str(exc)
                continue
    for lp in label_patterns:
        el = _proposal_first_label_control_locator(page, lp)
        if el is None:
            continue
        try:
            tag = (el.evaluate("e => e && e.tagName ? e.tagName.toUpperCase() : ''") or "").upper()
            if tag == "SELECT":
                last = f"label {lp!r} is SELECT; use proposal_step_select"
                continue
        except Exception:
            continue
        try:
            el.fill("", timeout=timeout_ms)
            el.fill(v, timeout=timeout_ms)
        except Exception as exc:
            last = f"fill failed {lp!r}: {exc!s}"
            continue
        snap = _read_locator_value_snapshot(el)
        got = (snap.get("value") or "").strip()
        if got != v and normalize_for_fuzzy_match(got) != normalize_for_fuzzy_match(v):
            if not _proposal_expected_matches_readback(v, got):
                return f"{step_id}: readback mismatch expected={v!r} got={got!r}"
        _proposal_log(
            ocr_output_dir,
            subfolder,
            step_id,
            f"fill ok label_pattern={lp[:48]!r} readback={got!r}",
        )
        return None
    return f"{step_id}: {last}"


def _proposal_cph1_checkbox_readback_pair(cb) -> tuple[bool | None, bool | None]:
    """DOM ``.checked`` and Playwright ``is_checked()`` (None if a read fails)."""
    dom_v: bool | None = None
    try:
        dom_v = bool(cb.evaluate("e => !!(e && e.checked)"))
    except Exception:
        pass
    pw_v: bool | None = None
    try:
        pw_v = bool(cb.is_checked())
    except Exception:
        pass
    return dom_v, pw_v


def _proposal_wait_cph1_checkbox_stable(
    page,
    cb,
    want_checked: bool,
    *,
    max_rounds: int = 8,
) -> tuple[bool, bool | None, bool | None]:
    """
    After toggling, ASP.NET can revert on the next tick — require **two consecutive** agreeing
    readbacks (DOM + PW when both available) with short settles between polls.
    """
    consecutive = 0
    last_dom: bool | None = None
    last_pw: bool | None = None
    for _ in range(max(3, max_rounds)):
        _t(page, _MISP_UI_SETTLE_CAP_MS)
        last_dom, last_pw = _proposal_cph1_checkbox_readback_pair(cb)
        ok = False
        if last_dom is not None and last_pw is not None:
            ok = last_dom == want_checked and last_pw == want_checked
        elif last_pw is not None:
            ok = last_pw == want_checked
        elif last_dom is not None:
            ok = last_dom == want_checked
        if ok:
            consecutive += 1
            if consecutive >= 2:
                return True, last_dom, last_pw
        else:
            consecutive = 0
    return False, last_dom, last_pw


def _proposal_first_visible_locator_nth(loc, *, max_n: int = 6, vis_timeout_ms: int = 600):
    """Prefer a **visible** match when the same id appears in multiple roots / duplicate nodes."""
    try:
        n = loc.count()
    except Exception:
        return None
    for i in range(min(n, max_n)):
        el = loc.nth(i)
        try:
            if el.is_visible(timeout=vis_timeout_ms):
                return el
        except Exception:
            continue
    try:
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    return None


def _proposal_step_checkbox(
    page,
    text_pattern: str,
    want_checked: bool,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """Find checkbox by label/row text, set checked state, verify. Fails if control not found."""
    rx = re.compile(text_pattern, re.I | re.M)
    last_exc = ""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            cbs = root.locator('input[type="checkbox"]')
            n = cbs.count()
        except Exception:
            continue
        for i in range(min(n, 160)):
            cb = cbs.nth(i)
            try:
                if not cb.is_visible(timeout=400):
                    continue
                t = _proposal_checkbox_context_text(cb)
                if not t.strip():
                    cid = cb.get_attribute("id") or ""
                    if cid:
                        try:
                            lab = root.locator(f'label[for="{cid}"]')
                            if lab.count() > 0:
                                t = (lab.first.inner_text() or "")[:400]
                        except Exception:
                            pass
                if not t.strip():
                    t = (
                        cb.evaluate(
                            "e => (e.closest('label, tr, div') && e.closest('label, tr, div').innerText) || ''"
                        )
                        or ""
                    )[:500]
                if not rx.search(t):
                    continue
                if want_checked and not cb.is_checked():
                    try:
                        cb.check(timeout=timeout_ms, force=True)
                    except Exception:
                        cb.check(timeout=timeout_ms)
                elif not want_checked and cb.is_checked():
                    try:
                        cb.uncheck(timeout=timeout_ms, force=True)
                    except Exception:
                        cb.uncheck(timeout=timeout_ms)
                if cb.is_checked() != want_checked:
                    return (
                        f"{step_id}: checkbox readback want_checked={want_checked} "
                        f"got={cb.is_checked()} pattern={text_pattern!r}"
                    )
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    f"checkbox {'checked' if want_checked else 'unchecked'} ok pattern={text_pattern[:56]!r}",
                )
                return None
            except Exception as exc:
                last_exc = str(exc)
                continue
    suf = f" ({last_exc})" if last_exc else ""
    return f"{step_id}: checkbox not found for pattern {text_pattern!r}{suf}"


def _proposal_step_checkbox_uncheck_if_present(
    page,
    text_pattern: str,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """
    If a visible checkbox matches ``text_pattern``, ensure it is **unchecked** and verify.
    If **no** such checkbox exists, log skip — **not** an error (portal builds vary).
    """
    if not (text_pattern or "").strip():
        return None
    rx = re.compile(text_pattern, re.I | re.M)
    last_exc = ""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            cbs = root.locator('input[type="checkbox"]')
            n = cbs.count()
        except Exception:
            continue
        for i in range(min(n, 160)):
            cb = cbs.nth(i)
            try:
                if not cb.is_visible(timeout=400):
                    continue
                t = _proposal_checkbox_context_text(cb)
                if not t.strip():
                    cid = cb.get_attribute("id") or ""
                    if cid:
                        try:
                            lab = root.locator(f'label[for="{cid}"]')
                            if lab.count() > 0:
                                t = (lab.first.inner_text() or "")[:400]
                        except Exception:
                            pass
                if not t.strip():
                    t = (
                        cb.evaluate(
                            "e => (e.closest('label, tr, div') && e.closest('label, tr, div').innerText) || ''"
                        )
                        or ""
                    )[:500]
                if not rx.search(t):
                    continue
                if cb.is_checked():
                    try:
                        cb.uncheck(timeout=timeout_ms, force=True)
                    except Exception:
                        cb.uncheck(timeout=timeout_ms)
                if cb.is_checked():
                    return (
                        f"{step_id}: optional checkbox could not be left unchecked "
                        f"pattern={text_pattern!r}"
                    )
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    f"checkbox optional uncheck ok pattern={text_pattern[:56]!r} readback=unchecked",
                )
                return None
            except Exception as exc:
                last_exc = str(exc)
                continue
    _proposal_log(
        ocr_output_dir,
        subfolder,
        step_id,
        f"skip optional_uncheck (no checkbox matched pattern={text_pattern[:56]!r}){(' ' + last_exc) if last_exc else ''}",
    )
    return None


def _proposal_step_checkbox_by_cph1_id(
    page,
    id_suffix: str,
    want_checked: bool,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """
    Set ``#ctl00_ContentPlaceHolder1_<id_suffix>`` checkbox when present (see
    proposal markup — e.g. ``chkroicover``, ``chkRSA``, ``chkEME``,
    ``chkNilDepreciation``, ``gridGMc_ctl02_chlGMC``).

    Returns ``None`` on success, ``PROPOSAL_CHECKBOX_ID_NOT_FOUND`` if no control matched in any proposal
    root (caller may fall back to label/regex), else an error string.
    """
    last_err = ""
    seen = False
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, id_suffix)
            if loc.count() == 0:
                continue
            seen = True
            cb = _proposal_first_visible_locator_nth(loc, max_n=6, vis_timeout_ms=600)
            if cb is None:
                last_err = f"{id_suffix} no visible locator"
                continue
            typ = (cb.get_attribute("type") or "").lower()
            if typ != "checkbox":
                return f"{step_id}: {id_suffix!r} is not type=checkbox"
            if not cb.is_visible(timeout=500):
                _proposal_scroll_visible(cb, timeout_ms=timeout_ms)
            if not cb.is_visible(timeout=min(4_000, timeout_ms)):
                last_err = f"{id_suffix} not visible"
                continue
            if want_checked and not cb.is_checked():
                try:
                    cb.check(timeout=timeout_ms, force=True)
                except Exception:
                    cb.check(timeout=timeout_ms)
            elif not want_checked and cb.is_checked():
                try:
                    cb.uncheck(timeout=timeout_ms, force=True)
                except Exception:
                    cb.uncheck(timeout=timeout_ms)
            if cb.is_checked() != want_checked:
                try:
                    cb.evaluate(
                        """(e, want) => {
                          e.checked = want;
                          e.dispatchEvent(new Event('change', { bubbles: true }));
                          e.dispatchEvent(new Event('click', { bubbles: true }));
                        }""",
                        want_checked,
                    )
                except Exception:
                    pass
            ok, d_r, p_r = _proposal_wait_cph1_checkbox_stable(
                page,
                cb,
                want_checked,
                max_rounds=min(12, max(8, timeout_ms // 2_000)),
            )
            if not ok:
                return (
                    f"{step_id}: checkbox id={id_suffix!r} want_checked={want_checked} "
                    f"unstable after settle readback_dom={d_r!r} readback_pw={p_r!r}"
                )
            _proposal_log(
                ocr_output_dir,
                subfolder,
                step_id,
                f"checkbox id_suffix={id_suffix[:56]!r} {'checked' if want_checked else 'unchecked'} ok "
                f"readback_dom={d_r!r} readback_pw={p_r!r} stable=2",
            )
            return None
        except Exception as exc:
            last_err = str(exc)
            continue
    if not seen:
        return PROPOSAL_CHECKBOX_ID_NOT_FOUND
    return f"{step_id}: checkbox id={id_suffix!r} failed ({last_err or 'not visible'})"


def _proposal_addon_checkbox_id_or_label(
    page,
    id_suffix: str,
    want_checked: bool,
    step_id: str,
    label_pattern: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """Prefer stable CPH1 id from MispPolicy scrape; fall back to row/label regex."""
    r = _proposal_step_checkbox_by_cph1_id(
        page,
        id_suffix,
        want_checked,
        step_id,
        ocr_output_dir,
        subfolder,
        timeout_ms=timeout_ms,
    )
    if r is None:
        return None
    if r == PROPOSAL_CHECKBOX_ID_NOT_FOUND:
        return _proposal_step_checkbox(
            page,
            label_pattern,
            want_checked,
            step_id,
            ocr_output_dir,
            subfolder,
            timeout_ms=timeout_ms,
        )
    return r


def _proposal_step_nominee_gender_radio(
    page,
    gender_raw: str,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """Nominee **Gender** is radio pair ``rdbtnMale`` / ``rdbtnFemale`` (not a ``<select>``)."""
    g = normalize_for_fuzzy_match((gender_raw or "").strip())
    if not g:
        return None
    want_male = (
        "male" in g
        or g in ("m", "mr", "mister")
        or g.startswith("m ")
        or g == "man"
    ) and "female" not in g
    want_female = "female" in g or g in ("f", "woman", "lady") or g.startswith("f ")
    if not want_male and not want_female:
        return f"{step_id}: could not interpret nominee gender from {gender_raw!r}"
    id_suffix = "rdbtnMale" if want_male else "rdbtnFemale"
    last_err = ""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, id_suffix)
            if loc.count() == 0:
                continue
            el = loc.first
            if not el.is_visible(timeout=800):
                _proposal_scroll_visible(el, timeout_ms=timeout_ms)
            if not el.is_visible(timeout=1_800):
                continue
            try:
                el.check(timeout=timeout_ms, force=True)
            except Exception:
                el.check(timeout=timeout_ms)
            if not el.is_checked():
                return f"{step_id}: nominee gender radio still not checked after check() ({id_suffix})"
            _proposal_log(
                ocr_output_dir,
                subfolder,
                step_id,
                f"radio {id_suffix} checked ok readback=checked",
            )
            return None
        except Exception as exc:
            last_err = str(exc)
            continue
    return f"{step_id}: nominee gender radio not found ({last_err or 'no candidate'})"


def _proposal_step_usgi_uncheck(
    page,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """CPA **USGI** checkbox (grid ``chlGMC`` ``ctl02``); force **unchecked** per SOP."""
    rid = _proposal_step_checkbox_by_cph1_id(
        page,
        "gridGMc_ctl02_chlGMC",
        False,
        step_id,
        ocr_output_dir,
        subfolder,
        timeout_ms=timeout_ms,
    )
    if rid is None:
        return None
    if rid != PROPOSAL_CHECKBOX_ID_NOT_FOUND:
        return rid
    last_err = ""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            locs = root.locator("[id*='chlGMC']")
            n = locs.count()
        except Exception:
            continue
        for i in range(min(n, 48)):
            try:
                cb = locs.nth(i)
                typ = (cb.get_attribute("type") or "").lower()
                if typ != "checkbox":
                    continue
                ctx = _proposal_checkbox_context_text(cb)
                if not re.search(r"\bUSGI\b", ctx, re.I):
                    continue
                if not cb.is_visible(timeout=600):
                    _proposal_scroll_visible(cb, timeout_ms=timeout_ms)
                if not cb.is_visible(timeout=2_000):
                    continue
                if cb.is_checked():
                    cb.uncheck(timeout=timeout_ms, force=True)
                if cb.is_checked():
                    try:
                        cb.evaluate(
                            "e => { e.checked = false; e.dispatchEvent(new Event('change', { bubbles: true })); }"
                        )
                    except Exception:
                        pass
                if cb.is_checked():
                    return f"{step_id}: USGI checkbox still checked after uncheck"
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    "USGI checkbox unchecked ok readback=unchecked",
                )
                return None
            except Exception as exc:
                last_err = str(exc)
                continue
    err = _proposal_step_checkbox(page, r"^USGI$|USGI", False, step_id, ocr_output_dir, subfolder, timeout_ms=timeout_ms)
    if err is None:
        return None
    return f"{step_id}: USGI checkbox not found ({last_err or err})"


# CPA bottom add-on: portal label varies (NIC / CPI / Hero CPI / …); match by row text; state from ``form_insurance_view.hero_cpi``.
# Inline ``(?i)``/``(?m)`` mid-pattern breaks ``re.compile(..., re.I)`` — use flags on ``compile`` only (**LLD** **6.212**).
HERO_MISP_HERO_CPI_ADDON_CHECKBOX_PATTERN = (
    r"^\s*(NIC|CPI)\s*$|\b(NIC|CPI)\b|NIC\s*/\s*CPI|CPI\s*/\s*NIC|"
    r"Hero\s*CPI|Consumer\s*Protection|Protection\s*Insurance|NIC\s*Cover|CPI\s*Cover"
)


def _proposal_step_hero_cpi_addon_by_dealer_flag(
    page,
    values: dict[str, Any],
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """``hero_cpi`` **Y** = check matching add-on row; **N** = uncheck if present."""
    flag = normalize_hero_cpi_flag(values.get("hero_cpi"))
    _proposal_log(
        ocr_output_dir,
        subfolder,
        step_id,
        f"dealer hero_cpi={flag!r} (Y=check NIC/CPI row, N=uncheck)",
    )
    pat = HERO_MISP_HERO_CPI_ADDON_CHECKBOX_PATTERN
    if flag == "Y":
        return _proposal_step_checkbox(
            page, pat, True, step_id, ocr_output_dir, subfolder, timeout_ms=timeout_ms
        )
    return _proposal_step_checkbox_uncheck_if_present(
        page, pat, step_id, ocr_output_dir, subfolder, timeout_ms=timeout_ms
    )


def _proposal_step_email_hardcoded(
    page,
    email_fixed: str,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    """Hero MISP uses ``type=text`` for **Email ID**; prefer CPH1 ``txtEmail`` then **Email ID** label."""
    last_err = ""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        for factory in (
            lambda r: _proposal_cph1_locator(r, "txtEmail"),
            lambda r: r.get_by_label(re.compile(r"Email\s*ID", re.I)),
            lambda r: r.get_by_label(re.compile(r"E-?mail", re.I)),
            lambda r: r.locator('input[name*="txtEmail" i]'),
            lambda r: r.locator('input[id$="txtEmail"]'),
            lambda r: r.locator('input[type="email"]'),
        ):
            try:
                loc = factory(root)
                if loc.count() == 0:
                    continue
                el = loc.first
                if not el.is_visible(timeout=800):
                    _proposal_scroll_visible(el, timeout_ms=timeout_ms)
                if not el.is_visible(timeout=1_800):
                    continue
                el.fill("", timeout=timeout_ms)
                el.fill(email_fixed, timeout=timeout_ms)
                got = (el.input_value() or "").strip()
                if got != email_fixed and normalize_for_fuzzy_match(got) != normalize_for_fuzzy_match(
                    email_fixed
                ):
                    return f"{step_id}: email readback mismatch want={email_fixed!r} got={got!r}"
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    f"fill ok readback={got!r}",
                )
                return None
            except Exception as exc:
                last_err = str(exc)
                continue
    return f"{step_id}: email input not found or not fillable ({last_err or 'no candidate'})"


def _proposal_step_date_of_registration_today(
    page,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    today = date.today()
    iso_d = today.isoformat()
    slash_d = today.strftime("%d/%m/%Y")
    # Stable CPH1 id (flatpickr ``MispCal`` text input, not ``type=date``)
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, "txtRegistrationDate")
            if loc.count() == 0:
                continue
            el = loc.first
            if not el.is_visible(timeout=800):
                _proposal_scroll_visible(el, timeout_ms=timeout_ms)
            if not el.is_visible(timeout=1_500):
                continue
            inp_type = (el.get_attribute("type") or "").lower()
            if inp_type == "date":
                el.fill(iso_d, timeout=timeout_ms)
            else:
                el.fill(slash_d, timeout=timeout_ms)
            snap = _read_locator_value_snapshot(el)
            got = (snap.get("value") or "").strip()
            if not got:
                return f"{step_id}: date readback empty after fill (txtRegistrationDate)"
            if iso_d not in got and slash_d not in got and got.replace("-", "/") != slash_d:
                if not any(x in got for x in (iso_d, today.strftime("%d-%m-%Y"), slash_d)):
                    return f"{step_id}: date readback mismatch want ~{slash_d!r} got={got!r}"
            _proposal_log(
                ocr_output_dir,
                subfolder,
                step_id,
                f"fill ok id_suffix=txtRegistrationDate readback={got!r}",
            )
            return None
        except Exception:
            continue
    # By label
    el = _proposal_first_label_control_locator(page, r"Date\s*of\s*Regist")
    if el is not None:
        try:
            tag = (el.evaluate("e => e && e.tagName ? e.tagName.toUpperCase() : ''") or "").upper()
            if tag == "INPUT":
                inp_type = (el.get_attribute("type") or "").lower()
                if inp_type == "date":
                    el.fill(iso_d, timeout=timeout_ms)
                else:
                    el.fill(slash_d, timeout=timeout_ms)
                snap = _read_locator_value_snapshot(el)
                got = (snap.get("value") or "").strip()
                if not got:
                    return f"{step_id}: date readback empty after fill"
                if iso_d not in got and slash_d not in got and got.replace("-", "/") != slash_d:
                    if not any(
                        x in got for x in (iso_d, today.strftime("%d-%m-%Y"), slash_d)
                    ):
                        return f"{step_id}: date readback mismatch want ~{iso_d!r} got={got!r}"
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    f"fill ok readback={got!r}",
                )
                return None
        except Exception as exc:
            return f"{step_id}: date fill by label failed: {exc!s}"
    # First date input
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = root.locator('input[type="date"]')
            if loc.count() == 0 or not loc.first.is_visible(timeout=1_200):
                continue
            el = loc.first
            el.fill(iso_d, timeout=timeout_ms)
            got = (el.input_value() or "").strip()
            if got != iso_d:
                return f"{step_id}: date readback mismatch want={iso_d!r} got={got!r}"
            _proposal_log(ocr_output_dir, subfolder, step_id, f"fill ok (generic date input) readback={got!r}")
            return None
        except Exception as exc:
            continue
    return f"{step_id}: Date of registration control not found"


def _proposal_step_payment_mode_cc_if_present(
    page,
    *,
    timeout_ms: int,
) -> None:
    """``ddlPaymentMode`` must often be **CC** before **HDFC** radio is enabled (MispPolicy scrape). Best-effort."""
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, "ddlPaymentMode")
            if loc.count() == 0:
                continue
            el = loc.first
            tag = (el.evaluate("e => e && e.tagName ? e.tagName.toUpperCase() : ''") or "").upper()
            if tag != "SELECT":
                continue
            if not el.is_visible(timeout=600):
                _proposal_scroll_visible(el, timeout_ms=timeout_ms)
            if not el.is_visible(timeout=1_500):
                continue
            for pat in (r"^CC\b", r"^\s*CC\s*$", r"Credit\s*Card", r"C\.?\s*C\.?"):
                try:
                    el.select_option(label=re.compile(pat, re.I), timeout=timeout_ms)
                    return
                except Exception:
                    continue
            return
        except Exception:
            continue


def _proposal_step_hdfc_payment(
    page,
    step_id: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    timeout_ms: int,
) -> str | None:
    _proposal_step_payment_mode_cc_if_present(page, timeout_ms=timeout_ms)
    _t(page, 200)
    hdfc_rid = f"{HERO_MISP_CPH1}_rdoHdfcCCType"
    # Prefer **label[for=…]** (matches MispPolicy scrape: radio id + labelText **HDFC**); then direct radio.
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            for lab_sel in (
                f'label[for="{hdfc_rid}"]',
                'label[for*="_rdoHdfcCCType"]',
            ):
                labs = root.locator(lab_sel)
                if labs.count() == 0:
                    continue
                for j in range(min(labs.count(), 4)):
                    lab = labs.nth(j)
                    if not lab.is_visible(timeout=600):
                        _proposal_scroll_visible(lab, timeout_ms=timeout_ms)
                    if not lab.is_visible(timeout=2_000):
                        continue
                    try:
                        lab.click(timeout=timeout_ms, force=True)
                    except Exception:
                        pass
                    _t(page, 200)
                    if _proposal_hdfc_radio_any_checked(page):
                        _proposal_log(
                            ocr_output_dir,
                            subfolder,
                            step_id,
                            "HDFC label click ok (rdoHdfcCCType)",
                        )
                        return None
        except Exception:
            continue
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            loc = _proposal_cph1_locator(root, "rdoHdfcCCType")
            if loc.count() > 0:
                for j in range(min(loc.count(), 6)):
                    r = loc.nth(j)
                    if not r.is_visible(timeout=800):
                        _proposal_scroll_visible(r, timeout_ms=timeout_ms)
                    if not r.is_visible(timeout=2_000):
                        continue
                    try:
                        r.click(timeout=timeout_ms, force=True)
                    except Exception:
                        pass
                    _t(page, 200)
                    try:
                        r.check(timeout=timeout_ms, force=True)
                    except Exception:
                        try:
                            r.click(timeout=timeout_ms, force=True)
                        except Exception:
                            pass
                    if not r.is_checked():
                        try:
                            r.evaluate(
                                "e => { e.checked = true; e.dispatchEvent(new Event('click', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); }"
                            )
                        except Exception:
                            pass
                    if r.is_checked():
                        _proposal_log(
                            ocr_output_dir,
                            subfolder,
                            step_id,
                            "radio HDFC (rdoHdfcCCType) checked ok readback=checked",
                        )
                        return None
        except Exception:
            continue
    for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            hdfc = root.get_by_role("radio", name=re.compile(r"HDFC", re.I))
            if hdfc.count() == 0:
                continue
            for i in range(min(hdfc.count(), 8)):
                r = hdfc.nth(i)
                if not r.is_visible(timeout=800):
                    _proposal_scroll_visible(r, timeout_ms=timeout_ms)
                if not r.is_visible(timeout=1_500):
                    continue
                try:
                    r.check(timeout=timeout_ms, force=True)
                except Exception:
                    try:
                        r.click(timeout=timeout_ms, force=True)
                    except Exception:
                        pass
                if not r.is_checked():
                    try:
                        r.evaluate(
                            "e => { e.checked = true; e.dispatchEvent(new Event('click', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); }"
                        )
                    except Exception:
                        pass
                if not r.is_checked():
                    return f"{step_id}: HDFC radio still not checked after check()"
                _proposal_log(
                    ocr_output_dir,
                    subfolder,
                    step_id,
                    "radio HDFC checked ok readback=checked",
                )
                return None
        except Exception:
            continue
    try:
        for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
            lab = root.locator("label, span, div").filter(has_text=re.compile(r"HDFC", re.I)).first
            if lab.count() == 0 or not lab.is_visible(timeout=1_200):
                continue
            try:
                lab.click(timeout=timeout_ms, force=True)
            except Exception:
                lab.click(timeout=timeout_ms)
            _t(page, 200)
            if not _proposal_hdfc_radio_any_checked(page):
                return (
                    f"{step_id}: HDFC label clicked but no HDFC payment radio is checked "
                    "(verify Preferred CC / payment panel)"
                )
            _proposal_log(
                ocr_output_dir,
                subfolder,
                step_id,
                "click HDFC label ok readback=checked",
            )
            return None
    except Exception as exc:
        return f"{step_id}: HDFC payment option not found: {exc!s}"
    return f"{step_id}: HDFC payment option not found"


def _hero_misp_page_and_frame_roots(page, *, purpose: str = "generic") -> list:
    """
    Locator roots for MISP UI: optional ``FrameLocator`` from module constants, then ``page`` (order depends on
    ``purpose``), then every ``Frame`` (legacy sweep). See ``INSURANCE_VIN_IFRAME_SELECTOR``,
    ``INSURANCE_KYC_IFRAME_SELECTOR``, ``INSURANCE_NAV_IFRAME_SELECTOR`` at top of this module.

    For ``purpose="vin"``, child frames are ordered so **2W / welcome / main app** URLs are tried before stale **KYC**
    frames (logs: ``txtFrameNo`` can live in the post–KYC app frame while an **ekycpage** iframe remains attached).

    For ``purpose="proposal"``, **main document first**, then the nav iframe locator, then frames — matches MispPolicy
    scrape where **CPH1** fields sit on the top document.
    """
    roots: list = []
    sel = ""
    if purpose == "vin":
        sel = INSURANCE_VIN_IFRAME_SELECTOR
    elif purpose == "kyc":
        sel = INSURANCE_KYC_IFRAME_SELECTOR
    elif purpose == "nav":
        sel = INSURANCE_NAV_IFRAME_SELECTOR
    elif purpose == "proposal":
        sel = INSURANCE_NAV_IFRAME_SELECTOR

    fl = None
    if sel:
        try:
            fl = page.frame_locator(sel)
        except Exception:
            fl = None

    # MispPolicy.aspx: ContentPlaceHolder1 controls often live on the top document; iframe-first
    # order (nav) caused stale/wrong hits and duplicate operator perception on proposal steps.
    if purpose == "proposal":
        roots.append(page)
        if fl is not None:
            roots.append(fl)
    else:
        if fl is not None:
            roots.append(fl)
        roots.append(page)
    try:
        frs = [f for f in page.frames if not f.is_detached()]
        if purpose == "vin":

            def _vin_frame_order_key(fr) -> int:
                try:
                    u = (fr.url or "").lower()
                except Exception:
                    return -999
                if not u or u in ("about:blank",):
                    return -50
                if any(
                    p in u
                    for p in (
                        "ekycpage",
                        "/apps/kyc/",
                        "/kyc/",
                        "ekyc",
                    )
                ):
                    return -30
                if any(
                    p in u
                    for p in (
                        "2w",
                        "mainindex",
                        "welcome",
                        "default.aspx",
                        "hibipl",
                        "addstate",
                        "policy",
                    )
                ):
                    return 20
                return 0

            frs.sort(key=_vin_frame_order_key, reverse=True)
        roots.extend(frs)
    except Exception:
        pass
    return roots


def _hero_misp_safe_url_for_insurance_log(url: str, *, max_len: int = 280) -> str:
    """Host + path for logs; query string omitted — only ``?[query_len=N]`` (``enckycdata`` etc. stay private)."""
    s = (url or "").strip()
    if not s:
        return ""
    try:
        p = urllib.parse.urlparse(s)
        q = p.query or ""
        q_note = f"?[query_len={len(q)}]" if q else ""
        out = f"{p.scheme}://{p.netloc}{p.path}{q_note}"
        return out[:max_len]
    except Exception:
        return s[:max_len]
def _hero_misp_classify_vin_transition_url(url: str) -> str:
    """
    Classify the **top** document during KYC→VIN polling. Paths are stable; query tokens (e.g. ``enckycdata``) are not.

    Real VIN step: ``…/2W/Policy/MispDms.aspx`` (see Hero MISP). Intermediate screens vary (welcome, loading, etc.).
    """
    u = (url or "").strip().lower()
    if not u:
        return "unknown"
    try:
        path = urllib.parse.urlparse(url).path.lower()
    except Exception:
        path = ""
    if "ekycpage" in u or "/apps/kyc/" in u or "ekycpage" in path:
        return "kyc"
    if "mispdms.aspx" in path or "mispdms.aspx" in u:
        return "mispdms_policy_vin"
    if "heroinsurance.com" in u or "misp.heroinsurance" in u:
        return "transient_intermediate"
    return "other"


def _hero_misp_kyc_please_wait_overlay_visible(page) -> bool:
    """True when MISP shows the **Please wait** / loading row on ``ekycpage`` (same URL before redirect to VIN)."""
    try:
        loc = page.get_by_text(re.compile(r"please\s*wait", re.I))
        if loc.count() == 0:
            return False
        return loc.first.is_visible(timeout=500)
    except Exception:
        return False


def _hero_misp_wait_for_mispdms_vin_url_event(
    page,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> bool:
    """
    Event-driven: ``page.wait_for_url`` until top-level URL contains **MispDms.aspx** (VIN policy step).
    Returns **True** when already on or navigated to that URL; **False** on timeout (caller may still poll ``txtFrameNo``
    if the portal uses a different path). No fixed sleep — Playwright waits on navigation / URL change.
    """
    to = min(max(3_000, int(timeout_ms)), 90_000)
    try:
        u0 = (page.url or "").lower()
        if "mispdms.aspx" in u0:
            _hero_misp_log_vin_transition_line(
                page,
                phase="already_on_mispdms_url",
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
                classification=_hero_misp_classify_vin_transition_url(page.url or ""),
            )
            return True
        page.wait_for_url(re.compile(r"mispdms\.aspx", re.I), timeout=to)
        logger.info("Hero Insurance: URL event — navigated to MispDms.aspx (VIN policy step).")
        _hero_misp_log_vin_transition_line(
            page,
            phase="navigated_to_mispdms_url",
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            classification=_hero_misp_classify_vin_transition_url(page.url or ""),
        )
        return True
    except Exception as exc:
        logger.debug("Hero Insurance: wait_for_url MispDms.aspx timed out or skipped: %s", exc)
        _hero_misp_log_vin_transition_line(
            page,
            phase="wait_mispdms_url_timeout_continue_txtframe_poll",
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            classification=_hero_misp_classify_vin_transition_url(page.url or ""),
        )
        return False


def _hero_misp_log_vin_transition_line(
    page,
    *,
    phase: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    classification: str | None = None,
) -> None:
    """
    KYC→VIN navigation breadcrumb. Full URL/frame detail goes to **debug logs only** — not
    ``Playwright_insurance.txt`` (avoid URL dumps in operator traces).
    """
    del ocr_output_dir, subfolder
    try:
        raw = page.url or ""
        cls = classification if classification is not None else _hero_misp_classify_vin_transition_url(raw)
        safe = _hero_misp_safe_url_for_insurance_log(raw)
        logger.debug(
            "Hero Insurance vin_transition phase=%s classification=%s url=%s",
            phase,
            cls,
            safe,
        )
    except Exception:
        pass


# Same ordering as ``_hero_misp_fill_vin_txt_frame_no`` — used to poll until the real VIN step is in the DOM
# (MISP may show a brief intermediate page after KYC **Proceed** before ``txtFrameNo`` appears).
_HERO_MISP_VIN_TXT_FRAME_NO_SELECTORS: tuple[str, ...] = (
    "#divtxtFrameNo input[name*='txtFrameNo' i]",
    "#divtxtFrameNo input.txtBox",
    "#divtxtFrameNo input[type='text']",
    'input[placeholder*="VIN" i]',
    'input[placeholder*="Chassis" i]',
    'input[placeholder*="Frame" i]',
    'input#ctl00_ContentPlaceHolder1_txtFrameNo',
    '#ctl00_ContentPlaceHolder1_upnlAddStateMaster input[name="ctl00$ContentPlaceHolder1$txtFrameNo"]',
    '#mainContainer input[name="ctl00$ContentPlaceHolder1$txtFrameNo"]',
    '#ctl00_ContentPlaceHolder1_upnlAddStateMaster input[name*="txtFrameNo" i]',
    '#mainContainer input[name*="txtFrameNo" i]',
    '#ctl00_ContentPlaceHolder1_upnlAddStateMaster #ContentPlaceHolder1_txtFrameNo',
    '#mainContainer #ContentPlaceHolder1_txtFrameNo',
    'input[name="ctl00$ContentPlaceHolder1$txtFrameNo"]',
    "#ContentPlaceHolder1_txtFrameNo",
    'input[id="ctl00_ContentPlaceHolder1_txtFrameNo"]',
    'input[name*="txtFrameNo" i]',
    'input[id*="txtFrameNo" i]',
)


def _hero_misp_wait_for_vin_txt_frame_no_attached(
    page,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    t0_vin: float | None = None,
) -> bool:
    """
    **URL first (event-driven):** ``wait_for_url`` **MispDms.aspx** — no fixed-duration “wait then fill”.

    **Field second:** Playwright ``locator(...).first.wait_for(state='attached')`` per selector/root — auto-waits
    on DOM updates, not a tight poll/sleep loop. MISP may show **Please wait** on **ekycpage** same-URL before
    navigation; the URL wait resolves when the browser actually navigates.
    """
    budget_ms = min(int(timeout_ms), 90_000)
    deadline = time.monotonic() + budget_ms / 1000.0
    selectors = _HERO_MISP_VIN_TXT_FRAME_NO_SELECTORS
    attach_attempts = 0
    _insurance_vin_phase_note(
        ocr_output_dir, subfolder, t0_vin, "vin_attach_poll_start", "(txtFrameNo)"
    )
    _hero_misp_log_vin_transition_line(
        page,
        phase="waiting_txtFrameNo_start",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    try:
        raw_u = page.url or ""
        logger.info(
            "Hero Insurance: VIN step — url classification=%s safe=%s",
            _hero_misp_classify_vin_transition_url(raw_u),
            _hero_misp_safe_url_for_insurance_log(raw_u)[:200],
        )
        _hero_misp_log_vin_transition_line(
            page,
            phase="waiting_txtFrameNo_nav_snapshot",
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            classification=_hero_misp_classify_vin_transition_url(raw_u),
        )
    except Exception:
        pass
    if _hero_misp_kyc_please_wait_overlay_visible(page):
        _hero_misp_log_vin_transition_line(
            page,
            phase="kyc_please_wait_overlay_visible",
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            classification="kyc",
        )

    url_remain_ms = max(0, int((deadline - time.monotonic()) * 1000))
    url_ok = _hero_misp_wait_for_mispdms_vin_url_event(
        page,
        timeout_ms=max(3_000, url_remain_ms),
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    _insurance_vin_phase_note(
        ocr_output_dir,
        subfolder,
        t0_vin,
        "wait_for_url_mispdms",
        f"ok={bool(url_ok)}",
    )

    post_cap_ms = min(
        int(INSURANCE_VIN_POST_URL_DOMCONTENTLOADED_MS),
        8_000,
        max(0, int((deadline - time.monotonic()) * 1000)),
    )
    try:
        if post_cap_ms > 0:
            _wait_load_optional(page, post_cap_ms)
    except Exception:
        pass
    _insurance_vin_phase_note(
        ocr_output_dir, subfolder, t0_vin, "post_url_domcontentloaded_done"
    )

    for sel in selectors:
        for root in _hero_misp_page_and_frame_roots(page, purpose="vin"):
            remain_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remain_ms <= 0:
                break
            attach_attempts += 1
            try:
                el = root.locator(sel).first
                # Cap each attempt so wrong selector/frame does not consume the whole budget (still event-driven).
                el.wait_for(state="attached", timeout=min(8_000, remain_ms))
                logger.info("Hero Insurance: VIN field attached (%s).", sel[:72])
                _insurance_vin_phase_note(
                    ocr_output_dir,
                    subfolder,
                    t0_vin,
                    "txtFrameNo_attached",
                    f"selector={sel[:56]!r} attach_attempts={attach_attempts}",
                )
                _hero_misp_log_vin_transition_line(
                    page,
                    phase="vin_field_attached",
                    ocr_output_dir=ocr_output_dir,
                    subfolder=subfolder,
                    classification=_hero_misp_classify_vin_transition_url(page.url or ""),
                )
                return True
            except Exception:
                continue

    logger.warning("Hero Insurance: timed out waiting for VIN/Chassis input after URL/DOM wait.")
    _insurance_vin_phase_note(
        ocr_output_dir,
        subfolder,
        t0_vin,
        "txtFrameNo_attach_timeout",
        f"attach_attempts={attach_attempts}",
    )
    _hero_misp_log_vin_transition_line(
        page,
        phase="waiting_txtFrameNo_timeout",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )
    return False


def _hero_misp_fill_vin_txt_frame_no(
    page,
    vin: str,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    t0_vin: float | None = None,
) -> bool:
    """
    Real MISP VIN step: ``ctl00$ContentPlaceHolder1$txtFrameNo`` — often under ``upnlAddStateMaster`` /
    ``mainContainer`` in a **frame**; visibility checks alone can skip inputs that need ``force`` / scroll.
    """
    v = (vin or "").strip()[:64]
    if not v:
        return False
    to = min(int(timeout_ms), 90_000)
    if not _hero_misp_wait_for_vin_txt_frame_no_attached(
        page,
        timeout_ms=to,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        t0_vin=t0_vin,
    ):
        return False

    # MISP markup: ``div#divtxtFrameNo.input-container`` + ``input#ctl00_ContentPlaceHolder1_txtFrameNo``,
    # label text **VIN Number** (``label for="txtFrameNo"`` vs full client id — use label + container).
    selectors = _HERO_MISP_VIN_TXT_FRAME_NO_SELECTORS
    for sel in selectors:
        for root in _hero_misp_page_and_frame_roots(page, purpose="vin"):
            try:
                loc = root.locator(sel)
                if loc.count() == 0:
                    continue
                el = loc.first
                el.wait_for(state="attached", timeout=min(12_000, to))
                try:
                    el.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                el.fill("", timeout=to)
                el.fill(v, timeout=to, force=True)
                logger.info("Hero Insurance: filled VIN/Chassis (%s).", sel[:72])
                _insurance_vin_phase_note(
                    ocr_output_dir,
                    subfolder,
                    t0_vin,
                    "vin_chassis_filled",
                    f"selector={sel[:56]!r}",
                )
                return True
            except Exception:
                continue
    return False


def _hero_misp_fill_vin_fallback_all_frames(page, vin: str, *, timeout_ms: int) -> bool:
    """Label / fuzzy locators scoped to main page and every frame (not only top document)."""
    v = (vin or "").strip()[:64]
    if not v:
        return False
    to = min(int(timeout_ms), 60_000)
    factories = (
        lambda r: r.get_by_label(re.compile(r"VIN\s*Number|VIN|Chassis|Vehicle\s*Identification|Frame\s*No", re.I)),
        lambda r: r.locator("#divtxtFrameNo input.txtBox"),
        lambda r: r.locator('input[name*="vin" i]'),
        lambda r: r.locator('input[id*="vin" i]'),
        lambda r: r.locator('input[placeholder*="VIN" i]'),
        lambda r: r.get_by_placeholder(re.compile(r"chassis|vin|frame", re.I)),
    )
    for fac in factories:
        for root in _hero_misp_page_and_frame_roots(page, purpose="vin"):
            try:
                loc = fac(root)
                if loc.count() == 0:
                    continue
                el = loc.first
                el.wait_for(state="attached", timeout=min(8_000, to))
                try:
                    el.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                el.fill("", timeout=to)
                el.fill(v, timeout=to, force=True)
                logger.info("Hero Insurance: filled VIN/Chassis (fallback in frame).")
                return True
            except Exception:
                continue
    return False


def _hero_misp_fill_vin_and_click_submit(
    page,
    values: dict,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    After KYC **Proceed**: on the VIN step, fill ``full_chassis`` / ``frame_no`` and click **Submit**.
    Does not handle **I agree** — that is **main_process**. Returns **None** on success, else an error message.
    """
    vin = (values.get("full_chassis") or values.get("frame_no") or "").strip()
    if not vin:
        return "vehicle_master full_chassis/frame (VIN) is empty in DB values."

    _hero_misp_log_vin_transition_line(
        page,
        phase="pre_process_vin_fill_submit_start",
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )

    t0_vin = time.monotonic()
    _insurance_vin_phase_note(
        ocr_output_dir, subfolder, t0_vin, "vin_fill_submit_start"
    )

    skip_pre_dom = False
    try:
        u0 = (page.url or "").lower()
        if "mispdms.aspx" in u0:
            rs = page.evaluate("() => document.readyState")
            if rs == "complete":
                skip_pre_dom = True
                _insurance_vin_phase_note(
                    ocr_output_dir,
                    subfolder,
                    t0_vin,
                    "pre_domcontentloaded_skipped",
                    "mispdms.aspx readyState=complete",
                )
    except Exception:
        pass

    if not skip_pre_dom:
        pre_cap = max(500, min(int(INSURANCE_VIN_PRE_DOMCONTENTLOADED_MS), 60_000))
        _wait_load_optional(page, pre_cap)
        _insurance_vin_phase_note(
            ocr_output_dir, subfolder, t0_vin, "pre_domcontentloaded_done"
        )

    filled = _hero_misp_fill_vin_txt_frame_no(
        page,
        vin,
        timeout_ms=timeout_ms,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        t0_vin=t0_vin,
    )
    if not filled:
        filled = _hero_misp_fill_vin_fallback_all_frames(page, vin, timeout_ms=timeout_ms)
        if filled:
            _insurance_vin_phase_note(
                ocr_output_dir,
                subfolder,
                t0_vin,
                "vin_chassis_filled",
                "fallback_all_frames",
            )
    if not filled:
        return "Could not find VIN/Chassis input after KYC Proceed (expected redirect to VIN page)."

    clicked = _hero_misp_click_vin_page_submit(page, timeout_ms=timeout_ms)
    if not clicked:
        try:
            sub = page.get_by_role("button", name=re.compile(r"^\s*Submit\s*$", re.I))
            if sub.count() > 0 and sub.first.is_visible(timeout=2_000):
                sub.first.click(timeout=timeout_ms)
            else:
                page.get_by_text(re.compile(r"^\s*Submit\s*$", re.I)).first.click(timeout=timeout_ms)
            logger.info("Hero Insurance: clicked Submit on VIN page.")
        except Exception as exc:
            return f"VIN Submit click failed: {exc!s}"

    _insurance_vin_phase_note(
        ocr_output_dir, subfolder, t0_vin, "vin_page_submit_clicked_or_fallback"
    )
    _t(page, HERO_MISP_UI_SETTLE_MS)
    _insurance_vin_phase_note(
        ocr_output_dir, subfolder, t0_vin, "vin_fill_submit_complete"
    )
    return None


def _hero_misp_click_vin_page_submit(page, *, timeout_ms: int) -> bool:
    """Real MISP: ``ctl00$ContentPlaceHolder1$btnSubmit`` (often next to VIN inside UpdatePanel)."""
    to = min(int(timeout_ms), 60_000)
    selectors = (
        '#ctl00_ContentPlaceHolder1_upnlAddStateMaster input[type="submit"][name="ctl00$ContentPlaceHolder1$btnSubmit"]',
        '#mainContainer input[type="submit"][name="ctl00$ContentPlaceHolder1$btnSubmit"]',
        '#ctl00_ContentPlaceHolder1_upnlAddStateMaster input[name="ctl00$ContentPlaceHolder1$btnSubmit"]',
        '#mainContainer input[name="ctl00$ContentPlaceHolder1$btnSubmit"]',
        'input[type="submit"][name="ctl00$ContentPlaceHolder1$btnSubmit"]',
        'input[name="ctl00$ContentPlaceHolder1$btnSubmit"]',
    )
    for sel in selectors:
        for root in _hero_misp_page_and_frame_roots(page, purpose="vin"):
            try:
                loc = root.locator(sel)
                if loc.count() == 0:
                    continue
                el = loc.first
                el.wait_for(state="attached", timeout=min(10_000, to))
                try:
                    el.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                el.click(timeout=to, force=True)
                logger.info("Hero Insurance: clicked VIN Submit (%s).", sel[:72])
                return True
            except Exception:
                continue
    return False


def _hero_misp_post_vin_i_agree_modal_visible(page, *, timeout_ms: int = 1_500) -> bool:
    """
    True when the Bootstrap post–VIN **I Agree** modal (``#btnOK``) is actually visible.
    Scans main document and a capped list of frames.
    """
    vt = min(max(300, int(timeout_ms)), 5_000)
    selectors = (
        "div.modal.show button#btnOK",
        "div.modal.in button#btnOK",
        "div.modal.fade.in button#btnOK",
        "div.modal-content button#btnOK",
    )
    roots: list = [page]
    try:
        roots.extend([f for f in page.frames if not f.is_detached()][:16])
    except Exception:
        pass
    for root in roots:
        for sel in selectors:
            try:
                loc = root.locator(sel)
                if loc.count() == 0:
                    continue
                if loc.first.is_visible(timeout=vt):
                    return True
            except Exception:
                continue
    return False


def _hero_misp_proposal_form_markers_visible(page, *, timeout_ms: int = 2_000) -> bool:
    """
    True when main **MispPolicy** proposal ``ContentPlaceHolder1`` controls are visible — i.e. user is
    already past VIN Submit + **I agree** (common when KYC / modal steps were completed manually).
    Checks top document first, then ``INSURANCE_NAV_IFRAME_SELECTOR`` (same order as proposal fills).
    """
    to = min(max(400, int(timeout_ms)), 8_000)
    for root in (page,):
        for suffix in ("ddlOccupatnType", "ddlRTO", "ddlMaritalStatus"):
            try:
                loc = _proposal_cph1_locator(root, suffix)
                if loc.count() == 0:
                    continue
                if loc.first.is_visible(timeout=min(1_500, to)):
                    return True
            except Exception:
                continue
    if INSURANCE_NAV_IFRAME_SELECTOR:
        try:
            fl = page.frame_locator(INSURANCE_NAV_IFRAME_SELECTOR)
            for suffix in ("ddlOccupatnType", "ddlRTO", "ddlMaritalStatus"):
                try:
                    loc = _proposal_cph1_locator(fl, suffix)
                    if loc.count() == 0:
                        continue
                    if loc.first.is_visible(timeout=min(1_200, to)):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


def _hero_misp_click_vin_post_submit_modal_i_agree(
    page,
    *,
    timeout_ms: int,
    visible_timeout_ms: int,
) -> bool:
    """
    After VIN **Submit**, MISP opens a Bootstrap modal (``div.modal-content``) with **I Agree** on
    ``button#btnOK`` (``HideModal()``). Tries main document and each non-detached frame.
    """
    to = min(int(timeout_ms), 60_000)
    vt = min(int(visible_timeout_ms), 30_000)
    selectors = (
        'div.modal-content button#btnOK',
        'div.modal-content.w-100 button#btnOK',
        'button#btnOK.button-modal.flex-fill',
        'button#btnOK.button-modal',
        'button#btnOK[type="button"]',
    )
    roots: list = [page]
    try:
        for fr in page.frames:
            if fr.is_detached():
                continue
            roots.append(fr)
    except Exception:
        pass
    for root in roots:
        for sel in selectors:
            try:
                loc = root.locator(sel)
                if loc.count() == 0:
                    continue
                btn = loc.first
                btn.wait_for(state="visible", timeout=vt)
                try:
                    btn.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                btn.click(timeout=to, force=True)
                logger.info("Hero Insurance: clicked post-VIN modal I Agree (#btnOK, %s).", sel[:40])
                return True
            except Exception:
                continue
    return False


def _hero_misp_i_agree_after_vin_submit(
    page,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> str | None:
    """
    After **pre_process** has filled VIN and clicked **Submit**: **main_process** dismisses the
    modal (``#btnOK`` **I Agree**) then continues on the main insurance form.

    If the portal already shows the proposal form (CPH1 dropdowns) and the post–VIN modal is **not**
    visible — e.g. operator finished KYC / modal manually — this step is skipped so automation does not
    spin on missing **I agree** controls.
    """
    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        "main_process: I agree step — scanning for post-VIN modal vs proposal form",
    )
    to = min(int(timeout_ms), 60_000)
    try:
        prop_here = _hero_misp_proposal_form_markers_visible(page, timeout_ms=min(2_500, to))
        modal_here = _hero_misp_post_vin_i_agree_modal_visible(page, timeout_ms=min(1_800, to))
        if prop_here and not modal_here:
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "main_process: skipped I agree — proposal markers visible, post-VIN modal not shown",
            )
            logger.info(
                "Hero Insurance: main_process — skipped post-VIN I agree; proposal shell already visible."
            )
            _wait_load_optional(page, min(15_000, max(to, 5_000) * 4))
            _t(page, 400)
            return None
    except Exception as exc:
        logger.debug("Hero Insurance: proposal/modal probe before I agree: %s", exc)

    agreed = False
    # First: wait up to ~20s for modal after navigation (VIN submit → main form + popup).
    if _hero_misp_click_vin_post_submit_modal_i_agree(
        page, timeout_ms=to, visible_timeout_ms=min(20_000, to)
    ):
        agreed = True
    else:
        for _ in range(28):
            if _hero_misp_click_vin_post_submit_modal_i_agree(
                page, timeout_ms=to, visible_timeout_ms=min(4_000, to)
            ):
                agreed = True
                break
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

    _wait_load_optional(page, min(30_000, timeout_ms * 5))
    _t(page, 600)
    return None
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


def _insurance_preview_apply_body_text_heuristics(body: str, out: dict[str, Any]) -> None:
    """Fill missing ``out`` keys from free-form page text (Proposal Review / preview / post-issue)."""
    if not body:
        return
    chunk = body[:150_000]

    if not out.get("policy_num"):
        m = re.search(
            r"(?:Policy|Proposal)\s*(?:Number|No\.?)\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,31})",
            chunk,
            re.I | re.M,
        )
        if m:
            out["policy_num"] = _normalize_policy_num_for_db(m.group(1))
    if not out.get("policy_num"):
        m = re.search(
            r"Proposal\s*No\.?\s*[:\s#]*\s*([A-Z]\d{5,20})\b",
            chunk,
            re.I | re.M,
        )
        if m:
            out["policy_num"] = _normalize_policy_num_for_db(m.group(1))

    if not out.get("policy_from"):
        m = re.search(
            r"Valid\s*From\s*[:\s]*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
            chunk,
            re.I | re.M,
        )
        if m:
            out["policy_from"] = m.group(1).strip()
    if not out.get("policy_to"):
        m = re.search(
            r"Valid\s*To\s*[:\s]*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
            chunk,
            re.I | re.M,
        )
        if m:
            out["policy_to"] = m.group(1).strip()
    if not out.get("policy_from"):
        m = re.search(
            r"Proposal\s*Date\s*[:\s]*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
            chunk,
            re.I | re.M,
        )
        if m:
            out["policy_from"] = m.group(1).strip()

    if (not out.get("policy_from") or not out.get("policy_to")) and chunk:
        for m in re.finditer(
            r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\s*[-–—to]+\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
            chunk[:80_000],
            re.I,
        ):
            if not out.get("policy_from"):
                out["policy_from"] = m.group(1).strip()
            if not out.get("policy_to"):
                out["policy_to"] = m.group(2).strip()
            if out.get("policy_from") and out.get("policy_to"):
                break
        if not out.get("policy_from") or not out.get("policy_to"):
            m2 = re.search(
                r"(?:Policy\s*)?(?:Period|From)\s*[:\s]*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}).*?"
                r"(?:To|End)\s*[:\s]*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
                chunk[:80_000],
                re.I | re.S,
            )
            if m2:
                if not out.get("policy_from"):
                    out["policy_from"] = m2.group(1).strip()
                if not out.get("policy_to"):
                    out["policy_to"] = m2.group(2).strip()

    if out.get("premium") is None:
        m = re.search(
            r"(?:Insurance\s*[Cc]ost|Total\s*(?:Policy\s*)?[Pp]remium|Net\s*[Pp]remium|Final\s*[Pp]remium|"
            r"Gross\s*[Pp]remium|Premium\s*(?:Amount|Paid|Payable)?|Amount\s*Payable)"
            r"\s*(?:\([^)]{0,48}\))?\s*[:\s]*\s*[₹RsINR.\s]*"
            r"([\d][\d,]*(?:\.\d{1,2})?)",
            chunk,
            re.I | re.M,
        )
        if m:
            amt = _parse_currency_amount_text(m.group(1))
            if amt is not None:
                out["premium"] = amt

    if out.get("idv") is None:
        m = re.search(
            r"Total\s*IDV\s*(?:\([^)]{0,48}\))?\s*[:\s₹RsINR.]*\s*([\d][\d,]*(?:\.\d{1,2})?)",
            chunk,
            re.I | re.M,
        )
        if m:
            amt = _parse_currency_amount_text(m.group(1))
            if amt is not None:
                out["idv"] = amt
    if out.get("idv") is None:
        for pat in (
            r"(?:IDV|Insured\s*Declared\s*Value)\s*[:\s₹RsINR.]*\s*([\d][\d,]*(?:\.\d{1,2})?)",
            r"IDV\s*[:\s]*\s*([\d][\d,]*(?:\.\d{1,2})?)",
        ):
            m = re.search(pat, chunk[:80_000], re.I | re.M)
            if m:
                amt = _parse_currency_amount_text(m.group(1))
                if amt is not None:
                    out["idv"] = amt
                    break


def scrape_insurance_policy_preview_before_issue(page, *, timeout_ms: int) -> dict[str, Any]:
    """
    Read **policy number** (Proposal No.), **Valid From** / **Valid To**, **premium**, and **Total IDV**
    from the proposal preview / **Proposal Review** page / post-**Issue Policy** screen — training
    dummy IDs ``#ins-preview-*``, then label/body heuristics across **main document + proposal iframes**
    (``_hero_misp_page_and_frame_roots(..., purpose="proposal")``). ``premium`` maps to DB total premium.
    """
    out: dict[str, Any] = {
        "policy_num": None,
        "policy_from": None,
        "policy_to": None,
        "premium": None,
        "idv": None,
    }
    to = max(2_000, min(int(timeout_ms), 25_000))
    roots = _hero_misp_page_and_frame_roots(page, purpose="proposal")
    if not roots:
        roots = [page]

    body_chunks: list[str] = []
    for root in roots:
        try:
            btxt = (root.locator("body").first.inner_text(timeout=min(12_000, to)) or "").strip()
            if btxt:
                body_chunks.append(btxt[:150_000])
        except Exception:
            pass

    for root in roots:
        try:
            loc_p = root.locator("#ins-preview-policy-num")
            if loc_p.count() > 0 and loc_p.first.is_visible(timeout=min(2_500, to)):
                t = (loc_p.first.inner_text() or "").strip()
                pn = _normalize_policy_num_for_db(t)
                if pn and not out["policy_num"]:
                    out["policy_num"] = pn
        except Exception as exc:
            logger.debug("Insurance preview scrape policy (dummy id): %s", exc)

        for dummy_id, key in (
            ("#ins-preview-policy-from", "policy_from"),
            ("#ins-preview-policy-to", "policy_to"),
        ):
            try:
                if out.get(key):
                    continue
                loc_d = root.locator(dummy_id)
                if loc_d.count() > 0 and loc_d.first.is_visible(timeout=min(2_000, to)):
                    t = (loc_d.first.inner_text() or "").strip()
                    if t:
                        out[key] = t
            except Exception as exc:
                logger.debug("Insurance preview scrape %s (dummy id): %s", key, exc)

        for dummy_id, key in (
            ("#ins-preview-premium", "premium"),
            ("#ins-preview-insurance-cost", "premium"),
            ("#ins-preview-idv", "idv"),
        ):
            try:
                if out.get(key) is not None:
                    continue
                loc_d = root.locator(dummy_id)
                if loc_d.count() > 0 and loc_d.first.is_visible(timeout=min(2_500, to)):
                    t = (loc_d.first.inner_text() or "").strip()
                    amt = _parse_currency_amount_text(t)
                    if amt is not None:
                        out[key] = amt
            except Exception as exc:
                logger.debug("Insurance preview scrape %s (dummy id): %s", key, exc)

    if not out["policy_num"]:
        for pat in (
            re.compile(r"Policy\s*(?:Number|No\.?)\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,31})", re.I),
            re.compile(r"Proposal\s*(?:Number|No\.?)\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,31})", re.I),
        ):
            for root in roots:
                try:
                    loc = root.get_by_text(pat)
                    if loc.count() > 0 and loc.first.is_visible(timeout=2_000):
                        m = pat.search((loc.first.inner_text() or "")[:300])
                        if m:
                            cand = _normalize_policy_num_for_db(m.group(1).strip())
                            if cand:
                                out["policy_num"] = cand
                                break
                except Exception:
                    continue
            if out["policy_num"]:
                break

    for root in roots:
        try:
            body = (root.locator("body").first.inner_text(timeout=min(12_000, to)) or "")[:150_000]
        except Exception:
            body = ""
        _insurance_preview_apply_body_text_heuristics(body, out)

    if (
        not out.get("policy_num")
        or not out.get("policy_from")
        or not out.get("policy_to")
        or out.get("premium") is None
        or out.get("idv") is None
    ) and body_chunks:
        merged = "\n\n".join(body_chunks)
        _insurance_preview_apply_body_text_heuristics(merged[:200_000], out)

    if any(v is not None for v in out.values()):
        logger.info(
            "Insurance policy preview scrape: policy_num=%r policy_from=%r policy_to=%r premium=%s idv=%s",
            out.get("policy_num"),
            out.get("policy_from"),
            out.get("policy_to"),
            out.get("premium"),
            out.get("idv"),
        )
    return out


def _hero_misp_note_proposal_review_scrape_for_insurance_master(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    preview: dict[str, Any],
) -> None:
    """Human-readable line in ``Playwright_insurance.txt`` for operator verification vs MISP."""
    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        "proposal_review_page (insurance_master fields): "
        f"Proposal No.={preview.get('policy_num')!r}; "
        f"Valid From={preview.get('policy_from')!r}; "
        f"Valid To={preview.get('policy_to')!r}; "
        f"Total IDV={preview.get('idv')!r}; "
        f"Premium={preview.get('premium')!r}",
    )


def _proposal_scroll_root_to_bottom(root) -> None:
    """Scroll a **Page**, **Frame**, or **FrameLocator** root so footer checkboxes / actions are visible."""
    try:
        root.locator("body").first.evaluate(
            "e => { try { e.scrollTop = e.scrollHeight; } catch (x) {} }"
        )
    except Exception:
        pass


def _hero_misp_proposal_review_print_proposal_and_consent(
    page,
    *,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    timeout_ms: int,
) -> str | None:
    """
    On **Proposal Review**: click **Print Proposal** (``Submit3``), then check **chkAgree** and
    **chkconsentagree**. Print click is best-effort (browser print dialog); consent checkboxes are required.
    """
    to = min(int(timeout_ms), 60_000)
    roots = _hero_misp_page_and_frame_roots(page, purpose="proposal")
    if not roots:
        roots = [page]

    for r in roots:
        _proposal_scroll_root_to_bottom(r)
    _t(page, 300)

    printed = False
    for root in roots:
        if printed:
            break
        try:
            pb = root.locator("button").filter(has_text=re.compile(r"Print\s*Proposal", re.I))
            if pb.count() > 0:
                el = pb.first
                if not el.is_visible(timeout=min(2_000, to)):
                    _proposal_scroll_visible(el, timeout_ms=to)
                el.click(timeout=to, force=True)
                printed = True
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "proposal_review: clicked Print Proposal (button element)",
                )
                logger.info("Hero Insurance: clicked Print Proposal (button).")
        except Exception as exc:
            logger.debug("Hero Insurance: Print Proposal button: %s", exc)

        for sel in (
            'input[type="button"][name="Submit3"][value="Print Proposal"]',
            'input.btn-success[name="Submit3"]',
            'input[type="button"][value="Print Proposal"]',
        ):
            try:
                loc = root.locator(sel)
                if loc.count() == 0:
                    continue
                el = loc.first
                if not el.is_visible(timeout=min(3_000, to)):
                    _proposal_scroll_visible(el, timeout_ms=to)
                if not el.is_visible(timeout=min(3_500, to)):
                    continue
                el.click(timeout=to, force=True)
                printed = True
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "proposal_review: clicked Print Proposal (input name=Submit3)",
                )
                logger.info("Hero Insurance: clicked Print Proposal (Submit3).")
                break
            except Exception as exc:
                logger.debug("Hero Insurance: Print Proposal selector %r: %s", sel[:56], exc)
                continue
        if printed:
            break

    if not printed:
        for root in roots:
            if printed:
                break
            try:
                pr = root.get_by_role("button", name=re.compile(r"Print\s*Proposal", re.I))
                if pr.count() > 0:
                    el = pr.first
                    if not el.is_visible(timeout=min(2_000, to)):
                        _proposal_scroll_visible(el, timeout_ms=to)
                    el.click(timeout=to, force=True)
                    printed = True
                    append_playwright_insurance_line(
                        ocr_output_dir,
                        subfolder,
                        "NOTE",
                        "proposal_review: clicked Print Proposal (role=button)",
                    )
                    logger.info("Hero Insurance: clicked Print Proposal (role=button).")
                    break
            except Exception as exc:
                logger.debug("Hero Insurance: Print Proposal role=button: %s", exc)

    if not printed:
        for root in roots:
            try:
                lk = root.locator("a").filter(has_text=re.compile(r"Print\s*Proposal", re.I))
                if lk.count() > 0:
                    lk.first.click(timeout=to, force=True)
                    printed = True
                    append_playwright_insurance_line(
                        ocr_output_dir,
                        subfolder,
                        "NOTE",
                        "proposal_review: clicked Print Proposal (link)",
                    )
                    logger.info("Hero Insurance: clicked Print Proposal (link).")
                    break
            except Exception as exc:
                logger.debug("Hero Insurance: Print Proposal link: %s", exc)

    if not printed:
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "proposal_review: Print Proposal control not found — skipped (check MISP layout)",
        )
        logger.warning("Hero Insurance: Print Proposal control not found; continuing to consent checkboxes.")

    _t(page, 450)

    for cid in ("chkAgree", "chkconsentagree"):
        checked_ok = False
        last_err = ""
        for root in roots:
            try:
                _proposal_scroll_root_to_bottom(root)
                _t(page, 150)
                loc = _proposal_cph1_locator(root, cid)
                if loc.count() == 0:
                    loc = root.locator(f'input[type="checkbox"][id*="{cid}" i]')
                if loc.count() == 0 and cid == "chkAgree":
                    loc = root.locator('input[type="checkbox"][id*="chkAgree" i]')
                if loc.count() == 0 and cid == "chkAgree":
                    loc = root.locator('input[type="checkbox"][name*="chkAgree" i]')
                if loc.count() == 0 and cid == "chkconsentagree":
                    loc = root.locator('input[type="checkbox"][id*="consent" i]')
                if loc.count() == 0:
                    continue
                cb = loc.first
                if not cb.is_visible(timeout=min(2_000, to)):
                    _proposal_scroll_visible(cb, timeout_ms=to)
                if not cb.is_visible(timeout=min(3_000, to)):
                    continue
                if not cb.is_checked():
                    try:
                        cb.check(timeout=to, force=True)
                    except Exception:
                        try:
                            cb.click(timeout=to, force=True)
                        except Exception:
                            pass
                if not cb.is_checked():
                    try:
                        cb.evaluate(
                            """e => {
                              e.checked = true;
                              e.dispatchEvent(new Event('click', { bubbles: true }));
                              e.dispatchEvent(new Event('change', { bubbles: true }));
                            }"""
                        )
                    except Exception:
                        pass
                if cb.is_checked():
                    checked_ok = True
                    _proposal_log(
                        ocr_output_dir,
                        subfolder,
                        f"proposal_review_{cid}",
                        "checkbox checked ok",
                    )
                    break
                last_err = "is_checked() false after check"
            except Exception as exc:
                last_err = str(exc)
                continue
        if not checked_ok:
            return f"proposal_review: could not check {cid} ({last_err})"

    return None


def click_issue_policy_and_scrape_preview(page, *, timeout_ms: int) -> dict[str, Any]:
    """
    Click **Issue Policy** (dummy ``#ins-issue-policy`` or MISP button / text), wait for navigation,
    then scrape ``policy_num``, ``policy_from``, ``policy_to``, ``premium``, ``idv`` via
    ``scrape_insurance_policy_preview_before_issue``.
    When ``HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY`` is True, skips the **Issue Policy** click and only scrapes.
    """
    to = max(2_000, int(timeout_ms))
    if HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY:
        logger.info(
            "Hero Insurance: Issue Policy click skipped (HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY=True)."
        )
        _t(page, 600)
        _wait_load_optional(page, min(25_000, to * 4))
        return scrape_insurance_policy_preview_before_issue(page, timeout_ms=to)
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
    _wait_load_optional(page, min(25_000, to * 4))
    return scrape_insurance_policy_preview_before_issue(page, timeout_ms=to)


def _hero_misp_fill_proposal_and_review(
    page,
    values: dict,
    *,
    timeout_ms: int,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    staging_payload: dict | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Proposal page after **I agree**: each fill is read back and logged (``Playwright_insurance.txt``);
    first failed step returns an error message. When ``customer_id`` / ``vehicle_id`` are set,
    ``insert_insurance_master_after_gi`` runs (staging / fill values; no preview scrape yet), then
    **Proposal Preview** (portal label) → scrape preview → ``update_insurance_master_policy_after_issue`` with that scrape.
    """
    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        "main_process: proposal form fill starting (marital, occupation, RTO, email, …)",
    )
    pt = max(int(timeout_ms), int(INSURANCE_POLICY_FILL_TIMEOUT_MS))

    _wait_load_optional(page, min(30_000, pt * 6))
    _t(page, 500)

    raw_marital = (values.get("marital_status") or "").strip()
    ms = _proposal_map_marital_for_misp(raw_marital)
    if raw_marital:
        _proposal_log(
            ocr_output_dir,
            subfolder,
            "marital_status",
            f"raw={raw_marital!r} mapped={ms!r}",
        )
    if ms:
        err = _proposal_step_select_fuzzy(
            page,
            (r"Marital\s*Status", r"Marital"),
            ms,
            "marital_status",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="ddlMaritalStatus",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    prof = _proposal_map_occupation_for_misp((values.get("profession") or "").strip())
    err = _proposal_step_select_fuzzy(
        page,
        (r"Occupation\s*Type", r"Occupation"),
        prof,
        "occupation",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
        cph1_id_suffix="ddlOccupatnType",
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    dob_val = (values.get("dob") or "").strip()
    if dob_val:
        err = _proposal_step_fill_dob(
            page,
            dob_val,
            "date_of_birth",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    err = _proposal_step_email_hardcoded(
        page,
        "na@gmail.com",
        "email",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    alt_raw = (values.get("alt_phone_num") or "").strip()
    if alt_raw:
        alt_digits = re.sub(r"\D", "", alt_raw)[:10]
        if len(alt_digits) == 10:
            err = _proposal_step_fill_input(
                page,
                (
                    r"Alternate\s*Mobile",
                    r"Alternate\s*Mobile\s*No",
                    r"Alt\.?\s*Mobile",
                ),
                alt_digits,
                "alternate_mobile",
                ocr_output_dir,
                subfolder,
                timeout_ms=pt,
                cph1_id_suffix="txtMobile2",
            )
            if err:
                return _proposal_fail(ocr_output_dir, subfolder, err)

    city = (values.get("city") or "").strip()
    rto_query = city if city else "City"
    err = _proposal_step_select_fuzzy(
        page,
        (r"RTO", r"Registering\s*Authority|R\.?T\.?O"),
        rto_query,
        "rto",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
        cph1_id_suffix="ddlRTO",
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    mname = (values.get("model_name") or "").strip()
    if mname:
        err = _proposal_step_select_fuzzy(
            page,
            (r"Model\s*Name", r"Model"),
            mname,
            "model_name",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="ddlModelName",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    err = _proposal_step_date_of_registration_today(
        page,
        "date_of_registration",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    nn = (values.get("nominee_name") or "").strip()
    if nn:
        err = _proposal_step_fill_input(
            page,
            (r"Nominee\s*Name", r"Name\s*of\s*Nominee"),
            nn,
            "nominee_name",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="txtNomineeName",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)
        _t(page, 350)

    na = (values.get("nominee_age") or "").strip()
    if na:
        err = _proposal_step_fill_input(
            page,
            (r"Nominee\s*Age", r"Age\s*of\s*Nominee"),
            na,
            "nominee_age",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="txtNomineeAge",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    ng = (values.get("nominee_gender") or "").strip()
    if ng:
        err = _proposal_step_nominee_gender_radio(
            page,
            ng,
            "nominee_gender",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    rel = (values.get("nominee_relationship") or "").strip()
    if rel:
        err = _proposal_step_select_fuzzy(
            page,
            (
                r"Nominee\s*Relation",
                r"Relation\s*with\s*Nominee",
                r"Relation\s*with\s*Proposer",
                r"Relation\s*with",
            ),
            rel,
            "nominee_relationship",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="ddlNomineeRelation",
        )
        if err:
            err = _proposal_step_fill_input(
                page,
                (r"Relation\s*with|Nominee\s*Relation",),
                rel,
                "nominee_relationship",
                ocr_output_dir,
                subfolder,
                timeout_ms=pt,
            )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    fin = (values.get("financer_name") or "").strip()
    if fin:
        err = _proposal_step_select_fuzzy(
            page,
            (r"Financier\s*Name", r"Financer\s*Name", r"Name\s*of\s*Financier"),
            fin,
            "financer_name",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="ddlFinancerName",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

        err = _proposal_step_select_fuzzy(
            page,
            (
                r"Agreement\s*Type\s*with\s*Financer",
                r"Agreement\s*Type\s*with\s*Financier",
                r"Aggwid\s*Financer",
                r"Agreement\s*Type\s*\(?\s*Financer",
            ),
            "HPA",
            "agreement_type_with_financer",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="ddlAgreementTypeWithFinancer",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    branch_city = city
    if branch_city and fin:
        err = _proposal_step_fill_input(
            page,
            (
                r"Finance\s*Company\s*Branch",
                r"Financier\s*Branch",
                r"Branch\s*Name",
                r"Finance\s*Branch",
            ),
            branch_city,
            "finance_branch",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
            cph1_id_suffix="txtFinComBranch",
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    if dob_val:
        v_norm = normalize_dob_for_misp(dob_val)
        if v_norm:
            got_dob = _proposal_read_dob_txt(page)
            if not got_dob or not _proposal_dob_readback_matches_expected(v_norm, got_dob):
                err = _proposal_step_fill_dob(
                    page,
                    dob_val,
                    "date_of_birth_reassert",
                    ocr_output_dir,
                    subfolder,
                    timeout_ms=pt,
                )
                if err:
                    return _proposal_fail(ocr_output_dir, subfolder, err)

    _t(page, 400)
    err = _proposal_addon_checkbox_id_or_label(
        page,
        "chkNilDepreciation",
        True,
        "addon_nd_cover",
        r"ND\s*Cover|Nil\s*Depreciation",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)
    err = _proposal_addon_checkbox_id_or_label(
        page,
        "chkroicover",
        True,
        "addon_rti",
        r"RTI\s*Cover|RTI\s*&?\s*Cover|R\.?T\.?I\.?\s*Cover|Return\s+to\s+Invoice|"
        r"Return\s*to\s*Invoice\s*\(?\s*RTI|Invoice\s*Cover|Cover\s*[-–]?\s*RTI|ROI",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)
    err = _proposal_addon_checkbox_id_or_label(
        page,
        "chkRSA",
        False,
        "addon_rsa",
        r"^RSA$|RSA\s*cover|RSA\s*Cover|Road\s*Side\s*Assist|Roadside\s*Assist",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)
    err = _proposal_addon_checkbox_id_or_label(
        page,
        "chkEME",
        False,
        "addon_emergency_medical",
        r"Emergency\s*Medical\s*Expenses?|Emergency\s*Medical|Medical\s*Emergency|"
        r"Emerg(?:ency)?\.?\s*Medical|Medical\s*Expenses?\s*\(?\s*Emerg|EME\b",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    _opt_uncheck = (HERO_MISP_PROPOSAL_OPTIONAL_UNCHECK_CHECKBOX_REGEX or "").strip()
    if _opt_uncheck:
        err = _proposal_step_checkbox_uncheck_if_present(
            page,
            _opt_uncheck,
            "addon_optional_uncheck",
            ocr_output_dir,
            subfolder,
            timeout_ms=pt,
        )
        if err:
            return _proposal_fail(ocr_output_dir, subfolder, err)

    err = _proposal_step_select_fuzzy(
        page,
        (r"CPA\s*Tenure", r"CPA"),
        "0",
        "cpa_tenure",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
        cph1_id_suffix="ddlCPATenure",
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    err = _proposal_step_usgi_uncheck(
        page,
        "cpa_usgi_uncheck",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    err = _proposal_step_hero_cpi_addon_by_dealer_flag(
        page,
        values,
        "addon_hero_cpi",
        ocr_output_dir,
        subfolder,
        timeout_ms=pt,
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    _t(page, 400)
    err = _proposal_step_hdfc_payment(
        page, "payment_hdfc", ocr_output_dir, subfolder, timeout_ms=pt
    )
    if err:
        return _proposal_fail(ocr_output_dir, subfolder, err)

    _t(page, 500)
    if customer_id is not None and vehicle_id is not None:
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "main_process: insurance_master INSERT (post-proposal fill, before Proposal Preview)",
        )
        try:
            insert_insurance_master_after_gi(
                int(customer_id),
                int(vehicle_id),
                fill_values=values,
                staging_payload=staging_payload,
                preview_scrape=None,
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
        except ValueError as persist_exc:
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "ERROR",
                f"main_process: insurance_master insert failed: {persist_exc!s}",
            )
            return str(persist_exc), {}
        except Exception as persist_exc:
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "ERROR",
                f"main_process: insurance_master insert failed: {persist_exc!s}",
            )
            return f"insurance_master insert failed: {persist_exc!s}", {}

    try:
        _proposal_preview_rx = re.compile(r"Proposal\s*(Preview|Review)", re.I)
        clicked = False
        for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
            rev = root.get_by_role("button", name=_proposal_preview_rx)
            if rev.count() > 0 and rev.first.is_visible(timeout=3_000):
                rev.first.click(timeout=pt)
                clicked = True
                break
        if not clicked:
            for root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
                try:
                    root.get_by_text(_proposal_preview_rx).first.click(timeout=pt)
                    clicked = True
                    break
                except Exception:
                    continue
        if not clicked:
            return _proposal_fail(ocr_output_dir, subfolder, "proposal_review: could not find or click Proposal Preview / Proposal Review")
        _proposal_log(
            ocr_output_dir,
            subfolder,
            "proposal_review",
            "clicked ok",
        )
        logger.info("Hero Insurance: clicked Proposal Preview (or Proposal Review).")
    except Exception as exc:
        return _proposal_fail(ocr_output_dir, subfolder, f"proposal_review: {exc!s}")

    _t(page, 600)
    _wait_load_optional(page, min(25_000, pt * 5))
    for _pr_root in _hero_misp_page_and_frame_roots(page, purpose="proposal"):
        try:
            _pr_root.get_by_text(
                re.compile(r"Print\s*Proposal|Proposal\s*No\.?", re.I)
            ).first.wait_for(state="visible", timeout=min(15_000, pt * 3))
            break
        except Exception:
            continue
    _t(page, 400)
    preview = scrape_insurance_policy_preview_before_issue(page, timeout_ms=pt)
    _hero_misp_note_proposal_review_scrape_for_insurance_master(
        ocr_output_dir, subfolder, preview
    )
    if customer_id is not None and vehicle_id is not None:
        try:
            update_insurance_master_policy_after_issue(
                int(customer_id),
                int(vehicle_id),
                scrape=preview,
            )
        except Exception as upd_exc:
            logger.warning(
                "Hero Insurance: insurance_master update from proposal preview scrape failed: %s",
                upd_exc,
            )

    err_pr = _hero_misp_proposal_review_print_proposal_and_consent(
        page,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        timeout_ms=pt,
    )
    if err_pr:
        return _proposal_fail(ocr_output_dir, subfolder, err_pr)
    return None, preview


def _insurance_match_base_from_config(insurance_base_url: str) -> tuple[str, str]:
    """Return ``(match_base`` origin, ``login_url`` full) for ``main_process`` tab reuse."""
    u = (insurance_base_url or "").strip()
    if not u.startswith("http"):
        u = "https://" + u.lstrip("/")
    p = urllib.parse.urlparse(u)
    if not p.netloc:
        raise ValueError("INSURANCE_BASE_URL must be a valid URL with a host")
    origin = f"{p.scheme}://{p.netloc}".rstrip("/")
    login_full = u.rstrip("/")
    return origin, login_full


def pre_process(
    *,
    insurance_base_url: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    subfolder: str | None = None,
    ocr_output_dir: Path | None = None,
    staging_payload: dict | None = None,
    dealer_id: int | None = None,
) -> dict:
    """
    Hero insurance **pre** stage: same behavior as ``run_fill_insurance_only`` (former standalone
    ``POST .../insurance``). On real MISP, includes **VIN fill + Submit** before handoff. Hands off
    ``match_base`` / ``_insurance_playwright_page`` to ``main_process``; dummy training HTML may finish
    the full flow (``main_process`` skips).
    """
    raw = (insurance_base_url or INSURANCE_BASE_URL or "").strip()
    if not raw:
        return {
            "success": False,
            "error": "Set INSURANCE_BASE_URL in backend/.env (or pass insurance_base_url).",
            "page_url": None,
            "login_url": None,
            "match_base": None,
        }
    return run_fill_insurance_only(
        raw,
        subfolder=subfolder,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        ocr_output_dir=ocr_output_dir,
        staging_payload=staging_payload,
        dealer_id=dealer_id,
    )


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
    After **pre_process** (KYC → **VIN fill** → **Submit** on real MISP): **I agree** (if shown) → proposal form.
    **Customer/vehicle/nominee/financer** fields come from
    ``form_insurance_view`` / ``_build_insurance_fill_values``; **email, add-ons, CPA tenure, payment (HDFC),
    and registration date** use hardcoded defaults for now. **insurance_master** INSERT runs inside proposal fill,
    before **Proposal Preview**; preview fields are updated after the preview scrape and again after **Issue Policy**
    when applicable. **Issue Policy** click may be skipped when ``HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY``
    is True; **Proposal Review** is always attempted.
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

    if pre_result.get("hero_pre_completed_full_dummy_flow"):
        out["success"] = True
        out["skipped"] = True
        out["error"] = None
        out["page_url"] = pre_result.get("page_url")
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
        ocr_output_dir, subfolder, "NOTE", "main_process: I agree → proposal form (VIN+Submit done in pre_process)"
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
        err = _hero_misp_i_agree_after_vin_submit(
            page,
            timeout_ms=to,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )
        if err:
            out["error"] = err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"main_process: I agree step failed: {err}"
            )
            return out
        prop_err, preview = _hero_misp_fill_proposal_and_review(
            page,
            values,
            timeout_ms=to,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            customer_id=int(customer_id),
            vehicle_id=int(vehicle_id),
            staging_payload=staging_payload,
        )
        if prop_err:
            out["error"] = prop_err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"main_process: proposal form failed: {prop_err}"
            )
            return out
        post_issue = click_issue_policy_and_scrape_preview(page, timeout_ms=to)
        try:
            update_insurance_master_policy_after_issue(
                int(customer_id),
                int(vehicle_id),
                scrape=post_issue,
            )
        except Exception as upd_exc:
            logger.warning("main_process: insurance_master post-issue update failed: %s", upd_exc)
        out["success"] = True
        out["error"] = None
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "main_process: completed — insurance_master insert (pre-preview), Proposal Review, preview + post-issue "
            f"updates (Issue Policy click skipped={HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY})",
        )
        try:
            out["page_url"] = (page.url or "").strip() or None
        except Exception:
            out["page_url"] = None
    except PlaywrightTimeout as exc:
        out["error"] = f"Timeout: {exc!s}"
        logger.error("Hero Insurance main_process: %s", out["error"])
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "ERROR",
            f"main_process: {out['error']}",
        )
    except Exception as exc:
        out["error"] = str(exc)
        logger.exception("Hero Insurance main_process failed")
        append_playwright_insurance_line(
            ocr_output_dir,
            subfolder,
            "ERROR",
            f"main_process: {out['error']}",
        )
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
    main_ok = bool(main_result.get("success")) or (
        bool(main_result.get("skipped")) and main_result.get("error") in (None, "")
    )
    ok = bool(pre_result.get("success")) and main_ok
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
def run_fill_insurance_only(
    insurance_base_url: str,
    *,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    ocr_output_dir: Path | None = None,
    staging_payload: dict | None = None,
    dealer_id: int | None = None,
) -> dict:
    """
    Fill Insurance portal from DB-backed values (``INSURANCE_BASE_URL`` = production MISP or partner login).
    Real MISP: **login** → **2W** → **New Policy** → **KYC** fill + **Proceed** → **VIN** + VIN page **Submit** (then
    **main_process** continues with **I agree** / proposal on Hero GI). Training-only HTML (**#ins-company**) is
    **disabled** (error); legacy flow preserved under ``if False`` for removal after owner confirmation.
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
        kyc_scan_paths = _kyc_local_scan_paths_from_uploaded_scans(dealer_id, subfolder)
        if kyc_scan_paths:
            values["kyc_local_scan_paths"] = kyc_scan_paths
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "KYC uploads: using Uploaded scans (Aadhar.jpg, Aadhar_back.jpg; third slot reuses front).",
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
        t0_flow = time.monotonic()
        _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "page_open")
        # Real MISP (and similar): same automated Sign In as Hero ``pre_process`` — this endpoint
        # previously only waited for manual login (_wait_for_insurance_kyc_after_login).
        _insurance_click_settle(page)
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
            if not _still_on_heroinsurance_misp_partner_login(page):
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "run_fill_insurance_only: past partner login URL — Sign In automation skipped",
                )
            else:
                append_playwright_insurance_line(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "run_fill_insurance_only: Sign In not auto-clicked — password field not ready or "
                    "Sign In did not leave partner login; complete login manually if needed.",
                )
        # Same MISP landing as Hero pre_process: after login, **2W** then **New Policy** before KYC / dummy fields.
        _hero_misp_after_sign_in_settle(page)
        _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "after_sign_in_settle")
        page, err_2w = _misp_click_nav_step(
            page, _click_2w_icon, "2W (two-wheeler)",
            portal_base_url=insurance_base_url.strip(), timeout_ms=INSURANCE_ACTION_TIMEOUT_MS,
            ocr_output_dir=ocr_output_dir, subfolder=subfolder, t0_flow=t0_flow,
        )
        if err_2w:
            logger.warning("Hero Insurance run_fill_insurance_only: %s", err_2w)
            result["error"] = err_2w
            append_playwright_insurance_line(ocr_output_dir, subfolder, "ERROR", err_2w)
            return result
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "NOTE", "run_fill_insurance_only: active tab after 2W"
        )
        _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "after_2w")
        _insurance_click_settle(page)
        page, err_np = _misp_click_nav_step(
            page, _click_new_policy, "New Policy",
            portal_base_url=insurance_base_url.strip(), timeout_ms=INSURANCE_ACTION_TIMEOUT_MS,
            ocr_output_dir=ocr_output_dir, subfolder=subfolder, t0_flow=t0_flow,
        )
        if err_np:
            logger.warning("Hero Insurance run_fill_insurance_only: %s", err_np)
            result["error"] = err_np
            append_playwright_insurance_line(ocr_output_dir, subfolder, "ERROR", err_np)
            return result
        append_playwright_insurance_line(
            ocr_output_dir, subfolder, "NOTE", "run_fill_insurance_only: active tab before KYC wait"
        )
        _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "after_new_policy")

        wait_err = _wait_for_insurance_kyc_after_login(page, insurance_base_url)
        if wait_err:
            result["error"] = wait_err
            append_playwright_insurance_line(
                ocr_output_dir, subfolder, "NOTE", f"run_fill_insurance_only: KYC wait failed: {wait_err}"
            )
            return result

        _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "after_kyc_screen_ready")
        base = (insurance_base_url or "").strip().rstrip("/")

        # Real MISP ``ekycpage`` has no training-only ``#ins-company`` / ``#ins-mobile-no`` markup.
        if not _insurance_page_has_dummy_kyc_training_html(page):
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
                t0_flow=t0_flow,
            )
            if kyc_fill_err:
                result["error"] = kyc_fill_err
                append_playwright_insurance_line(
                    ocr_output_dir, subfolder, "ERROR", f"run_fill_insurance_only: {kyc_fill_err}"
                )
                return result
            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=max(500, min(int(INSURANCE_VIN_PRE_DOMCONTENTLOADED_MS), 60_000)),
                )
            except Exception:
                pass
            _t(page, HERO_MISP_UI_SETTLE_MS)
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: real MISP KYC (post-mobile banner branch + Proceed or uploads) complete",
            )
            _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "after_kyc_fill")
            vin_to = _hero_misp_vin_step_timeout_ms(INSURANCE_ACTION_TIMEOUT_MS)
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"run_fill_insurance_only: VIN fill + Submit (pre_process), timeout_ms={vin_to}",
            )
            vin_err = _hero_misp_fill_vin_and_click_submit(
                page,
                values,
                timeout_ms=vin_to,
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
            )
            if vin_err:
                result["error"] = vin_err
                result["success"] = False
                append_playwright_insurance_line(
                    ocr_output_dir, subfolder, "ERROR", f"run_fill_insurance_only: {vin_err}"
                )
                return result
            _insurance_pre_elapsed_note(ocr_output_dir, subfolder, t0_flow, "after_vin_submit")
            result["success"] = True
            result["error"] = None
            try:
                mb, lu = _insurance_match_base_from_config(insurance_base_url)
                result["match_base"] = mb
                result["login_url"] = lu
            except ValueError as _mb_err:
                result["error"] = str(_mb_err)
                result["success"] = False
                return result
            result["_insurance_playwright_page"] = page
            try:
                result["page_url"] = (page.url or "").strip() or None
            except Exception:
                result["page_url"] = None
            return result

        if _insurance_page_has_dummy_kyc_training_html(page):
            result["error"] = (
                "Training-only insurance HTML (#ins-company) is disabled; use real MISP. "
                "Legacy dummy flow preserved below under `if False` for deletion after owner double-checks."
            )
            result["success"] = False
            append_playwright_insurance_line(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "run_fill_insurance_only: training HTML branch disabled",
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
