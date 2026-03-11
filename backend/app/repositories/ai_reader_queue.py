from app.db import get_connection


class AiReaderQueueRepository:
    """Data access for ai_reader_queue table. Microservice-friendly: no business logic."""

    TABLE_NAME = "ai_reader_queue"

    @staticmethod
    def ensure_table(conn) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {AiReaderQueueRepository.TABLE_NAME} (
                    id SERIAL PRIMARY KEY,
                    subfolder TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    @staticmethod
    def insert(conn, subfolder: str, filename: str, status: str = "queued") -> dict:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {AiReaderQueueRepository.TABLE_NAME} (subfolder, filename, status)
                VALUES (%s, %s, %s)
                RETURNING id, subfolder, filename, status, created_at
                """,
                (subfolder, filename, status),
            )
            return cur.fetchone()

    @staticmethod
    def list_all(conn, limit: int = 200) -> list[dict]:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, subfolder, filename, status, created_at, updated_at
                FROM {AiReaderQueueRepository.TABLE_NAME}
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
