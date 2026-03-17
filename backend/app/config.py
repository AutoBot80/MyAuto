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

# DMS fill (Playwright): base URL and login. Used when client calls POST /fill-dms.
DMS_BASE_URL = os.getenv("DMS_BASE_URL", "").rstrip("/")  # e.g. http://127.0.0.1:8000/dummy-dms
DMS_LOGIN_USER = os.getenv("DMS_LOGIN_USER", "demo")
DMS_LOGIN_PASSWORD = os.getenv("DMS_LOGIN_PASSWORD", "demo")
# Run browser visible (headed) so user sees DMS page and automation. Set to "false" for headless.
DMS_PLAYWRIGHT_HEADED = os.getenv("DMS_PLAYWRIGHT_HEADED", "true").lower() in ("1", "true", "yes")

# Vahan (dummy or real) base URL for Playwright RTO registration step after DMS. e.g. http://127.0.0.1:8000/dummy-vaahan
VAHAN_BASE_URL = os.getenv("VAHAN_BASE_URL", "").rstrip("/")

