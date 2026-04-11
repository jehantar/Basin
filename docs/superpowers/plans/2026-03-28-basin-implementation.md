# Basin Data Aggregator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted personal data warehouse that collects and normalizes data from HealthKit, Hevy, Schwab, and Teller into a single Postgres database on a Hetzner VM.

**Architecture:** Monorepo with Docker Compose running three services (Postgres, collector cron, webhook server) on a shared bridge network. Independent Python collectors per data source, all sharing a BaseCollector pattern for idempotent upserts and run tracking. Secrets via 1Password CLI.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, psycopg2, httpx, click, Docker Compose, Postgres 16

**Spec:** `docs/superpowers/specs/2026-03-28-basin-data-aggregator-design.md`

> **QA integration note (2026-03-28):** Feedback has been incorporated directly into the task steps and code snippets below (security hardening, idempotency, test repeatability, and config flexibility) instead of being tracked in a separate review section.

---

## File Structure

```
basin/
├── docker-compose.yml           # Three services: postgres, collector, webhook
├── .env.example                 # Template with op:// placeholders
├── .gitignore
├── pyproject.toml               # Python project config, dependencies, scripts
├── crontab                      # Cron schedule for collector container
├── Dockerfile.collector         # Python + cron, runs collectors on schedule
├── Dockerfile.webhook           # Python + uvicorn, runs FastAPI server
├── scripts/
│   ├── bootstrap-vm.sh          # One-time VM setup: Docker, swap, user, dirs
│   └── backup.sh                # Daily pg_dump backup script
├── migrations/
│   └── 001_initial.sql          # Full schema DDL (all 5 schemas)
├── shared/
│   ├── __init__.py
│   ├── config.py                # Env var reading, settings dataclass
│   ├── db.py                    # Engine, session, bulk_upsert helper
│   └── telegram.py              # send_alert(message) via Bot API
├── collectors/
│   ├── __init__.py
│   ├── base.py                  # BaseCollector: run tracking, error handling
│   ├── healthkit.py             # XML dump import from /data/healthkit/imports/
│   ├── hevy.py                  # CSV drop folder at /data/hevy/drop/
│   ├── schwab.py                # OAuth token management + positions/transactions
│   └── teller.py                # mTLS + access token, accounts/balances/transactions
├── webhook/
│   ├── __init__.py
│   └── server.py                # FastAPI: HealthKit webhook + Schwab OAuth callback
├── cli/
│   ├── __init__.py
│   └── health.py                # `python -m cli.health` status dashboard
└── tests/
    ├── __init__.py
    ├── conftest.py              # Shared fixtures: test DB, sample data
    ├── test_db.py               # bulk_upsert helper tests
    ├── test_base_collector.py   # BaseCollector run tracking tests
    ├── test_healthkit_webhook.py # FastAPI TestClient tests
    ├── test_healthkit_xml.py    # XML parsing tests
    ├── test_hevy.py             # CSV parsing tests
    ├── test_schwab.py           # Token management + API response parsing
    ├── test_teller.py           # API response parsing
    └── test_cli.py              # CLI output tests
```

---

## Task 1: VM Bootstrap Script

**Files:**
- Create: `scripts/bootstrap-vm.sh`

This script is run once on the Hetzner VM as root. It installs Docker, creates a swap file, creates a `basin` user, and sets up directories.

Implementation notes integrated from review: keep bootstrap idempotent (no duplicate fstab entries) and use conservative apt install flags.

- [ ] **Step 1: Write the bootstrap script**

```bash
#!/usr/bin/env bash
# Basin VM bootstrap — run once on Ubuntu 24.04 as root
set -euo pipefail

echo "=== Adding 1GB swap file ==="
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Swap created."
else
    echo "Swap already exists, skipping."
fi

echo "=== Installing Docker Engine ==="
if ! command -v docker &> /dev/null; then
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo "Docker installed."
else
    echo "Docker already installed, skipping."
fi

echo "=== Creating basin user ==="
if ! id -u basin &>/dev/null; then
    useradd -r -s /bin/bash -d /opt/basin -m basin
    usermod -aG docker basin
    echo "basin user created."
else
    echo "basin user already exists, skipping."
fi

echo "=== Creating directories ==="
mkdir -p /opt/basin/{data/hevy/drop,data/healthkit/imports,data/healthkit/failed,certs/teller,backups}
chown -R basin:basin /opt/basin

echo "=== Configuring Docker log rotation ==="
cat > /etc/docker/daemon.json << 'DAEMON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
DAEMON
systemctl restart docker

echo ""
echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "1. Clone Basin repo to /opt/basin/"
echo "2. Add .env with op:// references"
echo "3. Place Teller certs in /opt/basin/certs/teller/"
echo "4. Run: cd /opt/basin && op run --env-file=.env -- docker compose up -d"
```

- [ ] **Step 2: Make it executable and commit**

```bash
chmod +x scripts/bootstrap-vm.sh
git add scripts/bootstrap-vm.sh
git commit -m "feat: add VM bootstrap script (Docker, swap, basin user, dirs)"
```

---

## Task 2: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `Dockerfile.collector`
- Create: `Dockerfile.webhook`
- Create: `docker-compose.yml`
- Create: `crontab`

- [ ] **Step 1: Create pyproject.toml**

Include an explicit build backend so `pip install .` works reliably in container builds.

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "basin"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "sqlalchemy>=2.0,<3.0",
    "psycopg2-binary>=2.9,<3.0",
    "fastapi>=0.115,<1.0",
    "uvicorn[standard]>=0.34,<1.0",
    "httpx>=0.28,<1.0",
    "click>=8.1,<9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<9.0",
    "pytest-asyncio>=0.24,<1.0",
    "httpx",
]

[project.scripts]
basin = "cli.health:cli"
```

- [ ] **Step 2: Create .gitignore**

```gitignore
# Secrets
.env
certs/

# Python
__pycache__/
*.pyc
*.egg-info/
dist/
.venv/

# Data
data/
backups/

# OS
.DS_Store

# IDE
.vscode/
.idea/
```

- [ ] **Step 3: Create .env.example**

Add a configurable webhook bind variable to avoid hard-coded host/IP assumptions across environments.

```bash
# Basin — copy to .env and fill in op:// references
# Start with: op run --env-file=.env -- docker compose up -d

BASIN_PG_PASSWORD="op://Basin/Postgres/password"

# Schwab OAuth (register at developer.schwab.com)
SCHWAB_CLIENT_ID="op://Basin/Schwab/client_id"
SCHWAB_CLIENT_SECRET="op://Basin/Schwab/client_secret"
SCHWAB_REDIRECT_URI="http://<VM_IP>:8075/schwab/callback"

# Teller (download from teller.io dashboard)
TELLER_ACCESS_TOKEN="op://Basin/Teller/access_token"

# Telegram (reuse reservation bot credentials)
TELEGRAM_BOT_TOKEN="op://Basin/Telegram/bot_token"
TELEGRAM_CHAT_ID="op://Basin/Telegram/chat_id"

# Optional: externally published webhook port
WEBHOOK_BIND="8075"
```

- [ ] **Step 4: Create Dockerfile.collector**

Harden cron environment handling by writing `/etc/basin.env` with restricted permissions because it contains secrets.

```dockerfile
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y cron postgresql-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY shared/ shared/
COPY collectors/ collectors/
COPY cli/ cli/
COPY scripts/ scripts/
COPY crontab /etc/cron.d/basin-cron

RUN chmod 0644 /etc/cron.d/basin-cron && \
    crontab /etc/cron.d/basin-cron && \
    mkdir -p /var/log/basin && \
    chmod +x scripts/*.sh

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Dump runtime env vars to a file that cron jobs can source,
# since cron does not inherit the container's environment.
CMD ["sh", "-c", "umask 077 && env > /etc/basin.env && chmod 600 /etc/basin.env && cron -f"]
```

- [ ] **Step 5: Create Dockerfile.webhook**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY shared/ shared/
COPY webhook/ webhook/

RUN useradd -r -s /bin/false basin
USER basin

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "webhook.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: Create docker-compose.yml**

Use a configurable published port for webhook service and avoid binding to a single fixed host address in the base plan.

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: basin
      POSTGRES_USER: basin
      POSTGRES_PASSWORD: ${BASIN_PG_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d
    networks:
      - basin
    command: >
      postgres
        -c shared_buffers=64MB
        -c work_mem=4MB
        -c effective_cache_size=256MB
        -c max_connections=20
    shm_size: 128mb
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U basin -d basin"]
      interval: 10s
      timeout: 5s
      retries: 5

  collector:
    build:
      context: .
      dockerfile: Dockerfile.collector
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./data:/data
      - ./certs:/certs:ro
    networks:
      - basin
    environment:
      DATABASE_URL: postgresql://basin:${BASIN_PG_PASSWORD}@postgres:5432/basin
      SCHWAB_CLIENT_ID: ${SCHWAB_CLIENT_ID}
      SCHWAB_CLIENT_SECRET: ${SCHWAB_CLIENT_SECRET}
      SCHWAB_REDIRECT_URI: ${SCHWAB_REDIRECT_URI}
      TELLER_ACCESS_TOKEN: ${TELLER_ACCESS_TOKEN}
      TELLER_CERT_PATH: /certs/teller/certificate.pem
      TELLER_KEY_PATH: /certs/teller/private_key.pem
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}

  webhook:
    build:
      context: .
      dockerfile: Dockerfile.webhook
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "${WEBHOOK_BIND:-8075}:8000"
    networks:
      - basin
    environment:
      DATABASE_URL: postgresql://basin:${BASIN_PG_PASSWORD}@postgres:5432/basin
      SCHWAB_CLIENT_ID: ${SCHWAB_CLIENT_ID}
      SCHWAB_CLIENT_SECRET: ${SCHWAB_CLIENT_SECRET}
      SCHWAB_REDIRECT_URI: ${SCHWAB_REDIRECT_URI}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  pgdata:

networks:
  basin:
    driver: bridge
```

- [ ] **Step 7: Create crontab**

```crontab
# Basin collector schedule
# Each job sources /etc/basin.env because cron does not inherit Docker env vars.
# Logs go to /var/log/basin/ inside the collector container.

# Hevy — check drop folder daily at 6:00 AM UTC
0 6 * * * . /etc/basin.env; cd /app && python -m collectors.hevy >> /var/log/basin/hevy.log 2>&1

# HealthKit XML — check import folder daily at 6:05 AM UTC
5 6 * * * . /etc/basin.env; cd /app && python -m collectors.healthkit >> /var/log/basin/healthkit.log 2>&1

# Schwab — daily at 9:00 PM UTC (after US market close 4 PM ET)
0 21 * * * . /etc/basin.env; cd /app && python -m collectors.schwab >> /var/log/basin/schwab.log 2>&1

# Teller — daily at 7:00 AM UTC
0 7 * * * . /etc/basin.env; cd /app && python -m collectors.teller >> /var/log/basin/teller.log 2>&1

# Schwab token watchdog — every 6 hours
0 */6 * * * . /etc/basin.env; cd /app && python -m collectors.schwab --check-token >> /var/log/basin/schwab.log 2>&1

