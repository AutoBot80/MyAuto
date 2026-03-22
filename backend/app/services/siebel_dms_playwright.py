"""
Hero Connect / Oracle Siebel Open UI — Playwright helpers for real DMS automation.

Siebel renders in nested iframes with aria-labelled inputs. This module tries multiple
selectors per field and walks candidate frames. Tune with:

- ``DMS_SIEBEL_CONTENT_FRAME_SELECTOR`` — CSS selector for ``page.frame_locator(...)``
  when auto-detection fails (e.g. ``iframe#s_0_26``).
- ``DMS_SIEBEL_MOBILE_ARIA_HINTS`` — comma-separated extra substrings for mobile field
  (adds ``input[aria-label*="<hint>" i]`` patterns).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# Same order as fill_dms_service.DMS_MILESTONE_ORDER (avoid import cycle).
_MILESTONE_SORT_ORDER: tuple[str, ...] = (
    "Customer found",
    "Enquiry created",
    "Care of filled",
    "Vehicle received",
    "Vehicle inspection done",
    "Invoice created",
)


def _sort_milestone_labels(labels: list[str]) -> list[str]:
    order = {k: i for i, k in enumerate(_MILESTONE_SORT_ORDER)}
    return sorted(labels, key=lambda x: order.get(x, 99))


@dataclass(frozen=True)
class SiebelDmsUrls:
    contact: str
    vehicles: str
    pdi: str
    vehicle: str
    enquiry: str
    line_items: str
    reports: str


def _goto(page: Page, url: str, label: str, *, nav_timeout_ms: int) -> None:
    u = (url or "").strip()
    if not u:
        return
    logger.info("siebel_dms: navigate %s -> %s", label, u[:180])
    page.goto(u, wait_until="domcontentloaded", timeout=nav_timeout_ms)


def _frame_score(url: str) -> int:
    u = (url or "").lower()
    score = 0
    if "start.swe" in u or "sweapp" in u:
        score += 6
    if "edealer" in u or "siebel" in u or "heromotocorp" in u:
        score += 4
    if len(u) > 40:
        score += 1
    return score


def _ordered_frames(page: Page) -> list[Frame]:
    """Prefer Siebel content iframes; include main frame last as fallback."""
    frames = list(page.frames)
    main = page.main_frame
    rest = [f for f in frames if f != main]
    rest.sort(key=lambda f: -_frame_score(f.url or ""))
    return rest + [main]


def _resolve_work_locator(page: Page, content_frame_selector: str | None):
    """
    Returns a locator root for Siebel work area: either frame_locator(selector) or page.
    Playwright: use locator.fill on frame_locator('iframe').content_frame equivalent via frame_locator.
    """
    sel = (content_frame_selector or "").strip()
    if sel:
        return page.frame_locator(sel)
    return None


def _fill_in_frame(frame: Frame, selectors: list[str], value: str, *, timeout_ms: int) -> bool:
    if not (value or "").strip():
        return False
    for css in selectors:
        try:
            loc = frame.locator(css).first
            if loc.count() == 0:
                continue
            try:
                if not loc.is_visible(timeout=800):
                    continue
            except Exception:
                continue
            loc.click(timeout=min(3000, timeout_ms))
            loc.fill("", timeout=min(3000, timeout_ms))
            loc.fill(value.strip(), timeout=timeout_ms)
            logger.info("siebel_dms: filled via %s", css[:120])
            return True
        except Exception as e:
            logger.debug("siebel_dms: selector %s failed: %s", css[:80], e)
            continue
    return False


def _fill_with_frame_locator(
    fl,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int,
) -> bool:
    if not (value or "").strip():
        return False
    for css in selectors:
        try:
            loc = fl.locator(css).first
            if loc.count() == 0:
                continue
            try:
                if not loc.is_visible(timeout=800):
                    continue
            except Exception:
                continue
            loc.click(timeout=min(3000, timeout_ms))
            loc.fill("", timeout=min(3000, timeout_ms))
            loc.fill(value.strip(), timeout=timeout_ms)
            logger.info("siebel_dms: filled (scoped frame) via %s", css[:120])
            return True
        except Exception as e:
            logger.debug("siebel_dms: scoped selector %s failed: %s", css[:80], e)
            continue
    return False


def _try_fill_field(
    page: Page,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    fl = _resolve_work_locator(page, content_frame_selector)
    if fl is not None:
        if _fill_with_frame_locator(fl, selectors, value, timeout_ms=timeout_ms):
            return True
    for frame in _ordered_frames(page):
        if _fill_in_frame(frame, selectors, value, timeout_ms=timeout_ms):
            return True
    return False


def _mobile_selectors(extra_hints: list[str]) -> list[str]:
    base = [
        'input[aria-label*="Cellular" i]',
        'input[aria-label*="Cell Phone" i]',
        'input[aria-label*="Mobile Phone" i]',
        'input[aria-label*="Main Phone" i]',
        'input[aria-label*="Phone #" i]',
        'input[title*="Cellular" i]',
        'input[title*="Cell Phone" i]',
        'input[title*="Mobile" i]',
        'input[name*="Cellular" i]',
        'input[name*="CellPhone" i]',
        'input[name*="Mobile" i]',
    ]
    for h in extra_hints:
        if len(h) >= 2:
            base.insert(0, f'input[aria-label*="{h}" i]')
            base.insert(0, f'input[title*="{h}" i]')
    return base


def _try_select_option(
    page: Page,
    selectors: list[str],
    label: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    if not (label or "").strip():
        return False
    fl = _resolve_work_locator(page, content_frame_selector)
    if fl is not None:
        for css in selectors:
            try:
                loc = fl.locator(css).first
                if loc.count() == 0:
                    continue
                loc.select_option(label=label.strip(), timeout=timeout_ms)
                logger.info("siebel_dms: selected %s via %s", label[:40], css[:80])
                return True
            except Exception:
                continue
    for frame in _ordered_frames(page):
        for css in selectors:
            try:
                loc = frame.locator(css).first
                if loc.count() == 0:
                    continue
                loc.select_option(label=label.strip(), timeout=timeout_ms)
                logger.info("siebel_dms: selected %s via %s", label[:40], css[:80])
                return True
            except Exception:
                continue
    return False


def _click_find_go_query(page: Page, *, timeout_ms: int, content_frame_selector: str | None) -> bool:
    """Click Find / Go / Query on Siebel toolbar (common on list & form applets)."""

    def try_on_fl(fl) -> bool:
        for role, name_pat in (
            ("button", re.compile(r"(Find|Go|Query)", re.I)),
            ("link", re.compile(r"(Find|Go|Query)", re.I)),
        ):
            try:
                loc = fl.get_by_role(role, name=name_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s (%s) in scoped frame", role, name_pat.pattern)
                    return True
            except Exception:
                continue
        for css in (
            'input[type="submit"][value*="Find" i]',
            'input[type="button"][value*="Find" i]',
            'button[title*="Find" i]',
            'a[title*="Find" i]',
        ):
            try:
                loc = fl.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s (scoped)", css[:60])
                    return True
            except Exception:
                continue
        return False

    fl = _resolve_work_locator(page, content_frame_selector)
    if fl is not None and try_on_fl(fl):
        return True

    for frame in _ordered_frames(page):
        for role, name_pat in (
            ("button", re.compile(r"(Find|Go|Query)", re.I)),
            ("link", re.compile(r"(Find|Go|Query)", re.I)),
        ):
            try:
                loc = frame.get_by_role(role, name=name_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s (%s)", role, name_pat.pattern)
                    return True
            except Exception:
                continue
        for css in (
            'input[type="submit"][value*="Find" i]',
            'input[type="button"][value*="Find" i]',
            'button[title*="Find" i]',
            'a[title*="Find" i]',
        ):
            try:
                loc = frame.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s", css[:60])
                    return True
            except Exception:
                continue
    return False


def _js_best_grid_row(frame: Frame) -> dict | None:
    """Return {texts: [...], len: n} for the widest plausible data row in the frame."""
    try:
        return frame.evaluate(
            """() => {
            const tables = Array.from(document.querySelectorAll('table'));
            let best = null;
            for (const t of tables) {
              const tbody = t.querySelector('tbody');
              if (!tbody) continue;
              const rows = tbody.querySelectorAll('tr');
              for (const tr of rows) {
                const cells = tr.querySelectorAll('td');
                if (cells.length < 6) continue;
                let nonempty = 0;
                const texts = [];
                for (const c of cells) {
                  const tx = (c.innerText || '').trim();
                  texts.push(tx);
                  if (tx.length > 0) nonempty++;
                }
                if (nonempty < 4) continue;
                if (!best || texts.length > best.len) best = { texts, len: texts.length };
              }
            }
            return best;
        }"""
        )
    except Exception:
        return None


def scrape_siebel_vehicle_row(page: Page, *, content_frame_selector: str | None) -> dict:
    """
    Best-effort scrape of first wide row from Siebel list / grid in any frame.
    Maps 13+ columns like the dummy DMS table when possible.
    """
    _ = content_frame_selector  # reserved if we scope evaluate to frame_locator later
    best: dict | None = None
    best_len = 0
    for frame in _ordered_frames(page):
        row = _js_best_grid_row(frame)
        if row and row.get("len", 0) > best_len:
            best = row
            best_len = row["len"]

    if not best or not best.get("texts"):
        return {}

    texts: list[str] = best["texts"]
    if len(texts) >= 13:
        ex_show = texts[11].strip()
        return {
            "key_num": texts[0].strip(),
            "frame_num": texts[1].strip(),
            "engine_num": texts[2].strip(),
            "model": texts[3].strip(),
            "color": texts[4].strip(),
            "cubic_capacity": texts[5].strip(),
            "seating_capacity": texts[6].strip(),
            "body_type": texts[7].strip(),
            "vehicle_type": texts[8].strip(),
            "num_cylinders": texts[9].strip(),
            "horse_power": texts[10].strip(),
            "vehicle_price": ex_show,
            "ex_showroom_price": ex_show,
            "year_of_mfg": texts[12].strip(),
        }
    if len(texts) >= 6:
        return {
            "key_num": texts[0].strip(),
            "frame_num": texts[1].strip() if len(texts) > 1 else "",
            "engine_num": texts[2].strip() if len(texts) > 2 else "",
            "model": texts[3].strip() if len(texts) > 3 else "",
            "color": texts[4].strip() if len(texts) > 4 else "",
            "vehicle_price": texts[-2].strip() if len(texts) > 2 else "",
            "year_of_mfg": texts[-1].strip() if len(texts) > 1 else "",
        }
    return {}


def run_hero_siebel_dms_flow(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
) -> dict:
    """
    Execute real Siebel DMS steps. Returns a result dict fragment:
    ``vehicle``, ``error``, ``dms_siebel_forms_filled`` (bool), ``dms_siebel_notes`` (list str).
    """
    out: dict = {
        "vehicle": {},
        "error": None,
        "dms_siebel_forms_filled": False,
        "dms_siebel_notes": [],
        "dms_milestones": [],
    }

    def ms_done(label: str) -> None:
        m = out["dms_milestones"]
        if label not in m:
            m.append(label)

    page.set_default_timeout(action_timeout_ms)

    mobile = (dms_values.get("mobile_phone") or "").strip()
    first = (dms_values.get("first_name") or "").strip()
    last = (dms_values.get("last_name") or "").strip()
    addr = (dms_values.get("address_line_1") or "").strip()
    state = (dms_values.get("state") or "").strip()
    pin = (dms_values.get("pin_code") or "").strip()
    landline = (dms_values.get("landline") or "").strip()
    father = (dms_values.get("father_husband_name") or "").strip()
    relation = (dms_values.get("relation_prefix") or "").strip()
    key_p = (dms_values.get("key_partial") or "").strip()
    frame_p = (dms_values.get("frame_partial") or "").strip()
    engine_p = (dms_values.get("engine_partial") or "").strip()

    def note(msg: str) -> None:
        out["dms_siebel_notes"].append(msg)
        logger.info("siebel_dms: %s", msg)

    try:
        _goto(page, urls.contact, "contact", nav_timeout_ms=nav_timeout_ms)
        page.wait_for_timeout(1200)

        filled_mobile = _try_fill_field(
            page,
            _mobile_selectors(mobile_aria_hints),
            mobile,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        if not filled_mobile:
            out["error"] = (
                "Siebel: could not find a mobile/cellular phone input on the contact view. "
                "Log in, open Buyer/CoBuyer, set DMS_SIEBEL_CONTENT_FRAME_SELECTOR to the "
                "content iframe CSS selector, or add DMS_SIEBEL_MOBILE_ARIA_HINTS (comma-separated "
                "substrings matching the field label)."
            )
            out["dms_milestones"] = _sort_milestone_labels(out["dms_milestones"])
            return out

        ms_done("Customer found")

        _try_fill_field(
            page,
            [
                'input[aria-label*="First Name" i]',
                'input[title*="First Name" i]',
                'input[name*="FirstName" i]',
            ],
            first,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _try_fill_field(
            page,
            [
                'input[aria-label*="Last Name" i]',
                'input[title*="Last Name" i]',
                'input[name*="LastName" i]',
            ],
            last,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _try_fill_field(
            page,
            [
                'input[aria-label*="Address" i]',
                'textarea[aria-label*="Address" i]',
                'input[aria-label*="Street" i]',
            ],
            addr[:120],
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _try_select_option(
            page,
            [
                'select[aria-label*="State" i]',
                'select[title*="State" i]',
                'select[name*="State" i]',
            ],
            state,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _try_fill_field(
            page,
            [
                'input[aria-label*="Postal" i]',
                'input[aria-label*="ZIP" i]',
                'input[aria-label*="Pin" i]',
                'input[aria-label*="PIN Code" i]',
            ],
            pin,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        if landline:
            _try_fill_field(
                page,
                [
                    'input[aria-label*="Work Phone" i]',
                    'input[aria-label*="Alternate" i]',
                    'input[aria-label*="Landline" i]',
                ],
                landline,
                timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
        if father:
            _try_fill_field(
                page,
                [
                    'input[aria-label*="Father" i]',
                    'input[aria-label*="Husband" i]',
                    'input[aria-label*="Parent" i]',
                ],
                father[:255],
                timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
        if relation:
            _try_select_option(
                page,
                [
                    'select[aria-label*="Relation" i]',
                    'select[aria-label*="S/O" i]',
                ],
                relation,
                timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )

        if (father or relation):
            ms_done("Care of filled")

        if _click_find_go_query(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
            note("Clicked Find/Go on contact view after filling fields.")
        else:
            note("No Find/Go control clicked on contact view (fields may still persist).")

        page.wait_for_timeout(1500)

        vehicle_url = (urls.vehicle or "").strip()
        if not vehicle_url:
            out["error"] = (
                "Siebel: set DMS_REAL_URL_VEHICLE to the Auto Vehicle List (or stock search) "
                "GotoView URL so key/chassis/engine search can run."
            )
            out["dms_milestones"] = _sort_milestone_labels(out["dms_milestones"])
            return out

        _goto(page, vehicle_url, "vehicle_list", nav_timeout_ms=nav_timeout_ms)
        page.wait_for_timeout(1500)

        key_ok = _try_fill_field(
            page,
            [
                'input[aria-label*="Key" i]:not([aria-label*="Keyboard" i])',
                'input[title*="Key" i]',
                'input[name*="Key" i]',
            ],
            key_p,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        frame_ok = _try_fill_field(
            page,
            [
                'input[aria-label*="Chassis" i]',
                'input[aria-label*="Frame" i]',
                'input[aria-label*="VIN" i]',
                'input[title*="Chassis" i]',
            ],
            frame_p,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        engine_ok = _try_fill_field(
            page,
            [
                'input[aria-label*="Engine" i]',
                'input[title*="Engine" i]',
            ],
            engine_p,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        if not (key_ok or frame_ok or engine_ok):
            out["error"] = (
                "Siebel: could not find key/chassis/engine search inputs on the vehicle view. "
                "Open Auto Vehicle List in the browser, inspect the query fields, and set "
                "DMS_SIEBEL_CONTENT_FRAME_SELECTOR if they live inside a specific iframe."
            )
            out["dms_milestones"] = _sort_milestone_labels(out["dms_milestones"])
            return out

        if _click_find_go_query(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
            note("Clicked Find/Go on vehicle search.")
        else:
            note("Vehicle search: Find/Go not detected; waiting for grid anyway.")

        try:
            page.wait_for_timeout(2500)
            page.wait_for_load_state("networkidle", timeout=12_000)
        except PlaywrightTimeout:
            note("networkidle wait timed out; continuing scrape.")

        scraped = scrape_siebel_vehicle_row(page, content_frame_selector=content_frame_selector)
        out["vehicle"] = scraped
        if scraped.get("key_num") or scraped.get("frame_num") or scraped.get("engine_num"):
            note("Scraped vehicle row from Siebel grid.")
        else:
            note("Vehicle grid scrape returned no key/chassis/engine; check list applet or selectors.")

        out["dms_siebel_forms_filled"] = True

        try:
            for label, u in (
                ("vehicles", urls.vehicles),
                ("pdi", urls.pdi),
                ("enquiry", urls.enquiry),
                ("line_items", urls.line_items),
                ("reports", urls.reports),
            ):
                if not (u or "").strip():
                    continue
                _goto(page, u, label, nav_timeout_ms=nav_timeout_ms)
                page.wait_for_timeout(600)
                if label == "vehicles":
                    ms_done("Vehicle received")
                elif label == "pdi":
                    ms_done("Vehicle inspection done")
                elif label == "enquiry":
                    ms_done("Enquiry created")
                elif label == "line_items":
                    ms_done("Invoice created")
        except Exception as nav_exc:
            note(f"Optional Siebel view navigation ended early: {nav_exc!s}")

    except PlaywrightTimeout as e:
        out["error"] = f"Siebel automation timeout: {e!s}"
        logger.warning("siebel_dms: PlaywrightTimeout %s", e)
    except Exception as e:
        out["error"] = f"Siebel automation error: {e!s}"
        logger.warning("siebel_dms: exception %s", e, exc_info=True)

    out["dms_milestones"] = _sort_milestone_labels(list(out.get("dms_milestones") or []))
    return out
