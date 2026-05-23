"""SQLite schema, connection, and shared query helpers."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path        TEXT PRIMARY KEY,
  mtime       REAL    NOT NULL,
  bytes_read  INTEGER NOT NULL,
  scanned_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  uuid                    TEXT PRIMARY KEY,
  parent_uuid             TEXT,
  session_id              TEXT NOT NULL,
  project_slug            TEXT NOT NULL,
  cwd                     TEXT,
  git_branch              TEXT,
  cc_version              TEXT,
  entrypoint              TEXT,
  type                    TEXT NOT NULL,
  is_sidechain            INTEGER NOT NULL DEFAULT 0,
  agent_id                TEXT,
  timestamp               TEXT NOT NULL,
  model                   TEXT,
  stop_reason             TEXT,
  prompt_id               TEXT,
  message_id              TEXT,
  input_tokens            INTEGER NOT NULL DEFAULT 0,
  output_tokens           INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens  INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens  INTEGER NOT NULL DEFAULT 0,
  prompt_text             TEXT,
  prompt_chars            INTEGER,
  tool_calls_json         TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_project   ON messages(project_slug);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_model     ON messages(model);
CREATE INDEX IF NOT EXISTS idx_messages_msgid     ON messages(session_id, message_id);

CREATE TABLE IF NOT EXISTS tool_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  message_uuid  TEXT    NOT NULL,
  session_id    TEXT    NOT NULL,
  project_slug  TEXT    NOT NULL,
  tool_name     TEXT    NOT NULL,
  target        TEXT,
  result_tokens INTEGER,
  is_error      INTEGER NOT NULL DEFAULT 0,
  timestamp     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tools_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tools_name    ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tools_target  ON tool_calls(target);

CREATE TABLE IF NOT EXISTS plan (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS dismissed_tips (
  tip_key       TEXT PRIMARY KEY,
  dismissed_at  REAL NOT NULL
);
"""


def default_db_path() -> Path:
    return Path.home() / ".claude" / "token-dashboard.db"


def init_db(path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as c:
        _migrate_add_message_id(c)
        c.executescript(SCHEMA)


def _migrate_add_message_id(conn) -> None:
    """Add messages.message_id for streaming-snapshot dedup.

    Why: pre-migration rows were summed from all streaming snapshots (over-count).
    How to apply: if the old table exists without the column, add it and clear
    messages/tool_calls/files so the next scan replays JSONLs cleanly. Source
    of truth is on disk; rescanning is cheap.
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    if not has_table:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "message_id" in cols:
        return
    conn.execute("ALTER TABLE messages ADD COLUMN message_id TEXT")
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM tool_calls")
    conn.execute("DELETE FROM files")
    conn.commit()


@contextmanager
def connect(path: Union[str, Path]):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _range_clause(since, until, col: str = "timestamp"):
    where, args = [], []
    if since:
        where.append(f"{col} >= ?"); args.append(since)
    if until:
        where.append(f"{col} < ?"); args.append(until)
    return ((" AND " + " AND ".join(where)) if where else "", args)


def _encode_slug(path: str) -> str:
    """Claude Code's project-slug encoding: each of `:`, `\\`, `/`, space → one `-`."""
    return re.sub(r"[:\\/ ]", "-", path)


def _walk_to_root(cwd: str, slug: str) -> Optional[str]:
    """If any ancestor of cwd encodes to slug, return that ancestor's basename."""
    if not cwd or not slug:
        return None
    trimmed = cwd.rstrip("/\\")
    sep = "\\" if "\\" in trimmed else "/"
    parts = trimmed.split(sep)
    for i in range(len(parts), 0, -1):
        if _encode_slug(sep.join(parts[:i])) == slug:
            name = parts[i - 1]
            if name:
                return name
    return None


def project_name_for(cwd: Optional[str], fallback_slug: str) -> str:
    """Pretty project name from a single cwd + slug (best-effort).

    For the multi-cwd case, prefer `best_project_name`.
    """
    name = _walk_to_root(cwd or "", fallback_slug or "")
    if name:
        return name
    if cwd:
        trimmed = cwd.rstrip("/\\")
        sep = "\\" if "\\" in trimmed else "/"
        tail = trimmed.split(sep)[-1]
        if tail:
            return tail
    if fallback_slug:
        parts = [p for p in re.split(r"-+", fallback_slug) if p]
        if parts:
            return parts[-1]
    return fallback_slug or ""


def best_project_name(cwds, slug: str) -> str:
    """Pick a pretty name from a list of cwds.

    Prefer a cwd whose walk-up matches `slug` (a true descendant of the project
    root). If none match, fall back to `project_name_for` on the first cwd,
    then to the slug's last segment.
    """
    cwds = [c for c in (cwds or []) if c]
    for cwd in cwds:
        name = _walk_to_root(cwd, slug)
        if name:
            return name
    return project_name_for(cwds[0] if cwds else None, slug)


def overview_totals(db_path, since=None, until=None) -> dict:
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT COUNT(DISTINCT session_id) AS sessions,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             COALESCE(SUM(input_tokens),0)            AS input_tokens,
             COALESCE(SUM(output_tokens),0)           AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)       AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0)  AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0)  AS cache_create_1h_tokens
        FROM messages WHERE 1=1 {rng}
    """
    with connect(db_path) as c:
        return dict(c.execute(sql, args).fetchone())


def expensive_prompts(db_path, limit: int = 50, sort: str = "tokens") -> list:
    """User prompt joined with the immediately-following assistant turn's tokens.

    sort="tokens" (default) → largest billable first.
    sort="recent"           → newest first.
    """
    order = "u.timestamp DESC" if sort == "recent" else "billable_tokens DESC"
    sql = f"""
      SELECT u.uuid AS user_uuid, u.session_id, u.project_slug, u.timestamp,
             u.prompt_text, u.prompt_chars,
             a.uuid AS assistant_uuid, a.model,
             COALESCE(a.input_tokens,0)+COALESCE(a.output_tokens,0)
               +COALESCE(a.cache_create_5m_tokens,0)+COALESCE(a.cache_create_1h_tokens,0) AS billable_tokens,
             COALESCE(a.cache_read_tokens,0) AS cache_read_tokens
        FROM messages u
        JOIN messages a ON a.parent_uuid = u.uuid AND a.type='assistant'
       WHERE u.type='user' AND u.prompt_text IS NOT NULL
       ORDER BY {order}
       LIMIT ?
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (limit,))]


