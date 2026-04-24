# Design: Plan Quota, Per-Skill Attribution, Alert Threshold

**Date:** 2026-04-24
**Status:** Draft — awaiting user review
**Scope:** Extend token-dashboard with 3 independent features in a single spec.

## 1. Goals

Add three capabilities to the local token-dashboard:

1. **Plan quota tracking** — show rolling 5-hour window + rolling 7-day window usage against the user's Max plan quota, Desktop-Claude-UI style.
2. **Per-skill attribution (proxy)** — show how many tokens each skill invocation likely injected into context (via skill definition size × invocations).
3. **Alert threshold** — surface a dashboard banner when usage crosses configured percentage thresholds of quota.

All three live in one spec and one implementation plan because they share config UI, API surface, and touch the same data model.

## 2. Non-goals

- No integration with Claude Code's own `/status` / quota API (undocumented, fragile).
- No delta-based precise skill token measurement (phase 2; requires schema change to store `tool_use_id`).
- No push notifications (macOS, Slack, email) — banner only in phase 1.
- No dismiss mechanism for alerts — banner goes away when usage drops under threshold.
- No changes to existing Overview/Tools/Sessions tabs.
- No upstream contribution — this is a local customization branch.

## 3. Architecture

**Integration approach:** extend-in-place. New modules added to the `token_dashboard/` Python package and new tab HTML/JS blocks added to the `web/` frontend. Existing files get minimal edits (new API routes, new tab entries).

### 3.1 Backend modules (new files)

**`token_dashboard/quota.py`** (~80 LOC)
- `rolling_window_usage(db_path, hours: int) -> dict` — sum tokens from `messages` where `timestamp > now - hours*3600`, grouped by billable category (input / output / cache_read / cache_create_5m / cache_create_1h). Returns aggregate + a `total_billable` sum for threshold checks.
- `weekly_window_usage(db_path, days: int = 7) -> dict` — same, longer window.
- `get_quota_config(db_path) -> dict` — reads KV rows from existing `plan` table, returns `{plan, quota_5h, quota_weekly, alert_pct_5h, alert_pct_weekly, alert_pct_session}` with defaults fallback.
- `set_quota_config(db_path, **kwargs)` — validates and upserts.
- `PLAN_DEFAULTS` and `ALERT_DEFAULTS` module constants.

**`token_dashboard/skill_tokens.py`** (~40 LOC)
- `skill_cost_proxy(db_path, since=None, until=None) -> list` — join `db.skill_breakdown()` with `skills.cached_catalog()`. For each row, compute `proxy_tokens = invocations * (catalog[slug].chars // 4)`. Return `[{skill, invocations, sessions, last_used, proxy_tokens, chars}]` sorted by `proxy_tokens DESC`.
- Handles missing catalog entry (unknown skill) with `proxy_tokens=None` and a `catalog_missing: True` flag so frontend can show `—`.

**`token_dashboard/alerts.py`** (~60 LOC)
- `evaluate_alerts(db_path) -> list` — returns list of active alerts:
  ```
  [{level: "warning"|"critical",
    key: "5h"|"weekly"|"session",
    current: int, limit: int, pct: float,
    message: str,
    time_to_reset_minutes: int | None}]
  ```
- Thresholds from `get_quota_config`. A second band above threshold = critical (threshold+15pp).
- Session alert: compares current session's billable tokens to `alert_pct_session × quota_5h`. Current session = most recent `session_id` with activity in last 2 hours.
- Pure function; no persisted state.

### 3.2 Backend edits (existing files)

**`token_dashboard/db.py`** — no schema migration. Existing `plan` KV table is reused for config. Add helper `get_kv(db_path, key, default)` / `set_kv(db_path, key, value)` if not already present (check before adding).

**`token_dashboard/server.py`** — new routes:
- `GET /api/quota` → `{config, usage_5h, usage_weekly}`
- `POST /api/quota/config` → accepts `{quota_5h?, quota_weekly?, plan?, alert_pct_5h?, alert_pct_weekly?, alert_pct_session?}`, validates (positive ints, percentages 1–99), upserts, returns updated config.
- `GET /api/skills_cost` → proxy breakdown rows.
- `GET /api/alerts` → active alerts list.

### 3.3 Frontend

**`web/index.html`** — two new tabs: `Quota`, `Skills`. Banner `<div id="alert-banner">` added above existing tab bar, hidden by default.

**`web/app.js`** — add route handlers for the two tabs. Extend existing 30-second refresh loop to also hit `/api/alerts` and update banner visibility/content. Quota tab renders two progress bars + two charts + a settings card. Skills tab renders a sortable table.

**`web/charts.js`** — add two chart configs: `tokensPerHour` and `tokensPerDay` with a horizontal line overlay at the quota value.

**`web/style.css`** — banner styles (yellow for warning, red for critical), progress bar styles, settings card styles.

## 4. Data model

**No schema migration.** Reuses existing `plan` KV table with these keys:

| Key | Type | Default (Max plan) |
|---|---|---|
| `plan` | string | `"max"` (already present) |
| `quota_5h` | int (tokens) | `220000` |
| `quota_weekly` | int (tokens) | `6000000` |
| `alert_pct_5h` | int 1–99 | `75` |
| `alert_pct_weekly` | int 1–99 | `80` |
| `alert_pct_session` | int 1–99 | `50` |

