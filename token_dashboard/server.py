"""HTTP server: static frontend + JSON endpoints + SSE diff stream."""
from __future__ import annotations

import datetime
import http.server
import json
import mimetypes
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from .db import (
    overview_totals, expensive_prompts, project_summary,
    tool_token_breakdown, recent_sessions, session_turns,
    daily_token_breakdown, model_breakdown, skill_breakdown,
    scan_status,
)
from .pricing import load_pricing, cost_for, get_plan, set_plan
from .tips import all_tips, dismiss_tip
from .scanner import scan_roots
from .skills import cached_catalog


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"

# SSE event fan-out. Every connected /api/stream client gets its own queue;
# publish_event() pushes to all of them. A single global Queue would only
# deliver each event to one waiting client (whichever .get() ran first),
# so multi-tab setups would miss scan invalidations. Maxsize caps memory
# if a client disconnects without unregistering — old events get dropped
# rather than growing the queue unboundedly.
_SSE_CLIENTS: "list[queue.Queue[dict]]" = []
_SSE_CLIENTS_LOCK = threading.Lock()
_SSE_QUEUE_MAX = 256


def publish_event(evt: dict) -> None:
    with _SSE_CLIENTS_LOCK:
        clients = list(_SSE_CLIENTS)
    for q in clients:
        try:
            q.put_nowait(evt)
        except queue.Full:
            # Slow client — drop oldest to make room rather than crash.
            try:
                q.get_nowait()
                q.put_nowait(evt)
            except (queue.Empty, queue.Full):
                pass


MAX_POST_BYTES = 1_000_000  # 1 MB — we only accept tiny JSON bodies (plan, tip key)
MAX_LIMIT = 1000

# Simple in-process response cache. TTL is slightly under the scan interval so
# cached data is never more than one scan cycle stale. Cleared after each scan.
_CACHE: "dict[str, dict]" = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300.0  # 5 min safety net; scan loop clears explicitly on new data
_SCAN_LOCK = threading.Lock()


def _cache_get(key: str):
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.time() - entry["ts"] < _CACHE_TTL:
            return entry["data"]
    return None


def _cache_set(key: str, data) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = {"ts": time.time(), "data": data}


def _cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _bundle_cache_key(since, until) -> str:
    # Truncate to date so the key is stable within a day, enabling pre-warming.
    s = since[:10] if since else ""
    u = until[:10] if until else ""
    return f"/api/overview-bundle?since={s}&until={u}"


def _overview_bundle(db_path: str, since, until, pricing: dict) -> dict:
    """Run all overview-page queries and return a single combined payload."""
    by_model = model_breakdown(db_path, since, until)
    totals = overview_totals(db_path, since, until)
    cost_usd = 0.0
    for m in by_model:
        c = cost_for(m["model"], m, pricing)
        m["cost_usd"] = c["usd"]
        m["cost_estimated"] = c["estimated"]
        if c["usd"] is not None:
            cost_usd += c["usd"]
    totals["cost_usd"] = round(cost_usd, 4)
    return {
        "totals": totals,
        "projects": project_summary(db_path, since, until),
        "sessions": recent_sessions(db_path, limit=10, since=since, until=until),
        "tools": tool_token_breakdown(db_path, since, until),
        "daily": daily_token_breakdown(db_path, since, until),
        "byModel": by_model,
    }


_WARM_DAYS = [7, 30, 90, None]  # None = all time
_WARM_DEFAULT_DAYS = 30          # the range the UI lands on first


def _do_refresh(db_path: str, projects_roots, pricing: dict) -> None:
    """One-shot scan + cache-clear + warm, used by the manual /api/refresh endpoint."""
    if not _SCAN_LOCK.acquire(blocking=False):
        publish_event({"type": "scan-skip", "reason": "already-running", "ts": time.time()})
        return
    try:
        n = scan_roots(projects_roots, db_path)
        if n.get("messages", 0) > 0:
            _cache_clear()
            publish_event({"type": "scan", "n": n, "ts": time.time()})
        else:
            # No new content this tick — still publish so the UI clears
            # the "scanning…" indicator and updates the scan-status pill
            # without forcing a manual refresh.
            publish_event({"type": "scan-complete", "n": n, "ts": time.time()})
    except Exception as e:
        publish_event({"type": "error", "message": str(e), "ts": time.time()})
    finally:
        _SCAN_LOCK.release()


