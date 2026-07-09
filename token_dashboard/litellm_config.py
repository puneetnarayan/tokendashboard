"""LiteLLM credential discovery and connection validation."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import ssl
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

log = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".claude" / "token-dashboard-config.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# (origin, key_prefix) -> (state, user_info|None)
_validation_cache: dict = {}


def discover() -> Optional[dict]:
    """Return {"base_url": str, "api_key": str, "source": str} or None.

    Resolution order: manual config > env vars > settings.json env block.
    """
    # 1. Manual override
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        ll = raw.get("litellm", {})
        if ll.get("enabled") is False:
            return None
        if ll.get("base_url") and ll.get("api_key"):
            return {
                "base_url": _normalize_origin(ll["base_url"]),
                "api_key": ll["api_key"],
                "source": "manual",
            }
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        pass

    # 2. Process environment
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if base and key:
        return {"base_url": _normalize_origin(base), "api_key": key, "source": "env"}

    # 3. ~/.claude/settings.json env block
    try:
        settings = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        env_block = settings.get("env", {})
        base = env_block.get("ANTHROPIC_BASE_URL", "")
        key = env_block.get("ANTHROPIC_AUTH_TOKEN", "")
        if base and key:
            return {
                "base_url": _normalize_origin(base),
                "api_key": key,
                "source": "settings_json",
            }
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        pass

    return None


def validate(config: dict) -> tuple[str, Optional[dict]]:
    """GET /user/info against the proxy. Returns (state, user_info).

    States: "active" | "invalid_key" | "unreachable"
    Result cached in memory per (origin, key_prefix).
    """
    origin = config["base_url"]
    key = config["api_key"]
    cache_key = (origin, _key_prefix(key))
    if cache_key in _validation_cache:
        return _validation_cache[cache_key]
    result = _call_user_info(origin, key)
    _validation_cache[cache_key] = result
    return result


def clear_cache() -> None:
    _validation_cache.clear()


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) < 5:
        return "sk-…"
    return "sk-…" + key[-4:]


# ── internals ──────────────────────────────────────────────────────────────

def _normalize_origin(url: str) -> str:
    """Strip path/query — keep scheme://host[:port] only."""
    p = urlparse(url.strip())
    if not p.scheme:
        p = urlparse("https://" + url.strip())
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{p.hostname}{port}"


def _key_prefix(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _call_user_info(origin: str, key: str) -> tuple[str, Optional[dict]]:
    from datetime import date
    today = date.today().isoformat()
    url = f"{origin}/user/daily/activity?start_date={today}&end_date={today}"
    req = Request(url, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "token-dashboard",
    })
    try:
        with urlopen(req, timeout=5, context=_SSL_CTX) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return ("active", body)
    except HTTPError as e:
        if e.code in (401, 403):
            return ("invalid_key", None)
        return ("unreachable", None)
    except (URLError, json.JSONDecodeError, OSError):
        return ("unreachable", None)
