"""FastAPI webhook server — HealthKit data receiver + Schwab OAuth callback."""

import base64
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx
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


@app.get("/schwab/auth")
def schwab_auth_redirect():
    """Redirect user to Schwab's OAuth authorization page."""
    config = _get_schwab_config()
    params = urlencode({
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
    })
    return RedirectResponse(
        url=f"https://api.schwabapi.com/v1/oauth/authorize?{params}",
        status_code=307,
    )


@app.get("/schwab/callback")
def schwab_callback(code: str):
    """Exchange authorization code for access and refresh tokens."""
    config = _get_schwab_config()

    credentials = base64.b64encode(
        f"{config['client_id']}:{config['client_secret']}".encode()
    ).decode()

    resp = httpx.post(
        "https://api.schwabapi.com/v1/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    tokens = resp.json()

    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.execute(
            text("""
                INSERT INTO schwab.tokens (id, access_token, refresh_token, access_expires, refresh_expires, updated_at)
                VALUES (1, :access, :refresh, :access_exp, :refresh_exp, :now)
                ON CONFLICT (id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    access_expires = EXCLUDED.access_expires,
                    refresh_expires = EXCLUDED.refresh_expires,
                    updated_at = EXCLUDED.updated_at
            """),
            {
                "access": tokens["access_token"],
                "refresh": tokens["refresh_token"],
                "access_exp": now + timedelta(seconds=tokens.get("expires_in", 1800)),
                "refresh_exp": now + timedelta(days=7),
                "now": now,
            },
        )

    return {"message": "Schwab tokens stored successfully"}


def _get_schwab_config() -> dict:
    return {
        "client_id": os.environ.get("SCHWAB_CLIENT_ID", ""),
        "client_secret": os.environ.get("SCHWAB_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("SCHWAB_REDIRECT_URI", ""),
    }