def project_summary(db_path, since=None, until=None) -> list:
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT project_slug,
             COUNT(DISTINCT session_id) AS sessions,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             COALESCE(SUM(input_tokens), 0)  AS input_tokens,
             COALESCE(SUM(output_tokens), 0) AS output_tokens,
             SUM(input_tokens)+SUM(output_tokens)
               +SUM(cache_create_5m_tokens)+SUM(cache_create_1h_tokens) AS billable_tokens,
             SUM(cache_read_tokens) AS cache_read_tokens
        FROM messages m
       WHERE 1=1 {rng}
       GROUP BY project_slug
       ORDER BY billable_tokens DESC
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, args)]
        for r in rows:
            cwds = [row["cwd"] for row in c.execute(
                "SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL",
                (r["project_slug"],),
            )]
            r["project_name"] = best_project_name(cwds, r["project_slug"])
    return rows


def tool_token_breakdown(db_path, since=None, until=None) -> list:
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT tool_name,
             COUNT(*) AS calls,
             COALESCE(SUM(result_tokens),0) AS result_tokens
        FROM tool_calls
       WHERE tool_name != '_tool_result' {rng}
       GROUP BY tool_name
       ORDER BY calls DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def recent_sessions(db_path, limit: int = 20, since=None, until=None) -> list:
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT session_id, project_slug,
             MIN(timestamp) AS started, MAX(timestamp) AS ended,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             SUM(input_tokens)+SUM(output_tokens) AS tokens
        FROM messages m
       WHERE 1=1 {rng}
       GROUP BY session_id
       ORDER BY ended DESC
       LIMIT ?
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, (*args, limit))]
        # Cache per-slug name lookups so we don't query once per session.
        slug_cache = {}
        for r in rows:
            slug = r["project_slug"]
            if slug not in slug_cache:
                cwds = [row["cwd"] for row in c.execute(
                    "SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL",
                    (slug,),
                )]
                slug_cache[slug] = best_project_name(cwds, slug)
            r["project_name"] = slug_cache[slug]
    return rows


