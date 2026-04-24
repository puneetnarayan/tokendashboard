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


if __name__ == "__main__":
    unittest.main()
