"""
MISP: **Policy Issuance** (sidebar) → **Print Policy** → **AllPrintPolicy** search, then grid **Print**,
then the **Print Policy Certificates** window. **Save:** prefer Playwright ``Page.pdf`` (Chromium print-to-PDF;
works with the managed **Edge** or **Chrome** session from ``get_or_open_site_page`` — both use
``chromium`` with ``channel=msedge`` or ``chrome``) into **Upload-scans**; fallback: browser file download
(DMS-style).
# The **system print** UI (operator can print for the customer) is always scheduled after save
# via :func:`app.services.upload_scans_pdf_dispatch.schedule_misp_hero_post_pdf` (not env-gated; runs on the
# operator machine even when :data:`STORAGE_USE_S3` mirrors uploads to S3).
Env **HERO_MISP_PDF_STRATEGY** = ``auto`` (default) | ``playwright`` | ``download``.

Opt-in **HERO_MISP_PDF_DEBUG** = ``1``/``true`` (or similar): before ``page.pdf``, write
``misp_pdf_frame_debug_<IST-timestamp>.txt`` under the same path as ``Playwright_insurance.txt`` (i.e.
``<ocr_output_dir>/<safe_subfolder>/``). If that env is unset, **no dump is written** (only the PDF and log lines).

**Navigation:** Left menu under **Policy Issuance** → **Print Policy** (or optional ``MISP_GOTO_ALL_PRINT_POLICY``). After
the second **Print** in the cert window, we try **Policy Schedule** (``.../Policy/PolicySchedule.aspx``). A frame
debug often shows the server response containing ``window.open('.../Policy/PolicySchedule.aspx', …)`` — that tab is
**per-session** and may not repeat the same query string as ``AllPrintPolicy?PID=…``; we first **wait for a new
tab** whose URL contains **PolicySchedule**, then fall back to a derived **goto** (keeping ``?...`` from the
cert page when available), then **MISP_GOTO_POLICY_SCHEDULE** or a sidebar **Policy Schedule** link.

**Saved PDF name:** ``{10-digit-mobile}_Insurance_{ddmmyyyy}.pdf`` — ``ddmmyyyy`` is taken from the sale subfolder name ``{mobile}_{ddmmyyyy}`` when it matches; otherwise local date **Asia/Kolkata**. If mobile cannot be taken from subfolder, ``Insurance_{ddmmyyyy}_{policy_no_safe}.pdf``.

**Form (ASP.NET, AllPrintPolicy.aspx):** ``#ctl00_ContentPlaceHolder1_ddlProduct``,
``#ctl00_ContentPlaceHolder1_txtPolicyNo``, ``#ctl00_ContentPlaceHolder1_btnGO``; grid **Print** is
``gvCPDailySummary`` ``btnPrintPolicy``.

Logs to ``Playwright_insurance.txt`` via :func:`app.services.insurance_form_values.append_playwright_insurance_line`.

Uses :func:`run_hero_insure_reports` on the same Playwright **Insurance** (MISP) page as the proposal flow
(lazy-imports MISP helpers from ``fill_hero_insurance_service`` to avoid import cycles).
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from app.config import INSURANCE_ACTION_TIMEOUT_MS
from app.services.insurance_form_values import append_playwright_insurance_line

# from app.services.upload_scans_pdf_dispatch import schedule_misp_hero_post_pdf
from app.services.utility_functions import safe_subfolder_name

logger = logging.getLogger(__name__)

_PREFIX = "hero_insure_reports"

# ASP.NET ``ContentPlaceHolder1`` (MISP AllPrintPolicy / same pattern as DMS)
_CPH1 = "ctl00_ContentPlaceHolder1"
_SEL_PRODUCT = f"#{_CPH1}_ddlProduct"
_INP_POLICY_NO = f"#{_CPH1}_txtPolicyNo"
_BTN_GO = f"#{_CPH1}_btnGO"
_NAME_PRODUCT = r"ctl00$ContentPlaceHolder1$ddlProduct"
_NAME_POLICY_NO = r"ctl00$ContentPlaceHolder1$txtPolicyNo"
_NAME_GO = r"ctl00$ContentPlaceHolder1$btnGO"
_CSS_PRINT_IN_GRID = (
    f'input[id^="{_CPH1}_gvCPDailySummary_"][id$="btnPrintPolicy"]',
    f'input[name*="btnPrintPolicy"]',
    f'input[id*="btnPrintPolicy"]',
    'input.button1[type="submit"][value="Print"]',
)


def _misp_tmo() -> int:
    return max(3000, int(INSURANCE_ACTION_TIMEOUT_MS or 12_000))


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _mobile_prefix_from_subfolder(subfolder: str | None) -> str | None:
    m = re.match(r"^(\d{10})", (subfolder or "").strip())
    return m.group(1) if m else None


def _ddmmyyyy_from_subfolder_for_insurance_pdf(subfolder: str | None) -> str | None:
    """Match sale folder name ``{10 digits}_{8 digits}`` and return the ``ddmmyyyy`` part."""
    leaf = Path(str(subfolder or "").strip().replace("\\", "/")).name
    m = re.match(r"^\d{10}_(\d{8})$", leaf)
    return m.group(1) if m else None


def _insurance_pdf_out_name(
    mob: str | None, safe_policy_suffix: str, subfolder: str | None = None
) -> str:
    d = _ddmmyyyy_from_subfolder_for_insurance_pdf(subfolder)
    if not d:
        d = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d%m%Y")
    m = (mob or "").strip()
    if m:
        return f"{m}_Insurance_{d}.pdf"
    return f"Insurance_{d}_{safe_policy_suffix}.pdf"


def _ins_log(
    ocr_output_dir: Path | None, subfolder: str | None, level: str, message: str
) -> None:
    append_playwright_insurance_line(ocr_output_dir, subfolder, level, f"{_PREFIX}: {message}")
    if level == "ERROR":
        logger.error("%s: %s", _PREFIX, message)
    else:
        logger.info("%s: %s", _PREFIX, message)


def _longest_prefix_product_label(insurer: str, option_labels: list[str]) -> str | None:
    ins = _norm_ws(insurer)
    if not ins:
        return None
    ins_l = ins.lower()
    best: str | None = None
    best_len = -1
    for raw in option_labels:
        lab = _norm_ws(raw)
        if not lab or lab.lower() in (
            "select", "select one", "-", "--select--", "choose",
        ):
            continue
        if ins_l.startswith(lab.lower()) and len(lab) > best_len:
            best_len = len(lab)
            best = lab
    return best


def _pdf_file_looks_valid(path: Path, *, min_bytes: int = 800) -> bool:
    try:
        st = path.stat()
        if st.st_size < min_bytes:  # avoid empty chrome error pages
            return False
        with path.open("rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _misp_pdf_clip_candidates(target: Any) -> list[dict[str, float] | None]:
    """
    Prefer **embed/object** and **policy-like iframes** (server PDF or cert viewer), not the MISP
    **PrintPolicyDetails** shell (sidebar + “print” chrome). ``#content-bar`` is a last resort before
    full page.
    """
    cands: list[dict[str, float]] = []
    scored: list[tuple[float, dict[str, float], int]] = []  # area, rect, score boost

    def _append_rect(b: dict[str, float] | None, score: int) -> None:
        if not b or b.get("width", 0) * b.get("height", 0) < 1_200:
            return
        area = b["width"] * b["height"]
        d = {"x": b["x"], "y": b["y"], "width": b["width"], "height": b["height"]}
        scored.append((area, d, score))

    # 1) Native PDF / cert hosts (highest value)
    for kind in ("embed:visible", "object:visible"):
        try:
            loc = target.locator(kind)
            n = min(loc.count(), 8)
            for i in range(n):
                try:
                    b = loc.nth(i).bounding_box(timeout=1500)
                except Exception:
                    b = None
                _append_rect(b, 1_000_000)
        except Exception:
            pass

    # 2) iframes — prefer policy/PDF-like src; allow smaller size than the old 6_000 lower bound
    try:
        loc = target.locator("iframe:visible")
        n = min(loc.count(), 20)
        for i in range(n):
            try:
                nloc = loc.nth(i)
                st = (nloc.get_attribute("id") or "") + (nloc.get_attribute("name") or "")
                h = (nloc.get_attribute("src") or "")
                stlow, hlow = st.lower(), h.lower()
                if "calendar" in stlow or hlow.startswith("javascript:"):
                    continue
                boost = 0
                if re.search(
                    r"pdf|print|policy|cert|document|aspx|cp_|report|viewer", hlow, re.I
                ):
                    boost = 5_000_000
                elif hlow and "about:blank" not in hlow:
                    boost = 100_000
                b = nloc.bounding_box(timeout=1500)
            except Exception:
                b = None
            if not b:
                continue
            w, h_ = b.get("width", 0), b.get("height", 0)
            if w * h_ < 2_000 and boost < 1_000_000:
                continue
            if w * h_ < 6_000 and boost < 10_000:
                continue
            _append_rect(b, boost)
    except Exception:
        pass

    scored.sort(key=lambda t: -(t[0] + t[2]))
    for _a, d, _s in scored[:6]:
        cands.append(d)

    # 3) Inner content (not full MISP shell if possible) — add before #content-bar
    for sel in (
        "div#content-bar .container",
        "div#content-bar main",
        "#content-bar [class*='col-']",
        "main#content",
        "main[role=main]",
        "article",
        "#ctl00_ContentPlaceHolder1",
        "#printarea",
        "#divPrint",
        "form#aspnetForm",
        "form#form1",
        "#form1",
    ):
        try:
            b = target.locator(sel).first.bounding_box(timeout=1000)
        except Exception:
            b = None
        if b and b.get("width", 0) * b.get("height", 0) > 2_500:
            cands.append(
                {"x": b["x"], "y": b["y"], "width": b["width"], "height": b["height"]}
            )

    for sel in ("#content-bar",):
        try:
            b = target.locator(sel).first.bounding_box(timeout=1000)
        except Exception:
            b = None
        if b and b.get("width", 0) * b.get("height", 0) > 2_500:
            cands.append(
                {"x": b["x"], "y": b["y"], "width": b["width"], "height": b["height"]}
            )

    seen: set[tuple] = set()
    uniq: list[dict[str, float]] = []
    for c in cands:
        t = (round(c["x"]), round(c["y"]), round(c["width"]), round(c["height"]))
        if t in seen:
            continue
        seen.add(t)
        uniq.append(c)

    series: list[dict[str, float] | None] = uniq[:7] if uniq else []
    if None not in series:
        series.append(None)
    if not series:
        series = [None]
    return series


def _misp_pdf_debug_env() -> bool:
    v = (os.environ.get("HERO_MISP_PDF_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on", "debug", "frame", "html")


def _maybe_misp_frame_html_debug(
    labeled_pages: list[tuple[str, Any]],
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """
    When **HERO_MISP_PDF_DEBUG** is set, write one text file: frame tree, selector hits, truncated body HTML
    per frame. Not a substitute for in-browser devtools, but enough to see nested PDF/applet/iframe policy.
    """
    if not _misp_pdf_debug_env() or not ocr_output_dir or not (subfolder or "").strip():
        return
    out_dir = Path(ocr_output_dir).resolve() / safe_subfolder_name(subfolder)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    ts = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"misp_pdf_frame_debug_{ts}.txt"
    lines: list[str] = []
    for label, pg in labeled_pages:
        if pg is None:
            continue
        try:
            if pg.is_closed():
                continue
        except Exception:
            continue
        lines.append("=" * 72)
        lines.append(f"CONTEXT: {label}")
        lines.append("=" * 72)
        try:
            lines.append(f"page.url: {(pg.url or '')[:500]}")
        except Exception as e:
            lines.append(f"page.url: (err {e!s})")
        try:
            lines.append(f"page.title: {(pg.title() or '')[:240]}")
        except Exception as e:
            lines.append(f"page.title: (err {e!s})")
        try:
            frames = pg.frames
        except Exception as e:
            lines.append(f"frames: (err {e!s})")
            lines.append("")
            continue
        for fi, fr in enumerate(frames):
            try:
                if fr.is_detached():
                    lines.append(f"  [{fi}] (detached)")
                    continue
            except Exception:
                pass
            try:
                fu = (fr.url or "")[:500]
            except Exception:
                fu = "?"
            try:
                fn = (getattr(fr, "name", None) or "")[:200]
            except Exception:
                fn = ""
            lines.append(f"  frame[{fi}] name={fn!r} url={fu!r}")
            for sel in (
                f"#{_CPH1}",
                "#printarea",
                "#divPrint",
                "iframe",
                "object",
                "embed",
            ):
                try:
                    n = fr.locator(sel).count()
                    lines.append(f"    {sel!r} count={n}")
                except Exception as ex:
                    lines.append(f"    {sel!r} err={ex!s}")
            try:
                out = fr.evaluate(
                    """() => {
                    const b = document.body;
                    if (!b) return { snippet: '', total: 0 };
                    const h = b.innerHTML || '';
                    return { snippet: h.slice(0, 8000), total: h.length };
                }"""
                )
                if isinstance(out, dict):
                    total = int(out.get("total") or 0)
                    snip = str(out.get("snippet") or "")
                    lines.append(
                        f"    body.innerHTML: total length={total} (first 8000 chars follow)"
                    )
                    lines.append(snip)
            except Exception as ex:
                lines.append(f"    body.innerHTML: err {ex!s}")
            lines.append("")
    try:
        out_path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except OSError as exc:
        if ocr_output_dir and subfolder:
            _ins_log(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"HERO_MISP_PDF_DEBUG: could not write {out_path!s}: {exc!s}",
            )
        return
    _ins_log(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"HERO_MISP_PDF_DEBUG: frame/snapshot text -> {out_path!s}",
    )


def _try_playwright_page_pdf(
    target: Any,
    out_path: Path,
    *,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    tag: str,
) -> bool:
    """
    Save the policy view using Playwright ``Page.pdf``: ``emulate_media(print)`` to apply @media print
    (often hides nav/buttons), then try **clipped** regions (iframes + ContentPlaceHolder) so we do not
    get only the “outer applet with Print buttons” shell. **Edge/Chrome** via the same ``chromium`` channel.
    """
    p = out_path
    p.parent.mkdir(parents=True, exist_ok=True)
    abspath = str(p.resolve())
    margin = {"top": "6px", "right": "6px", "bottom": "6px", "left": "6px"}
    _media_toggled = False
    try:
        try:
            target.emulate_media(media="print")
            _media_toggled = True
        except Exception as emx:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"emulate_media(print) skipped: {emx!s}")
        for clip in _misp_pdf_clip_candidates(target):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
            kw: dict = {
                "path": abspath,
                "print_background": True,
                "format": "A4",
                "margin": margin,
            }
            if clip is not None:
                kw["clip"] = clip
            clip_lbl = "full" if clip is None else f"clip w={int(clip.get('width', 0))} h={int(clip.get('height', 0))}"
            try:
                target.pdf(**kw)
            except Exception as ex:
                _ins_log(ocr_output_dir, subfolder, "NOTE", f"page.pdf ({tag}) {clip_lbl}: {ex!s}")
                continue
            if not _pdf_file_looks_valid(p):
                _ins_log(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    f"page.pdf ({tag}) {clip_lbl}: invalid/too small — {p!s}",
                )
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            _ins_log(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"page.pdf ({tag}) {clip_lbl} -> {p!s} (print media + region)",
            )
            return True
    finally:
        if _media_toggled:
            try:
                target.emulate_media(media="screen")
            except Exception:
                pass
    return False


def _unique_out_path(dest: Path, out_name: str) -> Path:
    p = (dest / out_name).resolve()
    if p.is_file():
        for i in range(1, 200):
            alt = p.parent / f"{p.stem}_{i}{p.suffix}"
            if not alt.is_file():
                return alt
    return p


def _run_second_print_and_collect_downloads(
    page: Page,
    p2: Any,
    tmo: int,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> list:
    _raw_ms = int(INSURANCE_ACTION_TIMEOUT_MS or 12_000)
    _download_wait_sec = float(min(300, max(90, max(1, _raw_ms // 1000) * 3)))
    _collected: list = []

    def _on_download(d: Any) -> None:
        _collected.append(d)

    page.context.on("download", _on_download)
    try:
        try:
            p2.click(timeout=tmo, force=True)
        except Exception:
            p2.click(timeout=tmo, force=True)
        _deadline = time.time() + _download_wait_sec
        while time.time() < _deadline:
            page.wait_for_timeout(120)
            if len(_collected) >= 1:
                page.wait_for_timeout(850)
                break
        else:
            if not _collected:
                try:
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
    finally:
        try:
            page.context.remove_listener("download", _on_download)
        except Exception:
            pass
    return _collected


def _score_insurance_download_candidate(dl: Any) -> int:
    """Prefer real PDFs (same idea as DMS run-report download scoring)."""
    try:
        s = (dl.suggested_filename or "").lower()
    except Exception:
        s = ""
    if s.endswith(".pdf"):
        return 100
    # Stray temp names (DMS / portals sometimes)
    if s and re.match(r"^[0-9a-f-]{20,}([.][^/]*)?$", s, re.I):
        return 1
    return 10


def _collect_option_labels(sel: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        n = sel.locator("option").count()
    except Exception:
        n = 0
    for i in range(n):
        opt = sel.locator("option").nth(i)
        try:
            val = (opt.get_attribute("value") or "").strip()
            txt = (opt.inner_text() or "").strip()
            if not txt and val:
                txt = val
            tlow = txt.lower()
            if tlow in ("--select--",) or (val in ("0",) and tlow in ("--select--", "select", "")):
                continue
            if not txt or txt.lower() in ("", "select", "select one", "-", "choose"):
                continue
            out.append((val, txt))
        except Exception:
            continue
    return out


def _misp_form_roots(page: Page) -> list[Any]:
    from app.services.fill_hero_insurance_service import _hero_misp_page_and_frame_roots

    return _hero_misp_page_and_frame_roots(page, purpose="proposal")


def _misp_nav_roots(page: Page) -> list[Any]:
    from app.services.fill_hero_insurance_service import _hero_misp_page_and_frame_roots

    return _hero_misp_page_and_frame_roots(page, purpose="nav")


def _misp_open_all_print_policy(
    page: Page, *, tmo: int, ocr_output_dir: Path | None, subfolder: str | None
) -> None:
    direct = (os.environ.get("MISP_GOTO_ALL_PRINT_POLICY") or "").strip()
    if direct:
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"MISP_GOTO_ALL_PRINT_POLICY: {direct[:100]}")
        try:
            page.goto(direct, timeout=min(60000, tmo * 4), wait_until="domcontentloaded")
        except Exception as exc:
            _ins_log(ocr_output_dir, subfolder, "ERROR", f"goto MISP_GOTO_ALL_PRINT_POLICY: {exc!s}")
        try:
            page.wait_for_url(re.compile(r"AllPrintPolicy|printpolicy", re.I), timeout=30_000)
        except Exception:
            page.wait_for_timeout(2000)
        return

    from app.services.fill_hero_insurance_service import _expand_misp_policy_issuance_nav_if_collapsed

    _expand_misp_policy_issuance_nav_if_collapsed(page, timeout_ms=tmo)
    for root in _misp_nav_roots(page):
        try:
            ppl = root.get_by_text("Print Policy", exact=True)
            if ppl.count() > 0 and ppl.first.is_visible(timeout=2000):
                ppl.first.click(timeout=tmo, force=True)
                _ins_log(ocr_output_dir, subfolder, "NOTE", "clicked MISP sidebar Print Policy")
                try:
                    page.wait_for_url(re.compile(r"AllPrintPolicy|PrintPolicy", re.I), timeout=45_000)
                except Exception:
                    page.wait_for_timeout(2500)
                return
        except Exception:
            continue
        try:
            lnk = root.get_by_role("link", name=re.compile(r"Print\s*Policy", re.I))
            if lnk.count() > 0 and lnk.first.is_visible(timeout=2000):
                lnk.first.click(timeout=tmo, force=True)
                _ins_log(ocr_output_dir, subfolder, "NOTE", "clicked MISP link Print Policy (role=link)")
                try:
                    page.wait_for_url(re.compile(r"AllPrintPolicy|PrintPolicy", re.I), timeout=45_000)
                except Exception:
                    page.wait_for_timeout(2500)
                return
        except Exception:
            continue

    raise RuntimeError("MISP: could not find Print Policy (set MISP_GOTO_ALL_PRINT_POLICY to AllPrintPolicy URL)")


def _misp_policy_schedule_url_from_context(page: Page, popup: Page | None) -> str | None:
    """
    Build ``.../Policy/PolicySchedule.aspx[? same query]`` from the MISP app ``/Policy/`` page URL, using
    the best available query (often ``PID=``) from the main or cert pop-up page.
    """
    candidates: list[str] = []
    for p in (popup, page):
        if p is None:
            continue
        try:
            if p.is_closed():
                continue
        except Exception:
            continue
        try:
            u = (p.url or "").strip()
        except Exception:
            u = ""
        if u and "heroinsurance" in u.lower() and "/Policy/" in u and "PolicySchedule" not in u:
            candidates.append(u)
    if not candidates:
        try:
            u0 = (page.url or "").strip()
        except Exception:
            u0 = ""
        if u0:
            candidates.append(u0)
    u_src = (candidates[0] or "") if candidates else ""
    if not u_src:
        return None
    if "PolicySchedule" in u_src:
        return None
    sub = re.sub(
        r"^(.+/Policy/)(?:[^/]+\.aspx)(\?[^#]*)?(#.*)?$",
        r"\1PolicySchedule.aspx\2\3",
        u_src,
        count=1,
        flags=re.I,
    )
    if sub and sub != u_src and "PolicySchedule.aspx" in sub:
        return sub
    return None


def _misp_poll_new_policy_schedule_page(
    context: Any,
    page: Page,
    popup: Page | None,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    *,
    max_sec: float = 10.0,
) -> Any | None:
    """
    The AllPrintPolicy cert view often returns HTML that includes
    ``window.open('.../Policy/PolicySchedule.aspx', '…', '…')`` (see ``misp_pdf_frame_debug_*.txt``). That
    opens a **new** top-level page: same origin/session as the cert flow, not a generic one-size URL. Poll
    the browser **context** for a page whose URL contains **PolicySchedule**.
    """
    deadline = time.time() + max(2.0, min(45.0, max_sec))
    waker = page
    if popup is not None and not popup.is_closed():
        waker = popup
    while time.time() < deadline:
        try:
            for p in context.pages:
                if p is None:
                    continue
                try:
                    if p.is_closed():
                        continue
                except Exception:
                    continue
                u = (p.url or "")
                ulow = u.lower()
                if "policyschedule" not in ulow:
                    continue
                if p in (page, popup):
                    which = "main" if p is page else "cert"
                    _ins_log(
                        ocr_output_dir,
                        subfolder,
                        "NOTE",
                        f"MISP: {which} tab navigated to PolicySchedule — {u[:220]}",
                    )
                else:
                    _ins_log(
                        ocr_output_dir,
                        subfolder,
                        "NOTE",
                        f"MISP: PolicySchedule window (window.open / new tab) — {u[:220]}",
                    )
                return p
        except Exception:
            pass
        try:
            waker.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    return None


def _misp_goto_policy_schedule_in_sidebar(
    page: Page, *, tmo: int, ocr_output_dir: Path | None, subfolder: str | None
) -> bool:
    _patt = re.compile(r"Policy\s*Schedule", re.I)
    for root in _misp_nav_roots(page):
        for sel in (
            lambda r: r.get_by_role("link", name=_patt),
            lambda r: r.get_by_text(_patt),
        ):
            try:
                t = sel(root)
            except Exception:
                continue
            if t is None or t.count() < 1:
                continue
            for i in range(min(t.count(), 6)):
                el = t.nth(i)
                if not el.is_visible(timeout=1500):
                    continue
                try:
                    el.click(timeout=tmo, force=True)
                except Exception:
                    continue
                _ins_log(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "MISP: clicked Policy Schedule in sidebar (navigation)",
                )
                try:
                    page.wait_for_url(
                        re.compile(r"PolicySchedule|policyschedule", re.I), timeout=45_000
                    )
                except Exception:
                    try:
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
                return True
    return False


def _misp_goto_policy_schedule_for_save(
    page: Page,
    popup: Page | None,
    tmo: int,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> bool:
    """
    After the cert/Print steps, go to **PolicySchedule.aspx** (full policy/schedule as in the printed
    view) so :func:`_try_playwright_page_pdf` or download can use that document, not only AllPrint/PrintDetails.

    **MISP_GOTO_POLICY_SCHEDULE** — if set, full URL to go to. Otherwise: derive from context URLs, or
    click **Policy Schedule** in the nav. Callers run :func:`_misp_poll_new_policy_schedule_page` first when a
    ``PolicySchedule`` tab is opened without an explicit query (session-bound, as in the AllPrintPolicy HTML).
    """
    direct = (os.environ.get("MISP_GOTO_POLICY_SCHEDULE") or "").strip()
    if direct:
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"MISP_GOTO_POLICY_SCHEDULE: {direct[:100]}")
        try:
            page.bring_to_front()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            page.goto(direct, timeout=min(60000, tmo * 4), wait_until="domcontentloaded")
        except Exception as exc:
            _ins_log(ocr_output_dir, subfolder, "ERROR", f"goto MISP_GOTO_POLICY_SCHEDULE: {exc!s}")
            return False
        _ins_log(ocr_output_dir, subfolder, "NOTE", "MISP: on PolicySchedule (MISP_GOTO_* URL)")
        return True

    derived = _misp_policy_schedule_url_from_context(page, popup)
    if derived:
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"MISP: goto PolicySchedule derived URL: {derived[:200]}")
        try:
            page.bring_to_front()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            page.goto(derived, timeout=min(60000, tmo * 4), wait_until="domcontentloaded")
        except Exception as exc:
            _ins_log(ocr_output_dir, subfolder, "ERROR", f"PolicySchedule goto (derived): {exc!s}")
        else:
            try:
                page.wait_for_url(
                    re.compile(r"PolicySchedule|policyschedule", re.I), timeout=30_000
                )
            except Exception:
                try:
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
            _ins_log(ocr_output_dir, subfolder, "NOTE", "MISP: on PolicySchedule (derived URL)")
            return True
    if _misp_goto_policy_schedule_in_sidebar(
        page, tmo=tmo, ocr_output_dir=ocr_output_dir, subfolder=subfolder
    ):
        u = ""
        try:
            u = (page.url or "")[:220]
        except Exception:
            pass
        if "policyschedule" in u.lower():
            return True
        _ins_log(ocr_output_dir, subfolder, "NOTE", "MISP: sidebar PolicySchedule click but URL not matched")
    return False


def _find_product_row_select(root) -> Any:
    for spec in (
        _SEL_PRODUCT,
        f"select[name='{_NAME_PRODUCT}']",
    ):
        try:
            s = root.locator(spec)
            if s.count() > 0 and s.first.is_visible(timeout=1000):
                return s.first
        except Exception:
            continue
    for css in (
        "select[title*='Product' i]",
        "select[name*='Product' i]",
        "select[id*='Product' i]",
    ):
        try:
            s = root.locator(css)
            if s.count() > 0 and s.first.is_visible(timeout=200):
                return s.first
        except Exception:
            continue
    return None


def _find_policy_no_input(root) -> Any:
    for spec in (
        _INP_POLICY_NO,
        f"input[name='{_NAME_POLICY_NO}']",
    ):
        try:
            loc = root.locator(spec)
            if loc.count() > 0 and loc.first.is_visible(timeout=1000):
                return loc.first
        except Exception:
            continue
    for css in (
        "input[title*='Policy' i][title*='No' i]",
        "input[name*='Policy' i][name*='No' i]",
    ):
        try:
            loc = root.locator(css)
            if loc.count() > 0 and loc.first.is_visible(timeout=300):
                return loc.first
        except Exception:
            continue
    return None


def _find_go_button(root) -> Any:
    for spec in (
        _BTN_GO,
        f"input[name='{_NAME_GO}']",
    ):
        try:
            b = root.locator(spec)
            if b.count() > 0 and b.first.is_visible(timeout=1000):
                return b.first
        except Exception:
            continue
    return None


def _find_print_policy_row_button(root) -> Any:
    for css in _CSS_PRINT_IN_GRID:
        try:
            loc = root.locator(css)
            n = loc.count()
            for i in range(n):
                el = loc.nth(i)
                if el.is_visible(timeout=800):
                    return el
        except Exception:
            continue
    return None


def _find_print_policy_certificates_applet_print(popup: Page) -> Any:
    for sel in (
        "input[id*='btnAppRej']",
        "input[name*='btnAppRej']",
        "#gvPrintPolicy input.button1[value=Print]",
        "table#gvPrintPolicy input[value=Print]",
        'input[type="submit"][value="Print"]',
        'input[type="button"][value="Print"]',
        'input[value="Print"]',
    ):
        try:
            loc = popup.locator(sel)
            n = min(loc.count(), 10)
            for i in range(n):
                el = loc.nth(i)
                if el.is_visible(timeout=1000):
                    return el
        except Exception:
            continue
    try:
        b = popup.get_by_role("button", name=re.compile(r"^Print$", re.I))
        if b.count() > 0 and b.first.is_visible(timeout=2000):
            return b.first
    except Exception:
        pass
    return None


def _misp_cert_download_first_wait_sec() -> float:
    try:
        v = float((os.environ.get("HERO_MISP_CERT_DL_WAIT_SEC") or "35").strip())
    except ValueError:
        v = 35.0
    return max(8.0, min(120.0, v))


def _misp_context_extra_pages_for_cert(
    context: Any, page: Page, popup: Page | None
) -> list[tuple[str, Any]]:
    """
    New windows (often the real certificate, or a PDF host) that are not the search tab / AllPrintPolicy.
    """
    out: list[tuple[str, Any]] = []
    seen: set[int] = set()
    for p in context.pages:
        if p is page or p is popup:
            continue
        try:
            if p.is_closed():
                continue
        except Exception:
            continue
        k = id(p)
        if k in seen:
            continue
        seen.add(k)
        try:
            u = (p.url or "").strip()
        except Exception:
            u = ""
        if u.lower().startswith(("about:blank", "chrome://", "edge://", "devtools:")):
            continue
        out.append((f"misp_context_extra_{len(out)}", p))
    return out


def _misp_pages_for_print_to_pdf_in_order(
    page: Page, ap_target: Any, context: Any, popup: Page | None
) -> list[tuple[str, Any]]:
    """
    Order **Playwright** ``page.pdf`` targets. After the second **Print**, the true certificate is often
    a **file download** (not this list), a **new** tab, or the **AllPrintPolicy** / cert view in the
    pop-up — not a full **PrintPolicyDetails** shell. Extra context pages and the cert pop-up go first;
    the main insurance tab (sidebar + #content-bar) is last so we do not default to the “print page” U.I.
    """
    out: list[tuple[str, Any]] = []
    seen: set[int] = set()

    def _add(label: str, p: Any) -> None:
        if p is None:
            return
        try:
            if p.is_closed():
                return
        except Exception:
            return
        k = id(p)
        if k in seen:
            return
        seen.add(k)
        out.append((label, p))

    for tag, p in _misp_context_extra_pages_for_cert(context, page, popup):
        _add(tag, p)
    if ap_target is not page:
        # Pop-up may navigate to a certificate view; try it before the heavy PrintPolicyDetails tab.
        _add("misp_all_print_policy_popup", ap_target)
        _add("misp_insurance_main", page)
    else:
        _add("misp_page", page)
    return out


def run_hero_insure_reports(
    page: Page,
    *,
    insurer: str,
    policy_num: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
) -> dict[str, Any]:
    """
    On the MISP **Insurance** tab: **Policy Issuance** → **Print Policy** → **Search** (Product, Policy
    No., **Go**) → first **Print** in the search grid (opens **Print Policy Certificates**), then the
    second **Print** in that window (``gvPrintPolicy``/``btnAppRej``) to load the certificate, **then** save
    (never ``page.pdf`` the AllPrintPolicy grid before that click, or the PDF is only the applet). **Preferred:** :meth:`Page.pdf` (Edge/Chrome). **Fallback:**
    file download on the same ``context`` (DMS-style).
    # After save, a background :func:`schedule_misp_hero_post_pdf` (always: system print UI on save; not env-gated).
    Capture method: ``HERO_MISP_PDF_STRATEGY=auto|playwright|download`` (default ``auto`` = PDF first, then download).

    Returns ``{"ok": bool, "error": str|None, "pdf_path": str|None}``.
    """
    tmo = _misp_tmo()
    out: dict[str, Any] = {"ok": False, "error": None, "pdf_path": None}
    pn = (policy_num or "").strip()
    if not pn:
        _ins_log(ocr_output_dir, subfolder, "NOTE", "skipped: policy_num empty")
        return {**out, "error": "skipped: policy_num empty"}
    if page is None or page.is_closed():
        return {**out, "error": "MISP page is missing or closed"}

    dest = Path(uploads_dir).resolve()
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {**out, "error": f"uploads_dir not usable: {exc!s}"}

    popup: Page | None = None
    try:
        page.set_default_timeout(tmo)
        _misp_open_all_print_policy(
            page, tmo=tmo, ocr_output_dir=ocr_output_dir, subfolder=subfolder
        )
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"on Print Policy URL: {page.url[:200]!r}")

        product_sel = None
        for root in _misp_form_roots(page):
            product_sel = _find_product_row_select(root)
            if product_sel is not None:
                break
        if product_sel is None:
            raise RuntimeError("Product <select> not found (ddlProduct)")

        opts = _collect_option_labels(product_sel)
        labels = [o[1] for o in opts]
        pick = _longest_prefix_product_label(insurer, labels)
        if not pick:
            raise RuntimeError(
                f"No Product option is a prefix of insurer {insurer!r} (options: {labels[:24]!r})"
            )
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"Product: {pick!r} (insurer={insurer!r})")
        product_sel.select_option(label=pick, timeout=tmo)

        pol_in = None
        for root in _misp_form_roots(page):
            pol_in = _find_policy_no_input(root)
            if pol_in is not None:
                break
        if pol_in is None:
            raise RuntimeError("Policy No. field not found (txtPolicyNo)")

        pol_in.fill("", timeout=2000)
        pol_in.fill(pn, timeout=2000)
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"Policy No. filled: {pn!r}")

        go_loc = None
        for root in _misp_form_roots(page):
            go_loc = _find_go_button(root)
            if go_loc is not None:
                break
        if go_loc is None:
            g = page.locator('input[type="submit"][value="Go" i]').first
            if g.count() > 0 and g.is_visible(timeout=1000):
                go_loc = g
        if go_loc is None:
            raise RuntimeError("Go button not found (btnGO)")
        go_loc.click(timeout=tmo, force=True)
        _ins_log(ocr_output_dir, subfolder, "NOTE", "Go clicked")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        print_grid = None
        for root in _misp_form_roots(page):
            print_grid = _find_print_policy_row_button(root)
            if print_grid is not None:
                break
        if print_grid is None:
            _pt = re.compile(r"^Print$", re.I)
            for root in _misp_form_roots(page):
                t = root.locator("table").filter(has_text=re.compile(r"Print\s*Policy", re.I))
                if t.count() == 0:
                    continue
                c = t.first.locator("tbody a, tbody input, tbody button").filter(has_text=_pt).first
                if c.count() > 0 and c.is_visible(timeout=2000):
                    print_grid = c
                    break
        if print_grid is None:
            raise RuntimeError("Grid Print (btnPrintPolicy) not found after Go")

        _ins_log(ocr_output_dir, subfolder, "NOTE", "clicking first Print (search grid) — opens Print Policy Certificates")
        try:
            with page.context.expect_page(timeout=30_000) as pi:
                print_grid.click(timeout=tmo, force=True)
            popup = pi.value
        except PlaywrightTimeout:
            popup = None
            _ins_log(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "no new top-level window; applet may be on the same MISP page (or overlay)",
            )

        if popup is not None:
            try:
                popup.set_default_timeout(tmo)
            except Exception:
                pass
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
            u_pop = (popup.url or "")[:200]
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"Print Policy Certificates window: {u_pop!r}")

        ap_target = popup if popup is not None else page
        p2 = _find_print_policy_certificates_applet_print(ap_target)
        if p2 is None and ap_target is not page:
            p2 = _find_print_policy_certificates_applet_print(page)
        if p2 is None:
            raise RuntimeError("Second Print not found in Print Policy Certificates (popup or main)")

        mob = _mobile_prefix_from_subfolder(subfolder)
        safe_pn = re.sub(r"[^\w\-.]+", "_", pn)[:80]
        out_name = _insurance_pdf_out_name(mob, safe_pn, subfolder)
        out_path = _unique_out_path(dest, out_name)

        raw = (os.environ.get("HERO_MISP_PDF_STRATEGY") or "auto").strip().lower()
        if raw in ("", "auto", "playwright_first"):
            strategy = "auto"
        elif raw in ("download", "dl", "browser", "file"):
            strategy = "download"
        elif raw in ("playwright", "pdf", "print_to_pdf", "chromium", "edge"):
            strategy = "playwright"
        else:
            strategy = "auto"
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"unknown HERO_MISP_PDF_STRATEGY={raw!r}, using auto")

        def _commit_and_return() -> dict[str, Any]:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"PDF saved: {out_path!s}")
            # Printing moved to Add Sales "Print Forms & Queue RTO" (ordered batch). Save-only here.
            # try:
            #     schedule_misp_hero_post_pdf(out_path)
            #     _ins_log(
            #         ocr_output_dir,
            #         subfolder,
            #         "NOTE",
            #         f"post-save: system print UI (MISP): {out_path!s}",
            #     )
            # except Exception as exc:
            #     logger.warning("%s: schedule_misp_hero_post_pdf: %s", _PREFIX, exc)
            #     _ins_log(
            #         ocr_output_dir,
            #         subfolder,
            #         "NOTE",
            #         f"schedule_misp_hero_post_pdf skipped: {exc!s}",
            #     )
            return {"ok": True, "error": None, "pdf_path": str(out_path)}

        if strategy in ("auto", "playwright"):
            dls: list = []

            def _misp_on_down(d: Any) -> None:
                dls.append(d)

            _ins_log(
                ocr_output_dir,
                subfolder,
                "NOTE",
                "MISP: second Print (certificate) — file download or page.pdf (insurance main tab first, not the AllPrintPolicy grid only)",
            )
            try:
                try:
                    page.context.on("download", _misp_on_down)
                except Exception as exc:
                    _ins_log(ocr_output_dir, subfolder, "NOTE", f"download listener: {exc!s}")
                try:
                    p2.click(timeout=tmo, force=True)
                except Exception:
                    p2.click(timeout=tmo, force=True)
                t_deadline = time.time() + _misp_cert_download_first_wait_sec()
                while time.time() < t_deadline and not dls:
                    try:
                        page.wait_for_timeout(200)
                    except Exception:
                        break
                if dls:
                    try:
                        best = max(dls, key=_score_insurance_download_candidate)
                    except Exception:
                        best = dls[-1]
                    best.save_as(str(out_path))
                    if _pdf_file_looks_valid(out_path):
                        return _commit_and_return()
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                    _ins_log(
                        ocr_output_dir,
                        subfolder,
                        "NOTE",
                        "MISP: first download not a valid PDF, trying print-to-PDF on page",
                    )
            finally:
                try:
                    page.context.remove_listener("download", _misp_on_down)
                except Exception:
                    pass

            ps_tab = _misp_poll_new_policy_schedule_page(
                page.context, page, popup, ocr_output_dir, subfolder, max_sec=12.0
            )
            if ps_tab is not None and not ps_tab.is_closed():
                _ins_log(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "MISP: trying page.pdf on PolicySchedule tab (from server window.open or navigation)",
                )
                if _try_playwright_page_pdf(
                    ps_tab,
                    out_path,
                    ocr_output_dir=ocr_output_dir,
                    subfolder=subfolder,
                    tag="misp_policy_schedule_tab",
                ):
                    return _commit_and_return()

            if _misp_goto_policy_schedule_for_save(
                page, popup, tmo, ocr_output_dir, subfolder
            ):
                try:
                    if popup is not None and not popup.is_closed() and popup != page:
                        popup.close()
                except Exception:
                    pass
                popup = None
                ap_target = page
                try:
                    page.wait_for_load_state("networkidle", timeout=30_000)
                except Exception:
                    pass
                try:
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                _ins_log(
                    ocr_output_dir,
                    subfolder,
                    "NOTE",
                    "MISP: try print-to-PDF on PolicySchedule (save to uploads) before other MISP pages",
                )
                if not page.is_closed() and _try_playwright_page_pdf(
                    page,
                    out_path,
                    ocr_output_dir=ocr_output_dir,
                    subfolder=subfolder,
                    tag="misp_policy_schedule",
                ):
                    return _commit_and_return()

            try:
                if popup is not None:
                    popup.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(2000)
            except Exception:
                pass
            for _settle in range(4):
                n_emb = 0
                for tp2 in (page, ap_target) if ap_target is not page else (page,):
                    if tp2 is None or tp2.is_closed():
                        continue
                    try:
                        n_emb = max(
                            n_emb,
                            tp2.locator("embed:visible, object:visible, iframe:visible").count(),
                        )
                    except Exception:
                        pass
                if n_emb > 0:
                    break
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            _dbg: list[tuple[str, Any]] = [("misp_print_target", ap_target)]
            if ap_target is not page and page is not None and not page.is_closed():
                _dbg.append(("misp_insurance_page", page))
            for ep in _misp_context_extra_pages_for_cert(page.context, page, popup):
                _dbg.append((ep[0], ep[1]))
            _maybe_misp_frame_html_debug(_dbg, ocr_output_dir, subfolder)
            _ins_log(ocr_output_dir, subfolder, "NOTE", "try Playwright page.pdf (extra tabs + pop-up first, insurance shell last; embed/iframe clip preferred)")
            for tag, tp in _misp_pages_for_print_to_pdf_in_order(page, ap_target, page.context, popup):
                if tp.is_closed():
                    continue
                if _try_playwright_page_pdf(
                    tp,
                    out_path,
                    ocr_output_dir=ocr_output_dir,
                    subfolder=subfolder,
                    tag=tag,
                ):
                    return _commit_and_return()
            if strategy == "playwright":
                raise RuntimeError(
                    "playwright: page.pdf could not write a valid PDF (use Edge/Chrome; MISP may need the certificate visible in the window)."
                )

        if strategy in ("auto", "download"):
            _ins_log(ocr_output_dir, subfolder, "NOTE", "try browser file download (second Print, DMS-style poll)")
            _collected = _run_second_print_and_collect_downloads(
                page, p2, tmo, ocr_output_dir, subfolder
            )
            if _collected:
                if len(_collected) > 1:
                    _ins_log(
                        ocr_output_dir,
                        subfolder,
                        "NOTE",
                        f"{len(_collected)} download event(s) — best PDF (DMS-style)",
                    )
                try:
                    dl = max(_collected, key=_score_insurance_download_candidate)
                except Exception:
                    dl = _collected[-1]
                for d in _collected:
                    if d is not dl:
                        try:
                            d.cancel()
                        except Exception:
                            pass
                dl.save_as(str(out_path))
                return _commit_and_return()

        if strategy == "auto":
            _ins_log(ocr_output_dir, subfolder, "NOTE", "no download; retry page.pdf after settle (main first)")
            try:
                page.wait_for_timeout(2000)
            except Exception:
                pass
            for tag, tp in _misp_pages_for_print_to_pdf_in_order(page, ap_target, page.context, popup):
                if tp.is_closed():
                    continue
                if _try_playwright_page_pdf(
                    tp,
                    out_path,
                    ocr_output_dir=ocr_output_dir,
                    subfolder=subfolder,
                    tag=f"{tag}_late",
                ):
                    return _commit_and_return()

        raise RuntimeError(
            "Could not save policy PDF: set HERO_MISP_PDF_STRATEGY=playwright or =download, "
            "or ensure the Print Policy view shows the certificate (Edge/Chrome) before/after the second Print."
        )
    except Exception as exc:
        msg = str(exc).strip() or repr(exc)
        _ins_log(ocr_output_dir, subfolder, "ERROR", msg)
        logger.exception("%s failed", _PREFIX)
        return {**out, "error": msg}
    finally:
        if popup is not None:
            try:
                if not popup.is_closed() and popup != page:
                    popup.close()
            except Exception as exc:
                _ins_log(ocr_output_dir, subfolder, "NOTE", f"close applet window: {exc!s}")


def hero_insure_reports_service(
    *args: Any, **kwargs: Any,
) -> dict[str, Any]:
    return run_hero_insure_reports(*args, **kwargs)
