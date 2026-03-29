# Strength Panel Redesign — Design Spec

## Overview

Replace the current strength panel (exercise dropdown + weight scatter plot) with a progressive overload tracking view that groups workouts by type and shows volume trends with expandable session logs.

## Problem

The current panel shows weight per set on a scatter plot with a per-exercise dropdown. This is unhelpful because:
- Weight alone doesn't show progression (sets and reps matter)
- Mixing different workout types in one view makes comparison meaningless
- A dropdown across 34 exercises is tedious to navigate

## Design

### Layout

1. **Workout type tabs** — one tab per distinct workout title (e.g., Lower A, Upper A, Lower B, Upper B). Derived dynamically from the data. Selecting a tab filters everything below to that workout type only.

2. **Trend summary cards** — three inline cards:
   - Volume trend: % change from first to latest session in range
   - Peak volume: highest session volume and its date
   - Session count: number of sessions in range

3. **Volume bar chart** — Plotly bar chart showing total volume (weight x reps, excluding warmups) per session. Bars are proportional to actual values. Hover shows volume, set count, and session-over-session % change. Peak session highlighted.

4. **Expandable workout log** — scrollable list of workout rows (most recent first). Each row shows: date, exercise count, set count, total volume. Clicking a row expands it to show every exercise with its sets (set number, weight, reps) and per-exercise volume subtotal. Bodyweight exercises show "BW" instead of a weight.

### Workout Type Tabs

- Tabs are derived from `hevy.workouts.title` grouped and counted
- Ordered by frequency (most common first)
- Default: first tab (most frequent workout type)
- Switching tabs re-renders the chart and log without a new API call (data is pre-fetched for all types, filtered client-side)

### Volume Calculation

- Volume = sum of (weight_lbs x reps) for all qualifying sets in a workout
- Qualifying sets: `set_type != 'warmup'` AND `weight_lbs IS NOT NULL` AND `reps > 0`
- Bodyweight exercises (weight_lbs IS NULL) are excluded from volume totals but shown in the log

### Trend Cards

- **Volume trend %**: `(latest_volume - first_volume) / first_volume * 100`. Show as +X% (green) or -X% (red).
- **Peak volume**: max session volume across all sessions in range for the selected workout type. Show value and date.
- **Session count**: count of sessions for the selected workout type in range.
- If fewer than 2 sessions, volume trend shows "---" instead of a percentage.

### Session-Over-Session Change

Each bar in the chart (except the first) shows a % change label below it comparing to the previous session of the same workout type. Positive changes in green, negative in red.

## API Changes

### Modify `GET /api/fitness/strength`

Add a new query param: `title` (optional, exact match on `hevy.workouts.title`).

Add to the response:
```json
{
  "workout_titles": ["Lower A", "Upper A", "Lower B", "Upper B"],
  "workouts": [
    {
      "date": "2026-03-28",
      "title": "Lower A",
      "exercise_count": 6,
      "set_count": 16,
      "volume_lbs": 15150,
      "exercises": [
        {
          "name": "Deadlift (Trap bar)",
          "volume_lbs": 5320,
          "sets": [
            {"set_index": 0, "weight_lbs": 210, "reps": 7, "set_type": "normal"},
            {"set_index": 1, "weight_lbs": 210, "reps": 5, "set_type": "normal"}
          ]
        }
      ]
    }
  ]
}
```

The existing `exercises`, `sets`, and `prs` fields remain for backward compatibility but the new `workouts` field is what the redesigned panel uses.

### Workout titles

`workout_titles` is the distinct list of titles ordered by frequency (most sessions first). This determines the tab order.

## HTML/JS Changes

Replace the strength panel contents in `webhook/dashboard.html`:
- Remove: exercise dropdown, scatter chart
- Add: workout type tabs, trend cards, Plotly volume bar chart, expandable workout log
- All data comes from the existing `/api/fitness/strength` endpoint (with the new `workouts` field)
- Tab switching is client-side only (filter the pre-fetched workouts array by title)
- Workout row expansion uses CSS max-height transition (same pattern as the mockup)
- Use `textContent` and `createElement` for all dynamic content (no innerHTML with user data)

## Strength Summary Card

Update the top-level strength card to show:
- **Value**: total volume of the latest session (any type)
- **Subtitle**: workout title and date
- **Trend**: session count in range
- **Sparkline**: volume per session across all types

## File Changes

- **Modify**: `webhook/dashboard.py` — update strength endpoint to include `workouts` and `workout_titles`
- **Modify**: `webhook/dashboard.html` — replace strength panel
- **Modify**: `tests/test_dashboard.py` — update strength tests for new response shape
