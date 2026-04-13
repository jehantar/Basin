-- Strava API — OAuth tokens and activity data (elevation, GPS, splits)
-- Run manually: psql $DATABASE_URL -f migrations/004_strava.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS strava;

------------------------------------------------------------
-- OAuth token storage (singleton row, id=1)
------------------------------------------------------------
CREATE TABLE strava.tokens (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- Activity summaries from Strava API
------------------------------------------------------------
CREATE TABLE strava.activities (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strava_id               BIGINT NOT NULL UNIQUE,
    name                    TEXT,
    sport_type              TEXT,
    start_date              TIMESTAMPTZ NOT NULL,
    distance_m              DOUBLE PRECISION,
    moving_time_sec         INTEGER,
    elapsed_time_sec        INTEGER,
    total_elevation_gain_m  DOUBLE PRECISION,
    elev_high_m             DOUBLE PRECISION,
    elev_low_m              DOUBLE PRECISION,
    average_speed_mps       DOUBLE PRECISION,
    max_speed_mps           DOUBLE PRECISION,
    average_heartrate       DOUBLE PRECISION,
    max_heartrate           DOUBLE PRECISION,
    average_cadence         DOUBLE PRECISION,
    map_polyline            TEXT,
    calories                DOUBLE PRECISION,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_strava_activities_date
    ON strava.activities (start_date DESC);

CREATE INDEX idx_strava_activities_sport
    ON strava.activities (sport_type, start_date DESC);

COMMIT;
