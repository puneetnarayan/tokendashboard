"""LiteLLM proxy API client — stdlib urllib only."""
from __future__ import annotations

import json
import logging
import ssl
from datetime import date, timedelta
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "token-dashboard"}
_TIMEOUT = 10


def get_user_info(origin: str, api_key: str) -> Optional[dict]:
    return _get(origin, api_key, "/user/info")


def get_key_info(origin: str, api_key: str) -> Optional[dict]:
    return _get(origin, api_key, "/key/info")


def get_daily_activity(
    origin: str,
    api_key: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch /user/daily/activity for [start_date, end_date] (YYYY-MM-DD).

    Handles pagination; caps each request to 90-day windows.
    Returns list of normalised day dicts:
      {date, spend, prompt_tokens, completion_tokens, total_tokens,
       api_requests, models: {model: {...}}}
    """
    rows = []
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    # chunk into ≤90-day slices
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=89), end)
        rows.extend(_fetch_daily_chunk(origin, api_key, chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    return rows


# ── internals ──────────────────────────────────────────────────────────────

def _get(origin: str, api_key: str, path: str) -> Optional[dict]:
    url = origin + path
    req = Request(url, headers={**_HEADERS, "Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError, OSError) as e:
        log.debug("litellm %s failed: %s", path, type(e).__name__)
        return None


def _fetch_daily_chunk(
    origin: str, api_key: str, start: date, end: date
) -> list[dict]:
    params = urlencode({"start_date": start.isoformat(), "end_date": end.isoformat()})
    url = f"{origin}/user/daily/activity?{params}"
    req = Request(url, headers={**_HEADERS, "Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError, OSError) as e:
        log.debug("litellm daily fetch failed: %s", type(e).__name__)
        return []

    results = body.get("results") or []
    rows = []
    for entry in results:
        try:
            row = _parse_day(entry)
        except Exception as e:
            log.warning("litellm: skipping malformed daily entry: %s", e)
            continue
        rows.append(row)

    # follow pagination if present
    meta = body.get("metadata") or {}
    total_pages = int(meta.get("total_pages") or 1)
    current_page = int(meta.get("current_page") or 1)
    if total_pages > current_page:
        for page in range(current_page + 1, total_pages + 1):
            paged = _fetch_daily_page(origin, api_key, start, end, page)
            rows.extend(paged)

    return rows


def _fetch_daily_page(
    origin: str, api_key: str, start: date, end: date, page: int
) -> list[dict]:
    params = urlencode({
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "page": page,
    })
    url = f"{origin}/user/daily/activity?{params}"
    req = Request(url, headers={**_HEADERS, "Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError, OSError):
        return []
    rows = []
    for entry in (body.get("results") or []):
        try:
            rows.append(_parse_day(entry))
        except Exception as e:
            log.warning("litellm: skipping malformed daily entry on page %d: %s", page, e)
    return rows


def _parse_day(entry) -> dict:
    """Parse one results[] entry. Raises on non-dict or missing date."""
    if not isinstance(entry, dict):
        raise ValueError(f"expected dict, got {type(entry).__name__}")
    d = entry["date"]  # raises KeyError if missing — caller logs + skips

    # Some LiteLLM versions nest metrics under entry["metrics"]; others put
    # them at the entry root. Fall back to entry itself if "metrics" is absent.
    metrics = entry.get("metrics") or entry
    row = {
        "date": d,
        "spend": float(metrics.get("spend") or 0),
        "prompt_tokens": int(metrics.get("prompt_tokens") or 0),
        "completion_tokens": int(metrics.get("completion_tokens") or 0),
        "total_tokens": int(metrics.get("total_tokens") or 0),
        "api_requests": int(metrics.get("api_requests") or 0),
        "models": {},
    }

    breakdown = entry.get("breakdown") or {}
    for model, mv in (breakdown.get("models") or {}).items():
        if not isinstance(mv, dict):
            continue
        mm = mv.get("metrics") or mv  # newer LiteLLM nests values under "metrics"
        row["models"][model] = {
            "spend": float(mm.get("spend") or 0),
            "prompt_tokens": int(mm.get("prompt_tokens") or 0),
            "completion_tokens": int(mm.get("completion_tokens") or 0),
            "total_tokens": int(mm.get("total_tokens") or 0),
            "api_requests": int(mm.get("api_requests") or 0),
        }

    row["keys"] = {}
    for key_hash, kv in (breakdown.get("api_keys") or {}).items():
        if not isinstance(kv, dict):
            continue
        km = kv.get("metrics") or kv
        row["keys"][key_hash] = {
            "alias": kv.get("alias") or kv.get("key_alias") or None,
            "spend": float(km.get("spend") or 0),
            "prompt_tokens": int(km.get("prompt_tokens") or 0),
            "completion_tokens": int(km.get("completion_tokens") or 0),
            "total_tokens": int(km.get("total_tokens") or 0),
            "api_requests": int(km.get("api_requests") or 0),
        }

    return row
