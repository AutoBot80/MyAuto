from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import get_connection
from app.repositories import dealer_dashboard as dealer_dash_repo
from app.repositories.dealer_ref import DealerRefRepository
from app.security.deps import get_principal
from app.security.principal import Principal

router = APIRouter(prefix="/dealers", tags=["dealers"])


def _ensure_dealer_access(principal: Principal, dealer_id: int) -> None:
    if not principal.admin and int(dealer_id) != int(principal.dealer_id):
        raise HTTPException(status_code=403, detail="Access denied for this dealer")


class DealerDashboardSummaryResponse(BaseModel):
    timezone_label: str = Field(default="Asia/Kolkata (IST)")
    days: list[str] = Field(..., description="Seven YYYY-MM-DD IST dates, oldest first")
    rto_queued_count: int
    counter_sales_counts: list[int] = Field(..., description="Seven counts for counter sales")
    is_principal_dealer: bool
    subdealer_sales_counts: list[int] | None = Field(
        default=None,
        description="Seven counts (subdealers only); null when logged-in dealer is a child",
    )
    subdealer_challan_counts: list[int] | None = Field(
        default=None,
        description="Seven challan header counts; null when logged-in dealer is a child",
    )


class SubdealerSalesMatrixRow(BaseModel):
    dealer_id: int
    dealer_name: str
    counts: list[int]


class SubdealerSalesMatrixResponse(BaseModel):
    timezone_label: str = Field(default="Asia/Kolkata (IST)")
    days: list[str]
    rows: list[SubdealerSalesMatrixRow]


class ChallansByIstDayResponse(BaseModel):
    timezone_label: str = Field(default="Asia/Kolkata (IST)")
    ist_date: str
    rows: list[dict]


class ChallansRecentListResponse(BaseModel):
    timezone_label: str = Field(default="Asia/Kolkata (IST)")
    limit: int
    rows: list[dict]


class ChallansFilteredListResponse(BaseModel):
    timezone_label: str = Field(default="Asia/Kolkata (IST)")
    days: int
    ist_start: str
    ist_end: str
    dealer_to_id: int | None = None
    rows: list[dict]