# Daily Postgres backup at 2:00 AM UTC
0 2 * * * . /etc/basin.env; /app/scripts/backup.sh >> /var/log/basin/backup.log 2>&1

```

Note: the trailing blank line is required for cron to parse the file.

- [ ] **Step 8: Create empty Python package files**

Create these empty `__init__.py` files:
- `shared/__init__.py`
- `collectors/__init__.py`
- `webhook/__init__.py`
- `cli/__init__.py`
- `tests/__init__.py`

- [ ] **Step 9: Commit scaffolding**

```bash
git add pyproject.toml .gitignore .env.example Dockerfile.collector Dockerfile.webhook \
    docker-compose.yml crontab shared/__init__.py collectors/__init__.py webhook/__init__.py \
    cli/__init__.py tests/__init__.py
git commit -m "feat: add project scaffolding (Docker, Compose, pyproject, crontab)"
```

---

## Task 3: Database Migration

**Files:**
- Create: `migrations/001_initial.sql`

Incorporates Priority 0 improvements: `NUMERIC(18,4)` for monetary columns, `ON DELETE RESTRICT` for foreign keys. Also includes Priority 1 indexes.

- [ ] **Step 1: Write the full migration**

```sql
-- Basin data aggregator — initial schema
-- Runs automatically on first `docker compose up` via docker-entrypoint-initdb.d

BEGIN;

------------------------------------------------------------
-- HealthKit
------------------------------------------------------------
CREATE SCHEMA healthkit;

CREATE TABLE healthkit.metrics (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    metric_type     TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    unit            TEXT NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL,
    source_name     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (metric_type, recorded_at, source_name)
);

CREATE INDEX idx_healthkit_metrics_type_time
    ON healthkit.metrics (metric_type, recorded_at DESC);

CREATE TABLE healthkit.workouts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workout_type    TEXT NOT NULL,
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ NOT NULL,
    duration_sec    DOUBLE PRECISION,
    distance_m      DOUBLE PRECISION,
    energy_kcal     DOUBLE PRECISION,
    avg_hr          DOUBLE PRECISION,
    max_hr          DOUBLE PRECISION,
    avg_cadence     DOUBLE PRECISION,
    source_name     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workout_type, start_time, source_name)
);

------------------------------------------------------------
-- Hevy
------------------------------------------------------------
CREATE SCHEMA hevy;

CREATE TABLE hevy.exercises (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE hevy.workouts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title           TEXT,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    duration_sec    INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (started_at)
);

CREATE TABLE hevy.sets (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workout_id      BIGINT NOT NULL REFERENCES hevy.workouts(id) ON DELETE RESTRICT,
    exercise_id     BIGINT NOT NULL REFERENCES hevy.exercises(id) ON DELETE RESTRICT,
    set_index       INTEGER NOT NULL,
    set_type        TEXT NOT NULL DEFAULT 'normal',
    weight_kg       DOUBLE PRECISION,
    reps            INTEGER,
    distance_m      DOUBLE PRECISION,
    duration_sec    INTEGER,
    rpe             DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workout_id, exercise_id, set_index)
);

------------------------------------------------------------
-- Schwab
------------------------------------------------------------
CREATE SCHEMA schwab;

CREATE TABLE schwab.accounts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      TEXT NOT NULL UNIQUE,
    account_hash    TEXT NOT NULL,
    account_type    TEXT NOT NULL,
    nickname        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE schwab.positions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES schwab.accounts(id) ON DELETE RESTRICT,
    symbol          TEXT NOT NULL,
    asset_type      TEXT NOT NULL DEFAULT 'EQUITY',
    quantity        DOUBLE PRECISION NOT NULL,
    market_value    NUMERIC(18,4),
    cost_basis      NUMERIC(18,4),
    as_of           DATE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, symbol, as_of)
);

CREATE TABLE schwab.transactions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES schwab.accounts(id) ON DELETE RESTRICT,
    transaction_id  TEXT NOT NULL UNIQUE,
    transaction_type TEXT NOT NULL,
    symbol          TEXT,
    quantity        DOUBLE PRECISION,
    amount          NUMERIC(18,4) NOT NULL,
    transacted_at   TIMESTAMPTZ NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_schwab_txn_account_time
    ON schwab.transactions (account_id, transacted_at DESC);

CREATE TABLE schwab.tokens (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    access_expires  TIMESTAMPTZ NOT NULL,
    refresh_expires TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- Teller
------------------------------------------------------------
CREATE SCHEMA teller;

CREATE TABLE teller.institutions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id  TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE teller.accounts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      TEXT NOT NULL UNIQUE,
    enrollment_id   TEXT,
    institution_id  BIGINT NOT NULL REFERENCES teller.institutions(id) ON DELETE RESTRICT,
    account_type    TEXT NOT NULL,
    name            TEXT,
    subtype         TEXT,
    last_four       TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE teller.balances (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES teller.accounts(id) ON DELETE RESTRICT,
    available       NUMERIC(18,4),
    ledger          NUMERIC(18,4),
    as_of           DATE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, as_of)
);

CREATE TABLE teller.transactions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES teller.accounts(id) ON DELETE RESTRICT,
    transaction_id  TEXT NOT NULL UNIQUE,
    amount          NUMERIC(18,4) NOT NULL,
    description     TEXT,
    category        TEXT,
    date            DATE NOT NULL,
    status          TEXT NOT NULL,
    counterparty    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_teller_txn_account_date
    ON teller.transactions (account_id, date DESC);

------------------------------------------------------------
-- Basin (system)
------------------------------------------------------------
CREATE SCHEMA basin;

CREATE TABLE basin.collector_runs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collector       TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL,
    rows_upserted   INTEGER DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_collector_runs_collector_time
    ON basin.collector_runs (collector, started_at DESC);

CREATE TABLE basin.hevy_imports (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename        TEXT NOT NULL UNIQUE,
    file_hash       TEXT,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count       INTEGER
);

COMMIT;
```

Key changes from spec:
- Added `account_hash` to `schwab.accounts` (required by Schwab API — all data calls use the hash, not the raw account number)
- Added `set_type` to `hevy.sets` (the CSV includes warmup/normal/failure set types)
- Added `enrollment_id` and `status` to `teller.accounts` (from Teller API response)
- Added `asset_type` to `schwab.positions`
- Added `file_hash` to `basin.hevy_imports` (Priority 1: detect changed files with same name)
- All monetary columns use `NUMERIC(18,4)`
- All foreign keys use `ON DELETE RESTRICT`
- Indexes on frequently queried time-series columns

- [ ] **Step 2: Commit**

```bash
git add migrations/001_initial.sql
git commit -m "feat: add initial Postgres schema migration (5 schemas, 15 tables)"
```

---

## Task 4: Shared Config + DB Module

**Files:**
- Create: `shared/config.py`
- Create: `shared/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write shared/config.py**

```python
"""Basin configuration — reads from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    schwab_client_id: str = ""
    schwab_client_secret: str = ""
    schwab_redirect_uri: str = ""
    teller_access_token: str = ""
    teller_cert_path: str = ""
    teller_key_path: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def load_config() -> Config:
    return Config(
        database_url=os.environ["DATABASE_URL"],
        schwab_client_id=os.environ.get("SCHWAB_CLIENT_ID", ""),
        schwab_client_secret=os.environ.get("SCHWAB_CLIENT_SECRET", ""),
        schwab_redirect_uri=os.environ.get("SCHWAB_REDIRECT_URI", ""),
        teller_access_token=os.environ.get("TELLER_ACCESS_TOKEN", ""),
        teller_cert_path=os.environ.get("TELLER_CERT_PATH", ""),
        teller_key_path=os.environ.get("TELLER_KEY_PATH", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
```

- [ ] **Step 2: Write shared/db.py**

Ensure `bulk_upsert` handles dynamic SQL safely and falls back to `ON CONFLICT DO NOTHING` when no update columns remain.

```python
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
```

- [ ] **Step 3: Write tests/conftest.py**

Make fixture setup repeatable by resetting schemas before re-applying migration SQL.

```python
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
    with eng.connect() as conn:
        conn.execute(text("""
            DROP SCHEMA IF EXISTS healthkit CASCADE;
            DROP SCHEMA IF EXISTS hevy CASCADE;
            DROP SCHEMA IF EXISTS schwab CASCADE;
            DROP SCHEMA IF EXISTS teller CASCADE;
            DROP SCHEMA IF EXISTS basin CASCADE;
        """))
        conn.execute(text(sql))
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
```

- [ ] **Step 4: Write tests for bulk_upsert**

