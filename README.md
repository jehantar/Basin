# Basin

Personal data aggregator that collects fitness, financial, and health data from multiple sources into a PostgreSQL database with web dashboards for visualization.

## Architecture

```
Local Machine                          VM (Docker)
+-----------------+     SSH/SCP        +---------------------------+
| Apple Health    | ──────────────────> | Collector (cron)          |
| Hevy CSV        |                    |   HealthKit  XML parser   |
+-----------------+                    |   Hevy       CSV parser   |
                                       |   Teller     Bank API     |
                                       +---------------------------+
                                       | Webhook (FastAPI)         |
                                       |   /dashboard/fitness      |
                                       |   /dashboard/finance      |
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
| Teller | Bank API (mTLS) | Daily 7:00 AM UTC | Accounts, balances, transactions |

## Dashboards

- **Fitness** — Running stats (pace, distance, power), VO2max trends, strength volume/PRs, training calendar
- **Finance** — Monthly spend trends, category breakdowns, merchant analysis, per-card spending
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

### Start services

```bash
op run --env-file=.env -- docker compose up -d
```

### Run database migrations

```bash
docker compose exec collector python -c "
from shared.db import get_engine
from sqlalchemy import text
engine = get_engine()
with open('migrations/001_initial.sql') as f:
    with engine.connect() as conn:
        conn.execute(text(f.read()))
        conn.commit()
"
```

## Syncing Health Data

Export Apple Health or Hevy data to `~/Desktop/Basin Exports/`, then:

```bash
./scripts/sync-health.sh              # sync both
./scripts/sync-health.sh health       # health only
./scripts/sync-health.sh hevy         # hevy only
```

The script auto-extracts `export.zip`, uploads to the VM, and runs the collector. Subsequent syncs are incremental — only new records are processed.

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
| `basin` | `collector_runs`, `hevy_imports` | System tracking |

## Backups

Daily automated backup at 2:00 AM UTC via `scripts/backup.sh`. Stored in `/opt/basin/backups/`.