@router.get("/by-parent/{parent_dealer_id:int}")
def list_dealers_by_parent(
    parent_dealer_id: int,
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    """
    Dealers whose ``dealer_ref.parent_id`` equals *parent_dealer_id* (e.g. subdealers of the logged-in dealer).
    Returns ``dealer_id`` and ``dealer_name`` for dropdowns.
    """
    if not principal.admin and int(parent_dealer_id) != int(principal.dealer_id):
        raise HTTPException(status_code=403, detail="Access denied for this parent dealer")
    with get_connection() as conn:
        rows = DealerRefRepository.list_by_parent_id(conn, parent_dealer_id)
    return rows


@router.get("/{dealer_id:int}/dashboard/summary", response_model=DealerDashboardSummaryResponse)
def get_dealer_dashboard_summary(
    dealer_id: int,
    principal: Principal = Depends(get_principal),
) -> DealerDashboardSummaryResponse:
    """IST rolling 7 days: RTO Queued count, counter sales; principal-only subdealer sales + challans."""
    _ensure_dealer_access(principal, dealer_id)
    days, day_strs, start_s, end_s = dealer_dash_repo.ist_last_7_days()
    rto_n = dealer_dash_repo.count_rto_queued_for_dealer(dealer_id)
    counter_raw = dealer_dash_repo.counter_sales_buckets(dealer_id, start_s, end_s)
    counter_counts = dealer_dash_repo.pivot_bucket_counts(counter_raw, days)
    with get_connection() as conn:
        drow = DealerRefRepository.get_by_id(conn, dealer_id)
    if not drow:
        raise HTTPException(status_code=404, detail="Dealer not found")
    is_principal = drow.get("parent_id") is None
    sub_sales = None
    sub_chall = None
    if is_principal:
        sub_sales = dealer_dash_repo.pivot_bucket_counts(
            dealer_dash_repo.subdealer_sales_total_buckets(dealer_id, start_s, end_s),
            days,
        )
        sub_chall = dealer_dash_repo.pivot_bucket_counts(
            dealer_dash_repo.subdealer_challan_buckets(dealer_id, start_s, end_s),
            days,
        )
    return DealerDashboardSummaryResponse(
        timezone_label="Asia/Kolkata (IST)",
        days=day_strs,
        rto_queued_count=rto_n,
        counter_sales_counts=counter_counts,
        is_principal_dealer=is_principal,
        subdealer_sales_counts=sub_sales,
        subdealer_challan_counts=sub_chall,
    )


@router.get("/{dealer_id:int}/dashboard/subdealer-sales-matrix", response_model=SubdealerSalesMatrixResponse)
def get_dealer_dashboard_subdealer_sales_matrix(
    dealer_id: int,
    principal: Principal = Depends(get_principal),
) -> SubdealerSalesMatrixResponse:
    """Subdealer × 7 IST days; rows with zero sales across the window are omitted."""
    _ensure_dealer_access(principal, dealer_id)
    with get_connection() as conn:
        drow = DealerRefRepository.get_by_id(conn, dealer_id)
    if not drow:
        raise HTTPException(status_code=404, detail="Dealer not found")
    if drow.get("parent_id") is not None:
        raise HTTPException(status_code=403, detail="Subdealer sales matrix is only available for principal dealers")
    days, day_strs, start_s, end_s = dealer_dash_repo.ist_last_7_days()
    raw = dealer_dash_repo.subdealer_sales_by_child_buckets(dealer_id, start_s, end_s)
    pivoted = dealer_dash_repo.pivot_subdealer_sales_rows(raw, days)
    rows = [
        SubdealerSalesMatrixRow(dealer_id=int(r["dealer_id"]), dealer_name=str(r["dealer_name"]), counts=list(r["counts"]))
        for r in pivoted
    ]
    return SubdealerSalesMatrixResponse(timezone_label="Asia/Kolkata (IST)", days=day_strs, rows=rows)


@router.get("/{dealer_id:int}/dashboard/challans-by-ist-day", response_model=ChallansByIstDayResponse)
def get_dealer_dashboard_challans_by_ist_day(
    dealer_id: int,
    ist_date: str = Query(..., alias="date", description="IST calendar date YYYY-MM-DD"),
    principal: Principal = Depends(get_principal),
) -> ChallansByIstDayResponse:
    """Committed challan headers for one IST day (``dealer_from`` = dealer)."""
    _ensure_dealer_access(principal, dealer_id)
    with get_connection() as conn:
        drow = DealerRefRepository.get_by_id(conn, dealer_id)
    if not drow:
        raise HTTPException(status_code=404, detail="Dealer not found")
    if drow.get("parent_id") is not None:
        raise HTTPException(status_code=403, detail="Challan drill-down is only available for principal dealers")
    try:
        ist_day = date.fromisoformat(ist_date.strip()[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date (expected YYYY-MM-DD)")
    _, day_strs, _, _ = dealer_dash_repo.ist_last_7_days()
    if ist_day.isoformat() not in day_strs:
        raise HTTPException(status_code=400, detail="Date must fall within the current 7-day IST window")
    rows = dealer_dash_repo.list_challan_masters_for_dealer_ist_day(dealer_id, ist_day)
    return ChallansByIstDayResponse(
        timezone_label="Asia/Kolkata (IST)",
        ist_date=ist_day.isoformat(),
        rows=rows,
    )


@router.get("/{dealer_id:int}/dashboard/challans-filtered", response_model=ChallansFilteredListResponse)
def get_dealer_dashboard_challans_filtered(
    dealer_id: int,
    days: int = Query(7, description="IST window length: 7, 15, or 30"),
    dealer_to_id: int | None = Query(None, description="Optional ``dealer_to``; must be a subdealer of this dealer"),
    principal: Principal = Depends(get_principal),
) -> ChallansFilteredListResponse:
    """Committed challan headers in an IST calendar window, optionally filtered to one subdealer."""
    _ensure_dealer_access(principal, dealer_id)
    if int(days) not in (7, 15, 30):
        raise HTTPException(status_code=400, detail="days must be 7, 15, or 30")
    with get_connection() as conn:
        drow = DealerRefRepository.get_by_id(conn, dealer_id)
    if not drow:
        raise HTTPException(status_code=404, detail="Dealer not found")
    if drow.get("parent_id") is not None:
        raise HTTPException(status_code=403, detail="Challan list is only available for principal dealers")
    if dealer_to_id is not None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM dealer_ref WHERE dealer_id = %s AND parent_id = %s",
                    (int(dealer_to_id), int(dealer_id)),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=400,
                        detail="dealer_to_id is not a subdealer of this dealer",
                    )
    ist_start, ist_end = dealer_dash_repo.ist_calendar_window_last_n_days(int(days))
    rows = dealer_dash_repo.list_challan_masters_for_dealer_window(
        dealer_id,
        ist_start,
        ist_end,
        int(dealer_to_id) if dealer_to_id is not None else None,
    )
    return ChallansFilteredListResponse(
        timezone_label="Asia/Kolkata (IST)",
        days=int(days),
        ist_start=ist_start.isoformat(),
        ist_end=ist_end.isoformat(),
        dealer_to_id=int(dealer_to_id) if dealer_to_id is not None else None,
        rows=rows,
    )


@router.get("/{dealer_id:int}/dashboard/challans-recent", response_model=ChallansRecentListResponse)
def get_dealer_dashboard_challans_recent(
    dealer_id: int,
    limit: int = Query(5, ge=1, le=50),
    principal: Principal = Depends(get_principal),
) -> ChallansRecentListResponse:
    """Latest committed challan headers for this principal (``dealer_from``)."""
    _ensure_dealer_access(principal, dealer_id)
    with get_connection() as conn:
        drow = DealerRefRepository.get_by_id(conn, dealer_id)
    if not drow:
        raise HTTPException(status_code=404, detail="Dealer not found")
    if drow.get("parent_id") is not None:
        raise HTTPException(status_code=403, detail="Challan list is only available for principal dealers")
    rows = dealer_dash_repo.list_recent_challan_masters_for_dealer(dealer_id, limit=int(limit))
    return ChallansRecentListResponse(timezone_label="Asia/Kolkata (IST)", limit=int(limit), rows=rows)


@router.get("/{dealer_id:int}")
def get_dealer(
    dealer_id: int,
    principal: Principal = Depends(get_principal),
) -> dict:
    """Return dealer by id. Used by client to show dealer name in header."""
    if not principal.admin and int(dealer_id) != int(principal.dealer_id):
        raise HTTPException(status_code=403, detail="Access denied for this dealer")
    with get_connection() as conn:
        row = DealerRefRepository.get_by_id(conn, dealer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return dict(row)
