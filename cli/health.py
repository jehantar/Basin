"""Basin CLI — collector health dashboard."""

import logging
from datetime import datetime, timezone

import click
from sqlalchemy import text

from shared.db import get_session

logger = logging.getLogger("basin.cli")

COLLECTORS = ["healthkit", "hevy", "teller"]


def _time_ago(dt: datetime) -> str:
    """Format a datetime as 'Xh ago' or 'Xd ago'."""
    if dt is None:
        return "never"
    now = datetime.now(timezone.utc)
    delta = now - dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else now - dt
    hours = int(delta.total_seconds() / 3600)
    if hours < 1:
        minutes = int(delta.total_seconds() / 60)
        return f"{minutes}m ago"
    if hours < 48:
        return f"{hours}h ago"
    return f"{delta.days}d ago"


@click.command()
@click.option("--detail", type=str, default=None, help="Show detailed history for a specific collector")
def cli(detail):
    """Basin collector health dashboard."""
    with get_session() as session:
        if detail:
            _show_detail(session, detail)
        else:
            _show_summary(session)


def _show_summary(session):
    click.echo()
    click.echo(f"{'Collector':<14}{'Last Run':<15}{'Status':<10}{'Rows':<8}")
    click.echo("-" * 47)

    for name in COLLECTORS:
        row = session.execute(
            text("""
                SELECT started_at, status, rows_upserted
                FROM basin.collector_runs
                WHERE collector = :name
                ORDER BY started_at DESC
                LIMIT 1
            """),
            {"name": name},
        ).fetchone()

        if row:
            click.echo(f"{name:<14}{_time_ago(row[0]):<15}{row[1]:<10}{row[2] or 0:<8}")
        else:
            click.echo(f"{name:<14}{'never':<15}{'-':<10}{'-':<8}")

    click.echo()


def _show_detail(session, collector: str):
    click.echo(f"\nLast 10 runs for {collector}:")
    rows = session.execute(
        text("""
            SELECT started_at, finished_at, status, rows_upserted, error_message
            FROM basin.collector_runs
            WHERE collector = :name
            ORDER BY started_at DESC
            LIMIT 10
        """),
        {"name": collector},
    ).fetchall()

    if not rows:
        click.echo("  No runs recorded.")
        return

    for row in rows:
        started, finished, status, upserted, error = row
        duration = ""
        if finished and started:
            secs = (finished - started).total_seconds()
            duration = f"{secs:.1f}s"

        ts = started.strftime("%Y-%m-%d %H:%M") if started else "?"
        detail = f"{upserted or 0} rows" if status == "success" else f'"{error[:60]}"' if error else ""
        click.echo(f"  {ts}  {status:<8} {detail:<30} {duration}")

    click.echo()


if __name__ == "__main__":
    cli()
