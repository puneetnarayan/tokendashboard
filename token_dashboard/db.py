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
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0,
  active_seconds         REAL    NOT NULL DEFAULT 0
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
  cache_create_1h_tokens  INTEGER NOT NULL DEFAULT 0,
  active_seconds          REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_summary_sessions_ended ON summary_sessions(ended);
CREATE INDEX IF NOT EXISTS idx_summary_sessions_project ON summary_sessions(project_slug);
CREATE INDEX IF NOT EXISTS idx_summary_sessions_host ON summary_sessions(host);
CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp);

-- Per-slug metadata cache. Populated during rebuild_summaries; lets the
-- project-rollup code skip a full messages scan on every overview call.
CREATE TABLE IF NOT EXISTS slug_meta (
  project_slug TEXT PRIMARY KEY,
  best_cwd     TEXT,
  host         TEXT
);

-- Clients: business entities you bill for. A "client" is a freelance
-- engagement, an internal team, or a stand-in label for personal time —
-- whichever framing fits. One client owns many `client_patterns`
-- (substring matchers against the rolled-up project_name); pattern
-- matching is case-insensitive, longest-match wins. `client_overrides`
-- lets a specific project escape its pattern (or stay un-billed) without
-- changing the pattern rules.
CREATE TABLE IF NOT EXISTS clients (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL UNIQUE,
  hourly_rate  REAL,
  fixed_price  REAL,
  currency     TEXT NOT NULL DEFAULT 'CAD',
  status       TEXT NOT NULL DEFAULT 'active',
  notes        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS client_patterns (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  pattern   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_client_patterns_client ON client_patterns(client_id);

-- project_name is the rolled-up display name (post _rollup_projects).
-- NULL client_id means "explicitly not billable" — the project shows
-- in reports but adds 0 to any client's hours.
CREATE TABLE IF NOT EXISTS client_overrides (
  project_name TEXT PRIMARY KEY,
  client_id    INTEGER REFERENCES clients(id) ON DELETE SET NULL,
  set_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
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
        _migrate_add_active_seconds(c)
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


def _migrate_add_active_seconds(conn) -> None:
    """Add active_seconds to summary_daily + summary_sessions.

    Why: previously /api/overview-bundle ran the LAG-based query 3×
    (curr range, prev range, per-slug rollup) on raw messages — ~2.6s
    per call on a 1M-row DB. Precomputing it during rebuild_summaries
    drops the query to a column read.
    How to apply: ALTER if missing, then clear summary_meta so the next
    scan triggers a full rebuild — that populates the column.
    """
    for table in ("summary_daily", "summary_sessions"):
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not has_table:
            continue
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "active_seconds" in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN active_seconds REAL NOT NULL DEFAULT 0")
        conn.execute("DELETE FROM summary_meta WHERE k='last_rebuild'")
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


# Idle threshold for "active time" math. If two consecutive messages in a
# session are more than this many seconds apart, the gap is dropped — we
# assume the user walked away. Below the threshold, the gap counts as active
# time. Matches WakaTime's default AFK timeout (10 min) and gives a
# defensible lower bound on hands-on time without inflating wall-time.
ACTIVE_GAP_THRESHOLD_S = 600


def _active_seconds_sql(threshold_s: int, range_clause: str) -> str:
    """Capped-gap summation expression. Returns SQL that computes
    `active_seconds` per group via a LAG window over message timestamps.

    The subquery exposes `session_id, project_slug, day, timestamp, gap_s`
    so callers can group by additional time facets (hour-of-day,
    day-of-week, ISO week, etc.) without re-running the LAG.
    """
    return f"""
      WITH ordered AS (
        SELECT session_id, project_slug, timestamp,
               substr(timestamp, 1, 10) AS day,
               LAG(timestamp) OVER (PARTITION BY session_id ORDER BY timestamp) AS prev_ts
          FROM messages
         WHERE timestamp IS NOT NULL {range_clause}
      )
      SELECT session_id, project_slug, day, timestamp,
             CASE
               WHEN prev_ts IS NULL THEN 0.0
               WHEN (julianday(timestamp) - julianday(prev_ts)) * 86400.0 > {threshold_s} THEN 0.0
               ELSE (julianday(timestamp) - julianday(prev_ts)) * 86400.0
             END AS gap_s
        FROM ordered
    """


def _active_seconds_per_slug(conn, since=None, until=None,
                             threshold_s: int = ACTIVE_GAP_THRESHOLD_S) -> dict:
    """Map project_slug → active_seconds. Used by project_summary rollup.

    Fast path: summary_sessions.active_seconds (column read, ~5ms).
    Cold path: raw LAG over messages (~2.6s on 1M rows). Cold path only
    runs when the DB hasn't been summary-rebuilt yet.
    """
    if _summary_ready(conn):
        sess_where, sess_args = _session_range_clause(since, until)
        sql = (
            "SELECT project_slug, COALESCE(SUM(active_seconds), 0) "
            "FROM summary_sessions" + sess_where + " GROUP BY project_slug"
        )
        return {row[0]: row[1] for row in conn.execute(sql, sess_args)}
    rng, args = _range_clause(since, until)
    sql = f"SELECT project_slug, COALESCE(SUM(gap_s), 0) FROM ({_active_seconds_sql(threshold_s, rng)}) GROUP BY project_slug"
    return {row[0]: row[1] for row in conn.execute(sql, args)}


def total_active_seconds(db_path, since=None, until=None,
                         threshold_s: int = ACTIVE_GAP_THRESHOLD_S) -> float:
    """Scalar total of active-time across all sessions in the range. Powers
    the 'Active time' KPI on the Overview tab.

    Note: concurrent sessions in two terminals double-count — the data has
    no way to detect 'only one window in focus'. Surface this caveat in the
    UI tooltip; do not silently dedup, since we can't know which session
    actually had your attention.
    """
    with connect(db_path) as c:
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            row = c.execute(
                f"SELECT COALESCE(SUM(active_seconds), 0) FROM summary_daily WHERE 1=1 {day_rng}",
                day_args,
            ).fetchone()
            return float(row[0] or 0.0)
        rng, args = _range_clause(since, until)
        sql = f"SELECT COALESCE(SUM(gap_s), 0) FROM ({_active_seconds_sql(threshold_s, rng)})"
        return float(c.execute(sql, args).fetchone()[0] or 0.0)


def daily_active_seconds(db_path, since=None, until=None,
                         threshold_s: int = ACTIVE_GAP_THRESHOLD_S) -> dict:
    """Map YYYY-MM-DD → active_seconds. Powers the heatmap's hours toggle."""
    with connect(db_path) as c:
        if _summary_ready(c):
            day_rng, day_args = _date_range_clause(since, until, col="day")
            sql = f"SELECT day, COALESCE(active_seconds, 0) FROM summary_daily WHERE 1=1 {day_rng}"
            return {row[0]: row[1] for row in c.execute(sql, day_args)}
        rng, args = _range_clause(since, until)
        sql = f"SELECT day, COALESCE(SUM(gap_s), 0) FROM ({_active_seconds_sql(threshold_s, rng)}) GROUP BY day"
        return {row[0]: row[1] for row in c.execute(sql, args)}


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

        # Precompute per-day active_seconds in a temp table so the main
        # summary_daily INSERT can LEFT JOIN it in one shot. The LAG runs
        # over UNFILTERED messages — we need preceding-message context
        # even when the day_filter is applied (otherwise gaps that span
        # midnight get dropped). The day_filter is applied after the gap
        # math via the WHERE on the outer SELECT.
        c.execute("DROP TABLE IF EXISTS _daily_active")
        c.execute(f"""
          CREATE TEMP TABLE _daily_active AS
          WITH ordered AS (
            SELECT timestamp,
                   substr(timestamp, 1, 10) AS day,
                   LAG(timestamp) OVER (PARTITION BY session_id ORDER BY timestamp) AS prev_ts
              FROM messages
             WHERE timestamp IS NOT NULL
          )
          SELECT day,
                 SUM(CASE
                       WHEN prev_ts IS NULL THEN 0
                       WHEN (julianday(timestamp) - julianday(prev_ts)) * 86400.0 > {ACTIVE_GAP_THRESHOLD_S} THEN 0
                       ELSE (julianday(timestamp) - julianday(prev_ts)) * 86400.0
                     END) AS active_secs
            FROM ordered
           WHERE 1=1 {('AND day IN (' + ','.join('?' * len(day_args)) + ')') if day_args else ''}
           GROUP BY day
        """, day_args)

        c.execute("""
          INSERT INTO summary_daily (
            day, turns, input_tokens, output_tokens, cache_read_tokens,
            cache_create_5m_tokens, cache_create_1h_tokens, active_seconds
          )
          SELECT s.day,
                 s.turns,
                 s.input_tokens,
                 s.output_tokens,
                 s.cache_read_tokens,
                 s.cache_create_5m_tokens,
                 s.cache_create_1h_tokens,
                 COALESCE(a.active_secs, 0)
            FROM (
              SELECT substr(timestamp, 1, 10) AS day,
                     SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
                     COALESCE(SUM(input_tokens),0)            AS input_tokens,
                     COALESCE(SUM(output_tokens),0)           AS output_tokens,
                     COALESCE(SUM(cache_read_tokens),0)       AS cache_read_tokens,
                     COALESCE(SUM(cache_create_5m_tokens),0)  AS cache_create_5m_tokens,
                     COALESCE(SUM(cache_create_1h_tokens),0)  AS cache_create_1h_tokens
                FROM messages
               WHERE timestamp IS NOT NULL
        """ + day_filter + """
               GROUP BY day
            ) s
            LEFT JOIN _daily_active a ON a.day = s.day
        """, day_args)
        c.execute("DROP TABLE _daily_active")

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

        # Precompute per-session active_seconds. Session filter is applied
        # inside the temp table because each session's gaps are
        # self-contained (LAG partitions by session_id).
        c.execute("DROP TABLE IF EXISTS _session_active")
        c.execute(f"""
          CREATE TEMP TABLE _session_active AS
          WITH ordered AS (
            SELECT session_id, timestamp,
                   LAG(timestamp) OVER (PARTITION BY session_id ORDER BY timestamp) AS prev_ts
              FROM messages
             WHERE timestamp IS NOT NULL {session_filter}
          )
          SELECT session_id,
                 SUM(CASE
                       WHEN prev_ts IS NULL THEN 0
                       WHEN (julianday(timestamp) - julianday(prev_ts)) * 86400.0 > {ACTIVE_GAP_THRESHOLD_S} THEN 0
                       ELSE (julianday(timestamp) - julianday(prev_ts)) * 86400.0
                     END) AS active_secs
            FROM ordered
           GROUP BY session_id
        """, session_args)

        c.execute("""
          INSERT INTO summary_sessions (
            session_id, project_slug, host, sample_cwd, started, ended, turns,
            input_tokens, output_tokens, cache_read_tokens,
            cache_create_5m_tokens, cache_create_1h_tokens, active_seconds
          )
          SELECT m.session_id,
                 MIN(m.project_slug),
                 MIN(m.host),
                 MIN(m.cwd),
                 MIN(m.timestamp),
                 MAX(m.timestamp),
                 SUM(CASE WHEN m.type='user' THEN 1 ELSE 0 END) AS turns,
                 COALESCE(SUM(m.input_tokens),0),
                 COALESCE(SUM(m.output_tokens),0),
                 COALESCE(SUM(m.cache_read_tokens),0),
                 COALESCE(SUM(m.cache_create_5m_tokens),0),
                 COALESCE(SUM(m.cache_create_1h_tokens),0),
                 COALESCE(a.active_secs, 0)
            FROM messages m
            LEFT JOIN _session_active a ON a.session_id = m.session_id
           WHERE 1=1
        """ + session_filter.replace("session_id", "m.session_id") + """
           GROUP BY m.session_id
        """, session_args)
        c.execute("DROP TABLE _session_active")

        # Per-slug metadata cache (best_cwd + host). Recomputed in full on
        # every rebuild — the maps are tiny (~80 slugs × 2 strings each),
        # but each scan of `messages` to compute them costs ~2.6s, so
        # caching here is the difference between a 3s overview load and
        # a 2ms one. Scoped to slugs in the current rebuild filter so we
        # don't blow away unrelated slugs during incremental rebuilds.
        if full:
            c.execute("DELETE FROM slug_meta")
            scoped_slugs = [r[0] for r in c.execute("SELECT DISTINCT project_slug FROM messages")]
        else:
            # Find slugs touched by this incremental scope.
            scoped_slugs = []
            if session_args:
                scoped_slugs.extend(
                    r[0] for r in c.execute(
                        f"SELECT DISTINCT project_slug FROM messages WHERE session_id IN ({','.join('?' * len(session_args))})",
                        session_args,
                    )
                )
            if day_args:
                scoped_slugs.extend(
                    r[0] for r in c.execute(
                        f"SELECT DISTINCT project_slug FROM messages WHERE substr(timestamp, 1, 10) IN ({','.join('?' * len(day_args))})",
                        day_args,
                    )
                )
            scoped_slugs = sorted(set(scoped_slugs))
            if scoped_slugs:
                ph = ",".join("?" * len(scoped_slugs))
                c.execute(f"DELETE FROM slug_meta WHERE project_slug IN ({ph})", scoped_slugs)
        if scoped_slugs:
            best = _best_cwd_per_slug(c, scoped_slugs)
            hosts = _host_per_slug(c, scoped_slugs)
            c.executemany(
                "INSERT INTO slug_meta (project_slug, best_cwd, host) VALUES (?, ?, ?)",
                [(s, best.get(s), hosts.get(s)) for s in scoped_slugs],
            )

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
    """Claude Code's project-slug encoding: `:`, `\\`, `/`, space, `.` → one `-`.

    The `.` is the non-obvious one — observed in production slugs like
    `-home-dvids-Dev-luisabuchaibe-com` for `/home/dvids/Dev/luisabuchaibe.com`
    and `-home-jstationadmin--claude-mem-observer-sessions` for
    `/home/jstationadmin/.claude-mem/observer-sessions`. Omitting `.` made
    `_walk_to_root` fail for every dotted path, forcing the basename
    fallback. Result was the same most of the time but exact slug-to-cwd
    matching in `_best_cwd_per_slug` couldn't fire.
    """
    return re.sub(r"[:\\/ .]", "-", path)


# Patterns for "ephemeral session directories" — paths that look like
# auto-generated per-session workspaces and should all collapse into the
# tool's canonical name. The leaf is required to look like a session id
# (8+ hex chars) so we don't sweep up unrelated paths that happen to
# contain `/projects/`.
_HEX_ID = r"[0-9a-f]{8,}"
_COLLAPSE_PATTERNS = [
    # ~/.claude/<tool>/projects/<hex-id> — observed for claude-homunculus.
    (
        re.compile(rf"(?:^|.*/)\.claude/([A-Za-z][A-Za-z0-9_-]*)/projects/{_HEX_ID}(?:/.*)?$"),
        lambda m: m.group(1),
    ),
    # ~/.claude-<tool>/projects/<hex-id> — alternative structure some
    # plugins use (top-level dir instead of subdir).
    (
        re.compile(rf"(?:^|.*/)\.claude-([A-Za-z][A-Za-z0-9_-]*)/projects/{_HEX_ID}(?:/.*)?$"),
        lambda m: f"claude-{m.group(1)}",
    ),
]


def _collapse_pattern(cwd: Optional[str]) -> Optional[str]:
    """If `cwd` looks like an ephemeral session dir, return the canonical
    display name; otherwise None.

    Recognizes Claude plugin per-session workspace dirs (e.g. every
    `.claude/homunculus/projects/<hex>` collapses to `"homunculus"` so
    cross-host merge in `_rollup_projects` rolls every session-id row
    into one.
    """
    if not cwd:
        return None
    for pattern, fn in _COLLAPSE_PATTERNS:
        m = pattern.match(cwd)
        if m:
            return fn(m)
    return None


# Container directories that are NOT projects themselves. If a slug's best-cwd
# resolves to one of these, the slug stays standalone — children are not rolled
# up into it. Prevents pathological merges like every Claude session under
# /home/jstationadmin collapsing into a single "jstationadmin" row.
_NON_PROJECT_CWDS = frozenset({
    "/", "/tmp", "/var", "/opt", "/etc", "/mnt",
    "/home", "/home/dvids", "/home/jstationadmin",
    "/home/dvids/Dev", "/mnt/data/dev", "/mnt/data",
    "/home/dvids/.claude", "/home/jstationadmin/.claude",
    "/home/dvids/.claude-mem", "/home/jstationadmin/.claude-mem",
    "/mnt/data/obsidian", "/mnt/jstation/data/obsidian", "/mnt/jstation/data",
    "/home/dvids/gsd-workspaces", "/home/jstationadmin/gsd-workspaces",
})


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


def _best_cwd_per_slug(conn, slugs) -> dict:
    """Pick a representative cwd per slug.

    Why: `MIN(cwd)` is contaminated by `cd`-into-subdirectory side trips
    (Claude commonly cd's into ~/.claude/skills/... during a session, which
    sorts lexicographically before the real project root and wins MIN()).
    The selection rule below picks the cwd that *encodes back to the slug*
    if one exists, then a descendant cwd, then the most-frequent cwd.

    How to apply: pass a set of project_slugs and the rollup logic uses the
    returned dict for both display-name walks and host detection.
    """
    if not slugs:
        return {}
    # One query — all cwds with counts, grouped per slug.
    placeholders = ",".join("?" * len(slugs))
    rows = conn.execute(
        f"SELECT project_slug, cwd, COUNT(*) AS c FROM messages "
        f"WHERE cwd IS NOT NULL AND project_slug IN ({placeholders}) "
        f"GROUP BY project_slug, cwd",
        list(slugs),
    ).fetchall()
    by_slug = {}
    for r in rows:
        by_slug.setdefault(r[0], []).append((r[1], r[2]))
    best = {}
    for slug, lst in by_slug.items():
        lst.sort(key=lambda x: -x[1])
        exact = next((c for c, _ in lst if _encode_slug(c) == slug), None)
        if exact:
            best[slug] = exact; continue
        prefix = next((c for c, _ in lst if _encode_slug(c).startswith(slug + "-")), None)
        if prefix:
            best[slug] = prefix; continue
        best[slug] = lst[0][0]
    return best


def _host_per_slug(conn, slugs) -> dict:
    if not slugs:
        return {}
    placeholders = ",".join("?" * len(slugs))
    rows = conn.execute(
        f"SELECT project_slug, host, COUNT(*) AS c FROM messages "
        f"WHERE project_slug IN ({placeholders}) "
        f"GROUP BY project_slug, host",
        list(slugs),
    ).fetchall()
    by_slug = {}
    for slug, host, c in rows:
        by_slug.setdefault(slug, []).append((host, c))
    return {slug: max(lst, key=lambda x: x[1])[0] for slug, lst in by_slug.items()}


def _slug_meta(conn) -> tuple:
    """Return (best_cwd_per_slug, host_per_slug) maps.

    Fast path: read from slug_meta table (populated by rebuild_summaries).
    Cold path: scan messages (~2.6s on 1M rows; only fires on a fresh DB
    that hasn't been summary-rebuilt yet).
    """
    try:
        rows = list(conn.execute("SELECT project_slug, best_cwd, host FROM slug_meta"))
    except sqlite3.OperationalError:
        rows = []
    if rows:
        best = {r[0]: r[1] for r in rows}
        hosts = {r[0]: r[2] for r in rows}
        return best, hosts
    slugs = [r[0] for r in conn.execute("SELECT DISTINCT project_slug FROM messages")]
    return _best_cwd_per_slug(conn, slugs), _host_per_slug(conn, slugs)


def _rollup_projects(conn, rows, active_secs_map=None) -> list:
    """Roll sub-path slugs into their parent, then merge cross-host duplicates.

    Sub-path roll-up: a slug becomes a child of an existing slug iff the parent
    slug is present in the result set AND its best-cwd is not in
    `_NON_PROJECT_CWDS`. This stops pathological collapses like all sessions
    under `/home/jstationadmin` being merged into one "jstationadmin" row.

    Cross-host merge: after sub-path roll-up, rows whose display-name matches
    across hosts are unioned into one row carrying both host tags.
    """
    rows = [dict(r) for r in rows]
    slugs = sorted({r["project_slug"] for r in rows})
    best_cwd, host_of = _slug_meta(conn)
    # Cold-path returns maps over ALL slugs; in the warm path we only
    # need entries for slugs present in this result. The maps are tiny
    # so we accept the extra entries rather than filtering.

    eligible_parents = {
        slug for slug in slugs
        if (best_cwd.get(slug, "") or "").rstrip("/") not in _NON_PROJECT_CWDS
    }

    def find_parent(slug):
        # Walk progressively shorter slug prefixes (split on `-`); the longest
        # eligible prefix in the set wins. Slug encoding collapses `/` to `-`,
        # so a `-` boundary is the only structural cue we have.
        parts = slug.split("-")
        for i in range(len(parts) - 1, 0, -1):
            candidate = "-".join(parts[:i])
            if candidate and candidate in eligible_parents and candidate != slug:
                return candidate
        return None

    parent = {s: find_parent(s) for s in slugs}

    def resolve_root(s, _seen=None):
        seen = _seen or set()
        while parent.get(s) and s not in seen:
            seen.add(s); s = parent[s]
        return s

    root_of = {s: resolve_root(s) for s in slugs}

    # Aggregate per root slug.
    grouped = {}
    for r in rows:
        root = root_of[r["project_slug"]]
        if root not in grouped:
            root_cwd = best_cwd.get(root)
            name = _collapse_pattern(root_cwd) or project_name_for(root_cwd, root)
            grouped[root] = {
                "project_slug": root,
                "project_name": name,
                "sessions": 0, "turns": 0,
                "input_tokens": 0, "output_tokens": 0,
                "billable_tokens": 0, "cache_read_tokens": 0,
                "active_seconds": 0.0,
                "hosts": set(),
                "contributing_slugs": [],
            }
        g = grouped[root]
        for k in ("sessions", "turns", "input_tokens", "output_tokens",
                  "billable_tokens", "cache_read_tokens"):
            g[k] += int(r.get(k) or 0)
        if active_secs_map is not None:
            g["active_seconds"] += float(active_secs_map.get(r["project_slug"], 0) or 0)
        h = host_of.get(r["project_slug"])
        if h: g["hosts"].add(h)
        g["contributing_slugs"].append(r["project_slug"])

    # Cross-host merge by display name.
    by_name = {}
    for g in grouped.values():
        key = g["project_name"]
        if key not in by_name:
            by_name[key] = g; continue
        b = by_name[key]
        for k in ("sessions", "turns", "input_tokens", "output_tokens",
                  "billable_tokens", "cache_read_tokens"):
            b[k] += g[k]
        b["active_seconds"] += g["active_seconds"]
        b["hosts"].update(g["hosts"])
        b["contributing_slugs"].extend(g["contributing_slugs"])

    out = []
    for v in by_name.values():
        v["hosts"] = sorted(v["hosts"])
        v["contributing_slugs"] = sorted(set(v["contributing_slugs"]))
        out.append(v)
    out.sort(key=lambda r: -r["billable_tokens"])
    return out


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
            """, (*sess_args, *day_args))]
            active = _active_seconds_per_slug(c, since=since, until=until)
            return _rollup_projects(c, rows, active_secs_map=active)
        rows = [dict(r) for r in c.execute(sql, args)]
        active = _active_seconds_per_slug(c, since=since, until=until)
        return _rollup_projects(c, rows, active_secs_map=active)


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
    """One row per day: stacked bar data + active_seconds for the heatmap toggle."""
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
            # Fast path — active_seconds is already a column on summary_daily.
            day_rng, day_args = _date_range_clause(since, until, col="day")
            return [dict(r) for r in c.execute(f"""
              SELECT day,
                     input_tokens,
                     output_tokens,
                     cache_read_tokens,
                     cache_create_5m_tokens + cache_create_1h_tokens AS cache_create_tokens,
                     active_seconds
                FROM summary_daily
               WHERE 1=1 {day_rng}
               ORDER BY day ASC
            """, day_args)]
        # Cold path — DB hasn't been summary-rebuilt yet. LAG once per day.
        rows = [dict(r) for r in c.execute(sql, args)]
        active_by_day = daily_active_seconds(db_path, since=since, until=until)
        for r in rows:
            r["active_seconds"] = float(active_by_day.get(r["day"], 0) or 0)
        return rows


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
