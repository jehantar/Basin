"""Tests for Schwab OAuth flow and data collector."""

import base64
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import text


def test_schwab_auth_redirect(client):
    """The /schwab/auth endpoint should redirect to Schwab's OAuth page."""
    resp = client.get("/schwab/auth", follow_redirects=False)
    assert resp.status_code == 307
    assert "api.schwabapi.com/v1/oauth/authorize" in resp.headers["location"]


def test_schwab_callback_exchanges_code(client, session):
    """The /schwab/callback should exchange the auth code for tokens."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "test_access",
        "refresh_token": "test_refresh",
        "expires_in": 1800,
        "token_type": "Bearer",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("webhook.server.httpx.post", return_value=mock_response):
        resp = client.get("/schwab/callback?code=test_auth_code")

    assert resp.status_code == 200
    assert "stored" in resp.json()["message"].lower()

    # Verify tokens were saved
    row = session.execute(text("SELECT access_token FROM schwab.tokens WHERE id = 1")).fetchone()
    assert row[0] == "test_access"


def test_schwab_token_refresh(session):
    """Token refresh should update access_token and access_expires."""
    from collectors.schwab import _refresh_access_token

    # Seed an expired token
    session.execute(
        text("""
            INSERT INTO schwab.tokens (id, access_token, refresh_token, access_expires, refresh_expires)
            VALUES (1, 'old_access', 'valid_refresh', :expired, :future)
            ON CONFLICT (id) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                access_expires = EXCLUDED.access_expires,
                refresh_expires = EXCLUDED.refresh_expires
        """),
        {
            "expired": datetime.now(timezone.utc) - timedelta(minutes=5),
            "future": datetime.now(timezone.utc) + timedelta(days=6),
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new_access",
        "refresh_token": "valid_refresh",
        "expires_in": 1800,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("collectors.schwab.httpx.post", return_value=mock_response):
        _refresh_access_token(session, "client_id", "client_secret")

    row = session.execute(text("SELECT access_token FROM schwab.tokens WHERE id = 1")).fetchone()
    assert row[0] == "new_access"


def test_schwab_parse_positions():
    """Test parsing Schwab API position response."""
    from collectors.schwab import _parse_positions

    api_response = {
        "securitiesAccount": {
            "positions": [
                {
                    "longQuantity": 100.0,
                    "instrument": {"assetType": "EQUITY", "symbol": "AAPL"},
                    "marketValue": 15025.00,
                    "averageLongPrice": 150.25,
                },
                {
                    "longQuantity": 50.0,
                    "instrument": {"assetType": "EQUITY", "symbol": "MSFT"},
                    "marketValue": 21000.00,
                    "averageLongPrice": 420.00,
                },
            ]
        }
    }

    rows = _parse_positions(api_response, account_db_id=1, as_of="2026-03-28")
    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["quantity"] == 100.0
    assert rows[0]["market_value"] == 15025.00
    assert rows[0]["account_id"] == 1


def test_schwab_parse_transactions():
    """Test parsing Schwab API transaction response."""
    from collectors.schwab import _parse_transactions

    api_response = [
        {
            "activityId": 12345,
            "time": "2026-03-15T10:30:00+0000",
            "type": "TRADE",
            "netAmount": -5000.00,
            "description": "Bought 10 shares AAPL",
            "transferItems": [
                {"instrument": {"symbol": "AAPL"}, "amount": 10.0}
            ],
        }
    ]

    rows = _parse_transactions(api_response, account_db_id=1)
    assert len(rows) == 1
    assert rows[0]["transaction_id"] == "12345"
    assert rows[0]["transaction_type"] == "TRADE"
    assert rows[0]["amount"] == -5000.00
