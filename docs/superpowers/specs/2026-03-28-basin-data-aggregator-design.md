# Basin: Personal Data Aggregator — Design Spec

## Overview

Basin is a self-hosted personal data warehouse that continuously collects and normalizes data from multiple sources into a single Postgres database. It runs on a Hetzner VM (Ubuntu 24.04, 2 vCPU, 2GB RAM) alongside an existing reservation bot, fully containerized via Docker Compose and isolated from the bot's systemd service.

The goal: any future tool gets a clean one-line data access layer instead of building integrations from scratch.

## Data Sources

| Source | Domain | Connection Method | Auth |
|--------|--------|-------------------|------|
| Apple HealthKit | Cardio workouts, resting HR, weight, VO2 max, cadence | Webhook (Health Auto Export app) + XML dump import | None (Tailscale-only network) |
| Hevy | Strength training — sets, reps, weight per exercise | Manual CSV export dropped in a watched folder | None (local files) |
| Schwab | Brokerage — taxable + Roth IRA positions & transactions | OAuth REST API (developer.schwab.com) | OAuth 2.0, 30-min access / 7-day refresh tokens |
| Teller | Banking — checking, savings, credit cards | REST API with mTLS certificate auth | Client certificate |

Wealthfront investment data is out of scope for v1 (Teller doesn't cover it). A placeholder schema extension can be added later.

## Infrastructure

### VM Current State

- Ubuntu 24.04, 2 vCPU, 2GB RAM, 38GB disk (31GB free)
- Reservation bot at `/opt/reservebot/` running as systemd service (~95MB RAM)
- 1Password CLI installed for secrets management
- Tailscale for networking — VM is not exposed to public internet
- No Docker or Postgres installed (both will be set up from scratch)
- No swap configured (will add 1GB swap file as safety net)

### Docker Compose Stack

Three services on a shared `basin` bridge network:

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

  collector:
    build:
      dockerfile: Dockerfile.collector
    restart: unless-stopped
    depends_on:
      - postgres
    volumes:
      - ./data:/data
      - ./certs:/certs:ro
    networks:
      - basin
    environment:
      DATABASE_URL: postgresql://basin:${BASIN_PG_PASSWORD}@postgres:5432/basin
      SCHWAB_CLIENT_ID: ${SCHWAB_CLIENT_ID}
      SCHWAB_CLIENT_SECRET: ${SCHWAB_CLIENT_SECRET}
      TELLER_CERT_PATH: /certs/teller/certificate.pem
      TELLER_KEY_PATH: /certs/teller/private_key.pem
      TELLER_ACCESS_TOKEN: ${TELLER_ACCESS_TOKEN}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}

  webhook:
    build:
      dockerfile: Dockerfile.webhook
    restart: unless-stopped
    depends_on:
      - postgres
    ports:
      - "100.125.126.42:8075:8000"
    networks:
      - basin
    environment:
      DATABASE_URL: postgresql://basin:${BASIN_PG_PASSWORD}@postgres:5432/basin
      SCHWAB_CLIENT_ID: ${SCHWAB_CLIENT_ID}
      SCHWAB_CLIENT_SECRET: ${SCHWAB_CLIENT_SECRET}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}

volumes:
  pgdata:

networks:
  basin:
    driver: bridge
```

Key decisions:
- Webhook binds to Tailscale IP only (`100.125.126.42:8075`), never `0.0.0.0`
- Postgres tuned conservatively for 2GB RAM VM
- Migrations run automatically on first start via `docker-entrypoint-initdb.d`
- Teller mTLS certs mounted read-only, stored outside git in `certs/`
- The reservation bot remains a systemd service, completely untouched

### Secrets Management

All secrets use the 1Password CLI pattern matching the existing reservation bot:
- `.env` file contains `op://` references (e.g., `BASIN_PG_PASSWORD="op://Basin/Postgres/password"`)
- Docker Compose is started via `op run --env-file=.env -- docker compose up -d`
- 1Password resolves references at runtime; no plaintext secrets on disk
- `.env.example` committed to git with placeholder values; `.env` is gitignored

