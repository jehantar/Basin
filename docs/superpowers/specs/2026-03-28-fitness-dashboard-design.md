# Basin Fitness Dashboard — Design Spec

## Overview

A web-based fitness dashboard served by the existing Basin webhook container, accessible over Tailscale at `http://100.125.126.42:8075/dashboard`. Displays three panels of fitness data from the Basin Postgres database using interactive Plotly.js charts.

## Layout

**Summary + Drill-Down pattern:**

- **Top row:** 3 summary cards showing latest stats with sparkline trends
  - Running Pace (blue) — latest avg speed in mi/hr, sparkline of recent runs
  - VO2 Max (purple) — latest reading, sparkline of trend over time
  - Strength (green) — heaviest lift PR, sparkline of training volume
- **Detail panel:** Below the cards, shows the full interactive chart for the selected card
- Clicking a card swaps the detail panel content
- Default: Running Performance expanded on load

## Tech Stack

- **Plotly.js** — interactive charts (loaded from CDN)
- **FastAPI** — JSON API endpoints added to existing webhook server
- **Vanilla HTML/CSS/JS** — no build step, single HTML template served by FastAPI
- **Dark theme** — matches the approved mockup (#0f172a background, blue/purple/green accents)

## API Endpoints

All endpoints added to the existing `webhook/server.py`.

### `GET /dashboard`
Serves the HTML dashboard page (single file with embedded CSS/JS).

### `GET /api/fitness/running`
Query params: `start` (ISO date), `end` (ISO date)

Returns JSON:
```json
{
  "runs": [
    {"date": "2026-03-25", "avg_speed": 5.81, "duration_min": 39.2, "avg_power": 245}
  ],
  "summary": {
    "total_runs": 74,
    "avg_speed": 5.7,
    "latest_speed": 5.81
  }
}
```

Source: `healthkit.metrics` (metric_type = 'running_speed'), grouped by date. Duration from `healthkit.workouts` (workout_type = 'Running'). Running power from `healthkit.metrics` (metric_type = 'running_power').

### `GET /api/fitness/vo2max`
Query params: `start` (ISO date), `end` (ISO date)

Returns JSON:
```json
{
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

Source: `healthkit.metrics` (metric_type = 'vo2max').

### `GET /api/fitness/strength`
Query params: `start` (ISO date), `end` (ISO date), `exercise` (optional, filter by name)

Returns JSON:
```json
{
  "exercises": ["Deadlift (Trap bar)", "Incline Bench Press (Dumbbell)", ...],
  "sets": [
    {"date": "2026-03-28", "exercise": "Deadlift (Trap bar)", "weight_lbs": 210, "reps": 7, "set_index": 0}
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
- 3 summary cards with sparklines (rendered via small Plotly spark charts or CSS bars)
- Detail panel area that swaps content on card click
- Plotly.js loaded from CDN (`https://cdn.plot.ly/plotly-2.35.2.min.js`)
- Vanilla JS that:
  - Fetches data from `/api/fitness/*` endpoints on load
  - Renders Plotly charts in the detail panel
  - Handles card click to swap active panel
  - Handles time filter buttons (3M/6M/1Y/All) and date picker inputs
  - Re-fetches data when time range changes

## Chart Configurations

### Running Performance (detail panel)
- **Line chart:** avg speed (mi/hr) per run date, with filled area below
- **Hover:** date, speed, duration
- **X axis:** date, **Y axis:** speed in mi/hr
- **Table below chart:** 5 most recent runs (date, pace, duration, avg power)

### VO2 Max (detail panel)
- **Line chart with markers:** VO2 max readings over time
- **Hover:** date, value
- **Horizontal reference line** at peak value (dashed, labeled)
- **X axis:** date, **Y axis:** mL/kg/min

### Weight Progression (detail panel)
- **Dropdown** to select exercise (default: exercise with most sets)
- **Scatter + line chart:** weight per set over time, colored by set_type (normal vs warmup)
- **Hover:** date, weight, reps, set index
- **X axis:** date, **Y axis:** weight in lbs
- **PRs highlighted** with markers

## Time Range Filters

Visible in the detail panel above the chart:
- Preset buttons: **3M** | **6M** | **1Y** | **All**
- Date picker: start date and end date inputs
- Clicking a preset updates the date pickers and re-fetches data
- Changing date pickers re-fetches data
- Default: 6M

## File Changes

- **Modify:** `webhook/server.py` — add `/dashboard`, `/api/fitness/running`, `/api/fitness/vo2max`, `/api/fitness/strength` endpoints
- **Create:** `webhook/dashboard.html` — single-file HTML dashboard with embedded CSS/JS

No new dependencies. Plotly.js loaded from CDN. No changes to Docker or deployment — the webhook container already serves HTTP.

## Out of Scope

- Authentication (Tailscale-only network provides access control)
- Mobile-specific responsive layout (desktop-first, readable on phone but not optimized)
- Real-time updates / WebSocket (refresh the page for new data)
- Caching (queries are fast enough against this data volume)
