# Gallery Dashboard — implementation log

Plan: docs/superpowers/plans/2026-07-09-gallery-dashboard.md
Spec: Obsidian vault, Nexus/08-Claude/Ostatne/gallery-dashboard-design.md

## Progress

### Task 0 — checkpoint
Clean working tree confirmed, GOAL.md + this log committed. Baseline: 100 tests, 1 pre-existing flaky failure (`test_plan_usage_returns_503_when_no_creds`, env-dependent).

### Pre-flight — deterministic plan_usage test
Patched `token_dashboard.server.get_plan_usage` to raise `NoCredentialsError` directly instead of relying on the test machine lacking real credentials. Suite now 100/100 green.

### Task 1 — Skills description field
Sonnet subagent (pilot task, per GOAL.md cost-awareness rule). Added `_parse_frontmatter()` to `skills.py`, wired `description` into `scan_catalog()`. Found and fixed a deviation from the plan text: the live Skills UI reads `/api/skills_cost` (→ `skill_tokens.skill_cost_proxy`), not `/api/skills`, so `description` had to be plumbed through `skill_tokens.py` too (using `.get()` there to stay compatible with existing fake-catalog tests) — also added to `/api/skills` for consistency. Reviewed diff against plan, matches exactly plus the justified deviation. 102/102 tests green. Committed as `b933ca7`.

### Task 2 — Plugins gallery
Sonnet subagent, ran in parallel with Task 3 (disjoint files). Created `token_dashboard/plugins.py` (`scan_plugins`/`cached_plugins`, TTL 60s) + `web/routes/plugins.js`, matches plan code exactly. Sanity-checked against real `~/.claude/plugins/installed_plugins.json` shape — `installPath` field present as expected. 3 new tests, full suite green (108). Committed as `4741b58`.

### Task 3 — MCP gallery
Sonnet subagent, ran in parallel with Task 2. Created `token_dashboard/mcp_catalog.py` (local `~/.claude.json` scan + hand-maintained `mcp_known_connectors.json` for account-level connectors) + `web/routes/mcp.js`, matches plan code exactly. Sanity-checked against real `~/.claude.json` — has `projects` key as expected. 3 new tests, full suite green (108). Committed as `feb4a0b`.

### Task 4 — Hooks/Commands/Agents gallery
Sonnet subagent, ran after Task 2's commit (imports `plugins.cached_plugins`). Created `token_dashboard/hooks_catalog.py` (`scan_hooks`/`scan_commands`/`scan_agents`) + `web/routes/hooks.js`, matches plan code exactly. Subagent caught and fixed a real test-isolation bug on its own: the literal test spec from the plan let `scan_commands()`/`scan_agents()` read the developer's real `~/.claude/plugins/installed_plugins.json` (7 real plugins on this machine), making `test_user_commands_scanned` non-deterministic. Fix mirrors the existing pattern in `test_plugins.py` — redirects `plugins.INSTALLED_JSON`/`SETTINGS_JSON` to empty tmp paths and resets the module cache in setUp/tearDown. Reviewed and approved — no writes to `~/.claude`, matches the repo's existing test-isolation convention. 3 new tests, full suite green (111). Committed as `55ac476`.

### Task 5 — Integration (server.py + app.js), done by orchestrator directly
Wired `/api/plugins`, `/api/mcp` (+ best-effort `usage_calls` via `tool_token_breakdown` name-matching), `/api/hooks`, `/api/commands`, `/api/agents` into `server.py`; added `ROUTES` entries and `fmt.editorLink` to `app.js`. 5 new server tests, full suite green (116). Committed as `1595cec`.

Live smoke test via Playwright (headless Chromium, `npx playwright` in scratchpad — no python playwright package available, and `networkidle` hangs forever because of the app's persistent `/api/stream` SSE connection, so used `domcontentloaded` + explicit wait instead): all 3 new pages render real data with zero console errors — Plugins 8 cards, MCP 8 cards (2 with editor buttons = the 2 local servers, 6 account-level correctly show "manage via claude.ai" text with no dead button), Hooks/Commands/Agents 68 cards (7+30+31).

Cross-checked `/api/plugins` count (8) against `claude plugin list` (8) — exact match. Cross-checked `/api/mcp` against `claude mcp list`: found a real gap — `mcp_known_connectors.json` was missing the `n8n` connector (visible via `claude mcp list`, confirmed via raw `~/.claude.json` that it's not a local server). Fixed and committed separately (`14ab543`).

Verified the `vscode://file/<path>` mechanism end-to-end, not just string-shaped, per GOAL.md DoD: triggered `open "vscode://file//Users/tulen/.claude.json"` directly, confirmed via screenshot that VS Code showed its native "external application wants to open" confirmation and, after accepting, the file opened as a real editor tab.

While verifying, found the Skills gallery had no `file_path`/editor-link at all — the plan's Task 1 only added `description`, not an editor link, but GOAL.md's DoD explicitly requires this for every skill row with a real file. Added `file_path` passthrough in `skill_tokens.py` and an "Open in editor" button in `skills.js`, matching the established pattern from Plugins/MCP/Hooks. 1 new test, full suite green (117). Verified live: 20 skills with catalog matches show working buttons, skills without a catalog match (built-ins) correctly show none. Committed as `64481d4`.