def session_turns(db_path, session_id: str) -> list:
    sql = """
      SELECT uuid, parent_uuid, type, timestamp, model, is_sidechain, agent_id,
             input_tokens, output_tokens, cache_read_tokens,
             cache_create_5m_tokens, cache_create_1h_tokens,
             prompt_text, prompt_chars, tool_calls_json, project_slug, cwd
        FROM messages
       WHERE session_id = ?
       ORDER BY timestamp ASC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (session_id,))]


def daily_token_breakdown(db_path, since=None, until=None) -> list:
    """One row per day: stacked bar data for input/output/cache_read/cache_create."""
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT substr(timestamp, 1, 10) AS day,
             COALESCE(SUM(input_tokens),0)      AS input_tokens,
             COALESCE(SUM(output_tokens),0)     AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0)
               + COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_tokens
        FROM messages
       WHERE timestamp IS NOT NULL {rng}
       GROUP BY day
       ORDER BY day ASC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def skill_breakdown(db_path, since=None, until=None) -> list:
    """Per-skill invocation counts, distinct sessions, last-used timestamp.

    Token attribution per skill is not included: in Claude Code, a Skill's
    content is loaded via a system-reminder on the next turn, not as the
    tool_result body — so `result_tokens` on _tool_result rows reflects the
    activation ack (tiny), not the skill definition (which is what actually
    fills context). A future schema change (storing tool_use_id on the
    invocation row) could enable precise attribution; for now we only expose
    the reliable counts.
    """
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT target AS skill,
             COUNT(*) AS invocations,
             COUNT(DISTINCT session_id) AS sessions,
             MAX(timestamp) AS last_used
        FROM tool_calls
       WHERE tool_name = 'Skill' AND target IS NOT NULL AND target != '' {rng}
       GROUP BY target
       ORDER BY invocations DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def model_breakdown(db_path, since=None, until=None) -> list:
    """Per-model token totals + turn count. Caller computes cost via pricing."""
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT COALESCE(model, 'unknown') AS model,
             COUNT(*) AS turns,
             COALESCE(SUM(input_tokens),0)            AS input_tokens,
             COALESCE(SUM(output_tokens),0)           AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)       AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0)  AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0)  AS cache_create_1h_tokens
        FROM messages
       WHERE type = 'assistant' {rng}
       GROUP BY model
       ORDER BY (input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens) DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


# --- cross-workspace + subagent attribution -------------------------------


def _workspace_root_path(cwd: str, slug: str) -> Optional[str]:
    """Full ancestor path of cwd whose slug-encoding equals slug, or None.

    Unlike _walk_to_root (returns basename), this returns the full path so the
    caller can use it as a path prefix when classifying tool_call targets.
    """
    if not cwd or not slug:
        return None
    trimmed = cwd.rstrip("/\\")
    sep = "\\" if "\\" in trimmed else "/"
    parts = trimmed.split(sep)
    for i in range(len(parts), 0, -1):
        candidate = sep.join(parts[:i])
        if _encode_slug(candidate) == slug:
            return candidate
    return None


def _normalize_path(p: str) -> str:
    """Lowercase, forward-slash-free, trailing-separator-stripped path for prefix matching."""
    return (p or "").replace("/", "\\").lower().rstrip("\\")


def _build_workspace_index(conn) -> list:
    """List of (root_path_normalized, workspace_name) sorted by length desc.

    Built once per request from distinct (cwd, project_slug) pairs. Used to
    classify tool_call target paths into a workspace.
    """
    by_slug: dict = {}
    for r in conn.execute(
        "SELECT DISTINCT cwd, project_slug FROM messages "
        "WHERE cwd IS NOT NULL AND project_slug IS NOT NULL"
    ):
        by_slug.setdefault(r["project_slug"], []).append(r["cwd"])
    seen = set()
    out = []
    candidates = []
    for slug, cwds in by_slug.items():
        name = best_project_name(cwds, slug)
        roots = set()
        for cwd in cwds:
            root = _workspace_root_path(cwd, slug)
            roots.add(root or cwd.rstrip("/\\"))
        for root in roots:
            norm = _normalize_path(root)
            if norm:
                candidates.append((norm, name))
    candidates.sort(key=lambda x: -len(x[0]))
    for prefix, name in candidates:
        if prefix in seen:
            continue
        seen.add(prefix)
        out.append((prefix, name))
    return out


def _classify_path(path: str, index: list) -> str:
    """Return workspace name for a path, or 'external' when no prefix matches."""
    if not path:
        return "external"
    norm = _normalize_path(path)
    for prefix, name in index:
        if norm == prefix or norm.startswith(prefix + "\\"):
            return name
    return "external"


_CROSS_WS_TOOLS = ("Read", "Edit", "Write", "NotebookEdit")


def workspaces_matrix(db_path, since=None, until=None) -> dict:
    """Cross-workspace tool-target flow data shaped for an ECharts Sankey.

    Only Read/Edit/Write/NotebookEdit calls are counted — they uniquely
    identify a file. Glob/Grep patterns are skipped (often workspace-free).

    Layout is bipartite: src nodes (left column, suffixed " (agent)") and
    dst nodes (right column, suffixed " (files)"). This guarantees a DAG
    even when the raw data has self-loops (agent reading its own workspace)
    or opposite-direction pairs (A->B and B->A both happen) — both would
    crash ECharts' Sankey, which requires acyclic input.
    """
    rng, args = _range_clause(since, until, col="t.timestamp")
    sql = f"""
      SELECT m.cwd AS src_cwd, m.project_slug AS src_slug,
             t.target AS target, COUNT(*) AS n
        FROM tool_calls t JOIN messages m ON t.message_uuid = m.uuid
       WHERE t.tool_name IN ({",".join("?" * len(_CROSS_WS_TOOLS))})
         AND t.target IS NOT NULL AND t.target != ''
         AND m.cwd IS NOT NULL AND m.project_slug IS NOT NULL
         {rng}
       GROUP BY src_cwd, src_slug, target
    """
    src_nodes: set = set()
    dst_nodes: set = set()
    matrix: dict = {}
    self_loop_calls = 0
    cross_calls = 0
    with connect(db_path) as c:
        index = _build_workspace_index(c)
        for row in c.execute(sql, (*_CROSS_WS_TOOLS, *args)):
            src = project_name_for(row["src_cwd"], row["src_slug"]) or row["src_slug"] or "unknown"
            dst = _classify_path(row["target"], index)
            n = row["n"]
            if src == dst:
                self_loop_calls += n
            else:
                cross_calls += n
            src_label = f"{src} (agent)"
            dst_label = f"{dst} (files)"
            src_nodes.add(src_label); dst_nodes.add(dst_label)
            key = (src_label, dst_label)
            matrix[key] = matrix.get(key, 0) + n
    return {
        "nodes": [{"name": n} for n in sorted(src_nodes) + sorted(dst_nodes)],
        "links": [{"source": s, "target": t, "value": v} for (s, t), v in matrix.items()],
        "total_calls": sum(matrix.values()),
        "self_loop_calls": self_loop_calls,
        "cross_workspace_calls": cross_calls,
        "tools_considered": list(_CROSS_WS_TOOLS),
    }


def cross_workspace_leaks(db_path, limit: int = 20, since=None, until=None) -> list:
    """Top (src_workspace -> dst_workspace) pairs where src != dst.

    Includes touched-session count and top files per pair so the UI can drill
    into the actual repeat-read targets.
    """
    rng, args = _range_clause(since, until, col="t.timestamp")
    sql = f"""
      SELECT m.cwd AS src_cwd, m.project_slug AS src_slug,
             m.session_id AS src_session, t.target AS target, COUNT(*) AS n
        FROM tool_calls t JOIN messages m ON t.message_uuid = m.uuid
       WHERE t.tool_name IN ({",".join("?" * len(_CROSS_WS_TOOLS))})
         AND t.target IS NOT NULL AND t.target != ''
         AND m.cwd IS NOT NULL AND m.project_slug IS NOT NULL
         {rng}
       GROUP BY src_cwd, src_slug, src_session, target
    """
    pair: dict = {}
    with connect(db_path) as c:
        index = _build_workspace_index(c)
        for row in c.execute(sql, (*_CROSS_WS_TOOLS, *args)):
            src = project_name_for(row["src_cwd"], row["src_slug"]) or row["src_slug"] or "unknown"
            dst = _classify_path(row["target"], index)
            if src == dst:
                continue
            key = (src, dst)
            pd = pair.setdefault(key, {"calls": 0, "sessions": set(), "files": {}})
            pd["calls"] += row["n"]
            pd["sessions"].add(row["src_session"])
            pd["files"][row["target"]] = pd["files"].get(row["target"], 0) + row["n"]
    out = []
    for (src, dst), pd in pair.items():
        top_files = sorted(pd["files"].items(), key=lambda x: -x[1])[:5]
        out.append({
            "source": src,
            "target": dst,
            "calls": pd["calls"],
            "sessions": len(pd["sessions"]),
            "top_files": [{"path": p, "n": n} for p, n in top_files],
        })
    out.sort(key=lambda x: -x["calls"])
    return out[:limit]


def subagent_breakdown(db_path, since=None, until=None) -> list:
    """Per-model breakdown split by main vs sidechain (subagent dispatched via Task).

    Surfaces the attribution that model_breakdown aggregates away — e.g., the
    fact that Sonnet usage is almost entirely from subagent dispatch.
    """
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT COALESCE(model, 'unknown') AS model,
             is_sidechain,
             COUNT(*) AS messages,
             COUNT(DISTINCT session_id) AS sessions,
             COALESCE(SUM(input_tokens),0)           AS input_tokens,
             COALESCE(SUM(output_tokens),0)          AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)      AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens
        FROM messages
       WHERE type = 'assistant' AND model IS NOT NULL AND model != '<synthetic>'
       {rng}
       GROUP BY model, is_sidechain
       ORDER BY (input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens) DESC
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


_AGENT_KIND_SQL = """
  CASE
    WHEN is_sidechain = 0 THEN 'main'
    WHEN agent_id LIKE 'acompact-%' THEN 'compact'
    ELSE 'subagent'
  END
