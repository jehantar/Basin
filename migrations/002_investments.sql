-- Investment tracker — stock watchlist and daily price history
-- Run manually: psql -f migrations/002_investments.sql

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
