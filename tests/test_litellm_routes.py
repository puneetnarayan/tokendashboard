"""Tests for LiteLLM server route helpers: math, state, security."""
import json
import tempfile
import unittest
from datetime import date, timedelta

from token_dashboard.db import init_db, connect
from token_dashboard.pricing import load_pricing
from pathlib import Path

PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"


def _seed_daily(db_path, rows):
    with connect(db_path) as c:
        for r in rows:
            c.execute(
                "INSERT OR REPLACE INTO litellm_daily "
                "(date, spend, prompt_tokens, completion_tokens, total_tokens, api_requests, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'now')",
                (r["date"], r["spend"], r.get("prompt_tokens", 0),
                 r.get("completion_tokens", 0), r.get("total_tokens", 0),
                 r.get("api_requests", 0)),
            )
        c.commit()


def _seed_models(db_path, rows):
    with connect(db_path) as c:
        for r in rows:
            c.execute(
                "INSERT OR REPLACE INTO litellm_daily_models "
                "(date, model, spend, prompt_tokens, completion_tokens, total_tokens, api_requests) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["date"], r["model"], r["spend"], r.get("prompt_tokens", 0),
                 r.get("completion_tokens", 0), r.get("total_tokens", 0),
                 r.get("api_requests", 0)),
            )
        c.commit()


class TestSummaryMath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)
        self.pricing = load_pricing(PRICING_JSON)
        _seed_daily(self.db, [
            {"date": "2026-07-01", "spend": 1.0, "api_requests": 10, "total_tokens": 1000, "prompt_tokens": 400, "completion_tokens": 600},
            {"date": "2026-07-02", "spend": 2.0, "api_requests": 20, "total_tokens": 2000, "prompt_tokens": 800, "completion_tokens": 1200},
        ])

    def test_summary_spend_sum(self):
        from token_dashboard.server import _ll_summary
        result = _ll_summary(self.db, self.pricing, None, None)
        self.assertAlmostEqual(result["spend"], 3.0)

    def test_summary_api_requests_sum(self):
        from token_dashboard.server import _ll_summary
        result = _ll_summary(self.db, self.pricing, None, None)
        self.assertEqual(result["api_requests"], 30)

    def test_avg_cost_per_request(self):
        from token_dashboard.server import _ll_summary
        result = _ll_summary(self.db, self.pricing, None, None)
        self.assertAlmostEqual(result["avg_cost_per_request"], 3.0 / 30, places=5)

    def test_avg_cost_none_when_zero_requests(self):
        from token_dashboard.server import _ll_summary
        with connect(self.db) as c:
            c.execute("DELETE FROM litellm_daily")
            c.commit()
        result = _ll_summary(self.db, self.pricing, None, None)
        self.assertIsNone(result["avg_cost_per_request"])

    def test_delta_is_spend_minus_local(self):
        from token_dashboard.server import _ll_summary
        result = _ll_summary(self.db, self.pricing, None, None)
        expected = result["spend"] - result["local_estimate_spend"]
        self.assertAlmostEqual(result["delta"], expected, places=5)


class TestModelsRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)
        _seed_models(self.db, [
            {"date": "2026-07-01", "model": "claude-sonnet-4-6", "spend": 3.0, "total_tokens": 300, "api_requests": 3},
            {"date": "2026-07-01", "model": "claude-haiku-4-5",  "spend": 1.0, "total_tokens": 100, "api_requests": 1},
        ])

    def test_share_pct_sums_to_100(self):
        from token_dashboard.server import _ll_models
        result = _ll_models(self.db, None, None)
        total_pct = sum(m["share_pct"] for m in result["models"])
        self.assertAlmostEqual(total_pct, 100.0, places=1)

    def test_sorted_by_spend_desc(self):
        from token_dashboard.server import _ll_models
        result = _ll_models(self.db, None, None)
        spends = [m["spend"] for m in result["models"]]
        self.assertEqual(spends, sorted(spends, reverse=True))


class TestDailyZeroFill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)
        _seed_daily(self.db, [
            {"date": "2026-07-01", "spend": 1.0, "api_requests": 5},
            # 2026-07-03 intentionally missing
            {"date": "2026-07-05", "spend": 2.0, "api_requests": 8},
        ])

    def test_missing_days_zero_filled(self):
        from token_dashboard.server import _ll_daily
        pricing = load_pricing(PRICING_JSON)
        # 5-day range: 2026-07-01 to 2026-07-05
        since = "2026-07-01T00:00:00+00:00"
        until = "2026-07-06T00:00:00+00:00"
        result = _ll_daily(self.db, pricing, since, until)
        dates = [d["date"] for d in result["days"]]
        self.assertIn("2026-07-01", dates)
        self.assertIn("2026-07-03", dates)
        gap_day = next(d for d in result["days"] if d["date"] == "2026-07-03")
        self.assertEqual(gap_day["spend"], 0.0)
        self.assertEqual(gap_day["api_requests"], 0)


class TestStatusShape(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)

    def _set_state(self, s):
        with connect(self.db) as c:
            c.execute("INSERT OR REPLACE INTO litellm_meta (key, value) VALUES ('state', ?)", (s,))
            c.commit()

    def test_not_configured_state(self):
        from token_dashboard.server import _ll_status
        from unittest.mock import patch
        with patch("token_dashboard.server._ll_discover", return_value=None):
            result = _ll_status(self.db)
        self.assertEqual(result["state"], "not_configured")
        self.assertIn("proxy_origin", result)
        self.assertIn("last_sync", result)

    def test_all_required_keys_present(self):
        from token_dashboard.server import _ll_status
        from unittest.mock import patch
        self._set_state("active")
        with patch("token_dashboard.server._ll_discover", return_value=None):
            result = _ll_status(self.db)
        for key in ("state", "proxy_origin", "user_alias", "key_masked", "source", "last_sync", "stale"):
            self.assertIn(key, result, f"missing key: {key}")


class TestSecurityKeyNotInResponse(unittest.TestCase):
    """API key must never appear in any route response."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        init_db(self.db)
        self.pricing = load_pricing(PRICING_JSON)
        self.secret = "sk-supersecret-test-key-9999"

    def test_key_not_in_status_response(self):
        from token_dashboard.server import _ll_status
        from unittest.mock import patch
        with patch("token_dashboard.server._ll_discover", return_value={
            "base_url": "https://proxy:4000",
            "api_key": self.secret,
            "source": "env",
        }), patch("token_dashboard.server._ll_mask", return_value="sk-…9999"):
            result = _ll_status(self.db)
        result_str = json.dumps(result)
        self.assertNotIn(self.secret, result_str)

    def test_key_not_in_summary_response(self):
        from token_dashboard.server import _ll_summary
        result = _ll_summary(self.db, self.pricing, None, None)
        self.assertNotIn(self.secret, json.dumps(result))

    def test_key_not_in_models_response(self):
        from token_dashboard.server import _ll_models
        result = _ll_models(self.db, None, None)
        self.assertNotIn(self.secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
