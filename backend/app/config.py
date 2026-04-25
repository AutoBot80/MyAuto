from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from app.builtin_defaults import (
    DMS_PLAYWRIGHT_HEADED as _DEFAULT_DMS_PLAYWRIGHT_HEADED,
    OCR_UPLOAD_PARALLEL_TEXTRACT as _DEFAULT_OCR_UPLOAD_PARALLEL_TEXTRACT,
    PLAYWRIGHT_KEEP_OPEN as _DEFAULT_PLAYWRIGHT_KEEP_OPEN,
)
from app.hero_dms_defaults import (
    HERO_DMS_BASE_URL,
    HERO_DMS_REAL_URL_CONTACT,
    HERO_DMS_REAL_URL_ENQUIRY,
    HERO_DMS_REAL_URL_LINE_ITEMS,
    HERO_DMS_REAL_URL_PDI,
    HERO_DMS_REAL_URL_REPORTS,
    HERO_DMS_REAL_URL_VEHICLE,
    HERO_DMS_REAL_URL_VEHICLES,
    HERO_DMS_SIEBEL_INTER_ACTION_DELAY_MS,
)
from app.sqs_queue_defaults import (
    BULK_INGEST_ENABLED as _DEFAULT_BULK_INGEST_ENABLED,
    BULK_QUEUE_PROVIDER as _DEFAULT_BULK_QUEUE_PROVIDER,
    BULK_SQS_REGION as _DEFAULT_BULK_SQS_REGION,
    BULK_SQS_VISIBILITY_TIMEOUT_SEC as _DEFAULT_BULK_SQS_VISIBILITY_TIMEOUT_SEC,
    BULK_SQS_WAIT_TIME_SEC as _DEFAULT_BULK_SQS_WAIT_TIME_SEC,
    BULK_WORKER_ENABLED as _DEFAULT_BULK_WORKER_ENABLED,
)

# Load .env from backend/ so AWS and DB credentials are found when running from any cwd
_BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(_BACKEND_DIR / ".env")


def _bool_env(name: str, default: bool) -> bool:
    """Truthy: 1 / true / yes (case-insensitive). Unset uses ``default``; empty string is falsy."""
    raw = os.getenv(name)
    if raw is None:
        return default
    s = raw.strip().lower()
    if not s:
        return False
    return s in ("1", "true", "yes")

DATABASE_URL = os.getenv("DATABASE_URL")

# Paths (injectable for tests / different envs)
APP_ROOT = Path(__file__).resolve().parents[1]
_IS_LINUX = os.name != "nt"
# Electron dealer app: set SAATHI_BASE_DIR (e.g. D:\Saathi) so uploads/OCR align with the desktop install.
# On Linux/EC2 use hyphenated names (no spaces) to avoid systemd EnvironmentFile quoting issues.
_SAATHI_BASE_RAW = os.getenv("SAATHI_BASE_DIR", "").strip()
_UPLOADS_LEAF = "uploaded-scans" if _IS_LINUX else "Uploaded scans"
_BULK_LEAF = "bulk-upload" if _IS_LINUX else "Bulk Upload"
if _SAATHI_BASE_RAW:
    _SAATHI_BASE = Path(_SAATHI_BASE_RAW)
    UPLOADS_DIR = _SAATHI_BASE / _UPLOADS_LEAF
    OCR_OUTPUT_DIR = _SAATHI_BASE / "ocr_output"
    _CHALLANS_DEFAULT = _SAATHI_BASE / "Challans"
    _BULK_DEFAULT = _SAATHI_BASE / _BULK_LEAF
else:
    UPLOADS_DIR = APP_ROOT.parent / _UPLOADS_LEAF
    OCR_OUTPUT_DIR = APP_ROOT.parent / "ocr_output"
    _CHALLANS_DEFAULT = APP_ROOT.parent / "Challans"
    _BULK_DEFAULT = APP_ROOT.parent / _BULK_LEAF

# Optional absolute overrides (e.g. EC2: ``UPLOADS_DIR=/opt/saathi/data/uploaded-scans``). Win over ``SAATHI_BASE_DIR`` paths above.
_UPLOADS_ENV = os.getenv("UPLOADS_DIR", "").strip()
_OCR_ENV = os.getenv("OCR_OUTPUT_DIR", "").strip()
if _UPLOADS_ENV:
    UPLOADS_DIR = Path(_UPLOADS_ENV)
