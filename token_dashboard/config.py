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
    custom_models: dict = field(default_factory=dict)

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


def _config_path() -> Path:
    return Path(os.environ.get("TOKEN_DASHBOARD_CONFIG") or str(_DEFAULT_PATH))


def load() -> Config:
    """Load config. Missing/corrupt file returns defaults."""
    p = _config_path()
    cfg = Config()
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        ints = raw.get("integrations") or {}
        cfg.integrations.rtk    = str(ints.get("rtk",    "auto"))
        cfg.integrations.lavish = str(ints.get("lavish", "auto"))
        cfg.custom_models       = raw.get("custom_models") or {}
    except Exception:
        pass  # corrupt config → silently use defaults
    return cfg


def save(cfg: Config) -> None:
    """Persist config. Merges with existing file to avoid clobbering unknown keys."""
    p = _config_path()
    try:
        existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        existing = {}
    existing["custom_models"] = cfg.custom_models
    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