### Memory Budget

| Component | Estimated RAM |
|-----------|--------------|
| Existing (OS, reservebot, Tailscale) | ~500MB |
| Docker daemon | ~70MB |
| Postgres (tuned) | ~100MB |
| FastAPI webhook | ~60MB |
| Cron container (idle) | ~20MB |
| Collector processes (transient) | ~50MB peak |
| **Total** | **~800MB of 1.9GB** |
| 1GB swap file | Safety net for spikes |

### Connecting the Reservation Bot (Future)

If the reservation bot ever needs DB access:
1. Add `networks: [basin]` to a new Compose override, or
2. Connect its systemd process to the `basin` Docker network: `docker network connect basin <container>`, or
3. Expose Postgres on `127.0.0.1:5432` and connect from the host

No changes needed to the bot unless/until this is wanted.

## Postgres Schema

### HealthKit (`healthkit` schema)

```sql
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
```

### Hevy (`hevy` schema)

```sql
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
    workout_id      BIGINT NOT NULL REFERENCES hevy.workouts(id),
    exercise_id     BIGINT NOT NULL REFERENCES hevy.exercises(id),
    set_index       INTEGER NOT NULL,
    weight_kg       DOUBLE PRECISION,
    reps            INTEGER,
    distance_m      DOUBLE PRECISION,
    duration_sec    INTEGER,
    rpe             DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workout_id, exercise_id, set_index)
);
```

### Schwab (`schwab` schema)

```sql
CREATE SCHEMA schwab;

CREATE TABLE schwab.accounts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      TEXT NOT NULL UNIQUE,
    account_type    TEXT NOT NULL,
    nickname        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE schwab.positions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES schwab.accounts(id),
    symbol          TEXT NOT NULL,
    quantity        DOUBLE PRECISION NOT NULL,
    market_value    DOUBLE PRECISION,
    cost_basis      DOUBLE PRECISION,
    as_of           DATE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, symbol, as_of)
);

CREATE TABLE schwab.transactions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES schwab.accounts(id),
    transaction_id  TEXT NOT NULL UNIQUE,
    transaction_type TEXT NOT NULL,
    symbol          TEXT,
    quantity        DOUBLE PRECISION,
    amount          DOUBLE PRECISION NOT NULL,
    transacted_at   TIMESTAMPTZ NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE schwab.tokens (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    access_expires  TIMESTAMPTZ NOT NULL,
    refresh_expires TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The `tokens` table is a singleton (enforced by `CHECK (id = 1)`) — always one row, overwritten on refresh.

### Teller (`teller` schema)

```sql
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
    institution_id  BIGINT NOT NULL REFERENCES teller.institutions(id),
    account_type    TEXT NOT NULL,
    name            TEXT,
    subtype         TEXT,
    last_four       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE teller.balances (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES teller.accounts(id),
    available       DOUBLE PRECISION,
    ledger          DOUBLE PRECISION,
    as_of           DATE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, as_of)
);

CREATE TABLE teller.transactions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES teller.accounts(id),
    transaction_id  TEXT NOT NULL UNIQUE,
    amount          DOUBLE PRECISION NOT NULL,
    description     TEXT,
    category        TEXT,
    date            DATE NOT NULL,
    status          TEXT NOT NULL,
    counterparty    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### System (`basin` schema)

```sql
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

CREATE TABLE basin.hevy_imports (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename        TEXT NOT NULL UNIQUE,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count       INTEGER
);
```

## Collector Architecture

### Project Layout

