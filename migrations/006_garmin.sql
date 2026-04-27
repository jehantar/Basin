-- Garmin Connect — health metrics, activities, sleep, body battery, HRV, training readiness
-- Run manually: psql $DATABASE_URL -f migrations/006_garmin.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS garmin;

------------------------------------------------------------
-- Daily health summary (steps, calories, HR, VO2, stress, SpO2, etc.)
------------------------------------------------------------
CREATE TABLE garmin.daily_summary (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    total_steps     INTEGER,
    total_distance_m DOUBLE PRECISION,
    active_calories DOUBLE PRECISION,
    resting_hr      DOUBLE PRECISION,
    max_hr          DOUBLE PRECISION,
    min_hr          DOUBLE PRECISION,
    avg_stress      DOUBLE PRECISION,
    floors_climbed  INTEGER,
    intensity_minutes INTEGER,
    vo2max_running  DOUBLE PRECISION,
    respiration_avg DOUBLE PRECISION,
    spo2_avg        DOUBLE PRECISION,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- Sleep (score + stage breakdown)
------------------------------------------------------------
CREATE TABLE garmin.sleep (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    sleep_score     INTEGER,
    total_sleep_sec INTEGER,
    deep_sleep_sec  INTEGER,
    light_sleep_sec INTEGER,
    rem_sleep_sec   INTEGER,
    awake_sec       INTEGER,
    avg_spo2        DOUBLE PRECISION,
    avg_respiration DOUBLE PRECISION,
    body_battery_change INTEGER,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- Body Battery (daily high/low/charge/drain)
------------------------------------------------------------
CREATE TABLE garmin.body_battery (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    highest         INTEGER,
    lowest          INTEGER,
    start_of_day    INTEGER,
    end_of_day      INTEGER,
    charged         INTEGER,
    drained         INTEGER,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- HRV (nightly average + baseline)
------------------------------------------------------------
CREATE TABLE garmin.hrv (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    weekly_avg_ms   DOUBLE PRECISION,
    last_night_avg_ms DOUBLE PRECISION,
    last_night_5min_high_ms DOUBLE PRECISION,
    baseline_low_ms DOUBLE PRECISION,
    baseline_upper_ms DOUBLE PRECISION,
    status          TEXT,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- Training Readiness (daily score + sub-components)
------------------------------------------------------------
CREATE TABLE garmin.training_readiness (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    score           INTEGER,
    level           TEXT,
    sleep_score     INTEGER,
    recovery_score  INTEGER,
    training_load_score INTEGER,
    hrv_score       INTEGER,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- Activities (primary source, replaces healthkit.workouts + strava.activities for new data)
------------------------------------------------------------
CREATE TABLE garmin.activities (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    garmin_id       BIGINT NOT NULL UNIQUE,
    name            TEXT,
    activity_type   TEXT,
    sport_type      TEXT,
    start_time      TIMESTAMPTZ NOT NULL,
    duration_sec    DOUBLE PRECISION,
    distance_m      DOUBLE PRECISION,
    avg_hr          DOUBLE PRECISION,
    max_hr          DOUBLE PRECISION,
    avg_cadence     DOUBLE PRECISION,
    max_cadence     DOUBLE PRECISION,
    avg_power       DOUBLE PRECISION,
    avg_speed_mps   DOUBLE PRECISION,
    max_speed_mps   DOUBLE PRECISION,
    elevation_gain_m DOUBLE PRECISION,
    calories        DOUBLE PRECISION,
    training_effect_aerobic  DOUBLE PRECISION,
    training_effect_anaerobic DOUBLE PRECISION,
    vo2max_value    DOUBLE PRECISION,
    avg_ground_contact_time_ms DOUBLE PRECISION,
    avg_vertical_oscillation_cm DOUBLE PRECISION,
    avg_stride_length_m DOUBLE PRECISION,
    splits          JSONB,
    map_polyline    TEXT,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_garmin_activities_start
    ON garmin.activities (start_time DESC);

CREATE INDEX idx_garmin_activities_type
    ON garmin.activities (activity_type, start_time DESC);

------------------------------------------------------------
-- Body Composition (multiple weigh-ins per day possible)
------------------------------------------------------------
CREATE TABLE garmin.body_composition (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            DATE NOT NULL,
    recorded_at     TIMESTAMPTZ,
    weight_kg       DOUBLE PRECISION,
    bmi             DOUBLE PRECISION,
    body_fat_pct    DOUBLE PRECISION,
    muscle_mass_kg  DOUBLE PRECISION,
    bone_mass_kg    DOUBLE PRECISION,
    body_water_pct  DOUBLE PRECISION,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (date, recorded_at)
);

COMMIT;
