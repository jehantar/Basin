"""Strava collector — fetches activity data (elevation, GPS, HR) via Strava API."""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.strava")

API_BASE = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"


class StravaCollector(BaseCollector):
    name = "strava"

    def __init__(self):
        self.client_id = os.environ.get("STRAVA_CLIENT_ID", "")
        self.client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
        self._access_token = None

    def collect(self, session) -> int:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET required")

        self._ensure_valid_token(session)
        return self._collect_activities(session)

    def _ensure_valid_token(self, session):
        """Refresh the access token if expired or expiring soon."""
        row = session.execute(text("""
            SELECT access_token, refresh_token, expires_at
            FROM strava.tokens WHERE id = 1
        """)).fetchone()

        if not row:
            raise RuntimeError(
                "No Strava tokens found. Visit /strava/auth to authorize first."
            )

        access_token, refresh_token, expires_at = row
        now = datetime.now(timezone.utc)

        # Refresh if within 5 minutes of expiry
        if expires_at - now > timedelta(minutes=5):
            self._access_token = access_token
            return

        logger.info("Refreshing Strava access token")
        resp = httpx.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        self._access_token = data["access_token"]
        session.execute(text("""
            UPDATE strava.tokens SET
                access_token = :access,
                refresh_token = :refresh,
                expires_at = to_timestamp(:expires),
                updated_at = now()
            WHERE id = 1
        """), {
            "access": data["access_token"],
            "refresh": data["refresh_token"],
            "expires": data["expires_at"],
        })
        logger.info("Strava token refreshed")

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        resp = httpx.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _collect_activities(self, session) -> int:
        """Fetch new activities since last sync."""
        # Watermark: latest activity start_date or 1 year ago
        last_ts = session.execute(
            text("SELECT MAX(start_date) FROM strava.activities")
        ).scalar()

        if last_ts:
            after_epoch = int(last_ts.timestamp())
        else:
            after_epoch = int((datetime.now(timezone.utc) - timedelta(days=365)).timestamp())

        # Paginate through activity list
        activity_ids = []
        page = 1
        while True:
            items = self._get("/athlete/activities", {
                "after": after_epoch,
                "per_page": 30,
                "page": page,
            })
            if not items:
                break
            activity_ids.extend(item["id"] for item in items)
            if len(items) < 30:
                break
            page += 1

        if not activity_ids:
            logger.info("No new activities")
            return 0

        logger.info(f"Fetching details for {len(activity_ids)} activities")

        # Fetch full detail for each activity
        rows = []
        for aid in activity_ids:
            detail = self._get(f"/activities/{aid}")
            rows.append({
                "strava_id": detail["id"],
                "name": detail.get("name"),
                "sport_type": detail.get("sport_type"),
                "start_date": detail["start_date"],
                "distance_m": detail.get("distance"),
                "moving_time_sec": detail.get("moving_time"),
                "elapsed_time_sec": detail.get("elapsed_time"),
                "total_elevation_gain_m": detail.get("total_elevation_gain"),
                "elev_high_m": detail.get("elev_high"),
                "elev_low_m": detail.get("elev_low"),
                "average_speed_mps": detail.get("average_speed"),
                "max_speed_mps": detail.get("max_speed"),
                "average_heartrate": detail.get("average_heartrate"),
                "max_heartrate": detail.get("max_heartrate"),
                "average_cadence": detail.get("average_cadence"),
                "map_polyline": (detail.get("map") or {}).get("summary_polyline"),
                "calories": detail.get("calories"),
            })

        count = bulk_upsert(
            session,
            table="strava.activities",
            rows=rows,
            conflict_columns=["strava_id"],
        )
        logger.info(f"Activities: {count} rows upserted")
        return count


if __name__ == "__main__":
    collector = StravaCollector()
    collector.run()
