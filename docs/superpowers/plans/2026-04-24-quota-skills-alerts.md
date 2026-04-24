# Quota / Per-Skill Attribution / Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three features to token-dashboard — rolling 5h/weekly plan quota tracking, proxy-based per-skill token attribution, and dashboard-banner threshold alerts — all controllable from a new Quota tab.

**Architecture:** Extend-in-place. Three new Python modules in `token_dashboard/` package, four new API routes in `server.py`, one new JS route file in `web/routes/`, minimal edits to `web/app.js` / `index.html` / `style.css`. No DB schema migration — reuses existing `plan` KV table.

**Tech Stack:** Python 3.8+ (stdlib only — `http.server`, `sqlite3`), ES6 modules, no frontend build step. Tests via `unittest`.

**Spec:** `docs/superpowers/specs/2026-04-24-quota-skills-alerts-design.md`.

**Branch:** `customizations-20260424` (already created).

---

## File Structure

**New files:**
- `token_dashboard/quota.py` — plan config KV helpers, rolling window queries, defaults
- `token_dashboard/skill_tokens.py` — proxy cost formula combining `skill_breakdown` + `cached_catalog`
- `token_dashboard/alerts.py` — threshold evaluation (pure function)
- `web/routes/quota.js` — Quota tab UI (progress bars, charts, settings card)
- `tests/test_quota.py` — config + window + defaults
- `tests/test_skill_tokens.py` — proxy formula edge cases
- `tests/test_alerts.py` — threshold matrix

**Modified files:**
- `token_dashboard/server.py` — 4 new routes: `GET /api/quota`, `POST /api/quota/config`, `GET /api/skills_cost`, `GET /api/alerts`
- `web/app.js` — register `/quota` route, add banner poll to refresh loop
- `web/routes/skills.js` — add `proxy_tokens` column (replaces existing `tokens_per_call`)
- `web/index.html` — add banner `<div>` above nav, add `/quota` nav link
- `web/style.css` — banner + progress bar + settings card styles

Each file has one clear responsibility. The quota/alerts/skill_tokens modules are independent — can be tested and committed separately.

---

## Task 1: Quota config — KV helpers, defaults, get/set

**Files:**
- Create: `token_dashboard/quota.py`
- Test: `tests/test_quota.py`

- [ ] **Step 1: Write failing test for defaults**

Create `tests/test_quota.py`:

```python
import os
import tempfile
import unittest

from token_dashboard.db import init_db
from token_dashboard.quota import (
    get_quota_config, set_quota_config,
    PLAN_DEFAULTS, ALERT_DEFAULTS,
)


class QuotaConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "q.db")
        init_db(self.db)

    def test_defaults_when_unset(self):
        cfg = get_quota_config(self.db)
        self.assertEqual(cfg["plan"], "max")
        self.assertEqual(cfg["quota_5h"], PLAN_DEFAULTS["max"]["quota_5h"])
        self.assertEqual(cfg["quota_weekly"], PLAN_DEFAULTS["max"]["quota_weekly"])
        self.assertEqual(cfg["alert_pct_5h"], ALERT_DEFAULTS["alert_pct_5h"])

    def test_set_and_roundtrip(self):
        set_quota_config(self.db, plan="max-20x", quota_5h=999_000, alert_pct_5h=90)
        cfg = get_quota_config(self.db)
        self.assertEqual(cfg["plan"], "max-20x")
        self.assertEqual(cfg["quota_5h"], 999_000)
        self.assertEqual(cfg["alert_pct_5h"], 90)

    def test_plan_change_updates_quota_defaults(self):
        # When user changes plan without explicit quota, defaults for that plan apply
        set_quota_config(self.db, plan="pro")
        cfg = get_quota_config(self.db)
        self.assertEqual(cfg["quota_5h"], PLAN_DEFAULTS["pro"]["quota_5h"])

    def test_validation_rejects_bad_percent(self):
        with self.assertRaises(ValueError):
            set_quota_config(self.db, alert_pct_5h=150)
        with self.assertRaises(ValueError):
            set_quota_config(self.db, alert_pct_5h=0)

    def test_validation_rejects_bad_quota(self):
        with self.assertRaises(ValueError):
            set_quota_config(self.db, quota_5h=-1)
        with self.assertRaises(ValueError):
            set_quota_config(self.db, quota_5h=500)  # below min 1_000


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test — verify fail**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_quota -v`
Expected: `ModuleNotFoundError: No module named 'token_dashboard.quota'`

- [ ] **Step 3: Create `quota.py` with config functions**

Create `token_dashboard/quota.py`:

