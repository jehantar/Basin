# tests/test_db.py
"""Tests for shared.db bulk_upsert helper."""


def test_bulk_upsert_inserts_new_rows(session):
    """Inserting new rows should create them."""
    from shared.db import bulk_upsert

    rows = [
        {"metric_type": "weight", "value": 80.0, "unit": "kg",
         "recorded_at": "2026-01-01T08:00:00Z", "source_name": "iPhone"},
        {"metric_type": "weight", "value": 79.5, "unit": "kg",
         "recorded_at": "2026-01-02T08:00:00Z", "source_name": "iPhone"},
    ]
    count = bulk_upsert(
        session,
        table="healthkit.metrics",
        rows=rows,
        conflict_columns=["metric_type", "recorded_at", "source_name"],
    )
    assert count == 2


def test_bulk_upsert_updates_on_conflict(session):
    """Re-inserting with same key should update, not duplicate."""
    from shared.db import bulk_upsert

    row = {"metric_type": "weight", "value": 80.0, "unit": "kg",
           "recorded_at": "2026-01-01T08:00:00Z", "source_name": "iPhone"}

    bulk_upsert(session, "healthkit.metrics", [row],
                conflict_columns=["metric_type", "recorded_at", "source_name"])

    row["value"] = 81.0
    count = bulk_upsert(session, "healthkit.metrics", [row],
                        conflict_columns=["metric_type", "recorded_at", "source_name"])
    assert count == 1

    from sqlalchemy import text
    result = session.execute(
        text("SELECT value FROM healthkit.metrics WHERE metric_type = 'weight'")
    ).fetchone()
    assert float(result[0]) == 81.0


def test_bulk_upsert_empty_rows(session):
    """Empty input should return 0 and not error."""
    from shared.db import bulk_upsert

    count = bulk_upsert(session, "healthkit.metrics", [],
                        conflict_columns=["metric_type", "recorded_at", "source_name"])
    assert count == 0
