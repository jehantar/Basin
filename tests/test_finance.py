"""Tests for finance dashboard API endpoints."""

from sqlalchemy import text


def _seed_teller_data(session):
    """Insert sample Teller institutions, accounts, and transactions."""
    # Institution
    session.execute(text("""
        INSERT INTO teller.institutions (institution_id, name)
        VALUES ('chase', 'Chase')
        ON CONFLICT (institution_id) DO NOTHING
    """))
    inst_id = session.execute(
        text("SELECT id FROM teller.institutions WHERE institution_id = 'chase'")
    ).scalar()

    # Two accounts
    session.execute(text("""
        INSERT INTO teller.accounts (account_id, enrollment_id, institution_id, account_type, name, subtype, last_four, status)
        VALUES ('acc_freedom', 'enr_1', :inst, 'credit', 'Freedom Unlimited', 'credit_card', '2712', 'open'),
               ('acc_sapphire', 'enr_1', :inst, 'credit', 'Sapphire Preferred', 'credit_card', '4385', 'open')
        ON CONFLICT (account_id) DO NOTHING
    """), {"inst": inst_id})

    freedom_id = session.execute(
        text("SELECT id FROM teller.accounts WHERE account_id = 'acc_freedom'")
    ).scalar()
    sapphire_id = session.execute(
        text("SELECT id FROM teller.accounts WHERE account_id = 'acc_sapphire'")
    ).scalar()

    # Transactions across different months, merchants, categories
    txns = [
        # Freedom - groceries
        (freedom_id, 'txn_1', -85.50, 'TRADER JOES #123', 'groceries', '2026-02-10', 'posted', 'Trader Joes'),
        (freedom_id, 'txn_2', -120.00, 'WHOLE FOODS MARKET', 'general', '2026-02-15', 'posted', 'Whole Foods'),
        # Freedom - dining
        (freedom_id, 'txn_3', -35.00, 'DOORDASH*ORDER', 'general', '2026-02-20', 'posted', 'DoorDash'),
        (freedom_id, 'txn_4', -22.50, 'UBER EATS', 'general', '2026-03-01', 'posted', 'Uber Eats'),
        # Freedom - transportation (uber, not uber eats)
        (freedom_id, 'txn_5', -15.00, 'UBER TRIP', 'transportation', '2026-03-05', 'posted', 'Uber'),
        # Freedom - unknown merchant -> "other"
        (freedom_id, 'txn_6', -50.00, 'RANDOM STORE XYZ', 'general', '2026-03-10', 'posted', None),
        # Sapphire - shopping
        (sapphire_id, 'txn_7', -200.00, 'AMAZON.COM', 'shopping', '2026-02-12', 'posted', 'Amazon'),
        (sapphire_id, 'txn_8', -75.00, 'TARGET', 'shopping', '2026-03-08', 'posted', 'Target'),
        # Sapphire - pending (should be excluded)
        (sapphire_id, 'txn_9', -30.00, 'PENDING STORE', 'general', '2026-03-15', 'pending', None),
        # Freedom - automatic payment (should be excluded)
        (freedom_id, 'txn_10', 500.00, 'AUTOMATIC PAYMENT', 'general', '2026-03-01', 'posted', None),
        # Freedom - refund/credit (positive, should be excluded)
        (freedom_id, 'txn_11', 25.00, 'REFUND FROM STORE', 'general', '2026-03-02', 'posted', None),
    ]
    for acct_id, txn_id, amount, desc, cat, dt, status, counterparty in txns:
        session.execute(text("""
            INSERT INTO teller.transactions (account_id, transaction_id, amount, description, category, date, status, counterparty)
            VALUES (:acct, :txn, :amt, :desc, :cat, :dt, :status, :cp)
            ON CONFLICT (transaction_id) DO NOTHING
        """), {"acct": acct_id, "txn": txn_id, "amt": amount, "desc": desc, "cat": cat, "dt": dt, "status": status, "cp": counterparty})


# --- Categorization tests ---

def test_categorization_uber_eats_vs_uber():
    """'uber eats' should match dining, not transportation."""
    from webhook.finance import categorize_transaction
    assert categorize_transaction("UBER EATS", "Uber Eats", None) == "dining"
    assert categorize_transaction("UBER TRIP", "Uber", None) == "transportation"


def test_categorization_keyword_beats_teller():
    """Keyword match takes precedence over Teller category."""
    from webhook.finance import categorize_transaction
    assert categorize_transaction("TRADER JOES #123", None, "general") == "groceries"


def test_categorization_teller_fallback():
    """Teller category used when no keyword matches and category is meaningful."""
    from webhook.finance import categorize_transaction
    assert categorize_transaction("UNKNOWN STORE", None, "dining") == "dining"


def test_categorization_general_becomes_other():
    """'general' Teller category falls through to 'other'."""
    from webhook.finance import categorize_transaction
    assert categorize_transaction("UNKNOWN STORE", None, "general") == "other"
    assert categorize_transaction("UNKNOWN STORE", None, None) == "other"


def test_categorization_case_insensitive():
    """Matching is case-insensitive."""
    from webhook.finance import categorize_transaction
    assert categorize_transaction("trader joe", None, None) == "groceries"
    assert categorize_transaction("TRADER JOE", None, None) == "groceries"


# --- API endpoint tests ---

def test_finance_html_served(client):
    """GET /dashboard/finance returns HTML."""
    resp = client.get("/dashboard/finance")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_finance_overview_returns_data(session, client):
    _seed_teller_data(session)
    resp = client.get("/api/finance/overview?start=2026-01-01&end=2026-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert "monthly_spend" in data
    assert "category_breakdown" in data
    assert "summary" in data
    assert data["summary"]["total_spend"] > 0
    assert data["summary"]["transaction_count"] > 0
    # Verify pending and positive amounts are excluded
    # We seeded 8 qualifying spend txns (excluding pending, autopay, refund)
    assert data["summary"]["transaction_count"] == 8


def test_finance_overview_excludes_pending(session, client):
    """Pending transactions should not be included."""
    _seed_teller_data(session)
    resp = client.get("/api/finance/overview?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    # txn_9 is pending, should be excluded
    descriptions = []
    for cat in data["category_breakdown"]:
        descriptions.append(cat["category"])
    # Just verify total count excludes the pending one
    assert data["summary"]["transaction_count"] == 8


def test_finance_overview_empty_range(client):
    resp = client.get("/api/finance/overview?start=2020-01-01&end=2020-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["monthly_spend"] == []
    assert data["category_breakdown"] == []
    assert data["summary"]["total_spend"] == 0


def test_finance_merchants_returns_data(session, client):
    _seed_teller_data(session)
    resp = client.get("/api/finance/merchants?start=2026-01-01&end=2026-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["merchants"]) > 0
    # Should be sorted by spend descending
    spends = [m["total_spend"] for m in data["merchants"]]
    assert spends == sorted(spends, reverse=True)


def test_finance_cards_returns_data(session, client):
    _seed_teller_data(session)
    resp = client.get("/api/finance/cards?start=2026-01-01&end=2026-12-31")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["cards"]) == 2  # Freedom + Sapphire
    names = [c["name"] for c in data["cards"]]
    assert "Freedom Unlimited" in names
    assert "Sapphire Preferred" in names


def test_finance_invalid_date(client):
    resp = client.get("/api/finance/overview?start=bad-date")
    assert resp.status_code == 400


def test_dashboard_redirect(client):
    """GET /dashboard redirects to /dashboard/fitness."""
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 307
    assert "/dashboard/fitness" in resp.headers["location"]
