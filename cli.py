"""Token Dashboard CLI entrypoint."""
from __future__ import annotations

import argparse
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_dashboard.db import init_db, default_db_path, overview_totals
from token_dashboard.scanner import scan_dir
from token_dashboard.tips import all_tips
from token_dashboard import rtk as _rtk
from token_dashboard import lavish as _lavish
from token_dashboard.config import load as _load_config


def _db_path(args) -> str:
    return args.db or os.environ.get("TOKEN_DASHBOARD_DB") or str(default_db_path())


def _projects(args) -> str:
    return (
        args.projects_dir
        or os.environ.get("CLAUDE_PROJECTS_DIR")
        or str(Path.home() / ".claude" / "projects")
    )


def _today_range():
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return start, end


def _rtk_line() -> str:
    s = _rtk.stats()
    if s is None:
        return ""
    if not s.get("installed"):
        return "  RTK:      not installed  (cargo install --git https://github.com/rtk-ai/rtk)"
    if s.get("status") == "no_data":
        return "  RTK:      installed, no data yet  (run sessions with hook active)"
    return (
        f"  RTK:      {s['total_saved']:>12,} tokens saved lifetime"
        f"  ({s['efficiency_pct']:.0f}% efficiency)"
    )


def cmd_scan(args):
    db = _db_path(args)
    init_db(db)
    n = scan_dir(_projects(args), db)
    print(f"Token Dashboard: scanned {n['files']} files, {n['messages']} messages, {n['tools']} tool calls")


def cmd_today(args):
    db = _db_path(args)
    init_db(db)
    s, e = _today_range()
    t = overview_totals(db, since=s, until=e)
    print("Token Dashboard — today")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")
    print(f"  cache rd: {t['cache_read_tokens']:>12,}    cache cr: {t['cache_create_5m_tokens']+t['cache_create_1h_tokens']:>12,}")
    line = _rtk_line()
    if line:
        print(line)


def cmd_stats(args):
    db = _db_path(args)
    init_db(db)
    t = overview_totals(db)
    print("Token Dashboard — all time")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")
    line = _rtk_line()
    if line:
        print(line)


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


def cmd_report(args):
    """Generate ~/.claude/token-report.html and open it in lavish or browser."""
    db = _db_path(args)
    init_db(db)
    cfg = _load_config()

    totals = overview_totals(db)
    tips   = all_tips(db)
    rtk    = _rtk.stats() if cfg.rtk_enabled(bool(_rtk.stats())) else None
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    path = _lavish.write_report(totals, rtk, tips, ts)
    print(f"Report written → {path}")

    if cfg.lavish_enabled():
        _lavish.open_report(path)
    else:
        webbrowser.open(path.as_uri())


def cmd_dashboard(args):
    db = _db_path(args)
    init_db(db)
    if not args.no_scan:
        scan_dir(_projects(args), db)
    from token_dashboard.server import run

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    url = f"http://{host}:{port}/"
    if not args.no_open:
        webbrowser.open(url)
    print(f"Token Dashboard listening on {url}")
    run(host, port, db, _projects(args))


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite path (default ~/.claude/token-dashboard.db)")
    common.add_argument("--projects-dir", help="JSONL root (default ~/.claude/projects)")

    p = argparse.ArgumentParser(
        prog="token-dashboard",
        description="Local Claude Code usage dashboard",
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan",   parents=[common]).set_defaults(func=cmd_scan)
    sub.add_parser("today",  parents=[common]).set_defaults(func=cmd_today)
    sub.add_parser("stats",  parents=[common]).set_defaults(func=cmd_stats)
    sub.add_parser("tips",   parents=[common]).set_defaults(func=cmd_tips)
    sub.add_parser("report", parents=[common]).set_defaults(func=cmd_report)
    d = sub.add_parser("dashboard", parents=[common])
    d.add_argument("--no-scan", action="store_true")
    d.add_argument("--no-open", action="store_true")
    d.set_defaults(func=cmd_dashboard)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
