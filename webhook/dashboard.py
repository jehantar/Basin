"""Fitness dashboard -- API endpoints and HTML serving."""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session
from webhook.dashboard_shared import _parse_date_range, _response_metadata

router = APIRouter()


@router.get("/dashboard/fitness")
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
            SELECT date, array_agg(DISTINCT label ORDER BY label) as labels
            FROM (
                SELECT (started_at AT TIME ZONE 'America/Los_Angeles')::date as date, title as label
                FROM hevy.workouts
                WHERE (started_at AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
                UNION ALL
                SELECT (start_time AT TIME ZONE 'America/Los_Angeles')::date as date, workout_type as label
                FROM healthkit.workouts
                WHERE (start_time AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
                  AND workout_type NOT IN ('Strength Training', 'Functional Strength')
            ) combined
            GROUP BY date
            ORDER BY date
        """), {"start": start_date, "end": end_date}).fetchall()

        days = [{
            "date": str(r[0]),
            "labels": list(r[1]),
        } for r in rows]

    return {**_response_metadata(start_date, end_date), "days": days}


@router.get("/api/fitness/running")
def get_running_data(start: str | None = None, end: str | None = None):
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        # Single set-based query: join workouts with speed/power metrics
        # using each workout's time window, avoiding O(N) per-run queries
        rows = session.execute(text("""
            SELECT DISTINCT ON (w.start_time)
                   w.id,
                   (w.start_time AT TIME ZONE 'America/Los_Angeles')::date as date,
                   round((w.duration_sec / 60.0)::numeric, 1) as duration_min,
                   round(avg(speed.value)::numeric, 2) as avg_speed,
                   round(avg(power.value)::numeric, 0) as avg_power,
                   w.start_time, w.end_time,
                   w.elevation_m
            FROM healthkit.workouts w
            LEFT JOIN healthkit.metrics speed
              ON speed.metric_type = 'running_speed'
              AND speed.recorded_at BETWEEN w.start_time AND w.end_time
            LEFT JOIN healthkit.metrics power
              ON power.metric_type = 'running_power'
              AND power.recorded_at BETWEEN w.start_time AND w.end_time
            WHERE w.workout_type = 'Running'
              AND (w.start_time AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
            GROUP BY w.id, w.start_time, w.end_time, w.duration_sec
            ORDER BY w.start_time, (w.source_name = 'Health Auto Export') ASC
        """), {"start": start_date, "end": end_date}).fetchall()

        # Get avg stride length per workout (to derive cadence = speed / stride)
        stride_rows = session.execute(text("""
            SELECT w.id, round(avg(m.value)::numeric, 4) as avg_stride_m
            FROM healthkit.workouts w
            JOIN healthkit.metrics m ON m.metric_type = 'running_stride_length'
              AND m.recorded_at BETWEEN w.start_time AND w.end_time
            WHERE w.workout_type = 'Running'
              AND (w.start_time AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
            GROUP BY w.id
        """), {"start": start_date, "end": end_date}).fetchall()
        stride_by_id = {r[0]: float(r[1]) for r in stride_rows}

        runs = []
        for row in rows:
            w_id = row[0]
            d = str(row[1])
            duration_min = float(row[2]) if row[2] else None
            speed = float(row[3]) if row[3] else None
            avg_power = float(row[4]) if row[4] else None
            elevation_m = float(row[7]) if row[7] else None
            elevation_ft = round(elevation_m / 0.3048) if elevation_m else None
            distance_mi = round(speed * (duration_min / 60.0), 2) if speed and duration_min else None
            # Cadence = speed (m/min) / stride (m) = steps per minute
            stride_m = stride_by_id.get(w_id)
            cadence = None
            if speed and stride_m and stride_m > 0:
                speed_m_per_min = speed * 26.8224  # mph to m/min
                cadence = round(speed_m_per_min / stride_m)

            runs.append({
                "date": d,
                "avg_speed_mph": speed,
                "duration_min": duration_min,
                "distance_mi": distance_mi,
                "avg_power": avg_power,
                "cadence_spm": cadence,
                "elevation_ft": elevation_ft,
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
            SELECT (recorded_at AT TIME ZONE 'America/Los_Angeles')::date as date,
                   round(value::numeric, 1) as vo2max
            FROM healthkit.metrics
            WHERE metric_type = 'vo2max'
              AND (recorded_at AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
            ORDER BY date
        """), {"start": start_date, "end": end_date}).fetchall()

        readings = [{"date": str(r[0]), "vo2max": float(r[1])} for r in rows]

        # Peak is across ALL time, not just the filtered range
        peak_row = session.execute(text("""
            SELECT round(value::numeric, 1) as vo2max,
                   (recorded_at AT TIME ZONE 'America/Los_Angeles')::date as date
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
            SELECT w.title, count(*) as cnt, max((w.started_at AT TIME ZONE 'America/Los_Angeles')::date) as latest
            FROM hevy.workouts w
            WHERE (w.started_at AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
            GROUP BY w.title
            ORDER BY cnt DESC, latest DESC, lower(w.title) ASC
        """), {"start": start_date, "end": end_date}).fetchall()
        workout_titles = [r[0] for r in title_rows]

        # Workouts with nested exercises and sets (set-based, no N+1)
        all_rows = session.execute(text(f"""
            SELECT w.id as workout_id,
                   (w.started_at AT TIME ZONE 'America/Los_Angeles')::date as date,
                   w.title,
                   e.name as exercise_name,
                   s.set_index,
                   round(s.weight_lbs::numeric, 0) as weight_lbs,
                   s.reps,
                   s.set_type
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE (w.started_at AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
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
                   (w.started_at AT TIME ZONE 'America/Los_Angeles')::date as date
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE (w.started_at AT TIME ZONE 'America/Los_Angeles')::date BETWEEN :start AND :end
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


@router.get("/api/fitness/training-load")
def get_training_load(start: str | None = None, end: str | None = None):
    """CTL / ATL / TSB timeseries from Intervals.icu."""
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        rows = session.execute(text("""
            SELECT date, ctl, atl, tsb, ramp_rate, training_load
            FROM intervals.daily_fitness
            WHERE date BETWEEN :start AND :end
            ORDER BY date
        """), {"start": start_date, "end": end_date}).fetchall()

        days = [{
            "date": str(r[0]),
            "ctl": round(r[1], 1) if r[1] else None,
            "atl": round(r[2], 1) if r[2] else None,
            "tsb": round(r[3], 1) if r[3] else None,
            "ramp_rate": round(r[4], 2) if r[4] else None,
            "training_load": r[5],
        } for r in rows]

        # Current fitness state = latest row
        current = days[-1] if days else {}

    return {**_response_metadata(start_date, end_date), "days": days, "current": current}


@router.get("/api/fitness/pace-curve")
def get_pace_curve():
    """Best-effort pace curve — latest snapshot."""
    with get_session() as session:
        latest = session.execute(text("""
            SELECT MAX(captured_at) FROM intervals.pace_curves
        """)).scalar()

        if not latest:
            return {"distances": []}

        rows = session.execute(text("""
            SELECT distance_m, time_secs
            FROM intervals.pace_curves
            WHERE captured_at = :date AND period = '1 year'
            ORDER BY distance_m
        """), {"date": latest}).fetchall()

        # Extract key distances for display
        key_distances = [400, 800, 1000, 1609.344, 3000, 5000, 10000, 21097]
        efforts = []
        all_points = [(float(r[0]), float(r[1])) for r in rows]

        for target in key_distances:
            best = None
            for dist, secs in all_points:
                if dist >= target:
                    best = (dist, secs)
                    break
            if best:
                dist, secs = best
                pace_per_mi = (secs / dist) * 1609.344
                mins = int(pace_per_mi) // 60
                s = int(pace_per_mi) % 60
                efforts.append({
                    "target_m": target,
                    "actual_m": dist,
                    "time_secs": secs,
                    "pace_per_mile": f"{mins}:{s:02d}",
                })

    return {"captured_at": str(latest), "efforts": efforts, "all_points": all_points}


@router.get("/api/fitness/hr-curve")
def get_hr_curve():
    """Peak HR curve — latest snapshot."""
    with get_session() as session:
        latest = session.execute(text("""
            SELECT MAX(captured_at) FROM intervals.hr_curves
        """)).scalar()

        if not latest:
            return {"durations": []}

        rows = session.execute(text("""
            SELECT duration_secs, hr_bpm
            FROM intervals.hr_curves
            WHERE captured_at = :date AND period = '1 year'
            ORDER BY duration_secs
        """), {"date": latest}).fetchall()

        # Extract key durations
        key_durations = [
            (5, "5s"), (10, "10s"), (30, "30s"), (60, "1min"),
            (120, "2min"), (300, "5min"), (600, "10min"),
            (1200, "20min"), (1800, "30min"), (3600, "60min"),
        ]

        all_points = [(int(r[0]), int(r[1])) for r in rows]
        efforts = []
        for target, label in key_durations:
            best = None
            for secs, hr in all_points:
                if secs >= target:
                    best = (secs, hr)
                    break
            if best:
                efforts.append({
                    "label": label,
                    "duration_secs": best[0],
                    "hr_bpm": best[1],
                })

    return {"captured_at": str(latest), "efforts": efforts, "all_points": all_points}
