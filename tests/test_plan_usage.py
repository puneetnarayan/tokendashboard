import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from token_dashboard.db import init_db
from token_dashboard import plan_usage


SAMPLE_USAGE = {
    "five_hour": {"utilization": 17.0, "resets_at": "2026-04-25T10:00:00+00:00"},
    "seven_day": {"utilization": 11.0, "resets_at": "2026-05-01T00:00:00+00:00"},
    "seven_day_sonnet": {"utilization": 1.0, "resets_at": "2026-05-01T00:00:00+00:00"},
    "seven_day_omelette": {"utilization": 0.0, "resets_at": None},
}

SAMPLE_KEYCHAIN_CREDS = {
    "claudeAiOauth": {
        "accessToken": "sk-ant-oat01-fake",
        "refreshToken": "sk-ant-ort01-fake",
        "expiresAt": int(time.time() * 1000) + 3600 * 1000,
        "subscriptionType": "max",
        "rateLimitTier": "default_claude_max_5x",
    }
}

SAMPLE_REFRESH_RESP = {
    "access_token": "sk-ant-oat01-NEW",
    "refresh_token": "sk-ant-ort01-NEW",
    "expires_in": 28800,
}


class PlanUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "p.db")
        init_db(self.db)

    def _mock_keychain(self):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = json.dumps(SAMPLE_KEYCHAIN_CREDS)
        return mock

    def test_bootstrap_from_keychain(self):
        with patch("subprocess.run", return_value=self._mock_keychain()):
            with patch("token_dashboard.plan_usage._fetch_usage_raw", return_value=SAMPLE_USAGE):
                result = plan_usage.get_plan_usage(self.db)
        self.assertEqual(result["data"], SAMPLE_USAGE)
        self.assertFalse(result["cached"])
        # Tokens persisted
        self.assertEqual(plan_usage._kv_get(self.db, "pu_access_token"), "sk-ant-oat01-fake")

    def test_cache_hit_within_ttl(self):
        with patch("subprocess.run", return_value=self._mock_keychain()):
            with patch("token_dashboard.plan_usage._fetch_usage_raw", return_value=SAMPLE_USAGE) as mock_fetch:
                plan_usage.get_plan_usage(self.db)
                # Second call should not re-fetch
                result = plan_usage.get_plan_usage(self.db)
                self.assertEqual(mock_fetch.call_count, 1)
                self.assertTrue(result["cached"])

    def test_force_bypasses_cache(self):
        with patch("subprocess.run", return_value=self._mock_keychain()):
            with patch("token_dashboard.plan_usage._fetch_usage_raw", return_value=SAMPLE_USAGE) as mock_fetch:
                plan_usage.get_plan_usage(self.db)
                plan_usage.get_plan_usage(self.db, force=True)
                self.assertEqual(mock_fetch.call_count, 2)

    def test_no_credentials_raises(self):
        with patch("token_dashboard.plan_usage._read_keychain", return_value=None):
            with patch("token_dashboard.plan_usage._read_credentials_json", return_value=None):
                with self.assertRaises(plan_usage.NoCredentialsError):
                    plan_usage.get_plan_usage(self.db)

    def test_expired_token_triggers_refresh(self):
        # Pre-seed with expired token
        plan_usage._kv_set(self.db, "pu_access_token", "old")
        plan_usage._kv_set(self.db, "pu_refresh_token", "old_rt")
        plan_usage._kv_set(self.db, "pu_expires_at", str(int(time.time() * 1000) - 60_000))

        # Mock the refresh endpoint
        def _mock_urlopen(req, timeout=None):
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = lambda *a: None
            if "v1/oauth/token" in req.full_url:
                mock_resp.read.return_value = json.dumps(SAMPLE_REFRESH_RESP).encode()
            else:
                mock_resp.read.return_value = json.dumps(SAMPLE_USAGE).encode()
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            result = plan_usage.get_plan_usage(self.db)
        self.assertEqual(result["data"], SAMPLE_USAGE)
        # Token rotated
        self.assertEqual(plan_usage._kv_get(self.db, "pu_access_token"), "sk-ant-oat01-NEW")

    def test_reset_token_store(self):
        plan_usage._kv_set(self.db, "pu_access_token", "x")
        plan_usage.reset_token_store(self.db)
        self.assertIsNone(plan_usage._kv_get(self.db, "pu_access_token"))


if __name__ == "__main__":
    unittest.main()
