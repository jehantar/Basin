-- Intervals.icu — training load (CTL/ATL/TSB), pace curves, HR curves
-- Run manually: psql $DATABASE_URL -f migrations/003_intervals_icu.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS intervals;

------------------------------------------------------------
-- Daily fitness metrics from Intervals.icu wellness API
------------------------------------------------------------
CREATE TABLE intervals.daily_fitness (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    ctl             DOUBLE PRECISION,
    atl             DOUBLE PRECISION,
    tsb             DOUBLE PRECISION,
    ramp_rate       DOUBLE PRECISION,
    training_load   DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_intervals_fitness_date
    ON intervals.daily_fitness (date DESC);

------------------------------------------------------------
-- Pace curves — best effort times at various distances
------------------------------------------------------------
CREATE TABLE intervals.pace_curves (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at     DATE NOT NULL,
    period          TEXT NOT NULL,
    distance_m      DOUBLE PRECISION NOT NULL,
    time_secs       DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (captured_at, period, distance_m)
);

------------------------------------------------------------
-- HR curves — peak HR at various durations
------------------------------------------------------------
CREATE TABLE intervals.hr_curves (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at     DATE NOT NULL,
    period          TEXT NOT NULL,
    duration_secs   INTEGER NOT NULL,
    hr_bpm          INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (captured_at, period, duration_secs)
);

COMMIT;