```python
# tests/test_db.py
"""Tests for shared.db bulk_upsert helper."""


def test_bulk_upsert_inserts_new_rows(session):
    """Inserting new rows should create them."""
    from shared.db import bulk_upsert

    rows = [
        {"metric_type": "weight", "value": 80.0, "unit": "kg",
         "recorded_at": "2026-01-01T08:00:00Z", "source_name": "iPhone"},
        {"metric_type": "weight", "value": 79.5, "unit": "kg",
         "recorded_at": "2026-01-02T08:00:00Z", "source_name": "iPhone"},
    ]
    count = bulk_upsert(
        session,
        table="healthkit.metrics",
        rows=rows,
        conflict_columns=["metric_type", "recorded_at", "source_name"],
    )
    assert count == 2


def test_bulk_upsert_updates_on_conflict(session):
    """Re-inserting with same key should update, not duplicate."""
    from shared.db import bulk_upsert

    row = {"metric_type": "weight", "value": 80.0, "unit": "kg",
           "recorded_at": "2026-01-01T08:00:00Z", "source_name": "iPhone"}

    bulk_upsert(session, "healthkit.metrics", [row],
                conflict_columns=["metric_type", "recorded_at", "source_name"])

    row["value"] = 81.0
    count = bulk_upsert(session, "healthkit.metrics", [row],
                        conflict_columns=["metric_type", "recorded_at", "source_name"])
    assert count == 1

    from sqlalchemy import text
    result = session.execute(
        text("SELECT value FROM healthkit.metrics WHERE metric_type = 'weight'")
    ).fetchone()
    assert float(result[0]) == 81.0


def test_bulk_upsert_empty_rows(session):
    """Empty input should return 0 and not error."""
    from shared.db import bulk_upsert

    count = bulk_upsert(session, "healthkit.metrics", [],
                        conflict_columns=["metric_type", "recorded_at", "source_name"])
    assert count == 0
```

- [ ] **Step 5: Run tests to verify behavior**

(Updated wording: these are validation tests expected to pass when the local test database is available.)

Run: `pytest tests/test_db.py -v`
Expected: Tests should pass once local Postgres test DB is available. If Postgres is unavailable locally, a connection failure is expected in local dev environments without Docker.

- [ ] **Step 6: Commit**

```bash
git add shared/config.py shared/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: add shared config, DB engine, and bulk_upsert helper with tests"
```

---

## Task 5: Shared Telegram Module

**Files:**
- Create: `shared/telegram.py`

- [ ] **Step 1: Write shared/telegram.py**

```python
"""Telegram alert helper — sends messages via the Bot API."""

import logging

import httpx

from shared.config import load_config

logger = logging.getLogger("basin.telegram")

TELEGRAM_API = "https://api.telegram.org"


def send_alert(message: str, prefix: str = "[Basin]") -> bool:
    """
    Send a message to the configured Telegram chat.
    Returns True on success, False on failure (logs the error).
    """
    config = load_config()
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.warning("Telegram not configured, skipping alert")
        return False

    url = f"{TELEGRAM_API}/bot{config.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": f"{prefix} {message}",
        "parse_mode": "Markdown",
    }

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error(f"Telegram alert failed: {e}")
        return False
```

- [ ] **Step 2: Commit**

```bash
git add shared/telegram.py
git commit -m "feat: add Telegram alert helper"
```

---

## Task 6: BaseCollector

**Files:**
- Create: `collectors/base.py`
- Create: `tests/test_base_collector.py`

- [ ] **Step 1: Write tests/test_base_collector.py**

```python
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
```

- [ ] **Step 2: Write collectors/base.py**

```python
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_base_collector.py -v`
Expected: PASS (requires test Postgres)

- [ ] **Step 4: Commit**

```bash
git add collectors/base.py tests/test_base_collector.py
git commit -m "feat: add BaseCollector with run tracking and failure alerting"
```

---

## Task 7: HealthKit Webhook Server

**Files:**
- Create: `webhook/server.py`
- Create: `tests/test_healthkit_webhook.py`

The Health Auto Export app sends JSON with this structure:
```json
{
  "data": {
    "metrics": [{"name": "resting_heart_rate", "units": "bpm", "data": [{"date": "2026-01-15 08:30:00 -0500", "qty": 58, "source": "Apple Watch"}]}],
    "workouts": [{"id": "...", "name": "Running", "start": "...", "end": "...", "duration": 2700, "activeEnergyBurned": {"qty": 450, "units": "kcal"}, "distance": {"qty": 5.2, "units": "km"}, "heartRateData": [{"Avg": 155, "Max": 172}]}]
  }
}
```

- [ ] **Step 1: Write tests/test_healthkit_webhook.py**

```python
"""Tests for HealthKit webhook endpoint."""

import json
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


SAMPLE_METRICS_PAYLOAD = {
    "data": {
        "metrics": [
            {
                "name": "resting_heart_rate",
                "units": "bpm",
                "data": [
                    {"date": "2026-01-15 08:00:00 -0500", "qty": 58, "source": "Apple Watch"},
                    {"date": "2026-01-16 08:00:00 -0500", "qty": 57, "source": "Apple Watch"},
                ],
            },
            {
                "name": "weight_body_mass",
                "units": "kg",
                "data": [
                    {"date": "2026-01-15 07:00:00 -0500", "qty": 80.5, "source": "Withings"},
                ],
            },
        ],
        "workouts": [],
    }
}

SAMPLE_WORKOUT_PAYLOAD = {
    "data": {
        "metrics": [],
        "workouts": [
            {
                "id": "abc-123",
                "name": "Running",
                "start": "2026-01-15 07:00:00 -0500",
                "end": "2026-01-15 07:45:00 -0500",
                "duration": 2700,
                "activeEnergyBurned": {"qty": 450, "units": "kcal"},
                "distance": {"qty": 5200, "units": "m"},
                "heartRateData": [
                    {"date": "2026-01-15 07:00:00 -0500", "Avg": 155, "Max": 172, "Min": 120},
                ],
            }
        ],
    }
}


@pytest.fixture
def client(session, monkeypatch):
    """Create a FastAPI test client with a mocked DB session."""
    monkeypatch.setattr("webhook.server.get_session", lambda: _FakeCtx(session))
    from webhook.server import app
    return TestClient(app)


class _FakeCtx:
    def __init__(self, session):
        self._session = session
    def __enter__(self):
        return self._session
    def __exit__(self, *args):
        pass


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_healthkit_webhook_metrics(client, session):
    resp = client.post("/healthkit/webhook", json=SAMPLE_METRICS_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["metrics_upserted"] == 3
    assert resp.json()["workouts_upserted"] == 0

    from sqlalchemy import text
    count = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert count == 3


def test_healthkit_webhook_workouts(client, session):
    resp = client.post("/healthkit/webhook", json=SAMPLE_WORKOUT_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["workouts_upserted"] == 1

    from sqlalchemy import text
    row = session.execute(
        text("SELECT workout_type, energy_kcal FROM healthkit.workouts LIMIT 1")
    ).fetchone()
    assert row[0] == "Running"
    assert float(row[1]) == 450.0


def test_healthkit_webhook_idempotent(client, session):
    """Posting the same data twice should not duplicate rows."""
    client.post("/healthkit/webhook", json=SAMPLE_METRICS_PAYLOAD)
    client.post("/healthkit/webhook", json=SAMPLE_METRICS_PAYLOAD)

    from sqlalchemy import text
    count = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert count == 3


def test_healthkit_webhook_malformed(client):
    resp = client.post("/healthkit/webhook", json={"bad": "data"})
    assert resp.status_code == 200  # Accept but log warning
    assert resp.json()["metrics_upserted"] == 0
```

- [ ] **Step 2: Write webhook/server.py**

```python
"""FastAPI webhook server — HealthKit data receiver + Schwab OAuth callback."""

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.db import get_session, bulk_upsert

logger = logging.getLogger("basin.webhook")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Basin Webhook")

HEALTHKIT_FAILED_DIR = "/data/healthkit/failed"


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/healthkit/webhook")
async def healthkit_webhook(request: Request):
    """Receive HealthKit data from Health Auto Export app."""
    body = await request.json()
    data = body.get("data", {})

    metrics_count = 0
    workouts_count = 0

    try:
        with get_session() as session:
            metrics_count = _ingest_metrics(session, data.get("metrics", []))
            workouts_count = _ingest_workouts(session, data.get("workouts", []))
    except Exception as e:
        logger.error(f"HealthKit webhook error: {e}")
        _save_failed_payload(body, str(e))

    return {
        "metrics_upserted": metrics_count,
        "workouts_upserted": workouts_count,
    }


def _parse_healthkit_date(date_str: str) -> datetime:
    """
    Parse Health Auto Export date format.
    Examples: '2026-01-15 08:30:00 -0500', '2026-01-15 3:04:05 PM -0700'
    """
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %I:%M:%S %p %z"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse HealthKit date: {date_str}")


def _ingest_metrics(session, metrics: list) -> int:
    """Parse and upsert health metrics."""
    rows = []
    for metric in metrics:
        metric_name = metric.get("name", "")
        unit = metric.get("units", "")
        for point in metric.get("data", []):
            # Handle standard qty field
            value = point.get("qty")
            # Handle heart_rate special format (Avg)
            if value is None:
                value = point.get("Avg")
            if value is None:
                continue

            try:
                recorded_at = _parse_healthkit_date(point["date"])
            except (ValueError, KeyError):
                continue

            rows.append({
                "metric_type": metric_name,
                "value": float(value),
                "unit": unit,
                "recorded_at": recorded_at.isoformat(),
                "source_name": point.get("source"),
            })

    return bulk_upsert(
        session,
        table="healthkit.metrics",
        rows=rows,
        conflict_columns=["metric_type", "recorded_at", "source_name"],
    )


def _ingest_workouts(session, workouts: list) -> int:
    """Parse and upsert workouts."""
    rows = []
    for w in workouts:
        try:
            start = _parse_healthkit_date(w["start"])
            end = _parse_healthkit_date(w["end"])
        except (ValueError, KeyError):
            continue

        # Extract average and max HR from heartRateData array
        avg_hr = None
        max_hr = None
        hr_data = w.get("heartRateData", [])
        if hr_data:
            avgs = [p["Avg"] for p in hr_data if "Avg" in p]
            maxes = [p["Max"] for p in hr_data if "Max" in p]
            if avgs:
                avg_hr = sum(avgs) / len(avgs)
            if maxes:
                max_hr = max(maxes)

        energy = w.get("activeEnergyBurned", {})
        energy_kcal = energy.get("qty") if energy.get("units") in ("kcal", None) else None

        distance = w.get("distance", {})
        distance_m = distance.get("qty")
        # Convert km to meters if needed
        if distance.get("units") == "km" and distance_m is not None:
            distance_m = distance_m * 1000
        # Convert miles to meters if needed
        elif distance.get("units") == "mi" and distance_m is not None:
            distance_m = distance_m * 1609.344

        rows.append({
            "workout_type": w.get("name", "Unknown"),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_sec": w.get("duration"),
            "distance_m": distance_m,
            "energy_kcal": energy_kcal,
            "avg_hr": avg_hr,
            "max_hr": max_hr,
            "avg_cadence": None,  # Not in webhook payload; available in XML
            "source_name": "Health Auto Export",
        })

    return bulk_upsert(
        session,
        table="healthkit.workouts",
        rows=rows,
        conflict_columns=["workout_type", "start_time", "source_name"],
    )


def _save_failed_payload(payload: dict, error: str):
    """Save malformed payloads to dead-letter directory for replay."""
    os.makedirs(HEALTHKIT_FAILED_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(HEALTHKIT_FAILED_DIR, f"{ts}.json")
    with open(path, "w") as f:
        json.dump({"error": error, "payload": payload}, f, indent=2)
    logger.info(f"Saved failed payload to {path}")
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_healthkit_webhook.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webhook/server.py tests/test_healthkit_webhook.py
git commit -m "feat: add HealthKit webhook endpoint with dead-letter storage"
```

