"""Teller banking collector — fetches accounts, balances, and transactions via mTLS."""

import logging
import os
from datetime import date, datetime, timezone, timedelta

import httpx
from sqlalchemy import text

from collectors.base import BaseCollector
from shared.db import bulk_upsert

logger = logging.getLogger("basin.teller")

TELLER_API = "https://api.teller.io"
PAGE_SIZE = 250


def _make_client(access_token: str, cert_path: str, key_path: str) -> httpx.Client:
    """Create an httpx client with mTLS and Basic auth."""
    return httpx.Client(
        cert=(cert_path, key_path),
        auth=(access_token, ""),
        base_url=TELLER_API,
        timeout=15,
    )


def _parse_accounts(accounts_data: list) -> tuple[list[dict], list[dict]]:
    """Extract institution and account rows from Teller accounts response."""
    seen_institutions = {}
    institution_rows = []
    account_rows = []

    for acct in accounts_data:
        inst = acct.get("institution", {})
        inst_id = inst.get("id", "")
        if inst_id and inst_id not in seen_institutions:
            seen_institutions[inst_id] = True
            institution_rows.append({
                "institution_id": inst_id,
                "name": inst.get("name", ""),
            })

        account_rows.append({
            "account_id": acct["id"],
            "enrollment_id": acct.get("enrollment_id"),
            "institution_id_ext": inst_id,  # External ID — resolved to DB ID later
            "account_type": acct.get("type", ""),
            "name": acct.get("name"),
            "subtype": acct.get("subtype"),
            "last_four": acct.get("last_four"),
            "status": acct.get("status", "open"),
        })

    return institution_rows, account_rows


def _parse_balances(balance_data: dict, account_db_id: int, as_of: str) -> list[dict]:
    """Parse balance response (values are strings in Teller API)."""
    available = balance_data.get("available")
    ledger = balance_data.get("ledger")

    return [{
        "account_id": account_db_id,
        "available": float(available) if available else None,
        "ledger": float(ledger) if ledger else None,
        "as_of": as_of,
    }]


def _parse_transactions(transactions_data: list, account_db_id: int) -> list[dict]:
    """Parse transaction response."""
    rows = []
    for t in transactions_data:
        details = t.get("details", {}) or {}
        counterparty_obj = details.get("counterparty")
        counterparty = counterparty_obj.get("name") if counterparty_obj else None

        rows.append({
            "account_id": account_db_id,
            "transaction_id": t["id"],
            "amount": float(t.get("amount", 0)),
            "description": t.get("description"),
            "category": details.get("category"),
            "date": t.get("date"),
            "status": t.get("status", "unknown"),
            "counterparty": counterparty,
        })

    return rows


class TellerCollector(BaseCollector):
    name = "teller"

    def collect(self, session) -> int:
        access_token = os.environ.get("TELLER_ACCESS_TOKEN", "")
        cert_path = os.environ.get("TELLER_CERT_PATH", "")
        key_path = os.environ.get("TELLER_KEY_PATH", "")

        if not access_token or not cert_path:
            logger.warning("Teller credentials not configured, skipping")
            return 0

        client = _make_client(access_token, cert_path, key_path)
        total = 0
        today = date.today().isoformat()

        try:
            # Step 1: Fetch and upsert accounts
            resp = client.get("/accounts")
            resp.raise_for_status()
            accounts_data = resp.json()

            institution_rows, account_rows = _parse_accounts(accounts_data)

            # Upsert institutions
            total += bulk_upsert(
                session,
                table="teller.institutions",
                rows=institution_rows,
                conflict_columns=["institution_id"],
            )

            for acct_row in account_rows:
                # Resolve institution DB ID
                inst_ext_id = acct_row.pop("institution_id_ext")
                inst_db_id = session.execute(
                    text("SELECT id FROM teller.institutions WHERE institution_id = :iid"),
                    {"iid": inst_ext_id},
                ).scalar()

                acct_row["institution_id"] = inst_db_id
                total += bulk_upsert(
                    session,
                    table="teller.accounts",
                    rows=[acct_row],
                    conflict_columns=["account_id"],
                )

                # Get account DB ID
                acct_db_id = session.execute(
                    text("SELECT id FROM teller.accounts WHERE account_id = :aid"),
                    {"aid": acct_row["account_id"]},
                ).scalar()

                # Step 2: Fetch balances
                try:
                    resp = client.get(f"/accounts/{acct_row['account_id']}/balances")
                    resp.raise_for_status()
                    balance_rows = _parse_balances(resp.json(), acct_db_id, today)
                    total += bulk_upsert(
                        session,
                        table="teller.balances",
                        rows=balance_rows,
                        conflict_columns=["account_id", "as_of"],
                    )
                except httpx.HTTPStatusError as e:
                    logger.warning(f"Failed to fetch balances for {acct_row['account_id']}: {e}")

                # Step 3: Fetch transactions (paginated)
                all_txns = []
                from_id = None
                while True:
                    params = {"count": PAGE_SIZE}
                    if from_id:
                        params["from_id"] = from_id

                    resp = client.get(
                        f"/accounts/{acct_row['account_id']}/transactions",
                        params=params,
                    )
                    resp.raise_for_status()
                    page = resp.json()

                    if not page:
                        break

                    all_txns.extend(page)
                    if len(page) < PAGE_SIZE:
                        break

                    from_id = page[-1]["id"]

                txn_rows = _parse_transactions(all_txns, acct_db_id)
                total += bulk_upsert(
                    session,
                    table="teller.transactions",
                    rows=txn_rows,
                    conflict_columns=["transaction_id"],
                )

        finally:
            client.close()

        return total


if __name__ == "__main__":
    collector = TellerCollector()
    collector.run()
