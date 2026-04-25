"""Read Claude Code's statusline JSON mirror.

`~/.claude/context-status.sh` is patched to write the per-turn statusline
JSON payload to two files:
- last-status.json (overwritten each turn — current state)
- status-history.jsonl (append-only, trimmed to ~2000 lines)

This module exposes those files via simple readers. Failures (file missing,
bad JSON) return None / [] — the dashboard should render a "no data yet"
placeholder rather than 500.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_LAST_STATUS = Path.home() / ".claude" / "last-status.json"
DEFAULT_HISTORY = Path.home() / ".claude" / "status-history.jsonl"


def read_current_status(path: Optional[Path] = None) -> Optional[dict]:
    p = path or DEFAULT_LAST_STATUS
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def read_history(path: Optional[Path] = None, limit: int = 200) -> list:
    """Return the last `limit` history entries, oldest first.

    Each entry is {"ts": str, "payload": dict}. Lines that fail to parse
    are silently skipped (defensive — corrupt tail from concurrent write).
    """
    p = path or DEFAULT_HISTORY
    try:
        text = p.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    lines = text.strip().split("\n")
    if not lines or lines == [""]:
        return []
    if limit > 0:
        lines = lines[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
