"""Schwab collector — OAuth token management + brokerage data fetch."""

import argparse
import base64
import logging
import os
import sys
from datetime import datetime, timezone, timedelta, date

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import get_session, bulk_upsert
from shared.telegram import send_alert

logger = logging.getLogger("basin.schwab")

SCHWAB_API = "https://api.schwabapi.com"
TOKEN_REFRESH_BUFFER = timedelta(minutes=2)
REFRESH_ALERT_THRESHOLD = timedelta(hours=24)


def _get_auth_header(client_id: str, client_secret: str) -> str:
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _refresh_access_token(session, client_id: str, client_secret: str):
    """Refresh the access token using the stored refresh token."""
    row = session.execute(
        text("SELECT refresh_token FROM schwab.tokens WHERE id = 1")
    ).fetchone()
    if not row:
        raise RuntimeError("No Schwab tokens stored. Complete OAuth flow first.")

    resp = httpx.post(
        f"{SCHWAB_API}/v1/oauth/token",
        headers={
            "Authorization": f"Basic {_get_auth_header(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": row[0],
        },
        timeout=10,
    )
    resp.raise_for_status()
    tokens = resp.json()

    now = datetime.now(timezone.utc)
    session.execute(
        text("""
            UPDATE schwab.tokens SET
                access_token = :access,
                access_expires = :access_exp,
                updated_at = :now
            WHERE id = 1
        """),
        {
            "access": tokens["access_token"],
            "access_exp": now + timedelta(seconds=tokens.get("expires_in", 1800)),
            "now": now,
        },
    )


def _ensure_valid_token(session, client_id: str, client_secret: str) -> str:
    """Return a valid access token, refreshing if needed."""
    row = session.execute(
        text("SELECT access_token, access_expires, refresh_expires FROM schwab.tokens WHERE id = 1")
    ).fetchone()
    if not row:
        raise RuntimeError("No Schwab tokens stored. Complete OAuth flow first.")

    access_token, access_expires, refresh_expires = row
    now = datetime.now(timezone.utc)

    # Check if refresh token is expired
    if refresh_expires <= now:
        raise RuntimeError("Schwab refresh token has expired. Manual re-auth required.")

    # Refresh access token if expired or about to expire
    if access_expires <= now + TOKEN_REFRESH_BUFFER:
        logger.info("Access token expired, refreshing...")
        _refresh_access_token(session, client_id, client_secret)
        row = session.execute(
            text("SELECT access_token FROM schwab.tokens WHERE id = 1")
        ).fetchone()
        return row[0]

    return access_token


def _check_refresh_token_expiry(session):
    """Check if refresh token is expiring soon and alert via Telegram."""
    row = session.execute(
        text("SELECT refresh_expires FROM schwab.tokens WHERE id = 1")
    ).fetchone()
    if not row:
        send_alert("No Schwab tokens stored. Complete OAuth flow.")
        return

    refresh_expires = row[0]
    now = datetime.now(timezone.utc)
    remaining = refresh_expires - now

    if remaining <= REFRESH_ALERT_THRESHOLD:
        hours = int(remaining.total_seconds() / 3600)
        redirect_uri = os.environ.get("SCHWAB_REDIRECT_URI", "")
        auth_url = redirect_uri.rsplit("/callback", 1)[0] + "/auth"
        send_alert(
            f"Schwab refresh token expires in *{hours}h*.\n"
            f"Re-auth: {auth_url}"
        )
    else:
        days = remaining.days
        logger.info(f"Schwab refresh token OK, expires in {days}d")


def _parse_positions(account_data: dict, account_db_id: int, as_of: str) -> list[dict]:
    """Parse positions from Schwab account response."""
    positions = account_data.get("securitiesAccount", {}).get("positions", [])
    rows = []
    for p in positions:
        instrument = p.get("instrument", {})
        rows.append({
            "account_id": account_db_id,
            "symbol": instrument.get("symbol", "UNKNOWN"),
            "asset_type": instrument.get("assetType", "EQUITY"),
            "quantity": p.get("longQuantity", 0) - p.get("shortQuantity", 0),
            "market_value": p.get("marketValue"),
            "cost_basis": p.get("averageLongPrice"),
            "as_of": as_of,
        })
    return rows


