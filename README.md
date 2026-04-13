# Basin

Personal data aggregator that collects fitness, financial, health, and investment data from multiple sources into a PostgreSQL database with web dashboards for visualization.

## Architecture

```
Local Machine                          VM (Docker)
+-----------------+     SSH/SCP        +---------------------------+
| Apple Health    | ──────────────────> | Collector (cron)          |
| Hevy CSV        |                    |   HealthKit     XML parse |
+-----------------+                    |   Hevy          CSV parse |
                                       |   Intervals.icu REST API  |
                                       |   Teller        Bank API  |
                                       |   Nasdaq        SHARADAR  |
                                       +---------------------------+
                                       | Webhook (FastAPI)         |
                                       |   /dashboard/fitness      |
                                       |   /dashboard/finance      |
                                       |   /dashboard/investments  |
                                       |   /dashboard/system       |
                                       +---------------------------+
                                       | PostgreSQL 16             |
                                       +---------------------------+
```

## Data Sources

| Collector | Source | Schedule | Data |
|-----------|--------|----------|------|
| HealthKit | Apple Health XML export | Daily 6:05 AM UTC | Metrics (VO2max, weight, HR, body fat) + workouts |
| Hevy | CSV drop folder | Daily 6:00 AM UTC | Strength training: exercises, sets, weight/reps |
| Intervals.icu | REST API (Strava data) | Daily 6:10 AM UTC | Training load (CTL/ATL/TSB), pace curves, HR curves |
| Teller | Bank API (mTLS) | Daily 7:00 AM UTC | Accounts, balances, transactions |
| Nasdaq | SHARADAR/SEP + Yahoo Finance | Daily 1:30 AM UTC | Daily stock prices (equities via SHARADAR, ETF benchmarks via Yahoo) |

## Dashboards

- **Fitness** — Running stats (pace, distance, elevation, max HR, calories, per-mile splits with route maps), VO2max trends, strength volume/PRs, training load (CTL/ATL/TSB), pace curves, HR curves, training calendar
- **Finance** — Monthly spend trends, category breakdowns, merchant analysis, per-card spending
- **Investments** — Stock watchlist performance tracker with:
  - Normalized return overlay chart with hover highlighting
  - vs S&P 500 relative performance toggle (shows alpha)
  - SPY and QQQ benchmark reference lines
  - Compare Groups view with side-by-side line charts per portfolio
  - Heatmap view with color-coded return tiles
  - Sortable table with % return, CAGR, alpha vs S&P, 52-week high/low
  - Sector allocation donut chart
  - Configurable stock groups (e.g., Brokerage, IRA)
  - Search/filter, select all/unselect all, click-to-drilldown
- **System** — Collector health, run history, error tracking

## Setup

### Prerequisites

- Docker and Docker Compose
- 1Password CLI (`op`) for secret management
- Python 3.12+ (for local development)

### Bootstrap the VM

```bash
# Run on a fresh Ubuntu server
./scripts/bootstrap-vm.sh
```

This creates the `basin` user, installs Docker, sets up directories, and configures log rotation.

### Configure environment

Copy `.env.example` and fill in your secrets (or use 1Password `op://` references):

```bash
cp .env.example .env
```

Required environment variables:

| Variable | Purpose |
|----------|---------|
| `BASIN_PG_PASSWORD` | PostgreSQL password |
| `TELLER_ACCESS_TOKEN` | Teller bank API credentials |
| `TELEGRAM_BOT_TOKEN` | Telegram alert bot token |
| `TELEGRAM_CHAT_ID` | Telegram alert destination |
| `NASDAQ_DATA_LINK_API_KEY` | Nasdaq Data Link API key (SHARADAR equity prices) |
| `INTERVALS_ICU_API_KEY` | Intervals.icu API key (training load, pace/HR curves) |
| `INTERVALS_ICU_ATHLETE_ID` | Intervals.icu athlete ID (e.g., `i553742`) |
| `WEBHOOK_BIND` | Optional webhook port override (default: 8075) |

### Start services

```bash
op run --env-file=.env -- docker compose up -d
```

### Run database migrations

```bash
# Initial schema
docker compose exec -T postgres psql -U basin -d basin -f /docker-entrypoint-initdb.d/001_initial.sql

# Investments schema
docker compose exec -T postgres psql -U basin -d basin -f /docker-entrypoint-initdb.d/002_investments.sql

# Intervals.icu schema
docker compose exec -T postgres psql -U basin -d basin -f /docker-entrypoint-initdb.d/003_intervals_icu.sql

# Strava schema
docker compose exec -T postgres psql -U basin -d basin -f /docker-entrypoint-initdb.d/004_strava.sql
docker compose exec -T postgres psql -U basin -d basin -f /docker-entrypoint-initdb.d/004a_strava_splits.sql
```

## Syncing Health Data

Export Apple Health or Hevy data to `~/Desktop/Basin Exports/`, then:

```bash
./scripts/sync-health.sh              # sync both
./scripts/sync-health.sh health       # health only
./scripts/sync-health.sh hevy         # hevy only
```

The script auto-extracts `export.zip`, uploads to the VM, and runs the collector. Subsequent syncs are incremental — only new records are processed.

## Managing Investments

Stocks are managed via SQL on the database:

```sql
-- Add a stock
INSERT INTO investments.watchlist (ticker, name, sector)
VALUES ('TSLA', 'Tesla Inc.', 'Consumer')
ON CONFLICT (ticker) DO NOTHING;

-- Assign to a group
INSERT INTO investments.stock_group_members (group_id, watchlist_id)
SELECT g.id, w.id FROM investments.stock_groups g, investments.watchlist w
WHERE g.name = 'Brokerage' AND w.ticker = 'TSLA'
ON CONFLICT DO NOTHING;

-- Remove a stock (cascades, deletes all price data)
DELETE FROM investments.watchlist WHERE ticker = 'TSLA';

-- Soft-disable (keeps history, stops fetching)
UPDATE investments.watchlist SET active = false WHERE ticker = 'TSLA';
```

After adding new tickers, run the collector to backfill prices:

```bash
docker exec -e NASDAQ_DATA_LINK_API_KEY='your-key' basin-collector-1 python -m collectors.nasdaq
```

Benchmark ETFs (SPY, QQQ) are fetched from Yahoo Finance since SHARADAR/SEP only covers individual equities. Mark benchmarks with `is_benchmark = true`.

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests (requires local PostgreSQL)
pytest

# Test database defaults to:
# postgresql://basin:basin@localhost:5432/basin_test
```

## Database Schema

| Schema | Tables | Purpose |
|--------|--------|---------|
| `healthkit` | `metrics`, `workouts` | Apple Health fitness data |
| `hevy` | `exercises`, `workouts`, `sets` | Strength training |
| `teller` | `institutions`, `accounts`, `balances`, `transactions` | Banking |
| `investments` | `watchlist`, `stock_groups`, `stock_group_members`, `daily_prices` | Stock watchlist and price history |
| `intervals` | `daily_fitness`, `pace_curves`, `hr_curves` | Training load and performance curves |
| `strava` | `tokens`, `activities` | Strava OAuth tokens and activity data (elevation, splits, GPS) |
| `basin` | `collector_runs`, `hevy_imports` | System tracking |

## Backups

Daily automated backup at 2:00 AM UTC via `scripts/backup.sh`. Stored in `/opt/basin/backups/`.
