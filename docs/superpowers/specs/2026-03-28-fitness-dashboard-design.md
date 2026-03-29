# Basin Fitness Dashboard — Design Spec

## Overview

A web-based fitness dashboard served by the existing Basin webhook container at `/dashboard` on the Basin HTTP service. Displays three panels of fitness data from the Basin Postgres database using interactive Plotly.js charts.

## Goals

- Provide quick daily visibility into running, VO2 max, and strength trends.
- Support drill-down analysis for recent progress and long-term trajectory.
- Make data interpretation consistent by defining calculation rules and units.

## Non-Goals

- Authentication changes (Tailscale-only network provides access control).
- Real-time updates / WebSocket (refresh the page for new data).
- Complex caching layer (queries expected to be fast for current data volume).

## Layout

**Summary + Drill-Down pattern:**

- **Top row:** 3 summary cards showing latest stats with sparkline trends
  - Running Pace (blue) — latest pace and speed, sparkline of recent runs
  - VO2 Max (purple) — latest reading, sparkline of trend over time
  - Strength (green) — heaviest qualifying PR, sparkline of training volume
- **Detail panel:** Below the cards, shows the full interactive chart for the selected card
- Clicking a card swaps the detail panel content
- Default: Running Performance expanded on load

## Tech Stack

- **Plotly.js** — interactive charts (loaded from CDN)
- **FastAPI** — JSON API endpoints added to existing webhook server
- **Vanilla HTML/CSS/JS** — no build step, single HTML template served by FastAPI
- **Dark theme** — matches the approved mockup (`#0f172a` background, blue/purple/green accents)

## Data & Calculation Rules

### Date/time semantics

- All API date filtering and chart bucketing are performed in a single dashboard timezone.
- **Dashboard timezone default:** UTC (can be switched later if user-level timezone preference is added).
- Date grouping for daily series uses: `DATE(timestamp AT TIME ZONE 'UTC')`.
- Range semantics are **inclusive** for both start and end dates in API query params.

### Units and display formatting

- Running speed canonical API unit: `mi/hr` (`avg_speed_mph`).
- Running pace display unit: `min/mi` (derived from speed, rounded to `mm:ss`).
- Duration displayed in minutes with 1 decimal place.
- VO2 max displayed as `mL/kg/min` with 1 decimal place.
- Strength weight displayed in `lbs` with whole-number rounding.

### Missing/partial data rules

- If run power is unavailable, return `avg_power: null`.
- If a metric has no data in range, return an empty array and a `summary` object with nullable values.
- API responses are sorted ascending by date unless otherwise specified.

### Strength PR definition

- PR is the maximum `weight_lbs` per exercise across qualifying sets.
- Qualifying sets exclude warmup sets and sets with `reps <= 0`.
- Tie-break order for identical PR weights:
  1) earliest date,
  2) lowest set index.

## API Endpoints

All endpoints added to the existing `webhook/server.py`.

### Common query params

- `start` (ISO date, optional)
- `end` (ISO date, optional)

If omitted:
- `start` defaults to 6 months prior to current date in dashboard timezone.
- `end` defaults to current date in dashboard timezone.

Validation:
- Return HTTP `400` for invalid ISO date format or if `start > end`.

### Common response metadata

Every fitness endpoint returns:

```json
{
  "range_start": "2025-10-01",
  "range_end": "2026-03-29",
  "timezone": "UTC",
  "generated_at": "2026-03-29T18:20:00Z",
  "...": "endpoint-specific fields"
}
```

### Error responses

All 4xx/5xx responses use this shape:

```json
{
  "code": 400,
  "message": "start must be before end",
  "hint": "Provide dates in YYYY-MM-DD format with start <= end"
}
```

### `GET /dashboard`

Serves the HTML dashboard page (single file with embedded CSS/JS).

### `GET /api/fitness/running`

Query params: `start` (ISO date), `end` (ISO date)

Returns JSON:

```json
{
  "range_start": "2025-10-01",
  "range_end": "2026-03-29",
  "timezone": "UTC",
  "generated_at": "2026-03-29T18:20:00Z",
  "runs": [
    {
      "date": "2026-03-25",
      "avg_speed_mph": 5.81,
      "duration_min": 39.2,
      "avg_power": 245
    }
  ],
  "summary": {
    "total_runs": 74,
    "avg_speed_mph": 5.7,
    "latest_speed_mph": 5.81,
    "latest_pace_min_per_mile": "10:20"
  }
}
```

Source:
- `healthkit.metrics` (`metric_type = 'running_speed'`) grouped by date
- `healthkit.workouts` (`workout_type = 'Running'`) for duration
- `healthkit.metrics` (`metric_type = 'running_power'`) for running power

### `GET /api/fitness/vo2max`

Query params: `start` (ISO date), `end` (ISO date)

Returns JSON:

```json
{
  "range_start": "2025-10-01",
  "range_end": "2026-03-29",
  "timezone": "UTC",
  "generated_at": "2026-03-29T18:20:00Z",
  "readings": [
    {"date": "2026-03-25", "vo2max": 46.2}
  ],
  "summary": {
    "latest": 46.2,
    "peak": 51.0,
    "peak_date": "2023-12-08"
  }
}
```

