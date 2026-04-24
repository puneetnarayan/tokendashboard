import os
import tempfile
import unittest
from unittest.mock import patch

from token_dashboard.db import init_db
from token_dashboard.alerts import evaluate_alerts
from token_dashboard.quota import set_quota_config


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "a.db")
        init_db(self.db)
        set_quota_config(self.db, plan="max", quota_5h=100_000, quota_weekly=1_000_000,
                         alert_pct_5h=75, alert_pct_weekly=80, alert_pct_session=50)

    def _usage(self, total_5h, total_weekly, current_session=0):
        return {
            "usage_5h": {"total_billable": total_5h},
            "usage_weekly": {"total_billable": total_weekly},
            "current_session_tokens": current_session,
        }

    def test_no_alerts_below_threshold(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(50_000, 500_000, 10_000)):
            self.assertEqual(evaluate_alerts(self.db), [])

    def test_warning_at_threshold(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(80_000, 200_000, 10_000)):
            alerts = evaluate_alerts(self.db)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], "warning")
        self.assertEqual(alerts[0]["key"], "5h")

    def test_critical_when_over_threshold_plus_15(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(95_000, 200_000, 10_000)):
            alerts = evaluate_alerts(self.db)
        self.assertEqual(alerts[0]["level"], "critical")

    def test_multiple_alerts(self):
        with patch("token_dashboard.alerts._collect_usage", return_value=self._usage(80_000, 850_000, 60_000)):
            alerts = evaluate_alerts(self.db)
        keys = sorted(a["key"] for a in alerts)
        self.assertEqual(keys, ["5h", "session", "weekly"])


if __name__ == "__main__":
    unittest.main()
