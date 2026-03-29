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

    with get_session() as session:
        speed_rows = session.execute(text("""
            SELECT (recorded_at AT TIME ZONE 'UTC')::date as date,
                   round(avg(value)::numeric, 2) as avg_speed
            FROM healthkit.metrics
            WHERE metric_type = 'running_speed'
              AND (recorded_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            GROUP BY (recorded_at AT TIME ZONE 'UTC')::date
            ORDER BY date
        """), {"start": start_date, "end": end_date}).fetchall()

        power_rows = session.execute(text("""
            SELECT (recorded_at AT TIME ZONE 'UTC')::date as date,
                   round(avg(value)::numeric, 0) as avg_power
            FROM healthkit.metrics
            WHERE metric_type = 'running_power'
              AND (recorded_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            GROUP BY (recorded_at AT TIME ZONE 'UTC')::date
        """), {"start": start_date, "end": end_date}).fetchall()
        power_by_date = {str(r[0]): float(r[1]) for r in power_rows}

        dur_rows = session.execute(text("""
            SELECT (start_time AT TIME ZONE 'UTC')::date as date,
                   round((duration_sec / 60.0)::numeric, 1) as duration_min
            FROM healthkit.workouts
            WHERE workout_type = 'Running'
              AND (start_time AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
        """), {"start": start_date, "end": end_date}).fetchall()
        dur_by_date = {str(r[0]): float(r[1]) for r in dur_rows}

        runs = []
        for row in speed_rows:
            d = str(row[0])
            speed = float(row[1])
            runs.append({
                "date": d,
                "avg_speed_mph": speed,
                "duration_min": dur_by_date.get(d),
                "avg_power": power_by_date.get(d),
            })

        total_runs = len(runs)
        avg_speed = round(sum(r["avg_speed_mph"] for r in runs) / total_runs, 2) if total_runs else None
        latest_speed = runs[-1]["avg_speed_mph"] if runs else None
        latest_pace = _speed_to_pace(latest_speed) if latest_speed else None

        summary = {
            "total_runs": total_runs,
            "avg_speed_mph": avg_speed,
            "latest_speed_mph": latest_speed,
            "latest_pace_min_per_mile": latest_pace,
        }

    return {**_response_metadata(start_date, end_date), "runs": runs, "summary": summary}


def _speed_to_pace(speed_mph: float) -> str:
    """Convert speed in mi/hr to pace as 'mm:ss' per mile."""
    if speed_mph <= 0:
        return "---"
    min_per_mile = 60.0 / speed_mph
    minutes = int(min_per_mile)
    seconds = int((min_per_mile - minutes) * 60)
    return f"{minutes}:{seconds:02d}"


@router.get("/api/fitness/vo2max")
def get_vo2max_data(start: str | None = None, end: str | None = None):
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        rows = session.execute(text("""
            SELECT (recorded_at AT TIME ZONE 'UTC')::date as date,
                   round(value::numeric, 1) as vo2max
            FROM healthkit.metrics
            WHERE metric_type = 'vo2max'
              AND (recorded_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            ORDER BY date
        """), {"start": start_date, "end": end_date}).fetchall()

        readings = [{"date": str(r[0]), "vo2max": float(r[1])} for r in rows]

        # Peak is across ALL time, not just the filtered range
        peak_row = session.execute(text("""
            SELECT round(value::numeric, 1) as vo2max,
                   (recorded_at AT TIME ZONE 'UTC')::date as date
            FROM healthkit.metrics
            WHERE metric_type = 'vo2max'
            ORDER BY value DESC
            LIMIT 1
        """)).fetchone()

        summary = {
            "latest": readings[-1]["vo2max"] if readings else None,
            "peak": float(peak_row[0]) if peak_row else None,
            "peak_date": str(peak_row[1]) if peak_row else None,
        }

    return {**_response_metadata(start_date, end_date), "readings": readings, "summary": summary}


@router.get("/api/fitness/strength")
def get_strength_data(
    start: str | None = None,
    end: str | None = None,
    exercise: str | None = None,
):
    start_date, end_date = _parse_date_range(start, end)
    return {**_response_metadata(start_date, end_date), "exercises": [], "sets": [], "prs": []}
