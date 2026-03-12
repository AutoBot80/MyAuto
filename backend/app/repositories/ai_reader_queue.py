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
                    document_type VARCHAR(64),
                    classification_confidence REAL,
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
                SELECT id, subfolder, filename, status, document_type, classification_confidence,
                       created_at, updated_at
                FROM {AiReaderQueueRepository.TABLE_NAME}
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()

    @staticmethod
    def get_oldest_queued(conn, filename_contains: str | None = None) -> dict | None:
        """Get oldest queued item. If filename_contains is set (e.g. 'details'), only items whose filename contains it (case-insensitive)."""
        with conn.cursor() as cur:
            if filename_contains:
                cur.execute(
                    f"""
                    SELECT id, subfolder, filename, status, document_type, classification_confidence,
                           created_at, updated_at
                    FROM {AiReaderQueueRepository.TABLE_NAME}
                    WHERE status = 'queued' AND filename ILIKE %s
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (f"%{filename_contains}%",),
                )
            else:
                cur.execute(
                    f"""
                    SELECT id, subfolder, filename, status, document_type, classification_confidence,
                           created_at, updated_at
                    FROM {AiReaderQueueRepository.TABLE_NAME}
                    WHERE status = 'queued'
                    ORDER BY id ASC
                    LIMIT 1
                    """
                )
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def update_classification(
        conn, id: int, document_type: str | None, classification_confidence: float | None
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {AiReaderQueueRepository.TABLE_NAME}
                SET document_type = %s, classification_confidence = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (document_type, classification_confidence, id),
            )

    @staticmethod
    def update_status(conn, id: int, status: str) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {AiReaderQueueRepository.TABLE_NAME}
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (status, id),
            )

    @staticmethod
    def delete_all(conn) -> int:
        """Delete all rows from the queue. Returns number of rows deleted."""
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {AiReaderQueueRepository.TABLE_NAME}")
            return cur.rowcount

    @staticmethod
    def reset_for_reprocess(conn, id: int) -> int:
        """Set row back to queued and clear classification so it can be processed again. Returns 1 if updated, 0 if not found."""
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {AiReaderQueueRepository.TABLE_NAME}
                SET status = 'queued', document_type = NULL, classification_confidence = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (id,),
            )
            return cur.rowcount
