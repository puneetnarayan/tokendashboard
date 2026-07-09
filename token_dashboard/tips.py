"""Rule-based tips engine — produces actionable suggestions from SQLite."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import List, Optional

from .db import connect


def _iso_days_ago(today_iso: str, n: int) -> str:
    d = datetime.fromisoformat(today_iso.replace("Z", ""))
    return (d - timedelta(days=n)).isoformat()


def _key(category: str, scope: str) -> str:
    return f"{category}:{scope}"


def _is_dismissed(db_path, key: str) -> bool:
    with connect(db_path) as c:
        r = c.execute("SELECT dismissed_at FROM dismissed_tips WHERE tip_key=?", (key,)).fetchone()
    if not r:
        return False
    return (time.time() - r["dismissed_at"]) < 14 * 86400


def dismiss_tip(db_path, key: str) -> None:
    with connect(db_path) as c:
        c.execute(
            "INSERT OR REPLACE INTO dismissed_tips (tip_key, dismissed_at) VALUES (?, ?)",
            (key, time.time()),
        )
        c.commit()


def cache_discipline_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    sql = """
      SELECT project_slug,
             SUM(cache_read_tokens) AS cr,
             SUM(input_tokens + cache_create_5m_tokens + cache_create_1h_tokens) AS rebuild
        FROM messages
       WHERE type='assistant' AND timestamp >= ?
       GROUP BY project_slug
       HAVING (cr + rebuild) > 100000
    """
    out = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since,)):
            total = (row["cr"] or 0) + (row["rebuild"] or 0)
            hit = (row["cr"] or 0) / total if total else 0
            if hit < 0.40:
                key = _key("cache", row["project_slug"])
                if _is_dismissed(db_path, key):
                    continue
                potential_cr = total * 0.40 - (row["cr"] or 0)
                savings_usd = round(max(0, potential_cr) * 2.70 / 1_000_000, 2)
                out.append({
                    "key": key,
                    "category": "cache",
                    "title": f"Low cache hit rate in {row['project_slug']}",
                    "body": f"Cache hit rate is {hit*100:.0f}% over the last 7 days. Sessions that restart context frequently rebuild cache. Consider longer-lived sessions or fewer context resets.",
                    "scope": row["project_slug"],
                    "savings_usd": savings_usd,
                })
    return out


def repeated_target_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    out = []
    with connect(db_path) as c:
        for row in c.execute("""
          SELECT target, COUNT(*) AS n, COUNT(DISTINCT session_id) AS sessions
            FROM tool_calls
           WHERE tool_name IN ('Read','Edit','Write') AND timestamp >= ?
           GROUP BY target HAVING n > 10
           ORDER BY n DESC LIMIT 10
        """, (since,)):
            key = _key("repeat-file", row["target"] or "?")
            if _is_dismissed(db_path, key):
                continue
            out.append({
                "key": key, "category": "repeat-file",
                "title": f"{row['target']} read {row['n']} times",
                "body": f"This file was opened {row['n']} times across {row['sessions']} sessions in the past 7 days. A summary in CLAUDE.md or one read per session would avoid repeats.",
                "scope": row["target"],
            })
        for row in c.execute("""
          SELECT target, COUNT(*) AS n
            FROM tool_calls
           WHERE tool_name='Bash' AND timestamp >= ?
           GROUP BY target HAVING n > 15
           ORDER BY n DESC LIMIT 10
        """, (since,)):
            key = _key("repeat-bash", row["target"] or "?")
            if _is_dismissed(db_path, key):
                continue
            out.append({
                "key": key, "category": "repeat-bash",
                "title": f"`{row['target']}` ran {row['n']} times",
                "body": f"This bash command ran {row['n']} times in the past 7 days. Consider a watch flag or shell alias.",
                "scope": row["target"],
            })
    return out


def right_size_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    sql = """
      SELECT COUNT(*) AS n,
             SUM(input_tokens+cache_create_5m_tokens+cache_create_1h_tokens) AS in_tok,
             SUM(output_tokens) AS out_tok
        FROM messages
       WHERE type='assistant' AND model LIKE '%opus%'
         AND output_tokens < 500 AND is_sidechain = 0
         AND timestamp >= ?
    """
    with connect(db_path) as c:
        row = c.execute(sql, (since,)).fetchone()
    if not row or (row["n"] or 0) < 10:
        return []
    api_opus   = ((row["in_tok"] or 0) * 15 + (row["out_tok"] or 0) * 75) / 1_000_000
    api_sonnet = ((row["in_tok"] or 0) *  3 + (row["out_tok"] or 0) * 15) / 1_000_000
    savings = api_opus - api_sonnet
    if savings < 1.0:
        return []
    key = _key("right-size", "opus-short-turns-7d")
    if _is_dismissed(db_path, key):
        return []
    return [{
        "key": key, "category": "right-size",
        "title": f"{row['n']} short Opus turns might fit on Sonnet",
        "body": f"Opus turns under 500 output tokens cost ~${api_opus:.2f} in the last 7 days. Sonnet would have cost ~${api_sonnet:.2f} (savings ~${savings:.2f}).",
        "scope": "opus-short-turns-7d",
        "savings_usd": round(savings, 2),
    }]


def outlier_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    out = []
    with connect(db_path) as c:
        big = c.execute("""
          SELECT COUNT(*) AS n, AVG(result_tokens) AS avg_t
            FROM tool_calls
           WHERE tool_name='_tool_result' AND result_tokens > 50000 AND timestamp >= ?
        """, (since,)).fetchone()
        if big and (big["n"] or 0) >= 5:
            key = _key("tool-bloat", "result-50k+")
            if not _is_dismissed(db_path, key):
                savings_usd = round(big["n"] * max(0, (big["avg_t"] or 0) - 5000) * 3.0 / 1_000_000, 2)
                out.append({
                    "key": key, "category": "tool-bloat",
                    "title": f"{big['n']} tool results over 50k tokens this week",
                    "body": f"Average size is {int(big['avg_t']):,} tokens. Pipe long Bash output to head/tail and ask for narrower file reads.",
                    "scope": "result-50k+",
                    "savings_usd": savings_usd,
                })
        for row in c.execute("""
          SELECT agent_id, COUNT(*) AS n,
                 AVG(input_tokens+output_tokens) AS mean_t,
                 MAX(input_tokens+output_tokens) AS max_t
            FROM messages
           WHERE is_sidechain=1 AND agent_id IS NOT NULL AND timestamp >= ?
           GROUP BY agent_id HAVING n >= 10
        """, (since,)):
            if (row["max_t"] or 0) > 6 * (row["mean_t"] or 1) and (row["max_t"] or 0) > 50_000:
                key = _key("subagent-outlier", row["agent_id"])
                if _is_dismissed(db_path, key):
                    continue
                out.append({
                    "key": key, "category": "subagent-outlier",
                    "title": f"Subagent {row['agent_id']} has cost outliers",
                    "body": f"Largest invocation used {int(row['max_t']):,} tokens vs mean {int(row['mean_t']):,}. Worth checking what those did differently.",
                    "scope": row["agent_id"],
                })
    return out


def tool_tips() -> List[dict]:
    """Surface install/config hints for RTK, codebase-memory-mcp, and treehouse."""
    import shutil
    from . import rtk as _rtk
    tips: List[dict] = []

    # RTK — distinguish absent/wrong-package from present-but-empty
    rtk = _rtk.stats()
    if rtk is None:
        pass  # timeout or unexpected error — skip rather than mislead
    elif not rtk.get("installed"):
        tips.append({
            "key": "tool:rtk-absent",
            "category": "tool",
            "title": "RTK not installed — potential 60–90% Bash token savings",
            "body": (
                "RTK compresses Bash output before it reaches the LLM context. "
                "Install from source to avoid the crates.io name collision: "
                "`cargo install --git https://github.com/rtk-ai/rtk` "
                "then verify with `rtk gain` (must show savings, not an error). "
                "Tip: `brew install rtk` may install Rust Type Kit instead — verify."
            ),
            "scope": "rtk",
        })
    elif rtk.get("status") == "no_data":
        tips.append({
            "key": "tool:rtk-no-data",
            "category": "tool",
            "title": "RTK installed but no tracked commands yet",
            "body": (
                "Run Claude Code sessions with the RTK PreToolUse hook active. "
                "Check `.claude/settings.json` for the hook entry. "
                "Then run `rtk gain` to confirm savings are being tracked."
            ),
            "scope": "rtk",
        })
    elif rtk.get("efficiency_pct", 100) < 40:
        tips.append({
            "key": "tool:rtk-low-efficiency",
            "category": "tool",
            "title": f"RTK efficiency is low ({rtk['efficiency_pct']:.0f}%)",
            "body": (
                "Some Bash commands may not be proxied through RTK. "
                "Check your PreToolUse hook is registered in `.claude/settings.json` "
                "and that it fires on Bash calls."
            ),
            "scope": "rtk",
        })

    # codebase-memory-mcp
    if not shutil.which("codebase-memory-mcp"):
        tips.append({
            "key": "tool:codebase-memory-absent",
            "category": "tool",
            "title": "codebase-memory-mcp not installed — 120× fewer tokens on code exploration",
            "body": (
                "Indexes your codebase into a knowledge graph. "
                "Structural queries use ~3,400 tokens vs ~412,000 via file-by-file search. "
                "Install: `curl -fsSL https://raw.githubusercontent.com/DeusData/"
                "codebase-memory-mcp/main/install.sh | bash`"
            ),
            "scope": "codebase-memory-mcp",
        })

    # treehouse — only suggest if worktrees in use
    if not shutil.which("treehouse"):
        import subprocess
        try:
            r = subprocess.run(
                ["git", "worktree", "list"], capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and len(r.stdout.strip().splitlines()) > 1:
                tips.append({
                    "key": "tool:treehouse-absent",
                    "category": "tool",
                    "title": "treehouse not installed — reusable worktree pools for parallel agents",
                    "body": (
                        "You have active git worktrees. Treehouse manages a pool of "
                        "reusable worktrees so parallel agent sessions avoid reinstalling "
                        "dependencies each time. "
                        "Install: `curl -fsSL https://kunchenguid.github.io/treehouse/install.sh | sh`"
                    ),
                    "scope": "treehouse",
                })
        except Exception:
            pass

    return tips


def all_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    return [
        *cache_discipline_tips(db_path, today_iso),
        *repeated_target_tips(db_path, today_iso),
        *right_size_tips(db_path, today_iso),
        *outlier_tips(db_path, today_iso),
        *tool_tips(),
    ]