"""


def dispatch_tree(db_path, limit: int = 100, since=None, until=None) -> list:
    """Reconstruct dispatcher-prompt -> subagent thread relationships.

    Approach: the first sidechain message of a subagent thread has
    parent_uuid=NULL (the thread starts fresh), so we can't walk the message
    tree to find the dispatcher. Instead we join on Agent tool_call timing —
    every Agent tool call in main thread spawns a sidechain thread in the
    same session within milliseconds. Matching by (session_id, timestamp
    >= tc.timestamp, first one) recovers the link with >95% accuracy.

    Edge case: when a single main turn dispatches several Agent tools in
    parallel, the matching collapses them onto the first subagent thread;
    those rows show up as one dispatcher row even though multiple subagents
    were spawned.
    """
    rng, args = _range_clause(since, until, col="tc.timestamp")
    sql = f"""
      WITH dispatch AS (
        SELECT tc.session_id   AS session_id,
               tc.message_uuid AS dispatcher_uuid,
               tc.timestamp    AS dispatched_at,
               tc.target       AS subagent_type,
               m.model         AS dispatcher_model,
               m.project_slug  AS project_slug,
               (SELECT s.agent_id FROM messages s
                 WHERE s.session_id = tc.session_id
                   AND s.is_sidechain = 1
                   AND s.timestamp >= tc.timestamp
                   AND s.agent_id IS NOT NULL
                   AND s.agent_id NOT LIKE 'acompact-%'
                 ORDER BY s.timestamp ASC LIMIT 1) AS agent_id
          FROM tool_calls tc
          JOIN messages m ON m.uuid = tc.message_uuid
         WHERE tc.tool_name IN ('Agent','Task')
         {rng}
      ),
      agg AS (
        SELECT agent_id,
               COUNT(*) AS thread_msgs,
               GROUP_CONCAT(DISTINCT COALESCE(model,'unknown')) AS models,
               COALESCE(SUM(input_tokens + output_tokens),0)  AS io_tokens,
               COALESCE(SUM(cache_read_tokens),0)             AS cache_read_tokens,
               COALESCE(SUM(input_tokens),0)                  AS input_tokens,
               COALESCE(SUM(output_tokens),0)                 AS output_tokens,
               COALESCE(SUM(cache_create_5m_tokens),0)        AS cache_create_5m_tokens,
               COALESCE(SUM(cache_create_1h_tokens),0)        AS cache_create_1h_tokens
          FROM messages
         WHERE is_sidechain = 1 AND type = 'assistant'
           AND agent_id IS NOT NULL AND agent_id NOT LIKE 'acompact-%'
         GROUP BY agent_id
      )
      SELECT d.dispatcher_uuid, d.dispatcher_model, d.session_id,
             d.project_slug, d.dispatched_at, d.agent_id, d.subagent_type,
             agg.thread_msgs, agg.models, agg.io_tokens, agg.cache_read_tokens,
             agg.input_tokens, agg.output_tokens,
             agg.cache_create_5m_tokens, agg.cache_create_1h_tokens
        FROM dispatch d
        JOIN agg ON agg.agent_id = d.agent_id
       WHERE d.agent_id IS NOT NULL
       ORDER BY agg.io_tokens DESC
       LIMIT ?
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, (*args, limit))]
        slug_cache: dict = {}
        for r in rows:
            slug = r["project_slug"]
            if slug not in slug_cache:
                cwds = [r2["cwd"] for r2 in c.execute(
                    "SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL",
                    (slug,),
                )]
                slug_cache[slug] = best_project_name(cwds, slug)
            r["project_name"] = slug_cache[slug]
            r["models"] = sorted((r["models"] or "").split(",")) if r["models"] else []
    return rows


