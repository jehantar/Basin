# Investment Tracker — Detailed Implementation Plan

## Context

Build a stock watchlist tab at `/dashboard/investments` that tracks ~50 stocks' price performance over configurable time periods. Data source: Nasdaq Data Link SHARADAR/SEP (daily OHLCV, refreshed nightly after market close). This is a comparison watchlist — no positions or cost basis. Users manage tickers via DB, organize into saved groups, and compare performance via normalized overlay charts and a sortable returns table.

---

## 1. Database Schema — `migrations/002_investments.sql`

Following conventions from `001_initial.sql`: `BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`, `TIMESTAMPTZ DEFAULT now()`, schema-namespaced tables, UNIQUE constraints, CASCADE where appropriate.

```sql
BEGIN;

CREATE SCHEMA investments;

-- The universe of tracked tickers
CREATE TABLE investments.watchlist (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker      TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT true,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Named groups for organizing stocks (e.g., "Tech", "Dividend")
CREATE TABLE investments.stock_groups (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Many-to-many: which tickers belong to which groups
CREATE TABLE investments.stock_group_members (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id        BIGINT NOT NULL REFERENCES investments.stock_groups(id) ON DELETE CASCADE,
    watchlist_id    BIGINT NOT NULL REFERENCES investments.watchlist(id) ON DELETE CASCADE,
    UNIQUE (group_id, watchlist_id)
);

-- Daily OHLCV from SHARADAR/SEP
CREATE TABLE investments.daily_prices (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    watchlist_id    BIGINT NOT NULL REFERENCES investments.watchlist(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    open            NUMERIC(14,4),
    high            NUMERIC(14,4),
    low             NUMERIC(14,4),
    close           NUMERIC(14,4) NOT NULL,
    volume          BIGINT,
    close_unadj     NUMERIC(14,4),
    UNIQUE (watchlist_id, date)
);

CREATE INDEX idx_daily_prices_date
    ON investments.daily_prices (date DESC);

CREATE INDEX idx_daily_prices_watchlist_date
    ON investments.daily_prices (watchlist_id, date DESC);

COMMIT;
```

**Design notes:**
- `watchlist_id` FK on `daily_prices` with `ON DELETE CASCADE` — removing a ticker auto-cleans all its prices (requirement: "those removed from the list can be deleted")
- `NUMERIC(14,4)` matches `teller.balances` precision pattern
- `close` is split-adjusted (what SHARADAR provides); `close_unadj` stored for reference but unused in calculations
- `active` flag allows soft-disable without losing history; hard-DELETE cascades

**Seed template — `migrations/002a_investments_seed.sql`:**

```sql
-- Example seed — user populates with their actual tickers
INSERT INTO investments.watchlist (ticker, name) VALUES
    ('AAPL', 'Apple Inc.'),
    ('MSFT', 'Microsoft Corp.'),
    ('GOOG', 'Alphabet Inc.')
ON CONFLICT (ticker) DO NOTHING;

INSERT INTO investments.stock_groups (name) VALUES
    ('Tech'),
    ('Dividend')
ON CONFLICT (name) DO NOTHING;

-- Assign tickers to groups
INSERT INTO investments.stock_group_members (group_id, watchlist_id)
SELECT g.id, w.id
FROM investments.stock_groups g, investments.watchlist w
WHERE g.name = 'Tech' AND w.ticker IN ('AAPL', 'MSFT', 'GOOG')
ON CONFLICT DO NOTHING;
```

---

## 2. Config — `shared/config.py`

Add one field to the frozen dataclass + its env var read:

