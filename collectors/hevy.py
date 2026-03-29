"""Hevy CSV collector — watches drop folder for new workout exports."""

import csv
import hashlib
import logging
import os
from datetime import datetime

from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.hevy")

DROP_DIR = "/data/hevy/drop"

LBS_TO_KG = 0.45359237
MILES_TO_M = 1609.344
KM_TO_M = 1000.0


def _parse_hevy_date(s: str) -> datetime:
    """Parse Hevy date format: '21 Feb 2025, 07:17'."""
    return datetime.strptime(s.strip(), "%d %b %Y, %H:%M")


def _file_hash(path: str) -> str:
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class HevyCollector(BaseCollector):
    name = "hevy"

    def collect(self, session) -> int:
        csv_files = [
            f for f in os.listdir(DROP_DIR)
            if f.endswith(".csv")
        ]
        if not csv_files:
            logger.info("No CSV files in drop folder")
            return 0

        # Get already-imported filenames
        result = session.execute(text("SELECT filename, file_hash FROM basin.hevy_imports"))
        imported = {row[0]: row[1] for row in result.fetchall()}

        total = 0
        for filename in sorted(csv_files):
            filepath = os.path.join(DROP_DIR, filename)
            current_hash = _file_hash(filepath)

            if filename in imported and imported[filename] == current_hash:
                logger.info(f"Skipping already imported: {filename}")
                continue

            logger.info(f"Processing: {filename}")
            row_count = self._process_csv(session, filepath)
            total += row_count

            # Record import
            session.execute(
                text("""
                    INSERT INTO basin.hevy_imports (filename, file_hash, row_count)
                    VALUES (:filename, :hash, :count)
                    ON CONFLICT (filename) DO UPDATE SET
                        file_hash = EXCLUDED.file_hash,
                        row_count = EXCLUDED.row_count,
                        imported_at = now()
                """),
                {"filename": filename, "hash": current_hash, "count": row_count},
            )

        return total

    def _process_csv(self, session, filepath: str) -> int:
        """Parse a Hevy CSV and upsert workouts, exercises, and sets."""
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            # Detect unit system
            has_lbs = "weight_lbs" in headers
            has_miles = "distance_miles" in headers

            rows_by_workout = {}
            for row in reader:
                key = (row["title"], row["start_time"], row["end_time"])
                if key not in rows_by_workout:
                    rows_by_workout[key] = []
                rows_by_workout[key].append(row)

        total = 0

        for (title, start_str, end_str), sets_rows in rows_by_workout.items():
            started_at = _parse_hevy_date(start_str)
            ended_at = _parse_hevy_date(end_str)
            duration_sec = int((ended_at - started_at).total_seconds())

            # Upsert workout
            total += bulk_upsert(
                session,
                table="hevy.workouts",
                rows=[{
                    "title": title,
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "duration_sec": duration_sec,
                }],
                conflict_columns=["started_at"],
            )

            # Get workout_id
            workout_id = session.execute(
                text("SELECT id FROM hevy.workouts WHERE started_at = :ts"),
                {"ts": started_at.isoformat()},
            ).scalar()

            for row in sets_rows:
                exercise_name = row["exercise_title"]

                # Upsert exercise
                total += bulk_upsert(
                    session,
                    table="hevy.exercises",
                    rows=[{"name": exercise_name}],
                    conflict_columns=["name"],
                    update_columns=[],  # Nothing to update
                )

                exercise_id = session.execute(
                    text("SELECT id FROM hevy.exercises WHERE name = :name"),
                    {"name": exercise_name},
                ).scalar()

                # Parse weight (store as lbs)
                weight_lbs = None
                if has_lbs:
                    raw = row.get("weight_lbs", "").strip()
                    if raw:
                        weight_lbs = float(raw)
                else:
                    raw = row.get("weight_kg", "").strip()
                    if raw:
                        weight_lbs = float(raw) / LBS_TO_KG

                # Parse distance
                distance_m = None
                if has_miles:
                    raw = row.get("distance_miles", "").strip()
                    if raw:
                        distance_m = float(raw) * MILES_TO_M
                else:
                    raw = row.get("distance_km", "").strip()
                    if raw:
                        distance_m = float(raw) * KM_TO_M

                # Parse optional fields
                reps_raw = row.get("reps", "").strip()
                reps = int(reps_raw) if reps_raw else None

                dur_raw = row.get("duration_seconds", "").strip()
                dur = int(dur_raw) if dur_raw else None

                rpe_raw = row.get("rpe", "").strip()
                rpe = float(rpe_raw) if rpe_raw else None

                set_type = row.get("set_type", "normal").strip() or "normal"

                total += bulk_upsert(
                    session,
                    table="hevy.sets",
                    rows=[{
                        "workout_id": workout_id,
                        "exercise_id": exercise_id,
                        "set_index": int(row["set_index"]),
                        "set_type": set_type,
                        "weight_lbs": weight_lbs,
                        "reps": reps,
                        "distance_m": distance_m,
                        "duration_sec": dur,
                        "rpe": rpe,
                    }],
                    conflict_columns=["workout_id", "exercise_id", "set_index"],
                )

        return total


if __name__ == "__main__":
    collector = HevyCollector()
    collector.run()
