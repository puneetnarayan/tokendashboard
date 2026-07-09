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
