"""Client billing model and matching logic.

A `client` is a billable entity. Each client owns one or more substring
`patterns` matched against the rolled-up project_name. A per-project
`override` can pin a project to a specific client (or to `None`, meaning
"never billable to anyone — leave it out of the totals").

Matching precedence:
  1. override exists for this project_name        → use it
  2. longest pattern that's a substring of name   → use that client
  3. no match                                     → unassigned

Aggregation reads from `project_summary` (which already runs the host +
sub-path rollup), so client_summary inherits all of that work for free.
"""

from __future__ import annotations

import csv
import io
from typing import Iterable, Optional

from .db import connect, project_summary, ACTIVE_GAP_THRESHOLD_S, _slug_meta, project_name_for, _collapse_pattern


def list_clients(db_path) -> list:
    """All clients with their patterns. Sorted by name."""
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, name, hourly_rate, fixed_price, currency, status, notes, created_at "
            "FROM clients ORDER BY status='active' DESC, name ASC"
        )]
        patterns = {}
        for r in c.execute("SELECT client_id, pattern FROM client_patterns ORDER BY client_id, pattern"):
            patterns.setdefault(r[0], []).append(r[1])
        for r in rows:
            r["patterns"] = patterns.get(r["id"], [])
    return rows


def get_client(db_path, client_id: int) -> Optional[dict]:
    with connect(db_path) as c:
        row = c.execute(
            "SELECT id, name, hourly_rate, fixed_price, currency, status, notes, created_at "
            "FROM clients WHERE id=?", (client_id,)
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["patterns"] = [
            r[0] for r in c.execute(
                "SELECT pattern FROM client_patterns WHERE client_id=? ORDER BY pattern",
                (client_id,),
            )
        ]
        return out


def upsert_client(db_path, client_id: Optional[int], name: str, hourly_rate=None,
                  fixed_price=None, currency: str = "CAD", status: str = "active",
                  notes: str = "", patterns: Optional[Iterable[str]] = None) -> int:
    """Create or update a client. Patterns are fully replaced (set-style)
    on every call — pass [] to clear them. Returns the client_id."""
    patterns = [p.strip() for p in (patterns or []) if p and p.strip()]
    with connect(db_path) as c:
        if client_id is None:
            cur = c.execute(
                "INSERT INTO clients (name, hourly_rate, fixed_price, currency, status, notes) "
                "VALUES (?,?,?,?,?,?)",
                (name, hourly_rate, fixed_price, currency, status, notes),
            )
            client_id = cur.lastrowid
        else:
            c.execute(
                "UPDATE clients SET name=?, hourly_rate=?, fixed_price=?, currency=?, "
                "status=?, notes=? WHERE id=?",
                (name, hourly_rate, fixed_price, currency, status, notes, client_id),
            )
        c.execute("DELETE FROM client_patterns WHERE client_id=?", (client_id,))
        if patterns:
            c.executemany(
                "INSERT INTO client_patterns (client_id, pattern) VALUES (?, ?)",
                [(client_id, p) for p in patterns],
            )
        c.commit()
    return client_id


def delete_client(db_path, client_id: int) -> None:
    with connect(db_path) as c:
        c.execute("DELETE FROM clients WHERE id=?", (client_id,))
        c.commit()


def set_override(db_path, project_name: str, client_id: Optional[int]) -> None:
    """Pin a project to a client (or to None = 'not billable')."""
    with connect(db_path) as c:
        if client_id is None:
            # Distinguish "remove the override" from "explicitly null". The
            # caller's intent is overloaded here; we treat `None` as the
            # explicit-null case. To CLEAR the override entirely, use
            # clear_override below.
            c.execute(
                "INSERT INTO client_overrides (project_name, client_id) VALUES (?, NULL) "
                "ON CONFLICT(project_name) DO UPDATE SET client_id=NULL, "
                "set_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                (project_name,),
            )
        else:
            c.execute(
                "INSERT INTO client_overrides (project_name, client_id) VALUES (?, ?) "
                "ON CONFLICT(project_name) DO UPDATE SET client_id=excluded.client_id, "
                "set_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                (project_name, client_id),
            )
        c.commit()


def clear_override(db_path, project_name: str) -> None:
    with connect(db_path) as c:
        c.execute("DELETE FROM client_overrides WHERE project_name=?", (project_name,))
        c.commit()


def _load_match_rules(conn) -> tuple:
    """Return (patterns, overrides) for the resolver. Done once per
    client_summary call so the resolver doesn't hit the DB per project."""
    patterns = list(conn.execute(
        "SELECT client_id, pattern FROM client_patterns"
    ))
    overrides = {
        r[0]: r[1] for r in conn.execute(
            "SELECT project_name, client_id FROM client_overrides"
        )
    }
    return patterns, overrides


def resolve_client(project_name: str, patterns, overrides) -> tuple:
    """Resolve a project_name to (client_id, source) where source is one
    of 'override', 'pattern', or None for unassigned. Longest pattern
    wins on substring ambiguity."""
    if project_name in overrides:
        # Override may be an explicit None ("not billable"); preserve that.
        return overrides[project_name], "override"
    pn_lc = (project_name or "").lower()
    candidates = [(cid, p) for cid, p in patterns if p and p.lower() in pn_lc]
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: -len(x[1]))
    return candidates[0][0], "pattern"


