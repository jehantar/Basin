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