---

## Task 8: HealthKit XML Collector

**Files:**
- Create: `collectors/healthkit.py`
- Create: `tests/test_healthkit_xml.py`

Apple Health XML export uses `<Record>` and `<Workout>` elements. The XML can be very large (500MB+), so we use iterative parsing with `xml.etree.ElementTree.iterparse`.

- [ ] **Step 1: Write tests/test_healthkit_xml.py**

```python
"""Tests for HealthKit XML import collector."""

import os
import tempfile

from sqlalchemy import text


SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData>
<HealthData locale="en_US">
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Withings"
         unit="kg" value="80.5"
         startDate="2026-01-15 08:00:00 -0500" endDate="2026-01-15 08:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Apple Watch"
         unit="count/min" value="58"
         startDate="2026-01-15 00:00:00 -0500" endDate="2026-01-15 00:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierVO2Max" sourceName="Apple Watch"
         unit="mL/min*kg" value="42.5"
         startDate="2026-01-15 00:00:00 -0500" endDate="2026-01-15 00:00:00 -0500"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
          sourceName="Apple Watch"
          duration="45" durationUnit="min"
          totalDistance="5.2" totalDistanceUnit="km"
          totalEnergyBurned="450" totalEnergyBurnedUnit="kcal"
          startDate="2026-01-15 07:00:00 -0500" endDate="2026-01-15 07:45:00 -0500"/>
</HealthData>
"""


def test_parse_xml_metrics(session, monkeypatch, tmp_path):
    from collectors.healthkit import HealthKitCollector

    xml_file = tmp_path / "export.xml"
    xml_file.write_text(SAMPLE_XML)

    monkeypatch.setattr("collectors.healthkit.IMPORT_DIR", str(tmp_path))
    collector = HealthKitCollector()
    count = collector.collect(session)

    # 3 metrics + 1 workout = 4
    assert count == 4

    metrics = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert metrics == 3

    workouts = session.execute(text("SELECT count(*) FROM healthkit.workouts")).scalar()
    assert workouts == 1


def test_xml_import_idempotent(session, monkeypatch, tmp_path):
    from collectors.healthkit import HealthKitCollector

    xml_file = tmp_path / "export.xml"
    xml_file.write_text(SAMPLE_XML)

    monkeypatch.setattr("collectors.healthkit.IMPORT_DIR", str(tmp_path))
    collector = HealthKitCollector()
    collector.collect(session)
    collector.collect(session)

    metrics = session.execute(text("SELECT count(*) FROM healthkit.metrics")).scalar()
    assert metrics == 3  # No duplicates
```

- [ ] **Step 2: Write collectors/healthkit.py**

```python
"""HealthKit XML dump collector — parses Apple Health export XML."""

import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.healthkit")

IMPORT_DIR = "/data/healthkit/imports"

# Map HealthKit type identifiers to our metric names
METRIC_TYPE_MAP = {
    "HKQuantityTypeIdentifierBodyMass": "weight_body_mass",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierVO2Max": "vo2max",
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierStepCount": "step_count",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_energy",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "heart_rate_variability",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_heart_rate",
    "HKQuantityTypeIdentifierBodyFatPercentage": "body_fat_percentage",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "walking_running_distance",
    "HKQuantityTypeIdentifierFlightsClimbed": "flights_climbed",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate",
}

# Map workout activity types to readable names
WORKOUT_TYPE_MAP = {
    "HKWorkoutActivityTypeRunning": "Running",
    "HKWorkoutActivityTypeCycling": "Cycling",
    "HKWorkoutActivityTypeWalking": "Walking",
    "HKWorkoutActivityTypeHiking": "Hiking",
    "HKWorkoutActivityTypeSwimming": "Swimming",
    "HKWorkoutActivityTypeYoga": "Yoga",
    "HKWorkoutActivityTypeElliptical": "Elliptical",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "Functional Strength",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "Strength Training",
    "HKWorkoutActivityTypeHighIntensityIntervalTraining": "HIIT",
}


def _parse_date(s: str) -> datetime:
    """Parse Apple Health XML date format: '2026-01-15 08:00:00 -0500'."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


class HealthKitCollector(BaseCollector):
    name = "healthkit"

    def collect(self, session) -> int:
        xml_files = [
            os.path.join(IMPORT_DIR, f)
            for f in os.listdir(IMPORT_DIR)
            if f.endswith(".xml")
        ]
        if not xml_files:
            logger.info("No XML files found in import directory")
            return 0

        total = 0
        for xml_path in xml_files:
            logger.info(f"Processing {xml_path}")
            metrics, workouts = self._parse_xml(xml_path)

            total += bulk_upsert(
                session,
                table="healthkit.metrics",
                rows=metrics,
                conflict_columns=["metric_type", "recorded_at", "source_name"],
            )
            total += bulk_upsert(
                session,
                table="healthkit.workouts",
                rows=workouts,
                conflict_columns=["workout_type", "start_time", "source_name"],
            )

        return total

    def _parse_xml(self, path: str) -> tuple[list[dict], list[dict]]:
        """Iteratively parse a Health export XML file."""
        metrics = []
        workouts = []

        for event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "Record":
                row = self._parse_record(elem)
                if row:
                    metrics.append(row)
                elem.clear()

            elif elem.tag == "Workout":
                row = self._parse_workout(elem)
                if row:
                    workouts.append(row)
                elem.clear()

        return metrics, workouts

    def _parse_record(self, elem) -> dict | None:
        hk_type = elem.get("type", "")
        metric_type = METRIC_TYPE_MAP.get(hk_type)
        if metric_type is None:
            return None

        try:
            return {
                "metric_type": metric_type,
                "value": float(elem.get("value", 0)),
                "unit": elem.get("unit", ""),
                "recorded_at": _parse_date(elem.get("startDate")).isoformat(),
                "source_name": elem.get("sourceName"),
            }
        except (ValueError, TypeError):
            return None

    def _parse_workout(self, elem) -> dict | None:
        activity_type = elem.get("workoutActivityType", "")
        workout_type = WORKOUT_TYPE_MAP.get(activity_type, activity_type)

        try:
            start = _parse_date(elem.get("startDate"))
            end = _parse_date(elem.get("endDate"))
        except (ValueError, TypeError):
            return None

        duration_sec = None
        duration_val = elem.get("duration")
        if duration_val:
            duration_sec = float(duration_val)
            if elem.get("durationUnit") == "min":
                duration_sec *= 60

        distance_m = None
        dist_val = elem.get("totalDistance")
        if dist_val:
            distance_m = float(dist_val)
            unit = elem.get("totalDistanceUnit", "")
            if unit == "km":
                distance_m *= 1000
            elif unit == "mi":
                distance_m *= 1609.344

        energy_kcal = None
        energy_val = elem.get("totalEnergyBurned")
        if energy_val and elem.get("totalEnergyBurnedUnit") == "kcal":
            energy_kcal = float(energy_val)

        return {
            "workout_type": workout_type,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_sec": duration_sec,
            "distance_m": distance_m,
            "energy_kcal": energy_kcal,
            "avg_hr": None,
            "max_hr": None,
            "avg_cadence": None,
            "source_name": elem.get("sourceName"),
        }


if __name__ == "__main__":
    collector = HealthKitCollector()
    collector.run()
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_healthkit_xml.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add collectors/healthkit.py tests/test_healthkit_xml.py
git commit -m "feat: add HealthKit XML import collector with iterative parsing"
```

---

## Task 9: Hevy CSV Collector

**Files:**
- Create: `collectors/hevy.py`
- Create: `tests/test_hevy.py`

Hevy CSV format: one row per set. Columns: `title,start_time,end_time,description,exercise_title,superset_id,exercise_notes,set_index,set_type,weight_kg,reps,distance_km,duration_seconds,rpe`. Date format: `"21 Feb 2025, 07:17"`. May also have `weight_lbs`/`distance_miles` columns depending on user settings.

- [ ] **Step 1: Write tests/test_hevy.py**

