"""Nasdaq Data Link collector — fetches daily stock prices from SHARADAR/SEP."""

import logging
import os
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.nasdaq")

SHARADAR_API = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/SEP"
LOOKBACK_YEARS = 10
BATCH_SIZE = 10          # tickers per API request
PAGE_SIZE = 10000        # max rows per API page
RATE_LIMIT_SLEEP = 1.0   # seconds between paginated calls
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0    # seconds, doubles each retry


class NasdaqCollector(BaseCollector):
    name = "nasdaq"

    def collect(self, session) -> int:
        api_key = os.environ.get("NASDAQ_DATA_LINK_API_KEY", "")
        if not api_key:
            logger.warning("NASDAQ_DATA_LINK_API_KEY not configured, skipping")
            return 0

        # 1. Get active watchlist with their max existing date
        tickers = session.execute(text("""
            SELECT w.id, w.ticker, MAX(dp.date) as max_date
            FROM investments.watchlist w
            LEFT JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
            WHERE w.active = true
            GROUP BY w.id, w.ticker
        """)).fetchall()

        if not tickers:
            logger.info("No active tickers in watchlist")
            return 0

        # Build ticker -> watchlist_id lookup
        ticker_map = {row.ticker: row.id for row in tickers}

        # 2. Determine per-ticker fetch start date
        today = date.today()
        earliest = today - timedelta(days=LOOKBACK_YEARS * 365)

        per_ticker_start = {}
        for row in tickers:
            if row.max_date:
                per_ticker_start[row.ticker] = row.max_date + timedelta(days=1)
            else:
                per_ticker_start[row.ticker] = earliest

        # Skip tickers already up to date
        pending = {t: d for t, d in per_ticker_start.items() if d <= today}
        if not pending:
            logger.info("All tickers up to date")
            return 0

        logger.info(f"{len(pending)} tickers need updates")

        # 3. Group tickers by start date, then batch
        # Sort by start date so tickers with similar freshness are batched together.
        # This avoids refetching 10 years for a ticker that only needs yesterday's bar.
        sorted_tickers = sorted(pending.keys(), key=lambda t: pending[t])

        total = 0
        client = httpx.Client(timeout=60)

        try:
            # Build batches of tickers with the same start date
            batches = []
            current_batch = []
            current_start = None
            for ticker in sorted_tickers:
                start = pending[ticker]
                if current_start is None or current_start == start:
                    current_batch.append(ticker)
                    current_start = start
                else:
                    # Different start date — flush current batch, start new one
                    batches.append((current_batch, current_start))
                    current_batch = [ticker]
                    current_start = start
                # Flush when batch is full
                if len(current_batch) >= BATCH_SIZE:
                    batches.append((current_batch, current_start))
                    current_batch = []
                    current_start = None
            if current_batch:
                batches.append((current_batch, current_start))

            for batch_idx, (batch, batch_start) in enumerate(batches):
                rows = self._fetch_prices(
                    client, api_key, batch, batch_start.isoformat()
                )

                # Map ticker -> watchlist_id and upsert
                db_rows = []
                for row in rows:
                    wl_id = ticker_map.get(row["ticker"])
                    if not wl_id:
                        continue
                    db_rows.append({
                        "watchlist_id": wl_id,
                        "date": row["date"],
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row["close"],
                        "volume": row.get("volume"),
                        "close_unadj": row.get("closeunadj"),
                    })

                if db_rows:
                    count = bulk_upsert(
                        session,
                        table="investments.daily_prices",
                        rows=db_rows,
                        conflict_columns=["watchlist_id", "date"],
                    )
                    total += count
                    logger.info(
                        f"Batch {batch_idx + 1}/{len(batches)}: "
                        f"{count} rows upserted for {batch} (from {batch_start})"
                    )

        finally:
            client.close()

        return total

    def _fetch_prices(
        self, client: httpx.Client, api_key: str,
        tickers: list[str], start_date: str,
    ) -> list[dict]:
        """Fetch daily prices from SHARADAR/SEP with pagination and retry."""
        all_rows = []
        cursor_id = None

        while True:
            params = {
                "ticker": ",".join(tickers),
                "date.gte": start_date,
                "api_key": api_key,
                "qopts.per_page": PAGE_SIZE,
            }
            if cursor_id:
                params["qopts.cursor_id"] = cursor_id

            data = self._request_with_retry(client, params)

            # SHARADAR returns:
            # {datatable: {data: [...], columns: [...]}, meta: {next_cursor_id: ...}}
            datatable = data.get("datatable", {})
            columns = [c["name"] for c in datatable.get("columns", [])]
            rows_data = datatable.get("data", [])

            for row_values in rows_data:
                all_rows.append(dict(zip(columns, row_values)))

            # Check pagination
            meta = data.get("meta", {})
            cursor_id = meta.get("next_cursor_id")
            if not cursor_id:
                break

            time.sleep(RATE_LIMIT_SLEEP)

        return all_rows

    def _request_with_retry(
        self, client: httpx.Client, params: dict,
    ) -> dict:
        """GET with exponential backoff on 429/5xx."""
        backoff = INITIAL_BACKOFF
        for attempt in range(MAX_RETRIES + 1):
            resp = client.get(SHARADAR_API, params=params)

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"HTTP {resp.status_code}, retrying in {backoff}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                # Final attempt failed
                resp.raise_for_status()

            resp.raise_for_status()
            return resp.json()

        # Should not reach here, but satisfy type checker
        raise RuntimeError("Exhausted retries")


if __name__ == "__main__":
    collector = NasdaqCollector()
    collector.run()
