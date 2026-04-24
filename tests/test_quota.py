import os
import tempfile
import unittest

from token_dashboard.db import init_db
from token_dashboard.quota import (
    get_quota_config, set_quota_config,
    PLAN_DEFAULTS, ALERT_DEFAULTS,
)


class QuotaConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "q.db")
        init_db(self.db)

    def test_defaults_when_unset(self):
        cfg = get_quota_config(self.db)
        self.assertEqual(cfg["plan"], "max")
        self.assertEqual(cfg["quota_5h"], PLAN_DEFAULTS["max"]["quota_5h"])
        self.assertEqual(cfg["quota_weekly"], PLAN_DEFAULTS["max"]["quota_weekly"])
        self.assertEqual(cfg["alert_pct_5h"], ALERT_DEFAULTS["alert_pct_5h"])

    def test_set_and_roundtrip(self):
        set_quota_config(self.db, plan="max-20x", quota_5h=999_000, alert_pct_5h=90)
        cfg = get_quota_config(self.db)
        self.assertEqual(cfg["plan"], "max-20x")
        self.assertEqual(cfg["quota_5h"], 999_000)
        self.assertEqual(cfg["alert_pct_5h"], 90)

    def test_plan_change_updates_quota_defaults(self):
        # When user changes plan without explicit quota, defaults for that plan apply
        set_quota_config(self.db, plan="pro")
        cfg = get_quota_config(self.db)
        self.assertEqual(cfg["quota_5h"], PLAN_DEFAULTS["pro"]["quota_5h"])

    def test_validation_rejects_bad_percent(self):
        with self.assertRaises(ValueError):
            set_quota_config(self.db, alert_pct_5h=150)
        with self.assertRaises(ValueError):
            set_quota_config(self.db, alert_pct_5h=0)

    def test_validation_rejects_bad_quota(self):
        with self.assertRaises(ValueError):
            set_quota_config(self.db, quota_5h=-1)
        with self.assertRaises(ValueError):
            set_quota_config(self.db, quota_5h=500)  # below min 1_000

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            set_quota_config(self.db, unknown_field="foo")


from datetime import datetime, timedelta, timezone
from token_dashboard.quota import rolling_window_usage


class RollingWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "q.db")
        init_db(self.db)
        now = datetime.now(timezone.utc)
        inside = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        outside = (now - timedelta(hours=10)).isoformat().replace("+00:00", "Z")
        from token_dashboard.db import connect
        with connect(self.db) as c:
            c.executescript(f"""
            INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model,
              input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens)
            VALUES
              ('a1','s1','p','assistant','{inside}','claude-opus-4-7',100,50,0,0,0),
              ('a2','s1','p','assistant','{inside}','claude-opus-4-7',0,0,0,200,0),
              ('a3','s1','p','assistant','{outside}','claude-opus-4-7',9999,9999,0,0,0);
            """)
            c.commit()

    def test_5h_window_excludes_old_rows(self):
        u = rolling_window_usage(self.db, hours=5)
        self.assertEqual(u["input_tokens"], 100)
        self.assertEqual(u["output_tokens"], 50)
        self.assertEqual(u["cache_create_5m_tokens"], 200)
        self.assertEqual(u["total_billable"], 100 + 50 + 200)

    def test_weekly_window_includes_old_rows(self):
        u = rolling_window_usage(self.db, hours=168)
        self.assertEqual(u["input_tokens"], 100 + 9999)


if __name__ == "__main__":
    unittest.main()
