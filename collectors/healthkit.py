"""HealthKit XML dump collector — parses Apple Health export XML."""

import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.healthkit")

IMPORT_DIR = "/data/healthkit/imports"

# Map HealthKit type identifiers to our metric names
METRIC_TYPE_MAP = {
    "HKQuantityTypeIdentifierBodyMass": "weight_body_mass",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierVO2Max": "vo2max",
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierStepCount": "step_count",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_energy",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "heart_rate_variability",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_heart_rate",
    "HKQuantityTypeIdentifierBodyFatPercentage": "body_fat_percentage",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "walking_running_distance",
    "HKQuantityTypeIdentifierFlightsClimbed": "flights_climbed",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate",
    # Running performance
    "HKQuantityTypeIdentifierRunningSpeed": "running_speed",
    "HKQuantityTypeIdentifierRunningPower": "running_power",
    "HKQuantityTypeIdentifierRunningStrideLength": "running_stride_length",
    "HKQuantityTypeIdentifierRunningGroundContactTime": "running_ground_contact_time",
    "HKQuantityTypeIdentifierRunningVerticalOscillation": "running_vertical_oscillation",
    # Daily activity
    "HKQuantityTypeIdentifierBasalEnergyBurned": "basal_energy",
    "HKQuantityTypeIdentifierAppleExerciseTime": "exercise_time",
    "HKQuantityTypeIdentifierAppleStandTime": "stand_time",
    # Walking biomechanics
    "HKQuantityTypeIdentifierWalkingSpeed": "walking_speed",
    "HKQuantityTypeIdentifierWalkingStepLength": "walking_step_length",
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": "walking_double_support_pct",
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": "walking_asymmetry_pct",
}

# Map workout activity types to readable names
WORKOUT_TYPE_MAP = {
    "HKWorkoutActivityTypeRunning": "Running",
    "HKWorkoutActivityTypeCycling": "Cycling",
    "HKWorkoutActivityTypeWalking": "Walking",
    "HKWorkoutActivityTypeHiking": "Hiking",
    "HKWorkoutActivityTypeSwimming": "Swimming",
    "HKWorkoutActivityTypeYoga": "Yoga",
    "HKWorkoutActivityTypeElliptical": "Elliptical",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "Functional Strength",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "Strength Training",
    "HKWorkoutActivityTypeHighIntensityIntervalTraining": "HIIT",
}


def _parse_date(s: str) -> datetime:
    """Parse Apple Health XML date format: '2026-01-15 08:00:00 -0500'."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


class HealthKitCollector(BaseCollector):
    name = "healthkit"

    def collect(self, session) -> int:
        xml_files = [
            os.path.join(IMPORT_DIR, f)
            for f in os.listdir(IMPORT_DIR)
            if f.endswith(".xml")
        ]
        if not xml_files:
            logger.info("No XML files found in import directory")
            return 0

        total = 0
        for xml_path in xml_files:
            logger.info(f"Processing {xml_path}")
            metrics, workouts = self._parse_xml(xml_path)

            total += bulk_upsert(
                session,
                table="healthkit.metrics",
                rows=metrics,
                conflict_columns=["metric_type", "recorded_at", "source_name"],
            )
            total += bulk_upsert(
                session,
                table="healthkit.workouts",
                rows=workouts,
                conflict_columns=["workout_type", "start_time", "source_name"],
            )

        return total

    def _parse_xml(self, path: str) -> tuple[list[dict], list[dict]]:
        """Iteratively parse a Health export XML file."""
        metrics = []
        workouts = []

        for event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "Record":
                row = self._parse_record(elem)
                if row:
                    metrics.append(row)
                elem.clear()

            elif elem.tag == "Workout":
                row = self._parse_workout(elem)
                if row:
                    workouts.append(row)
                elem.clear()

        return metrics, workouts

    def _parse_record(self, elem) -> dict | None:
        hk_type = elem.get("type", "")
        metric_type = METRIC_TYPE_MAP.get(hk_type)
        if metric_type is None:
            return None

        try:
            return {
                "metric_type": metric_type,
                "value": float(elem.get("value", 0)),
                "unit": elem.get("unit", ""),
                "recorded_at": _parse_date(elem.get("startDate")).isoformat(),
                "source_name": elem.get("sourceName"),
            }
        except (ValueError, TypeError):
            return None

    def _parse_workout(self, elem) -> dict | None:
        activity_type = elem.get("workoutActivityType", "")
        workout_type = WORKOUT_TYPE_MAP.get(activity_type, activity_type)

        try:
            start = _parse_date(elem.get("startDate"))
            end = _parse_date(elem.get("endDate"))
        except (ValueError, TypeError):
            return None

        duration_sec = None
        duration_val = elem.get("duration")
        if duration_val:
            duration_sec = float(duration_val)
            if elem.get("durationUnit") == "min":
                duration_sec *= 60

        distance_m = None
        dist_val = elem.get("totalDistance")
        if dist_val:
            distance_m = float(dist_val)
            unit = elem.get("totalDistanceUnit", "")
            if unit == "km":
                distance_m *= 1000
            elif unit == "mi":
                distance_m *= 1609.344

        energy_kcal = None
        energy_val = elem.get("totalEnergyBurned")
        if energy_val and elem.get("totalEnergyBurnedUnit") == "kcal":
            energy_kcal = float(energy_val)

        return {
            "workout_type": workout_type,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_sec": duration_sec,
            "distance_m": distance_m,
            "energy_kcal": energy_kcal,
            "avg_hr": None,
            "max_hr": None,
            "avg_cadence": None,
            "source_name": elem.get("sourceName"),
        }


if __name__ == "__main__":
    collector = HealthKitCollector()
    collector.run()
