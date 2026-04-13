-- Teller Connect — token storage for re-enrollment flow
-- Run manually: psql $DATABASE_URL -f migrations/005_teller_tokens.sql

BEGIN;

CREATE TABLE IF NOT EXISTS teller.tokens (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    access_token    TEXT NOT NULL,
    enrollment_id   TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
