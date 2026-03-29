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
