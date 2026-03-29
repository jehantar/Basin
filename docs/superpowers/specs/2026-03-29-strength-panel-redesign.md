# Strength Panel Redesign — Design Spec

## Overview

Replace the current strength panel (exercise dropdown + weight scatter plot) with a progressive overload tracking view that groups workouts by type and shows volume trends with expandable session logs.

## Problem

The current panel shows weight per set on a scatter plot with a per-exercise dropdown. This is unhelpful because:
- Weight alone doesn't show progression (sets and reps matter)
- Mixing different workout types in one view makes comparison meaningless
- A dropdown across 34 exercises is tedious to navigate

## Goals

- Make progression easier to interpret at a workout-program level.
- Preserve backward compatibility for existing strength consumers during migration.
- Keep client interactions responsive while avoiding unbounded payload growth.

## Non-Goals

- Replacing running/VO2 panels.
- Adding advanced periodization analytics (e.g., 1RM estimation, fatigue scores) in this iteration.

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

- Tabs are derived from `hevy.workouts.title` grouped and counted.
- Ordered by deterministic sort:
  1. frequency descending (most sessions first)
  2. most recent session date descending
  3. title ascending (case-insensitive)
- Default: first tab in this order.
- Switching tabs re-renders the chart and log without a new API call (data is pre-fetched for all types and filtered client-side).

### Volume Calculation

- Session volume = sum of (`weight_lbs x reps`) for all qualifying sets in a workout.
- Qualifying sets: `set_type != 'warmup'` AND `weight_lbs IS NOT NULL` AND `reps > 0`.
- Bodyweight exercises (`weight_lbs IS NULL`) are excluded from volume totals but shown in the log.
- Per-exercise `volume_lbs` uses the same qualifying-set rules.

### Trend Cards

- **Volume trend %**: `(latest_volume - first_volume) / first_volume * 100`.
- Guardrails:
  - If fewer than 2 sessions, show `---`.
  - If `first_volume <= 0`, show `---` (avoid divide-by-zero and misleading infinities).
- Positive trends shown as `+X%` (green), negative trends as `-X%` (red).
- **Peak volume**: max session volume across all sessions in range for the selected workout type. Show value and date.
- **Session count**: count of sessions for the selected workout type in range.

### Session-Over-Session Change

- Each bar in the chart (except the first) shows a % change label below it comparing to the previous session of the same workout type.
- Positive changes in green, negative in red.
- If previous session volume is `0` or null, display `---` for that delta.

### Timezone and Date Semantics

- All workout date bucketing and labels are based on UTC dates, consistent with existing dashboard fitness endpoints.
- Response metadata continues to include `timezone: "UTC"`.

### Accessibility Requirements

- Workout rows are keyboard-operable controls (`button` semantics or equivalent).
- Expanded/collapsed state is exposed via `aria-expanded` and `aria-controls`.
- Enter/Space toggles expansion.
- Tab controls use appropriate selected-state semantics.

### Empty and Error States

- No sessions in range: show empty chart/log state and guidance to widen date range.
- Single session: show session metrics; trend fields that require comparison display `---`.
- All-zero-volume sessions: show bars at zero and trend deltas as `---` where applicable.
- Unknown/invalid title filter: return empty data (200) unless validation mode is explicitly added later.

## API Changes

### Modify `GET /api/fitness/strength`

Query params:
- `title` (optional, exact match on `hevy.workouts.title`).
- `exercise` remains supported for backward compatibility.

Filter behavior:
- If both `title` and `exercise` are provided, apply **AND** semantics.
- If either filter has no matches, return an empty result set with 200.

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

Compatibility contract:
- Existing `exercises`, `sets`, and `prs` fields remain in the response during migration.
- New strength panel consumes `workouts` + `workout_titles`.
- Legacy fields are marked deprecated in code comments/docs after rollout, then removed in a follow-up release once no consumers remain.

### Workout titles

`workout_titles` is the distinct list of titles ordered by the deterministic tab sort defined above.

### Performance / payload constraints

- Default behavior should avoid unbounded response sizes.
- Server should support one or both of:
  - hard maximum sessions returned (e.g., latest N workouts within range), and/or
  - pagination/cursor for large result sets.
- Client can still pre-fetch all returned types and switch tabs client-side.

### Query strategy

Implement data retrieval with set-based SQL aggregation and deterministic ordering to avoid N+1 query patterns when constructing nested `workouts[].exercises[].sets[]`.

## HTML/JS Changes

Replace the strength panel contents in `webhook/dashboard.html`:
- Remove: exercise dropdown, scatter chart.
- Add: workout type tabs, trend cards, Plotly volume bar chart, expandable workout log.
- All data comes from `/api/fitness/strength` (using the new `workouts` field).
- Tab switching is client-side only (filter pre-fetched workouts array by title).
- Workout row expansion uses CSS max-height transition (same pattern as the mockup).
- Use `textContent` and `createElement` for all dynamic content (no `innerHTML` with user data).
- Use Plotly `customdata` for hover metadata (volume, set count, session delta).

## Strength Summary Card

Update the top-level strength card to show:
- **Value**: total volume of the latest session (any type).
- **Subtitle**: workout title and date.
- **Trend**: session count in range.
- **Sparkline**: volume per session across all types.

## Test Plan

Update `tests/test_dashboard.py` with explicit coverage for:
- `workout_titles` deterministic ordering + tie-breakers.
- Volume inclusion/exclusion rules (warmups, null weight/bodyweight, reps <= 0).
- Trend edge cases (0 sessions, 1 session, first volume = 0).
- `title` filter behavior and combined `title+exercise` filtering.
- Backward compatibility fields (`exercises`, `sets`, `prs`) still present.
- Empty results for unmatched filters.

## File Changes

- **Modify**: `webhook/dashboard.py` — update strength endpoint to include `workouts` and `workout_titles`, add `title` filtering, preserve compatibility fields.
- **Modify**: `webhook/dashboard.html` — replace strength panel.
- **Modify**: `tests/test_dashboard.py` — extend tests for response shape, sorting, edge cases, and compatibility.
