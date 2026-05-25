"""Aggregate /lanes invocation logs into a small payload for the dashboard.

Each per-machine JSONL file (jlap.jsonl / jpc.jsonl / jstation.jsonl /
nanobanana.jsonl) is produced by ~/.claude/skills/lanes/scripts/lanes.sh
on every routed call. Schema (subset we use):
    ts, machine, lane, ai, model, exit_code, duration_ms, tokens_used

The lanes-dashboard at lanes.local.theinbtw.com renders per-call detail;
this module returns the high-level totals the token-dashboard surfaces.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Tuple


def _candidate_log_dirs() -> List[Path]:
    home = Path.home()
    return [
        # Synced via scripts/sync-remote.sh from Jstation:
        home / ".cache" / "token-dashboard" / "lanes-logs",
        # Same-host (if you ever run the dashboard on Jstation):
        Path("/mnt/data/lanes-logs"),
    ]


def _iter_log_files() -> List[Path]:
    out: List[Path] = []
    for d in _candidate_log_dirs():
        if d.is_dir():
            out.extend(sorted(p for p in d.glob("*.jsonl") if p.is_file()))
    return out


def lanes_stats(since_iso: Optional[str] = None) -> dict:
    """Return aggregate lane usage. since_iso filters records by ts >= since."""
    files = _iter_log_files()
    total = 0
    by_lane: dict = {}
    by_ai: dict = {}
    by_machine: dict = {}
    by_model: dict = {}
    duration_sum = 0
    tokens_sum = 0
    errors = 0
    latest_ts: Optional[str] = None

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("ts") or ""
                    if since_iso and ts and ts < since_iso:
                        continue
                    total += 1
                    if ts and (latest_ts is None or ts > latest_ts):
                        latest_ts = ts
                    lane = rec.get("lane") or "unknown"
                    ai = rec.get("ai") or "unknown"
                    machine = rec.get("machine") or fp.stem
                    model = rec.get("model") or "unknown"
                    by_lane[lane] = by_lane.get(lane, 0) + 1
                    by_ai[ai] = by_ai.get(ai, 0) + 1
                    by_machine[machine] = by_machine.get(machine, 0) + 1
                    by_model[model] = by_model.get(model, 0) + 1
                    try:
                        duration_sum += int(rec.get("duration_ms") or 0)
                    except (TypeError, ValueError):
                        pass
                    try:
                        t = rec.get("tokens_used")
                        if t is not None:
                            tokens_sum += int(t)
                    except (TypeError, ValueError):
                        pass
                    if rec.get("exit_code") not in (None, 0):
                        errors += 1
        except OSError:
            continue

    def _sorted(d):
        return [{"key": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])]

    return {
        "total": total,
        "errors": errors,
        "tokens_used_sum": tokens_sum,
        "duration_ms_sum": duration_sum,
        "latest_ts": latest_ts,
        "by_lane": _sorted(by_lane),
        "by_ai": _sorted(by_ai),
        "by_machine": _sorted(by_machine),
        "by_model": _sorted(by_model),
        "log_files": [str(p) for p in files],
        "dashboard_url": "http://lanes.local.theinbtw.com",
    }
