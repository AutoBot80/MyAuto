from fastapi import APIRouter, HTTPException

from app.db import get_connection
from app.repositories.dealer_ref import DealerRefRepository

router = APIRouter(prefix="/dealers", tags=["dealers"])


@router.get("/{dealer_id:int}")
def get_dealer(dealer_id: int) -> dict:
    """Return dealer by id. Used by client to show dealer name in header."""
    with get_connection() as conn:
        row = DealerRefRepository.get_by_id(conn, dealer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return dict(row)
