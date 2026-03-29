"""Tests for HealthKit webhook endpoint."""

import json
import pytest
from unittest.mock import patch, MagicMock


SAMPLE_METRICS_PAYLOAD = {
    "data": {
        "metrics": [
            {
                "name": "resting_heart_rate",
                "units": "bpm",
                "data": [
                    {"date": "2026-01-15 08:00:00 -0500", "qty": 58, "source": "Apple Watch"},
                    {"date": "2026-01-16 08:00:00 -0500", "qty": 57, "source": "Apple Watch"},
                ],
            },
            {
                "name": "weight_body_mass",
                "units": "kg",
                "data": [
                    {"date": "2026-01-15 07:00:00 -0500", "qty": 80.5, "source": "Withings"},
                ],
            },
        ],
        "workouts": [],
    }
}

SAMPLE_WORKOUT_PAYLOAD = {
    "data": {
        "metrics": [],
        "workouts": [
            {
                "id": "abc-123",
                "name": "Running",
                "start": "2026-01-15 07:00:00 -0500",
                "end": "2026-01-15 07:45:00 -0500",
                "duration": 2700,
                "activeEnergyBurned": {"qty": 450, "units": "kcal"},
                "distance": {"qty": 5200, "units": "m"},
                "heartRateData": [
                    {"date": "2026-01-15 07:00:00 -0500", "Avg": 155, "Max": 172, "Min": 120},
                ],
            }
        ],
    }
}


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_healthkit_webhook_metrics(client, session):
    resp = client.post("/healthkit/webhook", json=SAMPLE_METRICS_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["metrics_upserted"] == 3
    assert resp.json()["workouts_upserted"] == 0

    from sqlalchemy import text
    count = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert count == 3


def test_healthkit_webhook_workouts(client, session):
    resp = client.post("/healthkit/webhook", json=SAMPLE_WORKOUT_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["workouts_upserted"] == 1

    from sqlalchemy import text
    row = session.execute(
        text("SELECT workout_type, energy_kcal FROM healthkit.workouts LIMIT 1")
    ).fetchone()
    assert row[0] == "Running"
    assert float(row[1]) == 450.0


def test_healthkit_webhook_idempotent(client, session):
    """Posting the same data twice should not duplicate rows."""
    client.post("/healthkit/webhook", json=SAMPLE_METRICS_PAYLOAD)
    client.post("/healthkit/webhook", json=SAMPLE_METRICS_PAYLOAD)

    from sqlalchemy import text
    count = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert count == 3


def test_healthkit_webhook_malformed(client):
    resp = client.post("/healthkit/webhook", json={"bad": "data"})
    assert resp.status_code == 200  # Accept but log warning
    assert resp.json()["metrics_upserted"] == 0
