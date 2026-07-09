"""MCP server catalog: local servers from ~/.claude.json + known account-level connectors.

Account-level connectors (Gmail, Calendar, Notion, Drive, Supabase...) have no
local config file to scan — their capabilities can't be discovered
automatically. `mcp_known_connectors.json` is hand-maintained: update it when
you connect/disconnect one on claude.ai. This is a deliberate limitation
(see design spec section 7), not an oversight.
"""
from __future__ import annotations

import json
from pathlib import Path

CLAUDE_JSON = Path.home() / ".claude.json"
KNOWN_CONNECTORS_JSON = Path(__file__).resolve().parent.parent / "mcp_known_connectors.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _local_servers() -> list:
    data = _read_json(CLAUDE_JSON)
    seen: dict = {}
    for name, cfg in (data.get("mcpServers") or {}).items():
        seen.setdefault(name, cfg)
    for proj in (data.get("projects") or {}).values():
        for name, cfg in (proj.get("mcpServers") or {}).items():
            seen.setdefault(name, cfg)
    out = []
    for name, cfg in seen.items():
        out.append({
            "name": name,
            "kind": "local",
            "transport": "http" if "url" in cfg else "stdio",
            "command_or_url": cfg.get("url") or cfg.get("command", ""),
            "description": "",
            "status": "configured",
            "file_path": str(CLAUDE_JSON),
        })
    return out


def _known_connectors() -> list:
    data = _read_json(KNOWN_CONNECTORS_JSON)
    return [{**c, "kind": "account", "file_path": None} for c in data.get("connectors", [])]


def scan_mcp() -> list:
    out = _local_servers() + _known_connectors()
    out.sort(key=lambda s: s["name"])
    return out
