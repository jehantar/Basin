"""Tests for BaseCollector run tracking."""

from datetime import datetime, timezone

from sqlalchemy import text


class FakeCollector:
    """Test collector that returns a fixed count or raises."""

    def __init__(self, name, result=0, error=None):
        self._name = name
        self._result = result
        self._error = error

    @property
    def name(self):
        return self._name

    def collect(self, session):
        if self._error:
            raise self._error
        return self._result


def test_successful_run_records_success(session, monkeypatch):
    from collectors.base import BaseCollector

    class SuccessCollector(BaseCollector):
        name = "test_success"
        def collect(self, session):
            return 5

    monkeypatch.setattr("collectors.base.get_session", lambda: _FakeCtx(session))
    collector = SuccessCollector()
    collector.run()

    row = session.execute(
        text("SELECT status, rows_upserted FROM basin.collector_runs WHERE collector = 'test_success' ORDER BY id DESC LIMIT 1")
    ).fetchone()
    assert row[0] == "success"
    assert row[1] == 5


def test_failed_run_records_error(session, monkeypatch):
    from collectors.base import BaseCollector

    class FailCollector(BaseCollector):
        name = "test_fail"
        def collect(self, session):
            raise ValueError("something broke")

    monkeypatch.setattr("collectors.base.get_session", lambda: _FakeCtx(session))
    monkeypatch.setattr("collectors.base.send_alert", lambda msg, **kw: True)
    collector = FailCollector()
    collector.run()

    row = session.execute(
        text("SELECT status, error_message FROM basin.collector_runs WHERE collector = 'test_fail' ORDER BY id DESC LIMIT 1")
    ).fetchone()
    assert row[0] == "error"
    assert "something broke" in row[1]


class _FakeCtx:
    """Fake context manager that yields a session without committing."""
    def __init__(self, session):
        self._session = session
    def __enter__(self):
        return self._session
    def __exit__(self, *args):
        pass
