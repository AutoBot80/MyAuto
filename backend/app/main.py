from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .db import get_connection


app = FastAPI(title="Auto Dealer Server", version="0.1.0")

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


class DealerCreate(BaseModel):
    name: str
    city: str | None = None


@app.post("/dealers")
def create_dealer(payload: DealerCreate):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS dealers (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    city TEXT
                )
                """
            )
            cur.execute(
                "INSERT INTO dealers (name, city) VALUES (%s, %s) RETURNING id, name, city",
                (payload.name, payload.city),
            )
            row = cur.fetchone()
    return row


@app.get("/dealers")
def list_dealers():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS dealers (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    city TEXT
                )
                """
            )
            cur.execute("SELECT id, name, city FROM dealers ORDER BY id")
            rows = cur.fetchall()
    return rows


