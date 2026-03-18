"""Data access for bulk_loads table."""

from datetime import datetime

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
            cur.execute(
                f"ALTER TABLE {BulkLoadsRepository.TABLE_NAME} ADD COLUMN IF NOT EXISTS action_taken BOOLEAN NOT NULL DEFAULT FALSE"
            )
            cur.execute(
                f"ALTER TABLE {BulkLoadsRepository.TABLE_NAME} ADD COLUMN IF NOT EXISTS dealer_id INTEGER"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_id ON {BulkLoadsRepository.TABLE_NAME} (dealer_id)"
            )

    @staticmethod
    def insert(conn, subfolder: str, file_name: str | None = None, mobile: str | None = None, name: str | None = None, folder_path: str | None = None, status: str = "pending", error_message: str | None = None, dealer_id: int | None = None) -> dict:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {BulkLoadsRepository.TABLE_NAME} (subfolder, file_name, mobile, name, folder_path, status, error_message, dealer_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, subfolder, file_name, mobile, name, folder_path, status, error_message, created_at, updated_at, dealer_id
                """,
                (subfolder, file_name, mobile, name, folder_path, status, error_message, dealer_id),
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
    def reset_stale_processing(conn, stale_sec: int = 60, dealer_id: int | None = None) -> int:
        """Mark Processing rows older than stale_sec as Error. Returns count reset. Unblocks watcher."""
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    UPDATE {BulkLoadsRepository.TABLE_NAME}
                    SET status = 'Error', error_message = COALESCE(error_message, 'Reset: stuck in Processing')
                    WHERE status = 'Processing' AND updated_at < NOW() - make_interval(secs => %s)
                      AND (dealer_id IS NULL OR dealer_id = %s)
                    """,
                    (stale_sec, dealer_id),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE {BulkLoadsRepository.TABLE_NAME}
                    SET status = 'Error', error_message = COALESCE(error_message, 'Reset: stuck in Processing')
                    WHERE status = 'Processing' AND updated_at < NOW() - make_interval(secs => %s)
                    """,
                    (stale_sec,),
                )
            return cur.rowcount

    @staticmethod
    def set_action_taken(conn, id: int, action_taken: bool = True, dealer_id: int | None = None) -> bool:
        """Mark a record as action taken (reprocessed by operator). Only for Error/Rejected. Returns True if updated."""
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    UPDATE {BulkLoadsRepository.TABLE_NAME}
                    SET action_taken = %s, updated_at = NOW()
                    WHERE id = %s AND status IN ('Error', 'Rejected') AND (dealer_id IS NULL OR dealer_id = %s)
                    """,
                    (action_taken, id, dealer_id),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE {BulkLoadsRepository.TABLE_NAME}
                    SET action_taken = %s, updated_at = NOW()
                    WHERE id = %s AND status IN ('Error', 'Rejected')
                    """,
                    (action_taken, id),
                )
            return cur.rowcount > 0

    @staticmethod
    def get_by_id(conn, id: int, dealer_id: int | None = None) -> dict | None:
        """Get a single bulk load record by id. Optionally filter by dealer_id."""
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    SELECT id, subfolder, file_name, mobile, name, folder_path, result_folder, status, error_message, created_at, updated_at, dealer_id
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE id = %s AND (dealer_id IS NULL OR dealer_id = %s)
                    """,
                    (id, dealer_id),
                )
            else:
                cur.execute(
                    f"""
                    SELECT id, subfolder, file_name, mobile, name, folder_path, result_folder, status, error_message, created_at, updated_at, dealer_id
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE id = %s
                    """,
                    (id,),
                )
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_all(
        conn,
        limit: int = 200,
        status_filter: str | None = None,
        status_in: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        dealer_id: int | None = None,
    ) -> list[dict]:
        """List bulk loads. status_filter: single status. status_in: multiple (e.g. Processed). date_from/date_to: dd-mm-yyyy."""
        conditions: list[str] = []
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        if status_filter and status_filter.lower() in ("success", "error", "processing", "rejected"):
            conditions.append("status = %s")
            params.append(status_filter.capitalize())
        elif status_in:
            placeholders = ", ".join("%s" for _ in status_in)
            conditions.append(f"status IN ({placeholders})")
            params.extend(s.capitalize() for s in status_in)
        if date_from:
            try:
                d = datetime.strptime(date_from.strip(), "%d-%m-%Y").date()
                conditions.append("created_at::date >= %s")
                params.append(d)
            except ValueError:
                pass
        if date_to:
            try:
                d = datetime.strptime(date_to.strip(), "%d-%m-%Y").date()
                conditions.append("created_at::date <= %s")
                params.append(d)
            except ValueError:
                pass
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, subfolder, file_name, mobile, name, folder_path, result_folder, status, error_message, action_taken, created_at, updated_at
                FROM {BulkLoadsRepository.TABLE_NAME}
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def count_by_status(
        conn,
        date_from: str | None = None,
        date_to: str | None = None,
        dealer_id: int | None = None,
    ) -> dict[str, int]:
        """Return counts per status within date range. Keys: Success, Error, Processing, Rejected."""
        conditions: list[str] = []
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        if date_from:
            try:
                d = datetime.strptime(date_from.strip(), "%d-%m-%Y").date()
                conditions.append("created_at::date >= %s")
                params.append(d)
            except ValueError:
                pass
        if date_to:
            try:
                d = datetime.strptime(date_to.strip(), "%d-%m-%Y").date()
                conditions.append("created_at::date <= %s")
                params.append(d)
            except ValueError:
                pass
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT status, COUNT(*) as cnt
                FROM {BulkLoadsRepository.TABLE_NAME}
                {where}
                GROUP BY status
                """,
                params,
            )
            rows = cur.fetchall()
        result = {"Success": 0, "Error": 0, "Processing": 0, "Rejected": 0}
        for row in rows:
            status = (row.get("status") or "").capitalize()
            if status in result:
                result[status] = int(row.get("cnt") or 0)
        return result

    @staticmethod
    def count_by_status_pending(
        conn,
        date_from: str | None = None,
        date_to: str | None = None,
        dealer_id: int | None = None,
    ) -> dict[str, int]:
        """Like count_by_status but Error and Rejected exclude action_taken records (for tab display)."""
        base = BulkLoadsRepository.count_by_status(conn, date_from=date_from, date_to=date_to, dealer_id=dealer_id)
        conditions: list[str] = [
            "status IN ('Error', 'Rejected')",
            "(action_taken IS NULL OR action_taken = FALSE)",
        ]
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        if date_from:
            try:
                d = datetime.strptime(date_from.strip(), "%d-%m-%Y").date()
                conditions.append("created_at::date >= %s")
                params.append(d)
            except ValueError:
                pass
        if date_to:
            try:
                d = datetime.strptime(date_to.strip(), "%d-%m-%Y").date()
                conditions.append("created_at::date <= %s")
                params.append(d)
            except ValueError:
                pass
        where = " AND ".join(conditions)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT status, COUNT(*) as cnt
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE {where}
                GROUP BY status
                """,
                params,
            )
            rows = cur.fetchall()
        pending_err = sum(int(r.get("cnt") or 0) for r in rows if (r.get("status") or "").capitalize() == "Error")
        pending_rej = sum(int(r.get("cnt") or 0) for r in rows if (r.get("status") or "").capitalize() == "Rejected")
        return {
            "Success": base["Success"],
            "Error": pending_err,
            "Processing": base["Processing"],
            "Rejected": pending_rej,
        }

    @staticmethod
    def count_pending_attention(conn, dealer_id: int | None = None) -> int:
        """Count Error + Rejected records where action_taken = false (need operator attention)."""
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    SELECT COUNT(*) as cnt
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE status IN ('Error', 'Rejected') AND (action_taken IS NULL OR action_taken = FALSE)
                      AND (dealer_id IS NULL OR dealer_id = %s)
                    """,
                    (dealer_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT COUNT(*) as cnt
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE status IN ('Error', 'Rejected') AND (action_taken IS NULL OR action_taken = FALSE)
                    """
                )
            row = cur.fetchone()
            return int(row.get("cnt") or 0)
