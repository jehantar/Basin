"""BaseCollector — run tracking, error handling, logging."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from sqlalchemy import text

from shared.db import get_session
from shared.telegram import send_alert

logger = logging.getLogger("basin.collector")


class BaseCollector(ABC):
    """Base class for all data collectors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Collector identifier, e.g. 'healthkit', 'hevy'."""
        ...

    @abstractmethod
    def collect(self, session) -> int:
        """Run the collection. Returns number of rows upserted."""
        ...

    def run(self):
        """Execute the collector with run tracking."""
        with get_session() as session:
            run_id = self._start_run(session)
            try:
                count = self.collect(session)
                self._finish_run(session, run_id, "success", count)
                logger.info(f"[{self.name}] success: {count} rows upserted")
            except Exception as e:
                self._finish_run(session, run_id, "error", error=str(e))
                logger.error(f"[{self.name}] error: {e}")
                self._maybe_alert(session, str(e))
                # Don't re-raise — cron should not retry on its own

    def _start_run(self, session) -> int:
        result = session.execute(
            text("""
                INSERT INTO basin.collector_runs (collector, started_at, status)
                VALUES (:collector, :now, 'running')
                RETURNING id
            """),
            {"collector": self.name, "now": datetime.now(timezone.utc)},
        )
        return result.scalar()

    def _finish_run(self, session, run_id: int, status: str, rows: int = 0, error: str = None):
        session.execute(
            text("""
                UPDATE basin.collector_runs
                SET finished_at = :now, status = :status,
                    rows_upserted = :rows, error_message = :error
                WHERE id = :id
            """),
            {
                "now": datetime.now(timezone.utc),
                "status": status,
                "rows": rows,
                "error": error,
                "id": run_id,
            },
        )

    def _maybe_alert(self, session, error_msg: str):
        """Send Telegram alert if there have been 3+ consecutive failures."""
        result = session.execute(
            text("""
                SELECT status FROM basin.collector_runs
                WHERE collector = :name
                ORDER BY started_at DESC
                LIMIT 3
            """),
            {"name": self.name},
        )
        statuses = [row[0] for row in result.fetchall()]
        if len(statuses) >= 3 and all(s == "error" for s in statuses):
            send_alert(
                f"*{self.name}* collector has failed 3 times in a row.\n"
                f"Latest error: `{error_msg[:200]}`"
            )
