# Goal: Gallery Dashboard

## End state (verifiable — this is what "done" means)

This repo (`token-dashboard`) has 3 new galleries — Plugins, MCP servers, Hooks/Commands/Agents — plus a description field added to the existing Skills gallery, so that `python3 cli.py dashboard` shows a complete, accurate, link-to-source inventory of everything installed in `~/.claude/`. Concretely, ALL of these must be true when you stop:

- [ ] `curl -s localhost:8080/api/plugins | python3 -m json.tool | grep -c '"name"'` equals the count from `claude plugin list`
- [ ] `curl -s localhost:8080/api/mcp` returns local servers (from `~/.claude.json`) AND the known account-level connectors (from `mcp_known_connectors.json`) — combined count, no duplicates, no missing ones
- [ ] Every plugin/skill row has a non-empty `description` unless the source file genuinely has none
- [ ] Every skill/hook row with a real file on disk has a `file_path` and its `vscode://file/<path>` link actually opens the file in an editor (checked manually, not just string-shaped)
- [ ] Every account-level MCP row (Gmail, Calendar, Notion, Drive, Supabase) has `file_path: null` and shows "manage via claude.ai → Settings → Connectors" in the UI — not a dead/broken link
- [ ] `token_estimate` fields are present for skills and plugins, clearly marked approximate
- [ ] `usage_count`/`invocations` for at least 3 spot-checked skills matches a manual count of real `tool_use` events in one raw JSONL file — NOT an inflated grep count
- [ ] Install a throwaway test plugin, wait 60s+, refresh `#/plugins` — it appears without restarting the server; uninstall it, refresh — it disappears
- [ ] `python3 -m unittest discover tests` passes, all of it (old + new tests)
- [ ] `git log --oneline` shows one commit per task, not one giant commit
- [ ] `IMPLEMENTATION-LOG.md` has one entry per task, written as you go

## Where everything else lives

- **Implementation plan (do this, task by task):** `docs/superpowers/plans/2026-07-09-gallery-dashboard.md` — 6 tasks + Task 0, complete code for every step, no placeholders. Follow it directly.
- **Design spec (why, and the full guardrails):** Obsidian vault, `Nexus/08-Claude/Ostatne/gallery-dashboard-design.md` — read section 9 before starting anything.

## Non-negotiable rules (no exceptions, even running autonomously)

1. **Deletion-guard:** before ANY destructive action (`rm`, overwriting an existing file outside this repo, force-push, deleting an MCP config) — stop and ask via `AskUserQuestion`. This applies even in bypass-permissions mode. Inside this repo, normal file edits per the plan are fine; this rule is about anything touching state outside `~/Desktop/Claude/token-dashboard/`.
2. **Read-only toward `~/.claude/` and `~/.claude.json`.** This dashboard only ever reads those paths. Never write, move, or delete anything under them.
3. **Write only inside `~/Desktop/Claude/token-dashboard/`.** Nothing outside this repo gets modified as part of this goal.
4. **Shared-file sequencing:** Tasks 1–4 in the plan touch only their own new/isolated files and are safe to parallelize (different subagents, or delegate a task to Codex via `/codex:rescue --wait` if you want a second opinion or extra hands). Task 5 touches the shared `server.py` and `app.js` — do that one yourself, sequentially, after 1–4 are all committed. Do not let two parallel workers edit the same file.
5. **Commit after every task**, not one commit at the end. If something later fails, earlier committed tasks stay intact.
6. **Cost awareness:** multi-agent/delegated work costs several times more tokens than doing it single-agent (Anthropic's own measurements: ~3–10×). Check the result after Task 1 (the smallest one) before letting the rest run unsupervised.

## Delegation you're explicitly allowed to use

- Dispatch Sonnet subagents for Tasks 1–4 (parallel, independent files).
- For any task, `/codex:rescue --wait "<task description>"` is available if you want Codex to do or double-check the implementation.
- Use any other tool/connector you have access to (MCP connectors, etc.) if it genuinely helps — read/query freely.
- You are the orchestrator/planner/reviewer here — delegate the actual typing, review what comes back against the plan's exact code before committing.

**Known state of Codex right now (checked + fixed 2026-07-09):** READY. `codex-companion.mjs setup --json` returns `codex.available: true` (codex-cli 0.143.0) and `auth.loggedIn: true` (ChatGPT login active for dpg.creo@gmail.com). It was installed via `npm install -g @openai/codex` into `~/.npm-global/bin` — that directory is already on PATH via `~/.zshrc`, but if a fresh shell somehow doesn't see the `codex` binary, run `export PATH="$HOME/.npm-global/bin:$PATH"` first before assuming it's broken. Use `/codex:rescue` freely — no setup step should be needed. If `/codex:setup --json` ever reports `ready: false` again, treat that as a real regression (not expected) — stop and tell the user rather than trying to silently reinstall/relogin on their behalf.

## No external side effects

Read/write/commit freely inside this repo. Do **not**, under any circumstance in this goal: publish or push to any remote, send anything via a connector (email, Slack, calendar invite, etc.), install/uninstall Claude Code plugins or MCP servers, or make any account-level change (e.g. connecting/disconnecting a claude.ai connector). If a task seems to require one of these, stop and ask instead of doing it.
