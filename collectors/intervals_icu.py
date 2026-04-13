"""Intervals.icu collector — fetches training load, pace curves, HR curves."""

import logging
import os
from datetime import date, timedelta

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.intervals_icu")

API_BASE = "https://intervals.icu/api/v1"


class IntervalsICUCollector(BaseCollector):
    name = "intervals_icu"

    def __init__(self):
        self.api_key = os.environ.get("INTERVALS_ICU_API_KEY", "")
        self.athlete_id = os.environ.get("INTERVALS_ICU_ATHLETE_ID", "")

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{API_BASE}{path}"
        resp = httpx.get(url, auth=("API_KEY", self.api_key), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def collect(self, session) -> int:
        if not self.api_key or not self.athlete_id:
            raise RuntimeError("INTERVALS_ICU_API_KEY and INTERVALS_ICU_ATHLETE_ID required")

        total = 0
        total += self._collect_fitness(session)
        total += self._collect_pace_curves(session)
        total += self._collect_hr_curves(session)
        return total

    def _collect_fitness(self, session) -> int:
        """Fetch daily CTL/ATL/TSB from the wellness endpoint."""
        # Start from last collected date or 1 year ago
        last_date = session.execute(
            text("SELECT MAX(date) FROM intervals.daily_fitness")
        ).scalar()

        oldest = (last_date - timedelta(days=1)) if last_date else (date.today() - timedelta(days=365))
        newest = date.today()

        data = self._get(
            f"/athlete/{self.athlete_id}/wellness",
            {"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        )

        rows = []
        for day in data:
            ctl = day.get("ctl") or 0
            atl = day.get("atl") or 0
            load = day.get("ctlLoad") or day.get("atlLoad")
            rows.append({
                "date": day["id"],
                "ctl": ctl,
                "atl": atl,
                "tsb": round(ctl - atl, 2),
                "ramp_rate": day.get("rampRate"),
                "training_load": load,
            })

        count = bulk_upsert(
            session,
            table="intervals.daily_fitness",
            rows=rows,
            conflict_columns=["date"],
        )
        logger.info(f"Fitness: {count} days upserted")
        return count

    def _collect_pace_curves(self, session) -> int:
        """Fetch best-effort pace curve for running."""
        data = self._get(
            f"/athlete/{self.athlete_id}/pace-curves",
            {"type": "Run"},
        )

        today = date.today().isoformat()
        rows = []
        for period in data.get("list", []):
            label = period.get("label", "unknown")
            distances = period.get("distance", [])
            values = period.get("values", [])
            for i, dist in enumerate(distances):
                if i < len(values) and values[i] and values[i] > 0:
                    rows.append({
                        "captured_at": today,
                        "period": label,
                        "distance_m": dist,
                        "time_secs": values[i],
                    })

        count = bulk_upsert(
            session,
            table="intervals.pace_curves",
            rows=rows,
            conflict_columns=["captured_at", "period", "distance_m"],
        )
        logger.info(f"Pace curves: {count} rows upserted")
        return count

    def _collect_hr_curves(self, session) -> int:
        """Fetch peak HR curve for running."""
        data = self._get(
            f"/athlete/{self.athlete_id}/hr-curves",
            {"type": "Run"},
        )

        today = date.today().isoformat()
        rows = []
        for period in data.get("list", []):
            label = period.get("label", "unknown")
            secs = period.get("secs", [])
            values = period.get("values", [])
            for i, s in enumerate(secs):
                if i < len(values) and values[i] and values[i] > 0:
                    rows.append({
                        "captured_at": today,
                        "period": label,
                        "duration_secs": s,
                        "hr_bpm": values[i],
                    })

        count = bulk_upsert(
            session,
            table="intervals.hr_curves",
            rows=rows,
            conflict_columns=["captured_at", "period", "duration_secs"],
        )
        logger.info(f"HR curves: {count} rows upserted")
        return count


if __name__ == "__main__":
    collector = IntervalsICUCollector()
    collector.run()
