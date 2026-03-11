from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import UPLOADS_DIR
from app.routers import health_router, uploads_router, ai_reader_queue_router

app = FastAPI(title="Auto Dealer Server", version="0.1.0")

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(uploads_router)
app.include_router(ai_reader_queue_router)