if _OCR_ENV:
    OCR_OUTPUT_DIR = Path(_OCR_ENV)

# Subdealer challan OCR artifacts: Raw_OCR.txt, OCR_To_be_Used.json per challan folder
_CHALLANS_DIR = os.getenv("CHALLANS_DIR", "").strip()
CHALLANS_DIR = Path(_CHALLANS_DIR) if _CHALLANS_DIR else _CHALLANS_DEFAULT
_BULK_UPLOAD_ENV = os.getenv("BULK_UPLOAD_DIR", "").strip()
BULK_UPLOAD_DIR = Path(_BULK_UPLOAD_ENV) if _BULK_UPLOAD_ENV else _BULK_DEFAULT

# Object storage: ``local`` (default) = files only on disk; ``s3`` = sync uploads/OCR to S3 (production EC2).
_STORAGE_BACKEND_RAW = (os.getenv("STORAGE_BACKEND") or "local").strip().lower()
STORAGE_BACKEND = _STORAGE_BACKEND_RAW if _STORAGE_BACKEND_RAW in ("local", "s3") else "local"
STORAGE_USE_S3 = STORAGE_BACKEND == "s3"
S3_DATA_BUCKET = (os.getenv("S3_DATA_BUCKET") or "").strip()
S3_UPLOADS_PREFIX = (os.getenv("S3_UPLOADS_PREFIX") or "uploaded-scans").strip().strip("/") or "uploaded-scans"
S3_OCR_PREFIX = (os.getenv("S3_OCR_PREFIX") or "ocr-output").strip().strip("/") or "ocr-output"
try:
    S3_PRESIGNED_EXPIRES_SEC = int((os.getenv("S3_PRESIGNED_EXPIRES_SEC") or "3600").strip())
except ValueError:
    S3_PRESIGNED_EXPIRES_SEC = 3600

# Dealer ID for app. Used by bulk watcher, AUTH_DISABLED dev principal, and when client omits dealer_id.
DEALER_ID = int(os.getenv("DEALER_ID", "100001"))

# Deployment label from ``.env`` (e.g. dev, test, staging, prod). Case-insensitive ``prod`` / ``production``
# enable production-only automation; all other values keep dev/test-safe defaults.
ENVIRONMENT = (os.getenv("ENVIRONMENT") or "").strip()
_ENV_LOWER = ENVIRONMENT.lower()
# ``ENVIRONMENT`` in ``prod`` / ``production`` (case-insensitive) — shared gate for production-only automation.
ENVIRONMENT_IS_PRODUCTION = _ENV_LOWER in ("prod", "production")
# Siebel attach flow: auto-click **Create Invoice** only in production — never in dev/test/staging.
HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE = ENVIRONMENT_IS_PRODUCTION
# When Create Invoice is not used (non-production), ``sales_master`` / staging commit uses this placeholder Invoice#.
HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER = (os.getenv("HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER") or "DUMMY111").strip() or "DUMMY111"
# Insurance MISP: click **Proposal Preview** / **Proposal Review** only in production; dev/test skip and succeed without it.
HERO_MISP_CLICK_PROPOSAL_PREVIEW_REVIEW = ENVIRONMENT_IS_PRODUCTION

