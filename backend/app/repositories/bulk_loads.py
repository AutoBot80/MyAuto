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
                    file_name VARCHAR(256),
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
            cur.execute(
                f"ALTER TABLE {BulkLoadsRepository.TABLE_NAME} ADD COLUMN IF NOT EXISTS file_name VARCHAR(256)"
            )
            cur.execute(
                f"ALTER TABLE {BulkLoadsRepository.TABLE_NAME} ADD COLUMN IF NOT EXISTS result_folder VARCHAR(512)"
            )

    @staticmethod
    def insert(conn, subfolder: str, file_name: str | None = None, mobile: str | None = None, name: str | None = None, folder_path: str | None = None, status: str = "pending", error_message: str | None = None) -> dict:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {BulkLoadsRepository.TABLE_NAME} (subfolder, file_name, mobile, name, folder_path, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, subfolder, file_name, mobile, name, folder_path, status, error_message, created_at, updated_at
                """,
                (subfolder, file_name, mobile, name, folder_path, status, error_message),
            )
            return dict(cur.fetchone())

    @staticmethod
    def update_status(conn, id: int, status: str, error_message: str | None = None, mobile: str | None = None, name: str | None = None, folder_path: str | None = None, subfolder: str | None = None) -> None:
        with conn.cursor() as cur:
            updates: list[str] = ["status = %s", "error_message = %s", "updated_at = NOW()"]
            params: list = [status, error_message]
            if mobile is not None:
                updates.append("mobile = COALESCE(%s, mobile)")
                params.append(mobile)
            if name is not None:
                updates.append("name = COALESCE(%s, name)")
                params.append(name)
            if folder_path is not None:
                updates.append("folder_path = %s")
                params.append(folder_path)
            if subfolder is not None:
                updates.append("subfolder = %s")
                params.append(subfolder)
            if result_folder is not None:
                updates.append("result_folder = %s")
                params.append(result_folder)
            params.append(id)
            cur.execute(
                f"UPDATE {BulkLoadsRepository.TABLE_NAME} SET {', '.join(updates)} WHERE id = %s",
                params,
            )

    @staticmethod
    def update_result_folder(conn, id: int, result_folder: str) -> None:
        """Update only result_folder for a bulk load record."""
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {BulkLoadsRepository.TABLE_NAME} SET result_folder = %s, updated_at = NOW() WHERE id = %s",
                (result_folder, id),
            )

    @staticmethod
    def get_by_id(conn, id: int) -> dict | None:
        """Get a single bulk load record by id."""
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, subfolder, file_name, mobile, name, folder_path, result_folder, status, error_message, created_at, updated_at
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE id = %s
                """,
                (id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_all(conn, limit: int = 200, status_filter: str | None = None) -> list[dict]:
        with conn.cursor() as cur:
            if status_filter and status_filter.lower() in ("success", "error", "processing"):
                cur.execute(
                    f"""
                    SELECT id, subfolder, file_name, mobile, name, folder_path, result_folder, status, error_message, created_at, updated_at
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
                    SELECT id, subfolder, file_name, mobile, name, folder_path, result_folder, status, error_message, created_at, updated_at
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