```python
@dataclass(frozen=True)
class Config:
    database_url: str
    teller_access_token: str = ""
    teller_cert_path: str = ""
    teller_key_path: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    nasdaq_data_link_api_key: str = ""     # <-- NEW


def load_config() -> Config:
    return Config(
        database_url=os.environ["DATABASE_URL"],
        teller_access_token=os.environ.get("TELLER_ACCESS_TOKEN", ""),
        teller_cert_path=os.environ.get("TELLER_CERT_PATH", ""),
        teller_key_path=os.environ.get("TELLER_KEY_PATH", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        nasdaq_data_link_api_key=os.environ.get("NASDAQ_DATA_LINK_API_KEY", ""),  # <-- NEW
    )
```

---

## 3. Environment Plumbing

### `.env.example` — append:
```
# Nasdaq Data Link (SHARADAR equity prices)
NASDAQ_DATA_LINK_API_KEY="op://Basin/Nasdaq/api_key"
```

### `docker-compose.yml` — add to `collector.environment`:
```yaml
NASDAQ_DATA_LINK_API_KEY: ${NASDAQ_DATA_LINK_API_KEY}
```

No change needed to `webhook` service — the webhook only reads price data from DB, doesn't call the API.

---

## 4. Dashboard Shared — `webhook/dashboard_shared.py`

Add `max_days` parameter so investments can use 10-year spans while other dashboards keep 5-year default:

```python
def _parse_date_range(
    start: str | None,
    end: str | None,
    max_days: int = MAX_DATE_SPAN_DAYS,   # <-- NEW param, default unchanged
) -> tuple[date, date]:
```

Change the validation line from:
```python
if (end_date - start_date).days > MAX_DATE_SPAN_DAYS:
```
to:
```python
if (end_date - start_date).days > max_days:
```

And the error message references `max_days` instead of the constant. No other callers change — backward compatible.

---

## 5. Collector — `collectors/nasdaq.py`

Follows `BaseCollector` pattern exactly (ref: `collectors/teller.py`, `collectors/base.py`).

```python
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

        # Build ticker→watchlist_id lookup
        ticker_map = {row.ticker: row.id for row in tickers}

        # 2. Group tickers by fetch start date, then batch
        today = date.today()
        earliest = today - timedelta(days=LOOKBACK_YEARS * 365)

        # Build batches: list of (ticker_list, start_date)
        # Group by common start date for efficiency
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

        # 3. Fetch in batches
        total = 0
        client = httpx.Client(timeout=60)

        try:
            ticker_list = list(pending.keys())
            for i in range(0, len(ticker_list), BATCH_SIZE):
                batch = ticker_list[i:i + BATCH_SIZE]
                # Use the earliest start date in the batch
                batch_start = min(pending[t] for t in batch)

                rows = self._fetch_prices(
                    client, api_key, batch, batch_start.isoformat()
                )

                # Map ticker → watchlist_id and upsert
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
                    logger.info(f"Batch {i // BATCH_SIZE + 1}: {count} rows upserted for {batch}")

        finally:
            client.close()

        return total

    def _fetch_prices(
        self, client: httpx.Client, api_key: str,
        tickers: list[str], start_date: str,
    ) -> list[dict]:
        """Fetch daily prices from SHARADAR/SEP with pagination."""
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

            resp = client.get(SHARADAR_API, params=params)
            resp.raise_for_status()
            data = resp.json()

            # SHARADAR returns {datatable: {data: [...], columns: [...]}, meta: {next_cursor_id: ...}}
            datatable = data.get("datatable", {})
            columns = [c["name"] for c in datatable.get("columns", [])]
            rows_data = datatable.get("data", [])

            for row_values in rows_data:
                row_dict = dict(zip(columns, row_values))
                all_rows.append(row_dict)

            # Check pagination
            meta = data.get("meta", {})
            cursor_id = meta.get("next_cursor_id")
            if not cursor_id:
                break

            time.sleep(RATE_LIMIT_SLEEP)

        return all_rows


if __name__ == "__main__":
    collector = NasdaqCollector()
    collector.run()
```

