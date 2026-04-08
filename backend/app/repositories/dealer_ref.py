"""Data access for dealer_ref table."""

from app.db import get_connection


class DealerRefRepository:
    TABLE_NAME = "dealer_ref"

    @staticmethod
    def get_by_id(conn, dealer_id: int) -> dict | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.dealer_id, d.dealer_name, d.oem_id, d.address, d.pin, d.city, d.state, d.parent_id, d.phone,
                       d.prefer_insurer,
                       o.oem_name, o.dms_link
                FROM dealer_ref d
                LEFT JOIN oem_ref o ON o.oem_id = d.oem_id
                WHERE d.dealer_id = %s
                """,
                (dealer_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        out = dict(row)
        out["dealer_of"] = out.get("oem_name")  # backward compatibility for client
        return out

    @staticmethod
    def list_by_parent_id(conn, parent_id: int) -> list[dict]:
        """Child dealers where ``dealer_ref.parent_id`` = *parent_id* (subdealers of a parent)."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dealer_id, dealer_name
                FROM dealer_ref
                WHERE parent_id = %s
                ORDER BY dealer_name ASC
                """,
                (int(parent_id),),
            )
            rows = cur.fetchall() or []
        return [dict(r) for r in rows]
