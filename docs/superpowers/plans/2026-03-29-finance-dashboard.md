# Finance Dashboard — Implementation Plan

## Context

Basin has a fitness dashboard at `/dashboard` with running, VO2 max, and strength panels. The user wants to add a Finance page alongside it, with top-level navigation (Fitness | Finance tabs). The finance page will visualize spending data from 4 Chase credit cards (998 transactions) collected via Teller.

Key challenge: Teller's transaction categories are sparse — 420 txns tagged "general" and 146 uncategorized. Solution: server-side auto-categorization using merchant name keyword matching.

## Approach

- **Two separate HTML files** with a shared top nav bar (not SPA)
- **New Python module** `webhook/finance.py` for finance APIs
- **Extract shared utilities** (`_parse_date_range`, `_response_metadata`) to avoid duplication
- **Auto-categorize** via keyword-to-category mapping applied at query time (no DB changes)
- **Three finance views:** Monthly Spend + Categories, Top Merchants, Per-Card Breakdown

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `webhook/dashboard_shared.py` | Create | Extract `_parse_date_range`, `_response_metadata` |
| `webhook/finance.py` | Create | Finance API router + categorization logic |
| `webhook/finance.html` | Create | Finance dashboard page |
| `webhook/dashboard.py` | Modify | Import from shared, rename route to `/dashboard/fitness` |
| `webhook/dashboard.html` | Modify | Add top nav bar (Fitness/Finance tabs) |
| `webhook/server.py` | Modify | Mount finance router, add `/dashboard` redirect |
| `tests/conftest.py` | Modify | Patch `webhook.finance.get_session` |
| `tests/test_dashboard.py` | Modify | Update `/dashboard` → `/dashboard/fitness` in tests |
| `tests/test_finance.py` | Create | Finance API tests |

## Tasks

### Task 1: Extract shared utilities
- Create `webhook/dashboard_shared.py` with `_parse_date_range`, `_response_metadata`, `MAX_DATE_SPAN_DAYS`
- Update `webhook/dashboard.py` to import from it
- Verify existing fitness tests still pass

### Task 2: Routing + navigation
- `webhook/dashboard.py`: change route from `/dashboard` to `/dashboard/fitness`
- `webhook/server.py`: add `/dashboard` → `/dashboard/fitness` redirect, mount finance router
- `webhook/dashboard.html`: add top nav with Fitness (active) / Finance tabs
- Update test for new URL

### Task 3: Finance API endpoints (`webhook/finance.py`)
- `MERCHANT_CATEGORIES` keyword dict + `categorize_transaction()` function
- `GET /dashboard/finance` — serve finance.html
- `GET /api/finance/overview` — monthly spend trend + category breakdown + summary
- `GET /api/finance/merchants` — top 25 merchants by spend
- `GET /api/finance/cards` — per-card spending breakdown
- Shared `_fetch_transactions()` to avoid SQL duplication across endpoints

### Task 4: Finance HTML (`webhook/finance.html`)
- Top nav (Finance active)
- 3 summary cards: Total Spend, Biggest Category, Transaction Count
- View tabs: Spending | Merchants | Cards
- Time filters (3M/6M/1Y/All + date pickers)
- Spending panel: monthly bar chart + category donut (side-by-side)
- Merchants panel: ranked table of top 25 merchants
- Cards panel: 2x2 grid of card blocks with spend/count/top category
- Same dark theme, Plotly.js, textContent/createElement patterns

### Task 5: Tests
- Update `tests/conftest.py` to patch `webhook.finance.get_session`
- Update `tests/test_dashboard.py` for new URL
- Create `tests/test_finance.py` with categorization tests, API response shape tests, empty range tests

### Task 6: Deploy and verify

## Categorization Mapping

```python
MERCHANT_CATEGORIES = {
    "trader joe": "groceries", "whole foods": "groceries", "safeway": "groceries", "costco": "groceries",
    "doordash": "dining", "uber eats": "dining", "grubhub": "dining",
    "uber": "transportation", "lyft": "transportation",
    "amazon": "shopping", "target": "shopping", "walmart": "shopping",
    "airbnb": "travel", "hotel": "travel",
    "netflix": "subscriptions", "spotify": "subscriptions", "hulu": "subscriptions", "apple.com/bill": "subscriptions",
    "gym": "health", "fitness": "health",
    "shell": "fuel", "chevron": "fuel",
    "comcast": "utilities", "verizon": "utilities", "at&t": "utilities",
}
```

Match order: longer keywords first (so "uber eats" matches before "uber"). Fallback: Teller category if not "general"/null, else "other".

## API Response Shapes

### GET /api/finance/overview
```json
{
  "range_start": "...", "range_end": "...", "timezone": "UTC", "generated_at": "...",
  "monthly_spend": [{"month": "2026-03", "total": 3903.23}],
  "category_breakdown": [{"category": "groceries", "total": 567.89, "count": 23}],
  "summary": {"total_spend": 45000, "avg_monthly": 3750, "biggest_category": "groceries", "biggest_category_amount": 11000}
}
```

### GET /api/finance/merchants
```json
{
  "..metadata..",
  "merchants": [{"name": "DOORDASH", "total_spend": 2340.50, "transaction_count": 45, "avg_transaction": 52.01}]
}
```

### GET /api/finance/cards
```json
{
  "..metadata..",
  "cards": [{"name": "Freedom Unlimited", "last_four": "2712", "total_spend": 59991.18, "transaction_count": 860, "top_category": "general", "top_category_amount": 43108.93}]
}
```

## Task Sequencing

```
Task 1 (shared utils)
  ├→ Task 2 (routing + nav)  ─┐
  └→ Task 3 (finance API)    ─┼→ Task 4 (finance HTML) → Task 6 (deploy)
                               └→ Task 5 (tests)
```

Tasks 2+3 can run in parallel after Task 1. Tasks 4+5 can run in parallel after 2+3.

## Verification

1. `pytest tests/test_dashboard.py tests/test_finance.py -v` — all pass
2. `/dashboard` redirects to `/dashboard/fitness`
3. `/dashboard/fitness` shows fitness page with nav bar, existing functionality unchanged
4. `/dashboard/finance` shows finance page with spending chart, category donut, merchants table, card breakdown
5. Nav tabs switch between pages
6. Time filters work on finance page
7. Auto-categorization properly maps merchants
