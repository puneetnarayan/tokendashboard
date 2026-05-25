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

from .db import connect, project_summary, ACTIVE_GAP_THRESHOLD_S


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
