"""
Local test wrapper: Hero MISP — **full Generate Insurance** flow (``pre_process`` → ``main_process`` → ``post_process``).

Default scenario reproduces sale **7878793294_290626** (dealer **100003**, customer **70** / vehicle **94**,
staging **2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0**) — Bajaj **ND Cover, Rim Safeguard** (no RSA).
Same sale data as log ``Playwright_insurance_29062026_152630.txt`` (originally dealer 100001).

By default tries **DB-backed** ``build_insurance_fill_values`` + ``fetch_staging_payload`` when
``DATABASE_URL`` is set (``INSURANCE_TEST_USE_DB=1``). Falls back to a log/OCR-derived patched dict.

Requires ``backend/.env`` with ``INSURANCE_BASE_URL``. KYC uploads use ``Uploaded scans/{dealer}/{subfolder}``
when present. OCR merge checks (in order): ``INSURANCE_TEST_OCR_JSON``, ``ocr_output/{dealer}/{subfolder}``,
``Desktop/{subfolder}``, ``Uploaded scans/{dealer}/{subfolder}``.

Environment (optional):
  INSURANCE_TEST_DEALER_ID          default: 100003 (ND+Rim, no RSA; use 100001 for RSA preset)
  INSURANCE_TEST_SUBFOLDER          default: 7878793294_290626
  INSURANCE_TEST_STAGING_ID         default: 2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0
  INSURANCE_TEST_CUSTOMER_ID        default: 70
  INSURANCE_TEST_VEHICLE_ID         default: 94
  INSURANCE_TEST_USE_DB             default: 1 — load fill values + staging from DB when possible
  INSURANCE_TEST_HERO_CPI           default: N (log: dealer hero_cpi='N')
  INSURANCE_TEST_CPI_REQD           default: Y
  INSURANCE_TEST_INSURANCE_PAY      default: CC (log: ddlPaymentMode CC + HDFC)
  INSURANCE_TEST_INSURER            default: BAJAJ GENERAL INSURANCE LIMITED
  INSURANCE_TEST_PAUSE_BEFORE_EXIT  default: 1
  INSURANCE_TEST_OCR_JSON           path to OCR_To_be_Used.json

Double-click ``test_insurance_main_form_fill.bat`` or:
  python test_insurance_main_form_fill.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_insurance_main_form_fill")

# --- 7878793294_290626 @ dealer 100003 (Bajaj ND+Rim; DB sales_master/staging point at 100003) ---
DEALER_ID_TEST = "100003"
SALE_SUBFOLDER = "7878793294_290626"
STAGING_ID_TEST = "2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0"
CUSTOMER_ID_TEST = "70"
VEHICLE_ID_TEST = "94"
HERO_CPI = "N"
CPI_REQD = "Y"
INSURANCE_PAY = "CC"
MOBILE = "7878793294"
VIN = "MBLHAW481T9F01047"
CUSTOMER_NAME = "ANOOP SINGH"
FULL_CHASSIS = VIN
FULL_ENGINE = "HA11F6T9F027353"
MODEL_NAME = "SPLENDOR + BS6 DRS"
RTO_NAME = "RJ - BHARATPUR"
YEAR_OF_MFG = "2026"
FUEL_TYPE = "PETROL"
STATE = "RAJASTHAN"
CITY = "Bharatpur"
PIN_CODE = "321026"
ADDRESS = "CHAUKIPURA, BABEN, BHARATPUR, RAJASTHAN, 321026"
GENDER = "Male"
DOB = "01/01/1996"
MARITAL_STATUS = "Married"
PROFESSION = "Employed"
ALT_PHONE = "9259431877"
NOMINEE_NAME = "VIVEK"
NOMINEE_AGE = "18"
NOMINEE_RELATIONSHIP = "Brother"
NOMINEE_GENDER = "Male"
FINANCER_NAME = ".AXIS BANK LTD."
INSURER = "BAJAJ GENERAL INSURANCE LIMITED"
OEM_NAME = "Hero"
VEHICLE_PRICE = ""
PREFER_INSURER = ""


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y")


def _resolve_ocr_json_path(ocr_out: Path, subfolder: str, dealer_id: int) -> Path:
    override = (os.getenv("INSURANCE_TEST_OCR_JSON") or "").strip()
    if override:
        return Path(override)
    candidates = [
        ocr_out / subfolder / "OCR_To_be_Used.json",
        Path.home() / "OneDrive" / "Desktop" / subfolder / "OCR_To_be_Used.json",
        Path.home() / "Desktop" / subfolder / "OCR_To_be_Used.json",
    ]
    try:
        from app.config import get_uploads_dir

        candidates.append(get_uploads_dir(dealer_id) / subfolder / "OCR_To_be_Used.json")
    except Exception:
        pass
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def _try_load_staging_payload(staging_id: str, dealer_id: int) -> dict[str, Any] | None:
    sid = (staging_id or "").strip()
    if not sid:
        return None
    try:
        from app.repositories.add_sales_staging import fetch_staging_payload

        payload = fetch_staging_payload(sid, dealer_id)
        if payload:
            logger.info("Loaded staging payload from DB staging_id=%s", sid)
            return dict(payload)
    except Exception as exc:
        logger.warning("Could not load staging from DB (staging_id=%s): %s", sid, exc)
    return None


def _load_ocr_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read OCR JSON %s: %s", path, exc)
        return {}


def _ocr_merge_defaults(ocr: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    cust = ocr.get("customer") if isinstance(ocr.get("customer"), dict) else {}
    ins = ocr.get("insurance") if isinstance(ocr.get("insurance"), dict) else {}

    def _s(d: dict, *keys: str) -> str:
        for k in keys:
            v = d.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    if _s(cust, "name", "details_customer_name"):
        out["customer_name"] = _s(cust, "name") or str(ocr.get("details_customer_name") or "").strip()
    if _s(cust, "mobile_number"):
        out["mobile_number"] = _s(cust, "mobile_number")[:10]
    if _s(cust, "alt_phone_num"):
        out["alt_phone_num"] = _s(cust, "alt_phone_num")[:16]
    if _s(cust, "gender"):
        out["gender"] = _s(cust, "gender")
    if _s(cust, "date_of_birth"):
        out["dob"] = _s(cust, "date_of_birth")
    if _s(cust, "state"):
        out["state"] = _s(cust, "state").upper()
    if _s(cust, "city"):
        out["city"] = _s(cust, "city")
    if _s(cust, "pin_code", "pin"):
        out["pin_code"] = _s(cust, "pin_code", "pin")[:6]
    if _s(cust, "address"):
        out["address"] = _s(cust, "address")
    if _s(ins, "marital_status"):
        out["marital_status"] = _s(ins, "marital_status")
    if _s(ins, "profession"):
        out["profession"] = _s(ins, "profession")
    fin = _s(ins, "financier")
    if fin and fin not in ("[ ]", "[]", "-", "—"):
        out["financer_name"] = fin
    if _s(ins, "nominee_name"):
        out["nominee_name"] = _s(ins, "nominee_name")
    if _s(ins, "nominee_age"):
        out["nominee_age"] = _s(ins, "nominee_age")
    if _s(ins, "nominee_relationship"):
        out["nominee_relationship"] = _s(ins, "nominee_relationship")
    if _s(ins, "nominee_gender"):
        out["nominee_gender"] = _s(ins, "nominee_gender")
    if _s(ins, "insurer"):
        out["insurer"] = _s(ins, "insurer")
    return out


def _build_insurance_values(*, subfolder: str, ocr_path: Path) -> dict[str, Any]:
    from app.services.insurance_form_values import (
        effective_misp_hero_cpi,
        normalize_hero_cpi_flag,
        normalize_insurance_pay,
    )
    from app.repositories.add_sales_staging import _normalize_cpi_reqd_flag

    mobile = _env_str("INSURANCE_TEST_MOBILE_NUMBER", MOBILE)[:10]
    chassis = _env_str("INSURANCE_TEST_FRAME_NO", _env_str("INSURANCE_TEST_VIN", VIN)).strip()
    dealer_hero_cpi = normalize_hero_cpi_flag(_env_str("INSURANCE_TEST_HERO_CPI", HERO_CPI))
    effective_cpi_reqd = _normalize_cpi_reqd_flag(_env_str("INSURANCE_TEST_CPI_REQD", CPI_REQD))
    effective_hero_cpi = effective_misp_hero_cpi(
        effective_cpi_reqd=effective_cpi_reqd,
        dealer_hero_cpi=dealer_hero_cpi,
    )
    defaults: dict[str, str] = {
        "subfolder": subfolder,
        "insurer": _env_str("INSURANCE_TEST_INSURER", INSURER),
        "mobile_number": mobile,
        "alt_phone_num": ALT_PHONE[:16],
        "customer_name": CUSTOMER_NAME,
        "gender": GENDER,
        "dob": DOB,
        "marital_status": MARITAL_STATUS,
        "profession": PROFESSION,
        "state": STATE,
        "city": CITY,
        "pin_code": PIN_CODE[:6],
        "address": ADDRESS,
        "frame_no": chassis,
        "full_chassis": chassis,
        "engine_no": FULL_ENGINE,
        "model_name": MODEL_NAME,
        "fuel_type": FUEL_TYPE,
        "year_of_mfg": YEAR_OF_MFG,
        "vehicle_price": VEHICLE_PRICE,
        "oem_name": OEM_NAME,
        "rto_name": RTO_NAME,
        "nominee_name": NOMINEE_NAME,
        "nominee_age": NOMINEE_AGE,
        "nominee_relationship": NOMINEE_RELATIONSHIP,
        "nominee_gender": NOMINEE_GENDER,
        "financer_name": FINANCER_NAME,
        "prefer_insurer": PREFER_INSURER,
        "hero_cpi_dealer": dealer_hero_cpi,
        "effective_cpi_reqd": effective_cpi_reqd,
        "hero_cpi": effective_hero_cpi,
        "insurance_pay": normalize_insurance_pay(_env_str("INSURANCE_TEST_INSURANCE_PAY", INSURANCE_PAY)),
    }
    defaults.update(_ocr_merge_defaults(_load_ocr_json(ocr_path)))

    out: dict[str, Any] = {}
    for k, dflt in defaults.items():
        out[k] = _env_str(f"INSURANCE_TEST_{k.upper()}", dflt)

    out["hero_cpi_dealer"] = normalize_hero_cpi_flag(out.get("hero_cpi_dealer") or dealer_hero_cpi)
    out["effective_cpi_reqd"] = _normalize_cpi_reqd_flag(out.get("effective_cpi_reqd") or effective_cpi_reqd)
    out["hero_cpi"] = effective_misp_hero_cpi(
        effective_cpi_reqd=out["effective_cpi_reqd"],
        dealer_hero_cpi=out["hero_cpi_dealer"],
    )
    out["insurance_pay"] = normalize_insurance_pay(out.get("insurance_pay"))
    out["mobile_number"] = str(out.get("mobile_number") or mobile)[:10]
    out["alt_phone_num"] = str(out.get("alt_phone_num") or "")[:16]
    out["pin_code"] = str(out.get("pin_code") or "")[:6]
    out["frame_no"] = str(out.get("frame_no") or out.get("full_chassis") or chassis).strip()
    out["full_chassis"] = out["frame_no"]
    insurer = str(out.get("insurer") or "").strip()
    prefer = str(out.get("prefer_insurer") or "").strip()
    out["insurer_merged_before_prefer"] = insurer
    if not insurer and prefer:
        out["insurer"] = prefer
    fn = str(out.get("financer_name") or "").strip()
    if fn.upper() in ("NULL", "NONE", "NIL", "[ ]", "[]"):
        out["financer_name"] = ""
    return out


def _log_values_summary(values: dict[str, Any]) -> None:
    logger.info("--- Insurance fill values ---")
    for key in (
        "customer_name",
        "mobile_number",
        "frame_no",
        "engine_no",
        "model_name",
        "rto_name",
        "insurer",
        "insurance_addon_label",
        "hero_cpi_dealer",
        "effective_cpi_reqd",
        "hero_cpi",
        "insurance_pay",
        "nominee_name",
        "financer_name",
    ):
        logger.info("  %s: %s", key, values.get(key) or "—")
    flags = values.get("insurance_addon_flags")
    if isinstance(flags, dict):
        logger.info(
            "  insurance_addon_flags: nd=%s rti=%s rim=%s rsa=%s",
            flags.get("nd_cover"),
            flags.get("rti"),
            flags.get("rim_safeguard"),
            flags.get("rsa"),
        )
    else:
        logger.info("  insurance_addon_flags: — (missing; DB build may have failed)")


def _try_build_from_db(
    *,
    customer_id: int,
    vehicle_id: int,
    subfolder: str,
    ocr_out: Path,
    staging_payload: dict[str, Any] | None,
    staging_id: str | None,
    dealer_id: int,
) -> dict[str, Any] | None:
    try:
        from app.services.insurance_form_values import build_insurance_fill_values

        values = build_insurance_fill_values(
            customer_id,
            vehicle_id,
            subfolder,
            ocr_output_dir=ocr_out,
            staging_payload=staging_payload,
            staging_id=staging_id,
            dealer_id=dealer_id,
        )
        logger.info(
            "Using DB-backed build_insurance_fill_values customer_id=%s vehicle_id=%s",
            customer_id,
            vehicle_id,
        )
        return values
    except Exception as exc:
        logger.warning("DB fill values unavailable — falling back to patched dict: %s", exc)
        return None


def main() -> int:
    from app.config import INSURANCE_BASE_URL, get_ocr_output_dir
    from app.services.fill_hero_insurance_service import main_process, post_process, pre_process
    from app.services.handle_browser_opening import retain_automation_browser_for_operator_manual_close
    from app.services.insurance_form_values import write_insurance_form_values

    base = (INSURANCE_BASE_URL or "").strip()
    if not base:
        logger.error("INSURANCE_BASE_URL is not set — add it to backend/.env.")
        return 1

    try:
        dealer_id = int(_env_str("INSURANCE_TEST_DEALER_ID", DEALER_ID_TEST))
        customer_id = int(_env_str("INSURANCE_TEST_CUSTOMER_ID", CUSTOMER_ID_TEST))
        vehicle_id = int(_env_str("INSURANCE_TEST_VEHICLE_ID", VEHICLE_ID_TEST))
    except ValueError:
        logger.error("INSURANCE_TEST_DEALER_ID / CUSTOMER_ID / VEHICLE_ID must be integers.")
        return 1

    subfolder = _env_str("INSURANCE_TEST_SUBFOLDER", SALE_SUBFOLDER)
    staging_id = _env_str("INSURANCE_TEST_STAGING_ID", STAGING_ID_TEST) or None
    use_db = _env_bool("INSURANCE_TEST_USE_DB", True)
    ocr_out = get_ocr_output_dir(dealer_id)
    ocr_out.mkdir(parents=True, exist_ok=True)
    ocr_path = _resolve_ocr_json_path(ocr_out, subfolder, dealer_id)

    staging_payload = _try_load_staging_payload(staging_id or "", dealer_id) if use_db else None

    values: dict[str, Any] | None = None
    db_values_ok = False
    if use_db:
        values = _try_build_from_db(
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            subfolder=subfolder,
            ocr_out=ocr_out,
            staging_payload=staging_payload,
            staging_id=staging_id,
            dealer_id=dealer_id,
        )
        db_values_ok = values is not None
    if values is None:
        values = _build_insurance_values(subfolder=subfolder, ocr_path=ocr_path)
        logger.info("Using patched fill dict (no DB or INSURANCE_TEST_USE_DB=0)")

    _log_values_summary(values)

    if not str(values.get("insurer") or "").strip():
        logger.warning(
            "insurer is empty — set INSURANCE_TEST_INSURER or add insurer to OCR JSON; KYC may fail."
        )

    write_insurance_form_values(
        ocr_out,
        subfolder,
        customer_id,
        vehicle_id,
        values=values,
    )

    pause_before_exit = _env_str("INSURANCE_TEST_PAUSE_BEFORE_EXIT", "1").lower() not in (
        "0",
        "false",
        "no",
    )

    logger.info("Scenario: %s (dealer %s, customer %s / vehicle %s)", subfolder, dealer_id, customer_id, vehicle_id)
    if staging_id:
        logger.info("staging_id=%s", staging_id)
    logger.info("OCR JSON: %s (%s)", ocr_path, "found" if ocr_path.is_file() else "missing")
    logger.info("Full insurance flow: pre_process → main_process → post_process")
    logger.info("ocr_output: %s", ocr_out / subfolder)
    logger.info("VIN=%s  mobile=%s  insurer=%s", values.get("frame_no"), values.get("mobile_number"), values.get("insurer"))

    def _patched_build_insurance_fill_values(
        cid: int | None,
        vid: int | None,
        sub: str | None,
        *,
        ocr_output_dir: Path | None = None,
        staging_payload: dict | None = None,
        staging_id: str | None = None,
        dealer_id: int | None = None,
        effective_cpi_reqd: str | None = None,
    ) -> dict:
        _ = cid, vid, sub, ocr_output_dir, staging_payload, staging_id, dealer_id, effective_cpi_reqd
        return dict(values)

    exit_code = 1
    use_patch = not db_values_ok
    try:
        ctx = (
            patch(
                "app.services.fill_hero_insurance_service.build_insurance_fill_values",
                side_effect=_patched_build_insurance_fill_values,
            )
            if use_patch
            else nullcontext()
        )
        with ctx:
            pre = pre_process(
                insurance_base_url=base,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                subfolder=subfolder,
                ocr_output_dir=ocr_out,
                staging_payload=staging_payload,
                staging_id=staging_id,
                dealer_id=dealer_id,
            )
            main = main_process(
                pre_result=pre,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                subfolder=subfolder,
                ocr_output_dir=ocr_out,
                staging_payload=staging_payload,
                staging_id=staging_id,
                dealer_id=dealer_id,
            )
            result = post_process(pre_result=pre, main_result=main)

        logger.info("result: success=%s error=%s", result.get("success"), result.get("error"))
        if result.get("proposal_preview_scrape"):
            logger.info("proposal_preview_scrape: %s", result.get("proposal_preview_scrape"))
        if result.get("hero_insure_reports"):
            logger.info("hero_insure_reports: %s", result.get("hero_insure_reports"))
        if not result.get("success"):
            logger.error("Full insurance flow failed: %s", result.get("error"))
            exit_code = 1
        else:
            logger.info(
                "Completed — see Playwright_insurance_*.txt under %s",
                ocr_out / subfolder,
            )
            exit_code = 0
    except Exception:
        logger.exception("test_insurance_main_form_fill failed")
        exit_code = 1
    finally:
        try:
            retain_automation_browser_for_operator_manual_close()
        except Exception as exc:
            logger.debug("retain_automation_browser_for_operator_manual_close: %s", exc)
        if pause_before_exit:
            logger.info(
                "Press Enter to exit (browser stays open). "
                "Set INSURANCE_TEST_PAUSE_BEFORE_EXIT=0 to skip."
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