Source: `healthkit.metrics` (`metric_type = 'vo2max'`).

### `GET /api/fitness/strength`

Query params: `start` (ISO date), `end` (ISO date), `exercise` (optional exact-name filter)

Returns JSON:

```json
{
  "range_start": "2025-10-01",
  "range_end": "2026-03-29",
  "timezone": "UTC",
  "generated_at": "2026-03-29T18:20:00Z",
  "exercises": ["Deadlift (Trap bar)", "Incline Bench Press (Dumbbell)"],
  "sets": [
    {
      "date": "2026-03-28",
      "exercise": "Deadlift (Trap bar)",
      "weight_lbs": 210,
      "reps": 7,
      "set_index": 0,
      "set_type": "normal"
    }
  ],
  "prs": [
    {"exercise": "Deadlift (Trap bar)", "max_lbs": 210, "date": "2026-03-28"}
  ]
}
```

Source: `hevy.sets` joined with `hevy.exercises` and `hevy.workouts`.

## Dashboard HTML

Single HTML file served by FastAPI at `/dashboard`. Contains:

- Inline CSS (dark theme matching mockup)
- 3 summary cards with sparklines
- Detail panel area that swaps content on card click
- Plotly.js loaded from CDN (version pinned in implementation; spec does not require a specific patch number)
- Vanilla JS that:
  - Fetches data from `/api/fitness/*` endpoints on load
  - Renders Plotly charts in the detail panel
  - Handles card click to swap active panel
  - Handles time filter buttons (3M/6M/1Y/All) and date picker inputs
  - Re-fetches data when time range changes

## Chart Configurations

### Running Performance (detail panel)

- **Line chart:** average speed (`mi/hr`) per run date, with filled area below
- **Hover:** date, speed, pace, duration, average power
- **X axis:** date, **Y axis:** speed in `mi/hr`
- **Table below chart:** 5 most recent runs (date, pace, speed, duration, avg power)

### VO2 Max (detail panel)

- **Line chart with markers:** VO2 max readings over time
- **Hover:** date, value
- **Horizontal reference line** at peak value (dashed, labeled)
- **X axis:** date, **Y axis:** `mL/kg/min`

### Strength Progression (detail panel)

- **Dropdown** to select exercise (default: exercise with most qualifying sets in range)
- **Scatter + line chart:** weight per set over time, colored by `set_type` (normal vs warmup)
- **Hover:** date, weight, reps, set index, set type
- **X axis:** date, **Y axis:** weight in `lbs`
- **PRs highlighted** with markers

### Additional Workout Views (planned)

- **Consistency view:** workouts per week + rolling 4-week average
- **Training load view:**
  - Running load (duration and/or distance trend)
  - Strength volume (`weight × reps × sets`) trend
- **Exercise balance view:** weekly split by movement category

## Time Range Filters

Visible in the detail panel above the chart:

- Preset buttons: **3M** | **6M** | **1Y** | **All**
- Date picker: start date and end date inputs
- Clicking a preset updates the date pickers and re-fetches data
- Changing date pickers re-fetches data
- Default: 6M

## State Handling

### Loading

- Card and detail panel show loading placeholders while API requests are pending.

### Empty states

- If no data exists in selected range, chart area shows a “No data for selected range” message with a quick action to reset to 6M.

### Error states

- Show inline error banner for failed fetch with retry button.
- Keep prior successfully loaded panel visible when refresh fails.

## Accessibility & Interaction

- All card selections and filter controls are keyboard-accessible.
- Focus states are visible on dark theme controls.
- Color choices must meet WCAG AA contrast where practical.

## Performance Expectations

- Target API latency: p95 < 400ms for default 6M range.
- Server enforces maximum date span of 5 years to avoid pathological queries.
- Ensure indexes exist for key query fields used by running, vo2max, and strength endpoints.
- Verify existing indexes cover the dashboard queries: `idx_healthkit_metrics_type_time`, `idx_collector_runs_collector_time`.
- Run `EXPLAIN ANALYZE` on each endpoint query during verification to confirm index usage.
- For strength queries joining hevy.sets/workouts/exercises, the existing unique indexes are sufficient at current scale.

## Acceptance Criteria

- API p95 latency < 300ms for 1-year range.
- API payload size < 500KB for default 6M range.
- Dashboard initial load < 3 seconds on Tailscale.
- All interactive controls keyboard-accessible.
- WCAG AA contrast on text elements.

## File Changes

- **Modify:** `webhook/server.py` — add `/dashboard`, `/api/fitness/running`, `/api/fitness/vo2max`, `/api/fitness/strength` endpoints
- **Create:** `webhook/dashboard.html` — single-file HTML dashboard with embedded CSS/JS

No new Python dependencies required. Plotly.js loaded from CDN. No Docker or deployment changes required.

## Deployment

Staging verification (smoke tests + browser check) is required before production promotion.