# --- Auth (JWT) ---
# When true, requests skip JWT validation and use a dev Principal (DEALER_ID, admin=True). Local use only.
AUTH_DISABLED = os.getenv("AUTH_DISABLED", "false").lower() in ("1", "true", "yes")
# When true, POST /auth/login does not verify password against login_ref.pwd_hash (build/test only).
SKIP_PASSWORD_VERIFICATION = os.getenv("SKIP_PASSWORD_VERIFICATION", "false").lower() in (
    "1",
    "true",
    "yes",
)
JWT_SECRET = (os.getenv("JWT_SECRET") or "").strip()
JWT_ALGORITHM = (os.getenv("JWT_ALGORITHM") or "HS256").strip()
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
# Comma-separated list; empty = use built-in dev defaults in main.py
CORS_ORIGINS = [x.strip() for x in (os.getenv("CORS_ORIGINS") or "").split(",") if x.strip()]
# Request body size limits (bytes)
MAX_JSON_BODY_BYTES = int(os.getenv("MAX_JSON_BODY_BYTES", str(2 * 1024 * 1024)))
# Default max size for a single uploaded file (500 KB)
UPLOAD_MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(500 * 1024)))
# Add Sales consolidated PDF (multi-page Aadhaar + details); larger than per-image scans
UPLOAD_MAX_CONSOLIDATED_PDF_BYTES = int(
    os.getenv("UPLOAD_MAX_CONSOLIDATED_PDF_BYTES", str(5 * 1024 * 1024))
)
# Single-file upload routes: /qr-decode, /vision, /textract (POST), /subdealer-challan/parse-scan
MAX_SINGLE_UPLOAD_BODY_BYTES = int(
    os.getenv("MAX_SINGLE_UPLOAD_BODY_BYTES", str(int(UPLOAD_MAX_FILE_BYTES * 1.2)))
)
# Legacy name: non-/uploads binary routes use MAX_SINGLE_UPLOAD_BODY_BYTES in middleware
MAX_UPLOAD_BODY_BYTES = int(os.getenv("MAX_UPLOAD_BODY_BYTES", str(MAX_SINGLE_UPLOAD_BODY_BYTES)))
# Whole multipart body for POST /uploads/* (several parts; must fit consolidated PDF + boundaries)
_MAX_UPLOAD_ROUTE_DEFAULT = max(
    10 * UPLOAD_MAX_FILE_BYTES + 256 * 1024,
    UPLOAD_MAX_CONSOLIDATED_PDF_BYTES + 512 * 1024,
)
MAX_UPLOAD_ROUTE_BODY_BYTES = int(
    os.getenv("MAX_UPLOAD_ROUTE_BODY_BYTES", str(_MAX_UPLOAD_ROUTE_DEFAULT))
)

# Per-file caps in UploadService (bytes) — default 500 KB each
UPLOAD_MAX_IMAGE_BYTES = int(os.getenv("UPLOAD_MAX_IMAGE_BYTES", str(UPLOAD_MAX_FILE_BYTES)))
UPLOAD_MAX_PDF_BYTES = int(os.getenv("UPLOAD_MAX_PDF_BYTES", str(UPLOAD_MAX_FILE_BYTES)))
UPLOAD_MAX_LEGACY_FILE_BYTES = int(os.getenv("UPLOAD_MAX_LEGACY_FILE_BYTES", str(UPLOAD_MAX_FILE_BYTES)))

# All user-supplied text fields in JSON (unless overridden per call)
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "300"))


def get_uploads_dir(dealer_id: int) -> Path:
    """Dealer-scoped uploads: Uploaded scans/{dealer_id}/."""
    return UPLOADS_DIR / str(dealer_id)


def get_uploaded_scans_sale_subfolder_leaf(mobile: str) -> str:
    """
    Leaf directory name for a sale: ``{10-digit-mobile}_{ddmmyy}``.
    Must stay in sync with **get_uploaded_scans_sale_folder** and **UploadService** scans-v2 paths.
    """
    dig = re.sub(r"\D", "", str(mobile or ""))
    if len(dig) >= 10:
        mob = dig[-10:]
    elif dig:
        mob = dig.zfill(10)[:10]
    else:
        mob = "0000000000"
    return f"{mob}_{date.today().strftime('%d%m%y')}"


def get_uploaded_scans_sale_folder(dealer_id: int, mobile: str) -> Path:
    """
    Per-sale folder under **Uploaded scans**, same leaf convention as Add Sales / OCR:
    ``Uploaded scans/{dealer_id}/{10-digit-mobile}_{ddmmyy}/``.
    """
    return get_uploads_dir(int(dealer_id)) / get_uploaded_scans_sale_subfolder_leaf(mobile)


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


