import os
import tempfile
import unittest

from token_dashboard.db import (
    init_db, connect,
    overview_totals, expensive_prompts, project_summary,
    tool_token_breakdown, recent_sessions, session_turns,
    daily_token_breakdown, model_breakdown, project_name_for,
    skill_breakdown, rebuild_summaries,
)


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "q.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.executescript("""
            INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model,
              input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens,
              prompt_text, prompt_chars)
            VALUES
              ('u1',NULL,'s1','projA','user','2026-04-10T00:00:00Z',NULL,0,0,0,0,0,'big prompt',10),
              ('a1','u1','s1','projA','assistant','2026-04-10T00:00:01Z','claude-opus-4-7',100,200,300,0,0,NULL,NULL),
              ('u2',NULL,'s2','projB','user','2026-04-11T00:00:00Z',NULL,0,0,0,0,0,'small',5),
              ('a2','u2','s2','projB','assistant','2026-04-11T00:00:01Z','claude-sonnet-4-6',5,5,0,0,0,NULL,NULL);
            INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error)
            VALUES ('a1','s1','projA','Read','foo.py','2026-04-10T00:00:01Z',0),
                   ('a1','s1','projA','Bash','npm test','2026-04-10T00:00:01Z',0);
            """)
            c.commit()

    def test_overview_totals(self):
        t = overview_totals(self.db, since=None, until=None)
        self.assertEqual(t["sessions"], 2)
        self.assertEqual(t["turns"], 2)
        self.assertEqual(t["input_tokens"], 105)
        self.assertEqual(t["output_tokens"], 205)

    def test_expensive_prompts_orders_by_tokens(self):
        rows = expensive_prompts(self.db, limit=10)
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["prompt_text"], "big prompt")

    def test_expensive_prompts_sort_recent(self):
        rows = expensive_prompts(self.db, limit=10, sort="recent")
        self.assertEqual(rows[0]["prompt_text"], "small")
        self.assertEqual(rows[1]["prompt_text"], "big prompt")

    def test_project_summary_groups(self):
        rows = project_summary(self.db)
        slugs = {r["project_slug"]: r for r in rows}
        self.assertIn("projA", slugs)
        self.assertEqual(slugs["projA"]["turns"], 1)

    def test_tool_breakdown(self):
        rows = tool_token_breakdown(self.db)
        names = {r["tool_name"]: r for r in rows}
        self.assertIn("Read", names)
        self.assertIn("Bash", names)

    def test_recent_sessions(self):
        rows = recent_sessions(self.db, limit=5)
        self.assertEqual(rows[0]["session_id"], "s2")

    def test_session_turns(self):
        rows = session_turns(self.db, "s1")
        self.assertEqual(len(rows), 2)

    def test_daily_token_breakdown_groups_by_day(self):
        rows = daily_token_breakdown(self.db)
        days = {r["day"]: r for r in rows}
        self.assertIn("2026-04-10", days)
        self.assertIn("2026-04-11", days)
        self.assertEqual(days["2026-04-10"]["input_tokens"], 100)
        self.assertEqual(days["2026-04-10"]["output_tokens"], 200)
        self.assertEqual(days["2026-04-10"]["cache_read_tokens"], 300)

    def test_daily_token_breakdown_respects_since(self):
        rows = daily_token_breakdown(self.db, since="2026-04-11T00:00:00Z")
        days = [r["day"] for r in rows]
        self.assertEqual(days, ["2026-04-11"])

    def test_model_breakdown_respects_since_and_groups(self):
        rows = model_breakdown(self.db)
        models = {r["model"]: r for r in rows}
        self.assertIn("claude-opus-4-7", models)
        self.assertIn("claude-sonnet-4-6", models)
        self.assertEqual(models["claude-opus-4-7"]["input_tokens"], 100)

        filtered = model_breakdown(self.db, since="2026-04-11T00:00:00Z")
        names = [r["model"] for r in filtered]
        self.assertEqual(names, ["claude-sonnet-4-6"])


class SummaryQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "summary.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.executescript("""
            INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, cwd, type, timestamp, model,
              input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
            VALUES
              ('u1',NULL,'s1','projA','/work/projA','user','2026-04-10T00:00:00Z',NULL,0,0,0,0,0),
              ('a1','u1','s1','projA','/work/projA','assistant','2026-04-10T00:00:01Z','claude-opus-4-7',100,200,300,10,20),
              ('u2',NULL,'s2','projB','/work/projB','user','2026-04-11T00:00:00Z',NULL,0,0,0,0,0),
              ('a2','u2','s2','projB','/work/projB','assistant','2026-04-11T00:00:01Z','claude-sonnet-4-6',5,6,7,8,9);
            INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens, timestamp, is_error)
            VALUES
              ('a1','s1','projA','Read','foo.py',50,'2026-04-10T00:00:01Z',0),
              ('a1','s1','projA','Bash','npm test',100,'2026-04-10T00:00:02Z',0),
              ('a2','s2','projB','Read','bar.py',25,'2026-04-11T00:00:01Z',0);
            """)
            c.commit()

    def test_overview_queries_can_read_from_summaries_without_raw_rows(self):
        rebuild_summaries(self.db)
        with connect(self.db) as c:
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM tool_calls")
            c.commit()

        totals = overview_totals(self.db)
        self.assertEqual(totals["sessions"], 2)
        self.assertEqual(totals["turns"], 2)
        self.assertEqual(totals["input_tokens"], 105)
        self.assertEqual(totals["output_tokens"], 206)
        self.assertEqual(totals["cache_create_5m_tokens"], 18)
        self.assertEqual(totals["cache_create_1h_tokens"], 29)

        projects = {r["project_slug"]: r for r in project_summary(self.db)}
        self.assertEqual(projects["projA"]["billable_tokens"], 330)
        self.assertEqual(projects["projA"]["project_name"], "projA")

        tools = {r["tool_name"]: r for r in tool_token_breakdown(self.db)}
        self.assertEqual(tools["Read"]["calls"], 2)
        self.assertEqual(tools["Read"]["result_tokens"], 75)

        daily = {r["day"]: r for r in daily_token_breakdown(self.db)}
        self.assertEqual(daily["2026-04-10"]["cache_create_tokens"], 30)

        models = {r["model"]: r for r in model_breakdown(self.db)}
        self.assertEqual(models["claude-opus-4-7"]["cache_read_tokens"], 300)

        sessions = recent_sessions(self.db, limit=1)
        self.assertEqual(sessions[0]["session_id"], "s2")
        self.assertEqual(sessions[0]["tokens"], 11)

    def test_summary_queries_respect_date_ranges(self):
        rebuild_summaries(self.db)
        with connect(self.db) as c:
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM tool_calls")
            c.commit()

        totals = overview_totals(self.db, since="2026-04-11T00:00:00Z")
        self.assertEqual(totals["sessions"], 1)
        self.assertEqual(totals["input_tokens"], 5)

        projects = project_summary(self.db, since="2026-04-11T00:00:00Z")
        self.assertEqual([r["project_slug"] for r in projects], ["projB"])

    def test_partial_summary_rebuild_updates_only_requested_days_and_sessions(self):
        rebuild_summaries(self.db)
        with connect(self.db) as c:
            c.execute("""
                INSERT INTO messages (uuid, session_id, project_slug, cwd, type, timestamp, model,
                  input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
                VALUES ('a3','s2','projB','/work/projB','assistant','2026-04-11T00:00:02Z',
                  'claude-sonnet-4-6',20,30,40,50,60)
            """)
            c.execute("""
                INSERT INTO messages (uuid, session_id, project_slug, cwd, type, timestamp, model,
                  input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
                VALUES ('a4','s3','projC','/work/projC','assistant','2026-04-12T00:00:01Z',
                  'claude-haiku-4-5',1,2,3,4,5)
            """)
            c.commit()

        rebuild_summaries(self.db, days={"2026-04-11"}, sessions={"s2"})

        daily = {r["day"]: r for r in daily_token_breakdown(self.db)}
        self.assertEqual(daily["2026-04-10"]["input_tokens"], 100)
        self.assertEqual(daily["2026-04-11"]["input_tokens"], 25)
        self.assertNotIn("2026-04-12", daily)

        sessions = {r["session_id"]: r for r in recent_sessions(self.db, limit=10)}
        self.assertEqual(sessions["s2"]["tokens"], 61)
        self.assertNotIn("s3", sessions)


class SkillBreakdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.executescript("""
            INSERT INTO messages (uuid, session_id, project_slug, type, timestamp)
            VALUES
              ('u1','s1','pA','user','2026-04-10T00:00:00Z'),
              ('a1','s1','pA','assistant','2026-04-10T00:00:01Z'),
              ('u2','s2','pA','user','2026-04-11T00:00:00Z'),
              ('a2','s2','pA','assistant','2026-04-11T00:00:01Z');

            INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens, timestamp, is_error)
            VALUES
              ('a1','s1','pA','Skill','brainstorming',NULL,'2026-04-10T00:00:01Z',0),
              ('u1','s1','pA','_tool_result','use-123',500,'2026-04-10T00:00:05Z',0),
              ('a1','s1','pA','Skill','brainstorming',NULL,'2026-04-10T00:00:30Z',0),
              ('u1','s1','pA','_tool_result','use-124',800,'2026-04-10T00:00:32Z',0),
              ('a2','s2','pA','Skill','create-skill',NULL,'2026-04-11T00:00:01Z',0),
              ('u2','s2','pA','_tool_result','use-125',1200,'2026-04-11T00:00:02Z',0);
            """)
            c.commit()

    def test_groups_by_skill(self):
        rows = skill_breakdown(self.db)
        by_name = {r["skill"]: r for r in rows}
        self.assertEqual(by_name["brainstorming"]["invocations"], 2)
        self.assertEqual(by_name["brainstorming"]["sessions"], 1)
        self.assertEqual(by_name["create-skill"]["invocations"], 1)

    def test_orders_by_invocations(self):
        rows = skill_breakdown(self.db)
        self.assertEqual(rows[0]["skill"], "brainstorming")

    def test_respects_since(self):
        rows = skill_breakdown(self.db, since="2026-04-11T00:00:00Z")
        names = [r["skill"] for r in rows]
        self.assertEqual(names, ["create-skill"])


class ProjectNameTests(unittest.TestCase):
    def test_basename_of_posix_cwd(self):
        self.assertEqual(project_name_for("/Users/x/foo", "slug"), "foo")

    def test_basename_of_windows_cwd(self):
        self.assertEqual(
            project_name_for(r"C:\Users\alice\projects\Token Dashboard", "anything"),
            "Token Dashboard",
        )


class ProjectRollupTests(unittest.TestCase):
    """Cover the MIN(cwd) fix, sub-path roll-up, and cross-host merge.

    Each test seeds messages and asserts on project_summary() output. No
    rebuild_summaries() — the rollup runs in both the summary and raw paths.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "rollup.db")
        init_db(self.db)

    def _insert(self, rows):
        with connect(self.db) as c:
            for r in rows:
                c.execute(
                    "INSERT INTO messages "
                    "(uuid, session_id, project_slug, host, cwd, type, timestamp, "
                    " input_tokens, output_tokens, cache_read_tokens, "
                    " cache_create_5m_tokens, cache_create_1h_tokens) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        r["uuid"], r["session_id"], r["slug"], r.get("host", "jstation"),
                        r["cwd"], r.get("type", "assistant"), r["ts"],
                        r.get("in", 0), r.get("out", 0), r.get("cr", 0),
                        r.get("c5", 0), r.get("c1", 0),
                    ),
                )
            c.commit()

    def test_naming_ignores_wrong_min_cwd(self):
        # Slug encodes /mnt/data/dev/greybox-web. MIN(cwd) is the noise cwd
        # /home/jstationadmin/.claude. The fix must pick the cwd whose
        # encoded form matches the slug.
        slug = "-mnt-data-dev-greybox-web"
        self._insert([
            {"uuid": "a1", "session_id": "s1", "slug": slug,
             "cwd": "/mnt/data/dev/greybox-web", "ts": "2026-04-10T00:00:00Z", "in": 100},
            {"uuid": "a2", "session_id": "s1", "slug": slug,
             "cwd": "/home/jstationadmin/.claude", "ts": "2026-04-10T00:01:00Z", "in": 5},
        ])
        rows = project_summary(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project_name"], "greybox-web")

    def test_subpath_rolls_up_into_parent(self):
        parent = "-mnt-data-dev-greybox-web"
        child = "-mnt-data-dev-greybox-web-tools-cd-import"
        self._insert([
            {"uuid": "a1", "session_id": "s1", "slug": parent,
             "cwd": "/mnt/data/dev/greybox-web", "ts": "2026-04-10T00:00:00Z", "in": 100},
            {"uuid": "a2", "session_id": "s2", "slug": child,
             "cwd": "/mnt/data/dev/greybox-web/tools/cd-import", "ts": "2026-04-10T00:01:00Z", "in": 50},
        ])
        rows = project_summary(self.db)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["project_name"], "greybox-web")
        self.assertEqual(r["input_tokens"], 150)
        self.assertIn(parent, r["contributing_slugs"])
        self.assertIn(child, r["contributing_slugs"])

    def test_orphan_subpath_stays_standalone(self):
        # Only the child slug exists — no parent in the result set, so the
        # child stays as its own row.
        child = "-mnt-data-dev-greybox-web-tools-cd-import"
        self._insert([
            {"uuid": "a1", "session_id": "s1", "slug": child,
             "cwd": "/mnt/data/dev/greybox-web/tools/cd-import", "ts": "2026-04-10T00:00:00Z", "in": 50},
        ])
        rows = project_summary(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contributing_slugs"], [child])

    def test_non_project_parent_does_not_swallow_children(self):
        # /home/jstationadmin is in _NON_PROJECT_CWDS — slugs at that level
        # must not act as a roll-up parent for unrelated nested projects.
        home_slug = "-home-jstationadmin"
        proj_slug = "-home-jstationadmin--claude-mem-observer-sessions"
        self._insert([
            {"uuid": "a1", "session_id": "s1", "slug": home_slug,
             "cwd": "/home/jstationadmin", "ts": "2026-04-10T00:00:00Z", "in": 100},
            {"uuid": "a2", "session_id": "s2", "slug": proj_slug,
             "cwd": "/home/jstationadmin/.claude-mem/observer-sessions",
             "ts": "2026-04-10T00:01:00Z", "in": 50},
        ])
        rows = project_summary(self.db)
        names = sorted(r["project_name"] for r in rows)
        self.assertEqual(names, ["jstationadmin", "observer-sessions"])

    def test_cross_host_merge(self):
        # Same project on two hosts — different slugs but identical
        # project_name. Rollup should merge them into one row carrying
        # both hosts.
        self._insert([
            {"uuid": "a1", "session_id": "s1", "slug": "-mnt-data-dev-greybox-web",
             "host": "jstation",
             "cwd": "/mnt/data/dev/greybox-web", "ts": "2026-04-10T00:00:00Z", "in": 100},
            {"uuid": "a2", "session_id": "s2", "slug": "-home-dvids-Dev-greybox-web",
             "host": "jlap",
             "cwd": "/home/dvids/Dev/greybox-web", "ts": "2026-04-10T00:01:00Z", "in": 50},
        ])
        rows = project_summary(self.db)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["project_name"], "greybox-web")
        self.assertEqual(r["input_tokens"], 150)
        self.assertEqual(r["hosts"], ["jlap", "jstation"])

    def test_response_shape_unchanged_for_consumers(self):
        # The Projects UI relies on these keys — guard against rename drift.
        self._insert([
            {"uuid": "a1", "session_id": "s1", "slug": "-mnt-data-dev-greybox-web",
             "cwd": "/mnt/data/dev/greybox-web", "ts": "2026-04-10T00:00:00Z",
             "in": 1, "out": 2, "cr": 3, "c5": 4, "c1": 5},
        ])
        r = project_summary(self.db)[0]
        for k in (
            "project_slug", "project_name", "sessions", "turns",
            "input_tokens", "output_tokens",
            "billable_tokens", "cache_read_tokens",
            "hosts", "contributing_slugs",
        ):
            self.assertIn(k, r, f"missing key {k!r} from project_summary row")

    def test_trailing_slash_stripped(self):
        self.assertEqual(project_name_for("/a/b/c/", "slug"), "c")

    def test_fallback_uses_last_dash_segment(self):
        self.assertEqual(
            project_name_for(None, "C--Users-x-Foo-Bar"),
            "Bar",
        )

    def test_fallback_single_segment(self):
        self.assertEqual(project_name_for(None, "projA"), "projA")

    def test_empty(self):
        self.assertEqual(project_name_for(None, ""), "")

    def test_walks_up_cwd_to_project_root(self):
        # cwd is a subfolder; slug matches the parent → return the parent's basename
        self.assertEqual(
            project_name_for(
                r"C:\Users\alice\projects\MyProject\subdir",
                "C--Users-alice-projects-MyProject",
            ),
            "MyProject",
        )

    def test_walks_up_preserves_spaces(self):
        self.assertEqual(
            project_name_for(
                r"C:\Users\alice\projects\Token Dashboard\src\subdir",
                "C--Users-alice-projects-Token-Dashboard",
            ),
            "Token Dashboard",
        )


class ProjectNameInQueriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "n.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.executescript("""
            INSERT INTO messages (uuid, session_id, project_slug, cwd, type, timestamp,
              input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
            VALUES
              ('u1','s1','C--Users-x-My-Repo','/Users/x/My Repo','user','2026-04-10T00:00:00Z',0,0,0,0,0),
              ('a1','s1','C--Users-x-My-Repo','/Users/x/My Repo','assistant','2026-04-10T00:00:01Z',10,20,0,0,0),
              ('u2','s2','slugOnly',NULL,'user','2026-04-11T00:00:00Z',0,0,0,0,0),
              ('a2','s2','slugOnly',NULL,'assistant','2026-04-11T00:00:01Z',5,5,0,0,0);
            """)
            c.commit()

    def test_project_summary_uses_cwd_basename(self):
        rows = project_summary(self.db)
        names = {r["project_slug"]: r["project_name"] for r in rows}
        self.assertEqual(names["C--Users-x-My-Repo"], "My Repo")
        self.assertEqual(names["slugOnly"], "slugOnly")

    def test_recent_sessions_has_project_name(self):
        rows = recent_sessions(self.db)
        by_sid = {r["session_id"]: r for r in rows}
        self.assertEqual(by_sid["s1"]["project_name"], "My Repo")
        self.assertEqual(by_sid["s2"]["project_name"], "slugOnly")


if __name__ == "__main__":
    unittest.main()
