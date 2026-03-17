"""Data access for bulk_loads table."""

from app.db import get_connection


class BulkLoadsRepository:
    TABLE_NAME = "bulk_loads"

    @staticmethod
    def ensure_table(conn) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {BulkLoadsRepository.TABLE_NAME} (
                    id SERIAL PRIMARY KEY,
                    subfolder VARCHAR(128) NOT NULL,
                    mobile VARCHAR(16),
                    name VARCHAR(128),
                    folder_path VARCHAR(512),
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_created_at ON {BulkLoadsRepository.TABLE_NAME} (created_at DESC)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_status ON {BulkLoadsRepository.TABLE_NAME} (status)"
            )

    @staticmethod
    def insert(conn, subfolder: str, mobile: str | None = None, name: str | None = None, folder_path: str | None = None, status: str = "pending", error_message: str | None = None) -> dict:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {BulkLoadsRepository.TABLE_NAME} (subfolder, mobile, name, folder_path, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, subfolder, mobile, name, folder_path, status, error_message, created_at, updated_at
                """,
                (subfolder, mobile, name, folder_path, status, error_message),
            )
            return dict(cur.fetchone())

    @staticmethod
    def update_status(conn, id: int, status: str, error_message: str | None = None, mobile: str | None = None, name: str | None = None) -> None:
        with conn.cursor() as cur:
            if mobile is not None or name is not None:
                cur.execute(
                    f"""
                    UPDATE {BulkLoadsRepository.TABLE_NAME}
                    SET status = %s, error_message = %s, mobile = COALESCE(%s, mobile), name = COALESCE(%s, name), updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, error_message, mobile, name, id),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE {BulkLoadsRepository.TABLE_NAME}
                    SET status = %s, error_message = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, error_message, id),
                )

    @staticmethod
    def list_all(conn, limit: int = 200, status_filter: str | None = None) -> list[dict]:
        with conn.cursor() as cur:
            if status_filter and status_filter.lower() in ("success", "error"):
                cur.execute(
                    f"""
                    SELECT id, subfolder, mobile, name, folder_path, status, error_message, created_at, updated_at
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status_filter.capitalize(), limit),
                )
            else:
                cur.execute(
                    f"""
                    SELECT id, subfolder, mobile, name, folder_path, status, error_message, created_at, updated_at
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