def client_summary(db_path, since=None, until=None) -> dict:
    """Per-client aggregation for a date range.

    Returns a dict with three keys:
      - `clients`: list of per-client rows with name, status, rate,
        active_seconds, billable_amount, contributing_projects, etc.
      - `unassigned`: list of project rows that didn't match any client
        (and aren't explicitly excluded via an override → null mapping).
      - `excluded`: list of project rows the user explicitly pinned to
        client_id=NULL (i.e. "tagged but not billable").
    """
    projects = project_summary(db_path, since=since, until=until)
    with connect(db_path) as c:
        clients = [dict(r) for r in c.execute(
            "SELECT id, name, hourly_rate, fixed_price, currency, status, notes "
            "FROM clients"
        )]
        patterns, overrides = _load_match_rules(c)

    by_id = {c["id"]: c for c in clients}
    for c in clients:
        c["sessions"] = 0
        c["turns"] = 0
        c["billable_tokens"] = 0
        c["active_seconds"] = 0.0
        c["projects"] = []  # rolled-up project entries that mapped here
        c["match_source"] = None  # 'override' / 'pattern' / 'mixed'

    unassigned = []
    excluded = []
    for p in projects:
        cid, source = resolve_client(p["project_name"], patterns, overrides)
        if cid is None and source is None:
            unassigned.append(p)
            continue
        if cid is None and source == "override":
            excluded.append(p)
            continue
        cl = by_id.get(cid)
        if not cl:
            unassigned.append(p)  # dangling override (client deleted)
            continue
        cl["sessions"] += int(p.get("sessions") or 0)
        cl["turns"] += int(p.get("turns") or 0)
        cl["billable_tokens"] += int(p.get("billable_tokens") or 0)
        cl["active_seconds"] += float(p.get("active_seconds") or 0)
        cl["projects"].append({
            "project_name": p["project_name"],
            "active_seconds": p.get("active_seconds") or 0,
            "billable_tokens": p.get("billable_tokens") or 0,
            "sessions": p.get("sessions") or 0,
            "hosts": p.get("hosts") or [],
            "match_source": source,
        })
        cl["match_source"] = source if cl["match_source"] in (None, source) else "mixed"

    # Compute billable amounts and effective hourly rate.
    for cl in clients:
        hours = cl["active_seconds"] / 3600
        rate = cl.get("hourly_rate")
        fixed = cl.get("fixed_price")
        if rate is not None and fixed is None:
            cl["billable_amount"] = round(hours * rate, 2)
            cl["effective_rate"] = round(rate, 2)
            cl["pricing_mode"] = "hourly"
        elif fixed is not None:
            cl["billable_amount"] = round(fixed, 2)
            cl["effective_rate"] = round(fixed / hours, 2) if hours > 0 else None
            cl["pricing_mode"] = "fixed"
        else:
            cl["billable_amount"] = None
            cl["effective_rate"] = None
            cl["pricing_mode"] = "unpriced"
        cl["hours"] = round(hours, 2)

    # Sort: active first, then by active_seconds desc.
    clients.sort(key=lambda r: (r["status"] != "active", -r["active_seconds"]))
    return {
        "clients": clients,
        "unassigned": sorted(unassigned, key=lambda r: -r.get("active_seconds", 0)),
        "excluded": sorted(excluded, key=lambda r: -r.get("active_seconds", 0)),
        "threshold_seconds": ACTIVE_GAP_THRESHOLD_S,
    }


