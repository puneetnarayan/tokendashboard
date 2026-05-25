"""JSONL transcript walker + parser."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from .db import connect, rebuild_summaries, summaries_ready


INSERT_MSG = """
INSERT OR REPLACE INTO messages (
  uuid, parent_uuid, session_id, project_slug, host, cwd, git_branch, cc_version, entrypoint,
  type, is_sidechain, agent_id, timestamp, model, stop_reason, prompt_id, message_id,
  input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens,
  prompt_text, prompt_chars, tool_calls_json
) VALUES (
  :uuid, :parent_uuid, :session_id, :project_slug, :host, :cwd, :git_branch, :cc_version, :entrypoint,
  :type, :is_sidechain, :agent_id, :timestamp, :model, :stop_reason, :prompt_id, :message_id,
  :input_tokens, :output_tokens, :cache_read_tokens, :cache_create_5m_tokens, :cache_create_1h_tokens,
  :prompt_text, :prompt_chars, :tool_calls_json
)
"""

INSERT_TOOL = """
INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens, is_error, timestamp)
VALUES (:message_uuid, :session_id, :project_slug, :tool_name, :target, :result_tokens, :is_error, :timestamp)
"""


_TARGET_FIELDS = {
    "Read":      "file_path",
    "Edit":      "file_path",
    "Write":     "file_path",
    "Glob":      "pattern",
    "Grep":      "pattern",
    "Bash":      "command",
    "WebFetch":  "url",
    "WebSearch": "query",
    "Task":      "subagent_type",
    "Skill":     "skill",
}


def _usage(rec: dict) -> dict:
    u = (rec.get("message") or {}).get("usage") or {}
    cc = u.get("cache_creation") or {}
    return {
        "input_tokens":           int(u.get("input_tokens") or 0),
        "output_tokens":          int(u.get("output_tokens") or 0),
        "cache_read_tokens":      int(u.get("cache_read_input_tokens") or 0),
        "cache_create_5m_tokens": int(cc.get("ephemeral_5m_input_tokens") or 0),
        "cache_create_1h_tokens": int(cc.get("ephemeral_1h_input_tokens") or 0),
    }


def _prompt_text(rec: dict) -> Tuple[Optional[str], Optional[int]]:
    if rec.get("type") != "user":
        return None, None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content, len(content)
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        text = "".join(parts) if parts else None
        return text, (len(text) if text else None)
    return None, None


def _target(name: str, inp: dict) -> Optional[str]:
    field = _TARGET_FIELDS.get(name)
    if field and isinstance(inp, dict):
        v = inp.get(field)
        if isinstance(v, str):
            return v[:500]
    return None


def _extract_tools(rec: dict) -> List[dict]:
    out = []
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name") or "unknown"
        target = _target(name, block.get("input") or {})
        out.append({
            "tool_name":     name,
            "target":        target,
            "result_tokens": None,
            "is_error":      0,
            "timestamp":     rec.get("timestamp"),
        })
    return out


def _extract_results(rec: dict) -> List[dict]:
    out = []
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        body = block.get("content")
        if isinstance(body, str):
            chars = len(body)
        elif isinstance(body, list):
            chars = sum(len(p.get("text", "")) for p in body if isinstance(p, dict))
        else:
            chars = 0
        out.append({
            "tool_name":     "_tool_result",
            "target":        block.get("tool_use_id"),
            "result_tokens": chars // 4,
            "is_error":      1 if block.get("is_error") else 0,
            "timestamp":     rec.get("timestamp"),
        })
    return out


def parse_record(rec: dict, project_slug: str, host: str = "jstation") -> Tuple[dict, List[dict]]:
    """Return (message_row, [tool_call_rows]).

    ``host`` is the machine the JSONL came from (e.g. 'jlap', 'jpc',
    'jstation'). Defaults to 'jstation' for back-compat with callers that
    pre-date the multi-root scanner — those rows are the pre-migration
    Jstation-only history.
    """
    msg_obj = rec.get("message") or {}
    text, chars = _prompt_text(rec)
    msg = {
        "uuid":         rec.get("uuid"),
        "parent_uuid":  rec.get("parentUuid"),
        "session_id":   rec.get("sessionId"),
        "project_slug": project_slug,
        "host":         host,
        "cwd":          rec.get("cwd"),
        "git_branch":   rec.get("gitBranch"),
        "cc_version":   rec.get("version"),
        "entrypoint":   rec.get("entrypoint"),
        "type":         rec.get("type"),
        "is_sidechain": 1 if rec.get("isSidechain") else 0,
        "agent_id":     rec.get("agentId"),
        "timestamp":    rec.get("timestamp"),
        "model":        msg_obj.get("model"),
        "stop_reason":  msg_obj.get("stop_reason"),
        "prompt_id":    rec.get("promptId"),
        "message_id":   msg_obj.get("id"),
        "prompt_text":  text,
        "prompt_chars": chars,
        "tool_calls_json": None,
        **_usage(rec),
    }
    tools = _extract_tools(rec)
    tools.extend(_extract_results(rec))
    if tools:
        msg["tool_calls_json"] = json.dumps(
            [{"name": t["tool_name"], "target": t["target"]} for t in tools if t["tool_name"] != "_tool_result"]
        )
    for t in tools:
        t["message_uuid"] = msg["uuid"]
        t["session_id"]   = msg["session_id"]
        t["project_slug"] = project_slug
    return msg, tools


def _project_slug(file_path: Path, projects_root: Path) -> str:
    rel = file_path.relative_to(projects_root)
    return rel.parts[0]


def scan_file(path: Path, project_slug: str, conn, start_byte: int = 0, host: str = "jstation") -> dict:
    """Ingest new lines from a JSONL file starting at ``start_byte``.

    Bulk ingestion path: parse every valid record into memory, dedup
    streaming snapshots within the file, evict prior DB snapshots in one
    join, then ``executemany`` the inserts. ~10–50× faster than the
    per-row INSERT/SELECT/DELETE loop on initial backfill.

    Returns message/tool counts plus ``end_offset`` — the byte offset just
    past the last fully-parsed line. Callers persist ``end_offset`` as the
    file's high-water mark so a line partially flushed at EOF gets re-read
    once it completes.
    """
    parsed: List[Tuple[dict, list]] = []  # (msg, tools_list) in file order
    end_offset = start_byte
    with open(path, "rb") as fb:
        if start_byte:
            fb.seek(start_byte)
        while True:
            raw = fb.readline()
            if not raw:
                break  # EOF
            if not raw.endswith(b"\n"):
                # Partial line — Claude Code is mid-flush. Leave the
                # high-water mark behind the line start so we re-read it
                # once the write completes.
                break
            line_end = fb.tell()
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                end_offset = line_end
                continue
            if not line:
                end_offset = line_end
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                end_offset = line_end
                continue
            if not isinstance(rec, dict) or "uuid" not in rec or "type" not in rec:
                end_offset = line_end
                continue
            msg, tlist = parse_record(rec, project_slug, host)
            if not msg["session_id"] or not msg["timestamp"]:
                end_offset = line_end
                continue
            parsed.append((msg, tlist))
            end_offset = line_end

    if not parsed:
        return {"messages": 0, "tools": 0, "end_offset": end_offset, "days": set(), "sessions": set()}

    # In-file dedup: when 2-3 streaming snapshots share (session_id, message_id),
    # only the last uuid wins. Build last_uuid first, then drop superseded rows.
    last_uuid: dict = {}
    for m, _ in parsed:
        if m["message_id"]:
            last_uuid[(m["session_id"], m["message_id"])] = m["uuid"]
    final: List[Tuple[dict, list]] = []
    for m, tlist in parsed:
        if m["message_id"] and last_uuid[(m["session_id"], m["message_id"])] != m["uuid"]:
            continue
        final.append((m, tlist))

    # Evict prior DB snapshots: any row in `messages` that shares
    # (session_id, message_id) with one of our kept rows but has a
    # different uuid must be removed (along with its tool_calls). A temp
    # table + join scales to 100k+ rows per file without the parameter
    # limits a giant IN-list would hit.
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _scan_keep "
        "(sid TEXT, mid TEXT, uuid TEXT, PRIMARY KEY (sid, mid))"
    )
    conn.execute("DELETE FROM _scan_keep")
    keep_keys = [
        (m["session_id"], m["message_id"], m["uuid"])
        for m, _ in final if m["message_id"]
    ]
    if keep_keys:
        conn.executemany(
            "INSERT OR REPLACE INTO _scan_keep (sid, mid, uuid) VALUES (?, ?, ?)",
            keep_keys,
        )
        conn.execute("""
            DELETE FROM tool_calls WHERE message_uuid IN (
                SELECT m.uuid FROM messages m
                JOIN _scan_keep k ON m.session_id = k.sid AND m.message_id = k.mid
                WHERE m.uuid != k.uuid
            )
        """)
        conn.execute("""
            DELETE FROM messages WHERE uuid IN (
                SELECT m.uuid FROM messages m
                JOIN _scan_keep k ON m.session_id = k.sid AND m.message_id = k.mid
                WHERE m.uuid != k.uuid
            )
        """)

    # Bulk insert messages — INSERT OR REPLACE handles re-scan of the same
    # uuid (e.g. when a partial line completes between ticks).
    msg_rows = [m for m, _ in final]
    conn.executemany(INSERT_MSG, msg_rows)

    # tool_calls has no natural unique key, so clear stale rows for each
    # kept uuid before re-inserting. Chunk to stay under SQLite's
    # default 999-parameter limit per statement.
    kept_uuids = [m["uuid"] for m, _ in final]
    CHUNK = 500
    for i in range(0, len(kept_uuids), CHUNK):
        chunk = kept_uuids[i:i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(
            f"DELETE FROM tool_calls WHERE message_uuid IN ({placeholders})",
            chunk,
        )
    all_tools = [t for _, tlist in final for t in tlist]
    if all_tools:
        conn.executemany(INSERT_TOOL, all_tools)

    days = {m["timestamp"][:10] for m in msg_rows if m["timestamp"]}
    sessions = {m["session_id"] for m in msg_rows if m["session_id"]}
    return {
        "messages": len(msg_rows),
        "tools": len(all_tools),
        "end_offset": end_offset,
        "days": days,
        "sessions": sessions,
    }


Root = Tuple[str, Path]  # (host, projects_root)


def _walk_root(
    root: Path,
    host: str,
    conn,
    known_files: dict,
    summary_days: set,
    summary_sessions: set,
    pending_counter: List[int],
    totals: dict,
    per_host_totals: dict,
) -> None:
    """Walk one (host, root) and ingest its JSONLs through the open conn.

    Splits out of scan_roots so a single open connection + summary-trigger
    set can span multiple hosts in one pass — that keeps the summary rebuild
    at the end of the whole scan, not per-root.

    Also updates ``per_host_totals[host]`` with the total JSONL count seen
    on disk, so the dashboard can show a progress indicator (ingested /
    total).
    """
    BATCH_SIZE = 20
    total_seen = 0
    for p in root.rglob("*.jsonl"):
        total_seen += 1
        try:
            stat = p.stat()
        except OSError:
            continue
        path_s = str(p)
        row = known_files.get(path_s)
        offset = 0
        if row and row["bytes_read"] == stat.st_size:
            if row["mtime"] != stat.st_mtime:
                conn.execute(
                    "INSERT OR REPLACE INTO files (path, mtime, bytes_read, scanned_at) VALUES (?, ?, ?, ?)",
                    (path_s, stat.st_mtime, row["bytes_read"], time.time()),
                )
                pending_counter[0] += 1
                if pending_counter[0] >= BATCH_SIZE:
                    conn.commit()
                    pending_counter[0] = 0
                    time.sleep(0.05)  # yield so concurrent HTTP writes can acquire the lock
            continue
        if row and stat.st_size > row["bytes_read"]:
            offset = row["bytes_read"]
        slug = _project_slug(p, root)
        sub = scan_file(p, slug, conn, start_byte=offset, host=host)
        # Persist the byte offset of the last fully-parsed line (not
        # st_size) so a partial line mid-flush is retried on the next
        # scan instead of being skipped over.
        conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime, bytes_read, scanned_at) VALUES (?, ?, ?, ?)",
            (path_s, stat.st_mtime, sub["end_offset"], time.time()),
        )
        totals["messages"] += sub["messages"]
        totals["tools"]    += sub["tools"]
        totals["files"]    += 1
        if sub["messages"] or sub["tools"]:
            summary_days.update(sub["days"])
            summary_sessions.update(sub["sessions"])
        pending_counter[0] += 1
        if pending_counter[0] >= BATCH_SIZE:
            conn.commit()
            pending_counter[0] = 0
            time.sleep(0.05)  # yield so concurrent HTTP writes can acquire the lock
    per_host_totals[host] = total_seen


def scan_roots(
    roots: Sequence[Tuple[str, Union[str, Path]]],
    db_path: Union[str, Path],
) -> dict:
    """Scan one or more (host, projects_root) pairs into the same DB.

    ``roots`` is a sequence of ``(host_label, projects_root)`` tuples; each
    JSONL under each root is tagged with its host on ingest. Roots that
    don't exist on disk are skipped silently (the remote rsync cache may
    not be populated yet on first boot).
    """
    totals = {"messages": 0, "tools": 0, "files": 0}
    pairs = [(host, Path(root)) for host, root in roots if Path(root).is_dir()]
    if not pairs:
        return totals
    needs_full_summary_rebuild = not summaries_ready(db_path)
    summary_days: set = set()
    summary_sessions: set = set()
    per_host_totals: dict = {}
    scan_started = time.perf_counter()
    pending_counter = [0]
    with connect(db_path) as conn:
        known_files = {
            r["path"]: r for r in conn.execute("SELECT path, mtime, bytes_read FROM files")
        }
        for host, root in pairs:
            # Pre-write the scan_status row with the root_path BEFORE we
            # start walking. files_ingested at /api/scan/status query time
            # is a live SELECT against the files table by that root prefix,
            # so the UI lights up with real-time progress mid-walk —
            # essential for first-time backfills that take minutes.
            conn.execute(
                "INSERT INTO scan_status (host, root_path, files_total, last_scan_at, last_scan_secs) "
                "VALUES (?, ?, COALESCE((SELECT files_total FROM scan_status WHERE host=?), 0), "
                "  COALESCE((SELECT last_scan_at FROM scan_status WHERE host=?), NULL), "
                "  COALESCE((SELECT last_scan_secs FROM scan_status WHERE host=?), NULL)) "
                "ON CONFLICT(host) DO UPDATE SET root_path=excluded.root_path",
                (host, str(root), host, host, host),
            )
            conn.commit()
            host_started = time.perf_counter()
            _walk_root(root, host, conn, known_files, summary_days, summary_sessions, pending_counter, totals, per_host_totals)
            # Final write at end-of-host with the rglob total + duration.
            host_secs = round(time.perf_counter() - host_started, 3)
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE scan_status SET files_total=?, last_scan_at=?, last_scan_secs=? WHERE host=?",
                (per_host_totals.get(host, 0), now_iso, host_secs, host),
            )
            conn.commit()
    # Drift detection: any session whose messages are in the DB but has no
    # row in summary_sessions needs to be re-rolled-up. This recovers from
    # rebuild_summaries() failures that left messages committed but summary
    # rows missing (e.g. an earlier "database is locked" during a concurrent
    # write). Without this self-healing pass, a single transient failure
    # would leave Recent Sessions stale forever.
    DRIFT_FULL_REBUILD_THRESHOLD = 100
    drift_days: set = set()
    drift_sessions: set = set()
    with connect(db_path) as conn:
        drift_sessions = {
            r[0] for r in conn.execute(
                "SELECT m.session_id FROM messages m "
                "LEFT JOIN summary_sessions s ON s.session_id = m.session_id "
                "WHERE s.session_id IS NULL "
                "GROUP BY m.session_id"
            )
        }
        if drift_sessions and len(drift_sessions) <= DRIFT_FULL_REBUILD_THRESHOLD:
            ph = ",".join("?" * len(drift_sessions))
            drift_days = {
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT substr(timestamp, 1, 10) FROM messages "
                    f"WHERE session_id IN ({ph})",
                    tuple(drift_sessions),
                )
            }
    if drift_sessions and len(drift_sessions) > DRIFT_FULL_REBUILD_THRESHOLD:
        # Large drift suggests prolonged rebuild failures. Targeted
        # incremental with N>>thousand session-ids would hit SQLite's
        # parameter limit and is slower than just rebuilding everything
        # (full rebuild on ~1M messages completes in ~15s).
        needs_full_summary_rebuild = True
    else:
        summary_days.update(drift_days)
        summary_sessions.update(drift_sessions)

    summary_started = time.perf_counter()
    if needs_full_summary_rebuild:
        rebuild_summaries(db_path)
    elif summary_days or summary_sessions:
        rebuild_summaries(db_path, days=summary_days, sessions=summary_sessions)
    totals["scan_seconds"] = round(summary_started - scan_started, 3)
    totals["summary_seconds"] = round(time.perf_counter() - summary_started, 3)
    totals["summary_days"] = len(summary_days)
    totals["summary_sessions"] = len(summary_sessions)
    totals["hosts"] = [host for host, _ in pairs]
    if totals["scan_seconds"] > 1 or totals["summary_seconds"] > 1:
        print(
            "token-dashboard scan "
            f"scan={totals['scan_seconds']:.3f}s summary={totals['summary_seconds']:.3f}s "
            f"messages={totals['messages']} tools={totals['tools']} files={totals['files']} "
            f"days={totals['summary_days']} sessions={totals['summary_sessions']} "
            f"hosts={','.join(totals['hosts'])} "
            f"full_summary={int(needs_full_summary_rebuild)}",
            flush=True,
        )
    return totals


def scan_dir(
    projects_root: Union[str, Path],
    db_path: Union[str, Path],
    host: str = "jstation",
) -> dict:
    """Single-root wrapper around scan_roots.

    Kept for back-compat with cli.py, tests, and any caller that scans one
    root at a time. ``host`` defaults to 'jstation' to match the pre-multi-root
    backfill so existing callers (and pre-existing rows) stay consistent.
    """
    return scan_roots([(host, projects_root)], db_path)