```python
"""Plan quota: config KV helpers, defaults, rolling window queries."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from .db import connect

PLAN_DEFAULTS = {
    "pro":     {"quota_5h": 45_000,   "quota_weekly": 1_200_000},
    "max":     {"quota_5h": 220_000,  "quota_weekly": 6_000_000},
    "max-20x": {"quota_5h": 900_000,  "quota_weekly": 30_000_000},
    "api":     {"quota_5h": 1_000_000, "quota_weekly": 20_000_000},
}

ALERT_DEFAULTS = {
    "alert_pct_5h": 75,
    "alert_pct_weekly": 80,
    "alert_pct_session": 50,
}

_QUOTA_MIN = 1_000
_QUOTA_MAX = 100_000_000


def _get_kv(db_path: Union[str, Path], key: str, default=None):
    with connect(db_path) as c:
        row = c.execute("SELECT v FROM plan WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def _set_kv(db_path: Union[str, Path], key: str, value) -> None:
    with connect(db_path) as c:
        c.execute("INSERT OR REPLACE INTO plan (k, v) VALUES (?, ?)", (key, str(value)))
        c.commit()


def get_quota_config(db_path: Union[str, Path]) -> dict:
    plan = _get_kv(db_path, "plan", "max")
    plan_defaults = PLAN_DEFAULTS.get(plan, PLAN_DEFAULTS["max"])

    def _int(key, fallback):
        v = _get_kv(db_path, key)
        return int(v) if v is not None else fallback

    return {
        "plan": plan,
        "quota_5h": _int("quota_5h", plan_defaults["quota_5h"]),
        "quota_weekly": _int("quota_weekly", plan_defaults["quota_weekly"]),
        "alert_pct_5h": _int("alert_pct_5h", ALERT_DEFAULTS["alert_pct_5h"]),
        "alert_pct_weekly": _int("alert_pct_weekly", ALERT_DEFAULTS["alert_pct_weekly"]),
        "alert_pct_session": _int("alert_pct_session", ALERT_DEFAULTS["alert_pct_session"]),
    }


def set_quota_config(db_path: Union[str, Path], **kwargs) -> None:
    # Validate first, upsert after (all-or-nothing)
    if "plan" in kwargs:
        if kwargs["plan"] not in PLAN_DEFAULTS:
            raise ValueError(f"unknown plan: {kwargs['plan']}")
    for quota_key in ("quota_5h", "quota_weekly"):
        if quota_key in kwargs:
            v = kwargs[quota_key]
            if not isinstance(v, int) or v < _QUOTA_MIN or v > _QUOTA_MAX:
                raise ValueError(f"{quota_key} must be int in [{_QUOTA_MIN}, {_QUOTA_MAX}]")
    for pct_key in ("alert_pct_5h", "alert_pct_weekly", "alert_pct_session"):
        if pct_key in kwargs:
            v = kwargs[pct_key]
            if not isinstance(v, int) or v < 1 or v > 99:
                raise ValueError(f"{pct_key} must be int in [1, 99]")

    for k, v in kwargs.items():
        _set_kv(db_path, k, v)
```

- [ ] **Step 4: Run tests — verify pass**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_quota -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add -f token_dashboard/quota.py tests/test_quota.py
git commit -m "feat(quota): plan config with defaults and validation"
```

---

## Task 2: Rolling window usage queries

**Files:**
- Modify: `token_dashboard/quota.py` (append)
- Test: `tests/test_quota.py` (extend)

- [ ] **Step 1: Add failing window test**

Append to `tests/test_quota.py` (inside same class or new class):

```python
from datetime import datetime, timedelta, timezone
from token_dashboard.quota import rolling_window_usage


class RollingWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "q.db")
        init_db(self.db)
        now = datetime.now(timezone.utc)
        inside = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        outside = (now - timedelta(hours=10)).isoformat().replace("+00:00", "Z")
        from token_dashboard.db import connect
        with connect(self.db) as c:
            c.executescript(f"""
            INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model,
              input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
            VALUES
              ('a1','s1','p','assistant','{inside}','claude-opus-4-7',100,50,0,0,0),
              ('a2','s1','p','assistant','{inside}','claude-opus-4-7',0,0,0,200,0),
              ('a3','s1','p','assistant','{outside}','claude-opus-4-7',9999,9999,0,0,0);
            """)
            c.commit()

    def test_5h_window_excludes_old_rows(self):
        u = rolling_window_usage(self.db, hours=5)
        self.assertEqual(u["input_tokens"], 100)
        self.assertEqual(u["output_tokens"], 50)
        self.assertEqual(u["cache_create_5m_tokens"], 200)
        self.assertEqual(u["total_billable"], 100 + 50 + 200)  # input + output + cache_create

    def test_weekly_window_includes_old_rows(self):
        u = rolling_window_usage(self.db, hours=168)
        self.assertEqual(u["input_tokens"], 100 + 9999)
