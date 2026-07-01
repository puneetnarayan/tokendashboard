"""RTK (Rust Token Killer) integration — fetch stats via `rtk gain --all --format json`.

Identity check: rtk-ai's RTK returns a JSON object with `summary.total_saved`.
Rust Type Kit (the other `rtk` on crates.io) will not; it is treated as absent.

Status values returned:
  "absent"  — binary not found, non-zero exit, or JSON does not match expected shape
  "no_data" — correct RTK installed but no commands tracked yet
  "ok"      — installed and has data
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional


def stats() -> Optional[dict]:
    """Return RTK stats dict or None on unexpected error (e.g. timeout)."""
    try:
        result = subprocess.run(
            ["rtk", "gain", "--all", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return {"installed": False, "status": "absent"}
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return {"installed": False, "status": "absent"}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"installed": False, "status": "absent"}

    # Identity check: must be rtk-ai's RTK (Rust Type Kit will not produce this)
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, dict) or "total_saved" not in summary:
        return {"installed": False, "status": "absent"}

    if summary.get("total_commands", 0) == 0:
        return {"installed": True, "status": "no_data"}

    return {
        "installed":      True,
        "status":         "ok",
        "total_commands": summary["total_commands"],
        "total_input":    summary["total_input"],
        "total_output":   summary["total_output"],
        "total_saved":    summary["total_saved"],
        "efficiency_pct": summary["avg_savings_pct"],
        "daily":          data.get("daily", [])[-7:],
    }