**Key details:**
- Reads active tickers + their `MAX(date)` in one query to determine per-ticker fetch window
- New tickers: fetches 10 years back. Existing: fetches from `max_date + 1 day`
- Batches up to 10 tickers per API call (SHARADAR supports comma-separated)
- Handles SHARADAR's cursor-based pagination (`meta.next_cursor_id`)
- Rate limit: 1s sleep between paginated calls (Nasdaq free tier: 300 req/day, 1 req/sec)
- Uses `httpx.Client` with 60s timeout (same pattern as `teller.py`)
- Maps ticker → `watchlist_id` before upserting to `daily_prices`
- `bulk_upsert` with conflict on `(watchlist_id, date)` handles re-runs safely

---

## 6. Crontab

Append to `crontab`. SHARADAR refreshes ~8 PM ET daily; schedule at 1:30 AM UTC (9:30 PM ET with buffer):

```cron
# Nasdaq / SHARADAR — daily at 1:30 AM UTC (9:30 PM ET, after market data refresh)
30 1 * * * set -a; . /etc/basin.env; set +a; cd /app && python -m collectors.nasdaq >> /var/log/basin/nasdaq.log 2>&1
```

Runs daily including weekends (harmless — API returns 0 rows on non-trading days).

---

## 7. API Endpoints — `webhook/investments.py`

Follows `webhook/finance.py` pattern exactly: `APIRouter`, HTML serving, shared date parsing, `get_session()`, `_response_metadata()`.

### Endpoint 1: `GET /dashboard/investments`

```python
@router.get("/dashboard/investments")
def serve_investments():
    html_path = os.path.join(os.path.dirname(__file__), "investments.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())
```

### Endpoint 2: `GET /api/investments/watchlist`

Returns table data with computed metrics for the selected period.

**Params:** `start` (YYYY-MM-DD), `end` (YYYY-MM-DD), `group` (optional group ID)

**Response:**
```json
{
    "range_start": "2025-01-01",
    "range_end": "2026-04-10",
    "timezone": "UTC",
    "generated_at": "...",
    "stocks": [
        {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "current_price": 198.50,
            "period_start_price": 175.20,
            "period_return_pct": 13.30,
            "cagr_pct": 10.45,
            "high_52w": 210.00,
            "low_52w": 155.00
        }
    ],
    "groups": [
        {"id": 1, "name": "Tech", "tickers": ["AAPL", "MSFT", "GOOG"]},
        {"id": 2, "name": "Dividend", "tickers": ["JNJ", "PG", "KO"]}
    ]
}
```

**SQL approach:**

```sql
-- Get period start/end prices per ticker using window functions
WITH period_bounds AS (
    SELECT
        w.id, w.ticker, w.name,
        FIRST_VALUE(dp.close) OVER (PARTITION BY w.id ORDER BY dp.date ASC) as start_price,
        FIRST_VALUE(dp.close) OVER (PARTITION BY w.id ORDER BY dp.date DESC) as end_price,
        MIN(dp.date) OVER (PARTITION BY w.id) as first_date,
        MAX(dp.date) OVER (PARTITION BY w.id) as last_date,
        ROW_NUMBER() OVER (PARTITION BY w.id ORDER BY dp.date DESC) as rn
    FROM investments.watchlist w
    JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
    WHERE w.active = true
      AND dp.date BETWEEN :start AND :end
)
SELECT ticker, name, start_price, end_price, first_date, last_date
FROM period_bounds
WHERE rn = 1
```

```sql
-- 52-week high/low (separate query, always trailing 252 trading days)
SELECT w.ticker, MAX(dp.high) as high_52w, MIN(dp.low) as low_52w
FROM investments.watchlist w
JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
WHERE w.active = true
  AND dp.date >= CURRENT_DATE - INTERVAL '52 weeks'
GROUP BY w.ticker
```

**Python calculations:**
```python
period_return_pct = ((end_price - start_price) / start_price) * 100
years = (last_date - first_date).days / 365.25
cagr_pct = ((end_price / start_price) ** (1 / years) - 1) * 100 if years > 0 else 0
current_price = end_price  # latest close in range = current
```

