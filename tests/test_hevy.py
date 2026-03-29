"""Tests for Hevy CSV collector."""

import os
import textwrap

from sqlalchemy import text


SAMPLE_CSV_METRIC = textwrap.dedent("""\
    "title","start_time","end_time","description","exercise_title","superset_id","exercise_notes","set_index","set_type","weight_kg","reps","distance_km","duration_seconds","rpe"
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Bench Press (Barbell)",,"",0,"normal",80,10,,,
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Bench Press (Barbell)",,"",1,"normal",80,8,,,
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Overhead Press (Dumbbell)",,"",0,"warmup",15,12,,,
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Overhead Press (Dumbbell)",,"",1,"normal",25,10,,,8
""")

SAMPLE_CSV_IMPERIAL = textwrap.dedent("""\
    "title","start_time","end_time","description","exercise_title","superset_id","exercise_notes","set_index","set_type","weight_lbs","reps","distance_miles","duration_seconds","rpe"
    "Leg Day","22 Feb 2025, 09:00","22 Feb 2025, 10:00","","Squat (Barbell)",,"",0,"normal",225,5,,,
""")


def test_parse_metric_csv(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_2025-02-21.csv"
    csv_file.write_text(SAMPLE_CSV_METRIC)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    count = collector.collect(session)

    # 1 workout + 2 exercises + 4 sets = 7
    assert count == 7

    workouts = session.execute(text("SELECT count(*) FROM hevy.workouts")).scalar()
    assert workouts == 1

    exercises = session.execute(text("SELECT count(*) FROM hevy.exercises")).scalar()
    assert exercises == 2

    sets = session.execute(text("SELECT count(*) FROM hevy.sets")).scalar()
    assert sets == 4

    # Verify the import was recorded
    imports = session.execute(text("SELECT filename FROM basin.hevy_imports")).fetchall()
    assert len(imports) == 1
    assert imports[0][0] == "workout_2025-02-21.csv"


def test_skip_already_imported(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_2025-02-21.csv"
    csv_file.write_text(SAMPLE_CSV_METRIC)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    collector.collect(session)
    count = collector.collect(session)

    assert count == 0  # Already imported, skip


def test_parse_imperial_csv(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_imperial.csv"
    csv_file.write_text(SAMPLE_CSV_IMPERIAL)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    count = collector.collect(session)

    # 1 workout + 1 exercise + 1 set = 3
    assert count == 3

    # Check weight was converted to kg (225 lbs = 102.058 kg)
    weight = session.execute(
        text("SELECT weight_kg FROM hevy.sets LIMIT 1")
    ).scalar()
    assert abs(float(weight) - 102.058) < 0.1


def test_rpe_parsed(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_rpe.csv"
    csv_file.write_text(SAMPLE_CSV_METRIC)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    collector.collect(session)

    rpe = session.execute(
        text("SELECT rpe FROM hevy.sets WHERE rpe IS NOT NULL")
    ).scalar()
    assert float(rpe) == 8.0
