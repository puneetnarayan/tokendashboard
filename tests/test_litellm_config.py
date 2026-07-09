"""Tests for litellm_config: discovery precedence, kill switch, normalization."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from token_dashboard.litellm_config import discover, mask_key, _normalize_origin


class TestNormalize(unittest.TestCase):
    def test_strips_path(self):
        self.assertEqual(_normalize_origin("https://proxy:4000/v1"), "https://proxy:4000")

    def test_strips_long_path(self):
        self.assertEqual(_normalize_origin("https://proxy.internal/api/v1/chat"), "https://proxy.internal")

    def test_no_port(self):
        self.assertEqual(_normalize_origin("https://proxy.internal"), "https://proxy.internal")

    def test_adds_https_if_missing(self):
        result = _normalize_origin("proxy.internal:4000/v1")
        self.assertIn("proxy.internal", result)
        self.assertNotIn("/v1", result)


class TestMaskKey(unittest.TestCase):
    def test_masks_long_key(self):
        result = mask_key("sk-abcdefgh1234")
        self.assertEqual(result, "sk-…1234")
        self.assertNotIn("abcdefgh", result)

    def test_short_key(self):
        self.assertEqual(mask_key("ab"), "sk-…")

    def test_empty_key(self):
        self.assertEqual(mask_key(""), "")


class TestDiscover(unittest.TestCase):
    def setUp(self):
        # Ensure env vars don't bleed in from the test environment
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _patch_paths(self, config_data=None, settings_data=None):
        config_content = json.dumps(config_data) if config_data is not None else None
        settings_content = json.dumps(settings_data) if settings_data is not None else None

        def fake_read(path_self, encoding=None):
            p = str(path_self)
            if "token-dashboard-config" in p:
                if config_content is None:
                    raise FileNotFoundError
                return config_content
            if "settings.json" in p:
                if settings_content is None:
                    raise FileNotFoundError
                return settings_content
            raise FileNotFoundError

        return patch("pathlib.Path.read_text", fake_read)

    def test_env_vars_win_over_settings(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://env-proxy:4000"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "sk-env-key"
        settings = {"env": {"ANTHROPIC_BASE_URL": "https://settings-proxy:4000", "ANTHROPIC_AUTH_TOKEN": "sk-settings"}}
        with self._patch_paths(settings_data=settings):
            result = discover()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "env")
        self.assertIn("env-proxy", result["base_url"])

    def test_settings_json_discovered(self):
        settings = {"env": {"ANTHROPIC_BASE_URL": "https://settings-proxy:4000/v1", "ANTHROPIC_AUTH_TOKEN": "sk-settings"}}
        with self._patch_paths(settings_data=settings):
            result = discover()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "settings_json")
        self.assertNotIn("/v1", result["base_url"])

    def test_missing_one_var_returns_none(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://proxy:4000"
        # ANTHROPIC_AUTH_TOKEN not set
        with self._patch_paths():
            result = discover()
        self.assertIsNone(result)

    def test_manual_config_wins(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://env-proxy:4000"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "sk-env"
        manual = {"litellm": {"base_url": "https://manual:4000", "api_key": "sk-manual", "enabled": True}}
        with self._patch_paths(config_data=manual):
            result = discover()
        self.assertEqual(result["source"], "manual")
        self.assertIn("manual", result["base_url"])

    def test_kill_switch_enabled_false(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://env-proxy:4000"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "sk-env"
        manual = {"litellm": {"base_url": "https://manual:4000", "api_key": "sk-manual", "enabled": False}}
        with self._patch_paths(config_data=manual):
            result = discover()
        self.assertIsNone(result)

    def test_no_config_returns_none(self):
        with self._patch_paths():
            result = discover()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
