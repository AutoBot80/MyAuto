from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import OCR_OUTPUT_DIR, UPLOADS_DIR
from app.routers import (
    health_router,
    uploads_router,
    ai_reader_queue_router,
    vision_router,
    dealers_router,
    textract_router,
    qr_decode_router,
    submit_info_router,
    fill_dms_router,
)

app = FastAPI(title="Auto Dealer Server", version="0.1.0")

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OCR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dummy sites for Playwright automation (DMS / Vaahan); serve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DUMMY_DMS = _PROJECT_ROOT / "dummy-sites" / "dms"
_DUMMY_VAHAN = _PROJECT_ROOT / "dummy-sites" / "vaahan"
if _DUMMY_DMS.is_dir():
    app.mount("/dummy-dms", StaticFiles(directory=str(_DUMMY_DMS), html=True), name="dummy-dms")
if _DUMMY_VAHAN.is_dir():
    app.mount("/dummy-vaahan", StaticFiles(directory=str(_DUMMY_VAHAN), html=True), name="dummy-vaahan")

app.include_router(health_router)
app.include_router(uploads_router)
app.include_router(ai_reader_queue_router)
app.include_router(vision_router)
app.include_router(dealers_router)
app.include_router(textract_router)
app.include_router(qr_decode_router)
app.include_router(submit_info_router)
app.include_router(fill_dms_router)
