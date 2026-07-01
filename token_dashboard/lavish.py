"""Token usage HTML report generator.

write_report() produces a self-contained HTML file at ~/.claude/token-report.html.
open_report()  opens it: tries pinned lavish-axi first, falls back to webbrowser.

lavish-axi is pinned at 0.1.33 and runs only when `report` is invoked explicitly —
never as a scan side effect. External sharing (`lavish-axi share`) is out of scope.
"""
from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

REPORT_PATH = Path.home() / ".claude" / "token-report.html"
_LAVISH_PIN = "lavish-axi@0.1.33"

_CSS = """
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:24px}
h1{font-size:1.3rem;margin:0 0 4px}
.sub{color:#8b949e;font-size:.85rem;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.card h2{font-size:.75rem;color:#8b949e;margin:0 0 6px;text-transform:uppercase;letter-spacing:.05em}
.card .val{font-size:1.6rem;font-weight:600;color:#58a6ff}
.card .note{font-size:.8rem;color:#8b949e;margin-top:4px}
.rtk-ok{color:#2ea043}.rtk-warn{color:#f0883e}
.section{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.section h2{font-size:.9rem;margin:0 0 12px;color:#c9d1d9}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;color:#8b949e;padding:4px 8px;border-bottom:1px solid #30363d}
td{padding:4px 8px;border-bottom:1px solid #21262d}
.tip{border-left:3px solid #388bfd;padding:8px 12px;margin-bottom:8px;font-size:.85rem}
.tip .cat{font-size:.75rem;color:#8b949e;margin-bottom:2px;text-transform:uppercase}
.footer{margin-top:24px;font-size:.75rem;color:#484f58;text-align:center}
"""


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _pct(part: int, total: int) -> str:
    return f"{part / total * 100:.0f}%" if total > 0 else "n/a"


def write_report(
    totals: dict,
    rtk: Optional[dict],
    tips: list,
    generated_at: str,
) -> Path:
    """Render HTML report to REPORT_PATH and return the path."""
    inp = totals.get("input_tokens", 0)
    out = totals.get("output_tokens", 0)
    cr  = totals.get("cache_read_tokens", 0)
    cw  = totals.get("cache_create_5m_tokens", 0) + totals.get("cache_create_1h_tokens", 0)

    cache_denom = cr + inp + cw
    cache_hit   = _pct(cr, cache_denom)

    # RTK section
    if rtk and rtk.get("status") == "ok":
        eff = rtk["efficiency_pct"]
        rtk_section = f"""
<div class="section">
  <h2>RTK — Rust Token Killer &nbsp;
    <span class="rtk-ok">{_fmt(rtk['total_saved'])} tokens saved ({eff:.0f}% efficiency)</span>
  </h2>
  <p style="font-size:.85rem;color:#8b949e;margin:0">
    {rtk['total_commands']} commands proxied lifetime.
    Daily breakdown available in the RTK CLI: <code>rtk gain --daily</code>
  </p>
</div>"""
    elif rtk and rtk.get("status") == "no_data":
        rtk_section = """
<div class="section" style="border-left:3px solid #8b949e">
  <h2>RTK installed — no data yet</h2>
  <p style="font-size:.85rem;margin:0;color:#8b949e">
    Run some Claude Code sessions with the RTK PreToolUse hook active,
    then re-run <code>cli.py report</code> to see savings.
  </p>
</div>"""
    else:
        rtk_section = """
<div class="section" style="border-left:3px solid #f0883e">
  <h2 class="rtk-warn">RTK not installed</h2>
  <p style="font-size:.85rem;margin:0">
    Install RTK to save 60–90% on Bash output tokens:<br>
    <code>cargo install --git https://github.com/rtk-ai/rtk</code><br>
    Verify with <code>rtk gain</code> (must show savings, not an error).
    <em>Note: <code>brew install rtk</code> / <code>cargo install rtk</code>
    may install the wrong package — always install from the git source above.</em>
  </p>
</div>"""

    tip_html = "".join(
        f"<div class='tip'><div class='cat'>{t.get('category','')}</div>"
        f"<strong>{t.get('title','')}</strong><br>{t.get('body','')}</div>"
        for t in tips[:10]
    ) or "<p style='color:#8b949e;font-size:.85rem'>No tips — looking good.</p>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Token Report</title><style>{_CSS}</style></head>
<body>
<h1>Token Usage Report</h1>
<div class="sub">Generated {generated_at} · token-dashboard · all data local</div>

<div class="grid">
  <div class="card"><h2>Sessions</h2>
    <div class="val">{totals.get('sessions', 0)}</div>
    <div class="note">{totals.get('turns', 0)} turns</div></div>
  <div class="card"><h2>Input tokens</h2>
    <div class="val">{_fmt(inp)}</div><div class="note">uncached</div></div>
  <div class="card"><h2>Output tokens</h2>
    <div class="val">{_fmt(out)}</div>
    <div class="note" style="color:#f85149">5× cost weight</div></div>
  <div class="card"><h2>Cache reads</h2>
    <div class="val">{_fmt(cr)}</div>
    <div class="note" style="color:#2ea043">hit rate {cache_hit}</div></div>
  <div class="card"><h2>Cache writes</h2>
    <div class="val">{_fmt(cw)}</div><div class="note">1.25× cost weight</div></div>
</div>

{rtk_section}

<div class="section">
  <h2>Optimization Tips</h2>{tip_html}
</div>

<div class="section" style="border-left:3px solid #6e40c9">
  <h2>More tools</h2>
  <table>
    <tr><td><strong>codebase-memory-mcp</strong></td>
      <td style="color:#8b949e">120× fewer tokens on structural code queries</td>
      <td><a href="https://github.com/DeusData/codebase-memory-mcp" style="color:#58a6ff">install</a></td></tr>
    <tr><td><strong>treehouse</strong></td>
      <td style="color:#8b949e">Reusable worktree pools for parallel agent sessions</td>
      <td><a href="https://github.com/kunchenguid/treehouse" style="color:#58a6ff">install</a></td></tr>
    <tr><td><strong>lavish-axi</strong></td>
      <td style="color:#8b949e">Interactive HTML artifact collaboration</td>
      <td><a href="https://github.com/kunchenguid/lavish-axi" style="color:#58a6ff">docs</a></td></tr>
  </table>
</div>

<div class="footer">token-dashboard · no data leaves this machine</div>
</body></html>"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    return REPORT_PATH


def open_report(path: Path) -> None:
    """Open the report: try pinned lavish-axi, fall back to system browser."""
    try:
        # Non-blocking — lavish self-stops when idle
        subprocess.Popen(
            ["npx", "-y", _LAVISH_PIN, str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError):
        webbrowser.open(path.as_uri())
