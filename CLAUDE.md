# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

**Token Dashboard** — a local dashboard for tracking Claude Code token usage, costs, and session history. Reads the JSONL transcripts Claude Code writes to `~/.claude/projects/` and turns them into per-prompt cost analytics, tool/file heatmaps, subagent attribution, cache analytics, project comparisons, and a rule-based tips engine.

Inspired by [phuryn/claude-usage](https://github.com/phuryn/claude-usage) but diverges in UI (vanilla JS + ECharts, dark theme, hash router, SSE refresh) and scope (expensive-prompt drill-down, skills view, tips engine, streaming-snapshot dedup). See `docs/inspiration.md` for the original's feature set and known limitations.

## Status

Working codebase. 68 Python unit tests (`python3 -m unittest discover tests`). Seven UI tabs wired up (Overview, Prompts, Sessions, Projects, Skills, Tips, Settings). Runs on macOS, Windows, and Linux.

## Architecture

- `cli.py` → `token_dashboard/scanner.py` → `~/.claude/token-dashboard.db` (SQLite)
- `token_dashboard/server.py` exposes JSON APIs (`/api/*`) + SSE stream (`/api/stream`) + static frontend (`web/`)
- `web/` is vanilla JS, no build step — hash router + ECharts

## Data source

Claude Code writes one JSONL file per session to `~/.claude/projects/<project-slug>/<session-id>.jsonl`. Each line is a message record; usage fields live at `message.usage` and model identifier at `message.model`. The scanner is incremental — it tracks each file's mtime and byte offset in the `files` table and only reads new bytes on subsequent scans.

## Conventions

- **Fully local.** No telemetry, no remote calls for user data. Tests run offline.
- **Stdlib only.** No `pip install`. If a new feature needs a third-party library, argue for it first — we're willing to pay ergonomics cost to keep install friction at zero.
- **SQLite parameter binding always.** Any f-string in a SQL statement must interpolate only internal, caller-controlled values (column names, placeholder lists). User-reachable values go through `?`.
- **Small files with clear responsibilities.** If a file grows past ~400 lines or accretes three distinct concerns, split it.
- **Streaming-snapshot dedup.** When adding scanner logic that joins the `messages` table, remember `(session_id, message_id)` is the dedup key, not `uuid`. See `scanner._evict_prior_snapshots` and the migration note in `db._migrate_add_message_id`.

## Customizing

Env vars: `PORT` (default 8080), `HOST` (default 127.0.0.1), `CLAUDE_PROJECTS_DIR`, `TOKEN_DASHBOARD_DB`. Pricing lives in `pricing.json`. See README.md § Environment variables for details.

## Known limitations

See `docs/KNOWN_LIMITATIONS.md`. Current summary: Skills `tokens_per_call` is populated only for skills installed under the three scanned roots (`~/.claude/skills/`, `~/.claude/scheduled-tasks/`, `~/.claude/plugins/`); project-local skills and subagent-dispatched skills show invocation counts but blank token counts.

## Verifying changes

```bash
python3 -m unittest discover tests        # all tests
python3 cli.py dashboard --no-open        # start the server
curl http://127.0.0.1:8080/api/overview   # sanity-check an endpoint
```

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