def _warm_one(db_path: str, pricing: dict, days) -> None:
    try:
        since = (
            (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            if days else None
        )
        key = _bundle_cache_key(since, None)
        if _cache_get(key) is None:
            _cache_set(key, _overview_bundle(db_path, since, None, pricing))
    except Exception:
        pass


def _warm_bundle(db_path: str, pricing: dict) -> None:
    """Pre-warm all time-range bundles serially.

    Serial — was 4 parallel threads each on its own SQLite connection, which
    caused severe contention with scan writes on cold boot.
    """
    for days in _WARM_DAYS:
        _warm_one(db_path, pricing, days)


def _send_json(handler, obj, status: int = 200) -> None:
    body = json.dumps(obj, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler, status: int, msg: str) -> None:
    _send_json(handler, {"error": msg}, status=status)


def _clamp_limit(raw, default: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, MAX_LIMIT))


def _empty_rtk_payload(available: bool) -> dict:
    return {
        "available": available,
        "install_url": "https://github.com/rtk-ai/rtk",
        "summary": None,
        "daily": [],
        "weekly": [],
        "monthly": [],
    }


def _rtk_invoke(rtk_bin: str, env: dict) -> Optional[dict]:
    """Run ``rtk gain --format json --all`` with the given env (PATH + XDG)
    and return the parsed payload, or None on any failure. Caller decides
    whether a None means 'host had no rtk' (skip) or 'binary missing' (drop)."""
    try:
        r = subprocess.run(
            [rtk_bin, "gain", "--format", "json", "--all"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def _rtk_merge(payloads: List[Tuple[str, dict]]) -> dict:
    """Combine N per-host rtk payloads into one. Scalars sum across hosts;
    daily / weekly / monthly rows merge by their natural key (date /
    week_start / month). Averaged fields (savings_pct, avg_time_ms) get
    recomputed from the merged totals so the math is consistent."""
    summary_keys = (
        "total_commands", "total_input", "total_output",
        "total_saved", "total_time_ms",
    )
    bucket_keys = {
        "daily":   ("date",),
        "weekly":  ("week_start",),
        "monthly": ("month",),
    }
    bucket_sum_keys = (
        "commands", "input_tokens", "output_tokens",
        "saved_tokens", "total_time_ms",
    )

    merged_summary = {k: 0 for k in summary_keys}
    merged_buckets = {k: {} for k in bucket_keys}
    contributing_hosts: List[str] = []
    for host, p in payloads:
        if not p:
            continue
        contributing_hosts.append(host)
        s = p.get("summary") or {}
        for k in summary_keys:
            merged_summary[k] += int(s.get(k) or 0)
        for bk, (id_field,) in bucket_keys.items():
            extras = {}
            if bk == "weekly":
                extras = {"week_end": True}  # carry along, take max later
            for row in (p.get(bk) or []):
                key = row.get(id_field)
                if not key:
                    continue
                existing = merged_buckets[bk].setdefault(key, {id_field: key, **{f: 0 for f in bucket_sum_keys}})
                for f in bucket_sum_keys:
                    existing[f] = (existing[f] or 0) + int(row.get(f) or 0)
                if "week_end" in extras and row.get("week_end"):
                    prev = existing.get("week_end")
                    if not prev or row["week_end"] > prev:
                        existing["week_end"] = row["week_end"]

    def _finalize(row: dict) -> dict:
        commands = row.get("commands", 0) or 0
        input_tokens = row.get("input_tokens", 0) or 0
        saved = row.get("saved_tokens", 0) or 0
        total_time = row.get("total_time_ms", 0) or 0
        row["savings_pct"] = (saved / input_tokens * 100) if input_tokens else 0.0
        row["avg_time_ms"] = round(total_time / commands) if commands else 0
        return row

    out: dict = {
        "summary": {
            **merged_summary,
            "avg_savings_pct": (
                merged_summary["total_saved"] / merged_summary["total_input"] * 100
                if merged_summary["total_input"] else 0.0
            ),
            "avg_time_ms": (
                round(merged_summary["total_time_ms"] / merged_summary["total_commands"])
                if merged_summary["total_commands"] else 0
            ),
        },
        "daily":   sorted((_finalize(r) for r in merged_buckets["daily"].values()),   key=lambda r: r["date"]),
        "weekly":  sorted((_finalize(r) for r in merged_buckets["weekly"].values()),  key=lambda r: r["week_start"]),
        "monthly": sorted((_finalize(r) for r in merged_buckets["monthly"].values()), key=lambda r: r["month"]),
        "hosts":   contributing_hosts,
    }
    return out


def _rtk_payload(home=None) -> dict:
    """Aggregate rtk savings across the local host plus every cached
    remote host under ~/.cache/token-dashboard/rtk-state/<host>/rtk/.
    The remote DBs are populated by scripts/sync-remote.sh; pointing
    XDG_DATA_HOME at the parent dir makes rtk read that host's DB
    without any per-host binary needed (we use the local rtk binary
    for everyone)."""
    home_path = Path(home) if home is not None else Path.home()
    rtk_bin = str(home_path / ".local" / "bin" / "rtk")
    if not Path(rtk_bin).is_file():
        return _empty_rtk_payload(False)
    base_env = dict(os.environ)
    base_env["PATH"] = str(home_path / ".local" / "bin") + ":" + base_env.get("PATH", "")

    payloads: List[Tuple[str, dict]] = []

    # Local host first — no XDG override (use whatever the user's real
    # rtk install reads). We name it 'local' rather than guess the
    # hostname because rtk has no concept of "which machine" and this
    # value never surfaces to the UI in a way that needs to match
    # CLAUDE_PROJECTS_DIRS host labels.
    local = _rtk_invoke(rtk_bin, base_env)
    if local:
        payloads.append(("local", local))

    # Remote hosts: glob cached rtk-state dirs.
    rtk_cache = home_path / ".cache" / "token-dashboard" / "rtk-state"
    if rtk_cache.is_dir():
        for host_dir in sorted(rtk_cache.iterdir()):
            if not host_dir.is_dir():
                continue
            db_path = host_dir / "rtk" / "history.db"
            if not db_path.is_file():
                continue
            env = dict(base_env)
            env["XDG_DATA_HOME"] = str(host_dir)
            remote = _rtk_invoke(rtk_bin, env)
            if remote:
                payloads.append((host_dir.name, remote))

    if not payloads:
        return _empty_rtk_payload(True)

    merged = _rtk_merge(payloads)
    merged["available"] = True
    merged["install_url"] = "https://github.com/rtk-ai/rtk"
    return merged


def _serve_static(handler, rel: str) -> None:
    rel = rel.lstrip("/")
    web_root = WEB_ROOT.resolve()
    p = (web_root / rel).resolve()
    # is_relative_to is the path-correct containment test. A naive
    # startswith() on the stringified absolute path would accept a
    # sibling whose name shares the WEB_ROOT prefix (e.g. /home/dvids/
    # Dev/token-dashboard/web vs .../web-other).
    if not p.is_relative_to(web_root) or not p.is_file():
        handler.send_response(404)
        handler.end_headers()
        return
    body = p.read_bytes()
    ctype, _ = mimetypes.guess_type(str(p))
    handler.send_response(200)
    handler.send_header("Content-Type", ctype or "application/octet-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def build_handler(db_path: str, projects_roots):
    pricing = load_pricing(PRICING_JSON)
    # Optional bearer-token auth. Set DASHBOARD_TOKEN in the environment to
    # require a token on every request. Off by default to preserve existing
    # behavior; opt-in for LAN exposure (HOST=0.0.0.0).
    # Accepted in 3 forms, in priority order:
    #   1. Authorization: Bearer <token>  (preferred for API clients)
    #   2. ?token=<token>                 (sets the cookie + redirects to /)
    #   3. Cookie: td_token=<token>       (set automatically by form #2)
    auth_token = os.environ.get("DASHBOARD_TOKEN", "").strip() or None

    def _check_auth(handler, path: str, query: str) -> bool:
        """Return True if the request is authenticated (or auth is off).
        On failure, writes a 401 (API) or redirect (browser) response and
        returns False so the caller bails out."""
        if not auth_token:
            return True
        # 1. Authorization header
        auth_hdr = handler.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer ") and auth_hdr[7:].strip() == auth_token:
            return True
        # 2. ?token=… in URL — sets cookie + redirects to clean URL
        qs = parse_qs(query or "")
        token_in_url = qs.get("token", [""])[0]
        if token_in_url == auth_token:
            # Redirect to same path, querystring with token stripped.
            qs.pop("token", None)
            stripped = "&".join(
                f"{k}={v[0]}" for k, v in qs.items() if v
            )
            target = path + (("?" + stripped) if stripped else "")
            handler.send_response(302)
            handler.send_header(
                "Set-Cookie",
                f"td_token={auth_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000",
            )
            handler.send_header("Location", target)
            handler.end_headers()
            return False  # handler already replied
        # 3. Cookie
        cookie = handler.headers.get("Cookie", "")
        for piece in cookie.split(";"):
            k, _, v = piece.strip().partition("=")
            if k == "td_token" and v == auth_token:
                return True
        # API endpoints get JSON 401; everything else gets a small login hint
        if path.startswith("/api/"):
            _send_error(handler, 401, "missing or invalid token")
        else:
            handler.send_response(401)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(
                b"<h1>401 Unauthorized</h1>"
                b"<p>Append <code>?token=YOUR_TOKEN</code> to the URL to sign in.</p>"
            )
        return False

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_HEAD(self):
            # HTTP spec: HEAD response must include identical headers to GET
            # but no body. Delegate to do_GET with wfile swapped for a sink
            # that lets header bytes through (everything before end_headers)
            # and drops body bytes. Cheaper + more compliant than returning
            # 405 — some clients legitimately use HEAD for liveness checks.
            real_wfile = self.wfile
            headers_done = [False]

            class _HeaderOnlyWfile:
                def write(self, data):
                    if headers_done[0]:
                        return len(data) if hasattr(data, "__len__") else 0
                    return real_wfile.write(data)
                def flush(self):
                    return real_wfile.flush()

            sink = _HeaderOnlyWfile()
            orig_end_headers = self.end_headers
            def _patched_end_headers():
                orig_end_headers()
                headers_done[0] = True

            self.wfile = sink
            self.end_headers = _patched_end_headers
            try:
                self.do_GET()
            finally:
                self.wfile = real_wfile
                self.end_headers = orig_end_headers

        def do_GET(self):
            url = urlparse(self.path)
            if not _check_auth(self, url.path, url.query):
                return
            qs = parse_qs(url.query or "")
            path = url.path
            since = qs.get("since", [None])[0]
            until = qs.get("until", [None])[0]
            cache_key = self.path  # includes query string
            if path in ("/", "/index.html"):
                return _serve_static(self, "index.html")
            if path.startswith("/web/"):
                return _serve_static(self, path[5:])
            if path == "/api/overview-bundle":
                bundle_key = _bundle_cache_key(since, until)
                cached = _cache_get(bundle_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = _overview_bundle(db_path, since, until, pricing)
                _cache_set(bundle_key, data)
                return _send_json(self, data)
            if path == "/api/overview":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                totals = overview_totals(db_path, since, until)
                cost_usd = 0.0
                for m in model_breakdown(db_path, since, until):
                    c = cost_for(m["model"], m, pricing)
                    if c["usd"] is not None:
                        cost_usd += c["usd"]
                totals["cost_usd"] = round(cost_usd, 4)
                _cache_set(cache_key, totals)
                return _send_json(self, totals)
            if path == "/api/prompts":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                limit = _clamp_limit(qs.get("limit", ["50"])[0], 50)
                sort = qs.get("sort", ["tokens"])[0]
                rows = expensive_prompts(db_path, limit=limit, sort=sort)
                for r in rows:
                    c = cost_for(r["model"], {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": r["cache_read_tokens"],
                        "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
                    }, pricing)
                    r["estimated_cost_usd"] = c["usd"]
                _cache_set(cache_key, rows)
                return _send_json(self, rows)
            if path == "/api/projects":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = project_summary(db_path, since, until)
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/tools":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = tool_token_breakdown(db_path, since, until)
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/sessions":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = recent_sessions(
                    db_path, limit=_clamp_limit(qs.get("limit", ["20"])[0], 20),
                    since=since, until=until,
                )
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/daily":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = daily_token_breakdown(db_path, since, until)
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/skills":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                rows = skill_breakdown(db_path, since, until)
                catalog = cached_catalog()
                for r in rows:
                    info = catalog.get(r["skill"])
                    r["tokens_per_call"] = info["tokens"] if info else None
                _cache_set(cache_key, rows)
                return _send_json(self, rows)
            if path == "/api/by-model":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                rows = model_breakdown(db_path, since, until)
                for r in rows:
                    c = cost_for(r["model"], r, pricing)
                    r["cost_usd"] = c["usd"]
                    r["cost_estimated"] = c["estimated"]
                _cache_set(cache_key, rows)
                return _send_json(self, rows)
            if path.startswith("/api/sessions/"):
                sid = path.rsplit("/", 1)[1]
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = session_turns(db_path, sid)
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/tips":
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = all_tips(db_path)
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/plan":
                return _send_json(self, {"plan": get_plan(db_path), "pricing": pricing})
            if path == "/api/scan":
                # /api/scan is POST-only (write-heavy). GET returns 405 so
                # accidental prefetchers / CDNs / browser GETs can't trigger
                # a full scan unintentionally.
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"use POST"}')
                return
            if path == "/api/scan/status":
                # Lightweight — read scan_status table plus current lock
                # state so the UI can render a "fresh / stale / scanning"
                # pill. The :is_scanning flag is what enables the live
                # progress indicator during a long initial backfill.
                data = scan_status(db_path)
                data["is_scanning"] = _SCAN_LOCK.locked()
                return _send_json(self, data)
            if path == "/api/rtk":
                # Cache the aggregated payload: each call subprocess-shells
                # the rtk binary once per host. Repeated tab visits or SSE
                # client reconnects can hammer that. TTL is the standard
                # cache lifetime; cache is invalidated on scan events.
                cached = _cache_get(cache_key)
                if cached is not None:
                    return _send_json(self, cached)
                data = _rtk_payload()
                _cache_set(cache_key, data)
                return _send_json(self, data)
            if path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                # Register a per-connection queue so every connected tab
                # receives every event. The previous shared queue meant
                # whichever tab raced .get() first would consume the event;
                # other tabs would miss the scan invalidation.
                client_q: "queue.Queue[dict]" = queue.Queue(maxsize=_SSE_QUEUE_MAX)
                with _SSE_CLIENTS_LOCK:
                    _SSE_CLIENTS.append(client_q)
                try:
                    while True:
                        try:
                            evt = client_q.get(timeout=15)
                            chunk = f"data: {json.dumps(evt, default=str)}\n\n".encode()
                        except queue.Empty:
                            chunk = b": ping\n\n"
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                finally:
                    with _SSE_CLIENTS_LOCK:
                        try:
                            _SSE_CLIENTS.remove(client_q)
                        except ValueError:
                            pass
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            url = urlparse(self.path)
            if not _check_auth(self, url.path, url.query):
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return _send_error(self, 400, "invalid Content-Length")
            if length < 0 or length > MAX_POST_BYTES:
                return _send_error(self, 413, f"body too large (max {MAX_POST_BYTES} bytes)")
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                return _send_error(self, 400, "invalid JSON")
            if not isinstance(body, dict):
                return _send_error(self, 400, "body must be a JSON object")
            try:
                if url.path == "/api/plan":
                    set_plan(db_path, body.get("plan", "api"))
                    return _send_json(self, {"ok": True})
                if url.path == "/api/tips/dismiss":
                    dismiss_tip(db_path, body.get("key", ""))
                    _cache_clear()
                    return _send_json(self, {"ok": True})
                if url.path == "/api/refresh":
                    # Coalesce: if a scan is already running, don't spawn
                    # another thread — return early. The currently-running
                    # scan's completion event will reach this client via
                    # SSE just the same. Prevents thread churn under a
                    # flood of refresh clicks or accidental cron loops.
                    if _SCAN_LOCK.locked():
                        return _send_json(self, {"ok": True, "skipped": "already-scanning"})
                    threading.Thread(
                        target=_do_refresh, args=(db_path, projects_roots, pricing), daemon=True
                    ).start()
                    return _send_json(self, {"ok": True})
                if url.path == "/api/scan":
                    # POST-only version of /api/scan: run a scan synchronously,
                    # clear cache, emit completion event, return counts. The
                    # old GET form is gone — write-heavy endpoints should be
                    # POST so caches/CDNs/prefetchers don't accidentally
                    # trigger them.
                    if not _SCAN_LOCK.acquire(blocking=False):
                        return _send_json(self, {"skipped": "already-scanning"})
                    try:
                        n = scan_roots(projects_roots, db_path)
                    finally:
                        _SCAN_LOCK.release()
                    if n.get("messages", 0) > 0:
                        _cache_clear()
                        publish_event({"type": "scan", "n": n, "ts": time.time()})
                    else:
                        publish_event({"type": "scan-complete", "n": n, "ts": time.time()})
                    return _send_json(self, n)
            except Exception as e:
                return _send_error(self, 503, str(e))
            self.send_response(404)
            self.end_headers()

    return H


def _scan_loop(db_path: str, projects_roots, pricing: dict, interval: float = 60.0):
    # Sleep first: avoid contending with the synchronous boot warm + the user's
    # first request. The boot warm covers initial cache population.
    time.sleep(interval)
    while True:
        try:
            if _SCAN_LOCK.acquire(blocking=False):
                try:
                    n = scan_roots(projects_roots, db_path)
                    if n.get("messages", 0) > 0:
                        _cache_clear()
                        publish_event({"type": "scan", "n": n, "ts": time.time()})
                    else:
                        # Always publish a completion event even on zero-change
                        # scans — the UI's _scanning flag was getting stuck on
                        # idle scans because nothing told it the tick finished.
                        publish_event({"type": "scan-complete", "n": n, "ts": time.time()})
                finally:
                    _SCAN_LOCK.release()
        except Exception as e:
            publish_event({"type": "error", "message": str(e), "ts": time.time()})
        time.sleep(interval)


def run(host: str, port: int, db_path: str, projects_roots):
    pricing = load_pricing(PRICING_JSON)
    # Warm the default range (30d) synchronously before opening the port so
    # the user's first paint is a cache hit. Then warm 7d/90d/all in the
    # background while the server is already serving.
    _warm_one(db_path, pricing, _WARM_DEFAULT_DAYS)
    def _warm_rest():
        for days in _WARM_DAYS:
            if days != _WARM_DEFAULT_DAYS:
                _warm_one(db_path, pricing, days)
    threading.Thread(target=_warm_rest, daemon=True).start()
    threading.Thread(target=_scan_loop, args=(db_path, projects_roots, pricing), daemon=True).start()
    H = build_handler(db_path, projects_roots)
    httpd = http.server.ThreadingHTTPServer((host, port), H)
    httpd.serve_forever()
