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

# DMS fill (Playwright): base URL and login. Used when client calls POST /fill-dms.
# DMS_BASE_URL / VAHAN_BASE_URL / INSURANCE_BASE_URL: required in .env (no in-code defaults); validated at app startup.
DMS_BASE_URL = (os.getenv("DMS_BASE_URL") or "").strip().rstrip("/")
DMS_LOGIN_USER = os.getenv("DMS_LOGIN_USER", "demo")
DMS_LOGIN_PASSWORD = os.getenv("DMS_LOGIN_PASSWORD", "demo")
# Run browser visible (headed) so user sees DMS page and automation. Set to "false" for headless.
DMS_PLAYWRIGHT_HEADED = os.getenv("DMS_PLAYWRIGHT_HEADED", "true").lower() in ("1", "true", "yes")
# When true, do not auto-close Playwright browser after automation.
# Useful for operator inspection/debugging (server keeps the session alive).
PLAYWRIGHT_KEEP_OPEN = os.getenv("PLAYWRIGHT_KEEP_OPEN", "false").lower() in ("1", "true", "yes")

# Vahan (dummy or real) base URL for Playwright RTO registration step after DMS.
VAHAN_BASE_URL = (os.getenv("VAHAN_BASE_URL") or "").strip().rstrip("/")

# Insurance (dummy or real) base URL for Playwright insurance fill step.
INSURANCE_BASE_URL = (os.getenv("INSURANCE_BASE_URL") or "").strip().rstrip("/")


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