### Endpoint 3: `GET /api/investments/prices`

Returns time-series for charting.

**Params:** `start`, `end`, `tickers` (comma-sep, e.g. `AAPL,MSFT`), `group` (optional group ID), `normalize` (bool, default `true`)

**Response:**
```json
{
    "range_start": "2025-01-01",
    "range_end": "2026-04-10",
    "timezone": "UTC",
    "generated_at": "...",
    "series": {
        "AAPL": {
            "dates": ["2025-01-02", "2025-01-03", "..."],
            "values": [100.0, 101.5, "..."]
        },
        "MSFT": {
            "dates": ["2025-01-02", "2025-01-03", "..."],
            "values": [100.0, 99.8, "..."]
        }
    }
}
```

**SQL:**
```sql
SELECT w.ticker, dp.date, dp.close
FROM investments.daily_prices dp
JOIN investments.watchlist w ON w.id = dp.watchlist_id
WHERE w.ticker IN :tickers
  AND dp.date BETWEEN :start AND :end
ORDER BY w.ticker, dp.date ASC
```

If `group` is provided instead of `tickers`, join through `stock_group_members` to resolve tickers.

**Normalization (Python):**
```python
# When normalize=true, rebase each series to 100 at first date
if normalize:
    base = closes[0]
    values = [round((c / base) * 100, 2) for c in closes]
else:
    values = [float(c) for c in closes]
```

### Endpoint 4: `GET /api/investments/groups`

Simple lookup, used to populate the group dropdown.

```json
{
    "groups": [
        {"id": 1, "name": "Tech", "tickers": ["AAPL", "MSFT", "GOOG"]},
        {"id": 2, "name": "Dividend", "tickers": ["JNJ", "PG", "KO"]}
    ]
}
```

**All endpoints use `_parse_date_range(start, end, max_days=10 * 365)` for the extended window.**

---

## 8. Server Mounting — `webhook/server.py`

Add after the existing router imports (following exact pattern at lines 20-24):

```python
from webhook.investments import router as investments_router
app.include_router(investments_router)
```

---

## 9. Ops Dashboard — `webhook/ops.py`

Add to `COLLECTOR_SCHEDULES` dict (line 14-18):

```python
COLLECTOR_SCHEDULES = {
    "teller": "Daily 7:00 AM UTC",
    "hevy": "Daily 6:00 AM UTC",
    "healthkit": "Daily 6:05 AM UTC",
    "nasdaq": "Daily 1:30 AM UTC",
}
```

---

## 10. Frontend — `webhook/investments.html`

Single self-contained HTML file following `finance.html` conventions exactly: same CSS variables, fonts, Plotly CDN, IIFE pattern, `baseLayout()`, responsive breakpoints.

### HTML Structure

```
<nav class="top-nav">
  Fitness | Finance | [Investments (active)] | System
</nav>

<div class="page-container">
  <header>  Basin / Investments    sync timestamp  </header>

  <error-banner>  (hidden unless API error)  </error-banner>

  <summary-cards>  (3 cards)
    [# Tracked]  [Best Performer: AAPL +23.4%]  [Worst Performer: INTC -8.2%]
  </summary-cards>

  <controls-row>
    LEFT:  [Group dropdown: All | Tech | Dividend | ...]
           [Stock selector button → opens checkbox panel]
    RIGHT: [YTD] [1M] [3M] [6M] [1Y] [3Y] [5Y] [All]  |  [start] – [end]
  </controls-row>

  <chart-panel>
    Plotly line chart: normalized % return overlay (rebased to 100)
    One trace per selected stock, different colors
    Y-axis label: "Return (%)", reference line at 100
  </chart-panel>

  <drilldown-chart>  (hidden unless single stock clicked)
    Plotly line chart: absolute price for one stock
    Shows OHLC or just close, with volume bars below
    [Close] button to return to overlay
  </drilldown-chart>

  <table-panel>
    Sortable data-table:
    | Ticker | Name | Price | % Return | CAGR | 52W High | 52W Low |
    Rows are clickable → triggers drilldown chart
    Checkboxes on left for ad-hoc multi-select
  </table-panel>
</div>
```

