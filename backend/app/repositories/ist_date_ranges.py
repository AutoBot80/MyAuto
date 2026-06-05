"""IST calendar date ranges for Sales Reports (dd-mm-yyyy presets and SQL bounds)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")
_DATE_FMT = "%d-%m-%Y"


def ist_today() -> date:
    return datetime.now(_IST).date()


def ist_yesterday() -> date:
    return ist_today() - timedelta(days=1)


def parse_dd_mm_yyyy(raw: str | None) -> date | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(str(raw).strip(), _DATE_FMT).date()
    except ValueError:
        return None


def format_dd_mm_yyyy(d: date) -> str:
    return d.strftime(_DATE_FMT)


def validate_date_range(date_from: str | None, date_to: str | None) -> tuple[date, date] | None:
    """Return inclusive IST calendar bounds when both dates parse and from <= to."""
    start = parse_dd_mm_yyyy(date_from)
    end = parse_dd_mm_yyyy(date_to)
    if start is None or end is None:
        return None
    if start > end:
        return None
    return start, end


def _current_fy_start(ref: date) -> date:
    """Indian FY starts Apr 1; ref is an IST calendar date."""
    if ref.month >= 4:
        return date(ref.year, 4, 1)
    return date(ref.year - 1, 4, 1)


def preset_bounds(preset: str, *, ref: date | None = None) -> tuple[date, date] | None:
    """
    Resolve Sales Reports presets to inclusive IST calendar bounds.

    Presets: current_month, previous_month, current_fy, previous_fy.
    """
    today = ref or ist_today()
    yesterday = today - timedelta(days=1)
    key = (preset or "").strip().lower()

    if key == "current_month":
        start = date(today.year, today.month, 1)
        return start, yesterday

    if key == "previous_month":
        first_this_month = date(today.year, today.month, 1)
        end_prev = first_this_month - timedelta(days=1)
        start_prev = date(end_prev.year, end_prev.month, 1)
        return start_prev, end_prev

    if key == "current_fy":
        return _current_fy_start(today), yesterday

    if key == "previous_fy":
        curr_start = _current_fy_start(today)
        prev_start = date(curr_start.year - 1, 4, 1)
        prev_end = curr_start - timedelta(days=1)
        return prev_start, prev_end

    return None


def created_at_ist_sql_bounds(start: date, end: date) -> tuple[str, str]:
    """
    SQL fragments for inclusive IST calendar-day filter on ``created_at`` timestamptz.

    Compares ``(created_at AT TIME ZONE 'Asia/Kolkata')::date``.
    """
    start_s = start.isoformat()
    end_s = end.isoformat()
    lower = f"{start_s!r}::date"
    upper = f"{end_s!r}::date"
    return lower, upper
