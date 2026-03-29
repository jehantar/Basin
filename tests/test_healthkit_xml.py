"""Tests for HealthKit XML import collector."""

import os
import tempfile

from sqlalchemy import text


SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData>
<HealthData locale="en_US">
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Withings"
         unit="kg" value="80.5"
         startDate="2026-01-15 08:00:00 -0500" endDate="2026-01-15 08:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Apple Watch"
         unit="count/min" value="58"
         startDate="2026-01-15 00:00:00 -0500" endDate="2026-01-15 00:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierVO2Max" sourceName="Apple Watch"
         unit="mL/min*kg" value="42.5"
         startDate="2026-01-15 00:00:00 -0500" endDate="2026-01-15 00:00:00 -0500"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
          sourceName="Apple Watch"
          duration="45" durationUnit="min"
          totalDistance="5.2" totalDistanceUnit="km"
          totalEnergyBurned="450" totalEnergyBurnedUnit="kcal"
          startDate="2026-01-15 07:00:00 -0500" endDate="2026-01-15 07:45:00 -0500"/>
</HealthData>
"""


def test_parse_xml_metrics(session, monkeypatch, tmp_path):
    from collectors.healthkit import HealthKitCollector

    xml_file = tmp_path / "export.xml"
    xml_file.write_text(SAMPLE_XML)

    monkeypatch.setattr("collectors.healthkit.IMPORT_DIR", str(tmp_path))
    collector = HealthKitCollector()
    count = collector.collect(session)

    # 3 metrics + 1 workout = 4
    assert count == 4

    metrics = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert metrics == 3

    workouts = session.execute(text("SELECT count(*) FROM healthkit.workouts")).scalar()
    assert workouts == 1


def test_xml_import_idempotent(session, monkeypatch, tmp_path):
    from collectors.healthkit import HealthKitCollector

    xml_file = tmp_path / "export.xml"
    xml_file.write_text(SAMPLE_XML)

    monkeypatch.setattr("collectors.healthkit.IMPORT_DIR", str(tmp_path))
    collector = HealthKitCollector()
    collector.collect(session)
    collector.collect(session)

    metrics = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert metrics == 3  # No duplicates
