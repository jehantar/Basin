"""Garmin Connect collector — health metrics, activities, sleep, body battery, HRV, training readiness."""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import polyline as polyline_lib
from garminconnect import Garmin
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.garmin")

# How far back to backfill on first run
DEFAULT_LOOKBACK_DAYS = 90

# Pause between API calls to avoid hammering Garmin
DAILY_METRIC_DELAY = 0.5
ACTIVITY_DETAIL_DELAY = 1.0


class GarminCollector(BaseCollector):
    name = "garmin"

    def __init__(self):
        self.email = os.environ.get("GARMIN_EMAIL", "")
        self.password = os.environ.get("GARMIN_PASSWORD", "")
        self.token_dir = os.environ.get("GARMIN_TOKEN_DIR", "/data/garmin/tokens")

    def collect(self, session) -> int:
        if not self.email or not self.password:
            raise RuntimeError("GARMIN_EMAIL and GARMIN_PASSWORD required")

        client = self._get_client()
        total = 0
        total += self._collect_daily_summary(session, client)
        total += self._collect_sleep(session, client)
        total += self._collect_body_battery(session, client)
        total += self._collect_hrv(session, client)
        total += self._collect_training_readiness(session, client)
        total += self._collect_activities(session, client)
        total += self._collect_body_composition(session, client)
        return total

    def _get_client(self) -> Garmin:
        """Authenticate with Garmin Connect. Uses saved tokens if available."""
        os.makedirs(self.token_dir, exist_ok=True)
        client = Garmin(self.email, self.password)
        client.login(tokenstore=self.token_dir)
        return client

    # ------------------------------------------------------------------
    # Daily health metrics (one row per day)
    # ------------------------------------------------------------------

    def _collect_daily_summary(self, session, client: Garmin) -> int:
        """Daily stats: steps, calories, HR, VO2, stress, SpO2, etc."""
        dates = self._dates_to_fetch(session, "garmin.daily_summary")
        if not dates:
            logger.info("daily_summary: up to date")
            return 0

        rows = []
        for d in dates:
            try:
                data = client.get_stats(d.isoformat())
            except Exception as e:
                logger.warning(f"daily_summary {d}: {e}")
                time.sleep(DAILY_METRIC_DELAY)
                continue

            rows.append({
                "date": d,
                "total_steps": _get(data, "totalSteps"),
                "total_distance_m": _get(data, "totalDistanceMeters"),
                "active_calories": _get(data, "activeKilocalories"),
                "resting_hr": _get(data, "restingHeartRate"),
                "max_hr": _get(data, "maxHeartRate"),
                "min_hr": _get(data, "minHeartRate"),
                "avg_stress": _get(data, "averageStressLevel"),
                "floors_climbed": _get(data, "floorsAscended"),
                "intensity_minutes": _safe_add(
                    _get(data, "moderateIntensityMinutes"),
                    _get(data, "vigorousIntensityMinutes"),
                ),
                "vo2max_running": _get(data, "vo2MaxValue"),
                "respiration_avg": _get(data, "averageSpo2"),  # mapped below
                "spo2_avg": _get(data, "averageSpo2"),
                "raw_json": json.dumps(data),
            })
            time.sleep(DAILY_METRIC_DELAY)

        # Fix: respiration comes from a different field
        for row in rows:
            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
            row["respiration_avg"] = _get(raw, "averageRespirationRate") or _get(raw, "avgWakingRespirationValue")

        count = bulk_upsert(session, "garmin.daily_summary", rows, conflict_columns=["date"])
        logger.info(f"daily_summary: {count} rows upserted")
        return count

    def _collect_sleep(self, session, client: Garmin) -> int:
        """Sleep score and stage breakdown."""
        dates = self._dates_to_fetch(session, "garmin.sleep")
        if not dates:
            logger.info("sleep: up to date")
            return 0

        rows = []
        for d in dates:
            try:
                data = client.get_sleep_data(d.isoformat())
            except Exception as e:
                logger.warning(f"sleep {d}: {e}")
                time.sleep(DAILY_METRIC_DELAY)
                continue

            daily = data.get("dailySleepDTO", {}) if data else {}
            score_data = data.get("sleepScores", {}) if data else {}

            rows.append({
                "date": d,
                "sleep_score": _get(score_data, "overall", "value") or _get(score_data, "totalScore", "value"),
                "total_sleep_sec": _get(daily, "sleepTimeInSeconds"),
                "deep_sleep_sec": _get(daily, "deepSleepSeconds"),
                "light_sleep_sec": _get(daily, "lightSleepSeconds"),
                "rem_sleep_sec": _get(daily, "remSleepSeconds"),
                "awake_sec": _get(daily, "awakeSleepSeconds"),
                "avg_spo2": _get(daily, "averageSpO2Value"),
                "avg_respiration": _get(daily, "averageRespirationValue"),
                "body_battery_change": _get(daily, "bodyBatteryChange"),
                "raw_json": json.dumps(data),
            })
            time.sleep(DAILY_METRIC_DELAY)

        count = bulk_upsert(session, "garmin.sleep", rows, conflict_columns=["date"])
        logger.info(f"sleep: {count} rows upserted")
        return count

    def _collect_body_battery(self, session, client: Garmin) -> int:
        """Body battery daily high/low/charge/drain."""
        dates = self._dates_to_fetch(session, "garmin.body_battery")
        if not dates:
            logger.info("body_battery: up to date")
            return 0

        rows = []
        for d in dates:
            try:
                data = client.get_body_battery(d.isoformat())
            except Exception as e:
                logger.warning(f"body_battery {d}: {e}")
                time.sleep(DAILY_METRIC_DELAY)
                continue

            # get_body_battery returns a list; find the entry for this date
            day_data = _find_date_entry(data, d) if isinstance(data, list) else data
            if not day_data:
                time.sleep(DAILY_METRIC_DELAY)
                continue

            rows.append({
                "date": d,
                "highest": _get(day_data, "bodyBatteryHighestValue") or _get(day_data, "charged"),
                "lowest": _get(day_data, "bodyBatteryLowestValue") or _get(day_data, "drained"),
                "start_of_day": _get(day_data, "bodyBatteryMostRecentValue"),
                "end_of_day": _get(day_data, "bodyBatteryDynamicFeedbackEvent"),
                "charged": _get(day_data, "bodyBatteryChargedValue") or _get(day_data, "charged"),
                "drained": _get(day_data, "bodyBatteryDrainedValue") or _get(day_data, "drained"),
                "raw_json": json.dumps(day_data),
            })
            time.sleep(DAILY_METRIC_DELAY)

        count = bulk_upsert(session, "garmin.body_battery", rows, conflict_columns=["date"])
        logger.info(f"body_battery: {count} rows upserted")
        return count

    def _collect_hrv(self, session, client: Garmin) -> int:
        """Nightly HRV with baseline."""
        dates = self._dates_to_fetch(session, "garmin.hrv")
        if not dates:
            logger.info("hrv: up to date")
            return 0

        rows = []
        for d in dates:
            try:
                data = client.get_hrv_data(d.isoformat())
            except Exception as e:
                logger.warning(f"hrv {d}: {e}")
                time.sleep(DAILY_METRIC_DELAY)
                continue

            if not data:
                time.sleep(DAILY_METRIC_DELAY)
                continue

            summary = data.get("hrvSummary", {}) if data else {}
            baseline = data.get("baseline", {}) or data.get("startTimestampLocal", {})

            rows.append({
                "date": d,
                "weekly_avg_ms": _get(summary, "weeklyAvg"),
                "last_night_avg_ms": _get(summary, "lastNightAvg"),
                "last_night_5min_high_ms": _get(summary, "lastNight5MinHigh"),
                "baseline_low_ms": _get(baseline, "lowUpper") or _get(summary, "baselineLowUpper"),
                "baseline_upper_ms": _get(baseline, "balancedLow") or _get(summary, "baselineBalancedLow"),
                "status": _get(summary, "status") or _get(data, "status"),
                "raw_json": json.dumps(data),
            })
            time.sleep(DAILY_METRIC_DELAY)

        count = bulk_upsert(session, "garmin.hrv", rows, conflict_columns=["date"])
        logger.info(f"hrv: {count} rows upserted")
        return count

    def _collect_training_readiness(self, session, client: Garmin) -> int:
        """Training readiness score and sub-components."""
        dates = self._dates_to_fetch(session, "garmin.training_readiness")
        if not dates:
            logger.info("training_readiness: up to date")
            return 0

        rows = []
        for d in dates:
            try:
                data = client.get_training_readiness(d.isoformat())
            except Exception as e:
                logger.warning(f"training_readiness {d}: {e}")
                time.sleep(DAILY_METRIC_DELAY)
                continue

            if not data:
                time.sleep(DAILY_METRIC_DELAY)
                continue

            rows.append({
                "date": d,
                "score": _get(data, "score") or _get(data, "trainingReadinessScore"),
                "level": _get(data, "level") or _get(data, "trainingReadinessLevel"),
                "sleep_score": _get(data, "sleepScore") or _get(data, "sleepScoreValue"),
                "recovery_score": _get(data, "recoveryScore") or _get(data, "recoveryScoreValue"),
                "training_load_score": _get(data, "trainingLoadScore") or _get(data, "acuteTrainingLoadScore"),
                "hrv_score": _get(data, "hrvScore") or _get(data, "hrvStatusScore"),
                "raw_json": json.dumps(data),
            })
            time.sleep(DAILY_METRIC_DELAY)

        count = bulk_upsert(session, "garmin.training_readiness", rows, conflict_columns=["date"])
        logger.info(f"training_readiness: {count} rows upserted")
        return count

    # ------------------------------------------------------------------
    # Activities (with splits + best-effort GPS)
    # ------------------------------------------------------------------

    def _collect_activities(self, session, client: Garmin) -> int:
        """Fetch new activities with splits and GPS polyline (best-effort)."""
        last_ts = session.execute(
            text("SELECT MAX(start_time) FROM garmin.activities")
        ).scalar()

        if last_ts:
            start_date = (last_ts - timedelta(hours=1)).strftime("%Y-%m-%d")
        else:
            start_date = (date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()

        end_date = date.today().isoformat()

        try:
            activities = client.get_activities_by_date(start_date, end_date)
        except Exception as e:
            logger.error(f"activities list: {e}")
            return 0

        if not activities:
            logger.info("activities: no new activities")
            return 0

        # Filter out already-seen activities
        existing_ids = set()
        if last_ts:
            result = session.execute(
                text("SELECT garmin_id FROM garmin.activities WHERE start_time >= :since"),
                {"since": last_ts - timedelta(hours=1)},
            )
            existing_ids = {row[0] for row in result.fetchall()}

        rows = []
        for act in activities:
            garmin_id = act.get("activityId")
            if not garmin_id or garmin_id in existing_ids:
                continue

            # Fetch splits
            splits_json = None
            try:
                splits_data = client.get_activity_splits(str(garmin_id))
                splits_json = self._parse_splits(splits_data)
            except Exception as e:
                logger.warning(f"splits for {garmin_id}: {e}")
            time.sleep(ACTIVITY_DETAIL_DELAY)

            # Fetch GPS polyline (best-effort)
            map_polyline = None
            try:
                gpx_bytes = client.download_activity(
                    str(garmin_id),
                    dl_fmt=Garmin.ActivityDownloadFormat.GPX,
                )
                map_polyline = self._gpx_to_polyline(gpx_bytes)
            except Exception as e:
                logger.debug(f"gpx for {garmin_id}: {e}")
            time.sleep(ACTIVITY_DETAIL_DELAY)

            start_time = _parse_timestamp(act.get("startTimeGMT") or act.get("startTimeLocal"))

            rows.append({
                "garmin_id": garmin_id,
                "name": act.get("activityName"),
                "activity_type": (act.get("activityType") or {}).get("typeKey"),
                "sport_type": (act.get("sportType") or {}).get("typeKey") if isinstance(act.get("sportType"), dict) else act.get("sportType"),
                "start_time": start_time,
                "duration_sec": act.get("duration"),
                "distance_m": act.get("distance"),
                "avg_hr": act.get("averageHR"),
                "max_hr": act.get("maxHR"),
                "avg_cadence": _cadence_spm(act.get("averageRunningCadenceInStepsPerMinute") or act.get("averageBikingCadenceInRevPerMinute")),
                "max_cadence": _cadence_spm(act.get("maxRunningCadenceInStepsPerMinute") or act.get("maxBikingCadenceInRevPerMinute")),
                "avg_power": act.get("avgPower"),
                "avg_speed_mps": act.get("averageSpeed"),
                "max_speed_mps": act.get("maxSpeed"),
                "elevation_gain_m": act.get("elevationGain"),
                "calories": act.get("calories"),
                "training_effect_aerobic": act.get("aerobicTrainingEffect"),
                "training_effect_anaerobic": act.get("anaerobicTrainingEffect"),
                "vo2max_value": act.get("vO2MaxValue"),
                "avg_ground_contact_time_ms": act.get("avgGroundContactTime"),
                "avg_vertical_oscillation_cm": act.get("avgVerticalOscillation"),
                "avg_stride_length_m": _cm_to_m(act.get("avgStrideLength")),
                "splits": json.dumps(splits_json) if splits_json else None,
                "map_polyline": map_polyline,
                "raw_json": json.dumps(act),
            })

        if not rows:
            logger.info("activities: no new activities to upsert")
            return 0

        count = bulk_upsert(session, "garmin.activities", rows, conflict_columns=["garmin_id"])
        logger.info(f"activities: {count} rows upserted")
        return count

    def _parse_splits(self, splits_data: dict) -> list[dict] | None:
        """Parse Garmin splits into Strava-compatible format for dashboard."""
        if not splits_data:
            return None

        # Garmin returns splits in lapDTOs or splitSummaries
        laps = splits_data.get("lapDTOs") or splits_data.get("splitDTOs") or []
        if not laps:
            return None

        splits = []
        for i, lap in enumerate(laps, 1):
            avg_speed_mps = lap.get("averageSpeed") or lap.get("averageMovingSpeed") or 0
            splits.append({
                "split": i,
                "average_speed": avg_speed_mps,
                "elevation_difference": lap.get("elevationGain", 0) - lap.get("elevationLoss", 0) if lap.get("elevationGain") is not None else None,
                "average_heartrate": lap.get("averageHR"),
                "distance": lap.get("distance"),
                "duration": lap.get("duration") or lap.get("movingDuration"),
            })

        return splits if splits else None

    def _gpx_to_polyline(self, gpx_bytes: bytes) -> str | None:
        """Extract GPS track from GPX XML and encode as Google polyline."""
        if not gpx_bytes:
            return None

        try:
            root = ET.parse(BytesIO(gpx_bytes)).getroot()
        except ET.ParseError:
            return None

        # GPX namespace
        ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
        points = []

        for trkpt in root.findall(".//gpx:trkpt", ns):
            lat = trkpt.get("lat")
            lon = trkpt.get("lon")
            if lat and lon:
                points.append((float(lat), float(lon)))

        if len(points) < 2:
            return None

        # Downsample to ~200 points for storage efficiency (matches Strava summary_polyline density)
        if len(points) > 200:
            step = len(points) / 200
            points = [points[int(i * step)] for i in range(200)]

        return polyline_lib.encode(points)

    # ------------------------------------------------------------------
    # Body Composition
    # ------------------------------------------------------------------

    def _collect_body_composition(self, session, client: Garmin) -> int:
        """Body composition (weight, body fat, etc.)."""
        last_date = session.execute(
            text("SELECT MAX(date) FROM garmin.body_composition")
        ).scalar()

        start_date = (last_date or date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
        end_date = date.today().isoformat()

        try:
            data = client.get_body_composition(start_date, end_date)
        except Exception as e:
            logger.warning(f"body_composition: {e}")
            return 0

        if not data:
            logger.info("body_composition: no data")
            return 0

        weigh_ins = data.get("dateWeightList") or data.get("dailyWeightSummaries") or []
        if not weigh_ins:
            logger.info("body_composition: no weigh-ins")
            return 0

        rows = []
        for entry in weigh_ins:
            weight_grams = entry.get("weight") or entry.get("samplePk")
            weight_kg = weight_grams / 1000.0 if weight_grams and weight_grams > 100 else weight_grams

            ts_ms = entry.get("date") or entry.get("samplePk")
            if isinstance(ts_ms, (int, float)) and ts_ms > 1e12:
                recorded_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                d = recorded_at.date()
            else:
                d = date.fromisoformat(str(ts_ms)[:10]) if ts_ms else date.today()
                recorded_at = None

            rows.append({
                "date": d,
                "recorded_at": recorded_at,
                "weight_kg": weight_kg,
                "bmi": entry.get("bmi"),
                "body_fat_pct": entry.get("bodyFat"),
                "muscle_mass_kg": _grams_to_kg(entry.get("muscleMass")),
                "bone_mass_kg": _grams_to_kg(entry.get("boneMass")),
                "body_water_pct": entry.get("bodyWater"),
                "raw_json": json.dumps(entry),
            })

        count = bulk_upsert(
            session, "garmin.body_composition", rows,
            conflict_columns=["date", "recorded_at"],
        )
        logger.info(f"body_composition: {count} rows upserted")
        return count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dates_to_fetch(self, session, table: str) -> list[date]:
        """Return dates from watermark+1 through yesterday."""
        last_date = session.execute(
            text(f"SELECT MAX(date) FROM {table}")  # noqa: S608 — table name is hardcoded by callers
        ).scalar()

        start = (last_date + timedelta(days=1)) if last_date else (date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS))
        end = date.today() - timedelta(days=1)  # Yesterday (today's data may be incomplete)

        if start > end:
            return []

        return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _get(data: dict | None, *keys):
    """Safely traverse nested dict keys, returning None on any miss."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _safe_add(a, b):
    """Add two values, treating None as 0."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _find_date_entry(entries: list, target_date: date) -> dict | None:
    """Find an entry matching the target date in a list of dicts."""
    target_str = target_date.isoformat()
    for entry in entries:
        entry_date = entry.get("calendarDate") or entry.get("date") or ""
        if str(entry_date)[:10] == target_str:
            return entry
    return entries[0] if len(entries) == 1 else None


def _parse_timestamp(ts_str: str | None) -> datetime | None:
    """Parse Garmin timestamp string to aware datetime."""
    if not ts_str:
        return None
    try:
        # Garmin uses "2026-04-27 08:30:00" format (UTC for startTimeGMT)
        dt = datetime.fromisoformat(ts_str.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _cadence_spm(value) -> float | None:
    """Return cadence in steps/min (Garmin may already provide this)."""
    if value is None:
        return None
    return float(value)


def _cm_to_m(value) -> float | None:
    """Convert centimeters to meters."""
    if value is None:
        return None
    # Garmin sometimes gives stride length in cm, sometimes m
    if value > 10:  # Likely cm
        return value / 100.0
    return value


def _grams_to_kg(value) -> float | None:
    """Convert grams to kg if the value looks like grams."""
    if value is None:
        return None
    if value > 200:  # Likely grams
        return value / 1000.0
    return value


if __name__ == "__main__":
    collector = GarminCollector()
    collector.run()