```
basin/
├── docker-compose.yml
├── .env                          # op:// references (gitignored)
├── .env.example                  # Placeholder values (committed)
├── .gitignore
├── collectors/
│   ├── __init__.py
│   ├── base.py                   # BaseCollector — run tracking, error handling, logging
│   ├── healthkit.py              # XML dump import from /data/healthkit/imports/
│   ├── hevy.py                   # CSV drop folder watcher at /data/hevy/drop/
│   ├── schwab.py                 # OAuth token refresh + positions/transactions
│   └── teller.py                 # mTLS cert auth + accounts/balances/transactions
├── webhook/
│   ├── __init__.py
│   └── server.py                 # FastAPI — HealthKit webhook + Schwab OAuth callback
├── cli/
│   ├── __init__.py
│   └── health.py                 # `python -m cli.health` — collector status dashboard
├── shared/
│   ├── __init__.py
│   ├── db.py                     # get_engine(), get_session(), upsert helpers
│   ├── telegram.py               # send_alert(message) via Telegram Bot API
│   └── config.py                 # Env var reading
├── crontab                       # Schedule file copied into collector container
├── Dockerfile.collector
├── Dockerfile.webhook
├── migrations/
│   └── 001_initial.sql           # Full schema DDL
└── data/
    ├── hevy/
    │   └── drop/                 # Upload CSVs here
    └── healthkit/
        └── imports/              # Drop XML exports here
```

### BaseCollector

Every collector inherits from `BaseCollector`:

```python
class BaseCollector:
    name: str  # 'healthkit', 'hevy', 'schwab', 'teller'

    def run(self):
        # 1. INSERT into basin.collector_runs (status='running')
        # 2. Call self.collect()
        # 3. UPDATE basin.collector_runs (status='success', rows_upserted=N)
        # On exception:
        # 4. UPDATE basin.collector_runs (status='error', error_message=str(e))
        # 5. Send Telegram alert on 3+ consecutive failures

    def collect(self) -> int:
        """Override in subclass. Returns count of rows upserted."""
        raise NotImplementedError
```

Each collector is invoked as a standalone script: `python -m collectors.hevy`

### Collector Behaviors

**HealthKit (webhook path):**
- Health Auto Export app on iPhone configured to POST JSON to `http://100.125.126.42:8075/healthkit/webhook`
- FastAPI endpoint parses the payload, upserts into `healthkit.metrics` and `healthkit.workouts`
- Idempotency: `ON CONFLICT (metric_type, recorded_at, source_name) DO UPDATE`

**HealthKit (XML import path):**
- Export from iPhone Health app produces an `export.xml` file
- Drop it in `/data/healthkit/imports/` on the VM
- Cron job runs `python -m collectors.healthkit`, parses XML, upserts same tables
- Safe to reimport — same idempotency keys as webhook path

**Hevy:**
- Export CSV from Hevy app (Profile > Settings > Export & Import Data > Export Workouts)
- Upload CSV to `/data/hevy/drop/` on the VM (via scp, rsync, or any file transfer)
- Collector scans the drop directory, checks each filename against `basin.hevy_imports`
- For unprocessed CSVs: parse rows, upsert exercises/workouts/sets, record filename in `hevy_imports`
- Already-imported files are skipped

**Schwab:**
- On each run, check `schwab.tokens.access_expires`
- If expired: use refresh token to get new access token, update `schwab.tokens`
- If refresh token near expiry (within 24h): send Telegram alert with re-auth URL
- Fetch accounts, positions, transactions from Schwab API
- Upsert into `schwab.accounts`, `schwab.positions`, `schwab.transactions`
- `--check-token` flag: only check token expiry, don't fetch data (used by watchdog cron)

**Schwab OAuth re-auth flow:**
- Webhook service exposes `/schwab/auth` — redirects to Schwab OAuth authorize URL
- User visits over Tailscale, logs in at Schwab, gets redirected back to `/schwab/callback`
- Callback exchanges code for tokens, stores in `schwab.tokens`
- Required approximately every 7 days when refresh token expires

**Teller:**
- Uses mTLS (client certificate + private key) plus a per-enrollment access token for authentication
- Access token is issued once during Teller Connect enrollment and is long-lived (no refresh needed)
- Fetch accounts, balances, transactions
- Upsert into `teller.institutions`, `teller.accounts`, `teller.balances`, `teller.transactions`
- Teller enrollment (linking bank accounts) is done once via Teller Connect in a browser

### Idempotent Upsert Pattern

All collectors use the same SQL pattern via a shared helper:

```python
INSERT INTO schema.table (col1, col2, ...)
VALUES (...)
ON CONFLICT (unique_key_columns) DO UPDATE SET
    col1 = EXCLUDED.col1,
    col2 = EXCLUDED.col2,
    ...
```

