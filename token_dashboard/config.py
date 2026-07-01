"""Per-machine integration config at ~/.claude/token-dashboard-config.json.

Precedence: TOKEN_DASHBOARD_CONFIG env var > default path.
Missing or unreadable file → defaults (fail-open, never errors).

Integration flags are tri-state strings:
  "auto"  — enable when the tool is detected (default)
  "true"  — force enable
  "false" — force disable
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".claude" / "token-dashboard-config.json"


@dataclass
class Integrations:
    rtk: str = "auto"
    lavish: str = "auto"


@dataclass
class Config:
    integrations: Integrations = field(default_factory=Integrations)

    def rtk_enabled(self, rtk_installed: bool) -> bool:
        v = self.integrations.rtk
        if v == "true":
            return True
        if v == "false":
            return False
        return rtk_installed  # "auto": follow detection

    def lavish_enabled(self) -> bool:
        v = self.integrations.lavish
        if v == "true":
            return True
        if v == "false":
            return False
        return shutil.which("npx") is not None  # "auto": present if npx available


def load() -> Config:
    """Load config. Missing/corrupt file returns defaults."""
    p = Path(os.environ.get("TOKEN_DASHBOARD_CONFIG") or str(_DEFAULT_PATH))
    cfg = Config()
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        ints = raw.get("integrations") or {}
        cfg.integrations.rtk    = str(ints.get("rtk",    "auto"))
        cfg.integrations.lavish = str(ints.get("lavish", "auto"))
    except Exception:
        pass  # corrupt config → silently use defaults
    return cfg
