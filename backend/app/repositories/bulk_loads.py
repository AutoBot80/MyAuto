"""Data access for the bulk-loads hot/archive job model."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import uuid4


class BulkLoadsRepository:
    TABLE_NAME = "bulk_loads"
    ARCHIVE_TABLE_NAME = "bulk_loads_archive"
    HOT_COLUMNS = [
        "id",
        "job_id",
        "parent_job_id",
        "subfolder",
        "file_name",
        "mobile",
        "name",
        "folder_path",
        "result_folder",
        "status",
        "job_status",
        "processing_stage",
        "source_path",
        "source_token",
        "attempt_count",
        "leased_until",
        "worker_id",
        "error_code",
        "error_message",
        "action_taken",
        "dealer_id",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ]

    @staticmethod
    def ensure_table(conn) -> None:
        with conn.cursor() as cur:
            BulkLoadsRepository._ensure_hot_table(cur)
            BulkLoadsRepository._ensure_archive_table(cur)

    @staticmethod
    def _ensure_hot_table(cur) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {BulkLoadsRepository.TABLE_NAME} (
                id SERIAL PRIMARY KEY,
                subfolder VARCHAR(128) NOT NULL,
                file_name VARCHAR(256),
                mobile VARCHAR(16),
                name VARCHAR(128),
                folder_path VARCHAR(512),
                status VARCHAR(32) NOT NULL DEFAULT 'Processing',
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        BulkLoadsRepository._ensure_common_columns(cur, BulkLoadsRepository.TABLE_NAME)
        BulkLoadsRepository._ensure_hot_indexes(cur, BulkLoadsRepository.TABLE_NAME)

    @staticmethod
    def _ensure_archive_table(cur) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {BulkLoadsRepository.ARCHIVE_TABLE_NAME} (
                LIKE {BulkLoadsRepository.TABLE_NAME} INCLUDING DEFAULTS
            )
            """
        )
        BulkLoadsRepository._ensure_common_columns(cur, BulkLoadsRepository.ARCHIVE_TABLE_NAME)
        cur.execute(
            f"ALTER TABLE {BulkLoadsRepository.ARCHIVE_TABLE_NAME} ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
        )
        cur.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_bulk_loads_archive_job_id ON {BulkLoadsRepository.ARCHIVE_TABLE_NAME} (job_id)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_archive_dealer_created ON {BulkLoadsRepository.ARCHIVE_TABLE_NAME} (dealer_id, created_at DESC)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_archive_status_created ON {BulkLoadsRepository.ARCHIVE_TABLE_NAME} (dealer_id, status, created_at DESC)"
        )

    @staticmethod
    def _ensure_common_columns(cur, table_name: str) -> None:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS job_id VARCHAR(64)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS parent_job_id VARCHAR(64)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS result_folder VARCHAR(512)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS action_taken BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS dealer_id INTEGER")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS job_status VARCHAR(32) NOT NULL DEFAULT 'received'")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS processing_stage VARCHAR(64)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_path VARCHAR(1024)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_token VARCHAR(512)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS worker_id VARCHAR(128)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS error_code VARCHAR(64)")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ")
        cur.execute(f"UPDATE {table_name} SET job_id = CONCAT('legacy-', id) WHERE job_id IS NULL")

    @staticmethod
    def _ensure_hot_indexes(cur, table_name: str) -> None:
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_created_at ON {table_name} (created_at DESC)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_status ON {table_name} (status)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_id ON {table_name} (dealer_id)"
        )
        cur.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_bulk_loads_job_id ON {table_name} (job_id)"
        )
        cur.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_bulk_loads_dealer_source_token ON {table_name} (dealer_id, source_token) WHERE source_token IS NOT NULL"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_created_at_desc ON {table_name} (dealer_id, created_at DESC)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_status_created_at_desc ON {table_name} (dealer_id, status, created_at DESC)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_job_status_created_at_desc ON {table_name} (job_status, created_at DESC)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_bulk_loads_leased_until ON {table_name} (leased_until)"
        )
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_bulk_loads_unresolved_hot
            ON {table_name} (dealer_id, updated_at DESC)
            WHERE status IN ('Processing', 'Error', 'Rejected')
            """
        )

    @staticmethod
    def _select_columns_sql() -> str:
        return ", ".join(BulkLoadsRepository.HOT_COLUMNS)

    @staticmethod
    def _terminal_statuses() -> tuple[str, ...]:
        return ("success", "error", "rejected", "archived")

    @staticmethod
    def _parse_date_filters(date_from: str | None, date_to: str | None) -> tuple[list[str], list]:
        conditions: list[str] = []
        params: list = []
        if date_from:
            try:
                start_date = datetime.strptime(date_from.strip(), "%d-%m-%Y").date()
                start_at = datetime.combine(start_date, time.min)
                conditions.append("created_at >= %s")
                params.append(start_at)
            except ValueError:
                pass
        if date_to:
            try:
                end_date = datetime.strptime(date_to.strip(), "%d-%m-%Y").date() + timedelta(days=1)
                end_at = datetime.combine(end_date, time.min)
                conditions.append("created_at < %s")
                params.append(end_at)
            except ValueError:
                pass
        return conditions, params

    @staticmethod
    def create_job(
        conn,
        *,
        job_id: str | None = None,
        parent_job_id: str | None = None,
        subfolder: str,
        file_name: str | None = None,
        mobile: str | None = None,
        name: str | None = None,
        folder_path: str | None = None,
        source_path: str | None = None,
        source_token: str | None = None,
        status: str = "Processing",
        job_status: str = "received",
        processing_stage: str = "INGEST",
        error_message: str | None = None,
        dealer_id: int | None = None,
    ) -> dict:
        created_job_id = job_id or uuid4().hex
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {BulkLoadsRepository.TABLE_NAME} (
                    job_id, parent_job_id, subfolder, file_name, mobile, name, folder_path,
                    source_path, source_token, status, job_status, processing_stage,
                    error_message, dealer_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING {BulkLoadsRepository._select_columns_sql()}
                """,
                (
                    created_job_id,
                    parent_job_id,
                    subfolder,
                    file_name,
                    mobile,
                    name,
                    folder_path,
                    source_path,
                    source_token,
                    status,
                    job_status,
                    processing_stage,
                    error_message,
                    dealer_id,
                ),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            if source_token is None:
                cur.execute(
                    f"""
                    SELECT {BulkLoadsRepository._select_columns_sql()}
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE job_id = %s
                    """,
                    (created_job_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {BulkLoadsRepository._select_columns_sql()}
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE dealer_id = %s AND source_token = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (dealer_id, source_token),
                )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Failed to create or look up bulk job row")
            return dict(row)

    @staticmethod
    def insert(
        conn,
        subfolder: str,
        file_name: str | None = None,
        mobile: str | None = None,
        name: str | None = None,
        folder_path: str | None = None,
        status: str = "Processing",
        error_message: str | None = None,
        dealer_id: int | None = None,
    ) -> dict:
        return BulkLoadsRepository.create_job(
            conn,
            subfolder=subfolder,
            file_name=file_name,
            mobile=mobile,
            name=name,
            folder_path=folder_path,
            status=status,
            job_status="processing" if status == "Processing" else status.lower(),
            processing_stage="LEGACY_INLINE",
            error_message=error_message,
            dealer_id=dealer_id,
        )

    @staticmethod
    def update_source_path(conn, job_id: str, source_path: str, folder_path: str | None = None) -> None:
        with conn.cursor() as cur:
            updates = ["source_path = %s", "updated_at = NOW()"]
            params: list = [source_path]
            if folder_path is not None:
                updates.append("folder_path = %s")
                params.append(folder_path)
            params.append(job_id)
            cur.execute(
                f"UPDATE {BulkLoadsRepository.TABLE_NAME} SET {', '.join(updates)} WHERE job_id = %s",
                params,
            )

    @staticmethod
    def mark_queued(conn, job_id: str, processing_stage: str = "QUEUED") -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {BulkLoadsRepository.TABLE_NAME}
                SET status = 'Processing',
                    job_status = 'queued',
                    processing_stage = %s,
                    leased_until = NULL,
                    worker_id = NULL,
                    updated_at = NOW()
                WHERE job_id = %s
                """,
                (processing_stage, job_id),
            )

    @staticmethod
    def mark_retry_pending(conn, job_id: str, error_message: str | None = None, error_code: str | None = None) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {BulkLoadsRepository.TABLE_NAME}
                SET status = 'Processing',
                    job_status = 'retry_pending',
                    processing_stage = 'RETRY_PENDING',
                    error_message = %s,
                    error_code = %s,
                    leased_until = NULL,
                    worker_id = NULL,
                    updated_at = NOW()
                WHERE job_id = %s
                """,
                (error_message, error_code, job_id),
            )

    @staticmethod
    def update_stage(conn, job_id: str, processing_stage: str, error_message: str | None = None) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {BulkLoadsRepository.TABLE_NAME}
                SET status = 'Processing',
                    job_status = 'processing',
                    processing_stage = %s,
                    error_message = COALESCE(%s, error_message),
                    updated_at = NOW()
                WHERE job_id = %s
                """,
                (processing_stage, error_message, job_id),
            )

    @staticmethod
    def lease_job(conn, job_id: str, worker_id: str, lease_seconds: int) -> dict | None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {BulkLoadsRepository.TABLE_NAME}
                SET status = 'Processing',
                    job_status = 'processing',
                    processing_stage = CASE
                        WHEN COALESCE(processing_stage, '') IN ('', 'INGEST', 'QUEUED', 'RETRY_PENDING') THEN 'PRE_OCR'
                        ELSE processing_stage
                    END,
                    attempt_count = attempt_count + 1,
                    leased_until = NOW() + make_interval(secs => %s),
                    worker_id = %s,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE job_id = %s
                  AND (
                    job_status IN ('received', 'queued', 'retry_pending')
                    OR (job_status = 'processing' AND (leased_until IS NULL OR leased_until < NOW()))
                  )
                RETURNING {BulkLoadsRepository._select_columns_sql()}
                """,
                (lease_seconds, worker_id, job_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def complete_job(
        conn,
        *,
        job_id: str,
        status: str,
        job_status: str | None = None,
        processing_stage: str | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        mobile: str | None = None,
        name: str | None = None,
        folder_path: str | None = None,
        subfolder: str | None = None,
        result_folder: str | None = None,
    ) -> None:
        terminal_job_status = job_status or status.lower()
        with conn.cursor() as cur:
            updates = [
                "status = %s",
                "job_status = %s",
                "processing_stage = %s",
                "error_message = %s",
                "error_code = %s",
                "leased_until = NULL",
                "worker_id = NULL",
                "finished_at = NOW()",
                "updated_at = NOW()",
            ]
            params: list = [
                status,
                terminal_job_status,
                processing_stage or ("COMPLETE" if status == "Success" else status.upper()),
                error_message,
                error_code,
            ]
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
            params.append(job_id)
            cur.execute(
                f"UPDATE {BulkLoadsRepository.TABLE_NAME} SET {', '.join(updates)} WHERE job_id = %s",
                params,
            )

    @staticmethod
    def update_status(
        conn,
        id: int,
        status: str,
        error_message: str | None = None,
        mobile: str | None = None,
        name: str | None = None,
        folder_path: str | None = None,
        subfolder: str | None = None,
        result_folder: str | None = None,
        error_code: str | None = None,
        job_status: str | None = None,
        processing_stage: str | None = None,
    ) -> None:
        with conn.cursor() as cur:
            terminal = (job_status or status.lower()) in BulkLoadsRepository._terminal_statuses()
            updates: list[str] = [
                "status = %s",
                "job_status = %s",
                "processing_stage = %s",
                "error_message = %s",
                "error_code = %s",
                "updated_at = NOW()",
            ]
            params: list = [
                status,
                job_status or status.lower(),
                processing_stage or ("COMPLETE" if status == "Success" else status.upper()),
                error_message,
                error_code,
            ]
            if terminal:
                updates.extend(["leased_until = NULL", "worker_id = NULL", "finished_at = COALESCE(finished_at, NOW())"])
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
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {BulkLoadsRepository.TABLE_NAME} SET result_folder = %s, updated_at = NOW() WHERE id = %s",
                (result_folder, id),
            )

    @staticmethod
    def list_publishable_jobs(conn, dealer_id: int | None = None, limit: int = 100) -> list[dict]:
        conditions = ["job_status IN ('received', 'retry_pending')", "source_path IS NOT NULL"]
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        params.append(limit)
        where = " AND ".join(conditions)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {BulkLoadsRepository._select_columns_sql()}
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT %s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def list_runnable_jobs(conn, dealer_id: int | None = None, limit: int = 10) -> list[dict]:
        conditions = [
            "status = 'Processing'",
            """
            (
                job_status IN ('received', 'queued', 'retry_pending')
                OR (job_status = 'processing' AND (leased_until IS NULL OR leased_until < NOW()))
            )
            """.strip(),
        ]
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        params.append(limit)
        where = " AND ".join(conditions)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {BulkLoadsRepository._select_columns_sql()}
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT %s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def clear_for_dealer(conn, dealer_id: int | None = None) -> int:
        total = 0
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"DELETE FROM {BulkLoadsRepository.ARCHIVE_TABLE_NAME} WHERE dealer_id IS NULL OR dealer_id = %s",
                    (dealer_id,),
                )
                total += cur.rowcount
                cur.execute(
                    f"DELETE FROM {BulkLoadsRepository.TABLE_NAME} WHERE dealer_id IS NULL OR dealer_id = %s",
                    (dealer_id,),
                )
            else:
                cur.execute(f"DELETE FROM {BulkLoadsRepository.ARCHIVE_TABLE_NAME}")
                total += cur.rowcount
                cur.execute(f"DELETE FROM {BulkLoadsRepository.TABLE_NAME}")
            total += cur.rowcount
        return total

    @staticmethod
    def reset_stale_processing(conn, stale_sec: int = 60, dealer_id: int | None = None) -> int:
        with conn.cursor() as cur:
            conditions = [
                "status = 'Processing'",
                """
                (
                    (job_status = 'processing' AND (leased_until IS NOT NULL AND leased_until < NOW()))
                    OR updated_at < NOW() - make_interval(secs => %s)
                )
                """.strip(),
            ]
            params: list = [stale_sec]
            if dealer_id is not None:
                conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
                params.append(dealer_id)
            where = " AND ".join(conditions)
            cur.execute(
                f"""
                UPDATE {BulkLoadsRepository.TABLE_NAME}
                SET job_status = 'retry_pending',
                    processing_stage = 'RETRY_PENDING',
                    leased_until = NULL,
                    worker_id = NULL,
                    error_message = COALESCE(error_message, 'Reset stale lease for retry'),
                    updated_at = NOW()
                WHERE {where}
                """,
                params,
            )
            return cur.rowcount

    @staticmethod
    def set_action_taken(conn, id: int, action_taken: bool = True, dealer_id: int | None = None) -> bool:
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
    def get_by_job_id(conn, job_id: str) -> dict | None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {BulkLoadsRepository._select_columns_sql()}
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_id(conn, id: int, dealer_id: int | None = None) -> dict | None:
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    SELECT {BulkLoadsRepository._select_columns_sql()}
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE id = %s AND (dealer_id IS NULL OR dealer_id = %s)
                    """,
                    (id, dealer_id),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {BulkLoadsRepository._select_columns_sql()}
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
        date_conditions, date_params = BulkLoadsRepository._parse_date_filters(date_from, date_to)
        conditions.extend(date_conditions)
        params.extend(date_params)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {BulkLoadsRepository._select_columns_sql()}
                FROM {BulkLoadsRepository.TABLE_NAME}
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def count_by_status(
        conn,
        date_from: str | None = None,
        date_to: str | None = None,
        dealer_id: int | None = None,
    ) -> dict[str, int]:
        conditions: list[str] = []
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        date_conditions, date_params = BulkLoadsRepository._parse_date_filters(date_from, date_to)
        conditions.extend(date_conditions)
        params.extend(date_params)
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
        base = BulkLoadsRepository.count_by_status(conn, date_from=date_from, date_to=date_to, dealer_id=dealer_id)
        conditions: list[str] = [
            "status IN ('Error', 'Rejected')",
            "(action_taken IS NULL OR action_taken = FALSE)",
        ]
        params: list = []
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        date_conditions, date_params = BulkLoadsRepository._parse_date_filters(date_from, date_to)
        conditions.extend(date_conditions)
        params.extend(date_params)
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
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    SELECT COUNT(*) as cnt
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE status IN ('Error', 'Rejected')
                      AND (action_taken IS NULL OR action_taken = FALSE)
                      AND (dealer_id IS NULL OR dealer_id = %s)
                    """,
                    (dealer_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT COUNT(*) as cnt
                    FROM {BulkLoadsRepository.TABLE_NAME}
                    WHERE status IN ('Error', 'Rejected')
                      AND (action_taken IS NULL OR action_taken = FALSE)
                    """
                )
            row = cur.fetchone()
            return int(row.get("cnt") or 0)

    @staticmethod
    def archive_closed_rows(conn, retention_days: int, limit: int = 500, dealer_id: int | None = None) -> int:
        conditions = [
            """
            (
                status = 'Success'
                OR (
                    status IN ('Error', 'Rejected')
                    AND COALESCE(action_taken, FALSE) = TRUE
                )
            )
            """.strip(),
            "updated_at < NOW() - make_interval(days => %s)",
        ]
        params: list = [retention_days]
        if dealer_id is not None:
            conditions.append("(dealer_id IS NULL OR dealer_id = %s)")
            params.append(dealer_id)
        params.append(limit)
        where = " AND ".join(conditions)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE {where}
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                params,
            )
            ids = [row["id"] for row in cur.fetchall()]
            if not ids:
                return 0
            insert_columns = BulkLoadsRepository.HOT_COLUMNS + ["archived_at"]
            select_columns = BulkLoadsRepository.HOT_COLUMNS + ["NOW() AS archived_at"]
            cur.execute(
                f"""
                INSERT INTO {BulkLoadsRepository.ARCHIVE_TABLE_NAME} ({', '.join(insert_columns)})
                SELECT {', '.join(select_columns)}
                FROM {BulkLoadsRepository.TABLE_NAME}
                WHERE id = ANY(%s)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (ids,),
            )
            cur.execute(
                f"DELETE FROM {BulkLoadsRepository.TABLE_NAME} WHERE id = ANY(%s)",
                (ids,),
            )
            return cur.rowcount