def get_add_sales_pre_ocr_work_dir(dealer_id: int) -> Path:
    """
    Temp working directory for Add Sales consolidated-PDF pre-OCR (PDF copy + Tesseract ``*_pre_ocr.txt``),
    manual-fallback sessions, etc. Defaults to **{project}/.cache/add_sales_pre_ocr_work/{dealer_id}** (not under
    ``Uploaded scans``). Override with env ``ADD_SALES_PRE_OCR_WORK_ROOT`` (absolute path; dealer id is appended).
    Used only by in-request ``run_pre_ocr_and_prepare`` — does **not** enqueue ``bulk_loads`` or the bulk worker queue.
    """
    _raw = (os.getenv("ADD_SALES_PRE_OCR_WORK_ROOT") or "").strip()
    if _raw:
        return (Path(_raw) / str(dealer_id)).resolve()
    return (APP_ROOT.parent / ".cache" / "add_sales_pre_ocr_work" / str(dealer_id)).resolve()

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
OCR_UPLOAD_PARALLEL_TEXTRACT = _bool_env(
    "OCR_UPLOAD_PARALLEL_TEXTRACT", _DEFAULT_OCR_UPLOAD_PARALLEL_TEXTRACT
)
OCR_UPLOAD_TEXTRACT_TIMEOUT_SEC = int(os.getenv("OCR_UPLOAD_TEXTRACT_TIMEOUT_SEC", "240"))
# Pre-OCR: run Textract DetectDocumentText on the Details page (after Tesseract classification) for
# higher-quality text (handwriting). Adds ~$1.50/1000 pages. Aadhaar stays Tesseract-only.
OCR_PRE_OCR_TEXTRACT_DETAILS = _bool_env("OCR_PRE_OCR_TEXTRACT_DETAILS", True)
# Pre-OCR: if Tesseract text on Aadhaar front/back/combined pages looks failed, retry via Textract DDT.
OCR_PRE_OCR_TEXTRACT_AADHAR_FALLBACK = _bool_env("OCR_PRE_OCR_TEXTRACT_AADHAR_FALLBACK", True)
# Pre-OCR: run Tesseract OSD (orientation detection) on each page before OCR. OSD costs ~5-6s per
# page and only helps Tesseract (AWS Textract handles orientation automatically). With the UNUSED-page
# DDT rescue in place, Tesseract failures on rotated pages are recoverable — so OSD is off by default.
# Set to true only if scans regularly arrive rotated 90/180/270° and you want Tesseract (not just
# Textract) to read them correctly on the first pass.
OCR_PRE_OCR_OSD_ENABLED = _bool_env("OCR_PRE_OCR_OSD_ENABLED", False)
# Pre-OCR: PyMuPDF render DPI when rasterizing PDF pages (Tesseract + Textract DDT). 300 matches AWS
# guidance for best accuracy on small text; clamp 150–400 to stay within Textract size limits.
try:
    _OCR_RASTER_DPI_RAW = int((os.getenv("OCR_PRE_OCR_RASTER_DPI") or "300").strip())
except ValueError:
    _OCR_RASTER_DPI_RAW = 300
OCR_PRE_OCR_RASTER_DPI = max(150, min(400, _OCR_RASTER_DPI_RAW))
# Credentials: set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or use default profile).

# Bulk queue / worker settings. Defaults: ``sqs_queue_defaults``; ``BULK_SQS_QUEUE_URL`` still from env only.
BULK_QUEUE_PROVIDER = (
    (os.getenv("BULK_QUEUE_PROVIDER") or _DEFAULT_BULK_QUEUE_PROVIDER).strip().lower()
    or _DEFAULT_BULK_QUEUE_PROVIDER
)
BULK_SQS_QUEUE_URL = os.getenv("BULK_SQS_QUEUE_URL", "").strip()
BULK_SQS_REGION = (
    (os.getenv("BULK_SQS_REGION") or _DEFAULT_BULK_SQS_REGION).strip() or AWS_REGION
)
BULK_SQS_WAIT_TIME_SEC = int(
    os.getenv("BULK_SQS_WAIT_TIME_SEC", str(_DEFAULT_BULK_SQS_WAIT_TIME_SEC))
)
BULK_SQS_VISIBILITY_TIMEOUT_SEC = int(
    os.getenv("BULK_SQS_VISIBILITY_TIMEOUT_SEC", str(_DEFAULT_BULK_SQS_VISIBILITY_TIMEOUT_SEC))
)
BULK_INGEST_POLL_SEC = int(os.getenv("BULK_INGEST_POLL_SEC", "5"))
BULK_WORKER_THREADS = int(os.getenv("BULK_WORKER_THREADS", "1"))
BULK_WORKER_ENABLED = _bool_env("BULK_WORKER_ENABLED", _DEFAULT_BULK_WORKER_ENABLED)
BULK_INGEST_ENABLED = _bool_env("BULK_INGEST_ENABLED", _DEFAULT_BULK_INGEST_ENABLED)
BULK_JOB_MAX_ATTEMPTS = int(os.getenv("BULK_JOB_MAX_ATTEMPTS", "3"))

