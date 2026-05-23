import os
import tempfile
import unittest

from token_dashboard.db import init_db, connect
from token_dashboard.tips import (
    cache_discipline_tips, repeated_target_tips, right_size_tips,
    outlier_tips, cross_workspace_tips, all_tips, dismiss_tip,
)


class CacheTipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def _ins(self, ts, project, cache_read, cache_create):
        with connect(self.db) as c:
            c.execute("""INSERT INTO messages (uuid, session_id, project_slug, type, timestamp,
                model, input_tokens, output_tokens, cache_read_tokens,
                cache_create_5m_tokens, cache_create_1h_tokens) VALUES
                (?, 's', ?, 'assistant', ?, 'claude-opus-4-7', 100, 100, ?, ?, 0)""",
                (f"uuid-{ts}", project, ts, cache_read, cache_create))
            c.commit()

    def test_low_cache_hit_emits_tip(self):
        self._ins("2026-04-15T00:00:00Z", "projX", 10, 1_000_000)
        tips = cache_discipline_tips(self.db, today_iso="2026-04-19T00:00:00")
        self.assertTrue(any(t["category"] == "cache" for t in tips))

    def test_healthy_cache_no_tip(self):
        for i in range(10):
            self._ins(f"2026-04-15T00:00:0{i}Z", "projY", 1_000_000, 50)
        tips = cache_discipline_tips(self.db, today_iso="2026-04-19T00:00:00")
        self.assertFalse(any(t["category"] == "cache" for t in tips))


class RepeatTipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model) VALUES ('m1','s1','p','assistant','2026-04-15T00:00:00Z','claude-opus-4-7')")
            for i in range(15):
                c.execute("INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error) VALUES ('m1','s1','p','Read','src/Root.tsx','2026-04-15T00:00:00Z',0)")
            for i in range(20):
                c.execute("INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error) VALUES ('m1','s1','p','Bash','npm run lint','2026-04-15T00:00:00Z',0)")
            c.commit()

    def test_repeated_file_and_bash_emit_tips(self):
        tips = repeated_target_tips(self.db, today_iso="2026-04-19T00:00:00")
        cats = [t["category"] for t in tips]
        self.assertIn("repeat-file", cats)
        self.assertIn("repeat-bash", cats)


class RightSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_short_opus_turns_flagged(self):
        with connect(self.db) as c:
            for i in range(10):
                c.execute("INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens, is_sidechain) VALUES (?, 's','p','assistant','2026-04-18T00:00:00Z','claude-opus-4-7', 1000000, 200, 0, 0, 0, 0)", (f"a{i}",))
            c.commit()
        tips = right_size_tips(self.db, today_iso="2026-04-19T00:00:00")
        self.assertTrue(any(t["category"] == "right-size" for t in tips))


class OutlierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_giant_tool_result_flagged(self):
        with connect(self.db) as c:
            for i in range(20):
                c.execute("INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) VALUES (?, 's','p','user','2026-04-18T00:00:00Z')", (f"u{i}",))
                c.execute("INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens, timestamp, is_error) VALUES (?, 's','p','_tool_result','tu',100000,'2026-04-18T00:00:00Z',0)", (f"u{i}",))
            c.commit()
        tips = outlier_tips(self.db, today_iso="2026-04-19T00:00:00")
        self.assertTrue(any(t["category"] == "tool-bloat" for t in tips))


class CrossWorkspaceTipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "cw.db")
        init_db(self.db)
        slug_a = "C--Users-a-projects-ProjA"
        slug_b = "C--Users-a-projects-ProjB"
        cwd_a = r"C:\Users\a\projects\ProjA"
        cwd_b = r"C:\Users\a\projects\ProjB"
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, cwd, type, "
                "is_sidechain, timestamp, model) VALUES "
                "('m1','s1',?,?,'assistant',0,'2026-05-15T00:00:00Z','claude-opus-4-7')",
                (slug_a, cwd_a),
            )
            # Anchor ProjB in the workspace index so the classifier knows it.
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, cwd, type, "
                "is_sidechain, timestamp, model) VALUES "
                "('m2','s2',?,?,'assistant',0,'2026-05-15T00:00:00Z','claude-opus-4-7')",
                (slug_b, cwd_b),
            )
            for i in range(60):
                c.execute(
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, "
                    "tool_name, target, timestamp, is_error) VALUES "
                    "('m1','s1',?,'Read',?,?,0)",
                    (slug_a, cwd_b + r"\spec.md", f"2026-05-15T00:0{i//10}:0{i%10}Z"),
                )
            c.commit()

    def test_high_cross_workspace_activity_emits_tip(self):
        tips = cross_workspace_tips(self.db, today_iso="2026-05-16T00:00:00")
        self.assertTrue(any(t["category"] == "cross-workspace" for t in tips))
        tip = [t for t in tips if t["category"] == "cross-workspace"][0]
        self.assertIn("ProjA", tip["title"])
        self.assertIn("ProjB", tip["title"])

    def test_low_activity_under_threshold_no_tip(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "small.db")
        init_db(db)
        slug = "C--Users-a-projects-ProjA"
        cwd  = r"C:\Users\a\projects\ProjA"
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, cwd, type, "
                "is_sidechain, timestamp, model) VALUES "
                "('m1','s1',?,?,'assistant',0,'2026-05-15T00:00:00Z','claude-opus-4-7')",
                (slug, cwd),
            )
            for i in range(3):
                c.execute(
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, "
                    "tool_name, target, timestamp, is_error) VALUES "
                    "('m1','s1',?,'Read',?,?,0)",
                    (slug, r"C:\Users\a\projects\Other\file.md", f"2026-05-15T00:00:0{i}Z"),
                )
            c.commit()
        tips = cross_workspace_tips(db, today_iso="2026-05-16T00:00:00")
        self.assertFalse(any(t["category"] == "cross-workspace" for t in tips))


class DismissTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens) VALUES ('m','s','projZ','assistant','2026-04-15T00:00:00Z','claude-opus-4-7', 100, 100, 10, 1000000, 0)")
            c.commit()

    def test_dismissed_tip_doesnt_reappear(self):
        tips_before = cache_discipline_tips(self.db, today_iso="2026-04-19T00:00:00")
        self.assertTrue(tips_before)
        dismiss_tip(self.db, tips_before[0]["key"])
        tips_after = cache_discipline_tips(self.db, today_iso="2026-04-19T00:00:00")
        self.assertFalse(tips_after)


if __name__ == "__main__":
    unittest.main()