def clients_velocity(db_path, since=None, until=None) -> dict:
    """Weekly active hours per client. Powers the Clients-tab velocity chart.

    Aggregates summary_sessions.active_seconds by ISO week + project, then
    maps each project to its client via the same resolver client_summary
    uses. Unassigned + excluded projects collapse into synthetic
    `__unassigned__` and `__excluded__` series so the chart still
    accounts for them.
    """
    with connect(db_path) as c:
        # Active_seconds is already in summary_sessions; the query is
        # column-only — ~5ms even on a million-row history.
        sess_where, sess_args = [], []
        if since:
            sess_where.append("ended >= ?"); sess_args.append(since)
        if until:
            sess_where.append("started < ?"); sess_args.append(until)
        where_clause = (" WHERE " + " AND ".join(sess_where)) if sess_where else ""
        rows = list(c.execute(
            f"""
            SELECT substr(started, 1, 10) AS day,
                   project_slug,
                   active_seconds
              FROM summary_sessions
              {where_clause}
              ORDER BY started ASC
            """,
            sess_args,
        ))
        slug_cwd, _ = _slug_meta(c)
        patterns, overrides = _load_match_rules(c)
        client_names = {
            r[0]: r[1] for r in c.execute("SELECT id, name FROM clients")
        }

    # Resolve project_slug → display name → client_id (cached per slug
    # so we don't re-resolve thousands of times across the result rows).
    name_cache = {}
    client_cache = {}

    def slug_to_client(slug):
        if slug in client_cache:
            return client_cache[slug]
        name = name_cache.get(slug)
        if name is None:
            cwd = slug_cwd.get(slug)
            name = _collapse_pattern(cwd) or project_name_for(cwd, slug)
            name_cache[slug] = name
        cid, source = resolve_client(name, patterns, overrides)
        if source is None:
            label = "__unassigned__"
        elif cid is None:  # explicit-null override
            label = "__excluded__"
        else:
            label = client_names.get(cid, f"client-{cid}") if cid else "__unassigned__"
        client_cache[slug] = label
        return label

    # Bucket into ISO-style "YYYY-W##" weeks. We use a Monday-start week
    # via Python's isocalendar() for stable cross-year ordering.
    from datetime import datetime
    week_set = set()
    by_client = {}  # client_label -> { 'YYYY-W##' -> active_seconds }
    for day, slug, secs in rows:
        if not day or secs is None or secs <= 0:
            continue
        try:
            dt = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        y, w, _ = dt.isocalendar()
        wk = f"{y:04d}-W{w:02d}"
        week_set.add(wk)
        label = slug_to_client(slug)
        by_client.setdefault(label, {}).setdefault(wk, 0.0)
        by_client[label][wk] += float(secs)

    weeks = sorted(week_set)
    series = []
    for label, by_wk in by_client.items():
        # Skip synthetic series if they're zero (no exclusions in range etc).
        total = sum(by_wk.values())
        if total <= 0:
            continue
        display = {"__unassigned__": "(unassigned)",
                   "__excluded__":   "(not billable)"}.get(label, label)
        series.append({
            "name": display,
            "is_synthetic": label.startswith("__"),
            "total_seconds": total,
            "weeks": [round(by_wk.get(w, 0.0), 1) for w in weeks],
        })
    series.sort(key=lambda s: (s["is_synthetic"], -s["total_seconds"]))
    return {"weeks": weeks, "series": series}


