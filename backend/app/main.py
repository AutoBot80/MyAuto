from pathlib import Path

from datetime import datetime

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from .db import get_connection


app = FastAPI(title="Auto Dealer Server", version="0.1.0")

UPLOADS_DIR = Path(__file__).resolve().parents[2] / "Uploaded scans"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/uploads/scans")
async def upload_scans(
    aadhar_last4: str = Form(...),
    files: list[UploadFile] = File(...),
):
    aadhar_digits = "".join(ch for ch in aadhar_last4 if ch.isdigit())
    if len(aadhar_digits) != 4:
        # Keep it simple for now; client also validates.
        return {"error": "Invalid aadhar. Expected last 4 digits."}

    ddmm = datetime.now().strftime("%d%m")
    subdir = UPLOADS_DIR / f"{aadhar_digits}_{ddmm}"
    subdir.mkdir(parents=True, exist_ok=True)

    saved = []
    queued = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_reader_queue (
                    id SERIAL PRIMARY KEY,
                    subfolder TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            for f in files:
                # Avoid directory traversal; keep only the filename.
                filename = Path(f.filename or "scan").name
                target = subdir / filename

                # If same filename exists, add a suffix.
                if target.exists():
                    stem = target.stem
                    suffix = target.suffix
                    i = 1
                    while True:
                        candidate = subdir / f"{stem} ({i}){suffix}"
                        if not candidate.exists():
                            target = candidate
                            break
                        i += 1

                content = await f.read()
                target.write_bytes(content)
                saved.append(target.name)

                cur.execute(
                    """
                    INSERT INTO ai_reader_queue (subfolder, filename, status)
                    VALUES (%s, %s, 'queued')
                    RETURNING id, subfolder, filename, status, created_at
                    """,
                    (subdir.name, target.name),
                )
                queued.append(cur.fetchone())

    return {
        "saved_count": len(saved),
        "saved_files": saved,
        "saved_to": str(subdir),
        "queued_items": queued,
    }


@app.get("/ai-reader-queue")
def list_ai_reader_queue(limit: int = 200):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_reader_queue (
                    id SERIAL PRIMARY KEY,
                    subfolder TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                SELECT id, subfolder, filename, status, created_at, updated_at
                FROM ai_reader_queue
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()


