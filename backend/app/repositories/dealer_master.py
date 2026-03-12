"""Data access for dealer_master table."""

from app.db import get_connection


class DealerMasterRepository:
    TABLE_NAME = "dealer_master"

    @staticmethod
    def get_by_id(conn, dealer_id: int) -> dict | None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT dealer_id, dealer_name, dealer_of, address, pin, city, state, parent_id, phone
                FROM {DealerMasterRepository.TABLE_NAME}
                WHERE dealer_id = %s
                """,
                (dealer_id,),
            )
            return cur.fetchone()
