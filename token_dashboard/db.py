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
  host                    TEXT NOT NULL DEFAULT 'jstation',
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
CREATE INDEX IF NOT EXISTS idx_messages_host      ON messages(host);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_model     ON messages(model);
CREATE INDEX IF NOT EXISTS idx_messages_msgid     ON messages(session_id, message_id);
CREATE INDEX IF NOT EXISTS idx_messages_date      ON messages(substr(timestamp,1,10));
CREATE INDEX IF NOT EXISTS idx_messages_type_model ON messages(type, model);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp_session ON messages(timestamp, session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp_project ON messages(timestamp, project_slug);
CREATE INDEX IF NOT EXISTS idx_messages_type_timestamp_model ON messages(type, timestamp, model);

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
CREATE INDEX IF NOT EXISTS idx_tools_timestamp_name ON tool_calls(timestamp, tool_name);

CREATE TABLE IF NOT EXISTS plan (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS dismissed_tips (
  tip_key       TEXT PRIMARY KEY,
  dismissed_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_status (
  host             TEXT PRIMARY KEY,
  root_path        TEXT NOT NULL,
  files_total      INTEGER NOT NULL DEFAULT 0,
  last_scan_at     TEXT,
  last_scan_secs   REAL
);

CREATE TABLE IF NOT EXISTS summary_daily (
  day                    TEXT PRIMARY KEY,
  turns                  INTEGER NOT NULL DEFAULT 0,
  input_tokens           INTEGER NOT NULL DEFAULT 0,
  output_tokens          INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS summary_projects (
  day                    TEXT NOT NULL,
  project_slug           TEXT NOT NULL,
  sample_cwd             TEXT,
  turns                  INTEGER NOT NULL DEFAULT 0,
  input_tokens           INTEGER NOT NULL DEFAULT 0,
  output_tokens          INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, project_slug)
);

CREATE TABLE IF NOT EXISTS summary_models (
  day                    TEXT NOT NULL,
  model                  TEXT NOT NULL,
  turns                  INTEGER NOT NULL DEFAULT 0,
  input_tokens           INTEGER NOT NULL DEFAULT 0,
  output_tokens          INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS summary_tools (
  day           TEXT NOT NULL,
  tool_name     TEXT NOT NULL,
  calls         INTEGER NOT NULL DEFAULT 0,
  result_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, tool_name)
);

CREATE TABLE IF NOT EXISTS summary_sessions (
  session_id              TEXT PRIMARY KEY,
  project_slug            TEXT NOT NULL,
  host                    TEXT NOT NULL DEFAULT 'jstation',
  sample_cwd              TEXT,
  started                 TEXT NOT NULL,
  ended                   TEXT NOT NULL,
  turns                   INTEGER NOT NULL DEFAULT 0,
  input_tokens            INTEGER NOT NULL DEFAULT 0,
  output_tokens           INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens  INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_summary_sessions_ended ON summary_sessions(ended);
CREATE INDEX IF NOT EXISTS idx_summary_sessions_project ON summary_sessions(project_slug);
CREATE INDEX IF NOT EXISTS idx_summary_sessions_host ON summary_sessions(host);
"""


def default_db_path() -> Path:
    return Path.home() / ".claude" / "token-dashboard.db"


def search_prompts(db_path, query: str, limit: int = 50) -> list:
    """Full-text search over user prompts, FTS5-backed.

    Returns hits ranked by relevance with a 32-token snippet showing the
    surrounding context (matched terms wrapped in <mark>). Each hit
    includes session_id + project so the UI can link to session detail.

    Query syntax is raw FTS5 — supports phrase queries, prefix (foo*),
    boolean (foo AND bar). Caller is responsible for catching
    sqlite3.OperationalError on malformed queries.
    """
    q = (query or "").strip()
    if not q:
        return []
    sql = """
      SELECT m.uuid, m.session_id, m.host, m.project_slug, m.timestamp, m.cwd,
             snippet(messages_fts, 0, '<mark>', '</mark>', '…', 24) AS snippet,
             rank
        FROM messages_fts
        JOIN messages m ON m.rowid = messages_fts.rowid
       WHERE messages_fts MATCH ?
       ORDER BY rank
       LIMIT ?
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (q, limit))]


def scan_status(db_path) -> dict:
    """Per-host scan progress for the dashboard staleness indicator.

    Each scan_status row carries the host's configured root_path; we count
    rows in the files table whose path is under that prefix to derive
    files_ingested. Coupled with files_total (the rglob count from the
    most recent scan) the UI can render an "X / Y indexed" pill.
    """
    with connect(db_path) as c:
        rows = list(c.execute(
            "SELECT host, root_path, files_total, last_scan_at, last_scan_secs "
            "FROM scan_status ORDER BY host"
        ))
        hosts = []
        for r in rows:
            root = (r["root_path"] or "").rstrip("/")
            # Range comparison instead of LIKE so `%` and `_` inside a
            # path (rare on Unix but legal) don't act as SQL wildcards.
            # The upper bound is the prefix followed by U+10FFFF (the
            # largest valid Unicode code point) — guarantees inclusion
            # of every path that starts with the prefix.
            if root:
                lo = root + "/"
                hi = root + "/\U0010FFFF"
                ingested = c.execute(
                    "SELECT COUNT(*) FROM files WHERE path >= ? AND path < ?",
                    (lo, hi),
                ).fetchone()[0]
            else:
                ingested = 0
            total = r["files_total"] or 0
            pct = round(100 * ingested / total) if total else 0
            hosts.append({
                "host": r["host"],
                "files_total": total,
                "files_ingested": ingested,
                "pct": pct,
                "last_scan_at": r["last_scan_at"],
                "last_scan_secs": r["last_scan_secs"],
            })
        last_at = max(
            (h["last_scan_at"] for h in hosts if h["last_scan_at"]),
            default=None,
        )
        return {"hosts": hosts, "last_scan_at": last_at}


def init_db(path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        _migrate_add_message_id(c)
        _migrate_add_host(c)
        _migrate_add_host_summary(c)
        c.executescript(SCHEMA)
        _migrate_add_fts(c)


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


def _migrate_add_host(conn) -> None:
    """Add messages.host so the dashboard can attribute usage per machine.

    Why: the scanner now reads from multiple roots (JLAP, JPC, Jstation),
    and the UI needs to break out usage by host. Existing rows pre-migration
    all came from the local Jstation scan, so backfill them as 'jstation'.
    How to apply: ALTER if column is missing; preserve existing data (no
    rescan needed since the host value is a static backfill, not derived
    from JSONL content).
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    if not has_table:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "host" in cols:
        return
    conn.execute(
        "ALTER TABLE messages ADD COLUMN host TEXT NOT NULL DEFAULT 'jstation'"
    )
    conn.execute("UPDATE messages SET host='jstation' WHERE host IS NULL OR host=''")
    conn.commit()


def _migrate_add_fts(conn) -> None:
    """Build the FTS5 virtual table over prompt_text so the Search tab can
    find sessions by what you typed. Triggers keep the index in sync with
    the messages table; a one-time backfill seeds it from existing rows.

    Why init_db runs this AFTER executescript(SCHEMA): the FTS triggers
    reference the messages table, so it must already exist. Tables created
    in executescript don't trigger migrations to fire mid-stream.
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    if has:
        return
    conn.executescript("""
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            prompt_text,
            content='messages',
            content_rowid='rowid',
            tokenize='porter unicode61'
        );
        CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, prompt_text)
            VALUES (new.rowid, new.prompt_text);
        END;
        CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, prompt_text)
            VALUES('delete', old.rowid, old.prompt_text);
        END;
        CREATE TRIGGER messages_fts_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, prompt_text)
            VALUES('delete', old.rowid, old.prompt_text);
            INSERT INTO messages_fts(rowid, prompt_text)
            VALUES (new.rowid, new.prompt_text);
        END;
    """)
    # Backfill from existing rows. NULLs become empty strings — FTS5
    # tolerates empty content but won't index it for matching.
    conn.execute(
        "INSERT INTO messages_fts(rowid, prompt_text) "
        "SELECT rowid, prompt_text FROM messages WHERE prompt_text IS NOT NULL"
    )
    conn.commit()


def _migrate_add_host_summary(conn) -> None:
    """Add summary_sessions.host and invalidate the summary cache.

    Why: recent_sessions reads from the warm summary table; without a host
    column there, the UI can't tag sessions by machine even though messages
    already carry the attribution.
    How to apply: ALTER if missing, then clear summary_meta so the next
    scan triggers a full rebuild — that repopulates summary_sessions with
    host pulled correctly from messages.host.
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='summary_sessions'"
    ).fetchone()
    if not has_table:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(summary_sessions)")}
    if "host" in cols:
        return
    conn.execute(
        "ALTER TABLE summary_sessions ADD COLUMN host TEXT NOT NULL DEFAULT 'jstation'"
    )
    # Force a full summary rebuild on next scan so host gets joined in.
    conn.execute("DELETE FROM summary_meta WHERE k='last_rebuild'")
    conn.commit()


@contextmanager
def connect(path: Union[str, Path]):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA cache_size=-65536")  # 64 MB page cache ceiling
    # synchronous=NORMAL is safe with journal_mode=WAL: writes survive process
    # crashes, only an OS-level crash mid-fsync risks the last transaction.
    # FULL (the default) fsyncs on every commit — with BATCH_SIZE=20 and
    # thousands of files to ingest, that turns a quick walk into hours of
    # fsync stalls on the spinning /mnt/data disk.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
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


def _date_range_clause(since, until, col: str = "substr(timestamp, 1, 10)"):
    where, args = [], []
    if since:
        where.append(f"{col} >= ?"); args.append(since[:10])
    if until:
        where.append(f"{col} < ?"); args.append(until[:10])
    return ((" AND " + " AND ".join(where)) if where else "", args)


def _summary_ready(conn) -> bool:
    row = conn.execute("SELECT v FROM summary_meta WHERE k='last_rebuild'").fetchone()
    return row is not None


def summaries_ready(db_path) -> bool:
    with connect(db_path) as c:
        return _summary_ready(c)


def rebuild_summaries(db_path, days=None, sessions=None) -> None:
    """Rebuild aggregate tables used by overview endpoints.

    These summaries keep refresh-time overview queries bounded by days,
    projects, tools, models, and sessions instead of raw message volume.
    """
    days = {d for d in (days or set()) if d}
    sessions = {s for s in (sessions or set()) if s}
    full = not days and not sessions
    with connect(db_path) as c:
        if full:
            c.execute("DELETE FROM summary_meta")
            c.execute("DELETE FROM summary_daily")
            c.execute("DELETE FROM summary_projects")
            c.execute("DELETE FROM summary_models")
            c.execute("DELETE FROM summary_tools")
            c.execute("DELETE FROM summary_sessions")
            day_filter = ""
            day_args = ()
            session_filter = ""
            session_args = ()
        else:
            day_args = tuple(sorted(days))
            session_args = tuple(sorted(sessions))
            day_ph = ",".join("?" * len(day_args))
            session_ph = ",".join("?" * len(session_args))
            day_filter = f" AND substr(timestamp, 1, 10) IN ({day_ph})" if day_args else " AND 0"
            session_filter = f" AND session_id IN ({session_ph})" if session_args else " AND 0"
            if day_args:
                c.execute(f"DELETE FROM summary_daily WHERE day IN ({day_ph})", day_args)
                c.execute(f"DELETE FROM summary_projects WHERE day IN ({day_ph})", day_args)
                c.execute(f"DELETE FROM summary_models WHERE day IN ({day_ph})", day_args)
                c.execute(f"DELETE FROM summary_tools WHERE day IN ({day_ph})", day_args)
            if session_args:
                c.execute(f"DELETE FROM summary_sessions WHERE session_id IN ({session_ph})", session_args)

        c.execute("""
          INSERT INTO summary_daily (
            day, turns, input_tokens, output_tokens, cache_read_tokens,
            cache_create_5m_tokens, cache_create_1h_tokens
          )
          SELECT substr(timestamp, 1, 10) AS day,
                 SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
                 COALESCE(SUM(input_tokens),0),
                 COALESCE(SUM(output_tokens),0),
                 COALESCE(SUM(cache_read_tokens),0),
                 COALESCE(SUM(cache_create_5m_tokens),0),
                 COALESCE(SUM(cache_create_1h_tokens),0)
            FROM messages
           WHERE timestamp IS NOT NULL
        """ + day_filter + """
           GROUP BY day
        """, day_args)

        c.execute("""
          INSERT INTO summary_projects (
            day, project_slug, sample_cwd, turns, input_tokens, output_tokens,
            cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens
          )
          SELECT substr(timestamp, 1, 10) AS day,
                 project_slug,
                 MIN(cwd),
                 SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
                 COALESCE(SUM(input_tokens),0),
                 COALESCE(SUM(output_tokens),0),
                 COALESCE(SUM(cache_read_tokens),0),
                 COALESCE(SUM(cache_create_5m_tokens),0),
                 COALESCE(SUM(cache_create_1h_tokens),0)
            FROM messages
           WHERE timestamp IS NOT NULL
        """ + day_filter + """
           GROUP BY day, project_slug
        """, day_args)

        c.execute("""
          INSERT INTO summary_models (
            day, model, turns, input_tokens, output_tokens, cache_read_tokens,
            cache_create_5m_tokens, cache_create_1h_tokens
          )
          SELECT substr(timestamp, 1, 10) AS day,
                 COALESCE(model, 'unknown') AS model,
                 COUNT(*) AS turns,
                 COALESCE(SUM(input_tokens),0),
                 COALESCE(SUM(output_tokens),0),
                 COALESCE(SUM(cache_read_tokens),0),
                 COALESCE(SUM(cache_create_5m_tokens),0),
                 COALESCE(SUM(cache_create_1h_tokens),0)
            FROM messages
           WHERE type='assistant' AND timestamp IS NOT NULL
        """ + day_filter + """
           GROUP BY day, COALESCE(model, 'unknown')
        """, day_args)

        c.execute("""
          INSERT INTO summary_tools (day, tool_name, calls, result_tokens)
          SELECT substr(timestamp, 1, 10) AS day,
                 tool_name,
                 COUNT(*) AS calls,
                 COALESCE(SUM(result_tokens),0) AS result_tokens
            FROM tool_calls
           WHERE tool_name != '_tool_result' AND timestamp IS NOT NULL
        """ + day_filter + """
           GROUP BY day, tool_name
        """, day_args)

        c.execute("""
          INSERT INTO summary_sessions (
            session_id, project_slug, host, sample_cwd, started, ended, turns,
            input_tokens, output_tokens, cache_read_tokens,
            cache_create_5m_tokens, cache_create_1h_tokens
          )
          SELECT session_id,
                 MIN(project_slug),
                 MIN(host),
                 MIN(cwd),
                 MIN(timestamp),
                 MAX(timestamp),
                 SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
                 COALESCE(SUM(input_tokens),0),
                 COALESCE(SUM(output_tokens),0),
                 COALESCE(SUM(cache_read_tokens),0),
                 COALESCE(SUM(cache_create_5m_tokens),0),
                 COALESCE(SUM(cache_create_1h_tokens),0)
            FROM messages
           WHERE 1=1
        """ + session_filter + """
           GROUP BY session_id
        """, session_args)

        c.execute(
            "INSERT OR REPLACE INTO summary_meta (k, v) VALUES ('last_rebuild', strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        )
        c.commit()


def _session_range_clause(since, until):
    where, args = [], []
    if since:
        where.append("ended >= ?"); args.append(since)
    if until:
        where.append("started < ?"); args.append(until)
    return ((" WHERE " + " AND ".join(where)) if where else "", args)


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
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            sess_where, sess_args = _session_range_clause(since, until)
            totals = dict(c.execute(f"""
              SELECT COALESCE(SUM(turns),0)                  AS turns,
                     COALESCE(SUM(input_tokens),0)           AS input_tokens,
                     COALESCE(SUM(output_tokens),0)          AS output_tokens,
                     COALESCE(SUM(cache_read_tokens),0)      AS cache_read_tokens,
                     COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
                     COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens
                FROM summary_daily
               WHERE 1=1 {day_rng}
            """, day_args).fetchone())
            totals["sessions"] = c.execute(
                f"SELECT COUNT(*) FROM summary_sessions{sess_where}", sess_args
            ).fetchone()[0]
            return totals
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
             MIN(cwd) AS sample_cwd,
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
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            sess_where, sess_args = _session_range_clause(since, until)
            session_sql = f"""
              SELECT project_slug, COUNT(*) AS sessions
                FROM summary_sessions
                {sess_where}
               GROUP BY project_slug
            """
            rows = [dict(r) for r in c.execute(f"""
              SELECT p.project_slug,
                     MIN(p.sample_cwd) AS sample_cwd,
                     COALESCE(s.sessions, 0) AS sessions,
                     COALESCE(SUM(p.turns), 0) AS turns,
                     COALESCE(SUM(p.input_tokens), 0) AS input_tokens,
                     COALESCE(SUM(p.output_tokens), 0) AS output_tokens,
                     COALESCE(SUM(p.input_tokens),0)+COALESCE(SUM(p.output_tokens),0)
                       +COALESCE(SUM(p.cache_create_5m_tokens),0)
                       +COALESCE(SUM(p.cache_create_1h_tokens),0) AS billable_tokens,
                     COALESCE(SUM(p.cache_read_tokens), 0) AS cache_read_tokens
                FROM summary_projects p
                LEFT JOIN ({session_sql}) s ON s.project_slug = p.project_slug
               WHERE 1=1 {day_rng}
               GROUP BY p.project_slug
               ORDER BY billable_tokens DESC
            """, (*sess_args, *day_args))]
            for r in rows:
                r["project_name"] = project_name_for(r.pop("sample_cwd", None), r["project_slug"])
            return rows
        rows = [dict(r) for r in c.execute(sql, args)]
        for r in rows:
            r["project_name"] = project_name_for(r.pop("sample_cwd", None), r["project_slug"])
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
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            return [dict(r) for r in c.execute(f"""
              SELECT tool_name,
                     COALESCE(SUM(calls),0) AS calls,
                     COALESCE(SUM(result_tokens),0) AS result_tokens
                FROM summary_tools
               WHERE 1=1 {day_rng}
               GROUP BY tool_name
               ORDER BY calls DESC
            """, day_args)]
        return [dict(r) for r in c.execute(sql, args)]


def recent_sessions(db_path, limit: int = 20, since=None, until=None) -> list:
    rng, args = _range_clause(since, until)
    sql = f"""
      SELECT session_id, project_slug,
             MIN(host) AS host,
             MIN(cwd) AS sample_cwd,
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
        if _summary_ready(c):
            sess_where, sess_args = _session_range_clause(since, until)
            rows = [dict(r) for r in c.execute(f"""
              SELECT session_id, project_slug, host, sample_cwd,
                     started, ended, turns,
                     input_tokens + output_tokens AS tokens
                FROM summary_sessions
                {sess_where}
               ORDER BY ended DESC
               LIMIT ?
            """, (*sess_args, limit))]
            for r in rows:
                r["project_name"] = project_name_for(r.pop("sample_cwd", None), r["project_slug"])
            return rows
        rows = [dict(r) for r in c.execute(sql, (*args, limit))]
        for r in rows:
            r["project_name"] = project_name_for(r.pop("sample_cwd", None), r["project_slug"])
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
    rng, args = _date_range_clause(since, until)
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
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            return [dict(r) for r in c.execute(f"""
              SELECT day,
                     input_tokens,
                     output_tokens,
                     cache_read_tokens,
                     cache_create_5m_tokens + cache_create_1h_tokens AS cache_create_tokens
                FROM summary_daily
               WHERE 1=1 {day_rng}
               ORDER BY day ASC
            """, day_args)]
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
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            return [dict(r) for r in c.execute(f"""
              SELECT model,
                     COALESCE(SUM(turns),0) AS turns,
                     COALESCE(SUM(input_tokens),0) AS input_tokens,
                     COALESCE(SUM(output_tokens),0) AS output_tokens,
                     COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                     COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
                     COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens
                FROM summary_models
               WHERE 1=1 {day_rng}
               GROUP BY model
               ORDER BY (input_tokens + output_tokens + cache_create_5m_tokens + cache_create_1h_tokens) DESC
            """, day_args)]
        return [dict(r) for r in c.execute(sql, args)]
