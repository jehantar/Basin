# Basin — Project Context for AI Assistants

> This document captures everything an AI assistant needs to understand Basin, Jehan's preferences, hard-won lessons, and the accumulated context from months of development. It is designed to be portable across any LLM.

---

## Table of Contents

1. [Who is Jehan](#who-is-jehan)
2. [What is Basin](#what-is-basin)
3. [Architecture & Tech Stack](#architecture--tech-stack)
4. [Infrastructure & Deployment](#infrastructure--deployment)
5. [Database Schema](#database-schema)
6. [Data Collectors](#data-collectors)
7. [Dashboards](#dashboards)
8. [Integrations Deep Dive](#integrations-deep-dive)
9. [Training & Fitness Context](#training--fitness-context)
10. [Hard-Won Lessons & Rules](#hard-won-lessons--rules)
11. [Working Style & Preferences](#working-style--preferences)

---

## Who is Jehan

- Software engineer building Basin as a personal data aggregation platform
- Views dashboards primarily on iPhone via installed PWA
- Training for a **2:00 half marathon in late July 2026** — actively tracking running form, pace curves, HR zones, and mileage progression
- Wears a Garmin Forerunner 965 (does NOT wear it to sleep)
- Prefers CLI-first workflows (`vercel`, `gh`, `docker`, `op`, etc.)
- Values simplicity, minimal code impact, and root-cause fixes over band-aids
- Wants terse, direct responses — no trailing summaries, no filler

---

## What is Basin

Basin is a **personal data aggregation platform** that pulls fitness, financial, health, and investment data from 6+ external sources into a PostgreSQL database and serves interactive web dashboards.

**Data flow:**
```
External APIs → Collectors (cron) → PostgreSQL → FastAPI webhook → Browser dashboards
                     ↓
              Telegram alerts on failure
```

**Four dashboards:**
1. **Fitness** (`/dashboard/fitness`) — Runs, pace, splits, VO2max, recovery, training load
2. **Finance** (`/dashboard/finance`) — Spend trends, categories, per-card breakdown
3. **Investments** (`/dashboard/investments`) — Stock watchlist, heatmap, performance vs S&P 500
4. **System** (`/dashboard/system`) — Collector health, run history, error tracking

**Repo:** Public on GitHub (scrubbed of IPs/secrets). Planning docs gitignored.

---

## Architecture & Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Backend | FastAPI, uvicorn |
| ORM | SQLAlchemy 2.0+ |
| Database | PostgreSQL 16 (Alpine) |
| Frontend | Vanilla HTML5/JS, Chart.js, Leaflet (maps), Plotly |
| Orchestration | Docker Compose (3 services: postgres, collector, webhook) |
| Scheduling | crontab inside collector container |
| Auth | OAuth 2.0 (Strava, Garmin session-based) |
| Secrets | 1Password CLI (`op://` references in `.env`, injected via `op run`) |
| Alerts | Telegram bot |
| Testing | pytest, pytest-asyncio |
| PWA | Service worker + manifest for iPhone home screen install |

**Project structure:**
```
Basin/
├── collectors/          # 8 collector modules (6 active, cron-scheduled)
├── webhook/             # FastAPI server + 4 dashboard HTML files
├── shared/              # DB, config, Telegram utilities
├── migrations/          # 8 PostgreSQL schema files
├── scripts/             # bootstrap-vm.sh, backup.sh, sync-health.sh
├── tests/               # pytest suite
├── cli/                 # Click CLI for health checks
├── docker-compose.yml   # postgres + collector + webhook
├── pyproject.toml       # Python deps
├── crontab              # Collector schedules
└── .env.example         # 30+ env vars (all op:// references)
```

---

## Infrastructure & Deployment

### VM Access
- SSH: `root@Basin` (Tailscale IP: `100.125.126.42`)
- Webhook port: 8075 (mapped to container port 8000)
- Dashboard URL: `http://100.125.126.42:8075/dashboard/fitness`

### Deployment Workflow (Critical — read carefully)

Code is **baked into Docker images**, NOT volume-mounted. To deploy a file change:

1. SCP the file to the VM: `scp <local> root@Basin:/opt/basin/<path>`
2. Docker cp into the running container: `docker cp /opt/basin/<path> basin-webhook-1:/app/<path>`
3. Restart: `docker compose restart webhook`

**Common mistakes to avoid:**
- SCP + restart alone does NOT update code (host files aren't mounted)
- `docker compose restart` preserves docker cp'd files — use this, not `systemctl restart`
- `systemctl restart basin.service` recreates containers from the image, wiping docker cp'd changes
- Never run bare `docker compose up -d` — it won't resolve `op://` secret references
- Webhook HTML files go to `/app/webhook/*.html` (NOT `/app/*.html`) — the server uses `os.path.dirname(__file__)` to find them
- For new directories, `docker cp` first, THEN restart

### Secrets Management
- All secrets stored in 1Password with `op://Vault/Item/field` references in `.env`
- Injected at runtime via `op run` in the `basin.service` systemd unit
- NEVER store plaintext secrets on disk or in env vars
- If secrets appear missing, the fix is `systemctl restart basin.service`, not replacing `op://` with real values

---

## Database Schema

Connection: `postgresql://basin:<password>@postgres:5432/basin`

### Fitness (Primary: Garmin)

**`garmin.activities`** — Main run/activity table
- `garmin_id` (BIGINT unique), `name`, `activity_type` (running/treadmill_running/trail_running)
- `start_time` (TIMESTAMPTZ), `duration_sec`, `distance_m`
- `avg_speed_mps`, `max_speed_mps`, `avg_hr`, `max_hr`
- `avg_cadence`, `max_cadence`, `avg_power`
- `avg_ground_contact_time_ms`, `avg_vertical_oscillation_cm`, `avg_stride_length_m`
- `elevation_gain_m`, `calories`
- `training_effect_aerobic`, `training_effect_anaerobic`
- `splits` (JSONB — per-lap), `map_polyline` (GPS)
- Indexed on `(start_time DESC)` and `(activity_type, start_time DESC)`

**`garmin.daily_summary`** — Daily health metrics
- `calendar_date`, `resting_hr`, `stress_avg`, `body_battery_high/low`
- `steps`, `floors_climbed`, `intensity_minutes`
- `vo2_max`, `respiration_avg`

**`garmin.body_composition`** — Weight, body fat, muscle mass

**`strava.activities`** — Enrichment/fallback for splits, GPS, elevation
- `strava_id`, `sport_type` ('Run'), splits JSONB (per-mile), `map_polyline`
- OAuth tokens in `strava.tokens` (auto-refresh)

**`healthkit.workouts`** — Legacy Apple Health data (pre-Garmin)
- `workout_type` ('Running'), duration, distance, HR, cadence
- Related metrics in `healthkit.metrics` (running_speed, running_power, running_stride_length)

**`intervals.fitness`** — CTL/ATL/TSB from Intervals.icu
**`intervals.pace_curve` / `intervals.hr_curve`** — Best-effort curves

### Strength
**`hevy.workouts` / `hevy.sets`** — Exercise name, weight, reps, sets

### Finance
**`teller.accounts`** — Bank accounts (Chase)
**`teller.transactions`** — Amount, description, category, date, status
**`teller.balances`** — Daily balances

### Investments
**`investments.watchlist`** — ~36 stocks across 3 groups (Brokerage, IRA, Parth)
**`investments.daily_prices`** — Historical stock prices
**`investments.stock_groups` / `stock_group_members`** — Many-to-many grouping

### System
**`basin.collector_runs`** — Execution log (collector, status, started_at, finished_at, records_processed, error_message)

### Query Conventions
- **Always** use `AT TIME ZONE 'America/Los_Angeles'` for date conversions — never raw UTC
- Pace from speed: `round((1609.34 / avg_speed_mps / 60.0)::numeric, 2)` gives min/mi as decimal
- Distance: `distance_m / 1609.34` for miles
- Running activities: `WHERE activity_type IN ('running', 'treadmill_running', 'trail_running')`

---

## Data Collectors

| Collector | Source | Schedule (UTC) | Container | Key Details |
|-----------|--------|---------------|-----------|-------------|
| **Garmin** | garminconnect API | 6:05 AM | collector | Primary fitness. Auth via email/password, tokens at `/data/garmin/tokens/`. First login needs MFA |
| **Hevy** | CSV drop folder | 6:00 AM | collector | Strength training data |
| **Intervals.icu** | REST API | 6:10 AM | collector | CTL/ATL/TSB + pace/HR curves. Athlete ID: i553742. Strava TOS blocks individual activity details |
| **Strava** | OAuth REST API | 6:15 AM | collector | Passive fallback. Incremental via watermark. Client ID: 201200. Auto-refreshes 6h tokens |
| **Teller** | Bank API (mTLS) | 7:00 AM | collector | Chase accounts. Token expires on bank disconnect — sends Telegram alert with re-enroll link |
| **Nasdaq** | SHARADAR/SEP + Yahoo | 1:30 AM | collector | Equities via SHARADAR, ETFs (SPY/QQQ) via Yahoo. SHARADAR does NOT cover ETFs/mutual funds |

**Disabled:** HealthKit (replaced by Garmin), Schwab (removed entirely — don't re-add)

**Teller re-enrollment:** When Chase disconnects, collector gets 401 → sends Telegram alert with link to `/teller/enroll?key=<KEY>`. User re-auths, new token saved to `teller.tokens` singleton table.

---

## Dashboards

### Fitness Dashboard (`/dashboard/fitness`)
- **Runs table:** Name, date, distance, pace, HR, cadence, elevation, calories
  - Expandable splits with pace dots + Leaflet/OpenStreetMap GPS map
  - Activity names strip "Location - Plan - " prefix via `rsplit(" - ", 1)[-1]`
  - `col-landscape` class hides columns in portrait, shows at 600px+
- **VO2 Max card:** UNIONs `healthkit.metrics` + `garmin.daily_summary` for continuous history
- **Training Load card:** CTL/ATL/TSB chart from Intervals.icu
- **Weekly Mileage card**
- **Strength card:** From Hevy data

### Finance Dashboard (`/dashboard/finance`)
- Monthly spend trends, category heatmaps, transaction filtering, per-card breakdown

### Investments Dashboard (`/dashboard/investments`)
- Normalized return overlay chart with hover highlighting
- vs S&P 500 toggle (alpha calculation)
- Compare Groups (Brokerage / IRA / Parth) side-by-side
- Heatmap with color-coded return tiles
- Sortable table: Price, % Return, CAGR, Alpha, 52W High/Low
- Sector allocation donut, search filter
- SPY/QQQ as dashed reference lines
- User prefers managing tickers via direct SQL

### System Dashboard (`/dashboard/system`)
- Collector health, run history, error tracking

### All Dashboards
- **Mobile-first CSS:** Base styles target ~390px iPhone; desktop overrides in `@media (min-width: 900px)`
- Plotly charts use `isMobile()` helper (< 600px) for responsive margins/fonts
- Touch targets 44px+ (Apple HIG)
- Installable as PWA via Safari (manifest + service worker + 180x180 icon)

---

## Integrations Deep Dive

### Garmin
- Forerunner 965, daily wear (NOT worn to sleep)
- Sleep, Body Battery, HRV, Training Readiness metrics removed from schema (not applicable)
- VO2 Max via separate `get_max_metrics()` API call (not in daily summary payload)
- Splits are per-lap (Garmin format), not per-mile like Strava
- GPS polyline extracted from GPX download

### Strava
- Provides: elevation, per-mile splits, GPS polyline, max HR, calories, cadence
- Acts as enrichment layer — dashboard uses `COALESCE` to fill gaps from HealthKit
- Token refresh happens in separate DB transaction (prevents crash loop)
- HealthKit↔Strava matching: minute-level key with ±2min fallback
- Rate limit: 1,000 reads/day (well within budget)

### Health Auto Export (HAE) — Historical
- iOS app, sends HealthKit data to webhook every 6 hours
- Metric names are **snake_case** (not title-case as docs suggest) — always test with real payloads
- "Summarize Data" creates near-duplicate workouts with different start times — disable it
- Does NOT send elevation data (column exists but always empty)
- Batch Requests must be ON to avoid timeouts

### Teller (Banking)
- mTLS authentication for bank API
- Token stored in `teller.tokens` singleton table (id=1), fallback to env var
- Re-enrollment page at `/teller/enroll` gated by `TELLER_WEBHOOK_KEY`
- Immediate Telegram alert on 401 (not after 3 failures)

### Intervals.icu
- Collector hits REST API directly (MCP integration was tried and removed — broken)
- Only aggregated data works (wellness, curves, athlete profile) — individual activities blocked by Strava TOS
- Kept alongside Strava for battle-tested training load math (TRIMP/EMA)

### Nasdaq / Yahoo Finance
- SHARADAR/SEP for equities (requires API key from Nasdaq Data Link)
- Yahoo Finance for benchmark ETFs (SPY, QQQ) — marked `is_benchmark = true`
- SHARADAR does NOT cover ETFs or mutual funds (VTIAX, VTSAX, IVV, SCHB, QQQM all return 0 rows)
- Backfill: resolve 1Password key locally with `op read`, inject via `docker exec -e`

---

## Training & Fitness Context

### Half Marathon Goal
- **Target:** 2:00 HM, late July 2026
- **Race pace needed:** ~9:09/mi
- **Training structure:** 3x/week — long run + tempo/hills + easy
- **80/20 rule:** Most volume should be easy Z2

### Current Fitness (as of May 2026)
- **VO2max:** 46.5 (Garmin), up from 42.8 in mid-Jan 2026
- **Running ~W16 of training**
- **Key gap:** Distance (longest run ~8.5mi, roughly half race distance) and mechanical efficiency
- **Realistic estimate:** 2:08-2:14, with 2:00 possible if pace curve flattens

### Pace Curve (all-time bests, Apr 2026)
| Distance | Time | Pace |
|----------|------|------|
| 400m | 1:48 | 7:14/mi |
| 1K | 5:02 | 8:06/mi |
| 5K | 29:41 | 9:33/mi |
| 10K | 66:04 | 10:37/mi |

- 2:31 gap from 1K→10K = steep curve = aerobic endurance is the limiter, not speed
- 10K equivalency predicts HM ~2:22-2:25

### HR Profile
- Max HR: 189 bpm
- Threshold: ~176-180 bpm
- Z2 range: 141-157 bpm (target for easy runs)
- 60-min sustained: 168 bpm — moderate cardiac drift
- Flag if avg HR > 157 on easy days

### Running Form — Garmin Dynamics

**Baseline (early May 2026):**
| Metric | Value | Note |
|--------|-------|------|
| GCT | 312-314ms | Garmin "poor" — high, loading calves |
| Vert Osc | 9.3-9.4cm | Garmin "good" but bouncy combined with high GCT |
| Cadence | 143-145 spm | Low but NOT overstriding (stride 1.04-1.07m) |
| Power | 347W @ 10:13/mi | ~131 W/m/s — inefficient (efficient = 100-110) |

**May 10 checkpoint (8.5mi @ 9:56/mi):**
| Metric | Value | Change |
|--------|-------|--------|
| GCT | 287ms | -25ms, below 300 target |
| Vert Osc | 8.74cm | -0.6cm |
| Cadence | 158 spm | +13-15 from baseline |

**Key conclusions:**
- Not overstriding — confirmed with Garmin data
- Inefficiency is mechanical (energy waste from vertical bounce + long ground contact)
- Calf soreness caused by: extended GCT + high vert osc making calves work as springs
- GCT and vert osc are the real levers, not cadence

**Interventions in progress:**
- Eccentric calf raises (doing)
- Pogo hops / jump rope pre-run (GCT drill)
- Hill sprints (doing)
- Forward lean cue from ankles

**When analyzing runs, always compare:**
1. GCT (target: trending below 300ms)
2. Vertical oscillation (target: trending down from 9.3 baseline)
3. Cadence (context only)
4. Calf soreness status
5. Match pace/distance for apples-to-apples comparisons

### Long Run Progression Targets
- 10mi by June 1
- 12mi by July 1
- Add 0.5-1 mi/week

---

## Hard-Won Lessons & Rules

These are mistakes that were made and debugged. Don't repeat them.

### Deployment
1. **SCP alone doesn't update code** — must `docker cp` into container, then restart
2. **HTML files go to `/app/webhook/*.html`** — not `/app/*.html` (server uses `__file__` dirname)
3. **`docker compose restart` preserves cp'd files; `systemctl restart` wipes them** — use compose restart for file deploys
4. **Never run bare `docker compose up -d`** — won't resolve `op://` secrets
5. **VM containers may have stale code** from old images — check running containers, not just repo

### Data Integrity
6. **All dates must use `AT TIME ZONE 'America/Los_Angeles'`** — a 5:31 PM Pacific run showed as next day in UTC
7. **HAE sends snake_case** metric names, not title-case as docs suggest — always test with real payloads
8. **HAE "Summarize Data" creates near-duplicate workouts** — different start times, same workout
9. **Never present unverified data as fact** — only report metrics that exist as named DB columns. If citing `raw_json`, caveat as device-reported and potentially unreliable. Never invent environmental context (temperature was fabricated once — don't do that)

### Security
10. **Never store plaintext secrets** — always use `op://` references
11. **Repo is public** — don't hardcode IPs, hostnames, or credentials. Use env vars/placeholders
12. **GitHub email privacy** — must use `jehantar@users.noreply.github.com` for pushes

### Integration Gotchas
13. **SHARADAR/SEP doesn't cover ETFs** — use Yahoo Finance for SPY, QQQ, etc.
14. **Teller 401 = bank disconnect** — immediate Telegram alert, re-enroll at `/teller/enroll`
15. **Strava TOS blocks individual activities** via Intervals.icu — only aggregated data works
16. **Garmin VO2 Max requires separate `get_max_metrics()` call** — not in daily summary payload
17. **Garmin splits are per-lap, Strava splits are per-mile** — different formats

---

## Working Style & Preferences

- **Mobile-first:** All dashboards target iPhone (~390px) as base CSS, desktop as override via `@media (min-width: 900px)`
- **Simplicity first:** Make every change as simple as possible. Don't over-engineer
- **No laziness:** Find root causes. No temporary fixes. Senior developer standards
- **Minimal impact:** Changes should only touch what's necessary
- **Terse responses:** No trailing summaries. The user can read the diff
- **CLI-first:** Prefer `vercel`, `gh`, `docker`, `op` over web UIs
- **Stock management via SQL:** User prefers managing investment tickers directly via SQL rather than a UI
- **Plan before building:** For non-trivial tasks, write a plan first and check in before implementing
- **Verify before done:** Never mark a task complete without proving it works
- **Post-commit:** Review changed code for reuse, quality, and efficiency after every commit
