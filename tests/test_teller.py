"""Tests for Teller banking collector."""

from collectors.teller import _parse_accounts, _parse_balances, _parse_transactions


SAMPLE_ACCOUNTS = [
    {
        "enrollment_id": "enr_abc",
        "id": "acc_checking",
        "name": "My Checking",
        "type": "depository",
        "subtype": "checking",
        "status": "open",
        "last_four": "7890",
        "institution": {"id": "chase", "name": "Chase"},
    },
    {
        "enrollment_id": "enr_abc",
        "id": "acc_credit",
        "name": "Sapphire Reserve",
        "type": "credit",
        "subtype": "credit_card",
        "status": "open",
        "last_four": "4321",
        "institution": {"id": "chase", "name": "Chase"},
    },
]

SAMPLE_BALANCES = {
    "account_id": "acc_checking",
    "available": "28575.02",
    "ledger": "28575.02",
}

SAMPLE_TRANSACTIONS = [
    {
        "id": "txn_001",
        "account_id": "acc_checking",
        "amount": "-14.50",
        "date": "2026-01-15",
        "description": "UBER EATS",
        "status": "posted",
        "details": {
            "category": "dining",
            "counterparty": {"name": "Uber Eats", "type": "organization"},
        },
    },
    {
        "id": "txn_002",
        "account_id": "acc_checking",
        "amount": "3500.00",
        "date": "2026-01-15",
        "description": "DIRECT DEPOSIT",
        "status": "posted",
        "details": {
            "category": "income",
            "counterparty": None,
        },
    },
]


def test_parse_accounts():
    institutions, accounts = _parse_accounts(SAMPLE_ACCOUNTS)

    assert len(institutions) == 1  # Both accounts are at Chase
    assert institutions[0]["institution_id"] == "chase"
    assert institutions[0]["name"] == "Chase"

    assert len(accounts) == 2
    assert accounts[0]["account_id"] == "acc_checking"
    assert accounts[0]["account_type"] == "depository"
    assert accounts[1]["subtype"] == "credit_card"


def test_parse_balances():
    rows = _parse_balances(SAMPLE_BALANCES, account_db_id=1, as_of="2026-01-15")

    assert len(rows) == 1
    assert rows[0]["available"] == 28575.02
    assert rows[0]["ledger"] == 28575.02
    assert rows[0]["account_id"] == 1


def test_parse_transactions():
    rows = _parse_transactions(SAMPLE_TRANSACTIONS, account_db_id=1)

    assert len(rows) == 2
    assert rows[0]["transaction_id"] == "txn_001"
    assert rows[0]["amount"] == -14.50
    assert rows[0]["category"] == "dining"
    assert rows[0]["counterparty"] == "Uber Eats"

    assert rows[1]["amount"] == 3500.00
    assert rows[1]["counterparty"] is None