### JavaScript Architecture

Following the exact IIFE + state pattern from `finance.html`:

```javascript
(function() {
  'use strict';

  // ── State ──
  var watchlistData = null;       // from /api/investments/watchlist
  var pricesData = null;          // from /api/investments/prices
  var groupsData = null;          // from /api/investments/groups
  var selectedGroup = null;       // group ID or null for "All"
  var selectedTickers = [];       // ad-hoc checked tickers
  var activePreset = '1Y';        // default period
  var drilldownTicker = null;     // null = overlay mode, string = drilldown mode

  // ── DOM helpers ── (same $ / clearChildren / createEl / formatters from finance.html)

  // ── Plotly baseLayout() ── (identical to finance.html)

  // ── Formatters ──
  function fmtPct(n) { return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
  function fmtPrice(n) { return '$' + Number(n).toFixed(2); }

  // ── Time preset mapping ──
  // Unlike finance (which uses months), investments uses date calculation:
  // YTD → Jan 1 of current year
  // 1M/3M/6M → today - N months
  // 1Y/3Y/5Y → today - N years
  // All → 10 years ago (max lookback)

  // ── Stock selector panel ──
  // Dropdown for groups, plus a togglable checkbox list of all tickers
  // Selecting a group → auto-checks those tickers + unchecks others
  // Ad-hoc checking → clears group selection (switches to "Custom")

  // ── Data loading ──
  function loadAll() {
    // Parallel fetch: /api/investments/watchlist + /api/investments/groups
    // Then fetch /api/investments/prices for selected tickers
    // Same Promise.all pattern as finance.html
  }

  // ── Chart rendering ──
  function renderOverlayChart() {
    // Plotly scatter/line chart
    // One trace per ticker: {x: dates, y: normalized_values, name: ticker, mode: 'lines'}
    // Colors: cycle through palette [#10b981, #3b82f6, #f59e0b, #a78bfa, #ec4899, ...]
    // Y-axis: "Return (%)" with reference line at 100
    // Hover: ticker name + date + value
  }

  function renderDrilldownChart(ticker) {
    // Single stock absolute price chart
    // Plotly scatter: {x: dates, y: raw_prices, name: ticker}
    // Y-axis: dollar values with $ prefix
    // Show/hide toggle with animation
  }

  // ── Table rendering ──
  function renderTable() {
    // Sortable columns: click header to sort by that column
    // Default sort: % Return descending
    // Checkbox in first column for each row
    // Row click → drilldown to that stock
    // Color coding: green for positive returns, red for negative
  }

  // ── Sorting ──
  var sortCol = 'period_return_pct';
  var sortDir = 'desc';
  function sortBy(col) { /* toggle direction, re-render */ }

  // ── Initialization ──
  setPreset('1Y');  // default to 1-year view
})();
```

### Chart Color Palette

```javascript
var STOCK_COLORS = [
    '#10b981', '#3b82f6', '#f59e0b', '#a78bfa', '#ec4899',
    '#06b6d4', '#f97316', '#8b5cf6', '#14b8a6', '#ef4444',
    '#84cc16', '#e879f9', '#22d3ee', '#fb923c', '#818cf8'
];
```

### Key UI Interactions