_TIER_ROUTING_SKILLS = (
    "orchestrate",
    "agentic-efficiency-harness",
    "pareto-cascade-delivery",
    "run-benchmark",
)


def tier_routing_stats(db_path, since=None, until=None) -> dict:
    """Surface how often tier-routing skills are invoked vs total sessions.

    Tier-routing skills (orchestrate, agentic-efficiency-harness,
    pareto-cascade-delivery, run-benchmark) are opt-in — they're not a
    default Claude Code behavior. This function quantifies the gap between
    'sessions where I could have tier-routed' and 'sessions where I did'.
    """
    rng, args = _range_clause(since, until)
    placeholders = ",".join("?" * len(_TIER_ROUTING_SKILLS))
    with connect(db_path) as c:
        total = c.execute(
            f"SELECT COUNT(DISTINCT session_id) AS n FROM messages WHERE 1=1 {rng}",
            args,
        ).fetchone()["n"] or 0
        rng_t, args_t = _range_clause(since, until)
        routed = c.execute(
            f"""SELECT COUNT(DISTINCT session_id) AS n FROM tool_calls
                WHERE tool_name = 'Skill' AND target IN ({placeholders}) {rng_t}""",
            (*_TIER_ROUTING_SKILLS, *args_t),
        ).fetchone()["n"] or 0
        # Per-skill breakdown for the panel.
        per_skill = [
            dict(r) for r in c.execute(
                f"""SELECT target AS skill, COUNT(*) AS invocations,
                          COUNT(DISTINCT session_id) AS sessions,
                          MAX(timestamp) AS last_used
                     FROM tool_calls
                    WHERE tool_name='Skill' AND target IN ({placeholders}) {rng_t}
                    GROUP BY target ORDER BY invocations DESC""",
                (*_TIER_ROUTING_SKILLS, *args_t),
            )
        ]
    pct = (routed / total * 100) if total else 0.0
    return {
        "total_sessions": total,
        "routed_sessions": routed,
        "routed_pct": round(pct, 1),
        "per_skill": per_skill,
        "skills_considered": list(_TIER_ROUTING_SKILLS),
    }


