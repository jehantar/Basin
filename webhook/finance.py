"""Finance dashboard — API endpoints, categorization, and HTML serving."""

import os
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session
from webhook.dashboard_shared import _parse_date_range, _response_metadata

router = APIRouter()

# --- Merchant categorization ---

MERCHANT_CATEGORIES = {
    "trader joe": "groceries",
    "whole foods": "groceries",
    "safeway": "groceries",
    "costco": "groceries",
    "kroger": "groceries",
    "doordash": "dining",
    "uber eats": "dining",
    "grubhub": "dining",
    "seamless": "dining",
    "uber": "transportation",
    "lyft": "transportation",
    "amazon": "shopping",
    "target": "shopping",
    "walmart": "shopping",
    "airbnb": "travel",
    "hotel": "travel",
    "airline": "travel",
    "netflix": "subscriptions",
    "spotify": "subscriptions",
    "hulu": "subscriptions",
    "apple.com/bill": "subscriptions",
    "disney+": "subscriptions",
    "gym": "health",
    "fitness": "health",
    "cvs": "health",
    "walgreens": "health",
    "shell": "fuel",
    "chevron": "fuel",
    "exxon": "fuel",
    "bp ": "fuel",
    "comcast": "utilities",
    "verizon": "utilities",
    "at&t": "utilities",
    "t-mobile": "utilities",
}

# Precompute sorted rules: longer keywords match first (e.g., "uber eats" before "uber")
_SORTED_RULES = sorted(MERCHANT_CATEGORIES.items(), key=lambda x: len(x[0]), reverse=True)

_NORMALIZE_RE = re.compile(r'[^\w\s&]')


def _normalize_merchant(text_val: str) -> str:
    """Lowercase, strip punctuation (keep &), collapse spaces."""
    if not text_val:
        return ""
    return _NORMALIZE_RE.sub(" ", text_val.lower()).strip()


def categorize_transaction(description: str | None, counterparty: str | None, teller_category: str | None) -> str:
    """Determine effective category for a transaction.

    Precedence:
    1. Keyword match on description/counterparty (longest match first)
    2. Teller category if present and not generic
    3. "other"
    """
    # Check description and counterparty against keyword rules
    for field in [description, counterparty]:
        if not field:
            continue
        normalized = _normalize_merchant(field)
        for keyword, category in _SORTED_RULES:
            if keyword in normalized:
                return category

    # Fall back to Teller category if meaningful
    if teller_category and teller_category.lower() not in ("general", "uncategorized", ""):
        return teller_category.lower()

    return "other"


# --- Shared transaction fetching ---

def _fetch_spend_transactions(session, start_date: date, end_date: date) -> list[dict]:
    """Fetch posted transactions with categorization applied.

    Includes both charges (positive) and returns/refunds (negative)
    so net spend is accurate. Only excludes card bill payments.

    Excludes:
    - Pending transactions (only posted/settled)
    - Automatic payments / bill payments (card payments, not real activity)
    """
    rows = session.execute(text("""
        SELECT t.amount, t.description, t.category, t.counterparty, t.date,
               a.name as card_name, a.last_four
        FROM teller.transactions t
        JOIN teller.accounts a ON t.account_id = a.id
        WHERE t.date BETWEEN :start AND :end
          AND t.status = 'posted'
        ORDER BY t.date
    """), {"start": start_date, "end": end_date}).fetchall()

    transactions = []
    for r in rows:
        amount = float(r[0])
        description = r[1] or ""
        teller_cat = r[2]
        counterparty = r[3]
        txn_date = r[4]
        card_name = r[5]
        last_four = r[6]

        # Skip card bill payments only
        desc_lower = description.lower()
        if "automatic payment" in desc_lower or "autopay" in desc_lower or "payment thank you" in desc_lower:
            continue

        effective_category = categorize_transaction(description, counterparty, teller_cat)

        transactions.append({
            "amount": round(amount, 2),
            "description": description,
            "counterparty": counterparty,
            "category": effective_category,
            "date": str(txn_date),
            "card_name": card_name,
            "last_four": last_four,
        })

    return transactions


# --- API endpoints ---

