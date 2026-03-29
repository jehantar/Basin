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
