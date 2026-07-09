import os
import tempfile
import unittest
from unittest.mock import patch

from token_dashboard.db import init_db, connect
from token_dashboard.skill_tokens import skill_cost_proxy


class SkillProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        init_db(self.db)
        with connect(self.db) as c:
            c.executescript("""
            INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
            VALUES ('a1','s1','p','assistant','2026-04-20T00:00:00Z',0,0,0,0,0);
            INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error)
            VALUES
              ('a1','s1','p','Skill','brainstorming','2026-04-20T00:00:00Z',0),
              ('a1','s1','p','Skill','brainstorming','2026-04-20T00:00:01Z',0),
              ('a1','s1','p','Skill','unknown-skill','2026-04-20T00:00:02Z',0);
            """)
            c.commit()

    def test_proxy_multiplies_invocations_by_skill_size(self):
        fake_catalog = {
            "brainstorming": {"chars": 4000, "tokens": 1000, "path": "/x/SKILL.md"},
        }
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value=fake_catalog):
            rows = skill_cost_proxy(self.db)
        brain = [r for r in rows if r["skill"] == "brainstorming"][0]
        self.assertEqual(brain["invocations"], 2)
        self.assertEqual(brain["proxy_tokens"], 2 * 1000)  # 2 × tokens
        self.assertEqual(brain["chars"], 4000)

    def test_unknown_skill_has_null_proxy(self):
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value={}):
            rows = skill_cost_proxy(self.db)
        unknown = [r for r in rows if r["skill"] == "unknown-skill"][0]
        self.assertIsNone(unknown["proxy_tokens"])
        self.assertTrue(unknown["catalog_missing"])
        self.assertIsNone(unknown["file_path"])

    def test_file_path_passed_through_for_known_skill(self):
        fake_catalog = {
            "brainstorming": {"chars": 4000, "tokens": 1000, "path": "/x/SKILL.md"},
        }
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value=fake_catalog):
            rows = skill_cost_proxy(self.db)
        brain = [r for r in rows if r["skill"] == "brainstorming"][0]
        self.assertEqual(brain["file_path"], "/x/SKILL.md")

    def test_sorted_by_proxy_tokens_desc(self):
        fake_catalog = {
            "brainstorming": {"chars": 4000, "tokens": 1000, "path": "/x/SKILL.md"},
            "unknown-skill": {"chars": 2000, "tokens": 500, "path": "/y/SKILL.md"},
        }
        with patch("token_dashboard.skill_tokens.cached_catalog", return_value=fake_catalog):
            rows = skill_cost_proxy(self.db)
        tokens = [r["proxy_tokens"] for r in rows]
        self.assertEqual(tokens, sorted(tokens, reverse=True))


if __name__ == "__main__":
    unittest.main()