@router.get("/dashboard/finance")
def serve_finance():
    """Serve the finance dashboard HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "finance.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())


@router.get("/api/finance/overview")
def get_finance_overview(start: str | None = None, end: str | None = None):
    """Monthly spend trend + category breakdown."""
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        transactions = _fetch_spend_transactions(session, start_date, end_date)

    # Monthly spend aggregation
    monthly = defaultdict(float)
    for t in transactions:
        month = t["date"][:7]  # "YYYY-MM"
        monthly[month] += t["amount"]

    monthly_spend = [
        {"month": m, "total": round(v, 2)}
        for m, v in sorted(monthly.items())
    ]

    # Category breakdown
    cat_totals = defaultdict(lambda: {"total": 0.0, "count": 0})
    for t in transactions:
        cat_totals[t["category"]]["total"] += t["amount"]
        cat_totals[t["category"]]["count"] += 1

    category_breakdown = sorted(
        [{"category": k, "total": round(v["total"], 2), "count": v["count"]} for k, v in cat_totals.items()],
        key=lambda x: x["total"],
        reverse=True,
    )

    # Summary
    total_spend = round(sum(t["amount"] for t in transactions), 2)
    num_months = len(monthly) if monthly else 1
    avg_monthly = round(total_spend / num_months, 2)
    biggest = category_breakdown[0] if category_breakdown else None
    uncategorized_count = sum(1 for t in transactions if t["category"] == "other")

    summary = {
        "total_spend": total_spend,
        "avg_monthly": avg_monthly,
        "transaction_count": len(transactions),
        "biggest_category": biggest["category"] if biggest else None,
        "biggest_category_amount": biggest["total"] if biggest else None,
        "uncategorized_count": uncategorized_count,
    }

    # Simplified transaction list for drill-down
    txn_list = [{
        "date": t["date"],
        "description": t["counterparty"] or t["description"],
        "amount": t["amount"],
        "category": t["category"],
        "card": t["card_name"],
    } for t in sorted(transactions, key=lambda x: x["date"], reverse=True)]

    return {
        **_response_metadata(start_date, end_date),
        "monthly_spend": monthly_spend,
        "category_breakdown": category_breakdown,
        "summary": summary,
        "transactions": txn_list,
    }


@router.get("/api/finance/merchants")
def get_finance_merchants(start: str | None = None, end: str | None = None):
    """Top merchants by total spend."""
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        transactions = _fetch_spend_transactions(session, start_date, end_date)

    # Aggregate by merchant (use counterparty if available, else description)
    merchant_agg = defaultdict(lambda: {"total": 0.0, "count": 0, "transactions": []})
    for t in transactions:
        name = t["counterparty"] or t["description"] or "Unknown"
        name = " ".join(name.split()).strip()
        merchant_agg[name]["total"] += t["amount"]
        merchant_agg[name]["count"] += 1
        merchant_agg[name]["transactions"].append({
            "date": t["date"],
            "description": t["description"],
            "amount": t["amount"],
            "card": t["card_name"],
        })

    merchants = sorted(
        [
            {
                "name": k,
                "total_spend": round(v["total"], 2),
                "transaction_count": v["count"],
                "avg_transaction": round(v["total"] / v["count"], 2) if v["count"] > 0 else 0,
                "transactions": sorted(v["transactions"], key=lambda x: x["date"], reverse=True),
            }
            for k, v in merchant_agg.items()
        ],
        key=lambda x: x["total_spend"],
        reverse=True,
    )[:25]

    return {
        **_response_metadata(start_date, end_date),
        "merchants": merchants,
    }


@router.get("/api/finance/cards")
def get_finance_cards(start: str | None = None, end: str | None = None):
    """Per-card spending breakdown."""
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        transactions = _fetch_spend_transactions(session, start_date, end_date)

    # Aggregate by card + collect transactions per card
    card_agg = defaultdict(lambda: {"total": 0.0, "count": 0, "categories": defaultdict(float), "last_four": "", "transactions": []})
    for t in transactions:
        key = t["card_name"]
        card_agg[key]["total"] += t["amount"]
        card_agg[key]["count"] += 1
        card_agg[key]["categories"][t["category"]] += t["amount"]
        card_agg[key]["last_four"] = t["last_four"]
        card_agg[key]["transactions"].append({
            "date": t["date"],
            "description": t["counterparty"] or t["description"],
            "amount": t["amount"],
            "category": t["category"],
        })

    cards = []
    for name, data in sorted(card_agg.items(), key=lambda x: x[1]["total"], reverse=True):
        top_cat = max(data["categories"].items(), key=lambda x: x[1]) if data["categories"] else ("other", 0)
        cards.append({
            "name": name,
            "last_four": data["last_four"],
            "total_spend": round(data["total"], 2),
            "transaction_count": data["count"],
            "top_category": top_cat[0],
            "top_category_amount": round(top_cat[1], 2),
            "transactions": sorted(data["transactions"], key=lambda x: x["date"], reverse=True),
        })

    return {
        **_response_metadata(start_date, end_date),
        "cards": cards,
    }