1. **Group dropdown change** → updates `selectedTickers` to group members → fetches prices → re-renders chart + table
2. **Checkbox toggle** → updates `selectedTickers` → clears group selection → fetches prices → re-renders chart
3. **Preset button click** → computes start/end dates → fetches watchlist + prices → re-renders everything
4. **Custom date change** → clears preset highlight → fetches watchlist + prices
5. **Table row click** → sets `drilldownTicker` → fetches `/api/investments/prices?tickers=X&normalize=false` → renders drilldown chart
6. **Drilldown close** → clears `drilldownTicker` → shows overlay chart again
7. **Table header click** → sorts table by that column (ascending/descending toggle)

### Table Row Color Coding

```javascript
// % Return and CAGR cells
var color = value >= 0 ? '#10b981' : '#ef4444';
td.style.color = color;
td.textContent = fmtPct(value);
```

---

## 11. Nav Bar Updates

Add "Investments" link to the `<nav class="top-nav">` in all 3 existing HTML files, between Finance and System:

### `webhook/dashboard.html` (fitness) — line ~644 area:
```html
<nav class="top-nav">
  <a href="/dashboard/fitness" class="nav-tab active">Fitness</a>
  <a href="/dashboard/finance" class="nav-tab">Finance</a>
  <a href="/dashboard/investments" class="nav-tab">Investments</a>
  <a href="/dashboard/system" class="nav-tab">System</a>
</nav>
```

### `webhook/finance.html` — line 644-648:
```html
<nav class="top-nav">
  <a href="/dashboard/fitness" class="nav-tab">Fitness</a>
  <a href="/dashboard/finance" class="nav-tab active">Finance</a>
  <a href="/dashboard/investments" class="nav-tab">Investments</a>
  <a href="/dashboard/system" class="nav-tab">System</a>
</nav>
```

### `webhook/ops.html` — wherever its nav is:
```html
<nav class="top-nav">
  <a href="/dashboard/fitness" class="nav-tab">Fitness</a>
  <a href="/dashboard/finance" class="nav-tab">Finance</a>
  <a href="/dashboard/investments" class="nav-tab">Investments</a>
  <a href="/dashboard/system" class="nav-tab active">System</a>
</nav>
```

---

## Implementation Sequence

| Step | Files | Testable? |
|------|-------|-----------|
| 1 | `migrations/002_investments.sql`, `002a_investments_seed.sql` | `psql -f` then `\dt investments.*` |
| 2 | `shared/config.py` | `python -c "from shared.config import load_config"` |
| 3 | `.env.example`, `docker-compose.yml` | Visual check |
| 4 | `webhook/dashboard_shared.py` | Existing tests still pass |
| 5 | `collectors/nasdaq.py` | `python -m collectors.nasdaq` (backfills data) |
| 6 | `crontab` | Visual check |
| 7 | `webhook/investments.py` | `curl /api/investments/watchlist?start=2025-01-01` |
| 8 | `webhook/server.py` | Server starts without import errors |
| 9 | `webhook/ops.py` | `/api/ops/status` shows nasdaq collector |
| 10 | `webhook/investments.html` | Load `/dashboard/investments` in browser |
| 11 | Nav updates in 3 HTML files | Click between tabs, verify active state |

---

## Verification

1. Run migration against local DB, verify schema with `\dt investments.*`
2. Insert 3-5 test tickers via seed SQL
3. Set `NASDAQ_DATA_LINK_API_KEY` env var, run `python -m collectors.nasdaq` — verify rows populate in `investments.daily_prices`
4. Start webhook, `curl /api/investments/watchlist?start=2025-01-01` — verify JSON with return calculations
5. `curl "/api/investments/prices?tickers=AAPL,MSFT&normalize=true"` — verify normalized series starting at 100
6. Load `/dashboard/investments` in browser — confirm chart renders, table populates
7. Test group selection — dropdown filters tickers correctly
8. Test ad-hoc selection — checkboxes update chart
9. Test drilldown — click a stock row, verify individual price chart
10. Test all presets (YTD through All) — verify date ranges and data
11. Check ops dashboard — nasdaq collector appears with schedule
12. Check nav bar on all 4 dashboards — Investments link present and routing works
