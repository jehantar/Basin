"""Finance dashboard — API endpoints, categorization, and HTML serving."""

import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session
from webhook.dashboard_shared import _parse_date_range, _response_metadata

router = APIRouter()

# --- Merchant categorization ---

MERCHANT_CATEGORIES = {
    # Groceries
    "trader joe": "groceries", "whole foods": "groceries", "safeway": "groceries",
    "costco whse": "groceries", "costco": "groceries", "kroger": "groceries",
    "sprouts": "groceries", "grocery": "groceries", "market basket": "groceries",
    # Dining & restaurants
    "doordash": "dining", "dd doordash": "dining", "dd *doordash": "dining",
    "uber eats": "dining", "grubhub": "dining", "seamless": "dining",
    "restaurant": "dining", "restau": "dining", "tst*": "dining",
    "cafe": "dining", "coffee": "dining", "starbucks": "dining", "chipotle": "dining",
    "sweetgreen": "dining", "cotogna": "dining", "copra": "dining", "delfina": "dining",
    "quince": "dining", "papalote": "dining", "mugizo": "dining", "yakiniq": "dining",
    "rich table": "dining", "octavia": "dining", "kindred": "dining", "osamil": "dining",
    "ernest": "dining", "bicyclette": "dining", "bacchanal": "dining",
    "jules pizza": "dining", "eatery": "dining", "emery f&b": "dining",
    "capos": "dining", "sea nyc": "dining", "amara": "dining",
    # Bars & nightlife
    "devil's acre": "entertainment", "sullivan": "entertainment",
    "bowlero": "entertainment",
    # Transportation
    "uber": "transportation", "lyft": "transportation", "parking": "transportation",
    "bart": "transportation", "transit": "transportation",
    # Shopping & retail
    "amazon": "shopping", "target": "shopping", "walmart": "shopping",
    "suitsupply": "shopping", "grant stone": "shopping", "kachins": "shopping",
    "nordstrom": "shopping", "zara": "shopping", "uniqlo": "shopping",
    "ebay": "shopping", "etsy": "shopping", "wayfair": "shopping",
    "lululemon": "shopping", "allen edmonds": "shopping", "ray ban": "shopping",
    "sunglass": "shopping", "polo factory": "shopping", "bombay shirt": "shopping",
    "manyavar": "shopping", "freja": "shopping", "vionicshoes": "shopping",
    "fortes brothers": "shopping", "ups": "shopping",
    # Travel & accommodation
    "airbnb": "travel", "hotel": "travel", "airline": "travel", "vrbo": "travel",
    "algotels": "travel", "homeaway": "travel", "expedia": "travel",
    "united air": "travel", "delta air": "travel", "southwest": "travel",
    "four seasons": "travel", "anantara": "travel", "wynn": "travel",
    "caesers": "travel",
    # Entertainment & events
    "vivid seats": "entertainment", "vivid": "entertainment",
    "axs.com": "entertainment", "axs com": "entertainment",
    "ticketmaster": "entertainment", "movie": "entertainment", "cinema": "entertainment",
    "desi beat": "entertainment", "grand kyiv": "entertainment",
    "broadway": "entertainment",
    # Fitness & health
    "equinox": "fitness", "f45": "fitness", "barry": "fitness", "gym": "fitness",
    "peloton": "fitness", "runner": "fitness",
    "cvs": "health", "walgreens": "health", "hims": "health",
    "foot": "health", "plumbing": "health",
    # Subscriptions & digital
    "netflix": "subscriptions", "spotify": "subscriptions", "hulu": "subscriptions",
    "apple.com/bill": "subscriptions", "apple com bill": "subscriptions",
    "disney": "subscriptions", "claude.ai": "subscriptions", "claude ai": "subscriptions",
    "anthropic": "subscriptions", "openai": "subscriptions", "chatgpt": "subscriptions",
    "membership fee": "subscriptions", "stratechery": "subscriptions",
    "quandl": "subscriptions",
    # Fuel
    "shell": "fuel", "chevron": "fuel", "exxon": "fuel", "bp ": "fuel",
    # Utilities & telecom
    "comcast": "utilities", "verizon": "utilities", "at&t": "utilities", "t-mobile": "utilities",
    # Auto & vehicle
    "honda": "auto", "harbor automotive": "auto", "jiffy lube": "auto", "car wash": "auto",
    "geneva watch": "auto",
    # Wine & spirits
    "hamel family wines": "dining", "fsp*hamel": "dining", "fsp hamel": "dining",
    "tock at": "dining",
    # Paypal
    "paypal *hotel": "travel", "paypal *homeaway": "travel",
}

