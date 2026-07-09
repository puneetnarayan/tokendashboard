"""HTTP server: static frontend + JSON endpoints + SSE diff stream."""
from __future__ import annotations

import http.server
import json
import mimetypes
import queue
import threading
import time
import calendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .db import (
    overview_totals, expensive_prompts, project_summary,
    tool_token_breakdown, recent_sessions, session_turns,
    daily_token_breakdown, model_breakdown, skill_breakdown,
    daily_model_breakdown, litellm_meta_all, litellm_daily_raw, litellm_models_raw,
    litellm_keys_raw,
)
from .pricing import load_pricing, cost_for, get_plan, set_plan
from .tips import all_tips, dismiss_tip
from .scanner import scan_dir
from .skills import cached_catalog
from . import rtk as _rtk
from . import config as _cfg
from .litellm_config import discover as _ll_discover, mask_key as _ll_mask
from .litellm_sync import LiteLLMSync


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PUBLIC_ROOT = Path(__file__).resolve().parent.parent / "public"
PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"

EVENTS: "queue.Queue[dict]" = queue.Queue()

# Module-level LiteLLM sync instance — set in run(), used by POST /api/litellm/*
_litellm_sync: "LiteLLMSync | None" = None

MAX_POST_BYTES = 1_000_000  # 1 MB — we only accept tiny JSON bodies (plan, tip key)
MAX_LIMIT = 1000


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


def _serve_static(handler, rel: str) -> None:
    rel = rel.lstrip("/")
    p = (WEB_ROOT / rel).resolve()
    if not str(p).startswith(str(WEB_ROOT.resolve())) or not p.is_file():
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


