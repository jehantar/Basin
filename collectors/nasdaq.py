"""Nasdaq Data Link collector — fetches daily stock prices from SHARADAR/SEP.

Benchmark ETFs (SPY, QQQ) are fetched from Yahoo Finance since SHARADAR/SEP
only covers individual equities.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.nasdaq")

SHARADAR_API = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/SEP"
YAHOO_CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart"
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

        # 1. Get active watchlist with their max existing date
        tickers = session.execute(text("""
            SELECT w.id, w.ticker, w.is_benchmark, MAX(dp.date) as max_date
            FROM investments.watchlist w
            LEFT JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
            WHERE w.active = true
            GROUP BY w.id, w.ticker, w.is_benchmark
        """)).fetchall()

        if not tickers:
            logger.info("No active tickers in watchlist")
            return 0

        # Build ticker -> watchlist_id lookup
        ticker_map = {row.ticker: row.id for row in tickers}

        # Split benchmarks (Yahoo Finance) from equities (SHARADAR)
        benchmark_tickers = [row for row in tickers if row.is_benchmark]
        equity_tickers = [row for row in tickers if not row.is_benchmark]

        # 2. Determine per-ticker fetch start date
        today = date.today()
        earliest = today - timedelta(days=LOOKBACK_YEARS * 365)

        total = 0

        # 2a. Fetch benchmarks from Yahoo Finance
        total += self._fetch_benchmarks(session, benchmark_tickers, ticker_map, today, earliest)

        # 2b. Fetch equities from SHARADAR
        if not api_key:
            logger.warning("NASDAQ_DATA_LINK_API_KEY not configured, skipping equities")
            return total

        per_ticker_start = {}
        for row in equity_tickers:
            if row.max_date:
                per_ticker_start[row.ticker] = row.max_date + timedelta(days=1)
            else:
                per_ticker_start[row.ticker] = earliest

        pending = {t: d for t, d in per_ticker_start.items() if d <= today}
        if not pending:
            logger.info("All equities up to date")
            return total

        logger.info(f"{len(pending)} equities need updates")

        # 3. Group tickers by start date, then batch
        # Sort by start date so tickers with similar freshness are batched together.
        # This avoids refetching 10 years for a ticker that only needs yesterday's bar.
        sorted_tickers = sorted(pending.keys(), key=lambda t: pending[t])

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

    def _fetch_benchmarks(self, session, benchmark_rows, ticker_map, today, earliest) -> int:
        """Fetch benchmark ETF prices from Yahoo Finance."""
        total = 0
        client = httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"})

        try:
            for row in benchmark_rows:
                start_date = row.max_date + timedelta(days=1) if row.max_date else earliest
                if start_date > today:
                    continue

                # Yahoo Finance uses Unix timestamps
                period1 = int(datetime.combine(start_date, datetime.min.time()).timestamp())
                period2 = int(datetime.combine(today + timedelta(days=1), datetime.min.time()).timestamp())

                try:
                    resp = client.get(
                        f"{YAHOO_CHART_API}/{row.ticker}",
                        params={
                            "period1": period1,
                            "period2": period2,
                            "interval": "1d",
                            "events": "history",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    result = data["chart"]["result"][0]
                    timestamps = result.get("timestamp", [])
                    quote = result["indicators"]["quote"][0]
                    adjclose_list = result["indicators"].get("adjclose", [{}])
                    adjclose = adjclose_list[0].get("adjclose", []) if adjclose_list else []

                    db_rows = []
                    for idx, ts in enumerate(timestamps):
                        day = date.fromtimestamp(ts)
                        close = adjclose[idx] if idx < len(adjclose) and adjclose[idx] else quote["close"][idx]
                        if close is None:
                            continue
                        db_rows.append({
                            "watchlist_id": ticker_map[row.ticker],
                            "date": day.isoformat(),
                            "open": quote["open"][idx],
                            "high": quote["high"][idx],
                            "low": quote["low"][idx],
                            "close": round(close, 4),
                            "volume": int(quote["volume"][idx]) if quote["volume"][idx] else None,
                            "close_unadj": quote["close"][idx],
                        })

                    if db_rows:
                        count = bulk_upsert(
                            session,
                            table="investments.daily_prices",
                            rows=db_rows,
                            conflict_columns=["watchlist_id", "date"],
                        )
                        total += count
                        logger.info(f"Benchmark {row.ticker}: {count} rows upserted (Yahoo Finance)")

                except Exception as e:
                    logger.warning(f"Failed to fetch benchmark {row.ticker}: {e}")

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
