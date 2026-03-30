# Finance Dashboard — Implementation Plan (Tuned)

## Why this plan update

This revision tightens scope, makes API behavior explicit, and reduces rework risk. It keeps your original direction (simple server-rendered pages + API endpoints) while adding:

- clearer definitions for *what counts as spend*,
- deterministic categorization behavior,
- acceptance criteria per phase,
- test coverage that targets likely bugs (sign handling, date edges, unknown merchants).

---

## Product goal

Ship a Finance dashboard next to Fitness that answers:

1. **How much am I spending over time?**
2. **Where is money going (categories + merchants)?**
3. **How is spend distributed across cards?**

Primary audience: single user, personal use, read-only analytics over Teller credit card transactions.

---

## Scope and non-goals

### In scope (MVP)

- New page at `/dashboard/finance`.
- Navigation between Fitness and Finance.
- Server-side categorization fallback for sparse Teller categories.
- 3 analytics views: monthly/category overview, top merchants, per-card breakdown.
- Date filters (presets + manual range).

### Out of scope (defer)

- Budgeting, goals, alerts, recurring bill detection.
- Editing/relabeling categories in UI.
- Persisting custom categorization overrides in DB.
- Net-worth / asset aggregation (Schwab, cash, etc.).

---

## Current system constraints (from codebase/schema)

- Existing dashboard route is currently `/dashboard` and fitness APIs live under `/api/fitness/*`.
- Teller data exists in `teller.transactions` with fields including: `amount`, `description`, `category`, `date`, `status`, and FK `account_id`.
- Card metadata exists in `teller.accounts` (`name`, `last_four`, `subtype`, etc.).

Implication: we can build finance analytics with SQL + lightweight Python transforms and no migration.

---

## Critical data definitions (lock these early)

These decisions prevent 80% of dashboard inconsistencies:

1. **Spend sign rule**
   - Treat spend as `abs(amount)` only when transaction represents money outflow on card.
   - Exclude or separately classify positive/refund-like rows (e.g., credits, reversals) for MVP simplicity.
   - Add a follow-up metric later for “refunds”.

2. **Status filter**
   - Include only posted/settled transactions.
   - Exclude pending to avoid chart “jumping” day-to-day.

3. **Date semantics**
   - Use `teller.transactions.date` as canonical transaction date (not created_at).

4. **Category precedence**
   1) keyword override (merchant-based),
   2) Teller category when present and not generic,
   3) `"other"`.

5. **Merchant normalization**
   - Normalize source text via lowercase + strip punctuation + collapse spaces before matching.

---

## Proposed architecture

- Keep **two HTML pages** (Fitness and Finance), shared nav; no SPA conversion.
- Create `webhook/finance.py` for finance router and API endpoints.
- Extract reusable helpers into `webhook/dashboard_shared.py`:
  - `MAX_DATE_SPAN_DAYS`
  - `_parse_date_range`
  - `_response_metadata`
- Mount both routers in `webhook/server.py`.

This is consistent with current project structure and minimizes blast radius.

---

## Endpoint contract (tightened)

### `GET /dashboard/finance`
Returns finance dashboard HTML.

### `GET /api/finance/overview?start=YYYY-MM-DD&end=YYYY-MM-DD`
Returns:

- `monthly_spend`: month bucket + total
- `category_breakdown`: category totals + counts
- `summary`: total_spend, avg_monthly, biggest_category, biggest_category_amount, transaction_count
- standard metadata fields

### `GET /api/finance/merchants?...`
Returns top merchants by spend in date range:

- `name`, `total_spend`, `transaction_count`, `avg_transaction`

### `GET /api/finance/cards?...`
Returns per-card rollup:

- `name`, `last_four`, `total_spend`, `transaction_count`, `top_category`, `top_category_amount`

### Error model
Reuse existing dashboard error format for invalid dates/ranges to keep API behavior consistent.

---

## Categorization strategy (improved)

Use your keyword map, but make matching deterministic and testable:

1. Precompute ordered rules by descending keyword length.
2. Normalize merchant/description string once.
3. First matching keyword wins.
4. If none match, use Teller category unless `NULL`, empty, `"general"`, `"uncategorized"`.
5. Else `"other"`.

### Suggested starter taxonomy

