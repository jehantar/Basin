"""Tests for CLI health dashboard."""

from datetime import datetime, timezone, timedelta
from click.testing import CliRunner
from sqlalchemy import text


def _seed_collector_runs(session):
    """Insert sample collector runs for testing."""
    now = datetime.now(timezone.utc)
    runs = [
        ("healthkit", now - timedelta(hours=2), now - timedelta(hours=2, seconds=-12), "success", 142),
        ("hevy", now - timedelta(hours=18), now - timedelta(hours=18, seconds=-8), "success", 86),
        ("teller", now - timedelta(hours=17), now - timedelta(hours=17, seconds=-10), "success", 204),
    ]
    for collector, started, finished, status, rows in runs:
        session.execute(
            text("""
                INSERT INTO basin.collector_runs (collector, started_at, finished_at, status, rows_upserted)
                VALUES (:c, :s, :f, :st, :r)
            """),
            {"c": collector, "s": started, "f": finished, "st": status, "r": rows},
        )


def test_health_summary(session, monkeypatch):
    from cli.health import cli

    _seed_collector_runs(session)
    monkeypatch.setattr("cli.health.get_session", lambda: _FakeCtx(session))

    runner = CliRunner()
    result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert "healthkit" in result.output
    assert "hevy" in result.output
    assert "teller" in result.output
    assert "success" in result.output


def test_health_detail(session, monkeypatch):
    from cli.health import cli

    _seed_collector_runs(session)
    monkeypatch.setattr("cli.health.get_session", lambda: _FakeCtx(session))

    runner = CliRunner()
    result = runner.invoke(cli, ["--detail", "teller"])

    assert result.exit_code == 0
    assert "teller" in result.output
    assert "204" in result.output


class _FakeCtx:
    def __init__(self, session):
        self._session = session
    def __enter__(self):
        return self._session
    def __exit__(self, *args):
        pass