# DMS fill (Playwright): base URL and login. Used when client calls POST /fill-forms.
# Hero DMS URLs default from ``app.hero_dms_defaults``; optional env overrides (e.g. tests) may replace them.
# VAHAN_BASE_URL / INSURANCE_BASE_URL: required in .env (no in-code defaults); validated at app startup.
DMS_BASE_URL = (os.getenv("DMS_BASE_URL") or HERO_DMS_BASE_URL).strip().rstrip("/")
# Default **real** = Hero Connect / Siebel (``hero_dms_*`` modules). Values: ``real``, ``siebel``, ``live``, ``production``, ``hero``.
# ``dummy`` is no longer supported (static training HTML removed).
DMS_MODE = (os.getenv("DMS_MODE") or "real").strip().lower()
# Fill DMS Stage 1 Find (always; ``skip_find`` in DB does not bypass). Defaults: ``hero_dms_defaults``.
DMS_REAL_URL_CONTACT = (os.getenv("DMS_REAL_URL_CONTACT") or HERO_DMS_REAL_URL_CONTACT).strip()
# In Transit branch: e.g. Vehicles Receipt / HMCL In Transit (Process Receipt). See BRD §6.1a / run_hero_siebel_dms_flow.
DMS_REAL_URL_VEHICLES = (os.getenv("DMS_REAL_URL_VEHICLES") or HERO_DMS_REAL_URL_VEHICLES).strip()
# Optional: separate Pre Check view; if empty, Pre Check is attempted on ``DMS_REAL_URL_PDI`` after goto.
DMS_REAL_URL_PRECHECK = (os.getenv("DMS_REAL_URL_PRECHECK") or "").strip()
DMS_REAL_URL_PDI = (os.getenv("DMS_REAL_URL_PDI") or HERO_DMS_REAL_URL_PDI).strip()
DMS_REAL_URL_VEHICLE = (os.getenv("DMS_REAL_URL_VEHICLE") or HERO_DMS_REAL_URL_VEHICLE).strip()
DMS_REAL_URL_ENQUIRY = (os.getenv("DMS_REAL_URL_ENQUIRY") or HERO_DMS_REAL_URL_ENQUIRY).strip()
DMS_REAL_URL_LINE_ITEMS = (os.getenv("DMS_REAL_URL_LINE_ITEMS") or HERO_DMS_REAL_URL_LINE_ITEMS).strip()
DMS_REAL_URL_REPORTS = (os.getenv("DMS_REAL_URL_REPORTS") or HERO_DMS_REAL_URL_REPORTS).strip()
# Siebel Open UI automation: optional tuning
DMS_SIEBEL_ACTION_TIMEOUT_MS = int(os.getenv("DMS_SIEBEL_ACTION_TIMEOUT_MS", "30000"))
DMS_SIEBEL_NAV_TIMEOUT_MS = int(os.getenv("DMS_SIEBEL_NAV_TIMEOUT_MS", "90000"))
DMS_SIEBEL_CONTENT_FRAME_SELECTOR = (os.getenv("DMS_SIEBEL_CONTENT_FRAME_SELECTOR") or "").strip()
_siebel_mobile_hints = (os.getenv("DMS_SIEBEL_MOBILE_ARIA_HINTS") or "").strip()
DMS_SIEBEL_MOBILE_ARIA_HINTS = [x.strip() for x in _siebel_mobile_hints.split(",") if x.strip()]
# Extra ms after GotoView contact URL so nested iframes/applets can render (Hero Connect).
DMS_SIEBEL_POST_GOTO_WAIT_MS = int(os.getenv("DMS_SIEBEL_POST_GOTO_WAIT_MS", "5000"))
# Optional extra ms after every Siebel ``goto`` (before other waits). Hero default in ``hero_dms_defaults``.
DMS_SIEBEL_INTER_ACTION_DELAY_MS = HERO_DMS_SIEBEL_INTER_ACTION_DELAY_MS
# Optional extra iframe CSS selectors (comma-separated) tried before built-in Siebel patterns.
_siebel_if = (os.getenv("DMS_SIEBEL_AUTO_IFRAME_SELECTORS") or "").strip()
DMS_SIEBEL_AUTO_IFRAME_SELECTORS = [x.strip() for x in _siebel_if.split(",") if x.strip()]
# Optional override for Payment Lines frame fast-path (``hero_dms_shared_utilities._hero_default_payment_lines_root_hint``
# is used when both are empty). JSON object per LLD §2.4d.1, or path to a ``.json`` file.
DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE = (os.getenv("DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE") or "").strip()
DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_JSON = (os.getenv("DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_JSON") or "").strip()
# Optional overrides only — built-in Hero defaults live in ``hero_dms_shared_utilities`` (same pattern as Payment Lines).
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
# Run browser visible (headed) so user sees DMS page and automation. Default: ``builtin_defaults``.
DMS_PLAYWRIGHT_HEADED = _bool_env("DMS_PLAYWRIGHT_HEADED", _DEFAULT_DMS_PLAYWRIGHT_HEADED)
# Legacy flag (unused). ``fill_dms_service`` never calls ``Browser.close()`` / ``Playwright.stop()`` for operator sessions.
PLAYWRIGHT_KEEP_OPEN = _bool_env("PLAYWRIGHT_KEEP_OPEN", _DEFAULT_PLAYWRIGHT_KEEP_OPEN)
# When the backend launches Edge/Chrome (no existing CDP session), Chromium is started with
# ``--remote-debugging-port=<port>`` so DevTools / ``chrome://inspect`` / a matching
# ``PLAYWRIGHT_CDP_URL=http://127.0.0.1:<port>`` can attach. Default **9333** avoids clashing with
# a manually started Edge on 9222. Set to **0** or empty to disable.
_mdbg_raw = (os.getenv("PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT") or "9333").strip()
PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT: int | None = (
    int(_mdbg_raw) if _mdbg_raw.isdigit() and int(_mdbg_raw) > 0 else None
)

