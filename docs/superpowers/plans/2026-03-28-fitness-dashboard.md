# Fitness Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a web-based fitness dashboard to Basin showing running performance, VO2 max trends, and weight progression charts, served by the existing webhook container.

**Architecture:** Three new API endpoints return JSON data from Postgres. A single HTML page with embedded Plotly.js charts fetches from those endpoints. Dashboard routes are in a separate `webhook/dashboard.py` module mounted on the existing FastAPI app. No new dependencies -- Plotly loaded from CDN.

**Tech Stack:** FastAPI, SQLAlchemy (raw SQL), Plotly.js (CDN), vanilla HTML/CSS/JS

**Spec:** `docs/superpowers/specs/2026-03-28-fitness-dashboard-design.md`

---

## File Structure

```
webhook/
  server.py           # Modify: mount dashboard router
  dashboard.py        # Create: API endpoints + HTML serving
  dashboard.html      # Create: single-file HTML dashboard
tests/
  test_dashboard.py   # Create: API endpoint tests
```

- `dashboard.py` -- FastAPI APIRouter with `/dashboard`, `/api/fitness/running`, `/api/fitness/vo2max`, `/api/fitness/strength`. Contains all SQL queries and response formatting. Owns date validation and common metadata.
- `dashboard.html` -- standalone HTML file with inline CSS and JS. Fetches from the API, renders Plotly charts, handles card switching and time filters.
- `server.py` -- one-line change to include the dashboard router.

---

## Task 1: Dashboard API -- Date Validation and Response Metadata

**Files:**
- Create: `webhook/dashboard.py`
- Create: `tests/test_dashboard.py`

Establish the router, date parsing helpers, and common response metadata used by all three endpoints.

- [ ] **Step 1: Create tests/test_dashboard.py with date validation tests**

```python
"""Tests for fitness dashboard API endpoints."""

from datetime import date, datetime, timezone
import pytest
from sqlalchemy import text


def test_dashboard_html_served(client):
    """GET /dashboard returns HTML."""
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_invalid_date_returns_400(client):
    """Invalid date format returns 400."""
    resp = client.get("/api/fitness/running?start=not-a-date")
    assert resp.status_code == 400


def test_start_after_end_returns_400(client):
    """start > end returns 400."""
    resp = client.get("/api/fitness/running?start=2026-06-01&end=2026-01-01")
    assert resp.status_code == 400


def test_default_date_range(client):
    """Omitting dates defaults to 6 months."""
    resp = client.get("/api/fitness/vo2max")
    assert resp.status_code == 200
    data = resp.json()
    assert "range_start" in data
    assert "range_end" in data
    assert "timezone" in data
    assert data["timezone"] == "UTC"
    assert "generated_at" in data
```

- [ ] **Step 2: Create webhook/dashboard.py with router, date helpers, and placeholder endpoints**

```python
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
            raise HTTPException(400, f"Invalid end date: {end}")
    else:
        end_date = today

    if start is not None:
        try:
            start_date = date.fromisoformat(start)
        except ValueError:
            raise HTTPException(400, f"Invalid start date: {start}")
    else:
        start_date = end_date - timedelta(days=183)

    if start_date > end_date:
        raise HTTPException(400, "start must be before end")

    if (end_date - start_date).days > MAX_DATE_SPAN_DAYS:
        raise HTTPException(400, f"Date range exceeds {MAX_DATE_SPAN_DAYS} days maximum")

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
    return {**_response_metadata(start_date, end_date), "runs": [], "summary": {}}


@router.get("/api/fitness/vo2max")
def get_vo2max_data(start: str | None = None, end: str | None = None):
    start_date, end_date = _parse_date_range(start, end)
    return {**_response_metadata(start_date, end_date), "readings": [], "summary": {}}


@router.get("/api/fitness/strength")
def get_strength_data(
    start: str | None = None,
    end: str | None = None,
    exercise: str | None = None,
):
    start_date, end_date = _parse_date_range(start, end)
    return {**_response_metadata(start_date, end_date), "exercises": [], "sets": [], "prs": []}
```

- [ ] **Step 3: Create a minimal webhook/dashboard.html placeholder**

```html
<!DOCTYPE html>
<html><head><title>Basin Fitness</title></head>
<body><h1>Basin Fitness Dashboard</h1><p>Loading...</p></body>
</html>
```

- [ ] **Step 4: Mount the router in webhook/server.py**

Add this import at the top of `webhook/server.py` (after the existing imports):

```python
from webhook.dashboard import router as dashboard_router
```

And add this line after `app = FastAPI(title="Basin Webhook")`:

