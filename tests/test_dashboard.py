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
    data = resp.json()
    assert data["detail"]["code"] == 400
    assert "hint" in data["detail"]


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


def test_running_returns_data(session, client):
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
