# Strength Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the strength panel's exercise dropdown + weight scatter with workout-type tabs, volume progression bars, and an expandable workout log showing progressive overload.

**Architecture:** Extend the existing `/api/fitness/strength` endpoint to return a new `workouts` field with nested exercise/set data and a `workout_titles` field for tabs. Keep legacy fields for backward compatibility. Replace the HTML strength panel with tabs, trend cards, Plotly volume bars, and a DOM-built expandable log. All tab switching is client-side (no extra API calls).

**Tech Stack:** FastAPI, SQLAlchemy (raw SQL), Plotly.js, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-29-strength-panel-redesign.md`

---

## File Structure

```
webhook/
  dashboard.py        # Modify: extend strength endpoint with workouts/workout_titles
  dashboard.html      # Modify: replace strength panel HTML/JS
tests/
  test_dashboard.py   # Modify: add new strength tests, keep existing ones passing
```

---

## Task 1: Extend Strength API with Workouts and Titles

**Files:**
- Modify: `webhook/dashboard.py`
- Modify: `tests/test_dashboard.py`

Add `workout_titles` and `workouts` to the strength endpoint response. Use set-based SQL to avoid N+1 queries. Add `title` query param. Keep existing `exercises`, `sets`, `prs` fields intact.

- [ ] **Step 1: Update test seed data to use realistic workout titles**

Replace the existing `_seed_strength_data` function in `tests/test_dashboard.py` with one that uses proper workout titles (Lower A, Upper A) so we can test tab sorting and title filtering:

```python
def _seed_strength_data(session):
    """Insert sample strength workout data with realistic titles."""
    session.execute(text(
        "INSERT INTO hevy.exercises (name) VALUES ('Bench Press'), ('Squat'), ('Deadlift') ON CONFLICT DO NOTHING"
    ))
    bench_id = session.execute(text("SELECT id FROM hevy.exercises WHERE name = 'Bench Press'")).scalar()
    squat_id = session.execute(text("SELECT id FROM hevy.exercises WHERE name = 'Squat'")).scalar()
    deadlift_id = session.execute(text("SELECT id FROM hevy.exercises WHERE name = 'Deadlift'")).scalar()

    # 3 Lower A sessions, 2 Upper A sessions (Lower A is most frequent)
    workouts = [
        ("Lower A", "2026-01-10T10:00:00"),
        ("Upper A", "2026-01-12T10:00:00"),
        ("Lower A", "2026-02-15T10:00:00"),
        ("Upper A", "2026-02-17T10:00:00"),
        ("Lower A", "2026-03-20T10:00:00"),
    ]
    for title, st in workouts:
        session.execute(text("""
            INSERT INTO hevy.workouts (title, started_at, ended_at, duration_sec)
            VALUES (:title, :st, :et, 3600)
            ON CONFLICT (started_at) DO NOTHING
        """), {"title": title, "st": st, "et": st.replace("10:00", "11:00")})

    w_ids = [r[0] for r in session.execute(
        text("SELECT id FROM hevy.workouts ORDER BY started_at")
    ).fetchall()]

    # Lower A sets: squat + deadlift
    sets_data = [
        # Jan 10 Lower A: squat 185x8, deadlift 135x10
        (w_ids[0], squat_id, 0, "normal", 185, 8),
        (w_ids[0], deadlift_id, 0, "normal", 135, 10),
        # Jan 12 Upper A: bench 135x8, bench warmup 95x10
        (w_ids[1], bench_id, 0, "normal", 135, 8),
        (w_ids[1], bench_id, 1, "warmup", 95, 10),
        # Feb 15 Lower A: squat 195x8, deadlift 145x10
        (w_ids[2], squat_id, 0, "normal", 195, 8),
        (w_ids[2], deadlift_id, 0, "normal", 145, 10),
        # Feb 17 Upper A: bench 155x6
        (w_ids[3], bench_id, 0, "normal", 155, 6),
        # Mar 20 Lower A: squat 205x5, deadlift 155x8, bodyweight V-up
        (w_ids[4], squat_id, 0, "normal", 205, 5),
        (w_ids[4], deadlift_id, 0, "normal", 155, 8),
    ]
    for w_id, ex_id, idx, stype, weight, reps in sets_data:
        session.execute(text("""
            INSERT INTO hevy.sets (workout_id, exercise_id, set_index, set_type, weight_lbs, reps)
            VALUES (:w, :e, :i, :st, :wt, :r)
            ON CONFLICT (workout_id, exercise_id, set_index) DO NOTHING
        """), {"w": w_id, "e": ex_id, "i": idx, "st": stype, "wt": weight if weight else None, "r": reps})