```python
"""Tests for Hevy CSV collector."""

import os
import textwrap

from sqlalchemy import text


SAMPLE_CSV_METRIC = textwrap.dedent("""\
    "title","start_time","end_time","description","exercise_title","superset_id","exercise_notes","set_index","set_type","weight_kg","reps","distance_km","duration_seconds","rpe"
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Bench Press (Barbell)",,"",0,"normal",80,10,,,
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Bench Press (Barbell)",,"",1,"normal",80,8,,,
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Overhead Press (Dumbbell)",,"",0,"warmup",15,12,,,
    "Push Day","21 Feb 2025, 07:17","21 Feb 2025, 08:06","","Overhead Press (Dumbbell)",,"",1,"normal",25,10,,,8
""")

SAMPLE_CSV_IMPERIAL = textwrap.dedent("""\
    "title","start_time","end_time","description","exercise_title","superset_id","exercise_notes","set_index","set_type","weight_lbs","reps","distance_miles","duration_seconds","rpe"
    "Leg Day","22 Feb 2025, 09:00","22 Feb 2025, 10:00","","Squat (Barbell)",,"",0,"normal",225,5,,,
""")


def test_parse_metric_csv(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_2025-02-21.csv"
    csv_file.write_text(SAMPLE_CSV_METRIC)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    count = collector.collect(session)

    # 1 workout + 2 exercises + 4 sets = 7
    assert count == 7

    workouts = session.execute(text("SELECT count(*) FROM hevy.workouts")).scalar()
    assert workouts == 1

    exercises = session.execute(text("SELECT count(*) FROM hevy.exercises")).scalar()
    assert exercises == 2

    sets = session.execute(text("SELECT count(*) FROM hevy.sets")).scalar()
    assert sets == 4

    # Verify the import was recorded
    imports = session.execute(text("SELECT filename FROM basin.hevy_imports")).fetchall()
    assert len(imports) == 1
    assert imports[0][0] == "workout_2025-02-21.csv"


def test_skip_already_imported(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_2025-02-21.csv"
    csv_file.write_text(SAMPLE_CSV_METRIC)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    collector.collect(session)
    count = collector.collect(session)

    assert count == 0  # Already imported, skip


def test_parse_imperial_csv(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_imperial.csv"
    csv_file.write_text(SAMPLE_CSV_IMPERIAL)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    count = collector.collect(session)

    # 1 workout + 1 exercise + 1 set = 3
    assert count == 3

    # Check weight was converted to kg (225 lbs = 102.058 kg)
    weight = session.execute(
        text("SELECT weight_kg FROM hevy.sets LIMIT 1")
    ).scalar()
    assert abs(float(weight) - 102.058) < 0.1


def test_rpe_parsed(session, monkeypatch, tmp_path):
    from collectors.hevy import HevyCollector

    csv_file = tmp_path / "workout_rpe.csv"
    csv_file.write_text(SAMPLE_CSV_METRIC)

    monkeypatch.setattr("collectors.hevy.DROP_DIR", str(tmp_path))
    collector = HevyCollector()
    collector.collect(session)

    rpe = session.execute(
        text("SELECT rpe FROM hevy.sets WHERE rpe IS NOT NULL")
    ).scalar()
    assert float(rpe) == 8.0
```

- [ ] **Step 2: Write collectors/hevy.py**

```python
"""Hevy CSV collector — watches drop folder for new workout exports."""

import csv
import hashlib
import logging
import os
from datetime import datetime

from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.hevy")

DROP_DIR = "/data/hevy/drop"

LBS_TO_KG = 0.45359237
MILES_TO_M = 1609.344
KM_TO_M = 1000.0


def _parse_hevy_date(s: str) -> datetime:
    """Parse Hevy date format: '21 Feb 2025, 07:17'."""
    return datetime.strptime(s.strip(), "%d %b %Y, %H:%M")


def _file_hash(path: str) -> str:
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class HevyCollector(BaseCollector):
    name = "hevy"

    def collect(self, session) -> int:
        csv_files = [
            f for f in os.listdir(DROP_DIR)
            if f.endswith(".csv")
        ]
        if not csv_files:
            logger.info("No CSV files in drop folder")
            return 0

        # Get already-imported filenames
        result = session.execute(text("SELECT filename, file_hash FROM basin.hevy_imports"))
        imported = {row[0]: row[1] for row in result.fetchall()}

        total = 0
        for filename in sorted(csv_files):
            filepath = os.path.join(DROP_DIR, filename)
            current_hash = _file_hash(filepath)

            if filename in imported and imported[filename] == current_hash:
                logger.info(f"Skipping already imported: {filename}")
                continue

            logger.info(f"Processing: {filename}")
            row_count = self._process_csv(session, filepath)
            total += row_count

            # Record import
            session.execute(
                text("""
                    INSERT INTO basin.hevy_imports (filename, file_hash, row_count)
                    VALUES (:filename, :hash, :count)
                    ON CONFLICT (filename) DO UPDATE SET
                        file_hash = EXCLUDED.file_hash,
                        row_count = EXCLUDED.row_count,
                        imported_at = now()
                """),
                {"filename": filename, "hash": current_hash, "count": row_count},
            )

        return total

    def _process_csv(self, session, filepath: str) -> int:
        """Parse a Hevy CSV and upsert workouts, exercises, and sets."""
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            # Detect unit system
            has_lbs = "weight_lbs" in headers
            has_miles = "distance_miles" in headers

            rows_by_workout = {}
            for row in reader:
                key = (row["title"], row["start_time"], row["end_time"])
                if key not in rows_by_workout:
                    rows_by_workout[key] = []
                rows_by_workout[key].append(row)

        total = 0

        for (title, start_str, end_str), sets_rows in rows_by_workout.items():
            started_at = _parse_hevy_date(start_str)
            ended_at = _parse_hevy_date(end_str)
            duration_sec = int((ended_at - started_at).total_seconds())

            # Upsert workout
            total += bulk_upsert(
                session,
                table="hevy.workouts",
                rows=[{
                    "title": title,
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "duration_sec": duration_sec,
                }],
                conflict_columns=["started_at"],
            )

            # Get workout_id
            workout_id = session.execute(
                text("SELECT id FROM hevy.workouts WHERE started_at = :ts"),
                {"ts": started_at.isoformat()},
            ).scalar()

            for row in sets_rows:
                exercise_name = row["exercise_title"]

                # Upsert exercise
                total += bulk_upsert(
                    session,
                    table="hevy.exercises",
                    rows=[{"name": exercise_name}],
                    conflict_columns=["name"],
                    update_columns=[],  # Nothing to update
                )

                exercise_id = session.execute(
                    text("SELECT id FROM hevy.exercises WHERE name = :name"),
                    {"name": exercise_name},
                ).scalar()

                # Parse weight
                weight_kg = None
                if has_lbs:
                    raw = row.get("weight_lbs", "").strip()
                    if raw:
                        weight_kg = float(raw) * LBS_TO_KG
                else:
                    raw = row.get("weight_kg", "").strip()
                    if raw:
                        weight_kg = float(raw)

                # Parse distance
                distance_m = None
                if has_miles:
                    raw = row.get("distance_miles", "").strip()
                    if raw:
                        distance_m = float(raw) * MILES_TO_M
                else:
                    raw = row.get("distance_km", "").strip()
                    if raw:
                        distance_m = float(raw) * KM_TO_M

                # Parse optional fields
                reps_raw = row.get("reps", "").strip()
                reps = int(reps_raw) if reps_raw else None

                dur_raw = row.get("duration_seconds", "").strip()
                dur = int(dur_raw) if dur_raw else None

                rpe_raw = row.get("rpe", "").strip()
                rpe = float(rpe_raw) if rpe_raw else None

                set_type = row.get("set_type", "normal").strip() or "normal"

                total += bulk_upsert(
                    session,
                    table="hevy.sets",
                    rows=[{
                        "workout_id": workout_id,
                        "exercise_id": exercise_id,
                        "set_index": int(row["set_index"]),
                        "set_type": set_type,
                        "weight_kg": weight_kg,
                        "reps": reps,
                        "distance_m": distance_m,
                        "duration_sec": dur,
                        "rpe": rpe,
                    }],
                    conflict_columns=["workout_id", "exercise_id", "set_index"],
                )

        return total


if __name__ == "__main__":
    collector = HevyCollector()
    collector.run()
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_hevy.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add collectors/hevy.py tests/test_hevy.py
git commit -m "feat: add Hevy CSV collector with metric/imperial support and file hashing"
```

---

## Task 10: Schwab OAuth Flow

**Files:**
- Modify: `webhook/server.py` (add `/schwab/auth` and `/schwab/callback` endpoints)
- Create: `tests/test_schwab.py`

Schwab OAuth:
- Authorize: `GET https://api.schwabapi.com/v1/oauth/authorize?client_id=...&redirect_uri=...`
- Token exchange: `POST https://api.schwabapi.com/v1/oauth/token` with Basic auth (base64 of client_id:client_secret)
- Auth code expires in 30 seconds
- Access token: 30 min. Refresh token: 7 days. Refresh tokens are NOT rotated.

- [ ] **Step 1: Write Schwab OAuth tests in tests/test_schwab.py**

```python
"""Tests for Schwab OAuth flow and data collector."""

import base64
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import text


def test_schwab_auth_redirect(client):
    """The /schwab/auth endpoint should redirect to Schwab's OAuth page."""
    resp = client.get("/schwab/auth", follow_redirects=False)
    assert resp.status_code == 307
    assert "api.schwabapi.com/v1/oauth/authorize" in resp.headers["location"]


def test_schwab_callback_exchanges_code(client, session):
    """The /schwab/callback should exchange the auth code for tokens."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "test_access",
        "refresh_token": "test_refresh",
        "expires_in": 1800,
        "token_type": "Bearer",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("webhook.server.httpx.post", return_value=mock_response):
        resp = client.get("/schwab/callback?code=test_auth_code")

    assert resp.status_code == 200
    assert "stored" in resp.json()["message"].lower()

    # Verify tokens were saved
    row = session.execute(text("SELECT access_token FROM schwab.tokens WHERE id = 1")).fetchone()
    assert row[0] == "test_access"


def test_schwab_token_refresh(session):
    """Token refresh should update access_token and access_expires."""
    from collectors.schwab import _refresh_access_token

    # Seed an expired token
    session.execute(
        text("""
            INSERT INTO schwab.tokens (id, access_token, refresh_token, access_expires, refresh_expires)
            VALUES (1, 'old_access', 'valid_refresh', :expired, :future)
            ON CONFLICT (id) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                access_expires = EXCLUDED.access_expires,
                refresh_expires = EXCLUDED.refresh_expires
        """),
        {
            "expired": datetime.now(timezone.utc) - timedelta(minutes=5),
            "future": datetime.now(timezone.utc) + timedelta(days=6),
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new_access",
        "refresh_token": "valid_refresh",
        "expires_in": 1800,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("collectors.schwab.httpx.post", return_value=mock_response):
        _refresh_access_token(session, "client_id", "client_secret")

    row = session.execute(text("SELECT access_token FROM schwab.tokens WHERE id = 1")).fetchone()
    assert row[0] == "new_access"
```

Note: the `client` fixture is defined in `tests/test_healthkit_webhook.py` and should be moved to `tests/conftest.py`. When implementing, move the `client` and `_FakeCtx` fixtures to `conftest.py`.

