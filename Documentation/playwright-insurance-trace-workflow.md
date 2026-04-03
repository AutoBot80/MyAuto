# Playwright_insurance.txt — what is read, why repeats, what to do next

This note supports **one final (or occasional) test run** to drive **timeouts / resolver / KYC / VIN** tuning—not endless new logging.

## What logs exist in the repo

Only files that are **committed under the workspace** (or paths you **paste** in chat) are available for automated review. Example locations:

- `ocr_output/<dealer_id>/<subfolder>/Playwright_insurance.txt`

Runs that never wrote under `ocr_output/` in this clone are **not** visible to tooling unless you add or attach them.

**Example trace used for pre_process timing review (Apr 2026):**

- `ocr_output/100001/8279246146_030426/Playwright_insurance.txt`

## Why repeated runs were suggested

Multiple runs help when:

- Timings **vary** (network, portal load, operator path).
- You need **rare branches** (new tab vs same tab, upload KYC vs banner).

They are **not** mandatory before every fix. **One** run with clear `NOTE` lines is often enough to pick a **small** change (`HERO_MISP_LANDING_WAIT_MS`, resolver behavior, `INSURANCE_VIN_*`, `KYC_KEYBOARD_*`).

## Recommended workflow: final run → fix (not more logs)

1. Run **pre_process** (or full GI flow) once against real MISP with current code.
2. Open the new `ocr_output/<dealer>/<subfolder>/Playwright_insurance.txt`.
3. Prioritize using **existing** lines (no new log types unless a phase is blind):
   - **`tab_resolve`** … **`resolver_ms`** vs **`elapsed_ms`** — large **`resolver_ms`** with **`branch=stayed_on_fallback`** points at **`_misp_resolve_page_after_possible_new_tab`** in `fill_hero_insurance_service.py` (staged `wait_for_event`, same-tab fast path).
   - **`kyc_elapsed`** (e.g. **`after_ovd_ready`** → **`before_mobile_fill`**) — tune OVD / keyboard settles (`KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS`, etc.) or related `_t` pauses in `_fill_kyc_ekyc_keyboard_sop`.
   - **VIN** — **`wait_for_url_mispdms`**, **`txtFrameNo_attached`**, **`attach_attempts`** — tune **`_hero_misp_wait_for_mispdms_vin_url_event`** floor, **`_hero_misp_wait_for_vin_txt_frame_no_attached`** (root×selector order, per-attempt cap), and **`INSURANCE_VIN_*`** / **`_hero_misp_vin_step_timeout_ms`** as documented in **LLD** §2.4c.
4. Avoid **NOTE** spam unless something remains unmeasurable.

## Where this is implemented

Behavior and defaults are documented in **`Documentation/low-level-design.md`** §2.4c and **LLD changelog** (e.g. **6.213**). **BRD** / **HLD** changelog rows cross-reference the same delivery.