# VAHAN portal login URL — used by fill_rto_service Playwright automation.
VAHAN_BASE_URL = (
    os.getenv("VAHAN_BASE_URL")
    or "https://vahan.parivahan.gov.in/vahan/vahan/ui/login/login.xhtml"
).strip().rstrip("/")
# Logged-in dealer landing (office / action / Show Form). Used to reset when the tab was left on workbench.
VAHAN_DEALER_HOME_URL = (
    os.getenv("VAHAN_DEALER_HOME_URL")
    or "https://vahan.parivahan.gov.in/vahan/vahan/home.xhtml"
).strip().rstrip("/")

# Insurance portal base URL for Playwright (e.g. Hero MISP).
INSURANCE_BASE_URL = (os.getenv("INSURANCE_BASE_URL") or "").strip().rstrip("/")
# Max time (ms) to wait on the login page for the operator to sign in and reach KYC.
INSURANCE_LOGIN_WAIT_MS = int(os.getenv("INSURANCE_LOGIN_WAIT_MS", "600000"))
# Default Playwright timeout (ms) per action during Insurance automation (KYC + navigation). Lower = snappier.
INSURANCE_ACTION_TIMEOUT_MS = int(os.getenv("INSURANCE_ACTION_TIMEOUT_MS", "3500"))
# Tighter timeout while filling the policy / insurance-details form (many sequential fields).
INSURANCE_POLICY_FILL_TIMEOUT_MS = int(os.getenv("INSURANCE_POLICY_FILL_TIMEOUT_MS", "3200"))
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
KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS = _int_env("KYC_KEYBOARD_OVD_ARROW_DOWN_SETTLE_MS", 48)
KYC_KEYBOARD_INSURER_ARROW_DOWN_STEP_MS = _int_env("KYC_KEYBOARD_INSURER_ARROW_DOWN_STEP_MS", 65)