`PLAN_DEFAULTS` in `quota.py`:
```python
PLAN_DEFAULTS = {
  "pro":     {"quota_5h": 45_000,   "quota_weekly": 1_200_000},
  "max":     {"quota_5h": 220_000,  "quota_weekly": 6_000_000},
  "max-20x": {"quota_5h": 900_000,  "quota_weekly": 30_000_000},
}
```

These are best-effort community-estimate numbers. User overrides via Settings card.

## 5. UX

### 5.1 Quota tab

```
┌─ 5-HOUR WINDOW ──────────────────────────────────┐
│ ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░  172,340 / 220,000  (78%)   │
│ Resets at 18:42 (in 1h 15m)                      │
└──────────────────────────────────────────────────┘
┌─ 7-DAY WINDOW ───────────────────────────────────┐
│ ▓▓▓░░░░░░░░░░░░░░░░  892,120 / 6,000,000  (15%)  │
└──────────────────────────────────────────────────┘

[Chart: tokens per hour, last 24h, with quota line]
[Chart: tokens per day, last 14 days, with daily-avg quota line]

┌─ SETTINGS ────────────────────────────────────────┐
│ Plan:   [ max ▾ ]                                 │
│ 5h quota (tokens):     [ 220000 ]                 │
│ Weekly quota (tokens): [ 6000000 ]                │
│ Alert at:  5h  [ 75% ]  weekly [ 80% ]  session [ 50% ] │
│ [ Save ]                                          │
└───────────────────────────────────────────────────┘
```

### 5.2 Banner

Appears above tab bar when any alert is active:
```
⚠ 5h window: 78% (172k / 220k). Rolling reset in 1h 15m.
```
Critical (red) appears when usage > threshold + 15pp.

### 5.3 Skills tab

Table with columns: **Skill** | **Invocations** | **Proxy tokens** | **Last used**.
Subtitle: *"Proxy estimate: skill definition size × invocations. Doesn't include downstream tool calls triggered by the skill."*

## 6. Defaults & validation rules

- Plan defaults: `max` if not set.
- Quota min/max: `1_000` to `100_000_000`. Negative or zero rejected (400).
- Percentages: `1` to `99`. Out-of-range rejected.
- Settings POST is all-or-nothing: validate all, then upsert atomically.

## 7. Error handling

- `/api/quota` when DB empty (no sessions yet): returns `{usage_5h: {total_billable: 0, ...}, ...}`. Frontend shows 0%.
- `/api/skills_cost` when `skills.cached_catalog()` fails (rare — corrupted skill dir): returns empty list + logs to stderr; frontend shows "No skill invocations recorded."
- `/api/alerts` always returns a list (possibly empty). Banner hidden when empty.
- Config POST with invalid value: 400 with JSON error, UI shows inline error next to the field.

## 8. Testing

New pytest files:
- `tests/test_quota.py` — rolling window aggregation with fixture data (known tokens at known timestamps, verify 5h and weekly sums).
- `tests/test_skill_tokens.py` — proxy formula with fixture catalog + invocation rows. Empty catalog case. Unknown slug case.
- `tests/test_alerts.py` — threshold matrix: below, at, above warning, above critical. Combined (multiple alerts simultaneously).
- `tests/test_quota_config.py` — validation rules, upsert roundtrip.

Manual smoke:
1. Open dashboard; verify Quota tab loads with current usage.
2. Lower `alert_pct_5h` temporarily to trigger banner; verify banner appears, correct colour.
3. Edit `quota_5h` to a very large number; verify banner disappears.
4. Reload browser; verify config persisted.
5. Open Skills tab; verify at least one skill from recent sessions shows with non-zero invocations.

## 9. Git workflow

- Branch: `customizations-20260424` from `main`.
- Granular commits per module (`quota.py`, `skill_tokens.py`, `alerts.py`, route edits, frontend edits, tests).
- Local only — no GitHub fork.
- Future upstream `git pull` merges into `main`, rebase customizations branch; surface conflicts in config areas if upstream adds overlapping features.

## 10. Open questions / phase 2 (deferred)

- **Delta-based skill token measurement** — requires storing `tool_use_id` on `tool_calls` row and joining with next-turn `input_tokens` delta. Defer until proxy proves insufficient.
- **Real Claude Code quota fetch** — if Claude Code ever exposes a stable local file for quota state, read it instead of hard-coded defaults.
- **Alert channels** — macOS notification, Obsidian daily-note append, shell exit-code (deferred per user feedback — banner only in phase 1).
- **Session cost alert beyond 5h window** — e.g. "this single session used $5". Not in phase 1 (threshold is window-based).

## 11. Files touched summary

```
NEW  token_dashboard/quota.py                     ~80 LOC
NEW  token_dashboard/skill_tokens.py              ~40 LOC
NEW  token_dashboard/alerts.py                    ~60 LOC
EDIT token_dashboard/db.py                        (+get_kv/set_kv helpers if missing, ~15 LOC)
EDIT token_dashboard/server.py                    (+4 routes, ~40 LOC)
NEW  tests/test_quota.py                          ~60 LOC
NEW  tests/test_skill_tokens.py                   ~40 LOC
NEW  tests/test_alerts.py                         ~60 LOC
NEW  tests/test_quota_config.py                   ~40 LOC
EDIT web/index.html                               (+2 tabs, +banner div)
EDIT web/app.js                                   (+tab handlers, +banner update in refresh loop)
EDIT web/charts.js                                (+2 chart configs)
EDIT web/style.css                                (+banner, progress bar, settings card styles)
```

Estimated total: ~500 LOC new, ~100 LOC edit in existing.