- [ ] **Step 2: Add Schwab OAuth endpoints to webhook/server.py**

Append these endpoints to `webhook/server.py`:

```python
import httpx as httpx_client  # rename to avoid conflict with fastapi
from urllib.parse import urlencode
import base64


@app.get("/schwab/auth")
def schwab_auth_redirect():
    """Redirect user to Schwab's OAuth authorization page."""
    config = _get_schwab_config()
    params = urlencode({
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
    })
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=f"https://api.schwabapi.com/v1/oauth/authorize?{params}",
        status_code=307,
    )


@app.get("/schwab/callback")
def schwab_callback(code: str):
    """Exchange authorization code for access and refresh tokens."""
    config = _get_schwab_config()

    credentials = base64.b64encode(
        f"{config['client_id']}:{config['client_secret']}".encode()
    ).decode()

    resp = httpx.post(
        "https://api.schwabapi.com/v1/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    tokens = resp.json()

    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.execute(
            text("""
                INSERT INTO schwab.tokens (id, access_token, refresh_token, access_expires, refresh_expires, updated_at)
                VALUES (1, :access, :refresh, :access_exp, :refresh_exp, :now)
                ON CONFLICT (id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    access_expires = EXCLUDED.access_expires,
                    refresh_expires = EXCLUDED.refresh_expires,
                    updated_at = EXCLUDED.updated_at
            """),
            {
                "access": tokens["access_token"],
                "refresh": tokens["refresh_token"],
                "access_exp": now + timedelta(seconds=tokens.get("expires_in", 1800)),
                "refresh_exp": now + timedelta(days=7),
                "now": now,
            },
        )

    return {"message": "Schwab tokens stored successfully"}


def _get_schwab_config() -> dict:
    return {
        "client_id": os.environ.get("SCHWAB_CLIENT_ID", ""),
        "client_secret": os.environ.get("SCHWAB_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("SCHWAB_REDIRECT_URI", ""),
    }
```

Also add at the top of `webhook/server.py`:

```python
import base64
from datetime import timedelta
```

- [ ] **Step 3: Move test fixtures to conftest.py**

Move the `client` and `_FakeCtx` fixtures from `tests/test_healthkit_webhook.py` to `tests/conftest.py`:

```python
# Add to tests/conftest.py

@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr("webhook.server.get_session", lambda: _FakeCtx(session))
    from webhook.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class _FakeCtx:
    def __init__(self, session):
        self._session = session
    def __enter__(self):
        return self._session
    def __exit__(self, *args):
        pass
```

Remove the duplicate from `tests/test_healthkit_webhook.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_schwab.py tests/test_healthkit_webhook.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webhook/server.py tests/test_schwab.py tests/conftest.py tests/test_healthkit_webhook.py
git commit -m "feat: add Schwab OAuth flow (auth redirect + callback token exchange)"
```

---

## Task 11: Schwab Data Collector

**Files:**
- Create: `collectors/schwab.py`
- Modify: `tests/test_schwab.py` (add data collection tests)

Schwab API:
- Must first get account hashes via `GET /trader/v1/accounts/accountNumbers`
- Positions: `GET /trader/v1/accounts/{hash}?fields=positions`
- Transactions: `GET /trader/v1/accounts/{hash}/transactions?startDate=...&endDate=...`
- Auth: `Bearer {access_token}`
- Rate limit: 120 calls/min

- [ ] **Step 1: Add data collection tests to tests/test_schwab.py**

Append to `tests/test_schwab.py`:

```python
def test_schwab_parse_positions():
    """Test parsing Schwab API position response."""
    from collectors.schwab import _parse_positions

    api_response = {
        "securitiesAccount": {
            "positions": [
                {
                    "longQuantity": 100.0,
                    "instrument": {"assetType": "EQUITY", "symbol": "AAPL"},
                    "marketValue": 15025.00,
                    "averageLongPrice": 150.25,
                },
                {
                    "longQuantity": 50.0,
                    "instrument": {"assetType": "EQUITY", "symbol": "MSFT"},
                    "marketValue": 21000.00,
                    "averageLongPrice": 420.00,
                },
            ]
        }
    }

    rows = _parse_positions(api_response, account_db_id=1, as_of="2026-03-28")
    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["quantity"] == 100.0
    assert rows[0]["market_value"] == 15025.00
    assert rows[0]["account_id"] == 1


def test_schwab_parse_transactions():
    """Test parsing Schwab API transaction response."""
    from collectors.schwab import _parse_transactions

    api_response = [
        {
            "activityId": 12345,
            "time": "2026-03-15T10:30:00+0000",
            "type": "TRADE",
            "netAmount": -5000.00,
            "description": "Bought 10 shares AAPL",
            "transferItems": [
                {"instrument": {"symbol": "AAPL"}, "amount": 10.0}
            ],
        }
    ]

    rows = _parse_transactions(api_response, account_db_id=1)
    assert len(rows) == 1
    assert rows[0]["transaction_id"] == "12345"
    assert rows[0]["transaction_type"] == "TRADE"
    assert rows[0]["amount"] == -5000.00
```

- [ ] **Step 2: Write collectors/schwab.py**

```python
"""Schwab collector — OAuth token management + brokerage data fetch."""

import argparse
import base64
import logging
import os
import sys
from datetime import datetime, timezone, timedelta, date

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import get_session, bulk_upsert
from shared.telegram import send_alert

logger = logging.getLogger("basin.schwab")

SCHWAB_API = "https://api.schwabapi.com"
TOKEN_REFRESH_BUFFER = timedelta(minutes=2)
REFRESH_ALERT_THRESHOLD = timedelta(hours=24)


def _get_auth_header(client_id: str, client_secret: str) -> str:
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _refresh_access_token(session, client_id: str, client_secret: str):
    """Refresh the access token using the stored refresh token."""
    row = session.execute(
        text("SELECT refresh_token FROM schwab.tokens WHERE id = 1")
    ).fetchone()
    if not row:
        raise RuntimeError("No Schwab tokens stored. Complete OAuth flow first.")

    resp = httpx.post(
        f"{SCHWAB_API}/v1/oauth/token",
        headers={
            "Authorization": f"Basic {_get_auth_header(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": row[0],
        },
        timeout=10,
    )
    resp.raise_for_status()
    tokens = resp.json()

    now = datetime.now(timezone.utc)
    session.execute(
        text("""
            UPDATE schwab.tokens SET
                access_token = :access,
                access_expires = :access_exp,
                updated_at = :now
            WHERE id = 1
        """),
        {
            "access": tokens["access_token"],
            "access_exp": now + timedelta(seconds=tokens.get("expires_in", 1800)),
            "now": now,
        },
    )


def _ensure_valid_token(session, client_id: str, client_secret: str) -> str:
    """Return a valid access token, refreshing if needed."""
    row = session.execute(
        text("SELECT access_token, access_expires, refresh_expires FROM schwab.tokens WHERE id = 1")
    ).fetchone()
    if not row:
        raise RuntimeError("No Schwab tokens stored. Complete OAuth flow first.")

    access_token, access_expires, refresh_expires = row
    now = datetime.now(timezone.utc)

    # Check if refresh token is expired
    if refresh_expires <= now:
        raise RuntimeError("Schwab refresh token has expired. Manual re-auth required.")

    # Refresh access token if expired or about to expire
    if access_expires <= now + TOKEN_REFRESH_BUFFER:
        logger.info("Access token expired, refreshing...")
        _refresh_access_token(session, client_id, client_secret)
        row = session.execute(
            text("SELECT access_token FROM schwab.tokens WHERE id = 1")
        ).fetchone()
        return row[0]

    return access_token


def _check_refresh_token_expiry(session):
    """Check if refresh token is expiring soon and alert via Telegram."""
    row = session.execute(
        text("SELECT refresh_expires FROM schwab.tokens WHERE id = 1")
    ).fetchone()
    if not row:
        send_alert("No Schwab tokens stored. Complete OAuth flow.")
        return

    refresh_expires = row[0]
    now = datetime.now(timezone.utc)
    remaining = refresh_expires - now

    if remaining <= REFRESH_ALERT_THRESHOLD:
        hours = int(remaining.total_seconds() / 3600)
        redirect_uri = os.environ.get("SCHWAB_REDIRECT_URI", "")
        auth_url = redirect_uri.rsplit("/callback", 1)[0] + "/auth"
        send_alert(
            f"Schwab refresh token expires in *{hours}h*.\n"
            f"Re-auth: {auth_url}"
        )
    else:
        days = remaining.days
        logger.info(f"Schwab refresh token OK, expires in {days}d")


def _parse_positions(account_data: dict, account_db_id: int, as_of: str) -> list[dict]:
    """Parse positions from Schwab account response."""
    positions = account_data.get("securitiesAccount", {}).get("positions", [])
    rows = []
    for p in positions:
        instrument = p.get("instrument", {})
        rows.append({
            "account_id": account_db_id,
            "symbol": instrument.get("symbol", "UNKNOWN"),
            "asset_type": instrument.get("assetType", "EQUITY"),
            "quantity": p.get("longQuantity", 0) - p.get("shortQuantity", 0),
            "market_value": p.get("marketValue"),
            "cost_basis": p.get("averageLongPrice"),
            "as_of": as_of,
        })
    return rows


def _parse_transactions(transactions: list, account_db_id: int) -> list[dict]:
    """Parse transactions from Schwab API response."""
    rows = []
    for t in transactions:
        symbol = None
        quantity = None
        transfer_items = t.get("transferItems", [])
        if transfer_items:
            instrument = transfer_items[0].get("instrument", {})
            symbol = instrument.get("symbol")
            quantity = transfer_items[0].get("amount")

        rows.append({
            "account_id": account_db_id,
            "transaction_id": str(t["activityId"]),
            "transaction_type": t.get("type", "UNKNOWN"),
            "symbol": symbol,
            "quantity": quantity,
            "amount": t.get("netAmount", 0),
            "transacted_at": t.get("time", ""),
            "description": t.get("description"),
        })
    return rows


class SchwabCollector(BaseCollector):
    name = "schwab"

    def collect(self, session) -> int:
        client_id = os.environ.get("SCHWAB_CLIENT_ID", "")
        client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            logger.warning("Schwab credentials not configured, skipping")
            return 0

        # Check refresh token expiry (alerts if < 24h)
        _check_refresh_token_expiry(session)

        token = _ensure_valid_token(session, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}

        # Step 1: Get account number -> hash mapping
        resp = httpx.get(
            f"{SCHWAB_API}/trader/v1/accounts/accountNumbers",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        account_maps = resp.json()

        total = 0
        today = date.today().isoformat()

        for acct in account_maps:
            account_number = acct["accountNumber"]
            account_hash = acct["hashValue"]

            # Upsert account
            bulk_upsert(
                session,
                table="schwab.accounts",
                rows=[{
                    "account_id": account_number,
                    "account_hash": account_hash,
                    "account_type": "unknown",  # Updated when we fetch account details
                    "nickname": None,
                }],
                conflict_columns=["account_id"],
            )

            # Get the DB id for this account
            db_id = session.execute(
                text("SELECT id FROM schwab.accounts WHERE account_id = :aid"),
                {"aid": account_number},
            ).scalar()

            # Step 2: Fetch positions
            resp = httpx.get(
                f"{SCHWAB_API}/trader/v1/accounts/{account_hash}",
                headers=headers,
                params={"fields": "positions"},
                timeout=10,
            )
            resp.raise_for_status()
            account_data = resp.json()

            # Update account type from response
            acct_type = account_data.get("securitiesAccount", {}).get("type", "unknown")
            session.execute(
                text("UPDATE schwab.accounts SET account_type = :t WHERE id = :id"),
                {"t": acct_type, "id": db_id},
            )

            position_rows = _parse_positions(account_data, db_id, today)
            total += bulk_upsert(
                session,
                table="schwab.positions",
                rows=position_rows,
                conflict_columns=["account_id", "symbol", "as_of"],
            )

            # Step 3: Fetch transactions (last 30 days)
            start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59.999Z")

            resp = httpx.get(
                f"{SCHWAB_API}/trader/v1/accounts/{account_hash}/transactions",
                headers=headers,
                params={"startDate": start_date, "endDate": end_date},
                timeout=15,
            )
            resp.raise_for_status()
            txn_data = resp.json()

            txn_rows = _parse_transactions(txn_data, db_id)
            total += bulk_upsert(
                session,
                table="schwab.transactions",
                rows=txn_rows,
                conflict_columns=["transaction_id"],
            )

        return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-token", action="store_true",
                        help="Only check token expiry, don't fetch data")
    args = parser.parse_args()

    if args.check_token:
        with get_session() as session:
            _check_refresh_token_expiry(session)
    else:
        collector = SchwabCollector()
        collector.run()
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_schwab.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add collectors/schwab.py tests/test_schwab.py
git commit -m "feat: add Schwab collector with token refresh and position/transaction sync"
```

