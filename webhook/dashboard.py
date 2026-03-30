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


@router.get("/api/fitness/calendar")
def get_calendar_data(start: str | None = None, end: str | None = None):
    """Training calendar: workout counts per day from all sources."""
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        rows = session.execute(text("""
            SELECT date, array_agg(DISTINCT source ORDER BY source) as types, count(DISTINCT source) as count
            FROM (
                SELECT (started_at AT TIME ZONE 'UTC')::date as date, 'strength' as source
                FROM hevy.workouts
                WHERE (started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
                UNION ALL
                SELECT (start_time AT TIME ZONE 'UTC')::date as date,
                       CASE WHEN workout_type IN ('Strength Training', 'Functional Strength') THEN 'strength'
                            WHEN workout_type = 'Running' THEN 'running'
                            ELSE 'other'
                       END as source
                FROM healthkit.workouts
                WHERE (start_time AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            ) combined
            GROUP BY date
            ORDER BY date
        """), {"start": start_date, "end": end_date}).fetchall()

        days = [{
            "date": str(r[0]),
            "types": list(r[1]),
            "count": r[2],
        } for r in rows]

    return {**_response_metadata(start_date, end_date), "days": days}


@router.get("/api/fitness/running")
def get_running_data(start: str | None = None, end: str | None = None):
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        # Single set-based query: join workouts with speed/power metrics
        # using each workout's time window, avoiding O(N) per-run queries
        rows = session.execute(text("""
            SELECT w.id,
                   (w.start_time AT TIME ZONE 'UTC')::date as date,
                   round((w.duration_sec / 60.0)::numeric, 1) as duration_min,
                   round(avg(speed.value)::numeric, 2) as avg_speed,
                   round(avg(power.value)::numeric, 0) as avg_power,
                   w.start_time, w.end_time
            FROM healthkit.workouts w
            LEFT JOIN healthkit.metrics speed
              ON speed.metric_type = 'running_speed'
              AND speed.recorded_at BETWEEN w.start_time AND w.end_time
            LEFT JOIN healthkit.metrics power
              ON power.metric_type = 'running_power'
              AND power.recorded_at BETWEEN w.start_time AND w.end_time
            WHERE w.workout_type = 'Running'
              AND (w.start_time AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            GROUP BY w.id, w.start_time, w.end_time, w.duration_sec
            ORDER BY w.start_time
        """), {"start": start_date, "end": end_date}).fetchall()

        # Get step counts per workout separately (avoids expensive 3-way JOIN)
        step_rows = session.execute(text("""
            SELECT w.id, sum(m.value) as total_steps
            FROM healthkit.workouts w
            JOIN healthkit.metrics m ON m.metric_type = 'step_count'
              AND m.recorded_at BETWEEN w.start_time AND w.end_time
            WHERE w.workout_type = 'Running'
              AND (w.start_time AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            GROUP BY w.id
        """), {"start": start_date, "end": end_date}).fetchall()
        steps_by_id = {r[0]: float(r[1]) for r in step_rows}

        runs = []
        for row in rows:
            w_id = row[0]
            d = str(row[1])
            duration_min = float(row[2]) if row[2] else None
            speed = float(row[3]) if row[3] else None
            avg_power = float(row[4]) if row[4] else None
            distance_mi = round(speed * (duration_min / 60.0), 2) if speed and duration_min else None
            total_steps = steps_by_id.get(w_id)
            cadence = round(total_steps / duration_min) if total_steps and duration_min and duration_min > 0 else None

            runs.append({
                "date": d,
                "avg_speed_mph": speed,
                "duration_min": duration_min,
                "distance_mi": distance_mi,
                "avg_power": avg_power,
                "cadence_spm": cadence,
            })

        total_runs = len(runs)
        speeds = [r["avg_speed_mph"] for r in runs if r["avg_speed_mph"]]
        avg_speed = round(sum(speeds) / len(speeds), 2) if speeds else None
        latest_speed = runs[-1]["avg_speed_mph"] if runs and runs[-1]["avg_speed_mph"] else None
        latest_pace = _speed_to_pace(latest_speed) if latest_speed else None
        total_distance = round(sum(r["distance_mi"] for r in runs if r["distance_mi"]), 2) if runs else None

        summary = {
            "total_runs": total_runs,
            "avg_speed_mph": avg_speed,
            "latest_speed_mph": latest_speed,
            "latest_pace_min_per_mile": latest_pace,
            "total_distance_mi": total_distance,
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
    title: str | None = None,
):
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        filters = ""
        params: dict = {"start": start_date, "end": end_date}
        if exercise:
            filters += " AND e.name = :exercise"
            params["exercise"] = exercise
        if title:
            filters += " AND w.title = :title"
            params["title"] = title

        # Workout titles (deterministic sort: frequency desc, latest date desc, title asc)
        # Computed from date range only (ignoring title filter so tabs stay stable)
        title_rows = session.execute(text("""
            SELECT w.title, count(*) as cnt, max((w.started_at AT TIME ZONE 'UTC')::date) as latest
            FROM hevy.workouts w
            WHERE (w.started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            GROUP BY w.title
            ORDER BY cnt DESC, latest DESC, lower(w.title) ASC
        """), {"start": start_date, "end": end_date}).fetchall()
        workout_titles = [r[0] for r in title_rows]

        # Workouts with nested exercises and sets (set-based, no N+1)
        all_rows = session.execute(text(f"""
            SELECT w.id as workout_id,
                   (w.started_at AT TIME ZONE 'UTC')::date as date,
                   w.title,
                   e.name as exercise_name,
                   s.set_index,
                   round(s.weight_lbs::numeric, 0) as weight_lbs,
                   s.reps,
                   s.set_type
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE (w.started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
              {filters}
            ORDER BY w.started_at, e.name, s.set_index
        """), params).fetchall()

        # Group into workouts -> exercises -> sets
        from collections import OrderedDict
        workout_map = OrderedDict()
        for r in all_rows:
            w_id = r[0]
            if w_id not in workout_map:
                workout_map[w_id] = {
                    "date": str(r[1]),
                    "title": r[2],
                    "exercises": OrderedDict(),
                    "_sets_total": 0,
                }
            wk = workout_map[w_id]
            ex_name = r[3]
            if ex_name not in wk["exercises"]:
                wk["exercises"][ex_name] = {"name": ex_name, "sets": [], "volume_lbs": 0}
            ex = wk["exercises"][ex_name]
            weight = int(r[5]) if r[5] is not None else None
            reps = r[6]
            set_type = r[7]
            ex["sets"].append({
                "set_index": r[4],
                "weight_lbs": weight,
                "reps": reps,
                "set_type": set_type,
            })
            wk["_sets_total"] += 1
            if set_type != "warmup" and weight is not None and reps and reps > 0:
                ex["volume_lbs"] += weight * reps

        workouts = []
        for wk in workout_map.values():
            exercises_list = list(wk["exercises"].values())
            volume = sum(ex["volume_lbs"] for ex in exercises_list)
            workouts.append({
                "date": wk["date"],
                "title": wk["title"],
                "exercise_count": len(exercises_list),
                "set_count": wk["_sets_total"],
                "volume_lbs": volume,
                "exercises": exercises_list,
            })

        # Legacy fields (backward compat)
        sets = [{
            "date": str(r[1]),
            "exercise": r[3],
            "weight_lbs": int(r[5]) if r[5] is not None else None,
            "reps": r[6],
            "set_index": r[4],
            "set_type": r[7],
        } for r in all_rows]

        exercises = sorted(set(s["exercise"] for s in sets))

        pr_rows = session.execute(text(f"""
            SELECT DISTINCT ON (e.name)
                   e.name as exercise,
                   round(s.weight_lbs::numeric, 0) as max_lbs,
                   (w.started_at AT TIME ZONE 'UTC')::date as date
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE (w.started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
              AND s.set_type != 'warmup'
              AND s.reps > 0
              AND s.weight_lbs IS NOT NULL
              {filters}
            ORDER BY e.name, s.weight_lbs DESC, w.started_at ASC, s.set_index ASC
        """), params).fetchall()

        prs = [{
            "exercise": r[0],
            "max_lbs": int(r[1]),
            "date": str(r[2]),
        } for r in pr_rows]

    return {
        **_response_metadata(start_date, end_date),
        "workout_titles": workout_titles,
        "workouts": workouts,
        # Legacy fields (deprecated, kept for backward compat)
        "exercises": exercises,
        "sets": sets,
        "prs": prs,
    }