_NORMALIZE_RE = re.compile(r'[^\w\s&]')

# Precompute sorted rules: normalize keywords and sort longest-first (e.g., "uber eats" before "uber")
_SORTED_RULES = sorted(
    [(" ".join(_NORMALIZE_RE.sub(" ", k.lower()).split()), v) for k, v in MERCHANT_CATEGORIES.items()],
    key=lambda x: len(x[0]),
    reverse=True,
)

# Manual overrides file — persists user-assigned categories
OVERRIDES_PATH = os.environ.get("CATEGORY_OVERRIDES_PATH", "/data/category_overrides.json")


def _load_overrides() -> dict:
    """Load manual category overrides from JSON file."""
    try:
        with open(OVERRIDES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_overrides(overrides: dict):
    """Save manual category overrides to JSON file."""
    os.makedirs(os.path.dirname(OVERRIDES_PATH), exist_ok=True)
    with open(OVERRIDES_PATH, "w") as f:
        json.dump(overrides, f, indent=2)


def _normalize_merchant(text_val: str) -> str:
    """Lowercase, strip punctuation (keep &), collapse whitespace."""
    if not text_val:
        return ""
    return " ".join(_NORMALIZE_RE.sub(" ", text_val.lower()).split())


def categorize_transaction(
    description: str | None,
    counterparty: str | None,
    teller_category: str | None,
    overrides: dict | None = None,
) -> str:
    """Determine effective category for a transaction.

    Precedence:
    1. Manual override (exact match on normalized description/counterparty)
    2. Keyword match on description/counterparty (longest match first)
    3. Teller category if present and not generic
    4. "other"
    """
    if overrides is None:
        overrides = _load_overrides()

    # Check manual overrides first (exact normalized match)
    for field in [description, counterparty]:
        if not field:
            continue
        normalized = _normalize_merchant(field)
        if normalized in overrides:
            return overrides[normalized]

    # Check keyword rules
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


# --- Display bucket mapping (collapse fine categories into ~7 display groups) ---

DISPLAY_BUCKETS = {
    "groceries": "Food & Dining",
    "dining": "Food & Dining",
    "shopping": "Shopping",
    "travel": "Travel & Transport",
    "transportation": "Travel & Transport",
    "fuel": "Travel & Transport",
    "fitness": "Health & Fitness",
    "health": "Health & Fitness",
    "subscriptions": "Living",
    "utilities": "Living",
    "insurance": "Living",
    "auto": "Living",
    "entertainment": "Entertainment",
    "service": "Other",
    "other": "Other",
}


def display_category(raw_category: str) -> str:
    """Map a fine-grained category to a display bucket."""
    return DISPLAY_BUCKETS.get(raw_category, "Other")


# --- Shared transaction fetching ---

def _fetch_spend_transactions(session, start_date: date, end_date: date) -> list[dict]:
    """Fetch posted and pending transactions with categorization applied.

    Includes both charges (positive) and returns/refunds (negative)
    so net spend is accurate. Pending transactions are included in the
    list (tagged with status='pending') but should be excluded from
    aggregate totals to avoid double-counting when they settle.

    Excludes:
    - Automatic payments / bill payments (card payments, not real activity)
    """
    rows = session.execute(text("""
        SELECT t.amount, t.description, t.category, t.counterparty, t.date,
               a.name as card_name, a.last_four, t.status
        FROM teller.transactions t
        JOIN teller.accounts a ON t.account_id = a.id
        WHERE t.date BETWEEN :start AND :end
          AND t.status IN ('posted', 'pending')
        ORDER BY t.date
    """), {"start": start_date, "end": end_date}).fetchall()

    transactions = []
    overrides = _load_overrides()
    for r in rows:
        amount = float(r[0])
        description = r[1] or ""
        teller_cat = r[2]
        counterparty = r[3]
        txn_date = r[4]
        card_name = r[5]
        last_four = r[6]
        status = r[7]

        # Skip card bill payments only
        desc_lower = description.lower()
        if "automatic payment" in desc_lower or "autopay" in desc_lower or "payment thank you" in desc_lower:
            continue

        raw_category = categorize_transaction(description, counterparty, teller_cat, overrides)
        effective_category = display_category(raw_category)

        transactions.append({
            "amount": round(amount, 2),
            "description": description,
            "counterparty": counterparty,
            "category": effective_category,
            "date": str(txn_date),
            "card_name": card_name,
            "last_four": last_four,
            "status": status,
        })

    return transactions


# --- API endpoints ---

@router.get("/dashboard/finance")
def serve_finance():
    """Serve the finance dashboard HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "finance.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())


@router.post("/api/finance/categorize")
async def set_category_override(request: Request):
    """Save manual category overrides for one or more merchants.

    Accepts either:
      {"merchant": "...", "category": "..."}           — single override
      {"overrides": [{"merchant": "...", "category": "..."}, ...]}  — batch
    """
    body = await request.json()

    # Normalize to a list of overrides
    if "overrides" in body:
        items = body["overrides"]
    else:
        items = [body]

    overrides = _load_overrides()
    results = []

    for item in items:
        merchant = item.get("merchant", "")
        raw_category = item.get("category", "")
        if not merchant or not raw_category:
            continue
        normalized = _normalize_merchant(merchant)
        overrides[normalized] = raw_category
        results.append({
            "merchant": merchant,
            "normalized": normalized,
            "category": raw_category,
            "display_category": display_category(raw_category),
        })

    if results:
        _save_overrides(overrides)

    return {"status": "saved", "count": len(results), "results": results}


@router.get("/api/finance/categories")
def get_available_categories():
    """Return the list of available raw categories for the override UI."""
    return {
        "categories": sorted(set(DISPLAY_BUCKETS.keys()) - {"other", "service"}),
        "display_buckets": {k: v for k, v in DISPLAY_BUCKETS.items() if k not in ("other", "service")},
    }


@router.get("/api/finance/overview")
def get_finance_overview(start: str | None = None, end: str | None = None):
    """Monthly spend trend + category breakdown."""
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        transactions = _fetch_spend_transactions(session, start_date, end_date)

    # Only posted transactions contribute to aggregates (pending excluded to avoid double-counting)
    posted = [t for t in transactions if t["status"] == "posted"]

    # Monthly spend aggregation
    monthly = defaultdict(float)
    for t in posted:
        month = t["date"][:7]  # "YYYY-MM"
        monthly[month] += t["amount"]

    monthly_spend = [
        {"month": m, "total": round(v, 2)}
        for m, v in sorted(monthly.items())
    ]

    # Category breakdown
    cat_totals = defaultdict(lambda: {"total": 0.0, "count": 0})
    for t in posted:
        cat_totals[t["category"]]["total"] += t["amount"]
        cat_totals[t["category"]]["count"] += 1

    category_breakdown = sorted(
        [{"category": k, "total": round(v["total"], 2), "count": v["count"]} for k, v in cat_totals.items()],
        key=lambda x: x["total"],
        reverse=True,
    )

    # Summary
    total_spend = round(sum(t["amount"] for t in posted), 2)
    num_months = len(monthly) if monthly else 1
    avg_monthly = round(total_spend / num_months, 2)
    biggest = category_breakdown[0] if category_breakdown else None
    uncategorized_count = sum(1 for t in posted if t["category"] == "other")

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
        "status": t["status"],
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

    merchant_agg = defaultdict(lambda: {"total": 0.0, "count": 0, "transactions": []})
    for t in transactions:
        name = t["counterparty"] or t["description"] or "Unknown"
        name = " ".join(name.split()).strip()
        if t["status"] == "posted":
            merchant_agg[name]["total"] += t["amount"]
            merchant_agg[name]["count"] += 1
        merchant_agg[name]["transactions"].append({
            "date": t["date"],
            "description": t["description"],
            "amount": t["amount"],
            "card": t["card_name"],
            "status": t["status"],
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

    card_agg = defaultdict(lambda: {"total": 0.0, "count": 0, "categories": defaultdict(float), "last_four": "", "transactions": []})
    for t in transactions:
        key = t["card_name"]
        if t["status"] == "posted":
            card_agg[key]["total"] += t["amount"]
            card_agg[key]["count"] += 1
            card_agg[key]["categories"][t["category"]] += t["amount"]
        card_agg[key]["last_four"] = t["last_four"]
        card_agg[key]["transactions"].append({
            "date": t["date"],
            "description": t["counterparty"] or t["description"],
            "amount": t["amount"],
            "category": t["category"],
            "status": t["status"],
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
