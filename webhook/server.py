"""FastAPI webhook server — HealthKit data receiver + Schwab OAuth callback."""

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.db import get_session, bulk_upsert

logger = logging.getLogger("basin.webhook")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Basin Webhook")

HEALTHKIT_FAILED_DIR = "/data/healthkit/failed"


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/healthkit/webhook")
async def healthkit_webhook(request: Request):
    """Receive HealthKit data from Health Auto Export app."""
    body = await request.json()
    data = body.get("data", {})

    metrics_count = 0
    workouts_count = 0

    try:
        with get_session() as session:
            metrics_count = _ingest_metrics(session, data.get("metrics", []))
            workouts_count = _ingest_workouts(session, data.get("workouts", []))
    except Exception as e:
        logger.error(f"HealthKit webhook error: {e}")
        _save_failed_payload(body, str(e))

    return {
        "metrics_upserted": metrics_count,
        "workouts_upserted": workouts_count,
    }


def _parse_healthkit_date(date_str: str) -> datetime:
    """
    Parse Health Auto Export date format.
    Examples: '2026-01-15 08:30:00 -0500', '2026-01-15 3:04:05 PM -0700'
    """
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %I:%M:%S %p %z"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse HealthKit date: {date_str}")


def _ingest_metrics(session, metrics: list) -> int:
    """Parse and upsert health metrics."""
    rows = []
    for metric in metrics:
        metric_name = metric.get("name", "")
        unit = metric.get("units", "")
        for point in metric.get("data", []):
            # Handle standard qty field
            value = point.get("qty")
            # Handle heart_rate special format (Avg)
            if value is None:
                value = point.get("Avg")
            if value is None:
                continue

            try:
                recorded_at = _parse_healthkit_date(point["date"])
            except (ValueError, KeyError):
                continue

            rows.append({
                "metric_type": metric_name,
                "value": float(value),
                "unit": unit,
                "recorded_at": recorded_at.isoformat(),
                "source_name": point.get("source"),
            })

    return bulk_upsert(
        session,
        table="healthkit.metrics",
        rows=rows,
        conflict_columns=["metric_type", "recorded_at", "source_name"],
    )


def _ingest_workouts(session, workouts: list) -> int:
    """Parse and upsert workouts."""
    rows = []
    for w in workouts:
        try:
            start = _parse_healthkit_date(w["start"])
            end = _parse_healthkit_date(w["end"])
        except (ValueError, KeyError):
            continue

        # Extract average and max HR from heartRateData array
        avg_hr = None
        max_hr = None
        hr_data = w.get("heartRateData", [])
        if hr_data:
            avgs = [p["Avg"] for p in hr_data if "Avg" in p]
            maxes = [p["Max"] for p in hr_data if "Max" in p]
            if avgs:
                avg_hr = sum(avgs) / len(avgs)
            if maxes:
                max_hr = max(maxes)

        energy = w.get("activeEnergyBurned", {})
        energy_kcal = energy.get("qty") if energy.get("units") in ("kcal", None) else None

        distance = w.get("distance", {})
        distance_m = distance.get("qty")
        # Convert km to meters if needed
        if distance.get("units") == "km" and distance_m is not None:
            distance_m = distance_m * 1000
        # Convert miles to meters if needed
        elif distance.get("units") == "mi" and distance_m is not None:
            distance_m = distance_m * 1609.344

        rows.append({
            "workout_type": w.get("name", "Unknown"),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_sec": w.get("duration"),
            "distance_m": distance_m,
            "energy_kcal": energy_kcal,
            "avg_hr": avg_hr,
            "max_hr": max_hr,
            "avg_cadence": None,  # Not in webhook payload; available in XML
            "source_name": "Health Auto Export",
        })

    return bulk_upsert(
        session,
        table="healthkit.workouts",
        rows=rows,
        conflict_columns=["workout_type", "start_time", "source_name"],
    )


def _save_failed_payload(payload: dict, error: str):
    """Save malformed payloads to dead-letter directory for replay."""
    os.makedirs(HEALTHKIT_FAILED_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(HEALTHKIT_FAILED_DIR, f"{ts}.json")
    with open(path, "w") as f:
        json.dump({"error": error, "payload": payload}, f, indent=2)
    logger.info(f"Saved failed payload to {path}")
