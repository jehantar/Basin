"""System dashboard — collector health monitoring API endpoints."""

import os
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session

router = APIRouter()

COLLECTOR_SCHEDULES = {
    "teller": "Daily 7:00 AM UTC",
    "hevy": "Daily 6:00 AM UTC",
    "healthkit": "Daily 6:05 AM UTC",
    "nasdaq": "Daily 1:30 AM UTC",
    "intervals_icu": "Daily 6:10 AM UTC",
}


@router.get("/dashboard/system")
def serve_system():
    """Serve the system dashboard HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "ops.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())


@router.get("/api/ops/status")
def get_ops_status():
    """Latest run per collector + summary stats."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT DISTINCT ON (collector)
                collector, status, started_at, finished_at,
                rows_upserted, error_message
            FROM basin.collector_runs
            ORDER BY collector, started_at DESC
        """)).fetchall()

        collectors = []
        for r in rows:
            duration = None
            if r.finished_at and r.started_at:
                duration = round((r.finished_at - r.started_at).total_seconds(), 1)

            collectors.append({
                "name": r.collector,
                "last_status": r.status,
                "last_run": r.started_at.isoformat() if r.started_at else None,
                "last_rows": r.rows_upserted,
                "last_error": r.error_message,
                "duration_sec": duration,
                "schedule": COLLECTOR_SCHEDULES.get(r.collector, "Unknown"),
            })

    return {
        "collectors": collectors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/ops/history")
def get_ops_history(limit: int = 50):
    """Recent collector runs across all collectors."""
    limit = min(limit, 200)

    with get_session() as session:
        rows = session.execute(text("""
            SELECT collector, started_at, finished_at, status,
                   rows_upserted, error_message
            FROM basin.collector_runs
            ORDER BY started_at DESC
            LIMIT :lim
        """), {"lim": limit}).fetchall()

        runs = []
        for r in rows:
            duration = None
            if r.finished_at and r.started_at:
                duration = round((r.finished_at - r.started_at).total_seconds(), 1)

            runs.append({
                "collector": r.collector,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_sec": duration,
                "status": r.status,
                "rows_upserted": r.rows_upserted,
                "error_message": r.error_message,
            })

    return {
        "runs": runs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
