"""Tests for litellm_client: parsing golden fixtures, no network."""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_urlopen(body_dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body_dict).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestGetDailyActivity(unittest.TestCase):
    def _parse_fixture(self, filename):
        from token_dashboard.litellm_client import _parse_day
        data = json.loads((FIXTURES / filename).read_text())
        results = []
        for entry in data["results"]:
            try:
                results.append(_parse_day(entry))
            except Exception:
                pass
        return results

    def test_full_fixture_parses_two_days(self):
        rows = self._parse_fixture("litellm_daily_full.json")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-07-01")
        self.assertAlmostEqual(rows[0]["spend"], 0.0177)
        self.assertEqual(rows[0]["api_requests"], 11)

    def test_full_fixture_has_model_breakdown(self):
        rows = self._parse_fixture("litellm_daily_full.json")
        self.assertIn("claude-sonnet-4-6", rows[0]["models"])
        self.assertIn("claude-haiku-4-5",  rows[0]["models"])

    def test_missing_breakdown_returns_totals_only(self):
        rows = self._parse_fixture("litellm_daily_missing_breakdown.json")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["models"], {})
        self.assertAlmostEqual(rows[0]["spend"], 0.0177)

    def test_malformed_entry_skipped_good_entry_kept(self):
        rows = self._parse_fixture("litellm_daily_malformed.json")
        # "not a dict" should be skipped; the valid dict entry should be kept
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-07-02")

    def test_numeric_fields_default_to_zero(self):
        from token_dashboard.litellm_client import _parse_day
        row = _parse_day({"date": "2026-07-03", "metrics": {}})
        self.assertEqual(row["spend"], 0.0)
        self.assertEqual(row["prompt_tokens"], 0)
        self.assertEqual(row["api_requests"], 0)

    def test_get_daily_activity_calls_urlopen(self):
        fixture = json.loads((FIXTURES / "litellm_daily_full.json").read_text())
        mock_resp = _mock_urlopen(fixture)
        with patch("token_dashboard.litellm_client.urlopen", return_value=mock_resp):
            from token_dashboard.litellm_client import get_daily_activity
            rows = get_daily_activity("https://proxy:4000", "sk-test", "2026-07-01", "2026-07-02")
        self.assertEqual(len(rows), 2)

    def test_get_daily_activity_network_error_returns_empty(self):
        from urllib.error import URLError
        from token_dashboard.litellm_client import get_daily_activity
        with patch("token_dashboard.litellm_client.urlopen", side_effect=URLError("conn")):
            rows = get_daily_activity("https://proxy:4000", "sk-test", "2026-07-01", "2026-07-02")
        self.assertEqual(rows, [])


class TestGetUserInfo(unittest.TestCase):
    def test_returns_parsed_dict(self):
        fixture = json.loads((FIXTURES / "litellm_userinfo.json").read_text())
        mock_resp = _mock_urlopen(fixture)
        with patch("token_dashboard.litellm_client.urlopen", return_value=mock_resp):
            from token_dashboard.litellm_client import get_user_info
            result = get_user_info("https://proxy:4000", "sk-test")
        self.assertEqual(result["user_id"], "user-abc123")
        self.assertEqual(result["user_alias"], "vipin.kumar")

    def test_network_error_returns_none(self):
        from urllib.error import URLError
        from token_dashboard.litellm_client import get_user_info
        with patch("token_dashboard.litellm_client.urlopen", side_effect=URLError("conn")):
            result = get_user_info("https://proxy:4000", "sk-test")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
