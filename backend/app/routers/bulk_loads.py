"""Bulk Loads: list and filter bulk upload processing results."""

import logging
from datetime import datetime

from fastapi import APIRouter, Query

from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bulk-loads", tags=["bulk-loads"])


@router.get("")
def list_bulk_loads(
    status: str | None = Query(None, description="Filter: Success, Error, or both (omit)"),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict]:
    """List bulk loads, sorted latest first. Filter by status if provided."""
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        rows = BulkLoadsRepository.list_all(conn, limit=limit, status_filter=status)
        for r in rows:
            for k in ("created_at", "updated_at"):
                if k in r and isinstance(r[k], datetime):
                    r[k] = r[k].isoformat()
        return rows
    finally:
        conn.close()
