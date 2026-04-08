"""
Subdealer challan: merge ``dealer_ref`` (``to_dealer_id``) into ``dms_values`` for Siebel order creation.

Retail ``prepare_customer`` stays in ``hero_dms_prepare_customer``; this module is challan-only.
"""

from __future__ import annotations

from typing import Any

from app.db import get_connection
from app.repositories.dealer_ref import DealerRefRepository

# Dummy mobile so My Orders search does not match a real retail contact (product rule).
CHALLAN_DUMMY_MOBILE = "0000000000"


def prepare_customer_for_challan(
    dms_values: dict[str, Any],
    *,
    to_dealer_id: int,
    from_dealer_id: int | None = None,
) -> None:
    """
    Load ``dealer_ref`` for ``to_dealer_id`` and set Siebel-facing keys on ``dms_values`` in place.

    Sets ``hero_dms_flow``, dummy ``mobile_phone``, name/address/pin from dealer, Network fields,
    and challan Comments text when ``from_dealer_id`` is set.
    """
    tid = int(to_dealer_id)
    with get_connection() as conn:
        row = DealerRefRepository.get_by_id(conn, tid)
    if not row:
        raise ValueError(f"dealer_ref not found for to_dealer_id={tid}")

    name = (row.get("dealer_name") or "").strip() or "Subdealer"
    parts = name.split(None, 1)
    first = parts[0][:80] if parts else "Subdealer"
    last = parts[1][:80] if len(parts) > 1 else ""

    addr = (row.get("address") or "").strip()
    pin = (str(row.get("pin") or "").strip()[:6] or "000000")
    city = (row.get("city") or "").strip()
    state = (row.get("state") or "").strip()

    dms_values["hero_dms_flow"] = "add_subdealer_challan"
    dms_values["mobile_phone"] = CHALLAN_DUMMY_MOBILE
    dms_values["first_name"] = first
    dms_values["last_name"] = last
    dms_values["customer_name"] = name
    dms_values["address_line_1"] = addr or name
    dms_values["pin_code"] = pin
    dms_values["state"] = state or ""
    dms_values["city"] = city
    dms_values["to_dealer_id"] = tid
    dms_values["network_dealer_name"] = name
    dms_values["network_pin_code"] = pin
    dms_values["financier_name"] = ""

    if from_dealer_id is not None:
        dms_values["challan_comments_text"] = f"From {int(from_dealer_id)}. Helmet credited"
    else:
        dms_values["challan_comments_text"] = "Helmet credited"
