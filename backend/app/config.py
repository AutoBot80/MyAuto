import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from backend/ so AWS and DB credentials are found when running from any cwd
_BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(_BACKEND_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

# Paths (injectable for tests / different envs)
APP_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = APP_ROOT.parent / "Uploaded scans"
OCR_OUTPUT_DIR = APP_ROOT.parent / "ocr_output"
BULK_UPLOAD_DIR = APP_ROOT.parent / "Bulk Upload"

# Dealer ID for app (JWT later). Used by bulk watcher and when client omits dealer_id.
DEALER_ID = int(os.getenv("DEALER_ID", "100001"))


def get_uploads_dir(dealer_id: int) -> Path:
    """Dealer-scoped uploads: Uploaded scans/{dealer_id}/."""
    return UPLOADS_DIR / str(dealer_id)


def get_ocr_output_dir(dealer_id: int) -> Path:
    """Dealer-scoped OCR output: ocr_output/{dealer_id}/."""
    return OCR_OUTPUT_DIR / str(dealer_id)


def get_bulk_upload_dir(dealer_id: int) -> Path:
    """Dealer-scoped bulk upload: Bulk Upload/{dealer_id}/."""
    return BULK_UPLOAD_DIR / str(dealer_id)


def get_bulk_input_scans_dir(dealer_id: int) -> Path:
    return get_bulk_upload_dir(dealer_id) / "Input Scans"


def get_bulk_queue_dir(dealer_id: int) -> Path:
    return get_bulk_upload_dir(dealer_id) / "Queued"


def get_bulk_processing_dir(dealer_id: int) -> Path:
    return get_bulk_upload_dir(dealer_id) / "Processing"
# Pre-OCR for bulk: use AWS Textract (default) for better mobile extraction; set false for Tesseract
BULK_PRE_OCR_USE_TEXTRACT = os.getenv("BULK_PRE_OCR_USE_TEXTRACT", "true").lower() in ("1", "true", "yes")
# Form 20 blank templates (PDF). Prefer single official PDF (page 0=front, page 1=back).
# Or use separate Form 20 Front.pdf and Form 20 back.pdf in Raw Scans/.
_FORM20_SINGLE = os.getenv("FORM20_TEMPLATE_SINGLE", "")
_FORM20_FRONT = os.getenv("FORM20_TEMPLATE_FRONT", "")
_FORM20_BACK = os.getenv("FORM20_TEMPLATE_BACK", "")
FORM20_TEMPLATE_SINGLE = Path(_FORM20_SINGLE) if _FORM20_SINGLE else APP_ROOT.parent / "Raw Scans" / "Official FORM-20 english.pdf"
FORM20_TEMPLATE_FRONT = Path(_FORM20_FRONT) if _FORM20_FRONT else APP_ROOT.parent / "Raw Scans" / "Form 20 Front.pdf"
FORM20_TEMPLATE_BACK = Path(_FORM20_BACK) if _FORM20_BACK else APP_ROOT.parent / "Raw Scans" / "Form 20 back.pdf"
# Word templates: project templates/word/ folder (reduces chance of missing templates)
_TEMPLATES_WORD = APP_ROOT.parent / "templates" / "word"
# Form 20 Word template (preferred - preserves layout)
_FORM20_DOCX = os.getenv("FORM20_TEMPLATE_DOCX", "")
FORM20_TEMPLATE_DOCX = Path(_FORM20_DOCX) if _FORM20_DOCX else _TEMPLATES_WORD / "FORM 20 Template.docx"
# Gate Pass Word template
_GATE_PASS_DOCX = os.getenv("GATE_PASS_TEMPLATE_DOCX", "")
GATE_PASS_TEMPLATE_DOCX = Path(_GATE_PASS_DOCX) if _GATE_PASS_DOCX else _TEMPLATES_WORD / "Gate Pass Template.docx"

# Tesseract OCR languages: "eng" (English), "hin" (Hindi/Devanagari). Use "+" for multiple (e.g. "eng+hin" for Aadhar).
OCR_LANG = os.getenv("OCR_LANG", "eng+hin")

# Tesseract PSM (page segmentation): 3=auto, 6=single block (default), 11=sparse text. Use 3 for docs with logos/graphics.
OCR_PSM = int(os.getenv("OCR_PSM", "3"))

# Preprocess image before OCR (grayscale + contrast) to improve text extraction when logos/pictures are present.
OCR_PREPROCESS = os.getenv("OCR_PREPROCESS", "true").lower() in ("1", "true", "yes")

# Two-step pipeline: Step 1 = AI classify image, Step 2 = Tesseract OCR. Set to "true" to use CLIP for classification.
USE_AI_CLASSIFIER = os.getenv("USE_AI_CLASSIFIER", "false").lower() in ("1", "true", "yes")
# Labels for zero-shot classification (comma-separated). Used when USE_AI_CLASSIFIER=true.
DOCUMENT_CLASSIFIER_LABELS = os.getenv(
    "DOCUMENT_CLASSIFIER_LABELS",
    "Aadhar card,Driving license,Vehicle registration certificate,Insurance document,Other document",
).split(",")

# OpenAI (for vision: Aadhar analysis, extract customer photo region).
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# AWS Textract (optional: for better extraction on details/sales sheets).
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
# Upload scans-v2: run independent Textract API calls (Aadhar front, Details forms, Insurance, …) in parallel.
OCR_UPLOAD_PARALLEL_TEXTRACT = os.getenv("OCR_UPLOAD_PARALLEL_TEXTRACT", "true").lower() in (
    "1",
    "true",
    "yes",
)
OCR_UPLOAD_TEXTRACT_TIMEOUT_SEC = int(os.getenv("OCR_UPLOAD_TEXTRACT_TIMEOUT_SEC", "240"))
# Credentials: set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or use default profile).

# Bulk queue / worker settings.
BULK_QUEUE_PROVIDER = os.getenv("BULK_QUEUE_PROVIDER", "local").strip().lower() or "local"
BULK_SQS_QUEUE_URL = os.getenv("BULK_SQS_QUEUE_URL", "").strip()
BULK_SQS_REGION = os.getenv("BULK_SQS_REGION", AWS_REGION).strip() or AWS_REGION
BULK_SQS_WAIT_TIME_SEC = int(os.getenv("BULK_SQS_WAIT_TIME_SEC", "20"))
BULK_SQS_VISIBILITY_TIMEOUT_SEC = int(os.getenv("BULK_SQS_VISIBILITY_TIMEOUT_SEC", "900"))
BULK_INGEST_POLL_SEC = int(os.getenv("BULK_INGEST_POLL_SEC", "5"))
BULK_WORKER_THREADS = int(os.getenv("BULK_WORKER_THREADS", "1"))
BULK_WORKER_ENABLED = os.getenv("BULK_WORKER_ENABLED", "true").lower() in ("1", "true", "yes")
BULK_INGEST_ENABLED = os.getenv("BULK_INGEST_ENABLED", "true").lower() in ("1", "true", "yes")
BULK_JOB_MAX_ATTEMPTS = int(os.getenv("BULK_JOB_MAX_ATTEMPTS", "3"))

# DMS fill (Playwright): base URL and login. Used when client calls POST /fill-forms.
# DMS_BASE_URL / VAHAN_BASE_URL / INSURANCE_BASE_URL: required in .env (no in-code defaults); validated at app startup.
DMS_BASE_URL = (os.getenv("DMS_BASE_URL") or "").strip().rstrip("/")
# Default **real** = Hero Connect / Siebel (``siebel_dms_playwright``). Values: ``real``, ``siebel``, ``live``, ``production``, ``hero``.
# ``dummy`` is no longer supported (static training HTML removed).
DMS_MODE = (os.getenv("DMS_MODE") or "real").strip().lower()
# Required for Fill DMS: ``DMS_REAL_URL_CONTACT`` for Stage 1 Find (always; ``skip_find`` in DB does not bypass).
# Also set ``DMS_REAL_URL_VEHICLE`` and other ``DMS_REAL_URL_*`` as needed (see fill_dms_service / LLD §2.4d).
DMS_REAL_URL_CONTACT = (os.getenv("DMS_REAL_URL_CONTACT") or "").strip()
# In Transit branch: e.g. Vehicles Receipt / HMCL In Transit (Process Receipt). See BRD §6.1a / run_hero_siebel_dms_flow.
DMS_REAL_URL_VEHICLES = (os.getenv("DMS_REAL_URL_VEHICLES") or "").strip()
# Optional: separate Pre Check view; if empty, Pre Check is attempted on ``DMS_REAL_URL_PDI`` after goto.
DMS_REAL_URL_PRECHECK = (os.getenv("DMS_REAL_URL_PRECHECK") or "").strip()
DMS_REAL_URL_PDI = (os.getenv("DMS_REAL_URL_PDI") or "").strip()
DMS_REAL_URL_VEHICLE = (os.getenv("DMS_REAL_URL_VEHICLE") or "").strip()
DMS_REAL_URL_ENQUIRY = (os.getenv("DMS_REAL_URL_ENQUIRY") or "").strip()
DMS_REAL_URL_LINE_ITEMS = (os.getenv("DMS_REAL_URL_LINE_ITEMS") or "").strip()
DMS_REAL_URL_REPORTS = (os.getenv("DMS_REAL_URL_REPORTS") or "").strip()
# Siebel Open UI automation: optional tuning
DMS_SIEBEL_ACTION_TIMEOUT_MS = int(os.getenv("DMS_SIEBEL_ACTION_TIMEOUT_MS", "30000"))
DMS_SIEBEL_NAV_TIMEOUT_MS = int(os.getenv("DMS_SIEBEL_NAV_TIMEOUT_MS", "90000"))
DMS_SIEBEL_CONTENT_FRAME_SELECTOR = (os.getenv("DMS_SIEBEL_CONTENT_FRAME_SELECTOR") or "").strip()
_siebel_mobile_hints = (os.getenv("DMS_SIEBEL_MOBILE_ARIA_HINTS") or "").strip()
DMS_SIEBEL_MOBILE_ARIA_HINTS = [x.strip() for x in _siebel_mobile_hints.split(",") if x.strip()]
# Extra ms after GotoView contact URL so nested iframes/applets can render (Hero Connect).
DMS_SIEBEL_POST_GOTO_WAIT_MS = int(os.getenv("DMS_SIEBEL_POST_GOTO_WAIT_MS", "5000"))
# Optional extra ms after every Siebel ``goto`` (before other waits). Use for flaky applets — not for
# “anti-bot” evasion (very short values like 10ms are ineffective). Typical stability tries: 150-500.
DMS_SIEBEL_INTER_ACTION_DELAY_MS = int(os.getenv("DMS_SIEBEL_INTER_ACTION_DELAY_MS", "0"))
# Optional extra iframe CSS selectors (comma-separated) tried before built-in Siebel patterns.
_siebel_if = (os.getenv("DMS_SIEBEL_AUTO_IFRAME_SELECTORS") or "").strip()
DMS_SIEBEL_AUTO_IFRAME_SELECTORS = [x.strip() for x in _siebel_if.split(",") if x.strip()]
# Optional override for Payment Lines frame fast-path (``siebel_dms_playwright._hero_default_payment_lines_root_hint``
# is used when both are empty). JSON object per LLD §2.4d.1, or path to a ``.json`` file.
DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE = (os.getenv("DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE") or "").strip()
DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_JSON = (os.getenv("DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_JSON") or "").strip()
# Optional overrides only — built-in Hero defaults live in ``siebel_dms_playwright`` (same pattern as Payment Lines).
DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_FILE = (os.getenv("DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_FILE") or "").strip()
DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_JSON = (os.getenv("DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_JSON") or "").strip()
DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_FILE = (
    os.getenv("DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_FILE") or ""
).strip()
DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_JSON = (
    os.getenv("DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_JSON") or ""
).strip()
DMS_LOGIN_USER = os.getenv("DMS_LOGIN_USER", "demo")
DMS_LOGIN_PASSWORD = os.getenv("DMS_LOGIN_PASSWORD", "demo")
# Run browser visible (headed) so user sees DMS page and automation. Set to "false" for headless.
DMS_PLAYWRIGHT_HEADED = os.getenv("DMS_PLAYWRIGHT_HEADED", "true").lower() in ("1", "true", "yes")
# Legacy flag (unused). ``fill_dms_service`` never calls ``Browser.close()`` / ``Playwright.stop()`` for operator sessions.
PLAYWRIGHT_KEEP_OPEN = os.getenv("PLAYWRIGHT_KEEP_OPEN", "false").lower() in ("1", "true", "yes")
# When the backend launches Edge/Chrome (no existing CDP session), Chromium is started with
# ``--remote-debugging-port=<port>`` so DevTools / ``chrome://inspect`` / a matching
# ``PLAYWRIGHT_CDP_URL=http://127.0.0.1:<port>`` can attach. Default **9333** avoids clashing with
# a manually started Edge on 9222. Set to **0** or empty to disable.
_mdbg_raw = (os.getenv("PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT") or "9333").strip()
PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT: int | None = (
    int(_mdbg_raw) if _mdbg_raw.isdigit() and int(_mdbg_raw) > 0 else None
)

# VAHAN base URL (production portal). Static training Vaahan automation was removed from the codebase.
VAHAN_BASE_URL = (os.getenv("VAHAN_BASE_URL") or "").strip().rstrip("/")

# Insurance portal base URL for Playwright (e.g. Hero MISP).
INSURANCE_BASE_URL = (os.getenv("INSURANCE_BASE_URL") or "").strip().rstrip("/")
# Max time (ms) to wait on the login page for the operator to sign in and reach KYC.
INSURANCE_LOGIN_WAIT_MS = int(os.getenv("INSURANCE_LOGIN_WAIT_MS", "600000"))
# Default Playwright timeout (ms) per action during Insurance automation (KYC + navigation). Lower = snappier.
INSURANCE_ACTION_TIMEOUT_MS = int(os.getenv("INSURANCE_ACTION_TIMEOUT_MS", "3500"))
# Tighter timeout while filling the policy / insurance-details form (many sequential fields).
INSURANCE_POLICY_FILL_TIMEOUT_MS = int(os.getenv("INSURANCE_POLICY_FILL_TIMEOUT_MS", "3200"))
# main_process: before proposal fill, write per-frame visible control scrape to ocr_output (see fill_hero_insurance_service).
_INSURANCE_MPS_SCRAPE = (os.getenv("INSURANCE_MAIN_PROCESS_FRAME_SCRAPE") or "true").strip().lower()
INSURANCE_MAIN_PROCESS_FRAME_SCRAPE = _INSURANCE_MPS_SCRAPE not in ("0", "false", "no", "off")
# Hero MISP KYC (``ekycpage`` / ``kycpage.aspx`` / ``/ekyc`` / ``/apps/kyc/``): optional keyboard SOP
# (click iframe/body → Tab → type insurer → …). See ``fill_hero_insurance_service._fill_kyc_ekyc_keyboard_sop``.
def _int_env(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


KYC_KEYBOARD_TABS_TO_INSURANCE_FIELD = _int_env("KYC_KEYBOARD_TABS_TO_INSURANCE_FIELD", 1)
KYC_KEYBOARD_TABS_INSURER_TO_OVD = _int_env("KYC_KEYBOARD_TABS_INSURER_TO_OVD", 3)
KYC_KEYBOARD_TABS_OVD_TO_MOBILE = _int_env("KYC_KEYBOARD_TABS_OVD_TO_MOBILE", 4)
KYC_KEYBOARD_TABS_MOBILE_TO_CONSENT = _int_env("KYC_KEYBOARD_TABS_MOBILE_TO_CONSENT", 2)
KYC_KEYBOARD_INSURER_ARROW_DOWN_MAX = _int_env("KYC_KEYBOARD_INSURER_ARROW_DOWN_MAX", 60)
KYC_KEYBOARD_OVD_ARROW_DOWN_MAX = _int_env("KYC_KEYBOARD_OVD_ARROW_DOWN_MAX", 28)
# Keyboard SOP inter-key delays (ms). Lower = faster; increase on slow portals if flaky.
KYC_KEYBOARD_INSURER_TYPE_DELAY_MS = _int_env("KYC_KEYBOARD_INSURER_TYPE_DELAY_MS", 7)
KYC_KEYBOARD_MOBILE_TYPE_DELAY_MS = _int_env("KYC_KEYBOARD_MOBILE_TYPE_DELAY_MS", 18)
KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS = _int_env("KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS", 72)
KYC_KEYBOARD_INSURER_ARROW_DOWN_STEP_MS = _int_env("KYC_KEYBOARD_INSURER_ARROW_DOWN_STEP_MS", 65)

# After insurer / KYC Partner: optional ``networkidle`` (ms). **0** = skip (default; no .env required).
INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS = _int_env("INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS", 0)
INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS = _int_env("INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS", 0)
# Short UI settle after KYC / VIN submit / post-mobile micro-pauses (ms). Override via HERO_MISP_UI_SETTLE_MS.
HERO_MISP_UI_SETTLE_MS = _int_env("HERO_MISP_UI_SETTLE_MS", 200)
# Default portal label for MISP **KYC Partner** when ``values['kyc_partner']`` is unset (documentation only;
# automation does not change ``ddlkycPartner`` — portal default e.g. Signzy remains).
KYC_DEFAULT_KYC_PARTNER_LABEL = (os.getenv("KYC_DEFAULT_KYC_PARTNER_LABEL") or "Signzy").strip() or "Signzy"


def _float_env(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


# KYC insurer match: ``fuzzy_best_option_label`` min score (global default 0.42 is often too strict for typos).
KYC_INSURER_FUZZY_MIN_SCORE = _float_env("KYC_INSURER_FUZZY_MIN_SCORE", 0.28)
# Fallback when fuzzy returns None: ``difflib.SequenceMatcher`` on normalized insurer vs focused/display text.
KYC_INSURER_DISPLAY_SEQUENCE_MIN = _float_env("KYC_INSURER_DISPLAY_SEQUENCE_MIN", 0.48)

_KYC_KB_SOP_RAW = (os.getenv("KYC_USE_KEYBOARD_EKYC_SOP") or "1").strip().lower()
# When true (default), ``ekycpage`` KYC uses the keyboard SOP instead of DOM clicks.
KYC_USE_KEYBOARD_EKYC_SOP = _KYC_KB_SOP_RAW not in ("0", "false", "no", "off")


def dms_automation_is_real_siebel() -> bool:
    """True when Fill DMS should run Siebel automation (``dummy`` mode is not supported)."""
    m = (DMS_MODE or "real").strip().lower()
    if m == "dummy":
        return False
    return m in ("real", "siebel", "live", "production", "hero")


def validate_external_site_urls() -> None:
    """Fail fast if DMS/Vahan/Insurance base URLs are not set (no fallbacks)."""
    missing: list[str] = []
    if not DMS_BASE_URL:
        missing.append("DMS_BASE_URL")
    if not VAHAN_BASE_URL:
        missing.append("VAHAN_BASE_URL")
    if not INSURANCE_BASE_URL:
        missing.append("INSURANCE_BASE_URL")
    if missing:
        raise RuntimeError(
            "Missing required environment variables (no defaults): "
            + ", ".join(missing)
            + ". Set them in backend/.env — see backend/.env.example."
        )
    if (DMS_MODE or "").strip().lower() == "dummy":
        raise RuntimeError(
            "DMS_MODE=dummy is no longer supported (static training sites were removed). "
            "Set DMS_MODE=real (or siebel/live/production/hero) and configure DMS_REAL_URL_CONTACT."
        )