---

## Task 12: Teller Data Collector

**Files:**
- Create: `collectors/teller.py`
- Create: `tests/test_teller.py`

Teller API:
- Base URL: `https://api.teller.io`
- Auth: mTLS (cert + key) + access token as Basic auth username (empty password)
- Accounts: `GET /accounts` → `[{id, name, type, subtype, institution: {id, name}, last_four, enrollment_id, status}]`
- Balances: `GET /accounts/{id}/balances` → `{available, ledger}` (both are strings)
- Transactions: `GET /accounts/{id}/transactions?count=250` → `[{id, amount, date, description, status, details: {category, counterparty: {name}}}]`
- Pagination: use `from_id` parameter with last transaction id

- [ ] **Step 1: Write tests/test_teller.py**

```python
"""Tests for Teller banking collector."""

from collectors.teller import _parse_accounts, _parse_balances, _parse_transactions


SAMPLE_ACCOUNTS = [
    {
        "enrollment_id": "enr_abc",
        "id": "acc_checking",
        "name": "My Checking",
        "type": "depository",
        "subtype": "checking",
        "status": "open",
        "last_four": "7890",
        "institution": {"id": "chase", "name": "Chase"},
    },
    {
        "enrollment_id": "enr_abc",
        "id": "acc_credit",
        "name": "Sapphire Reserve",
        "type": "credit",
        "subtype": "credit_card",
        "status": "open",
        "last_four": "4321",
        "institution": {"id": "chase", "name": "Chase"},
    },
]

SAMPLE_BALANCES = {
    "account_id": "acc_checking",
    "available": "28575.02",
    "ledger": "28575.02",
}

SAMPLE_TRANSACTIONS = [
    {
        "id": "txn_001",
        "account_id": "acc_checking",
        "amount": "-14.50",
        "date": "2026-01-15",
        "description": "UBER EATS",
        "status": "posted",
        "details": {
            "category": "dining",
            "counterparty": {"name": "Uber Eats", "type": "organization"},
        },
    },
    {
        "id": "txn_002",
        "account_id": "acc_checking",
        "amount": "3500.00",
        "date": "2026-01-15",
        "description": "DIRECT DEPOSIT",
        "status": "posted",
        "details": {
            "category": "income",
            "counterparty": None,
        },
    },
]


def test_parse_accounts():
    institutions, accounts = _parse_accounts(SAMPLE_ACCOUNTS)

    assert len(institutions) == 1  # Both accounts are at Chase
    assert institutions[0]["institution_id"] == "chase"
    assert institutions[0]["name"] == "Chase"

    assert len(accounts) == 2
    assert accounts[0]["account_id"] == "acc_checking"
    assert accounts[0]["account_type"] == "depository"
    assert accounts[1]["subtype"] == "credit_card"


def test_parse_balances():
    rows = _parse_balances(SAMPLE_BALANCES, account_db_id=1, as_of="2026-01-15")

    assert len(rows) == 1
    assert rows[0]["available"] == 28575.02
    assert rows[0]["ledger"] == 28575.02
    assert rows[0]["account_id"] == 1


def test_parse_transactions():
    rows = _parse_transactions(SAMPLE_TRANSACTIONS, account_db_id=1)

    assert len(rows) == 2
    assert rows[0]["transaction_id"] == "txn_001"
    assert rows[0]["amount"] == -14.50
    assert rows[0]["category"] == "dining"
    assert rows[0]["counterparty"] == "Uber Eats"

    assert rows[1]["amount"] == 3500.00
    assert rows[1]["counterparty"] is None
```

- [ ] **Step 2: Write collectors/teller.py**

```python
"""Teller banking collector — fetches accounts, balances, and transactions via mTLS."""

import logging
import os
from datetime import date, datetime, timezone, timedelta

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.teller")

TELLER_API = "https://api.teller.io"
PAGE_SIZE = 250


def _make_client(access_token: str, cert_path: str, key_path: str) -> httpx.Client:
    """Create an httpx client with mTLS and Basic auth."""
    return httpx.Client(
        cert=(cert_path, key_path),
        auth=(access_token, ""),
        base_url=TELLER_API,
        timeout=15,
    )


def _parse_accounts(accounts_data: list) -> tuple[list[dict], list[dict]]:
    """Extract institution and account rows from Teller accounts response."""
    seen_institutions = {}
    institution_rows = []
    account_rows = []

    for acct in accounts_data:
        inst = acct.get("institution", {})
        inst_id = inst.get("id", "")
        if inst_id and inst_id not in seen_institutions:
            seen_institutions[inst_id] = True
            institution_rows.append({
                "institution_id": inst_id,
                "name": inst.get("name", ""),
            })

        account_rows.append({
            "account_id": acct["id"],
            "enrollment_id": acct.get("enrollment_id"),
            "institution_id_ext": inst_id,  # External ID — resolved to DB ID later
            "account_type": acct.get("type", ""),
            "name": acct.get("name"),
            "subtype": acct.get("subtype"),
            "last_four": acct.get("last_four"),
            "status": acct.get("status", "open"),
        })

    return institution_rows, account_rows


def _parse_balances(balance_data: dict, account_db_id: int, as_of: str) -> list[dict]:
    """Parse balance response (values are strings in Teller API)."""
    available = balance_data.get("available")
    ledger = balance_data.get("ledger")

    return [{
        "account_id": account_db_id,
        "available": float(available) if available else None,
        "ledger": float(ledger) if ledger else None,
        "as_of": as_of,
    }]


def _parse_transactions(transactions_data: list, account_db_id: int) -> list[dict]:
    """Parse transaction response."""
    rows = []
    for t in transactions_data:
        details = t.get("details", {}) or {}
        counterparty_obj = details.get("counterparty")
        counterparty = counterparty_obj.get("name") if counterparty_obj else None

        rows.append({
            "account_id": account_db_id,
            "transaction_id": t["id"],
            "amount": float(t.get("amount", 0)),
            "description": t.get("description"),
            "category": details.get("category"),
            "date": t.get("date"),
            "status": t.get("status", "unknown"),
            "counterparty": counterparty,
        })

    return rows


class TellerCollector(BaseCollector):
    name = "teller"

    def collect(self, session) -> int:
        access_token = os.environ.get("TELLER_ACCESS_TOKEN", "")
        cert_path = os.environ.get("TELLER_CERT_PATH", "")
        key_path = os.environ.get("TELLER_KEY_PATH", "")

        if not access_token or not cert_path:
            logger.warning("Teller credentials not configured, skipping")
            return 0

        client = _make_client(access_token, cert_path, key_path)
        total = 0
        today = date.today().isoformat()

        try:
            # Step 1: Fetch and upsert accounts
            resp = client.get("/accounts")
            resp.raise_for_status()
            accounts_data = resp.json()

            institution_rows, account_rows = _parse_accounts(accounts_data)

            # Upsert institutions
            total += bulk_upsert(
                session,
                table="teller.institutions",
                rows=institution_rows,
                conflict_columns=["institution_id"],
            )

            for acct_row in account_rows:
                # Resolve institution DB ID
                inst_ext_id = acct_row.pop("institution_id_ext")
                inst_db_id = session.execute(
                    text("SELECT id FROM teller.institutions WHERE institution_id = :iid"),
                    {"iid": inst_ext_id},
                ).scalar()

                acct_row["institution_id"] = inst_db_id
                total += bulk_upsert(
                    session,
                    table="teller.accounts",
                    rows=[acct_row],
                    conflict_columns=["account_id"],
                )

                # Get account DB ID
                acct_db_id = session.execute(
                    text("SELECT id FROM teller.accounts WHERE account_id = :aid"),
                    {"aid": acct_row["account_id"]},
                ).scalar()

                # Step 2: Fetch balances
                try:
                    resp = client.get(f"/accounts/{acct_row['account_id']}/balances")
                    resp.raise_for_status()
                    balance_rows = _parse_balances(resp.json(), acct_db_id, today)
                    total += bulk_upsert(
                        session,
                        table="teller.balances",
                        rows=balance_rows,
                        conflict_columns=["account_id", "as_of"],
                    )
                except httpx.HTTPStatusError as e:
                    logger.warning(f"Failed to fetch balances for {acct_row['account_id']}: {e}")

                # Step 3: Fetch transactions (paginated)
                all_txns = []
                from_id = None
                while True:
                    params = {"count": PAGE_SIZE}
                    if from_id:
                        params["from_id"] = from_id

                    resp = client.get(
                        f"/accounts/{acct_row['account_id']}/transactions",
                        params=params,
                    )
                    resp.raise_for_status()
                    page = resp.json()

                    if not page:
                        break

                    all_txns.extend(page)
                    if len(page) < PAGE_SIZE:
                        break

                    from_id = page[-1]["id"]

                txn_rows = _parse_transactions(all_txns, acct_db_id)
                total += bulk_upsert(
                    session,
                    table="teller.transactions",
                    rows=txn_rows,
                    conflict_columns=["transaction_id"],
                )

        finally:
            client.close()

        return total


if __name__ == "__main__":
    collector = TellerCollector()
    collector.run()
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_teller.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add collectors/teller.py tests/test_teller.py
git commit -m "feat: add Teller banking collector with mTLS and pagination"
```

