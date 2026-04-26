"""Unit tests for token_dashboard.skill_budgets.

Parser fixtures use inline strings — never reads ~/.claude/. The actuals
tests seed a tmp SQLite DB and exercise the LEAD window-function boundary.
"""
import os
import tempfile
import unittest

from token_dashboard.db import connect, init_db
from token_dashboard.skill_budgets import (
    parse_budget_from_text,
    skill_actuals,
    skill_costs,
    skill_subagent_costs,
)


class ParseBudgetTests(unittest.TestCase):
    def test_parse_inline_budget(self):
        body = (
            "---\nname: example-skill\n---\n\n"
            "Execute these steps in order. Complete in <800 output tokens. Conversational.\n"
        )
        self.assertEqual(parse_budget_from_text(body), 800)

    def test_parse_section_budget(self):
        body = (
            "---\nname: skill-foo\n---\n\n"
            "Some body.\n\n## Token Budget\n< 100 output tokens. Fire-and-forget.\n"
        )
        self.assertEqual(parse_budget_from_text(body), 100)

    def test_parse_budget_with_commas(self):
        body = "Complete in <5,500 output tokens. Every claim must trace.\n"
        self.assertEqual(parse_budget_from_text(body), 5500)

    def test_parse_no_budget(self):
        body = (
            "---\nname: skill-bar\ndescription: Generic.\n---\n\n"
            "No declaration in body. Just prose.\n"
        )
        self.assertIsNone(parse_budget_from_text(body))

    def test_parse_inline_wins_over_section(self):
        # Both present — inline (top-of-file, more prescriptive) wins.
        body = (
            "Execute these steps. Complete in <800 output tokens.\n\n"
            "## Token Budget\n< 1,500 output tokens\n"
        )
        self.assertEqual(parse_budget_from_text(body), 800)


def _seed_messages(c, rows):
    """Insert assistant messages. Each row = (uuid, session, ts, output_tokens)."""
    for uuid, session, ts, out in rows:
        c.execute(
            "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, output_tokens) "
            "VALUES (?, ?, 'p', 'assistant', ?, ?)",
            (uuid, session, ts, out),
        )


def _seed_skill_call(c, *, uuid, session, target, ts):
    c.execute(
        "INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error) "
        "VALUES (?, ?, 'p', 'Skill', ?, ?, 0)",
        (uuid, session, target, ts),
    )


class SkillActualsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        init_db(self.db)

    def test_skill_actuals_basic(self):
        """Single Skill call, 2 subsequent assistant messages → one sample summing both."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="brainstorming", ts="2026-04-10T00:00:00Z")
            _seed_messages(c, [
                ("m1", "s1", "2026-04-10T00:00:05Z", 100),
                ("m2", "s1", "2026-04-10T00:00:10Z", 200),
            ])
            c.commit()

        actuals = skill_actuals(self.db)
        self.assertIn("brainstorming", actuals)
        stat = actuals["brainstorming"]
        self.assertEqual(stat["count"], 1)
        self.assertEqual(stat["p50"], 300)
        self.assertEqual(stat["p95"], 300)

    def test_skill_actuals_next_skill_terminates_window(self):
        """Two Skill calls in one session: first's window ends at second's ts."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="first", ts="2026-04-10T00:00:00Z")
            _seed_messages(c, [
                ("m1", "s1", "2026-04-10T00:00:01Z", 100),
                ("m2", "s1", "2026-04-10T00:00:02Z", 200),
                ("m3", "s1", "2026-04-10T00:00:03Z", 300),
            ])
            _seed_skill_call(c, uuid="a2", session="s1",
                             target="second", ts="2026-04-10T00:00:10Z")
            _seed_messages(c, [
                ("m4", "s1", "2026-04-10T00:00:11Z", 50),
                ("m5", "s1", "2026-04-10T00:00:12Z", 70),
            ])
            c.commit()

        actuals = skill_actuals(self.db)
        # first: messages m1+m2+m3 (before the second call) = 600
        # second: messages m4+m5 (after second call, no next call) = 120
        self.assertEqual(actuals["first"]["p50"], 600)
        self.assertEqual(actuals["second"]["p50"], 120)

    def test_skill_actuals_end_of_session_window(self):
        """Last Skill call in a session with no subsequent call: all remaining output counted."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="tail", ts="2026-04-10T00:00:00Z")
            _seed_messages(c, [
                ("m1", "s1", "2026-04-10T00:00:01Z", 500),
                ("m2", "s1", "2026-04-10T01:00:00Z", 500),
            ])
            c.commit()

        actuals = skill_actuals(self.db)
        self.assertEqual(actuals["tail"]["p50"], 1000)

    def test_skill_actuals_cross_session_does_not_leak(self):
        """A Skill call in session A must not receive output from session B."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="sA",
                             target="isolated", ts="2026-04-10T00:00:00Z")
            # Same timestamp range, different session — must NOT be counted.
            _seed_messages(c, [
                ("m1", "sB", "2026-04-10T00:00:05Z", 9999),
            ])
            c.commit()

        actuals = skill_actuals(self.db)
        self.assertEqual(actuals["isolated"]["p50"], 0)
        self.assertEqual(actuals["isolated"]["count"], 1)

    def test_skill_actuals_excludes_sidechain(self):
        """Assistant output with is_sidechain=1 (subagents, auto-compaction) must not count."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="leaky", ts="2026-04-10T00:00:00Z")
            # One main-chain message + one huge sidechain message in the window.
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, output_tokens, is_sidechain) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:01Z', 100, 0)"
            )
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, output_tokens, is_sidechain) "
                "VALUES ('m2', 's1', 'p', 'assistant', '2026-04-10T00:00:02Z', 9999, 1)"
            )
            c.commit()
        actuals = skill_actuals(self.db)
        self.assertEqual(actuals["leaky"]["p50"], 100)

    def test_skill_actuals_real_user_message_terminates_window(self):
        """A real-user-typed message (prompt_chars>0, no meta prefix) ends the window."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="chatty", ts="2026-04-10T00:00:00Z")
            # Assistant emits 100 tokens, user types something, assistant emits more.
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, output_tokens) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:01Z', 100)"
            )
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text, prompt_chars) "
                "VALUES ('u1', 's1', 'p', 'user', '2026-04-10T00:00:02Z', 'change of plans, do X instead', 30)"
            )
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, output_tokens) "
                "VALUES ('m2', 's1', 'p', 'assistant', '2026-04-10T00:00:03Z', 5000)"
            )
            c.commit()
        actuals = skill_actuals(self.db)
        # Only m1 should count; m2 is past the real-user boundary.
        self.assertEqual(actuals["chatty"]["p50"], 100)

    def test_skill_actuals_meta_user_messages_do_not_terminate(self):
        """System-injected user messages (SKILL.md body, <system-reminder>, etc.) are not boundaries."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="loaded", ts="2026-04-10T00:00:00Z")
            # Immediately after the Skill call, Claude Code injects the SKILL.md body
            # as a user-role message. This must NOT terminate attribution.
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text, prompt_chars) "
                "VALUES ('u-inject', 's1', 'p', 'user', '2026-04-10T00:00:00.500Z', ?, 5000)",
                ("Base directory for this skill: /home/x/.claude/skills/loaded\n\n# body...",),
            )
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, output_tokens) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:01Z', 400)"
            )
            c.commit()
        actuals = skill_actuals(self.db)
        # Injected skill-load user message is filtered out, so m1 is counted.
        self.assertEqual(actuals["loaded"]["p50"], 400)

    def test_skill_actuals_respects_since(self):
        """Skill calls before `since` are filtered out."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="old", ts="2026-04-10T00:00:00Z")
            _seed_skill_call(c, uuid="a2", session="s2",
                             target="new", ts="2026-04-20T00:00:00Z")
            _seed_messages(c, [
                ("m1", "s1", "2026-04-10T00:00:01Z", 111),
                ("m2", "s2", "2026-04-20T00:00:01Z", 222),
            ])
            c.commit()

        actuals = skill_actuals(self.db, since="2026-04-15T00:00:00Z")
        self.assertNotIn("old", actuals)
        self.assertEqual(actuals["new"]["p50"], 222)


class SkillCostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        init_db(self.db)
        # Minimal pricing table covering one known model + tier fallback.
        self.pricing = {
            "models": {
                "claude-haiku-4-5": {
                    "input":           1.0,
                    "output":          5.0,
                    "cache_read":      0.1,
                    "cache_create_5m": 1.25,
                    "cache_create_1h": 2.0,
                },
            },
            "tier_fallback": {
                "haiku":  {"input": 1.0, "output": 5.0, "cache_read": 0.1,
                           "cache_create_5m": 1.25, "cache_create_1h": 2.0},
                "sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3,
                           "cache_create_5m": 3.75, "cache_create_1h": 6.0},
                "opus":   {"input": 15.0, "output": 75.0, "cache_read": 1.5,
                           "cache_create_5m": 18.75, "cache_create_1h": 30.0},
            },
        }

    def test_skill_costs_basic(self):
        """Costs one invocation with known model; verifies the multiplication."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="billable", ts="2026-04-10T00:00:00Z")
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "model, input_tokens, output_tokens, cache_read_tokens, "
                "cache_create_5m_tokens, cache_create_1h_tokens) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:05Z', "
                "'claude-haiku-4-5', 1000000, 200000, 0, 0, 0)"
            )
            c.commit()
        costs = skill_costs(self.db, self.pricing)
        # 1M input × $1/M + 200k output × $5/M = $1 + $1 = $2
        self.assertIn("billable", costs)
        self.assertAlmostEqual(costs["billable"]["cost_usd"], 2.0, places=4)
        self.assertFalse(costs["billable"]["cost_estimated"])

    def test_skill_costs_unknown_model_falls_back_to_tier(self):
        """A model name matching a known tier (opus/sonnet/haiku) uses tier pricing and flags estimated."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="tiered", ts="2026-04-10T00:00:00Z")
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "model, output_tokens) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:05Z', "
                "'claude-opus-4-99-unreleased', 1000000)"
            )
            c.commit()
        costs = skill_costs(self.db, self.pricing)
        # 1M output × $75/M (opus tier fallback) = $75
        self.assertAlmostEqual(costs["tiered"]["cost_usd"], 75.0, places=2)
        self.assertTrue(costs["tiered"]["cost_estimated"])

    def test_skill_costs_aggregates_across_models(self):
        """A single skill window hitting two models costs each separately and sums."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="a1", session="s1",
                             target="mixed", ts="2026-04-10T00:00:00Z")
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "model, output_tokens) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:05Z', "
                "'claude-haiku-4-5', 1000000)"
            )
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "model, output_tokens) "
                "VALUES ('m2', 's1', 'p', 'assistant', '2026-04-10T00:00:10Z', "
                "'claude-opus-4-7', 100000)"
            )
            c.commit()
        costs = skill_costs(self.db, self.pricing)
        # haiku output 1M × $5 = $5, opus output 100k × $75 = $7.5 → total $12.5
        self.assertAlmostEqual(costs["mixed"]["cost_usd"], 12.5, places=2)
        self.assertTrue(costs["mixed"]["cost_estimated"])  # opus was tier-fallback


def _seed_sidechain(c, *, uuid, session, ts, agent_id, output_tokens=0,
                    model="claude-opus-4-5", input_tokens=0, msg_type="assistant"):
    """Seed a sidechain message. Real subagent messages carry an agentId
    (the hash from subagents/agent-<hash>.jsonl); attribution joins on it.
    """
    c.execute(
        "INSERT INTO messages (uuid, session_id, project_slug, type, is_sidechain, "
        "timestamp, model, input_tokens, output_tokens, agent_id) "
        "VALUES (?, ?, 'p', ?, 1, ?, ?, ?, ?, ?)",
        (uuid, session, msg_type, ts, model, input_tokens, output_tokens, agent_id),
    )


class SkillSubagentCostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        init_db(self.db)
        self.pricing = {
            "models": {
                "claude-opus-4-5": {
                    "input":           15.0,
                    "output":          75.0,
                    "cache_read":      1.5,
                    "cache_create_5m": 18.75,
                    "cache_create_1h": 30.0,
                },
            },
            "tier_fallback": {
                "opus":   {"input": 15.0, "output": 75.0, "cache_read": 1.5,
                           "cache_create_5m": 18.75, "cache_create_1h": 30.0},
                "sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3,
                           "cache_create_5m": 3.75, "cache_create_1h": 6.0},
                "haiku":  {"input": 1.0, "output": 5.0, "cache_read": 0.1,
                           "cache_create_5m": 1.25, "cache_create_1h": 2.0},
            },
        }

    def test_single_dispatch_sums_sidechain(self):
        """One Skill call → one subagent (agent_id=ag1) → two assistant messages."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="sk1", session="s1",
                             target="orch-a", ts="2026-04-10T00:00:00Z")
            # Subagent starts inside the window (user-injected prompt).
            _seed_sidechain(c, uuid="u1", session="s1", agent_id="ag1",
                            ts="2026-04-10T00:00:06Z", msg_type="user")
            _seed_sidechain(c, uuid="a1", session="s1", agent_id="ag1",
                            ts="2026-04-10T00:00:10Z", output_tokens=1000)
            _seed_sidechain(c, uuid="a2", session="s1", agent_id="ag1",
                            ts="2026-04-10T00:00:15Z", output_tokens=500)
            c.commit()
        sub = skill_subagent_costs(self.db, self.pricing)
        self.assertIn("orch-a", sub)
        self.assertEqual(sub["orch-a"]["output_tokens"], 1500)
        # 1500 output × $75/M = $0.1125
        self.assertAlmostEqual(sub["orch-a"]["cost_usd"], 0.1125, places=4)

    def test_sidechain_past_window_end_still_attributed(self):
        """Subagent started inside window, finishes AFTER user typed next message."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="sk1", session="s1",
                             target="orch-b", ts="2026-04-10T00:00:00Z")
            # Subagent started at t+6s (inside window).
            _seed_sidechain(c, uuid="u1", session="s1", agent_id="ag1",
                            ts="2026-04-10T00:00:06Z", msg_type="user")
            # User types again, closing the skill's own-cost window.
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "prompt_text, prompt_chars, is_sidechain) "
                "VALUES ('u-real', 's1', 'p', 'user', '2026-04-10T00:01:00Z', "
                "'go ahead', 8, 0)"
            )
            # Subagent response arrives after the user typed — by lineage still qa's.
            _seed_sidechain(c, uuid="a1", session="s1", agent_id="ag1",
                            ts="2026-04-10T00:02:00Z", output_tokens=5000)
            c.commit()
        sub = skill_subagent_costs(self.db, self.pricing)
        self.assertEqual(sub["orch-b"]["output_tokens"], 5000)

    def test_two_skills_dispatches_disjoint(self):
        """Skill A and skill B each dispatch one subagent; attribution doesn't cross."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="skA", session="s1",
                             target="A", ts="2026-04-10T00:00:00Z")
            _seed_sidechain(c, uuid="uA", session="s1", agent_id="agA",
                            ts="2026-04-10T00:00:06Z", msg_type="user")
            _seed_sidechain(c, uuid="aA", session="s1", agent_id="agA",
                            ts="2026-04-10T00:00:10Z", output_tokens=100)
            # Real user message → closes A's window.
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "prompt_text, prompt_chars, is_sidechain) "
                "VALUES ('u1', 's1', 'p', 'user', '2026-04-10T00:01:00Z', "
                "'run B', 6, 0)"
            )
            _seed_skill_call(c, uuid="skB", session="s1",
                             target="B", ts="2026-04-10T00:02:00Z")
            _seed_sidechain(c, uuid="uB", session="s1", agent_id="agB",
                            ts="2026-04-10T00:02:06Z", msg_type="user")
            _seed_sidechain(c, uuid="aB", session="s1", agent_id="agB",
                            ts="2026-04-10T00:02:10Z", output_tokens=900)
            c.commit()
        sub = skill_subagent_costs(self.db, self.pricing)
        self.assertEqual(sub["A"]["output_tokens"], 100)
        self.assertEqual(sub["B"]["output_tokens"], 900)

    def test_nested_subagent_attributed_to_root_skill(self):
        """Team pattern: outer subagent dispatches an inner subagent during orchestrator's window."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="sk1", session="s1",
                             target="orch-c", ts="2026-04-10T00:00:00Z")
            # Outer subagent.
            _seed_sidechain(c, uuid="u1", session="s1", agent_id="outer",
                            ts="2026-04-10T00:00:06Z", msg_type="user")
            _seed_sidechain(c, uuid="a1", session="s1", agent_id="outer",
                            ts="2026-04-10T00:00:10Z", output_tokens=300)
            # Inner subagent starts at t+12s — still inside team-audit's window.
            _seed_sidechain(c, uuid="u2", session="s1", agent_id="inner",
                            ts="2026-04-10T00:00:12Z", msg_type="user")
            _seed_sidechain(c, uuid="a2", session="s1", agent_id="inner",
                            ts="2026-04-10T00:00:20Z", output_tokens=700)
            c.commit()
        sub = skill_subagent_costs(self.db, self.pricing)
        # outer (300) + inner (700) = 1000; both attributed to the root skill.
        self.assertEqual(sub["orch-c"]["output_tokens"], 1000)

    def test_dispatch_outside_skill_window_ignored(self):
        """Subagent started before any Skill call is not attributed."""
        with connect(self.db) as c:
            # Subagent starts at t=00:00 with no preceding Skill call.
            _seed_sidechain(c, uuid="u1", session="s1", agent_id="ag-orphan",
                            ts="2026-04-10T00:00:00Z", msg_type="user")
            _seed_sidechain(c, uuid="a1", session="s1", agent_id="ag-orphan",
                            ts="2026-04-10T00:00:05Z", output_tokens=9999)
            c.commit()
        sub = skill_subagent_costs(self.db, self.pricing)
        self.assertEqual(sub, {})

    def test_auto_compaction_sidechain_ignored(self):
        """agent_id prefixed acompact is auto-compaction, never counted."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="sk1", session="s1",
                             target="brainstorming", ts="2026-04-10T00:00:00Z")
            _seed_sidechain(c, uuid="u1", session="s1", agent_id="acompact-abc",
                            ts="2026-04-10T00:00:05Z", msg_type="user")
            _seed_sidechain(c, uuid="ac1", session="s1", agent_id="acompact-abc",
                            ts="2026-04-10T00:00:10Z", output_tokens=2000)
            c.commit()
        sub = skill_subagent_costs(self.db, self.pricing)
        self.assertNotIn("brainstorming", sub)

    def test_skill_costs_unchanged_for_non_orchestrators(self):
        """Regression: skill with no Agent dispatches has skill_costs unchanged and no subagent row."""
        with connect(self.db) as c:
            _seed_skill_call(c, uuid="sk1", session="s1",
                             target="leaf", ts="2026-04-10T00:00:00Z")
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "model, output_tokens, is_sidechain) "
                "VALUES ('m1', 's1', 'p', 'assistant', '2026-04-10T00:00:05Z', "
                "'claude-opus-4-5', 200, 0)"
            )
            c.commit()
        own = skill_costs(self.db, self.pricing)
        sub = skill_subagent_costs(self.db, self.pricing)
        self.assertIn("leaf", own)
        self.assertAlmostEqual(own["leaf"]["cost_usd"], 200 * 75.0 / 1_000_000, places=6)
        self.assertNotIn("leaf", sub)


if __name__ == "__main__":
    unittest.main()
