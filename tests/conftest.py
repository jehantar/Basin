"""Shared test fixtures — Postgres-backed test database."""

import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Use a test database URL; default to local Postgres for dev
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://basin:basin@localhost:5432/basin_test",
)


@pytest.fixture(scope="session")
def engine():
    """Create a test engine. Requires a running Postgres instance."""
    eng = create_engine(TEST_DATABASE_URL)
    # Run the migration to set up schemas
    migration_path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "001_initial.sql"
    )
    with open(migration_path) as f:
        sql = f.read()
    # Strip BEGIN/COMMIT from migration SQL since SQLAlchemy manages its own transactions
    clean_sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    with eng.connect() as conn:
        conn.execute(text("""
            DROP SCHEMA IF EXISTS healthkit CASCADE;
            DROP SCHEMA IF EXISTS hevy CASCADE;
            DROP SCHEMA IF EXISTS schwab CASCADE;
            DROP SCHEMA IF EXISTS teller CASCADE;
            DROP SCHEMA IF EXISTS basin CASCADE;
        """))
        conn.execute(text(clean_sql))
        conn.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Yield a session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(session, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("webhook.server.get_session", lambda: _FakeCtx(session))
    monkeypatch.setattr("webhook.dashboard.get_session", lambda: _FakeCtx(session))
    monkeypatch.setattr("webhook.finance.get_session", lambda: _FakeCtx(session))
    from webhook.server import app
    return TestClient(app)


class _FakeCtx:
    def __init__(self, session):
        self._session = session
    def __enter__(self):
        return self._session
    def __exit__(self, *args):
        pass
