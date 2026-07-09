"""Hooks / commands / agents catalog — the smaller 'operational' gallery.

Hook dedup + broken-script detection mirrors ~/.claude/scripts/system-register.py's
collect_hooks(), reimplemented here (stdlib-only, no dependency on that script).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_JSON = CLAUDE_DIR / "settings.json"
COMMANDS_DIR = CLAUDE_DIR / "commands"

_SCRIPT_RE = re.compile(r"(/[^\s\"']+\.(?:sh|py|mjs|js))")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def scan_hooks() -> list:
    settings = _read_json(SETTINGS_JSON)
    out = []
    seen = set()
    for event, entries in (settings.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                m = _SCRIPT_RE.search(cmd)
                path = m.group(1) if m else None
                key = path or cmd
                if key in seen:
                    continue
                seen.add(key)
                exists = Path(path).is_file() if path else True
                out.append({
                    "name": Path(path).name if path else f"{event} (inline)",
                    "event": event,
                    "file_path": path,
                    "status": "ok" if exists else "broken — script missing",
                })
    return out


def scan_commands() -> list:
    out = []
    if COMMANDS_DIR.is_dir():
        for f in sorted(COMMANDS_DIR.glob("*.md")):
            out.append({"name": f.stem, "file_path": str(f), "source": "user"})
    from .plugins import cached_plugins
    for p in cached_plugins():
        if not p["install_path"]:
            continue
        cmd_dir = Path(p["install_path"]) / "commands"
        if cmd_dir.is_dir():
            for f in sorted(cmd_dir.rglob("*.md")):
                out.append({"name": f.stem, "file_path": str(f), "source": p["name"]})
    return out


def scan_agents() -> list:
    out = []
    from .plugins import cached_plugins
    for p in cached_plugins():
        if not p["install_path"]:
            continue
        agents_dir = Path(p["install_path"]) / "agents"
        if agents_dir.is_dir():
            for f in sorted(agents_dir.glob("*.md")):
                out.append({"name": f.stem, "file_path": str(f), "source": p["name"]})
    return out