```python
app.include_router(dashboard_router)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS (requires test Postgres -- date validation tests don't need data)

- [ ] **Step 6: Commit**

```bash
git add webhook/dashboard.py webhook/dashboard.html tests/test_dashboard.py webhook/server.py
git commit -m "feat: add dashboard router with date validation and placeholder endpoints"
```

---

## Task 2: Running Performance API

**Files:**
- Modify: `webhook/dashboard.py`
- Modify: `tests/test_dashboard.py`

Implement the `/api/fitness/running` endpoint with real queries.

- [ ] **Step 1: Add running API tests to tests/test_dashboard.py**

Append to `tests/test_dashboard.py`:

```python
def _seed_running_data(session):
    """Insert sample running metrics and workouts."""
    for i, (d, speed) in enumerate([
        ("2026-01-15", 5.5), ("2026-02-10", 5.8),
        ("2026-03-01", 6.0), ("2026-03-15", 5.7),
    ]):
        session.execute(text("""
            INSERT INTO healthkit.metrics (metric_type, value, unit, recorded_at, source_name)
            VALUES ('running_speed', :speed, 'mi/hr', :dt, 'Apple Watch')
        """), {"speed": speed, "dt": f"{d}T10:00:00Z"})

    for d, power in [
        ("2026-01-15", 240), ("2026-02-10", 250),
        ("2026-03-01", 260), ("2026-03-15", 245),
    ]:
        session.execute(text("""
            INSERT INTO healthkit.metrics (metric_type, value, unit, recorded_at, source_name)
            VALUES ('running_power', :power, 'W', :dt, 'Apple Watch')
        """), {"power": power, "dt": f"{d}T10:00:00Z"})

    for d, dur in [
        ("2026-01-15", 1800), ("2026-02-10", 2100),
        ("2026-03-01", 2400), ("2026-03-15", 1950),
    ]:
        session.execute(text("""
            INSERT INTO healthkit.workouts
                (workout_type, start_time, end_time, duration_sec, source_name)
            VALUES ('Running', :st, :et, :dur, 'Apple Watch')
        """), {"st": f"{d}T10:00:00Z", "et": f"{d}T11:00:00Z", "dur": dur})