```

- [ ] **Step 2: Run — verify fail**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_quota -v`
Expected: 2 new tests fail with `ImportError: cannot import name 'rolling_window_usage'`.

- [ ] **Step 3: Add `rolling_window_usage` to `quota.py`**

Append to `token_dashboard/quota.py`:

```python
def rolling_window_usage(db_path: Union[str, Path], hours: int) -> dict:
    """Tokens used in last `hours` hours, across all sessions.

    Returns dict with per-category tokens + total_billable (input + output +
    cache_create; cache_read excluded because it's discounted and doesn't
    count against the rate-limit window in the same way).
    """
    sql = """
      SELECT
        COALESCE(SUM(input_tokens), 0)           AS input_tokens,
        COALESCE(SUM(output_tokens), 0)          AS output_tokens,
        COALESCE(SUM(cache_read_tokens), 0)      AS cache_read_tokens,
        COALESCE(SUM(cache_create_5m_tokens), 0) AS cache_create_5m_tokens,
        COALESCE(SUM(cache_create_1h_tokens), 0) AS cache_create_1h_tokens
      FROM messages
      WHERE type = 'assistant'
        AND timestamp > datetime('now', ?)
    """
    with connect(db_path) as c:
        row = c.execute(sql, (f"-{hours} hours",)).fetchone()
    out = dict(row)
    out["total_billable"] = (
        out["input_tokens"]
        + out["output_tokens"]
        + out["cache_create_5m_tokens"]
        + out["cache_create_1h_tokens"]
    )
    return out
```

- [ ] **Step 4: Run — verify pass**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_quota -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add -f token_dashboard/quota.py tests/test_quota.py
git commit -m "feat(quota): rolling window token usage query"
```

---

## Task 3: Per-skill proxy token cost

**Files:**
- Create: `token_dashboard/skill_tokens.py`
- Test: `tests/test_skill_tokens.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_skill_tokens.py`:

```python
import os
import tempfile
import unittest
from unittest.mock import patch

from token_dashboard.db import init_db, connect
from token_dashboard.skill_tokens import skill_cost_proxy


class SkillProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.executescript("""
            INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
            VALUES ('a1','s1','p','assistant','2026-04-20T00:00:00Z',0,0,0,0,0);
            INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error)
            VALUES
              ('a1','s1','p','Skill','brainstorming','2026-04-20T00:00:00Z',0),
              ('a1','s1','p','Skill','brainstorming','2026-04-20T00:00:01Z',0),
              ('a1','s1','p','Skill','unknown-skill','2026-04-20T00:00:02Z',0);
            """)
            c.commit()

    def test_proxy_multiplies_invocations_by_skill_size(self):
        fake_catalog = {
            "brainstorming": {"chars": 4000, "tokens": 1000, "path": "/x/SKILL.md"},
        }
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value=fake_catalog):
            rows = skill_cost_proxy(self.db)
        brain = [r for r in rows if r["skill"] == "brainstorming"][0]
        self.assertEqual(brain["invocations"], 2)
        self.assertEqual(brain["proxy_tokens"], 2 * 1000)  # 2 × tokens
        self.assertEqual(brain["chars"], 4000)

    def test_unknown_skill_has_null_proxy(self):
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value={}):
            rows = skill_cost_proxy(self.db)
        unknown = [r for r in rows if r["skill"] == "unknown-skill"][0]
        self.assertIsNone(unknown["proxy_tokens"])
        self.assertTrue(unknown["catalog_missing"])

    def test_sorted_by_proxy_tokens_desc(self):
        fake_catalog = {
            "brainstorming": {"chars": 4000, "tokens": 1000, "path": "/x/SKILL.md"},
            "unknown-skill": {"chars": 2000, "tokens": 500, "path": "/y/SKILL.md"},
        }
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value=fake_catalog):
            rows = skill_cost_proxy(self.db)
        tokens = [r["proxy_tokens"] for r in rows]
        self.assertEqual(tokens, sorted(tokens, reverse=True))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — verify fail**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_skill_tokens -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `skill_tokens.py`**

Create `token_dashboard/skill_tokens.py`:

```python
"""Proxy token cost per skill: invocations × catalog token estimate."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .db import skill_breakdown
from .skills import cached_catalog


def skill_cost_proxy(
    db_path: Union[str, Path],
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list:
    rows = skill_breakdown(db_path, since=since, until=until)
    catalog = cached_catalog()
    out = []
    for r in rows:
        info = catalog.get(r["skill"])
        if info is None:
            out.append({
                **r,
                "proxy_tokens": None,
                "chars": None,
                "catalog_missing": True,
            })
        else:
            out.append({
                **r,
                "proxy_tokens": r["invocations"] * info["tokens"],
                "chars": info["chars"],
                "catalog_missing": False,
            })
    # Sort: known skills by proxy_tokens desc, unknown last (stable).
    out.sort(key=lambda x: (x["proxy_tokens"] is None, -(x["proxy_tokens"] or 0)))
    return out
```

