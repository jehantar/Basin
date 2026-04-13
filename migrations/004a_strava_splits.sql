-- Add splits column to strava.activities
-- Run manually: psql $DATABASE_URL -f migrations/004a_strava_splits.sql

ALTER TABLE strava.activities ADD COLUMN IF NOT EXISTS splits JSONB;