def orchestration_breakdown(db_path, since=None, until=None) -> dict:
    """Per-entrypoint + per-agent-kind attribution, including SDK external runs.

    Why this exists: /api/by-model and /api/subagents both aggregate dimensions
    that hide real attribution. In particular:

    * Auto-compaction (CC's internal context-compression subagent, agent_id
      LIKE 'acompact-*') is a separate kind from user-dispatched Task subagents
      but the original breakdown lumps both as 'sidechain'.
    * External orchestration via claude_agent_sdk (e.g. the
      AgenticEfficiencyHarness harness invoking Opus/Sonnet/Haiku from a
      Python script) writes its own JSONL with entrypoint='sdk-py'/'sdk-ts'/
      'sdk-cli'. These NEVER go through the Task tool so they have no
      is_sidechain marker — they show as main-thread messages from the
      orchestrator's perspective. Splitting on entrypoint surfaces them.

    Returns:
      by_kind: rows per (kind, model) — kind ∈ {main, compact, subagent}
      by_entrypoint: rows per (entrypoint, model) covering all assistant turns
      sdk_runs: SDK-entrypoint sessions grouped by cwd — the "external
        orchestration" clusters (AEH-style)
    """
    rng, args = _range_clause(since, until)
    by_kind_sql = f"""
      SELECT {_AGENT_KIND_SQL} AS kind,
             COALESCE(model, 'unknown') AS model,
             COUNT(*) AS messages,
             COUNT(DISTINCT session_id) AS sessions,
             COALESCE(SUM(input_tokens),0)           AS input_tokens,
             COALESCE(SUM(output_tokens),0)          AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)      AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens
        FROM messages
       WHERE type = 'assistant' AND model IS NOT NULL AND model != '<synthetic>'
       {rng}
       GROUP BY kind, model
       ORDER BY (input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens) DESC
    """
    by_ep_sql = f"""
      SELECT COALESCE(entrypoint, 'unknown') AS entrypoint,
             COALESCE(model, 'unknown') AS model,
             COUNT(*) AS messages,
             COUNT(DISTINCT session_id) AS sessions,
             COALESCE(SUM(input_tokens),0)           AS input_tokens,
             COALESCE(SUM(output_tokens),0)          AS output_tokens,
             COALESCE(SUM(cache_read_tokens),0)      AS cache_read_tokens,
             COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
             COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens
        FROM messages
       WHERE type = 'assistant' AND model IS NOT NULL AND model != '<synthetic>'
       {rng}
       GROUP BY entrypoint, model
       ORDER BY (input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens) DESC
    """
    sdk_runs_sql = f"""
      SELECT entrypoint, cwd, project_slug,
             COUNT(DISTINCT session_id) AS sessions,
             COUNT(*) AS messages,
             COALESCE(SUM(input_tokens + output_tokens),0) AS io_tokens,
             COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
             GROUP_CONCAT(DISTINCT COALESCE(model, 'unknown')) AS models
        FROM messages
       WHERE type = 'assistant' AND entrypoint LIKE 'sdk-%'
       {rng}
       GROUP BY entrypoint, cwd, project_slug
       ORDER BY io_tokens DESC
       LIMIT 25
    """
    with connect(db_path) as c:
        by_kind = [dict(r) for r in c.execute(by_kind_sql, args)]
        by_entrypoint = [dict(r) for r in c.execute(by_ep_sql, args)]
        sdk_runs = []
        slug_cache: dict = {}
        for row in c.execute(sdk_runs_sql, args):
            d = dict(row)
            slug = d["project_slug"]
            if slug not in slug_cache:
                cwds = [r2["cwd"] for r2 in c.execute(
                    "SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL",
                    (slug,),
                )]
                slug_cache[slug] = best_project_name(cwds, slug)
            d["workspace"] = slug_cache[slug]
            d["models"] = sorted((d["models"] or "").split(",")) if d["models"] else []
            sdk_runs.append(d)
    return {
        "by_kind": by_kind,
        "by_entrypoint": by_entrypoint,
        "sdk_runs": sdk_runs,
    }