# After insurer / KYC Partner: optional ``networkidle`` (ms). **0** = skip (default; no .env required).
INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS = _int_env("INSURANCE_KYC_POST_INSURER_NETWORKIDLE_MS", 0)
INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS = _int_env("INSURANCE_KYC_POST_KYC_PARTNER_NETWORKIDLE_MS", 0)
# Short UI settle after KYC / VIN submit / post-mobile micro-pauses (ms). Override via HERO_MISP_UI_SETTLE_MS.
# ``fill_hero_insurance_service._t`` clamps each wait to **200** ms max (``_MISP_UI_SETTLE_CAP_MS``); values above 200 have no extra effect there.
HERO_MISP_UI_SETTLE_MS = _int_env("HERO_MISP_UI_SETTLE_MS", 200)
# Pre-Proceed (after mobile fill on KYC): ``domcontentloaded`` cap — **not** ``networkidle`` (was up to 12s).
INSURANCE_KYC_POST_MOBILE_DOM_MS = _int_env("INSURANCE_KYC_POST_MOBILE_DOM_MS", 2000)
# Max wait for #navbarVerticalNav / New Policy / KYC hints after **2W** or **New Policy** click (was 5000).
HERO_MISP_LANDING_WAIT_MS = _int_env("HERO_MISP_LANDING_WAIT_MS", 2500)
# When 1: after insurer commit (non-light KYC nav), open a temporary ``about:blank`` tab so the KYC document
# gets a real visibility transition. Default **off** — DOM insurer + light keyboard nav usually do not need it.
_HERO_KYC_TAB_AWAY_RAW = (os.getenv("HERO_MISP_KYC_TAB_AWAY_SIMULATION") or "0").strip().lower()
HERO_MISP_KYC_TAB_AWAY_SIMULATION = _HERO_KYC_TAB_AWAY_RAW in ("1", "true", "yes", "on")
# Opening ``domcontentloaded`` before VIN attach poll (decoupled from ``_hero_misp_vin_step_timeout_ms``).
INSURANCE_VIN_PRE_DOMCONTENTLOADED_MS = _int_env("INSURANCE_VIN_PRE_DOMCONTENTLOADED_MS", 1000)
# After ``wait_for_url`` to MispDms.aspx: second ``domcontentloaded`` cap (lower than legacy 8000 ms).
INSURANCE_VIN_POST_URL_DOMCONTENTLOADED_MS = _int_env(
    "INSURANCE_VIN_POST_URL_DOMCONTENTLOADED_MS", 2500
)
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
# When ``dealer_ref.prefer_insurer`` is set, require at least this ``SequenceMatcher`` ratio vs the merged
# details insurer (``master_ref``-aligned) before replacing with the dealer string. High default: insurers are
# normalized; lower the env var only for unusual short prefer aliases.
INSURER_PREFER_FUZZY_MIN_RATIO = _float_env("INSURER_PREFER_FUZZY_MIN_RATIO", 0.80)
# After the last eKYC file attach: wait for MISP to process uploads before clicking Proceed/Next (ms). Default **2s** max.
MISP_KYC_POST_UPLOAD_STABLE_MS = _int_env("MISP_KYC_POST_UPLOAD_STABLE_MS", 2_000)
# eKYC may **auto-advance** to ``MispDms.aspx``. Poll the top URL this long before requiring CTA (default **2s**).
MISP_KYC_TO_VIN_URL_POLL_MS = _int_env("MISP_KYC_TO_VIN_URL_POLL_MS", 2_000)
# VIN step: if **Please wait** / interstitial is present, add this to ``wait_for_url`` (MispDms) budget (default **2s**).
MISP_KYC_PLEASE_WAIT_EXTRA_URL_MS = _int_env("MISP_KYC_PLEASE_WAIT_EXTRA_URL_MS", 2_000)

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
            "Set DMS_MODE=real (or siebel/live/production/hero); Hero DMS URLs default from app.hero_dms_defaults."
        )

