"""Token Dashboard CLI entrypoint."""
from __future__ import annotations

import argparse
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

from token_dashboard.db import init_db, default_db_path, overview_totals
from token_dashboard.scanner import scan_roots
from token_dashboard.tips import all_tips


def _db_path(args) -> str:
    return args.db or os.environ.get("TOKEN_DASHBOARD_DB") or str(default_db_path())


def _parse_projects_dirs(spec: str) -> List[Tuple[str, str]]:
    """Parse CLAUDE_PROJECTS_DIRS into [(host, path), ...].

    Preferred form: newline-separated ``host=path`` pairs (one per line),
    which removes any ambiguity about what character splits entries.
    Back-compat: colon-separated form ``host1=/a:host2=/b`` is also
    accepted. To support paths containing ``:`` in the colon form, a
    token without ``=`` is treated as a continuation of the previous
    entry's path — so ``jlap=/srv/foo:bar:jstation=/cache`` parses as
    ``jlap=/srv/foo:bar`` + ``jstation=/cache``.

    A bare path with no ``=`` anywhere defaults to host=jstation so a
    misconfigured deployment never silently drops data with the wrong
    attribution.
    """
    out: List[Tuple[str, str]] = []
    # Newline takes precedence over colon when present.
    if "\n" in spec:
        tokens = [t for t in spec.splitlines() if t.strip()]
    else:
        # Re-stitch colon-split tokens that didn't carry their own host:
        # those are path fragments belonging to the previous entry.
        raw = [t for t in spec.split(":") if t.strip()]
        tokens = []
        for t in raw:
            if "=" in t or not tokens:
                tokens.append(t)
            else:
                tokens[-1] = tokens[-1] + ":" + t
    for entry in tokens:
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            host, _, path = entry.partition("=")
            host, path = host.strip(), path.strip()
        else:
            host, path = "jstation", entry
        if host and path:
            out.append((host, path))
    return out


def _projects_roots(args) -> List[Tuple[str, str]]:
    """Resolve scan roots into a list of (host, path) tuples.

    Priority: --projects-dir CLI flag (single root, host='jstation') >
    CLAUDE_PROJECTS_DIRS env (multi-root) > CLAUDE_PROJECTS_DIR env
    (single root, host='jstation') > default ~/.claude/projects.
    """
    if args.projects_dir:
        return [("jstation", args.projects_dir)]
    multi = os.environ.get("CLAUDE_PROJECTS_DIRS")
    if multi:
        roots = _parse_projects_dirs(multi)
        if roots:
            return roots
    single = os.environ.get("CLAUDE_PROJECTS_DIR")
    if single:
        return [("jstation", single)]
    return [("jstation", str(Path.home() / ".claude" / "projects"))]


def _today_range():
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return start, end


def cmd_scan(args):
    db = _db_path(args)
    init_db(db)
    roots = _projects_roots(args)
    n = scan_roots(roots, db)
    host_summary = ",".join(h for h, _ in roots)
    print(
        f"Token Dashboard: scanned {n['files']} files, {n['messages']} messages, "
        f"{n['tools']} tool calls (hosts: {host_summary})"
    )


def cmd_today(args):
    db = _db_path(args)
    init_db(db)
    s, e = _today_range()
    t = overview_totals(db, since=s, until=e)
    print("Token Dashboard — today")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")
    print(f"  cache rd: {t['cache_read_tokens']:>12,}    cache cr: {t['cache_create_5m_tokens']+t['cache_create_1h_tokens']:>12,}")


def cmd_stats(args):
    db = _db_path(args)
    init_db(db)
    t = overview_totals(db)
    print("Token Dashboard — all time")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")


def cmd_tips(args):
    db = _db_path(args)
    init_db(db)
    tips = all_tips(db)
    if not tips:
        print("Token Dashboard: no suggestions")
        return
    for tip in tips:
        print(f"[{tip['category']}] {tip['title']}")
        print(f"  {tip['body']}\n")


def cmd_dashboard(args):
    db = _db_path(args)
    init_db(db)
    roots = _projects_roots(args)
    if not args.no_scan:
        scan_roots(roots, db)
    from token_dashboard.server import run

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    url = f"http://{host}:{port}/"
    if not args.no_open:
        webbrowser.open(url)
    print(f"Token Dashboard listening on {url} (hosts: {','.join(h for h, _ in roots)})")
    run(host, port, db, roots)


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite path (default ~/.claude/token-dashboard.db)")
    common.add_argument("--projects-dir", help="JSONL root (default ~/.claude/projects)")

    p = argparse.ArgumentParser(prog="token-dashboard", description="Local Claude Code usage dashboard", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan",  parents=[common]).set_defaults(func=cmd_scan)
    sub.add_parser("today", parents=[common]).set_defaults(func=cmd_today)
    sub.add_parser("stats", parents=[common]).set_defaults(func=cmd_stats)
    sub.add_parser("tips",  parents=[common]).set_defaults(func=cmd_tips)
    d = sub.add_parser("dashboard", parents=[common])
    d.add_argument("--no-scan", action="store_true")
    d.add_argument("--no-open", action="store_true")
    d.set_defaults(func=cmd_dashboard)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
