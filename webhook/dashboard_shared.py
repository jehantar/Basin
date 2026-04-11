"""Shared utilities for Basin dashboard modules."""

from datetime import date, datetime, timezone, timedelta

from fastapi import HTTPException

MAX_DATE_SPAN_DAYS = 5 * 365  # 5 years


def _parse_date_range(
    start: str | None,
    end: str | None,
    max_days: int = MAX_DATE_SPAN_DAYS,
) -> tuple[date, date]:
    """Parse and validate date range query params.

    Defaults to 6 months ending today if omitted.
    Raises HTTPException(400) on invalid input.
    """
    today = date.today()

    if end is not None:
        try:
            end_date = date.fromisoformat(end)
        except ValueError:
            raise HTTPException(400, detail={"code": 400, "message": f"Invalid end date: {end}", "hint": "Use YYYY-MM-DD format"})
    else:
        end_date = today

    if start is not None:
        try:
            start_date = date.fromisoformat(start)
        except ValueError:
            raise HTTPException(400, detail={"code": 400, "message": f"Invalid start date: {start}", "hint": "Use YYYY-MM-DD format"})
    else:
        start_date = end_date - timedelta(days=183)

    if start_date > end_date:
        raise HTTPException(400, detail={"code": 400, "message": "start must be before end", "hint": "Provide dates in YYYY-MM-DD format with start <= end"})

    if (end_date - start_date).days > max_days:
        raise HTTPException(400, detail={"code": 400, "message": f"Date range exceeds {max_days} days maximum", "hint": "Use a shorter date range"})

    return start_date, end_date


def _response_metadata(start_date: date, end_date: date) -> dict:
    """Common metadata included in every dashboard API response."""
    return {
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
        "timezone": "America/Los_Angeles",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