def top_subagent_sessions(db_path, limit: int = 20, since=None, until=None) -> list:
    """Sessions ranked by total subagent (sidechain) tokens spent.

    The full dispatch tree (which Opus prompt dispatched which subagent) is
    reconstructable via dispatch_tree() — the first sidechain message's
    parent_uuid points to the dispatching main-thread assistant message.
    This function gives the lighter session-level rollup; use dispatch_tree
    for per-dispatch attribution.
    """
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT session_id,
             project_slug,
             COUNT(*) AS subagent_msgs,
             COALESCE(SUM(input_tokens + output_tokens),0) AS io_tokens,
             COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
             GROUP_CONCAT(DISTINCT COALESCE(model,'unknown')) AS models
        FROM messages
       WHERE is_sidechain = 1 AND type = 'assistant' AND model IS NOT NULL
       {rng}
       GROUP BY session_id
       ORDER BY io_tokens DESC
       LIMIT ?
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, (*args, limit))]
        slug_cache: dict = {}
        for r in rows:
            slug = r["project_slug"]
            if slug not in slug_cache:
                cwds = [row["cwd"] for row in c.execute(
                    "SELECT DISTINCT cwd FROM messages WHERE project_slug=? AND cwd IS NOT NULL",
                    (slug,),
                )]
                slug_cache[slug] = best_project_name(cwds, slug)
            r["project_name"] = slug_cache[slug]
            r["models"] = sorted((r["models"] or "").split(",")) if r["models"] else []
    return rows