def hour_of_day(db_path, since=None, until=None) -> dict:
    """Per (weekday, hour) active-seconds heatmap. Powers the 'Hour of day'
    toggle on the Overview heatmap.

    Uses the same LAG-based gap computation as the active_seconds queries.
    Result is small (24*7 = 168 rows), but the query reads from messages
    so it's worth caching server-side.
    """
    from .db import _active_seconds_sql, _range_clause, ACTIVE_GAP_THRESHOLD_S
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT CAST(strftime('%w', timestamp) AS INTEGER) AS dow,
             CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
             COALESCE(SUM(gap_s), 0) AS active_secs
        FROM ({_active_seconds_sql(ACTIVE_GAP_THRESHOLD_S, rng)})
       GROUP BY dow, hour
    """
    grid = [[0.0] * 24 for _ in range(7)]
    with connect(db_path) as c:
        for dow, hour, secs in c.execute(sql, args):
            if dow is None or hour is None:
                continue
            grid[int(dow)][int(hour)] = float(secs or 0)
    return {"grid": grid, "labels_dow": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]}


# Tool-name → phase category. The categorization is intentionally rough:
# four buckets that map to the typical project-lifecycle stages
# Greybox runs through (discovery → research → build → deploy/ops).
# Anything not listed below counts as "chat" for the dominant-phase
# calculation, which is the right default — meta tools (Skill, Task,
# AskUserQuestion, TodoWrite, etc.) usually accompany discovery turns.
_TOOL_PHASE = {
    "Write": "build", "Edit": "build", "MultiEdit": "build", "NotebookEdit": "build",
    "Read": "research", "Grep": "research", "Glob": "research", "WebFetch": "research", "WebSearch": "research",
    "Bash": "ops",
}


def phase_breakdown(db_path, since=None, until=None) -> dict:
    """Per-project distribution of session phase (discovery/research/build/ops).

    Each session's dominant tool category becomes its phase. Sessions with
    no tool calls are tagged 'chat' (pure conversation — discovery work).
    The result is suitable for stacked-bar rendering on the Clients tab.
    """
    from .db import _range_clause
    rng, args = _range_clause(since, until)

    with connect(db_path) as c:
        # Per-session tool call counts within the range.
        rows = list(c.execute(
            f"""
            SELECT t.session_id, t.tool_name, COUNT(*) AS n
              FROM tool_calls t
             WHERE t.tool_name != '_tool_result' {rng.replace('timestamp', 't.timestamp')}
             GROUP BY t.session_id, t.tool_name
            """,
            args,
        ))
        # All sessions in the range — including ones with no tool calls.
        all_sessions = {
            r[0]: r[1] for r in c.execute(
                "SELECT session_id, project_slug FROM summary_sessions "
                "WHERE ended >= COALESCE(?, '0000-01-01') AND started < COALESCE(?, '9999-12-31')",
                (since, until),
            )
        }
        slug_cwd, _ = _slug_meta(c)

    # Aggregate per session.
    session_phase_counts = {}
    for sid, tool, n in rows:
        cat = _TOOL_PHASE.get(tool, "chat")
        bucket = session_phase_counts.setdefault(sid, {})
        bucket[cat] = bucket.get(cat, 0) + int(n)

    # Dominant phase per session; ties broken by build > ops > research > chat.
    PRIORITY = {"build": 3, "ops": 2, "research": 1, "chat": 0}
    session_phase = {}
    for sid, counts in session_phase_counts.items():
        best = max(counts.items(), key=lambda kv: (kv[1], PRIORITY[kv[0]]))
        session_phase[sid] = best[0]
    for sid in all_sessions:
        session_phase.setdefault(sid, "chat")

    # Aggregate to (project_name, phase).
    per_project = {}
    name_cache = {}
    for sid, slug in all_sessions.items():
        name = name_cache.get(slug)
        if name is None:
            cwd = slug_cwd.get(slug)
            name = _collapse_pattern(cwd) or project_name_for(cwd, slug)
            name_cache[slug] = name
        phase = session_phase.get(sid, "chat")
        bucket = per_project.setdefault(name, {"chat": 0, "research": 0, "build": 0, "ops": 0, "total": 0})
        bucket[phase] += 1
        bucket["total"] += 1

    rows_out = [
        {"project_name": name, **counts}
        for name, counts in per_project.items()
        if counts["total"] > 0
    ]
    rows_out.sort(key=lambda r: -r["total"])
    return {"projects": rows_out, "categories": ["build", "ops", "research", "chat"]}


def activity_digest(db_path, since=None, until=None, limit: int = 10) -> dict:
    """Highlight reel for a period: top sessions by active time,
    top prompts by tokens. Designed to answer 'what did I actually do
    last week' without scrolling Sessions/Prompts tabs.
    """
    from .db import _range_clause, expensive_prompts
    sess_where, sess_args = [], []
    if since:
        sess_where.append("ended >= ?"); sess_args.append(since)
    if until:
        sess_where.append("started < ?"); sess_args.append(until)
    where_clause = (" WHERE " + " AND ".join(sess_where)) if sess_where else ""

    with connect(db_path) as c:
        top_sessions_raw = list(c.execute(
            f"""
            SELECT session_id, project_slug, host, started, ended,
                   active_seconds, turns,
                   input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens AS billable
              FROM summary_sessions
              {where_clause}
             ORDER BY active_seconds DESC
             LIMIT ?
            """,
            sess_args + [limit],
        ))
        slug_cwd, _ = _slug_meta(c)

    # Resolve project_name per row.
    top_sessions = []
    name_cache = {}
    for r in top_sessions_raw:
        sid, slug, host, started, ended, secs, turns, billable = r
        name = name_cache.get(slug)
        if name is None:
            cwd = slug_cwd.get(slug)
            name = _collapse_pattern(cwd) or project_name_for(cwd, slug)
            name_cache[slug] = name
        top_sessions.append({
            "session_id": sid,
            "project_name": name,
            "host": host,
            "started": started,
            "ended": ended,
            "active_seconds": float(secs or 0),
            "turns": int(turns or 0),
            "billable_tokens": int(billable or 0),
        })

    # Top prompts by tokens already exists.
    top_prompts = expensive_prompts(db_path, limit=limit)
    return {"top_sessions": top_sessions, "top_prompts": top_prompts}


def export_client(db_path, client_id: int, since=None, until=None, fmt: str = "csv") -> tuple:
    """Return (content_type, body_str) for a per-client time export.

    `fmt='csv'`     — one row per contributing project + total row.
    `fmt='markdown'` — invoice-ready bullet list.
    """
    client = get_client(db_path, client_id)
    if not client:
        raise KeyError(f"client {client_id} not found")
    summary = client_summary(db_path, since=since, until=until)
    cl = next((c for c in summary["clients"] if c["id"] == client_id), None)
    if not cl:
        # Client exists but has no work in this range — emit an empty
        # report so the exporter still works.
        cl = {**client, "hours": 0, "projects": [],
              "billable_amount": None, "currency": client["currency"]}

    range_str = _range_label(since, until)
    if fmt == "markdown":
        return ("text/markdown; charset=utf-8", _md_invoice(cl, range_str))
    return ("text/csv; charset=utf-8", _csv_invoice(cl, range_str))


def _range_label(since, until) -> str:
    if not since and not until:
        return "all time"
    s = (since or "")[:10] or "start"
    u = (until or "")[:10] or "now"
    return f"{s} → {u}"


def _csv_invoice(cl: dict, range_str: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["client", cl["name"], "", "range", range_str])
    w.writerow(["pricing_mode", cl.get("pricing_mode") or ""])
    w.writerow(["currency", cl.get("currency") or ""])
    if cl.get("pricing_mode") == "hourly" and cl.get("hourly_rate"):
        w.writerow(["hourly_rate", cl["hourly_rate"]])
    if cl.get("pricing_mode") == "fixed" and cl.get("fixed_price"):
        w.writerow(["fixed_price", cl["fixed_price"]])
    w.writerow([])
    w.writerow(["project", "active_hours", "sessions", "billable_tokens", "match_source"])
    for p in cl.get("projects", []):
        w.writerow([
            p["project_name"],
            round(p["active_seconds"] / 3600, 2),
            p["sessions"],
            p["billable_tokens"],
            p.get("match_source") or "",
        ])
    w.writerow([])
    w.writerow(["total_active_hours", cl.get("hours") or 0])
    w.writerow(["billable_amount", cl.get("billable_amount") or ""])
    if cl.get("effective_rate") is not None:
        w.writerow(["effective_hourly_rate", cl["effective_rate"]])
    return buf.getvalue()


def _md_invoice(cl: dict, range_str: str) -> str:
    lines = [
        f"# {cl['name']} — {range_str}",
        "",
        f"- **Total active time:** {cl.get('hours') or 0:.2f} hours",
    ]
    mode = cl.get("pricing_mode")
    amt = cl.get("billable_amount")
    cur = cl.get("currency") or ""
    if mode == "hourly" and amt is not None:
        lines.append(f"- **Rate:** {cl.get('hourly_rate')} {cur}/h")
        lines.append(f"- **Amount due:** {amt} {cur}")
    elif mode == "fixed":
        lines.append(f"- **Fixed price:** {cl.get('fixed_price')} {cur}")
        if cl.get("effective_rate") is not None:
            lines.append(f"- **Effective hourly rate:** {cl['effective_rate']} {cur}/h")
    else:
        lines.append("- **Amount due:** (no rate configured)")
    lines.append("")
    lines.append("## Breakdown by project")
    lines.append("")
    for p in cl.get("projects", []):
        h = p["active_seconds"] / 3600
        lines.append(f"- **{p['project_name']}** — {h:.2f}h · {p['sessions']} sessions")
    return "\n".join(lines) + "\n"