- groceries
- dining
- transportation
- shopping
- travel
- subscriptions
- health
- fuel
- utilities
- other

Keep category strings stable (used by charts + tests).

---

## Files to create/modify

| File | Action | Notes |
|---|---|---|
| `webhook/dashboard_shared.py` | Create | shared date parsing + metadata helpers |
| `webhook/finance.py` | Create | finance routes, SQL aggregations, categorization |
| `webhook/finance.html` | Create | finance dashboard UI |
| `webhook/dashboard.py` | Modify | move helper imports; route becomes `/dashboard/fitness` |
| `webhook/dashboard.html` | Modify | add Fitness/Finance top nav |
| `webhook/server.py` | Modify | include finance router + `/dashboard` redirect |
| `tests/conftest.py` | Modify | monkeypatch `webhook.finance.get_session` |
| `tests/test_dashboard.py` | Modify | route assertions for `/dashboard/fitness` and redirect |
| `tests/test_finance.py` | Create | categorization + endpoint behavior tests |

---

## Implementation phases with acceptance criteria

### Phase 1 — Shared helpers extraction

**Work**
- Create `dashboard_shared.py`.
- Update fitness module imports.

**Accept when**
- Fitness tests pass unchanged.
- API response metadata is byte-for-byte equivalent for fitness endpoints.

### Phase 2 — Routing and navigation

**Work**
- Move fitness HTML route to `/dashboard/fitness`.
- Add `/dashboard` redirect to `/dashboard/fitness`.
- Add top nav to fitness page.

**Accept when**
- `/dashboard` returns redirect.
- `/dashboard/fitness` serves existing fitness UI.

### Phase 3 — Finance backend

**Work**
- Build finance router with 3 API endpoints + `/dashboard/finance`.
- Implement shared transaction fetch/filter utility.
- Implement categorization function and merchant normalization helper.

**Accept when**
- Each endpoint returns valid JSON shape on seeded data.
- Empty-range queries return 200 with empty lists + zeroed summaries.

### Phase 4 — Finance frontend

**Work**
- Implement finance page layout + nav (Finance active).
- Add summary cards, tabs, charts, and table.
- Wire date filters to endpoint query params.

**Accept when**
- All three views render from live API data.
- Preset ranges and manual date range both update all visible widgets.

### Phase 5 — Tests and polish

**Work**
- Add unit tests for categorization precedence/normalization.
- Add API tests for overview, merchants, cards, date validation, empty ranges.

**Accept when**
- New finance tests pass.
- Existing dashboard tests still pass.

---

## Test plan (recommended specifics)

1. **Categorization**
   - `"UBER EATS"` => dining (beats `"uber"` transportation due to longer match).
   - punctuation/case normalization (e.g., `"APPLE.COM/BILL"`).
   - Teller category fallback when merchant unmatched.
   - `"general"` + unmatched => `"other"`.

2. **Spend math**
   - verify refunds/positive amounts are excluded (or consistently handled per chosen rule).
   - verify totals match sum of returned rows.

3. **API contracts**
   - response contains metadata keys.
   - numeric fields are numbers, not strings.
   - merchants sorted desc by spend.

4. **Routing**
   - `/dashboard` redirect behavior.
   - both `/dashboard/fitness` and `/dashboard/finance` return HTML.

---

## Risks and mitigations

- **Risk:** misinterpreting amount signs inflates/deflates spend.  
  **Mitigation:** lock sign rule + add dedicated tests.

- **Risk:** keyword map overfits and mislabels major merchants.  
  **Mitigation:** include `uncategorized_count` metric in overview for visibility.

- **Risk:** SQL duplication across endpoints causes drift.  
  **Mitigation:** shared `_fetch_transactions()` and shared filter builder.

---

## Rollout checklist

1. Run tests:
   - `pytest tests/test_dashboard.py tests/test_finance.py -v`
2. Manual verify:
   - `/dashboard` redirects to `/dashboard/fitness`
   - nav switches pages
   - finance filters and tabs work
3. Spot-check 10 known transactions against category assignment.

---

## Notes for future V2 (optional)

- Persist per-merchant category overrides table.
- Add month-over-month deltas and trend arrows.
- Add transaction drill-down table with search.
- Add CSV export for filtered transactions.