---

## Task 13: CLI Health Dashboard

**Files:**
- Create: `cli/health.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write tests/test_cli.py**

```python
"""Tests for CLI health dashboard."""

from datetime import datetime, timezone, timedelta
from click.testing import CliRunner
from sqlalchemy import text


def _seed_collector_runs(session):
    """Insert sample collector runs for testing."""
    now = datetime.now(timezone.utc)
    runs = [
        ("healthkit", now - timedelta(hours=2), now - timedelta(hours=2, seconds=-12), "success", 142),
        ("hevy", now - timedelta(hours=18), now - timedelta(hours=18, seconds=-8), "success", 86),
        ("schwab", now - timedelta(hours=3), now - timedelta(hours=3, seconds=-12), "success", 23),
        ("teller", now - timedelta(hours=17), now - timedelta(hours=17, seconds=-10), "success", 204),
    ]
    for collector, started, finished, status, rows in runs:
        session.execute(
            text("""
                INSERT INTO basin.collector_runs (collector, started_at, finished_at, status, rows_upserted)
                VALUES (:c, :s, :f, :st, :r)
            """),
            {"c": collector, "s": started, "f": finished, "st": status, "r": rows},
        )


def test_health_summary(session, monkeypatch):
    from cli.health import cli

    _seed_collector_runs(session)
    monkeypatch.setattr("cli.health.get_session", lambda: _FakeCtx(session))

    runner = CliRunner()
    result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert "healthkit" in result.output
    assert "hevy" in result.output
    assert "schwab" in result.output
    assert "teller" in result.output
    assert "success" in result.output


def test_health_detail(session, monkeypatch):
    from cli.health import cli

    _seed_collector_runs(session)
    monkeypatch.setattr("cli.health.get_session", lambda: _FakeCtx(session))

    runner = CliRunner()
    result = runner.invoke(cli, ["--detail", "schwab"])

    assert result.exit_code == 0
    assert "schwab" in result.output
    assert "23" in result.output


class _FakeCtx:
    def __init__(self, session):
        self._session = session
    def __enter__(self):
        return self._session
    def __exit__(self, *args):
        pass
```

- [ ] **Step 2: Write cli/health.py**

```python
"""Basin CLI — collector health dashboard."""

import logging
from datetime import datetime, timezone

import click
from sqlalchemy import text

from shared.db import get_session

logger = logging.getLogger("basin.cli")

COLLECTORS = ["healthkit", "hevy", "schwab", "teller"]


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

    # Schwab token status
    token_row = session.execute(
        text("SELECT refresh_expires FROM schwab.tokens WHERE id = 1")
    ).fetchone()

    click.echo()
    if token_row:
        remaining = token_row[0] - datetime.now(timezone.utc)
        days = remaining.days
        hours = int((remaining.total_seconds() % 86400) / 3600)
        click.echo(f"Schwab refresh token expires in {days}d {hours}h")
    else:
        click.echo("Schwab: no tokens stored (OAuth not completed)")
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add cli/health.py tests/test_cli.py
git commit -m "feat: add CLI health dashboard with summary and detail views"
```

---

## Task 14: Backup Script

**Files:**
- Create: `scripts/backup.sh`

- [ ] **Step 1: Write the backup script**

```bash
#!/usr/bin/env bash
# Daily Postgres backup — run inside collector container via cron
set -euo pipefail

BACKUP_DIR="/data/backups"
RETENTION_DAYS=30
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/basin_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u)] Starting backup..."

# pg_dump via the postgres container over the Docker network
PGPASSWORD="${BASIN_PG_PASSWORD:-}" pg_dump \
    -h postgres \
    -U basin \
    -d basin \
    --no-owner \
    --no-privileges \
    | gzip > "$BACKUP_FILE"

echo "[$(date -u)] Backup written to $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Remove backups older than retention period
find "$BACKUP_DIR" -name "basin_*.sql.gz" -mtime "+${RETENTION_DAYS}" -delete
echo "[$(date -u)] Cleaned up backups older than ${RETENTION_DAYS} days"
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x scripts/backup.sh
git add scripts/backup.sh
git commit -m "feat: add daily Postgres backup script with 30-day retention"
```

---

## Task 15: Deploy to VM

**Files:**
- No new files — this task deploys the built project

- [ ] **Step 1: Initialize git repo on VM**

SSH into the VM and set up the Basin project:

```bash
ssh root@<VM_HOST> "cd /opt/basin && git init && git remote add origin https://github.com/jehantar/Basin.git && git pull origin main"
```

- [ ] **Step 2: Create .env with 1Password references**

SSH and create the `.env` file:

```bash
ssh root@<VM_HOST> "cat > /opt/basin/.env << 'EOF'
BASIN_PG_PASSWORD=\"op://Basin/Postgres/password\"
SCHWAB_CLIENT_ID=\"op://Basin/Schwab/client_id\"
SCHWAB_CLIENT_SECRET=\"op://Basin/Schwab/client_secret\"
SCHWAB_REDIRECT_URI=\"http://<VM_IP>:8075/schwab/callback\"
TELLER_ACCESS_TOKEN=\"op://Basin/Teller/access_token\"
TELLER_CERT_PATH=/certs/teller/certificate.pem
TELLER_KEY_PATH=/certs/teller/private_key.pem
TELEGRAM_BOT_TOKEN=\"op://Basin/Telegram/bot_token\"
TELEGRAM_CHAT_ID=\"op://Basin/Telegram/chat_id\"
EOF"
```

Note: The 1Password vault entries (`op://Basin/*`) must be created in 1Password before this works. Teller cert/key paths are not secrets — they point to files mounted in the container.

- [ ] **Step 3: Set directory ownership**

```bash
ssh root@<VM_HOST> "chown -R basin:basin /opt/basin"
```

- [ ] **Step 4: Run bootstrap script**

```bash
ssh root@<VM_HOST> "bash /opt/basin/scripts/bootstrap-vm.sh"
```

- [ ] **Step 5: Build and start Docker Compose**

```bash
ssh root@<VM_HOST> "cd /opt/basin && op run --env-file=.env -- docker compose up -d --build"
```

- [ ] **Step 6: Verify services are running**

```bash
ssh root@<VM_HOST> "cd /opt/basin && docker compose ps"
```

Expected output: three services (postgres, collector, webhook) all showing `Up (healthy)`.

- [ ] **Step 7: Verify webhook is reachable over Tailscale**

From your local machine:

```bash
curl http://<VM_IP>:8075/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 8: Verify Postgres schema was created**

```bash
ssh root@<VM_HOST> "cd /opt/basin && docker compose exec postgres psql -U basin -d basin -c '\\dn'"
```

Expected: schemas `basin`, `healthkit`, `hevy`, `schwab`, `teller` listed.

- [ ] **Step 9: Run the health CLI**

```bash
ssh root@<VM_HOST> "cd /opt/basin && docker compose exec collector python -m cli.health"
```

Expected: shows all collectors with "never" since no data has been ingested yet.

- [ ] **Step 10: Commit any deployment adjustments and push**

If any fixes were needed during deployment, commit them and push:

```bash
git add -A && git commit -m "fix: deployment adjustments" && git push origin main
```

---

## Verification Checklist

After deployment, verify each component end-to-end:

- [ ] **HealthKit webhook:** Configure Health Auto Export to POST to `http://<VM_IP>:8075/healthkit/webhook`. Trigger a manual export. Check `basin.collector_runs` and `healthkit.metrics`.
- [ ] **Hevy CSV:** SCP a Hevy export CSV to `/opt/basin/data/hevy/drop/` on the VM. Run `docker compose exec collector python -m collectors.hevy`. Check that data appears in `hevy.workouts` and `hevy.sets`.
- [ ] **Schwab OAuth:** Visit `http://<VM_IP>:8075/schwab/auth` over Tailscale (only after registering the app at developer.schwab.com). Complete login. Check `schwab.tokens` for stored tokens.
- [ ] **Teller:** Only testable after completing Teller Connect enrollment and placing certs on the VM. Run `docker compose exec collector python -m collectors.teller` once credentials are in place.
- [ ] **CLI health:** Run `docker compose exec collector python -m cli.health` — should show recent runs for whichever collectors have been tested.
- [ ] **Backup:** Run `docker compose exec collector /app/scripts/backup.sh`. Check `/data/backups/` for the gzipped dump.
- [ ] **Telegram alert:** Test by manually calling `python -c "from shared.telegram import send_alert; send_alert('test')"` inside the collector container.