```

- [ ] **Step 2: Add new tests for workouts and workout_titles**

Append these tests to `tests/test_dashboard.py`:

```python
def test_strength_workout_titles_ordering(session, client):
    """workout_titles sorted by frequency desc, then recent date desc, then title asc."""
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    assert "workout_titles" in data
    # Lower A has 3 sessions, Upper A has 2 -> Lower A first
    assert data["workout_titles"][0] == "Lower A"
    assert data["workout_titles"][1] == "Upper A"


def test_strength_workouts_structure(session, client):
    """workouts field has nested exercises and sets."""
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    assert "workouts" in data
    assert len(data["workouts"]) == 5
    w = data["workouts"][0]  # oldest first
    assert "date" in w
    assert "title" in w
    assert "exercise_count" in w
    assert "set_count" in w
    assert "volume_lbs" in w
    assert "exercises" in w
    assert len(w["exercises"]) > 0
    ex = w["exercises"][0]
    assert "name" in ex
    assert "volume_lbs" in ex
    assert "sets" in ex


def test_strength_volume_excludes_warmup(session, client):
    """Volume calculation excludes warmup sets."""
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    # Find the Upper A on Jan 12 which has bench 135x8 normal + 95x10 warmup
    upper_jan = next(w for w in data["workouts"] if w["title"] == "Upper A" and w["date"] == "2026-01-12")
    # Volume should be 135*8 = 1080 (warmup excluded)
    assert upper_jan["volume_lbs"] == 1080


def test_strength_title_filter(session, client):
    """title param filters workouts to that type only."""
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31&title=Lower+A")
    data = resp.json()
    assert all(w["title"] == "Lower A" for w in data["workouts"])
    assert len(data["workouts"]) == 3


def test_strength_title_and_exercise_filter(session, client):
    """title + exercise applies AND semantics."""
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31&title=Lower+A&exercise=Squat")
    data = resp.json()
    for w in data["workouts"]:
        assert w["title"] == "Lower A"
        for ex in w["exercises"]:
            assert ex["name"] == "Squat"


def test_strength_backward_compat(session, client):
    """Legacy fields (exercises, sets, prs) still present."""
    _seed_strength_data(session)
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31")
    data = resp.json()
    assert "exercises" in data
    assert "sets" in data
    assert "prs" in data
    assert len(data["exercises"]) > 0


def test_strength_empty_title_filter(client):
    """Unmatched title returns empty workouts with 200."""
    resp = client.get("/api/fitness/strength?start=2026-01-01&end=2026-12-31&title=Nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workouts"] == []
