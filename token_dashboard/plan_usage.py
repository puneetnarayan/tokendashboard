"""Mirror Claude Code's `/status` Plan usage panel.

Calls Anthropic's internal `/api/oauth/usage` endpoint using OAuth tokens
bootstrapped from macOS Keychain (`Claude Code-credentials` service).
Maintains its own token store in the `plan` KV table to avoid racing with
Claude Code's in-memory state — never writes back to Keychain or
~/.claude/.credentials.json.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Union

from .db import connect

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
USER_AGENT = "claude-cli/2.1.111 (cli, native)"
BETA_HEADER = "oauth-2025-04-20"

# Cache TTL for the usage response — Anthropic's data updates per request,
# but we don't want to spam the endpoint on every dashboard refresh.
CACHE_TTL_SEC = 60


class PlanUsageError(Exception):
    pass


class NoCredentialsError(PlanUsageError):
    pass


def _kv_get(db_path, key, default=None):
    with connect(db_path) as c:
        row = c.execute("SELECT v FROM plan WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def _kv_set(db_path, key, value):
    with connect(db_path) as c:
        c.execute("INSERT OR REPLACE INTO plan (k, v) VALUES (?, ?)", (key, str(value)))
        c.commit()


def _kv_del(db_path, key):
    with connect(db_path) as c:
        c.execute("DELETE FROM plan WHERE k=?", (key,))
        c.commit()


def _read_keychain() -> Optional[dict]:
    """Return claudeAiOauth dict from macOS Keychain, or None on failure."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("claudeAiOauth")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _read_credentials_json() -> Optional[dict]:
    """Fallback: read ~/.claude/.credentials.json."""
    p = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("claudeAiOauth")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _bootstrap_tokens(db_path):
    """Seed the dashboard's KV store from Keychain or credentials.json on first run."""
    if _kv_get(db_path, "pu_access_token"):
        return  # already bootstrapped
    creds = _read_keychain() or _read_credentials_json()
    if not creds:
        raise NoCredentialsError(
            "Couldn't read OAuth tokens from Keychain or ~/.claude/.credentials.json. "
            "Run Claude Code at least once and authenticate (/login)."
        )
    _kv_set(db_path, "pu_access_token", creds["accessToken"])
    _kv_set(db_path, "pu_refresh_token", creds["refreshToken"])
    _kv_set(db_path, "pu_expires_at", creds["expiresAt"])  # ms epoch


def _refresh_token(db_path) -> str:
    """POST refresh_token, store new pair, return new access_token."""
    rt = _kv_get(db_path, "pu_refresh_token")
    if not rt:
        raise PlanUsageError("No refresh token available — re-bootstrap from Keychain")
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "anthropic-beta": BETA_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise PlanUsageError(f"Token refresh failed (HTTP {e.code}): {body}")
    except urllib.error.URLError as e:
        raise PlanUsageError(f"Token refresh network error: {e}")
    new_access = data["access_token"]
    new_refresh = data["refresh_token"]
    expires_in = int(data.get("expires_in", 28800))
    expires_at_ms = int(time.time() * 1000) + expires_in * 1000
    _kv_set(db_path, "pu_access_token", new_access)
    _kv_set(db_path, "pu_refresh_token", new_refresh)
    _kv_set(db_path, "pu_expires_at", expires_at_ms)
    return new_access


def _get_valid_access_token(db_path) -> str:
    """Return a non-expired access token, refreshing if needed."""
    _bootstrap_tokens(db_path)
    expires_at = int(_kv_get(db_path, "pu_expires_at", "0"))
    now_ms = int(time.time() * 1000)
    # Refresh ~60s before expiry to avoid edge races.
    if expires_at - now_ms < 60_000:
        return _refresh_token(db_path)
    token = _kv_get(db_path, "pu_access_token")
    if not token:
        raise PlanUsageError("Token store inconsistent")
    return token


def _fetch_usage_raw(token: str) -> dict:
    req = urllib.request.Request(
        USAGE_URL,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "anthropic-beta": BETA_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        if e.code == 401:
            raise PlanUsageError(f"401 unauthorized — token may be invalid: {body}")
        raise PlanUsageError(f"Usage fetch failed (HTTP {e.code}): {body}")
    except urllib.error.URLError as e:
        raise PlanUsageError(f"Usage fetch network error: {e}")


def get_plan_usage(db_path: Union[str, Path], force: bool = False) -> dict:
    """Return cached or fresh usage payload. Returns:
        {"data": <usage payload>, "fetched_at": <ms epoch>, "cached": bool}
    Raises PlanUsageError on hard failure (no creds, refresh failure).
    """
    last_ts_str = _kv_get(db_path, "pu_last_fetch_ts")
    last_resp_str = _kv_get(db_path, "pu_last_response_json")
    if not force and last_ts_str and last_resp_str:
        last_ts = int(last_ts_str)
        if (int(time.time() * 1000) - last_ts) < CACHE_TTL_SEC * 1000:
            return {
                "data": json.loads(last_resp_str),
                "fetched_at": last_ts,
                "cached": True,
            }
    token = _get_valid_access_token(db_path)
    try:
        data = _fetch_usage_raw(token)
    except PlanUsageError as e:
        # On 401, try one refresh + retry
        if "401" in str(e):
            token = _refresh_token(db_path)
            data = _fetch_usage_raw(token)
        else:
            raise
    fetched_at = int(time.time() * 1000)
    _kv_set(db_path, "pu_last_fetch_ts", fetched_at)
    _kv_set(db_path, "pu_last_response_json", json.dumps(data))
    return {"data": data, "fetched_at": fetched_at, "cached": False}


def reset_token_store(db_path):
    """Clear the dashboard's stored tokens — forces re-bootstrap from Keychain."""
    for key in ("pu_access_token", "pu_refresh_token", "pu_expires_at",
                "pu_last_fetch_ts", "pu_last_response_json"):
        _kv_del(db_path, key)
