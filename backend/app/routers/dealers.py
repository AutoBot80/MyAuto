from fastapi import APIRouter, HTTPException

from app.db import get_connection
from app.repositories.dealer_ref import DealerRefRepository

router = APIRouter(prefix="/dealers", tags=["dealers"])


@router.get("/by-parent/{parent_dealer_id:int}")
def list_dealers_by_parent(parent_dealer_id: int) -> list[dict]:
    """
    Dealers whose ``dealer_ref.parent_id`` equals *parent_dealer_id* (e.g. subdealers of the logged-in dealer).
    Returns ``dealer_id`` and ``dealer_name`` for dropdowns.
    """
    with get_connection() as conn:
        rows = DealerRefRepository.list_by_parent_id(conn, parent_dealer_id)
    return rows


@router.get("/{dealer_id:int}")
def get_dealer(dealer_id: int) -> dict:
    """Return dealer by id. Used by client to show dealer name in header."""
    with get_connection() as conn:
        row = DealerRefRepository.get_by_id(conn, dealer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return dict(row)
