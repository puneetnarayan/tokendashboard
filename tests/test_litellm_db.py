"""Tests for LiteLLM DB schema and write policy."""
import tempfile
import unittest
from datetime import date, timedelta

from token_dashboard.db import init_db, connect


def _write_day(db_path, row_date, spend, today_str):
    yesterday = (date.fromisoformat(today_str) - timedelta(days=1)).isoformat()
    is_recent = row_date >= yesterday
    policy = "INSERT OR REPLACE" if is_recent else "INSERT OR IGNORE"
    with connect(db_path) as c:
        c.execute(
            f"{policy} INTO litellm_daily "
            "(date, spend, prompt_tokens, completion_tokens, total_tokens, api_requests, fetched_at) "
            "VALUES (?, ?, 0, 0, 0, 0, 'now')",
            (row_date, spend),
        )
        c.commit()


class TestLiteLLMSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)

    def test_tables_created(self):
        with connect(self.db) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("litellm_daily", tables)
        self.assertIn("litellm_daily_models", tables)
        self.assertIn("litellm_meta", tables)

    def test_meta_round_trip(self):
        with connect(self.db) as c:
            c.execute("INSERT OR REPLACE INTO litellm_meta (key, value) VALUES ('state', 'active')")
            c.commit()
        with connect(self.db) as c:
            row = c.execute("SELECT value FROM litellm_meta WHERE key='state'").fetchone()
        self.assertEqual(row["value"], "active")


class TestWritePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)
        self.today = date.today().isoformat()

    def _read_spend(self, row_date):
        with connect(self.db) as c:
            row = c.execute("SELECT spend FROM litellm_daily WHERE date=?", (row_date,)).fetchone()
        return row["spend"] if row else None

    def test_settled_day_not_overwritten(self):
        old = (date.today() - timedelta(days=10)).isoformat()
        _write_day(self.db, old, 1.0, self.today)
        _write_day(self.db, old, 99.0, self.today)  # second write — should be ignored
        self.assertAlmostEqual(self._read_spend(old), 1.0)

    def test_today_is_overwritten(self):
        _write_day(self.db, self.today, 1.0, self.today)
        _write_day(self.db, self.today, 5.0, self.today)
        self.assertAlmostEqual(self._read_spend(self.today), 5.0)

    def test_yesterday_is_overwritten(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _write_day(self.db, yesterday, 1.0, self.today)
        _write_day(self.db, yesterday, 7.5, self.today)
        self.assertAlmostEqual(self._read_spend(yesterday), 7.5)


class TestModelRows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)

    def test_model_rows_keyed_date_model(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT OR IGNORE INTO litellm_daily_models "
                "(date, model, spend, prompt_tokens, completion_tokens, total_tokens, api_requests) "
                "VALUES ('2026-07-01', 'claude-sonnet-4-6', 0.01, 100, 500, 600, 5)",
            )
            c.commit()
        with connect(self.db) as c:
            rows = c.execute("SELECT * FROM litellm_daily_models").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "claude-sonnet-4-6")


class TestSecurityNoKey(unittest.TestCase):
    """API key must never appear in any DB row."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)

    def test_api_key_not_in_meta(self):
        secret = "sk-supersecret-test-key-12345"
        with connect(self.db) as c:
            c.execute("INSERT OR REPLACE INTO litellm_meta (key, value) VALUES ('proxy_origin', 'https://proxy:4000')")
            c.execute("INSERT OR REPLACE INTO litellm_meta (key, value) VALUES ('user_alias', 'vipin')")
            c.execute("INSERT OR REPLACE INTO litellm_meta (key, value) VALUES ('state', 'active')")
            c.commit()
        with connect(self.db) as c:
            rows = c.execute("SELECT value FROM litellm_meta").fetchall()
        values = " ".join(r["value"] or "" for r in rows)
        self.assertNotIn(secret, values)


if __name__ == "__main__":
    unittest.main()
