-- Investment tracker seed data — edit tickers and groups to match your watchlist
-- Run after 002_investments.sql: psql -f migrations/002a_investments_seed.sql

INSERT INTO investments.watchlist (ticker, name) VALUES
    ('AAPL', 'Apple Inc.'),
    ('MSFT', 'Microsoft Corp.'),
    ('GOOG', 'Alphabet Inc.')
ON CONFLICT (ticker) DO NOTHING;

INSERT INTO investments.stock_groups (name) VALUES
    ('Tech'),
    ('Dividend')
ON CONFLICT (name) DO NOTHING;

-- Assign tickers to groups
INSERT INTO investments.stock_group_members (group_id, watchlist_id)
SELECT g.id, w.id
FROM investments.stock_groups g, investments.watchlist w
WHERE g.name = 'Tech' AND w.ticker IN ('AAPL', 'MSFT', 'GOOG')
ON CONFLICT DO NOTHING;