```

- [ ] **Step 3: Implement the extended strength endpoint**

In `webhook/dashboard.py`, replace the `get_strength_data` function. The new version adds `title` param, a set-based query for `workouts` with nested `exercises[].sets[]`, and `workout_titles`. It keeps the existing `exercises`, `sets`, `prs` fields.

```python
@router.get("/api/fitness/strength")
def get_strength_data(
    start: str | None = None,
    end: str | None = None,
    exercise: str | None = None,
    title: str | None = None,
):
    start_date, end_date = _parse_date_range(start, end)

    with get_session() as session:
        # Build filters
        filters = ""
        params: dict = {"start": start_date, "end": end_date}
        if exercise:
            filters += " AND e.name = :exercise"
            params["exercise"] = exercise
        if title:
            filters += " AND w.title = :title"
            params["title"] = title

        # --- Workout titles (deterministic sort: frequency desc, latest date desc, title asc) ---
        title_rows = session.execute(text("""
            SELECT w.title, count(*) as cnt, max((w.started_at AT TIME ZONE 'UTC')::date) as latest
            FROM hevy.workouts w
            WHERE (w.started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
            GROUP BY w.title
            ORDER BY cnt DESC, latest DESC, lower(w.title) ASC
        """), {"start": start_date, "end": end_date}).fetchall()
        workout_titles = [r[0] for r in title_rows]

        # --- Workouts with nested exercises and sets (set-based, no N+1) ---
        all_rows = session.execute(text(f"""
            SELECT w.id as workout_id,
                   (w.started_at AT TIME ZONE 'UTC')::date as date,
                   w.title,
                   e.name as exercise_name,
                   s.set_index,
                   round(s.weight_lbs::numeric, 0) as weight_lbs,
                   s.reps,
                   s.set_type
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE (w.started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
              {filters}
            ORDER BY w.started_at, e.name, s.set_index
        """), params).fetchall()

        # Group into workouts -> exercises -> sets
        from collections import OrderedDict
        workout_map = OrderedDict()
        for r in all_rows:
            w_id = r[0]
            if w_id not in workout_map:
                workout_map[w_id] = {
                    "date": str(r[1]),
                    "title": r[2],
                    "exercises": OrderedDict(),
                    "_sets_total": 0,
                }
            wk = workout_map[w_id]
            ex_name = r[3]
            if ex_name not in wk["exercises"]:
                wk["exercises"][ex_name] = {"name": ex_name, "sets": [], "volume_lbs": 0}
            ex = wk["exercises"][ex_name]
            weight = int(r[5]) if r[5] is not None else None
            reps = r[6]
            set_type = r[7]
            ex["sets"].append({
                "set_index": r[4],
                "weight_lbs": weight,
                "reps": reps,
                "set_type": set_type,
            })
            wk["_sets_total"] += 1
            # Volume: exclude warmups, null weight, reps <= 0
            if set_type != "warmup" and weight is not None and reps and reps > 0:
                ex["volume_lbs"] += weight * reps

        workouts = []
        for wk in workout_map.values():
            exercises_list = list(wk["exercises"].values())
            volume = sum(ex["volume_lbs"] for ex in exercises_list)
            workouts.append({
                "date": wk["date"],
                "title": wk["title"],
                "exercise_count": len(exercises_list),
                "set_count": wk["_sets_total"],
                "volume_lbs": volume,
                "exercises": exercises_list,
            })

        # --- Legacy fields (backward compat) ---
        sets = [{
            "date": str(r[1]),
            "exercise": r[3],
            "weight_lbs": int(r[5]) if r[5] is not None else None,
            "reps": r[6],
            "set_index": r[4],
            "set_type": r[7],
        } for r in all_rows]

        exercises = sorted(set(s["exercise"] for s in sets))

        pr_rows = session.execute(text(f"""
            SELECT DISTINCT ON (e.name)
                   e.name as exercise,
                   round(s.weight_lbs::numeric, 0) as max_lbs,
                   (w.started_at AT TIME ZONE 'UTC')::date as date
            FROM hevy.sets s
            JOIN hevy.exercises e ON s.exercise_id = e.id
            JOIN hevy.workouts w ON s.workout_id = w.id
            WHERE (w.started_at AT TIME ZONE 'UTC')::date BETWEEN :start AND :end
              AND s.set_type != 'warmup'
              AND s.reps > 0
              AND s.weight_lbs IS NOT NULL
              {filters}
            ORDER BY e.name, s.weight_lbs DESC, w.started_at ASC, s.set_index ASC
        """), params).fetchall()

        prs = [{
            "exercise": r[0],
            "max_lbs": int(r[1]),
            "date": str(r[2]),
        } for r in pr_rows]

    return {
        **_response_metadata(start_date, end_date),
        "workout_titles": workout_titles,
        "workouts": workouts,
        # Legacy fields (deprecated, kept for backward compat)
        "exercises": exercises,
        "sets": sets,
        "prs": prs,
    }
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dashboard.py -v`
Expected: All existing tests pass + new tests pass

- [ ] **Step 5: Commit**

```bash
git add webhook/dashboard.py tests/test_dashboard.py
git commit -m "feat: extend strength API with workouts, workout_titles, and title filter"
```

---

## Task 2: Replace Strength Panel HTML/JS

**Files:**
- Modify: `webhook/dashboard.html`

Replace the strength panel with workout type tabs, trend summary cards, Plotly volume bar chart, and expandable workout log. Remove the exercise dropdown and scatter chart.

- [ ] **Step 1: Add CSS for the new strength panel components**

Add these styles to the `<style>` block in `webhook/dashboard.html` (after the existing styles, before `</style>`):

```css
  /* Strength tabs */
  .workout-tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .workout-tab {
    padding: 6px 14px; border-radius: 8px; cursor: pointer;
    font-size: 0.8rem; font-weight: 600; border: 2px solid #334155;
    background: #1e293b; color: #94a3b8; transition: all 0.15s;
  }
  .workout-tab:hover { border-color: #475569; }
  .workout-tab.active { border-color: #22c55e; color: #22c55e; background: #0f2a1a; }
  .workout-tab:focus-visible { outline: 2px solid #22c55e; outline-offset: 2px; }
  .workout-tab .tab-count { font-weight: 400; color: #475569; font-size: 0.7rem; }

  /* Trend summary */
  .trend-cards { display: flex; gap: 12px; margin-bottom: 16px; }
  .trend-card { background: #0f172a; border-radius: 8px; padding: 10px 14px; flex: 1; border: 1px solid #334155; }
  .trend-card-label { font-size: 0.65rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
  .trend-card-value { font-size: 1.2rem; font-weight: 700; }
  .trend-card-sub { font-size: 0.7rem; color: #64748b; margin-top: 2px; }

  /* Workout log */
  .workout-log { max-height: 400px; overflow-y: auto; border-radius: 8px; border: 1px solid #334155; margin-top: 16px; }
  .wk-row { background: #1e293b; border-bottom: 1px solid #0f172a; }
  .wk-header {
    display: grid; grid-template-columns: 80px 1fr 60px 55px 90px;
    padding: 10px 14px; align-items: center; font-size: 0.8rem;
    cursor: pointer; transition: background 0.15s; outline: none;
  }
  .wk-header:hover, .wk-header:focus-visible { background: #253347; }
  .wk-date { color: #94a3b8; }
  .wk-title { color: #f1f5f9; font-weight: 600; }
  .wk-stat { color: #64748b; text-align: center; }
  .wk-volume { color: #22c55e; font-weight: 600; text-align: right; }
  .wk-detail { max-height: 0; overflow: hidden; transition: max-height 0.3s; background: #0f172a; }
  .wk-detail.open { max-height: 800px; }
  .wk-detail-inner { padding: 10px 14px; }
  .wk-exercise { margin-bottom: 8px; }
  .wk-exercise-name { color: #94a3b8; font-size: 0.8rem; font-weight: 600; display: flex; justify-content: space-between; margin-bottom: 3px; }
  .wk-exercise-vol { color: #475569; font-weight: 400; }
  .wk-set { display: flex; gap: 10px; font-size: 0.78rem; padding: 1px 0; }
  .wk-set-label { color: #475569; width: 45px; }
  .wk-set-weight { color: #22c55e; width: 65px; }
  .wk-set-reps { color: #94a3b8; }
```

- [ ] **Step 2: Replace the strength panel HTML structure**

Find the existing strength panel div in the HTML:
```html
<div class="panel" id="panelStrength">
```

Replace everything from that div through its closing `</div>` with:

```html
  <div class="panel" id="panelStrength">
    <div class="workout-tabs" id="workoutTabs" role="tablist"></div>
    <div class="trend-cards" id="trendCards">
      <div class="trend-card">
        <div class="trend-card-label">Volume Trend</div>
        <div class="trend-card-value" id="trendVolume">---</div>
        <div class="trend-card-sub" id="trendVolumeSub"></div>
      </div>
      <div class="trend-card">
        <div class="trend-card-label">Peak Session</div>
        <div class="trend-card-value" id="trendPeak">---</div>
        <div class="trend-card-sub" id="trendPeakSub"></div>
      </div>
      <div class="trend-card">
        <div class="trend-card-label">Sessions</div>
        <div class="trend-card-value" id="trendSessions">---</div>
        <div class="trend-card-sub" id="trendSessionsSub"></div>
      </div>
    </div>
    <div class="chart-area" id="chartStrength"></div>
    <div class="workout-log" id="workoutLog"></div>
  </div>
```

Also remove the exercise select dropdown from the filters section (the `<select class="exercise-select" id="exerciseSelect" ...>` element) and any JS that shows/hides it.

- [ ] **Step 3: Replace the strength panel JavaScript**

Remove the existing `renderStrengthPanel`, `populateExerciseSelect` (if it exists), and `updateStrengthCard` functions. Replace with new implementations.

The new JS must:
- Build workout type tabs from `strData.workout_titles`
- Track `activeWorkoutTitle` state
- On tab click: filter `strData.workouts` by title, render volume chart + trend cards + workout log
- Volume bar chart: Plotly bars with proportional heights, hover showing volume/sets/delta
- Workout log: DOM-built rows with click-to-expand using `aria-expanded`
- Update the strength summary card to show latest session volume

Key functions to implement:
- `updateStrengthCard()` — update the top card with latest session volume/title
- `renderStrengthPanel()` — build tabs, render active tab content
- `renderStrengthContent(filtered)` — render trend cards, chart, log for filtered workouts
- `buildWorkoutLog(filtered)` — build expandable workout rows with DOM methods
- `fmtVolume(n)` — format volume as "15,150" or "15.2k"

All dynamic text via `textContent`/`createElement`. Keyboard support with `tabindex`, `role="tab"`, `aria-selected`, `aria-expanded`, `aria-controls` on expandable rows. Enter/Space toggles expansion.

- [ ] **Step 4: Commit**

```bash
git add webhook/dashboard.html
git commit -m "feat: replace strength panel with volume progression and workout log"
```

---

## Task 3: Deploy and Verify

**Files:**
- No new files

- [ ] **Step 1: Sync and rebuild**

```bash
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='docs/' --exclude='.DS_Store' --exclude='.superpowers/' \
  /Users/jehan/Projects/Basin/webhook/ root@reservebot:/opt/basin/webhook/

ssh root@reservebot 'export $(cat /etc/basin/secrets | xargs) && cd /opt/basin && \
  op run --env-file=.env -- docker compose up -d --build webhook'
```

- [ ] **Step 2: Verify API returns new fields**

```bash
curl -s "http://100.125.126.42:8075/api/fitness/strength?start=2025-01-01" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('Titles:', d['workout_titles'])
print('Workouts:', len(d['workouts']))
if d['workouts']:
    w = d['workouts'][-1]
    print(f'Latest: {w[\"date\"]} {w[\"title\"]} - {w[\"volume_lbs\"]} lbs, {w[\"exercise_count\"]} exercises')
print('Legacy fields present:', 'exercises' in d and 'sets' in d and 'prs' in d)
"
```

Expected: workout_titles list, workouts with nested exercises/sets, legacy fields present.

- [ ] **Step 3: Browser verification**

Open `http://100.125.126.42:8075/dashboard` and verify:
- Strength card shows latest session volume and title
- Clicking strength card shows tabs (Lower A, Upper A, Lower B, Upper B)
- Tabs switch the chart and log to that workout type only
- Volume bars are proportional with session-over-session % deltas
- Clicking a workout row expands to show exercises with sets/reps/weight
- Time filters work (changing range updates strength data)
- Keyboard: Tab to tabs, Enter to select, Tab to workout rows, Enter to expand

- [ ] **Step 4: Commit any fixes and push**

```bash
git add -A
git commit -m "fix: strength panel deployment adjustments"
git push origin main
```
