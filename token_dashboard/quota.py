"""Plan quota: config KV helpers, defaults, rolling window queries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def rolling_window_usage(db_path: Union[str, Path], hours: int) -> dict:
    """Tokens used in last `hours` hours, across all sessions.

    Returns dict with per-category tokens + total_billable (input + output +
    cache_create; cache_read excluded because it's discounted and doesn't
    count against the rate-limit window in the same way).
    """
    # Compute cutoff in Python as ISO-8601 with 'T' separator and 'Z' suffix
    # so it sorts lexicographically against timestamps stored by the scanner.
    # (SQLite's datetime('now', '-N hours') uses a space separator, which would
    # compare incorrectly against 'T'-separated ISO strings.)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")
    sql = """
      SELECT
        COALESCE(SUM(input_tokens), 0)           AS input_tokens,
        COALESCE(SUM(output_tokens), 0)          AS output_tokens,
        COALESCE(SUM(cache_read_tokens), 0)      AS cache_read_tokens,
        COALESCE(SUM(cache_create_5m_tokens), 0) AS cache_create_5m_tokens,
        COALESCE(SUM(cache_create_1h_tokens), 0) AS cache_create_1h_tokens
      FROM messages
      WHERE type = 'assistant'
        AND timestamp > ?
    """
    with connect(db_path) as c:
        row = c.execute(sql, (cutoff,)).fetchone()
    out = dict(row)
    out["total_billable"] = (
        out["input_tokens"]
        + out["output_tokens"]
        + out["cache_create_5m_tokens"]
        + out["cache_create_1h_tokens"]
    )
    return out
