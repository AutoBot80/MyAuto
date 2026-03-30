"""Customer search by mobile and/or vehicle plate number."""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import DEALER_ID
from app.db import get_connection
from app.repositories.form_vahan import get_by_customer_vehicle as get_form_vahan_by_customer_vehicle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/customer-search", tags=["customer-search"])


@router.get("/search")
def search_customer(
    mobile: str | None = Query(None, description="Customer mobile number"),
    plate_num: str | None = Query(None, description="Vehicle plate number"),
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """
    Search by customer mobile and/or vehicle plate number.
    Requires at least one. When both provided, customer must match both.
    Identifies customer_id, then fetches from sales_master, vehicle_master, insurance_master.
    """
    mobile_clean = (mobile or "").strip()
    plate_clean = (plate_num or "").strip()
    if not mobile_clean and not plate_clean:
        raise HTTPException(
            status_code=400,
            detail="Provide at least customer mobile or vehicle plate number.",
        )

    by_mobile: set[int] = set()
    by_plate: set[int] = set()
    did = dealer_id if dealer_id is not None else DEALER_ID

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT oem_id FROM dealer_ref WHERE dealer_id = %s", (did,))
            dealer_row = cur.fetchone()
            if not dealer_row:
                raise HTTPException(status_code=404, detail="Dealer not found.")
            oem_id = dealer_row.get("oem_id")
            if oem_id is None:
                raise HTTPException(status_code=400, detail="Dealer has no OEM configured.")
    finally:
        conn.close()
    if mobile_clean:
        try:
            mobile_int = int("".join(c for c in mobile_clean if c.isdigit()))
        except ValueError:
            mobile_int = None
        if mobile_int is not None:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT cm.customer_id
                        FROM customer_master cm
                        JOIN sales_master sm ON sm.customer_id = cm.customer_id
                        JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                        WHERE cm.mobile_number = %s
                          AND dr.oem_id = %s
                        """,
                        (mobile_int, oem_id),
                    )
                    by_mobile = {row["customer_id"] for row in cur.fetchall()}
            finally:
                conn.close()
        elif not plate_clean:
            return {
                "found": False,
                "customer": None,
                "vehicles": [],
                "message": "Invalid mobile number.",
            }

    if plate_clean:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT sm.customer_id
                    FROM sales_master sm
                    JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
                    JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                    WHERE TRIM(COALESCE(vm.plate_num, '')) <> ''
                      AND dr.oem_id = %s
                      AND (LOWER(TRIM(vm.plate_num)) = LOWER(TRIM(%s))
                           OR vm.plate_num ILIKE %s)
                    """,
                    (oem_id, plate_clean, f"%{plate_clean}%"),
                )
                by_plate = {row["customer_id"] for row in cur.fetchall()}
        finally:
            conn.close()

    if mobile_clean and plate_clean:
        customer_ids = by_mobile & by_plate
    else:
        customer_ids = by_mobile | by_plate

    if not customer_ids:
        return {
            "found": False,
            "customer": None,
            "vehicles": [],
            "message": "No customer found for the given criteria.",
        }

    cid = min(customer_ids)

    conn = get_connection()
    try:
        with conn.cursor() as cur:

            # 2) Customer details from customer_master (file_location per sale on sales_master;
            # nominee_gender on insurance_master)
            cur.execute(
                """
                SELECT customer_id, name, mobile_number, address, pin, city, state,
                       date_of_birth, alt_phone_num, profession, financier, marital_status,
                       gender
                FROM customer_master
                WHERE customer_id = %s
                """,
                (cid,),
            )
            cust_row = cur.fetchone()
            if not cust_row:
                return {"found": False, "customer": None, "vehicles": [], "message": "Customer not found."}

            customer = dict(cust_row)
            if customer.get("mobile_number") is not None:
                customer["mobile"] = str(customer["mobile_number"])
            else:
                customer["mobile"] = None

            # 3) Use customer_id to fetch from sales_master (gives customer_id + vehicle_id)
            # 4) Fetch vehicle details from vehicle_master, insurance from insurance_master
            cur.execute(
                """
                SELECT vm.vehicle_id,
                       vm.model,
                       vm.colour,
                       vm.plate_num,
                       vm.chassis,
                       vm.engine,
                       vm.year_of_mfg,
                       sm.file_location,
                       sm.billing_date AS date_of_purchase
                FROM sales_master sm
                JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
                JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                WHERE sm.customer_id = %s
                  AND dr.oem_id = %s
                ORDER BY sm.billing_date DESC
                """,
                (cid, oem_id),
            )
            vehicles = [dict(r) for r in cur.fetchall()]
            for v in vehicles:
                if v.get("date_of_purchase"):
                    v["date_of_purchase"] = v["date_of_purchase"].strftime("%d-%m-%Y")
                else:
                    v["date_of_purchase"] = None

            # Insurance per vehicle (latest by insurance_year)
            vehicle_ids = [v["vehicle_id"] for v in vehicles]
            ins_map: dict[int, dict] = {}
            if vehicle_ids:
                cur.execute(
                    """
                    SELECT DISTINCT ON (vehicle_id) vehicle_id, insurer, policy_num, policy_from, policy_to, nominee_gender
                    FROM insurance_master
                    WHERE customer_id = %s AND vehicle_id = ANY(%s)
                    ORDER BY vehicle_id, insurance_year DESC NULLS LAST
                    """,
                    (cid, vehicle_ids),
                )
                for row in cur.fetchall():
                    r = dict(row)
                    if r.get("policy_from"):
                        r["policy_from"] = r["policy_from"].strftime("%d-%m-%Y")
                    if r.get("policy_to"):
                        r["policy_to"] = r["policy_to"].strftime("%d-%m-%Y")
                    ins_map[r["vehicle_id"]] = r

            return {
                "found": True,
                "customer": customer,
                "vehicles": vehicles,
                "insurance_by_vehicle": ins_map,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("customer_search failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        conn.close()


@router.get("/form-vahan")
def get_form_vahan_row(
    customer_id: int = Query(..., description="Customer ID"),
    vehicle_id: int = Query(..., description="Vehicle ID"),
) -> dict:
    """Return the form_vahan_view row for one customer/vehicle pair."""
    try:
        row = get_form_vahan_by_customer_vehicle(customer_id, vehicle_id)
        if not row:
            return {"found": False, "columns": [], "row": None}
        columns = list(row.keys())
        return {"found": True, "columns": columns, "row": row}
    except Exception as e:
        logger.exception("customer_search form-vahan failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
