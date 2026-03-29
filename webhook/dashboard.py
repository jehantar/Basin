"""Fitness dashboard -- API endpoints and HTML serving."""

import os
from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session

router = APIRouter()

MAX_DATE_SPAN_DAYS = 5 * 365  # 5 years


def _parse_date_range(
    start: str | None,
    end: str | None,
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

    if (end_date - start_date).days > MAX_DATE_SPAN_DAYS:
        raise HTTPException(400, detail={"code": 400, "message": f"Date range exceeds {MAX_DATE_SPAN_DAYS} days maximum", "hint": "Use a shorter date range"})

    return start_date, end_date


def _response_metadata(start_date: date, end_date: date) -> dict:
    """Common metadata included in every fitness API response."""
    return {
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
        "timezone": "UTC",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/dashboard")
def serve_dashboard():
    """Serve the fitness dashboard HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())


@router.get("/api/fitness/running")
def get_running_data(start: str | None = None, end: str | None = None):
    start_date, end_date = _parse_date_range(start, end)
    return {**_response_metadata(start_date, end_date), "runs": [], "summary": {"total_runs": 0, "avg_speed_mph": None, "latest_speed_mph": None, "latest_pace_min_per_mile": None}}


@router.get("/api/fitness/vo2max")
def get_vo2max_data(start: str | None = None, end: str | None = None):
    start_date, end_date = _parse_date_range(start, end)
    return {**_response_metadata(start_date, end_date), "readings": [], "summary": {"latest": None, "peak": None, "peak_date": None}}


@router.get("/api/fitness/strength")
def get_strength_data(
    start: str | None = None,
    end: str | None = None,
    exercise: str | None = None,
):
    start_date, end_date = _parse_date_range(start, end)
    return {**_response_metadata(start_date, end_date), "exercises": [], "sets": [], "prs": []}