Re-running any collector with the same data produces zero duplicates and updates any changed values.

## Cron Schedule

```crontab
# Hevy — check drop folder daily at 6:00 AM UTC
0 6 * * *   python -m collectors.hevy >> /var/log/basin/hevy.log 2>&1

# HealthKit XML — check import folder daily at 6:05 AM UTC
5 6 * * *   python -m collectors.healthkit >> /var/log/basin/healthkit.log 2>&1

# Schwab — daily at 9:00 PM UTC (after US market close 4 PM ET)
0 21 * * *  python -m collectors.schwab >> /var/log/basin/schwab.log 2>&1

# Teller — daily at 7:00 AM UTC
0 7 * * *   python -m collectors.teller >> /var/log/basin/teller.log 2>&1

# Schwab token watchdog — every 6 hours, alerts if refresh token expiring within 24h
0 */6 * * * python -m collectors.schwab --check-token >> /var/log/basin/schwab.log 2>&1
```

## CLI Health Dashboard

Run from the host via: `docker compose exec collector python -m cli.health`

For convenience, add a shell alias: `alias basin='docker compose -f /opt/basin/docker-compose.yml exec collector python -m cli.health'`

```
$ basin health

Collector     Last Run       Status    Rows
───────────────────────────────────────────────
healthkit     2h ago         success   142
hevy          18h ago        success   86
schwab        3h ago         success   23
teller        17h ago        success   204

Schwab refresh token expires in 5d 12h

$ basin health --detail schwab
Last 5 runs:
  2026-03-28 21:00  success  23 rows   12.4s
  2026-03-27 21:00  success  19 rows   11.8s
  2026-03-26 21:00  error    "Token refresh failed: 401"
  ...
```

Queries `basin.collector_runs` for run history and `schwab.tokens` for token expiry status.

## Schwab Re-Auth Alerting

Three-tier approach:

1. **Automatic (every collector run):** If `access_token` is expired, silently refresh using `refresh_token`. No user action needed. Happens every 30 minutes during scheduled runs.

2. **Watchdog (every 6 hours):** Cron checks `refresh_expires`. If within 24 hours, sends Telegram alert:
   > `[Basin] Schwab refresh token expires in 18h. Re-auth: http://100.125.126.42:8075/schwab/auth`

3. **Manual re-auth (approximately weekly):** User clicks the link over Tailscale, gets redirected through Schwab's OAuth flow, new tokens are stored. Takes about 30 seconds.

If the refresh token expires without re-auth, the Schwab collector logs an error and sends a Telegram alert on each failed run until re-auth is completed.

## VM Setup Requirements

Before deploying Basin, the VM needs:

1. **Docker Engine + Docker Compose plugin** — installed via official Docker apt repository
2. **1GB swap file** — safety net for memory spikes (`fallocate -l 1G /swapfile`)
3. **Basin project directory** — `/opt/basin/` owned by a dedicated `basin` user
4. **Teller certificates** — stored in `/opt/basin/certs/teller/` (outside git)
5. **Teller enrollment** — complete Teller Connect once in a browser to get the access token
6. **Schwab OAuth redirect URI** — register `http://100.125.126.42:8075/schwab/callback` at developer.schwab.com
7. **1Password `.env` file** — at `/opt/basin/.env` with `op://` references

The reservation bot at `/opt/reservebot/` remains completely untouched.

## Python Dependencies

- `sqlalchemy` — ORM and connection management
- `psycopg2-binary` — Postgres driver
- `fastapi` + `uvicorn` — webhook server
- `httpx` — HTTP client for Schwab and Teller APIs (supports mTLS)
- `click` — CLI framework for health dashboard
- `python-dotenv` — fallback env loading for local development

## Out of Scope (v1)

- Wealthfront investment data (no Teller coverage)
- Historical data backfill beyond what APIs/exports provide
- Web UI or dashboard (CLI only for v1)
- Automated deployment/CI (manual `docker compose up` for now)
- Data retention policies or archiving
