"""Threshold-based alert evaluator for the dashboard banner."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from .db import connect
from .quota import get_quota_config, rolling_window_usage

CRITICAL_OFFSET_PCT = 15  # threshold + 15pp → critical


def _current_session_tokens(db_path) -> int:
    """Billable tokens for most recent session with activity in last 2 hours."""
    # See quota.rolling_window_usage — must compute cutoff in Python as
    # ISO-8601 with 'T' separator + millisecond precision + 'Z' suffix so it
    # compares lexicographically against stored timestamps. SQLite
    # datetime('now', ...) uses a space separator and would give wrong results.
    from datetime import datetime, timedelta, timezone
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
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
          WHERE timestamp > ?
          ORDER BY timestamp DESC LIMIT 1
        )
    """
    with connect(db_path) as c:
        row = c.execute(sql, (cutoff,)).fetchone()
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
