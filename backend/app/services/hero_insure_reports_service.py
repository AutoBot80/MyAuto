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
# VIN / Frame No. input selectors (fallback when policy_num is empty)
_INP_FRAME_NO = f"#{_CPH1}_txtFrameNo"
_NAME_FRAME_NO = r"ctl00$ContentPlaceHolder1$txtFrameNo"


def _misp_tmo() -> int:
    return max(3000, int(INSURANCE_ACTION_TIMEOUT_MS or 12_000))


def _suppress_window_print(page_or_popup: Any) -> None:
    """Override window.print() and other print triggers to prevent system print dialogs from MISP website."""
    try:
        page_or_popup.evaluate("""(() => {
            window.print = () => {};
            // Also block document.execCommand('print')
            const origExec = document.execCommand.bind(document);
            document.execCommand = (cmd, ...args) => {
                if (cmd && cmd.toLowerCase() === 'print') return false;
                return origExec(cmd, ...args);
            };
        })()""")
    except Exception:
        pass


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
    ins_l = ins.lower().rstrip(".")
    best: str | None = None
    best_len = -1
    for raw in option_labels:
        lab = _norm_ws(raw)
        if not lab or lab.lower() in (
            "select", "select one", "-", "--select--", "choose",
        ):
            continue
        lab_l = lab.lower().rstrip(".")
        if (ins_l.startswith(lab_l) or lab_l.startswith(ins_l)) and len(lab) > best_len:
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
    (often hides nav/buttons). Full-page PDF only (Playwright does not support ``clip`` for PDFs).
    **Edge/Chrome** via the same ``chromium`` channel.
    """
    try:
        if target.is_closed():
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"page.pdf ({tag}): page already closed")
            return False
    except Exception:
        pass
    try:
        target.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        if target.is_closed():
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"page.pdf ({tag}): page closed after load wait")
            return False
    except Exception:
        pass
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
        try:
            target.pdf(**kw)
        except Exception as ex:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"page.pdf ({tag}): {ex!s}")
            return False
        if not _pdf_file_looks_valid(p):
            _ins_log(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"page.pdf ({tag}): invalid/too small — {p!s}",
            )
            try:
                p.unlink()
            except OSError:
                pass
            return False
        _ins_log(
            ocr_output_dir,
            subfolder,
            "NOTE",
            f"page.pdf ({tag}) -> {p!s} (print media, full page)",
        )
        return True
    finally:
        if _media_toggled:
            try:
                target.emulate_media(media="screen")
            except Exception:
                pass

def _unique_out_path(dest: Path, out_name: str) -> Path:
    p = (dest / out_name).resolve()
    if p.is_file():
        for i in range(1, 200):
            alt = p.parent / f"{p.stem}_{i}{p.suffix}"
            if not alt.is_file():
                return alt
    return p


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


def _frame_dump_on_error(
    page: Page,
    reason: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """Write frame dump for debugging when navigation/controls fail."""
    try:
        from app.services.fill_hero_insurance_service import _append_hero_misp_frame_dump

        _append_hero_misp_frame_dump(
            page,
            reason=reason,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )
    except Exception as exc:
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"frame dump skipped ({reason}): {exc!s}")


def _misp_on_print_policy_search_page(page: Page) -> bool:
    try:
        u = (page.url or "").lower()
    except Exception:
        return False
    return bool(
        re.search(r"printpolicydetails\.aspx|allprintpolicy\.aspx", u)
    )


def _misp_on_print_policy_cert_page(page: Page) -> bool:
    """**PrintPolicy.aspx** certificates row (post–proposal Submit), not the search grid."""
    try:
        u = (page.url or "").lower()
    except Exception:
        return False
    if _misp_on_print_policy_search_page(page):
        return False
    return "printpolicy.aspx" in u


def _misp_navigate_to_print_policy_search(
    page: Page,
    *,
    tmo: int,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> None:
    """From **PrintPolicy.aspx** cert page, search grid, or elsewhere → **PrintPolicyDetails** / **AllPrintPolicy**."""
    if _misp_on_print_policy_cert_page(page):
        from app.services.fill_hero_insurance_service import (
            _hero_misp_goto_print_policy_details_search,
        )

        _ins_log(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "on PrintPolicy cert page — goto PrintPolicyDetails for search grid",
        )
        _hero_misp_goto_print_policy_details_search(
            page,
            timeout_ms=tmo,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
        )
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass
        return
    if not _misp_on_print_policy_search_page(page):
        _misp_open_all_print_policy(
            page, tmo=tmo, ocr_output_dir=ocr_output_dir, subfolder=subfolder
        )


def _misp_scrape_print_policy_grid(
    page: Page,
    *,
    policy_num_hint: str,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    tmo: int,
) -> dict[str, Any]:
    """After **Go** on **PrintPolicyDetails**: scrape grid only (no ``insurance_master`` INSERT)."""
    from app.services.fill_hero_insurance_service import (
        scrape_insurance_print_policy_details_grid,
    )

    hint = (policy_num_hint or "").strip()
    grid_scrape = scrape_insurance_print_policy_details_grid(
        page,
        policy_num_hint=hint or None,
        timeout_ms=tmo,
    )
    if not grid_scrape.get("policy_num") and hint:
        grid_scrape["policy_num"] = hint
    _ins_log(
        ocr_output_dir,
        subfolder,
        "NOTE",
        "print_policy_details grid scrape: "
        f"policy_num={grid_scrape.get('policy_num')!r} premium={grid_scrape.get('premium')!r}",
    )
    return grid_scrape


def _misp_insert_insurance_master_from_grid_scrape(
    *,
    grid_scrape: dict[str, Any],
    customer_id: int,
    vehicle_id: int,
    fill_values: dict | None,
    staging_payload: dict | None,
    staging_id: str | None,
    dealer_id: int | None,
    ocr_output_dir: Path | None,
    subfolder: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """``insurance_master`` INSERT after successful PDF using prior grid scrape."""
    from app.repositories.add_sales_staging import fetch_staging_payload
    from app.services.add_sales_commit_service import insert_insurance_master_after_gi
    from app.services.add_sales_staging_state_service import (
        mark_staging_insurance_state,
        persist_staging_insurance_main_fields,
    )

    if not grid_scrape.get("policy_num"):
        return "print_policy_details: policy_num missing after grid scrape", grid_scrape

    sid = (staging_id or "").strip()
    did = int(dealer_id) if dealer_id is not None else None
    if sid and did is not None:
        persist_staging_insurance_main_fields(
            sid,
            did,
            policy_num=str(grid_scrape.get("policy_num") or "").strip() or None,
            policy_from=str(grid_scrape.get("policy_from") or "").strip() or None,
            policy_to=str(grid_scrape.get("policy_to") or "").strip() or None,
            premium=grid_scrape.get("premium"),
            idv=grid_scrape.get("idv"),
        )
        fresh = fetch_staging_payload(sid, did)
        if fresh is not None:
            staging_payload = fresh

    try:
        insert_insurance_master_after_gi(
            int(customer_id),
            int(vehicle_id),
            fill_values=fill_values or {},
            staging_payload=staging_payload,
            preview_scrape=grid_scrape,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            staging_id=staging_id,
            dealer_id=dealer_id,
        )
    except ValueError as exc:
        msg = str(exc).strip()
        if "already recorded" in msg.lower():
            _ins_log(
                ocr_output_dir,
                subfolder,
                "NOTE",
                f"insurance_master INSERT skipped (existing Main row): {msg}",
            )
        else:
            _ins_log(
                ocr_output_dir,
                subfolder,
                "ERROR",
                f"insurance_master insert failed: {msg}",
            )
            return msg, grid_scrape
    except Exception as exc:
        _ins_log(
            ocr_output_dir,
            subfolder,
            "ERROR",
            f"insurance_master insert failed: {exc!s}",
        )
        return f"insurance_master insert failed: {exc!s}", grid_scrape
    else:
        _ins_log(ocr_output_dir, subfolder, "NOTE", "insurance_master INSERT ok (after PDF)")

    if sid and did is not None:
        mark_staging_insurance_state(sid, did, 3)
        _ins_log(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "staging: insurance_state=3 (GI complete — PDF + insurance_master)",
        )
    return None, grid_scrape


def _misp_insurance_master_commit_from_grid(
    page: Page,
    *,
    insurer: str,
    policy_num_hint: str,
    customer_id: int,
    vehicle_id: int,
    fill_values: dict | None,
    staging_payload: dict | None,
    staging_id: str | None,
    dealer_id: int | None,
    ocr_output_dir: Path | None,
    subfolder: str | None,
    tmo: int,
) -> tuple[str | None, dict[str, Any]]:
    """After **Go** on **PrintPolicyDetails**: scrape grid → ``insurance_master`` INSERT (legacy one-step)."""
    _ = insurer
    grid_scrape = _misp_scrape_print_policy_grid(
        page,
        policy_num_hint=policy_num_hint,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
        tmo=tmo,
    )
    return _misp_insert_insurance_master_from_grid_scrape(
        grid_scrape=grid_scrape,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        fill_values=fill_values,
        staging_payload=staging_payload,
        staging_id=staging_id,
        dealer_id=dealer_id,
        ocr_output_dir=ocr_output_dir,
        subfolder=subfolder,
    )


def _misp_open_all_print_policy(
    page: Page, *, tmo: int, ocr_output_dir: Path | None, subfolder: str | None
) -> None:
    if _misp_on_print_policy_search_page(page):
        _ins_log(
            ocr_output_dir,
            subfolder,
            "NOTE",
            f"already on print policy search page — skip sidebar nav ({(page.url or '')[:120]!r})",
        )
        return
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


def _find_vin_input(root) -> Any:
    """Find VIN / Frame No. input field on PrintPolicyDetails page."""
    for spec in (
        _INP_FRAME_NO,
        f"input[name='{_NAME_FRAME_NO}']",
    ):
        try:
            loc = root.locator(spec)
            if loc.count() > 0 and loc.first.is_visible(timeout=1000):
                return loc.first
        except Exception:
            continue
    for css in (
        "input[id*='FrameNo' i]",
        "input[id*='Frame' i]",
        "input[title*='Frame' i]",
        "input[name*='Frame' i]",
        "input[title*='Chassis' i]",
        "input[title*='VIN' i]",
        "input[name*='Chassis' i]",
        "input[name*='VIN' i]",
        "input[id*='Chassis' i]",
        "input[id*='VIN' i]",
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


def _misp_direct_url_pdf_approach(
    *,
    page: Page,
    popup: Any,
    dest: Path,
    pn: str,
    subfolder: str | None,
    ocr_output_dir: Path | None,
    tmo: int,
) -> dict[str, Any]:
    """
    DIRECT URL APPROACH: Skip second Print button entirely.
    
    1. Extract PID/query from popup URL
    2. Close popup immediately (no interaction with Print buttons inside)
    3. Navigate directly to PolicySchedule.aspx with the extracted parameters
    4. Use page.pdf() to capture the certificate
    
    Returns: {"ok": True/False, "error": str|None, "pdf_path": str|None}
    """
    import urllib.parse
    
    out: dict[str, Any] = {"ok": False, "error": None, "pdf_path": None}
    
    try:
        # Step 1: Extract URL info from popup
        popup_url = ""
        try:
            popup_url = popup.url or ""
        except Exception:
            pass
        
        if not popup_url:
            return {**out, "error": "direct_url: popup URL is empty"}
        
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"direct_url: popup URL = {popup_url[:300]!r}")
        
        # Step 2: Close popup immediately - we don't need it anymore
        try:
            if not popup.is_closed():
                popup.close()
            _ins_log(ocr_output_dir, subfolder, "NOTE", "direct_url: closed popup immediately")
        except Exception as exc:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"direct_url: popup close: {exc!s}")
        
        # Step 3: Build PolicySchedule.aspx URL from popup URL
        # The popup URL typically contains the Policy folder path and query params (PID, etc.)
        policy_schedule_url = None
        
        # Try to derive PolicySchedule.aspx URL by replacing the page name
        if "/Policy/" in popup_url:
            # Replace whatever.aspx with PolicySchedule.aspx, keeping query string
            policy_schedule_url = re.sub(
                r"(/Policy/)([^/?#]+\.aspx)",
                r"\1PolicySchedule.aspx",
                popup_url,
                count=1,
                flags=re.I,
            )
        
        if not policy_schedule_url or policy_schedule_url == popup_url:
            # Fallback: try to construct from base URL + query
            parsed = urllib.parse.urlparse(popup_url)
            if parsed.query:
                # Build PolicySchedule URL with same query params
                base = f"{parsed.scheme}://{parsed.netloc}"
                # Try common paths
                for path_prefix in ["/prod/apps/V1/2W/Policy/", "/Policy/"]:
                    test_url = f"{base}{path_prefix}PolicySchedule.aspx?{parsed.query}"
                    policy_schedule_url = test_url
                    break
        
        if not policy_schedule_url:
            return {**out, "error": "direct_url: could not derive PolicySchedule.aspx URL from popup"}
        
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"direct_url: navigating to {policy_schedule_url[:300]!r}")
        
        # Step 4: Open PolicySchedule.aspx in a NEW tab (don't navigate main page)
        # This way we can close the tab after PDF capture and leave main page untouched
        ps_page = None
        try:
            ps_page = page.context.new_page()
            ps_page.goto(policy_schedule_url, timeout=30_000, wait_until="domcontentloaded")
        except Exception as exc:
            if ps_page is not None:
                try:
                    ps_page.close()
                except Exception:
                    pass
            return {**out, "error": f"direct_url: navigation failed: {exc!s}"}
        
        try:
            ps_page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        
        # Small settle time for any dynamic content
        try:
            ps_page.wait_for_timeout(2000)
        except Exception:
            pass
        
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"direct_url: on PolicySchedule page (new tab): {ps_page.url[:200]!r}")
        
        # Step 5: Generate output path and use page.pdf() on the new tab
        mob = _mobile_prefix_from_subfolder(subfolder)
        safe_pn = re.sub(r"[^\w\-.]+", "_", pn)[:80]
        out_name = _insurance_pdf_out_name(mob, safe_pn, subfolder)
        out_path = _unique_out_path(dest, out_name)
        
        # Try page.pdf() on the PolicySchedule tab
        pdf_success = _try_playwright_page_pdf(
            ps_page,
            out_path,
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            tag="direct_url_policy_schedule",
        )
        
        # Step 6: Close the PolicySchedule tab (regardless of success)
        try:
            if ps_page is not None and not ps_page.is_closed():
                ps_page.close()
                _ins_log(ocr_output_dir, subfolder, "NOTE", "direct_url: closed PolicySchedule tab")
        except Exception as exc:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"direct_url: close PolicySchedule tab: {exc!s}")
        
        if pdf_success:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"direct_url: PDF saved successfully: {out_path!s}")
            return {"ok": True, "error": None, "pdf_path": str(out_path)}
        
        # If page.pdf() didn't produce valid PDF, log and return error
        _ins_log(ocr_output_dir, subfolder, "NOTE", "direct_url: page.pdf() did not produce valid PDF")
        return {**out, "error": "direct_url: page.pdf() did not produce valid PDF"}
        
    except Exception as exc:
        return {**out, "error": f"direct_url: {exc!s}"}


def run_hero_insure_reports(
    page: Page,
    *,
    insurer: str,
    vin: str,
    policy_num_hint: str | None = None,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    fill_values: dict | None = None,
    staging_payload: dict | None = None,
    staging_id: str | None = None,
    dealer_id: int | None = None,
    commit_insurance_master: bool = False,
) -> dict[str, Any]:
    """
    On the MISP **Insurance** tab: navigate to **Print Policy** search (**PrintPolicyDetails** /
    **AllPrintPolicy**), **Product** + **Frame No.** (VIN) + **Go**.

    Always searches by **VIN/Frame No.** — the ``vin`` parameter is required.
    ``policy_num_hint`` is optional and only used as a fallback until a grid-scraped policy number is available.

    When ``commit_insurance_master`` is set (Generate Insurance production path), scrape **Total Premium**
    from the grid and **INSERT** ``insurance_master`` **after** PDF capture succeeds.

    Then: first grid **Print** (opens **Print Policy Certificates**), second **Print** in that window,
    save PDF (never ``page.pdf`` the search grid alone). **Preferred:** :meth:`Page.pdf` (Edge/Chrome).
    **Fallback:** file download on the same ``context`` (DMS-style).
    Capture method: ``HERO_MISP_PDF_STRATEGY=auto|playwright|download`` (default ``auto``).

    Returns ``{"ok": bool, "error": str|None, "pdf_path": str|None, "grid_scrape": dict|None}``.
    """
    tmo = _misp_tmo()
    out: dict[str, Any] = {
        "ok": False,
        "error": None,
        "pdf_path": None,
        "grid_scrape": None,
    }
    vin_val = (vin or "").strip()
    pn = (policy_num_hint or "").strip()
    if not vin_val:
        _ins_log(ocr_output_dir, subfolder, "NOTE", "skipped: vin is empty")
        return {**out, "error": "skipped: vin is empty"}
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
        # Block window.print() and document.execCommand('print') globally for all pages in this context
        _print_block_script = """(() => {
            window.print = () => {};
            const origExec = document.execCommand.bind(document);
            document.execCommand = (cmd, ...args) => {
                if (cmd && cmd.toLowerCase() === 'print') return false;
                return origExec(cmd, ...args);
            };
        })()"""
        try:
            page.context.add_init_script(_print_block_script)
        except Exception as exc:
            _ins_log(ocr_output_dir, subfolder, "NOTE", f"add_init_script for print block: {exc!s}")
        # Also suppress on the existing main page (add_init_script only affects NEW pages)
        try:
            page.evaluate(_print_block_script)
        except Exception:
            pass
        # Auto-dismiss any JS dialogs (alert/confirm/prompt) that could block execution
        def _auto_dismiss_dialog(dialog: Any) -> None:
            try:
                dialog.dismiss()
            except Exception:
                pass
        try:
            page.on("dialog", _auto_dismiss_dialog)
        except Exception:
            pass
        _misp_navigate_to_print_policy_search(
            page, tmo=tmo, ocr_output_dir=ocr_output_dir, subfolder=subfolder
        )
        _ins_log(
            ocr_output_dir,
            subfolder,
            "NOTE",
            "print_policy_details: navigation complete; preparing Product/Frame No./Go",
        )
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"on Print Policy URL: {page.url[:200]!r}")

        product_sel = None
        for root in _misp_form_roots(page):
            product_sel = _find_product_row_select(root)
            if product_sel is not None:
                break
        if product_sel is None:
            _frame_dump_on_error(page, "product_select_not_found", ocr_output_dir, subfolder)
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

        # Always search by VIN/Frame No.
        vin_in = None
        for root in _misp_form_roots(page):
            vin_in = _find_vin_input(root)
            if vin_in is not None:
                break
        if vin_in is None:
            _frame_dump_on_error(page, "vin_input_not_found", ocr_output_dir, subfolder)
            raise RuntimeError("Frame No. field not found (txtFrameNo)")
        vin_in.fill("", timeout=2000)
        vin_in.fill(vin_val, timeout=2000)
        _ins_log(ocr_output_dir, subfolder, "NOTE", f"Frame No. filled: {vin_val!r}")

        go_loc = None
        for root in _misp_form_roots(page):
            go_loc = _find_go_button(root)
            if go_loc is not None:
                break
        if go_loc is None:
            try:
                g = page.locator('input[type="submit"][value="Go" i]').first
                if g.count() > 0 and g.is_visible(timeout=1000):
                    go_loc = g
            except Exception:
                pass
        if go_loc is None:
            _frame_dump_on_error(page, "go_button_not_found", ocr_output_dir, subfolder)
            raise RuntimeError("Go button not found (btnGO)")
        go_loc.click(timeout=tmo, force=True)
        _ins_log(ocr_output_dir, subfolder, "NOTE", "Go clicked")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        grid_scrape: dict[str, Any] | None = None
        if commit_insurance_master:
            if customer_id is None or vehicle_id is None:
                return {
                    **out,
                    "error": "commit_insurance_master requires customer_id and vehicle_id",
                }
            grid_scrape = _misp_scrape_print_policy_grid(
                page,
                policy_num_hint=pn,
                ocr_output_dir=ocr_output_dir,
                subfolder=subfolder,
                tmo=tmo,
            )
            out["grid_scrape"] = grid_scrape
            _sid = (staging_id or "").strip()
            _did = int(dealer_id) if dealer_id is not None else None
            if _sid and _did is not None and grid_scrape:
                from app.services.add_sales_staging_state_service import (
                    persist_staging_insurance_main_fields,
                )

                persist_staging_insurance_main_fields(
                    _sid,
                    _did,
                    policy_num=str(grid_scrape.get("policy_num") or "").strip() or None,
                    policy_from=str(grid_scrape.get("policy_from") or "").strip() or None,
                    policy_to=str(grid_scrape.get("policy_to") or "").strip() or None,
                    premium=grid_scrape.get("premium"),
                    idv=grid_scrape.get("idv"),
                )
            if not grid_scrape.get("policy_num"):
                return {
                    **out,
                    "error": "print_policy_details: policy_num missing after grid scrape",
                    "grid_scrape": grid_scrape,
                }
            scraped_pn = (grid_scrape or {}).get("policy_num")
            if scraped_pn:
                pn = str(scraped_pn).strip()

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
            _frame_dump_on_error(page, "grid_print_not_found", ocr_output_dir, subfolder)
            raise RuntimeError("Grid Print (btnPrintPolicy) not found after Go")

        _suppress_window_print(page)
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
            _suppress_window_print(popup)
            # Auto-dismiss dialogs on popup too
            try:
                popup.on("dialog", _auto_dismiss_dialog)
            except Exception:
                pass

        # Direct URL approach: extract PID from popup URL and navigate directly to
        # PolicySchedule.aspx to capture PDF (no second Print button, no print dialogs)
        if popup is None:
            raise RuntimeError("No popup window opened after first Print — cannot extract PID for direct URL approach")

        direct_result = _misp_direct_url_pdf_approach(
            page=page,
            popup=popup,
            dest=dest,
            pn=pn,
            subfolder=subfolder,
            ocr_output_dir=ocr_output_dir,
            tmo=tmo,
        )
        if direct_result.get("ok"):
            if commit_insurance_master and grid_scrape is not None:
                commit_err, grid_scrape = _misp_insert_insurance_master_from_grid_scrape(
                    grid_scrape=grid_scrape,
                    customer_id=int(customer_id),
                    vehicle_id=int(vehicle_id),
                    fill_values=fill_values,
                    staging_payload=staging_payload,
                    staging_id=staging_id,
                    dealer_id=dealer_id,
                    ocr_output_dir=ocr_output_dir,
                    subfolder=subfolder,
                )
                out["grid_scrape"] = grid_scrape
                if commit_err:
                    return {**out, **direct_result, "ok": False, "error": commit_err}
            return {**out, **direct_result, "grid_scrape": grid_scrape or out.get("grid_scrape")}

        raise RuntimeError(f"Direct URL PDF capture failed: {direct_result.get('error')}")
    except Exception as exc:
        msg = str(exc).strip() or repr(exc)
        _ins_log(ocr_output_dir, subfolder, "ERROR", msg)
        if "detached" in msg.lower():
            _frame_dump_on_error(page, "frame_detached_error", ocr_output_dir, subfolder)
        logger.exception("%s failed", _PREFIX)
        return {**out, "error": msg}
    finally:
        # Close popup if still open (direct URL approach closes it, but this handles error cases)
        if popup is not None:
            try:
                if not popup.is_closed() and popup != page:
                    popup.close()
            except Exception:
                pass


def hero_insure_reports_service(
    *args: Any, **kwargs: Any,
) -> dict[str, Any]:
    return run_hero_insure_reports(*args, **kwargs)