def build_handler(db_path: str, projects_dir: str):
    pricing = load_pricing(PRICING_JSON)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def handle_error(self, request, client_address):
            pass  # suppress ConnectionResetError / BrokenPipeError noise

        def do_HEAD(self):
            return self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query or "")
            path = url.path
            since   = qs.get("since",   [None])[0]
            until   = qs.get("until",   [None])[0]
            project = qs.get("project", [None])[0]
            if path in ("/", "/index.html"):
                return _serve_static(self, "index.html")
            if path.startswith("/web/"):
                return _serve_static(self, path[5:])
            if path.startswith("/public/"):
                rel = path[8:].lstrip("/")
                p = (PUBLIC_ROOT / rel).resolve()
                if not str(p).startswith(str(PUBLIC_ROOT.resolve())) or not p.is_file():
                    self.send_response(404); self.end_headers(); return
                body = p.read_bytes()
                ctype, _ = mimetypes.guess_type(str(p))
                self.send_response(200)
                self.send_header("Content-Type", ctype or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            custom_models = _cfg.load().custom_models
            if path == "/api/overview":
                totals = overview_totals(db_path, since, until, project)
                cost_usd = 0.0
                for m in model_breakdown(db_path, since, until, project):
                    c = cost_for(m["model"], m, pricing, custom_models)
                    if c["usd"] is not None:
                        cost_usd += c["usd"]
                totals["cost_usd"] = round(cost_usd, 4)
                # Previous period for delta indicators (only when since is set, no until)
                if since and not until:
                    try:
                        since_dt = datetime.fromisoformat(since.rstrip("Z")).replace(tzinfo=timezone.utc)
                        now_dt   = datetime.now(timezone.utc)
                        period   = now_dt - since_dt
                        prev_since = (since_dt - period).isoformat()
                        prev = overview_totals(db_path, prev_since, since, project)
                        prev_cost = 0.0
                        for m in model_breakdown(db_path, prev_since, since, project):
                            c = cost_for(m["model"], m, pricing, custom_models)
                            if c["usd"] is not None:
                                prev_cost += c["usd"]
                        prev["cost_usd"] = round(prev_cost, 4)
                        totals["prev"] = prev
                    except Exception:
                        pass
                return _send_json(self, totals)
            if path == "/api/prompts":
                limit = _clamp_limit(qs.get("limit", ["50"])[0], 50)
                sort = qs.get("sort", ["tokens"])[0]
                rows = expensive_prompts(db_path, limit=limit, sort=sort)
                for r in rows:
                    c = cost_for(r["model"], {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": r["cache_read_tokens"],
                        "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
                    }, pricing, custom_models)
                    r["estimated_cost_usd"] = c["usd"]
                return _send_json(self, rows)
            if path == "/api/projects":
                rows = project_summary(db_path, since, until)
                for r in rows:
                    cost = 0.0
                    for m in model_breakdown(db_path, since, until, r["project_slug"]):
                        c = cost_for(m["model"], m, pricing, custom_models)
                        if c["usd"] is not None:
                            cost += c["usd"]
                    r["cost_usd"] = round(cost, 4)
                return _send_json(self, rows)
            if path == "/api/custom-models":
                return _send_json(self, _cfg.load().custom_models)
            if path == "/api/tools":
                return _send_json(self, tool_token_breakdown(db_path, since, until, project))
            if path == "/api/sessions":
                return _send_json(self, recent_sessions(
                    db_path, limit=_clamp_limit(qs.get("limit", ["20"])[0], 20),
                    since=since, until=until, project_slug=project,
                ))
            if path == "/api/daily":
                return _send_json(self, daily_token_breakdown(db_path, since, until, project))
            if path == "/api/skills":
                rows = skill_breakdown(db_path, since, until, project)
                catalog = cached_catalog()
                for r in rows:
                    info = catalog.get(r["skill"])
                    r["tokens_per_call"] = info["tokens"] if info else None
                return _send_json(self, rows)
            if path == "/api/by-model":
                rows = model_breakdown(db_path, since, until, project)
                for r in rows:
                    c = cost_for(r["model"], r, pricing, custom_models)
                    r["cost_usd"] = c["usd"]
                    r["cost_estimated"] = c["estimated"]
                return _send_json(self, rows)
            if path.startswith("/api/sessions/"):
                sid = path.rsplit("/", 1)[1]
                return _send_json(self, session_turns(db_path, sid))
            if path == "/api/tips":
                return _send_json(self, all_tips(db_path))
            if path == "/api/rtk":
                return _send_json(self, _rtk.stats() or {"installed": False})
            if path == "/api/plan":
                return _send_json(self, {"plan": get_plan(db_path), "pricing": pricing, "custom_models": custom_models})
            if path == "/api/scan":
                n = scan_dir(projects_dir, db_path)
                return _send_json(self, n)
            if path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                while True:
                    try:
                        evt = EVENTS.get(timeout=15)
                        chunk = f"data: {json.dumps(evt, default=str)}\n\n".encode()
                    except queue.Empty:
                        chunk = b": ping\n\n"
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            # ── LiteLLM routes ─────────────────────────────────────────
            if path == "/api/litellm/status":
                return _send_json(self, _ll_status(db_path))
            if path == "/api/litellm/summary":
                rng = qs.get("range", ["30d"])[0]
                s, u = _range_to_since_until(rng)
                return _send_json(self, _ll_summary(db_path, pricing, s, u))
            if path == "/api/litellm/daily":
                rng = qs.get("range", ["30d"])[0]
                s, u = _range_to_since_until(rng)
                return _send_json(self, _ll_daily(db_path, pricing, s, u))
            if path == "/api/litellm/models":
                rng = qs.get("range", ["30d"])[0]
                s, u = _range_to_since_until(rng)
                return _send_json(self, _ll_models(db_path, s, u))
            if path == "/api/litellm/monthly":
                return _send_json(self, _ll_monthly(db_path))
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            url = urlparse(self.path)
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
            if url.path == "/api/plan":
                set_plan(db_path, body.get("plan", "api"))
                return _send_json(self, {"ok": True})
            if url.path == "/api/tips/dismiss":
                dismiss_tip(db_path, body.get("key", ""))
                return _send_json(self, {"ok": True})
            if url.path == "/api/litellm/test":
                cfg = _ll_discover()
                if cfg is None:
                    return _send_json(self, _ll_status(db_path))
                from .litellm_config import validate as _ll_validate, clear_cache as _ll_clear
                _ll_clear()
                state, user_info = _ll_validate(cfg)
                # persist the result
                if _litellm_sync:
                    _litellm_sync.trigger_now()
                return _send_json(self, _ll_status(db_path))
            if url.path == "/api/litellm/sync":
                if _litellm_sync:
                    _litellm_sync.trigger_now()
                    return _send_json(self, {"ok": True, "state": litellm_meta_all(db_path).get("state", "unknown")})
                return _send_json(self, {"ok": False, "state": "not_configured"})
            if url.path == "/api/litellm/clear-data":
                from .db import connect as _conn
                with _conn(db_path) as c:
                    c.execute("DELETE FROM litellm_daily")
                    c.execute("DELETE FROM litellm_daily_models")
                    c.execute("DELETE FROM litellm_daily_keys")
                    c.commit()
                if _litellm_sync:
                    _litellm_sync.trigger_now()
                return _send_json(self, {"ok": True})
            if url.path == "/api/litellm/save-config":
                import os
                from pathlib import Path as _Path
                cfg_path = _Path.home() / ".claude" / "token-dashboard-config.json"
                try:
                    existing = {}
                    if cfg_path.exists():
                        try:
                            import json as _json
                            existing = _json.loads(cfg_path.read_text())
                        except Exception:
                            pass
                    new_key = str(body.get("api_key") or "").strip()
                    existing["litellm"] = {
                        "base_url": str(body.get("base_url") or "").strip(),
                        "api_key":  new_key or (existing.get("litellm", {}).get("api_key", "")),
                        "enabled":  bool(body.get("enabled", True)),
                    }
                    cfg_path.parent.mkdir(parents=True, exist_ok=True)
                    cfg_path.write_text(json.dumps(existing, indent=2))
                    os.chmod(cfg_path, 0o600)
                    if _litellm_sync:
                        _litellm_sync.trigger_now()
                    return _send_json(self, {"ok": True})
                except Exception as e:
                    return _send_error(self, 500, str(e))
            if url.path == "/api/custom-models":
                c = _cfg.load()
                action = body.get("action", "set")
                model_id = (body.get("model") or "").strip()
                if not model_id:
                    return _send_error(self, 400, "model is required")
                if action == "delete":
                    c.custom_models.pop(model_id, None)
                else:
                    try:
                        inp = float(body.get("input", 0))
                        out = float(body.get("output", 0))
                    except (TypeError, ValueError):
                        return _send_error(self, 400, "input and output must be numbers")
                    c.custom_models[model_id] = {"input": inp, "output": out}
                _cfg.save(c)
                return _send_json(self, {"ok": True, "custom_models": c.custom_models})
            self.send_response(404)
            self.end_headers()

    return H


def _scan_loop(db_path: str, projects_dir: str, interval: float = 30.0):
    while True:
        try:
            n = scan_dir(projects_dir, db_path)
            if n["messages"] > 0:
                EVENTS.put({"type": "scan", "n": n, "ts": time.time()})
        except Exception as e:
            EVENTS.put({"type": "error", "message": str(e)})
        time.sleep(interval)


def run(host: str, port: int, db_path: str, projects_dir: str):
    global _litellm_sync
    _litellm_sync = LiteLLMSync(db_path)
    threading.Thread(target=_litellm_sync.run_forever, daemon=True).start()
    threading.Thread(target=_scan_loop, args=(db_path, projects_dir), daemon=True).start()
    H = build_handler(db_path, projects_dir)

    class QuietServer(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # suppress ConnectionResetError / BrokenPipeError noise

    httpd = QuietServer((host, port), H)
    httpd.serve_forever()


# ── LiteLLM route helpers ──────────────────────────────────────────────────

def _range_to_since_until(range_key: str) -> tuple:
    """Convert ?range=7d|30d|90d|all to (since_iso, until_iso)."""
    days_map = {"7d": 7, "30d": 30, "90d": 90}
    days = days_map.get(range_key)
    if not days:
        return (None, None)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return (since, None)


def _ll_status(db_path: str) -> dict:
    meta = litellm_meta_all(db_path)
    cfg = _ll_discover()
    state = meta.get("state", "not_configured" if cfg is None else "active")
    if cfg is None and state not in ("disabled",):
        state = "not_configured"
    last_sync = meta.get("last_sync")
    stale = False
    if last_sync:
        try:
            ls_dt = datetime.fromisoformat(last_sync)
            stale = (datetime.now(timezone.utc) - ls_dt).total_seconds() > 1800
        except Exception:
            pass
    return {
        "state":        state,
        "proxy_origin": meta.get("proxy_origin"),
        "user_alias":   meta.get("user_alias"),
        "key_masked":   _ll_mask(cfg["api_key"]) if cfg else None,
        "source":       cfg["source"] if cfg else None,
        "last_sync":    last_sync,
        "stale":        stale,
    }


def _local_estimate_by_day(db_path: str, pricing: dict, since, until) -> dict:
    """Return {date_str: cost_usd} for local transcript estimates."""
    custom_models = _cfg.load().custom_models
    rows = daily_model_breakdown(db_path, since, until)
    by_day: dict = {}
    for r in rows:
        c = cost_for(r["model"], r, pricing, custom_models)
        usd = c["usd"] or 0.0
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + usd
    return by_day


def _date_range(since, until) -> list:
    """Generate YYYY-MM-DD strings from since to today (or until)."""
    from datetime import date
    try:
        start = datetime.fromisoformat(since.rstrip("Z")).date() if since else None
    except Exception:
        start = None
    try:
        end = datetime.fromisoformat(until.rstrip("Z")).date() if until else None
    except Exception:
        end = None
    today = datetime.now(timezone.utc).date()
    end = end or today
    if not start:
        return []
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _ll_daily(db_path: str, pricing: dict, since, until) -> dict:
    raw = {r["date"]: r for r in litellm_daily_raw(db_path, since, until)}
    local = _local_estimate_by_day(db_path, pricing, since, until)
    days = _date_range(since, until)
    result = []
    for d in days:
        row = raw.get(d, {})
        result.append({
            "date":                 d,
            "spend":                row.get("spend", 0.0),
            "prompt_tokens":        row.get("prompt_tokens", 0),
            "completion_tokens":    row.get("completion_tokens", 0),
            "total_tokens":         row.get("total_tokens", 0),
            "api_requests":         row.get("api_requests", 0),
            "local_estimate_spend": round(local.get(d, 0.0), 6),
        })
    return {"days": result}


def _ll_summary(db_path: str, pricing: dict, since, until) -> dict:
    rows = litellm_daily_raw(db_path, since, until)
    spend        = sum(r["spend"] for r in rows)
    api_requests = sum(r["api_requests"] for r in rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    prompt_tokens = sum(r["prompt_tokens"] for r in rows)
    completion_tokens = sum(r["completion_tokens"] for r in rows)
    local_est = sum(_local_estimate_by_day(db_path, pricing, since, until).values())
    delta = round(spend - local_est, 6)
    delta_pct = round(delta / spend * 100, 2) if spend else None
    avg = round(spend / api_requests, 6) if api_requests else None
    meta = litellm_meta_all(db_path)
    budget = None
    if meta.get("budget_max"):
        try:
            budget = {
                "max":      float(meta["budget_max"]),
                "spent":    float(meta.get("budget_spend") or 0),
                "duration": meta.get("budget_duration"),
            }
        except Exception:
            pass
    return {
        "spend":                round(spend, 4),
        "api_requests":         api_requests,
        "total_tokens":         total_tokens,
        "prompt_tokens":        prompt_tokens,
        "completion_tokens":    completion_tokens,
        "avg_cost_per_request": avg,
        "local_estimate_spend": round(local_est, 4),
        "delta":                delta,
        "delta_pct":            delta_pct,
        "budget":               budget,
    }


def _ll_monthly(db_path: str, _today: "date | None" = None) -> dict:
    """Month-over-month comparison for the LiteLLM tab.

    Returns:
      months       — last 6 calendar months, zero-filled
      compare      — MTD vs same-period-last-month, projection
      model_deltas — top 8 models by abs(delta) MTD vs same-period
    """
    today = _today or date.today()

    # Build ordered list of last 6 calendar months (oldest first)
    months_seq = []
    y, m = today.year, today.month
    for _ in range(6):
        months_seq.insert(0, (y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1

    earliest = date(months_seq[0][0], months_seq[0][1], 1)

    from .db import connect as _conn
    with _conn(db_path) as c:
        daily_rows = c.execute(
            "SELECT date, spend, total_tokens, api_requests FROM litellm_daily "
            "WHERE date >= ? AND date <= ? ORDER BY date",
            (earliest.isoformat(), today.isoformat()),
        ).fetchall()
        model_rows = c.execute(
            "SELECT date, model, spend FROM litellm_daily_models "
            "WHERE date >= ? AND date <= ?",
            (earliest.isoformat(), today.isoformat()),
        ).fetchall()
        key_rows = c.execute(
            "SELECT date, key_hash, key_alias, spend FROM litellm_daily_keys "
            "WHERE date >= ? AND date <= ?",
            (earliest.isoformat(), today.isoformat()),
        ).fetchall()

    # Aggregate daily rows by calendar month
    month_agg: dict = {
        f"{y:04d}-{m:02d}": {"month": f"{y:04d}-{m:02d}", "spend": 0.0,
                              "total_tokens": 0, "api_requests": 0}
        for (y, m) in months_seq
    }
    for r in daily_rows:
        mk = r["date"][:7]
        if mk in month_agg:
            month_agg[mk]["spend"]        += r["spend"]
            month_agg[mk]["total_tokens"] += r["total_tokens"]
            month_agg[mk]["api_requests"] += r["api_requests"]

    month_list = [month_agg[f"{y:04d}-{m:02d}"] for (y, m) in months_seq]
    for entry in month_list:
        entry["spend"] = round(entry["spend"], 4)

    # MTD boundaries
    cur_start = date(today.year, today.month, 1)
    prev_y, prev_m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    prev_start = date(prev_y, prev_m, 1)
    prev_last_day = calendar.monthrange(prev_y, prev_m)[1]
    same_day = min(today.day, prev_last_day)
    prev_same_end = date(prev_y, prev_m, same_day)
    prev_end = date(prev_y, prev_m, prev_last_day)

    cur_start_s, cur_end_s = cur_start.isoformat(), today.isoformat()
    prev_start_s, prev_same_s = prev_start.isoformat(), prev_same_end.isoformat()
    prev_end_s = prev_end.isoformat()

    current_mtd = sum(r["spend"] for r in daily_rows if cur_start_s <= r["date"] <= cur_end_s)
    prev_same   = sum(r["spend"] for r in daily_rows if prev_start_s <= r["date"] <= prev_same_s)
    prev_total  = sum(r["spend"] for r in daily_rows if prev_start_s <= r["date"] <= prev_end_s)

    change_pct = round((current_mtd - prev_same) / prev_same * 100, 2) if prev_same else None

    elapsed_days   = today.day
    days_in_month  = calendar.monthrange(today.year, today.month)[1]
    projected      = round(current_mtd / elapsed_days * days_in_month, 4) if elapsed_days else current_mtd

    compare = {
        "current_mtd":             round(current_mtd, 4),
        "current_mtd_month":       f"{today.year:04d}-{today.month:02d}",
        "previous_same_period":    round(prev_same, 4),
        "previous_month":          f"{prev_y:04d}-{prev_m:02d}",
        "same_day":                same_day,
        "change_pct":              change_pct,
        "projected_month_end":     projected,
        "previous_month_total":    round(prev_total, 4),
        "days_in_month":           days_in_month,
        "elapsed_days":            elapsed_days,
    }

    # Per-model MTD deltas
    cur_model: dict = {}
    prev_model: dict = {}
    for r in model_rows:
        d, model, spend = r["date"], r["model"], r["spend"]
        if cur_start_s <= d <= cur_end_s:
            cur_model[model]  = cur_model.get(model, 0.0) + spend
        if prev_start_s <= d <= prev_same_s:
            prev_model[model] = prev_model.get(model, 0.0) + spend

    all_models = set(cur_model) | set(prev_model)
    deltas = sorted(
        [{"model": mo,
          "current_mtd_spend":          round(cur_model.get(mo, 0.0), 6),
          "previous_same_period_spend": round(prev_model.get(mo, 0.0), 6),
          "delta": round(cur_model.get(mo, 0.0) - prev_model.get(mo, 0.0), 6)}
         for mo in all_models],
        key=lambda x: abs(x["delta"]), reverse=True,
    )[:8]

    # Per-key MTD deltas
    # key_alias tracks the most-recently-seen alias for each key_hash
    cur_key: dict = {}
    prev_key: dict = {}
    key_alias: dict = {}  # key_hash -> alias
    for r in key_rows:
        d, kh, alias, spend = r["date"], r["key_hash"], r["key_alias"], r["spend"]
        if alias:
            key_alias[kh] = alias
        if cur_start_s <= d <= cur_end_s:
            cur_key[kh]  = cur_key.get(kh, 0.0) + spend
        if prev_start_s <= d <= prev_same_s:
            prev_key[kh] = prev_key.get(kh, 0.0) + spend

    all_keys = set(cur_key) | set(prev_key)
    key_deltas = sorted(
        [{"key_alias":                    key_alias.get(kh) or kh[:12],
          "current_mtd_spend":            round(cur_key.get(kh, 0.0), 6),
          "previous_same_period_spend":   round(prev_key.get(kh, 0.0), 6),
          "delta": round(cur_key.get(kh, 0.0) - prev_key.get(kh, 0.0), 6)}
         for kh in all_keys],
        key=lambda x: abs(x["delta"]), reverse=True,
    )

    return {"months": month_list, "compare": compare, "model_deltas": deltas, "key_deltas": key_deltas}


def _ll_models(db_path: str, since, until) -> dict:
    rows = litellm_models_raw(db_path, since, until)
    # aggregate by model across days
    agg: dict = {}
    for r in rows:
        m = r["model"]
        if m not in agg:
            agg[m] = {"model": m, "spend": 0.0, "prompt_tokens": 0,
                      "completion_tokens": 0, "total_tokens": 0, "api_requests": 0}
        agg[m]["spend"]             += r["spend"]
        agg[m]["prompt_tokens"]     += r["prompt_tokens"]
        agg[m]["completion_tokens"] += r["completion_tokens"]
        agg[m]["total_tokens"]      += r["total_tokens"]
        agg[m]["api_requests"]      += r["api_requests"]
    models = sorted(agg.values(), key=lambda x: x["spend"], reverse=True)
    total_spend = sum(m["spend"] for m in models) or 1.0
    for m in models:
        m["avg_cost_per_request"] = round(m["spend"] / m["api_requests"], 6) if m["api_requests"] else None
        m["share_pct"] = round(m["spend"] / total_spend * 100, 2)
        m["spend"] = round(m["spend"], 6)
    return {"models": models}
