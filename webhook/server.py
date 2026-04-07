"""FastAPI webhook server — HealthKit data receiver."""

import json
import logging
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from shared.db import get_session, bulk_upsert
from webhook.dashboard import router as dashboard_router

logger = logging.getLogger("basin.webhook")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Basin Webhook")
app.include_router(dashboard_router)

from webhook.finance import router as finance_router
app.include_router(finance_router)

from webhook.ops import router as ops_router
app.include_router(ops_router)

@app.get("/dashboard")
def dashboard_redirect():
    return RedirectResponse(url="/dashboard/fitness", status_code=307)

HEALTHKIT_FAILED_DIR = "/data/healthkit/failed"
HEALTHKIT_WEBHOOK_KEY = os.environ.get("HEALTHKIT_WEBHOOK_KEY")

# HAE sends snake_case names — only map the ones that differ from DB column values.
HAE_METRIC_MAP = {
    "vo2_max": "vo2max",
    "basal_energy_burned": "basal_energy",
    "apple_exercise_time": "exercise_time",
    "apple_stand_time": "stand_time",
    "walking_heart_rate_average": "walking_heart_rate",
    "walking_double_support_percentage": "walking_double_support_pct",
    "walking_asymmetry_percentage": "walking_asymmetry_pct",
    "body_mass": "weight_body_mass",
    "body_fat_percentage": "body_fat_percentage",
}

# Normalize HAE workout names to match existing DB values.
HAE_WORKOUT_MAP = {
    "Outdoor Run": "Running",
    "Indoor Run": "Running",
    "Traditional Strength Training": "Strength Training",
    "Functional Strength Training": "Functional Strength",
    "High Intensity Interval Training": "HIIT",
}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/healthkit/webhook")
async def healthkit_webhook(request: Request):
    """Receive HealthKit data from Health Auto Export app."""
    if HEALTHKIT_WEBHOOK_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if api_key != HEALTHKIT_WEBHOOK_KEY:
            return JSONResponse(status_code=401, content={"error": "invalid api key"})

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
        raw_name = metric.get("name", "")
        metric_name = HAE_METRIC_MAP.get(raw_name, raw_name)
        unit = metric.get("units", "")
        for point in metric.get("data", []):
            # Handle standard qty field, then fall back to Avg (heart rate)
            value = point.get("qty")
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
                "source_name": point.get("source", "Health Auto Export"),
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

        # Extract HR — try structured heartRate object first (HAE v2),
        # then fall back to heartRateData array.
        avg_hr = None
        max_hr = None
        hr_summary = w.get("heartRate", {})
        if hr_summary:
            avg_obj = hr_summary.get("avg", {})
            max_obj = hr_summary.get("max", {})
            if avg_obj:
                avg_hr = avg_obj.get("qty")
            if max_obj:
                max_hr = max_obj.get("qty")
        if avg_hr is None or max_hr is None:
            hr_data = w.get("heartRateData", [])
            if hr_data:
                avgs = [p["Avg"] for p in hr_data if "Avg" in p]
                maxes = [p["Max"] for p in hr_data if "Max" in p]
                if avgs and avg_hr is None:
                    avg_hr = sum(avgs) / len(avgs)
                if maxes and max_hr is None:
                    max_hr = max(maxes)

        energy = w.get("activeEnergyBurned", {})
        energy_kcal = energy.get("qty")
        if energy.get("units") == "kJ" and energy_kcal is not None:
            energy_kcal = energy_kcal / 4.184

        distance = w.get("distance", {})
        distance_m = distance.get("qty")
        if distance.get("units") == "km" and distance_m is not None:
            distance_m = distance_m * 1000
        elif distance.get("units") == "mi" and distance_m is not None:
            distance_m = distance_m * 1609.344

        raw_name = w.get("name", "Unknown")
        workout_type = HAE_WORKOUT_MAP.get(raw_name, raw_name)

        # Extract cadence if available
        cadence = w.get("stepCadence", {})
        avg_cadence = cadence.get("qty") if cadence else None

        # Extract elevation gain, convert to meters
        elev = w.get("elevationUp", {})
        elevation_m = elev.get("qty")
        if elev.get("units") == "ft" and elevation_m is not None:
            elevation_m = elevation_m * 0.3048

        rows.append({
            "workout_type": workout_type,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_sec": w.get("duration"),
            "distance_m": distance_m,
            "energy_kcal": energy_kcal,
            "avg_hr": avg_hr,
            "max_hr": max_hr,
            "avg_cadence": avg_cadence,
            "elevation_m": elevation_m,
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