def _parse_transactions(transactions: list, account_db_id: int) -> list[dict]:
    """Parse transactions from Schwab API response."""
    rows = []
    for t in transactions:
        symbol = None
        quantity = None
        transfer_items = t.get("transferItems", [])
        if transfer_items:
            instrument = transfer_items[0].get("instrument", {})
            symbol = instrument.get("symbol")
            quantity = transfer_items[0].get("amount")

        rows.append({
            "account_id": account_db_id,
            "transaction_id": str(t["activityId"]),
            "transaction_type": t.get("type", "UNKNOWN"),
            "symbol": symbol,
            "quantity": quantity,
            "amount": t.get("netAmount", 0),
            "transacted_at": t.get("time", ""),
            "description": t.get("description"),
        })
    return rows


class SchwabCollector(BaseCollector):
    name = "schwab"

    def collect(self, session) -> int:
        client_id = os.environ.get("SCHWAB_CLIENT_ID", "")
        client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            logger.warning("Schwab credentials not configured, skipping")
            return 0

        # Check refresh token expiry (alerts if < 24h)
        _check_refresh_token_expiry(session)

        token = _ensure_valid_token(session, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}

        # Step 1: Get account number -> hash mapping
        resp = httpx.get(
            f"{SCHWAB_API}/trader/v1/accounts/accountNumbers",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        account_maps = resp.json()

        total = 0
        today = date.today().isoformat()

        for acct in account_maps:
            account_number = acct["accountNumber"]
            account_hash = acct["hashValue"]

            # Upsert account
            bulk_upsert(
                session,
                table="schwab.accounts",
                rows=[{
                    "account_id": account_number,
                    "account_hash": account_hash,
                    "account_type": "unknown",  # Updated when we fetch account details
                    "nickname": None,
                }],
                conflict_columns=["account_id"],
            )

            # Get the DB id for this account
            db_id = session.execute(
                text("SELECT id FROM schwab.accounts WHERE account_id = :aid"),
                {"aid": account_number},
            ).scalar()

            # Step 2: Fetch positions
            resp = httpx.get(
                f"{SCHWAB_API}/trader/v1/accounts/{account_hash}",
                headers=headers,
                params={"fields": "positions"},
                timeout=10,
            )
            resp.raise_for_status()
            account_data = resp.json()

            # Update account type from response
            acct_type = account_data.get("securitiesAccount", {}).get("type", "unknown")
            session.execute(
                text("UPDATE schwab.accounts SET account_type = :t WHERE id = :id"),
                {"t": acct_type, "id": db_id},
            )

            position_rows = _parse_positions(account_data, db_id, today)
            total += bulk_upsert(
                session,
                table="schwab.positions",
                rows=position_rows,
                conflict_columns=["account_id", "symbol", "as_of"],
            )

            # Step 3: Fetch transactions (last 30 days)
            start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59.999Z")

            resp = httpx.get(
                f"{SCHWAB_API}/trader/v1/accounts/{account_hash}/transactions",
                headers=headers,
                params={"startDate": start_date, "endDate": end_date},
                timeout=15,
            )
            resp.raise_for_status()
            txn_data = resp.json()

            txn_rows = _parse_transactions(txn_data, db_id)
            total += bulk_upsert(
                session,
                table="schwab.transactions",
                rows=txn_rows,
                conflict_columns=["transaction_id"],
            )

        return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-token", action="store_true",
                        help="Only check token expiry, don't fetch data")
    args = parser.parse_args()

    if args.check_token:
        with get_session() as session:
            _check_refresh_token_expiry(session)
    else:
        collector = SchwabCollector()
        collector.run()
