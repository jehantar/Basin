"""Database engine, session management, and upsert helpers."""

from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shared.config import load_config

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        config = load_config()
        _engine = create_engine(
            config.database_url,
            pool_size=5,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal


@contextmanager
def get_session():
    """Yield a SQLAlchemy session that auto-commits on success, rolls back on error."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_upsert(
    session: Session,
    table: str,
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
) -> int:
    """
    Upsert rows into a table using ON CONFLICT DO UPDATE.

    Args:
        session: SQLAlchemy session
        table: Fully qualified table name (e.g., 'healthkit.metrics')
        rows: List of dicts, each dict is one row
        conflict_columns: Columns forming the unique constraint
        update_columns: Columns to update on conflict. If None, updates all non-conflict columns.

    Returns:
        Number of rows affected.
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    if update_columns is None:
        update_columns = [c for c in columns if c not in conflict_columns]

    # Safety check for dynamic SQL identifiers in table/column names
    safe_ident = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
    if not safe_ident.match(table):
        raise ValueError(f"Invalid table name: {table}")
    for name in [*columns, *conflict_columns, *update_columns]:
        if not safe_ident.match(name):
            raise ValueError(f"Invalid column name: {name}")

    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    conflict_list = ", ".join(conflict_columns)

    if update_columns:
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
        conflict_action = f"DO UPDATE SET {update_set}"
    else:
        conflict_action = "DO NOTHING"

    sql = f"""
        INSERT INTO {table} ({col_list})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_list}) {conflict_action}
    """

    result = session.execute(text(sql), rows)
    return result.rowcount
