from psycopg2 import sql
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from app.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])

PRESERVED_TABLES = ("dealer_ref", "oem_ref", "oem_service_schedule", "subdealer_discount_master")
CONFIRMATION_TEXT = "DELETE ALL DATA"


class ResetAllDataRequest(BaseModel):
    confirmation: str


@router.post("/reset-all-data")
def reset_all_data(payload: ResetAllDataRequest) -> dict:
    """Delete all public-table data except reference tables in PRESERVED_TABLES."""
    if payload.confirmation != CONFIRMATION_TEXT:
        raise HTTPException(status_code=400, detail="Invalid confirmation text")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                  AND table_name <> ALL(%s::text[])
                ORDER BY table_name
                """,
                (list(PRESERVED_TABLES),),
            )
            table_names = [row["table_name"] for row in cur.fetchall()]

            if table_names:
                truncate_sql = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(table_name) for table_name in table_names)
                )
                cur.execute(truncate_sql)

        conn.commit()
        return {
            "ok": True,
            "message": f"Deleted data from {len(table_names)} table(s).",
            "truncated_count": len(table_names),
            "truncated_tables": table_names,
            "preserved_tables": list(PRESERVED_TABLES),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