- [ ] **Step 4: Run — verify pass**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_skill_tokens -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add -f token_dashboard/skill_tokens.py tests/test_skill_tokens.py
git commit -m "feat(skills): proxy token cost per skill invocation"
```

---

## Task 4: Alert evaluator

**Files:**
- Create: `token_dashboard/alerts.py`
- Test: `tests/test_alerts.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_alerts.py`:

```python
import os
import tempfile
import unittest
from unittest.mock import patch

from token_dashboard.db import init_db
from token_dashboard.alerts import evaluate_alerts
from token_dashboard.quota import set_quota_config


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "a.db")
        init_db(self.db)
        set_quota_config(self.db, plan="max", quota_5h=100_000, quota_weekly=1_000_000,
                         alert_pct_5h=75, alert_pct_weekly=80, alert_pct_session=50)

    def _usage(self, total_5h, total_weekly, current_session=0):
        return {
            "usage_5h": {"total_billable": total_5h},
            "usage_weekly": {"total_billable": total_weekly},
            "current_session_tokens": current_session,
        }

    def test_no_alerts_below_threshold(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(50_000, 500_000, 10_000)):
            self.assertEqual(evaluate_alerts(self.db), [])

    def test_warning_at_threshold(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(80_000, 200_000, 10_000)):
            alerts = evaluate_alerts(self.db)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], "warning")
        self.assertEqual(alerts[0]["key"], "5h")

    def test_critical_when_over_threshold_plus_15(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(95_000, 200_000, 10_000)):
            alerts = evaluate_alerts(self.db)
        self.assertEqual(alerts[0]["level"], "critical")

    def test_multiple_alerts(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(80_000, 850_000, 60_000)):
            alerts = evaluate_alerts(self.db)
        keys = sorted(a["key"] for a in alerts)
        self.assertEqual(keys, ["5h", "session", "weekly"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — verify fail**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_alerts -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `alerts.py`**

Create `token_dashboard/alerts.py`:

```python
"""Threshold-based alert evaluator for the dashboard banner."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from .db import connect
from .quota import get_quota_config, rolling_window_usage

CRITICAL_OFFSET_PCT = 15  # threshold + 15pp → critical


def _current_session_tokens(db_path) -> int:
    """Billable tokens for most recent session with activity in last 2 hours."""
    sql = """
      SELECT
        COALESCE(SUM(input_tokens), 0)
        + COALESCE(SUM(output_tokens), 0)
        + COALESCE(SUM(cache_create_5m_tokens), 0)
        + COALESCE(SUM(cache_create_1h_tokens), 0) AS total
      FROM messages
      WHERE type = 'assistant'
        AND session_id = (
          SELECT session_id FROM messages
          WHERE timestamp > datetime('now', '-2 hours')
          ORDER BY timestamp DESC LIMIT 1
        )
    """
    with connect(db_path) as c:
        row = c.execute(sql).fetchone()
    return row["total"] if row else 0


def _collect_usage(db_path) -> dict:
    return {
        "usage_5h": rolling_window_usage(db_path, hours=5),
        "usage_weekly": rolling_window_usage(db_path, hours=24 * 7),
        "current_session_tokens": _current_session_tokens(db_path),
    }


def _classify(pct: float, threshold_pct: int) -> str:
    if pct >= threshold_pct + CRITICAL_OFFSET_PCT:
        return "critical"
    if pct >= threshold_pct:
        return "warning"
    return "none"


def evaluate_alerts(db_path: Union[str, Path]) -> list:
    cfg = get_quota_config(db_path)
    usage = _collect_usage(db_path)

    results = []
    checks = [
        ("5h", usage["usage_5h"]["total_billable"], cfg["quota_5h"], cfg["alert_pct_5h"]),
        ("weekly", usage["usage_weekly"]["total_billable"], cfg["quota_weekly"], cfg["alert_pct_weekly"]),
        ("session", usage["current_session_tokens"], cfg["quota_5h"], cfg["alert_pct_session"]),
    ]
    for key, current, limit, threshold in checks:
        if limit <= 0:
            continue
        pct = (current / limit) * 100
        level = _classify(pct, threshold)
        if level == "none":
            continue
        results.append({
            "level": level,
            "key": key,
            "current": current,
            "limit": limit,
            "pct": round(pct, 1),
            "message": f"{key} window: {pct:.0f}% ({current:,} / {limit:,} tokens)",
        })
    return results
```

- [ ] **Step 4: Run — verify pass**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_alerts -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add -f token_dashboard/alerts.py tests/test_alerts.py
git commit -m "feat(alerts): threshold evaluator with warning/critical levels"
```

---

## Task 5: Wire new routes into server

**Files:**
- Modify: `token_dashboard/server.py`
- Test: `tests/test_server.py` (extend)

- [ ] **Step 1: Add failing server tests**

Append to `tests/test_server.py`:

```python
    def test_quota_get_returns_config_and_usage(self):
        body = json.loads(self._get("/api/quota"))
        self.assertIn("config", body)
        self.assertIn("usage_5h", body)
        self.assertIn("usage_weekly", body)
        self.assertIn("quota_5h", body["config"])

    def test_quota_config_post_persists(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/quota/config",
            data=json.dumps({"alert_pct_5h": 60}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req).read()
        body = json.loads(self._get("/api/quota"))
        self.assertEqual(body["config"]["alert_pct_5h"], 60)

    def test_quota_config_post_rejects_invalid(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/quota/config",
            data=json.dumps({"alert_pct_5h": 200}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req).read()
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_skills_cost_returns_list(self):
        body = json.loads(self._get("/api/skills_cost"))
        self.assertIsInstance(body, list)

    def test_alerts_returns_list(self):
        body = json.loads(self._get("/api/alerts"))
        self.assertIsInstance(body, list)
```

Add at top of file: `import urllib.error`.

- [ ] **Step 2: Run — verify fail**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest tests.test_server -v`
Expected: 5 new tests fail with 404.

- [ ] **Step 3: Add imports and routes to `server.py`**

In `token_dashboard/server.py` near the other `from .` imports (around line 16), add:

```python
from .quota import get_quota_config, set_quota_config, rolling_window_usage
from .skill_tokens import skill_cost_proxy
from .alerts import evaluate_alerts
```

Inside `do_GET`, before `/api/stream` block (around line 146), add:

```python
            if path == "/api/quota":
                cfg = get_quota_config(db_path)
                return _send_json(self, {
                    "config": cfg,
                    "usage_5h": rolling_window_usage(db_path, hours=5),
                    "usage_weekly": rolling_window_usage(db_path, hours=24 * 7),
                })
            if path == "/api/skills_cost":
                return _send_json(self, skill_cost_proxy(db_path, since, until))
            if path == "/api/alerts":
                return _send_json(self, evaluate_alerts(db_path))
```

Inside `do_POST`, before the `self.send_response(404)` block (around line 187), add:

```python
            if url.path == "/api/quota/config":
                try:
                    set_quota_config(db_path, **body)
                except ValueError as e:
                    return _send_error(self, 400, str(e))
                return _send_json(self, get_quota_config(db_path))
```

- [ ] **Step 4: Run all tests — verify pass**

Run: `cd ~/Desktop/Claude/token-dashboard && python3 -m unittest discover tests -v`
Expected: all tests pass (including previously-existing ones).

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add token_dashboard/server.py tests/test_server.py
git commit -m "feat(server): /api/quota, /api/skills_cost, /api/alerts routes"
```

---

## Task 6: Frontend route — Quota tab

**Files:**
- Create: `web/routes/quota.js`

- [ ] **Step 1: Create Quota route**

Create `web/routes/quota.js`:

```javascript
import { api, fmt } from '/web/app.js';

const PLANS = ['pro', 'max', 'max-20x', 'api'];

function progressBar(current, limit, alertPct) {
  const pct = limit > 0 ? Math.min(100, (current / limit) * 100) : 0;
  const over = pct >= alertPct;
  const critical = pct >= alertPct + 15;
  const cls = critical ? 'critical' : over ? 'warning' : 'ok';
  return `
    <div class="progress-row">
      <div class="progress-bar ${cls}">
        <div class="progress-fill" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <div class="progress-label">
        ${fmt.int(current)} / ${fmt.int(limit)} (${pct.toFixed(0)}%)
      </div>
    </div>
  `;
}

function settingsCard(cfg) {
  const planOptions = PLANS.map(p =>
    `<option value="${p}" ${p === cfg.plan ? 'selected' : ''}>${p}</option>`
  ).join('');
  return `
    <form id="quota-settings" class="settings-card">
      <h3>Settings</h3>
      <label>Plan <select name="plan">${planOptions}</select></label>
      <label>5h quota (tokens) <input name="quota_5h" type="number" value="${cfg.quota_5h}" min="1000" max="100000000"></label>
      <label>Weekly quota (tokens) <input name="quota_weekly" type="number" value="${cfg.quota_weekly}" min="1000" max="100000000"></label>
      <label>Alert at 5h (%) <input name="alert_pct_5h" type="number" value="${cfg.alert_pct_5h}" min="1" max="99"></label>
      <label>Alert at weekly (%) <input name="alert_pct_weekly" type="number" value="${cfg.alert_pct_weekly}" min="1" max="99"></label>
      <label>Alert per session (%) <input name="alert_pct_session" type="number" value="${cfg.alert_pct_session}" min="1" max="99"></label>
      <button type="submit">Save</button>
      <span id="quota-save-status"></span>
    </form>
  `;
}

export default async function (root) {
  root.innerHTML = '<div class="loading">Loading quota…</div>';
  const data = await api('/api/quota');
  const { config, usage_5h, usage_weekly } = data;

  root.innerHTML = `
    <h2>Quota</h2>
    <section class="quota-window">
      <h3>5-hour rolling window</h3>
      ${progressBar(usage_5h.total_billable, config.quota_5h, config.alert_pct_5h)}
    </section>
    <section class="quota-window">
      <h3>7-day rolling window</h3>
      ${progressBar(usage_weekly.total_billable, config.quota_weekly, config.alert_pct_weekly)}
    </section>
    <section>${settingsCard(config)}</section>
  `;

  const form = root.querySelector('#quota-settings');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = {};
    for (const [k, v] of fd.entries()) {
      payload[k] = k === 'plan' ? v : parseInt(v, 10);
    }
    const status = root.querySelector('#quota-save-status');
    status.textContent = 'Saving…';
    try {
      const r = await fetch('/api/quota/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const err = await r.json();
        status.textContent = 'Error: ' + (err.error || r.status);
        return;
      }
      status.textContent = 'Saved.';
      setTimeout(() => { location.reload(); }, 400);
    } catch (err) {
      status.textContent = 'Error: ' + err.message;
    }
  });
}
```

- [ ] **Step 2: Register route in `web/app.js`**

In `web/app.js`, modify the `ROUTES` object (around line 34):

```javascript
const ROUTES = {
  '/overview': () => import('/web/routes/overview.js'),
  '/quota':    () => import('/web/routes/quota.js'),
  '/prompts':  () => import('/web/routes/prompts.js'),
  '/sessions': () => import('/web/routes/sessions.js'),
  '/projects': () => import('/web/routes/projects.js'),
  '/skills':   () => import('/web/routes/skills.js'),
  '/tips':     () => import('/web/routes/tips.js'),
  '/settings': () => import('/web/routes/settings.js'),
};
```

- [ ] **Step 3: Smoke test in browser**

```bash
cd ~/Desktop/Claude/token-dashboard
# Kill any running server
lsof -ti:8080 | xargs kill 2>/dev/null
python3 cli.py dashboard &
sleep 3
# Open http://127.0.0.1:8080/#/quota in browser manually
```

Expected: Quota tab loads, shows two progress bars + settings form. Saving persists.

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add web/routes/quota.js web/app.js
git commit -m "feat(web): Quota tab with progress bars and settings"
```

---

## Task 7: Skills tab — add proxy_tokens column

**Files:**
- Modify: `web/routes/skills.js`

- [ ] **Step 1: Change endpoint from `/api/skills` to `/api/skills_cost`**

In `web/routes/skills.js` line 31, replace:

```javascript
const url = '/api/skills' + (since ? '?since=' + encodeURIComponent(since) : '');
```

with:

```javascript
const url = '/api/skills_cost' + (since ? '?since=' + encodeURIComponent(since) : '');
```

- [ ] **Step 2: Update table header (line 64-70)**

Replace:

```javascript
        <thead><tr>
          <th>skill</th>
          <th class="num">invocations</th>
          <th class="num">tokens per call</th>
          <th class="num">sessions</th>
          <th>last used</th>
        </tr></thead>
```

with:

```javascript
        <thead><tr>
          <th>skill</th>
          <th class="num">invocations</th>
          <th class="num">tokens per call</th>
          <th class="num">proxy tokens</th>
          <th class="num">sessions</th>
          <th>last used</th>
        </tr></thead>
```

- [ ] **Step 3: Update table rows (line 72-79)**

Replace:

```javascript
          ${skills.map(s => `
            <tr>
              <td><span class="badge">${fmt.htmlSafe(s.skill)}</span></td>
              <td class="num">${fmt.int(s.invocations)}</td>
              <td class="num">${s.tokens_per_call == null ? '<span class="muted">—</span>' : fmt.int(s.tokens_per_call)}</td>
              <td class="num">${fmt.int(s.sessions)}</td>
              <td class="mono">${fmt.ts(s.last_used)}</td>
            </tr>`).join('') || '<tr><td colspan="5" class="muted">no skills invoked in this range</td></tr>'}
```

with:

```javascript
          ${skills.map(s => `
            <tr>
              <td><span class="badge">${fmt.htmlSafe(s.skill)}</span></td>
              <td class="num">${fmt.int(s.invocations)}</td>
              <td class="num">${s.chars == null ? '<span class="muted">—</span>' : fmt.int(Math.floor(s.chars / 4))}</td>
              <td class="num">${s.proxy_tokens == null ? '<span class="muted">—</span>' : fmt.int(s.proxy_tokens)}</td>
              <td class="num">${fmt.int(s.sessions)}</td>
              <td class="mono">${fmt.ts(s.last_used)}</td>
            </tr>`).join('') || '<tr><td colspan="6" class="muted">no skills invoked in this range</td></tr>'}
```

- [ ] **Step 4: Update note paragraph (line 62)**

Replace:

```javascript
        <p class="muted" style="margin:-4px 0 14px;font-size:12px">"Tokens per call" is the size of the skill's <code>SKILL.md</code> file — what Claude Code loads into context each time the skill is invoked.</p>
```

with:

```javascript
        <p class="muted" style="margin:-4px 0 14px;font-size:12px">"Tokens per call" = skill SKILL.md size. "Proxy tokens" = invocations × tokens per call. Proxy excludes downstream tool calls triggered by the skill.</p>
```

- [ ] **Step 5: Smoke test in browser**

Reload `http://127.0.0.1:8080/#/skills` — verify `proxy tokens` column shows numbers for known skills and `—` for unknown.

- [ ] **Step 6: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add web/routes/skills.js
git commit -m "feat(web): per-skill proxy token column in Skills tab"
```

---

## Task 8: Alert banner — poll + display

**Files:**
- Modify: `web/app.js`, `web/index.html`

- [ ] **Step 1: Add banner element to `index.html`**

Read current `web/index.html` first:

```bash
cat ~/Desktop/Claude/token-dashboard/web/index.html
```

Locate the `<body>` or main layout section and insert directly inside `<body>`, before any other content:

```html
<div id="alert-banner" class="alert-banner hidden" role="alert"></div>
```

- [ ] **Step 2: Add banner update function in `app.js`**

In `web/app.js`, append near the end of the file (before any `init()` call):

```javascript
async function refreshBanner() {
  const el = document.getElementById('alert-banner');
  if (!el) return;
  try {
    const alerts = await api('/api/alerts');
    if (!alerts.length) {
      el.classList.add('hidden');
      el.innerHTML = '';
      return;
    }
    const top = alerts.find(a => a.level === 'critical') || alerts[0];
    el.className = 'alert-banner ' + top.level;
    el.innerHTML = `<strong>${top.level === 'critical' ? '⛔' : '⚠'} ${top.message}</strong>`;
    el.classList.remove('hidden');
  } catch (e) {
    // Silent fail; banner stays hidden.
  }
}

// Poll on load and every 30s
refreshBanner();
setInterval(refreshBanner, 30_000);
```

- [ ] **Step 3: Smoke test banner shows**

To trigger a banner, POST a low threshold:

```bash
curl -X POST http://127.0.0.1:8080/api/quota/config \
  -H 'Content-Type: application/json' \
  -d '{"alert_pct_5h": 1}'
```

Reload page — yellow banner should appear at top. Restore threshold:

```bash
curl -X POST http://127.0.0.1:8080/api/quota/config \
  -H 'Content-Type: application/json' \
  -d '{"alert_pct_5h": 75}'
```

Reload — banner disappears.

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add web/app.js web/index.html
git commit -m "feat(web): alert banner with 30s polling"
```

---

## Task 9: Banner + progress bar + settings CSS

**Files:**
- Modify: `web/style.css`

- [ ] **Step 1: Append styles**

Append to `web/style.css`:

```css
/* Alert banner */
.alert-banner {
  padding: 10px 16px;
  font-size: 14px;
  border-bottom: 1px solid rgba(0,0,0,0.1);
}
.alert-banner.hidden { display: none; }
.alert-banner.warning { background: #fff4d6; color: #7a5200; border-color: #e8c569; }
.alert-banner.critical { background: #fde2e1; color: #8a1c1a; border-color: #e89c99; }

/* Progress bar */
.progress-row { display: flex; align-items: center; gap: 12px; margin: 8px 0 16px; }
.progress-bar { flex: 1; height: 16px; background: #eee; border-radius: 8px; overflow: hidden; position: relative; }
.progress-fill { height: 100%; background: #4caf50; transition: width 0.3s; }
.progress-bar.warning .progress-fill { background: #f5a623; }
.progress-bar.critical .progress-fill { background: #e53935; }
.progress-label { min-width: 220px; text-align: right; font-variant-numeric: tabular-nums; font-size: 13px; }

/* Settings card */
.settings-card { display: grid; gap: 10px; max-width: 500px; padding: 16px; background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; margin-top: 20px; }
.settings-card label { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 13px; }
.settings-card input, .settings-card select { flex: 0 0 180px; padding: 4px 6px; font-family: inherit; }
.settings-card button { width: 100px; padding: 6px; cursor: pointer; }
#quota-save-status { font-size: 13px; color: #666; }

.quota-window { margin-bottom: 24px; }
.quota-window h3 { margin: 0 0 6px; font-size: 14px; color: #555; }

.muted { color: #888; }
.small { font-size: 12px; }
```

- [ ] **Step 2: Visual check in browser**

Reload `http://127.0.0.1:8080/#/quota` — progress bars should have rounded corners, green fill by default, yellow/red when over threshold. Settings card should look like a bordered card.

- [ ] **Step 3: Commit**

```bash
cd ~/Desktop/Claude/token-dashboard
git add web/style.css
git commit -m "style(web): banner, progress bar, settings card"
```

---

## Task 10: Navigation link for Quota tab

**Files:**
- Modify: `web/app.js` (if topbar is built dynamically — it is, per line 50)

The topbar is auto-generated from `ROUTES` keys (see `buildTopbar` in `app.js:44`). Since Task 6 already added `/quota` to `ROUTES`, the nav link already appears. No further edit needed.

- [ ] **Step 1: Verify nav has "quota" link**

Reload dashboard. Top nav bar should show: overview, quota, prompts, sessions, projects, skills, tips, settings.

- [ ] **Step 2: No commit needed (verification only)**

---

## Task 11: Manual smoke test — full acceptance

- [ ] **Step 1: Full test pass**

```bash
cd ~/Desktop/Claude/token-dashboard
python3 -m unittest discover tests -v
```

Expected: all tests pass (original + new).

- [ ] **Step 2: Dashboard smoke**

1. Stop any running dashboard: `lsof -ti:8080 | xargs kill 2>/dev/null`
2. Start fresh: `python3 cli.py dashboard &`
3. Open `http://127.0.0.1:8080` in browser
4. Click **Quota** — verify progress bars + settings
5. Edit `alert_pct_5h` to `5`, Save — verify banner appears
6. Click **Skills** — verify proxy_tokens column shows values
7. Restore `alert_pct_5h` to `75`, Save — verify banner disappears
8. Hard reload (Cmd+Shift+R) — verify settings persisted

- [ ] **Step 3: Final commit (if any leftover changes)**

```bash
cd ~/Desktop/Claude/token-dashboard
git status
# If anything left unstaged:
git add -A && git commit -m "chore: smoke test fixes"
```

- [ ] **Step 4: Summary commit log**

```bash
git log --oneline main..customizations-20260424
```

Expected: 8–10 commits covering quota config, rolling window, skill proxy, alerts, server routes, Quota tab, Skills update, banner, CSS, and docs.

---

## Self-Review Notes

Spec coverage verified:
- §3.1 `quota.py` → Tasks 1, 2 ✓
- §3.1 `skill_tokens.py` → Task 3 ✓
- §3.1 `alerts.py` → Task 4 ✓
- §3.2 server routes → Task 5 ✓
- §3.3 Quota tab → Task 6 ✓
- §3.3 Skills tab → Task 7 ✓
- §3.3 Banner → Tasks 8, 9 ✓
- §5.1 UX → Task 6 (progress bars + settings card) ✓
- §6 validation → Task 1 (test_validation_rejects_*) ✓
- §7 error handling → Task 5 (400 on invalid config) ✓
- §8 testing → Tasks 1–5 (unit tests), Task 11 (manual smoke) ✓

Deferred per spec §10:
- Delta-based precise skill attribution — not in this plan
- macOS notifications / daily-note append — not in this plan
- Chart overlays in Quota tab — skipped for phase 1 (progress bars cover the core signal; charts can be added after user feedback)

Type consistency check:
- `rolling_window_usage(db_path, hours)` used in quota.py, alerts.py, and server.py — same signature ✓
- `evaluate_alerts(db_path)` returns list in all usages ✓
- `skill_cost_proxy(db_path, since, until)` signature consistent ✓

Scope note: This plan omits standalone chart additions (spec §5.1 mentions "tokens per hour, last 24h, with quota line overlay" and "tokens per day, last 14 days"). Progress bars cover the critical user need (how close to quota). Charts are a phase-1.5 enhancement once signal-over-noise is confirmed.