def test_running_returns_data(session, client, monkeypatch):
    _seed_running_data(session)
    resp = client.get("/api/fitness/running?start=2026-01-01&end=2026-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 4
    assert data["runs"][0]["avg_speed_mph"] == 5.5
    assert data["runs"][0]["date"] == "2026-01-15"
    assert data["summary"]["total_runs"] == 4
    assert data["summary"]["latest_speed_mph"] == 5.7


def test_running_empty_range(client):
    resp = client.get("/api/fitness/running?start=2020-01-01&end=2020-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["runs"] == []
    assert data["summary"]["total_runs"] == 0


def test_running_pace_format(session, client):
    _seed_running_data(session)
    resp = client.get("/api/fitness/running?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    pace = data["summary"]["latest_pace_min_per_mile"]
    assert ":" in pace
```

- [ ] **Step 2: Implement the running endpoint in webhook/dashboard.py**

Replace the placeholder `get_running_data` function:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webhook/dashboard.py tests/test_dashboard.py
git commit -m "feat: implement running performance API endpoint"
```

---

## Task 3: VO2 Max API

**Files:**
- Modify: `webhook/dashboard.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add VO2 max tests to tests/test_dashboard.py**

Append:

```python
def _seed_vo2max_data(session):
    """Insert sample VO2 max readings."""
    for d, val in [
        ("2023-12-08", 51.0), ("2024-06-15", 43.5),
        ("2025-01-10", 45.0), ("2026-03-20", 46.2),
    ]:
        session.execute(text("""
            INSERT INTO healthkit.metrics (metric_type, value, unit, recorded_at, source_name)
            VALUES ('vo2max', :val, 'mL/min/kg', :dt, 'Apple Watch')
        """), {"val": val, "dt": f"{d}T00:00:00Z"})


def test_vo2max_returns_data(session, client):
    _seed_vo2max_data(session)
    resp = client.get("/api/fitness/vo2max?start=2023-01-01&end=2026-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["readings"]) == 4
    assert data["summary"]["latest"] == 46.2
    assert data["summary"]["peak"] == 51.0
    assert data["summary"]["peak_date"] == "2023-12-08"


def test_vo2max_empty_range(client):
    resp = client.get("/api/fitness/vo2max?start=2020-01-01&end=2020-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["readings"] == []
    assert data["summary"]["latest"] is None
```

- [ ] **Step 2: Implement the VO2 max endpoint**

Replace the placeholder `get_vo2max_data`:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webhook/dashboard.py tests/test_dashboard.py
git commit -m "feat: implement VO2 max API endpoint"
```

---

## Task 4: Strength API

**Files:**
- Modify: `webhook/dashboard.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add strength tests to tests/test_dashboard.py**

Append:

```python
def _seed_strength_data(session):
    """Insert sample strength workout data."""
    session.execute(text(
        "INSERT INTO hevy.exercises (name) VALUES ('Bench Press'), ('Squat') ON CONFLICT DO NOTHING"
    ))
    bench_id = session.execute(
        text("SELECT id FROM hevy.exercises WHERE name = 'Bench Press'")
    ).scalar()
    squat_id = session.execute(
        text("SELECT id FROM hevy.exercises WHERE name = 'Squat'")
    ).scalar()

    for i, d in enumerate(["2026-01-10", "2026-02-15", "2026-03-20"]):
        session.execute(text("""
            INSERT INTO hevy.workouts (title, started_at, ended_at, duration_sec)
            VALUES (:title, :st, :et, 3600)
            ON CONFLICT (started_at) DO NOTHING
        """), {"title": f"Workout {i}", "st": f"{d}T10:00:00", "et": f"{d}T11:00:00"})

    w_ids = [r[0] for r in session.execute(
        text("SELECT id FROM hevy.workouts ORDER BY started_at")
    ).fetchall()]

    sets_data = [
        (w_ids[0], bench_id, 0, "normal", 135, 8),
        (w_ids[0], bench_id, 1, "warmup", 95, 10),
        (w_ids[1], bench_id, 0, "normal", 155, 6),
        (w_ids[2], bench_id, 0, "normal", 175, 5),
        (w_ids[0], squat_id, 0, "normal", 185, 8),
        (w_ids[1], squat_id, 0, "normal", 205, 5),
    ]
    for w_id, ex_id, idx, stype, weight, reps in sets_data:
        session.execute(text("""
            INSERT INTO hevy.sets (workout_id, exercise_id, set_index, set_type, weight_lbs, reps)
            VALUES (:w, :e, :i, :st, :wt, :r)
            ON CONFLICT (workout_id, exercise_id, set_index) DO NOTHING
        """), {"w": w_id, "e": ex_id, "i": idx, "st": stype, "wt": weight, "r": reps})


def test_strength_returns_data(session, client):
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert "Bench Press" in data["exercises"]
    assert "Squat" in data["exercises"]
    assert len(data["sets"]) == 6
    assert len(data["prs"]) == 2


def test_strength_pr_excludes_warmup(session, client):
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    bench_pr = next(p for p in data["prs"] if p["exercise"] == "Bench Press")
    assert bench_pr["max_lbs"] == 175


def test_strength_filter_by_exercise(session, client):
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31&exercise=Squat")
    data = resp.json()
    assert all(s["exercise"] == "Squat" for s in data["sets"])


def test_strength_empty_range(client):
    resp = client.get("/api/fitness/strength?start=2020-01-01&end=2020-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sets"] == []
    assert data["prs"] == []
```

- [ ] **Step 2: Implement the strength endpoint**

Replace the placeholder `get_strength_data`:

```python
@router.get("/api/fitness/strength")
def get_strength_data(
    start: str | None = None,
    end: str | None = None,
    exercise: str | None = None,
):
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        exercise_filter = ""
        params: dict = {"start": start_date, "end": end_date}
        if exercise:
            exercise_filter = "AND e.name = :exercise"
            params["exercise"] = exercise

        sets_rows = session.execute(text(f"""
            SELECT w.started_at::date as date,
                   e.name as exercise,
                   s.weight_lbs,
                   s.reps,
                   s.set_index,
                   s.set_type
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE w.started_at::date BETWEEN :start AND :end
              {exercise_filter}
            ORDER BY w.started_at, e.name, s.set_index
        """), params).fetchall()

        sets = [{{
            "date": str(r[0]),
            "exercise": r[1],
            "weight_lbs": round(float(r[2])) if r[2] else None,
            "reps": r[3],
            "set_index": r[4],
            "set_type": r[5],
        }} for r in sets_rows]

        exercises = sorted(set(s["exercise"] for s in sets))

        pr_rows = session.execute(text(f"""
            SELECT DISTINCT ON (e.name)
                   e.name as exercise,
                   round(s.weight_lbs::numeric, 0) as max_lbs,
                   w.started_at::date as date
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE w.started_at::date BETWEEN :start AND :end
              AND s.set_type != 'warmup'
              AND s.reps > 0
              AND s.weight_lbs IS NOT NULL
              {exercise_filter}
            ORDER BY e.name, s.weight_lbs DESC, w.started_at ASC, s.set_index ASC
        """), params).fetchall()

        prs = [{{
            "exercise": r[0],
            "max_lbs": int(r[1]),
            "date": str(r[2]),
        }} for r in pr_rows]

    return {{
        **_response_metadata(start_date, end_date),
        "exercises": exercises,
        "sets": sets,
        "prs": prs,
    }}
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webhook/dashboard.py tests/test_dashboard.py
git commit -m "feat: implement strength progression API endpoint"
```

---

## Task 5: Dashboard HTML -- Full Interactive Page

**Files:**
- Modify: `webhook/dashboard.html`

Replace the placeholder with the full dashboard HTML including Plotly charts, card switching, time filters, and all three panels. This is the largest task -- the complete HTML file with inline CSS and JS.

The dashboard HTML should implement:
- Dark theme (#0f172a background) matching the approved mockup
- 3 summary cards (running/vo2max/strength) with Plotly sparklines
- Detail panel that swaps on card click
- Time range filters (3M/6M/1Y/All presets + date pickers)
- Running panel: line chart + recent runs table
- VO2 max panel: line chart with peak reference line
- Strength panel: exercise dropdown + scatter chart with warmup/normal/PR markers
- Loading, empty, and error states
- Keyboard-accessible card selection and filter controls

Key implementation notes for the HTML:
- Load Plotly from CDN: `https://cdn.plot.ly/plotly-2.35.2.min.js`
- Use `textContent` for all dynamic text updates (not innerHTML for user data)
- Fetch all 3 endpoints on load with `Promise.all`
- Use DOM element creation methods for the runs table rows
- Plotly layout: `paper_bgcolor: '#1e293b'`, `plot_bgcolor: '#1e293b'`, font color `#94a3b8`
- Speed to pace conversion: `min_per_mile = 60 / speed_mph`, format as `mm:ss`
- Strength PR definition: max weight excluding warmups and reps <= 0
- Sparklines: small Plotly bar charts with `staticPlot: true`
- Color scheme: running=#3b82f6, vo2max=#a78bfa, strength=#22c55e

Reference the approved mockup in `.superpowers/brainstorm/` for visual design.

- [ ] **Step 1: Write the full dashboard HTML**

Create the complete `webhook/dashboard.html` file implementing all the above. Use safe DOM methods (createElement, textContent) instead of innerHTML for any content that includes user data.

- [ ] **Step 2: Commit**

```bash
git add webhook/dashboard.html
git commit -m "feat: add fitness dashboard HTML with Plotly charts and card layout"
```

---

## Task 6: Deploy and Verify

**Files:**
- No new files -- deploy and verify

- [ ] **Step 1: Sync to VM and rebuild webhook**

```bash
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='docs/' --exclude='.DS_Store' \
  /Users/jehan/Projects/Basin/ root@reservebot:/opt/basin/

ssh root@reservebot 'export $(cat /etc/basin/secrets | xargs) && cd /opt/basin && \
  op run --env-file=.env -- docker compose up -d --build webhook'
```

- [ ] **Step 2: Verify dashboard loads**

```bash
curl -s http://100.125.126.42:8075/dashboard | head -5
```

Expected: HTML starting with `<!DOCTYPE html>`

- [ ] **Step 3: Verify API endpoints return data**

```bash
curl -s "http://100.125.126.42:8075/api/fitness/running?start=2024-01-01" | python3 -m json.tool | head -20
curl -s "http://100.125.126.42:8075/api/fitness/vo2max?start=2023-01-01" | python3 -m json.tool | head -20
curl -s "http://100.125.126.42:8075/api/fitness/strength?start=2024-01-01" | python3 -m json.tool | head -20
```

Expected: JSON responses with `runs`, `readings`, and `sets` arrays containing real data.

- [ ] **Step 4: Open dashboard in browser and verify charts render**

Open `http://100.125.126.42:8075/dashboard` in browser over Tailscale. Verify:
- Three summary cards load with real numbers
- Clicking each card switches the detail panel
- Running chart shows pace data points
- VO2 max chart shows trend with peak reference line
- Strength chart shows exercise data with warmup/normal distinction
- Time filters (3M/6M/1Y/All) reload data
- Date pickers work

- [ ] **Step 5: Commit any fixes and push**

```bash
git add -A
git commit -m "fix: dashboard deployment adjustments"
git push origin main
```
