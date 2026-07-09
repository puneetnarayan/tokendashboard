import json
import tempfile
import unittest
from pathlib import Path

from token_dashboard import plugins as plugins_mod


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


class PluginCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude_dir = self.tmp / ".claude"
        self.install_path = self.claude_dir / "plugins" / "cache" / "acme" / "widget" / "1.0.0"

        _write_json(self.claude_dir / "plugins" / "installed_plugins.json", {
            "version": 2,
            "plugins": {
                "widget@acme": [{
                    "installPath": str(self.install_path),
                    "version": "1.0.0",
                }]
            },
        })
        _write_json(self.claude_dir / "settings.json", {
            "enabledPlugins": {"widget@acme": False}
        })
        _write_json(self.install_path / ".claude-plugin" / "plugin.json", {
            "name": "widget", "description": "Does widget things."
        })
        (self.install_path / "skills" / "make-widget").mkdir(parents=True)
        (self.install_path / "skills" / "make-widget" / "SKILL.md").write_text("x" * 400, encoding="utf-8")
        (self.install_path / "agents").mkdir(parents=True)
        (self.install_path / "agents" / "widget-fixer.md").write_text("agent", encoding="utf-8")

        self._orig_installed = plugins_mod.INSTALLED_JSON
        self._orig_settings = plugins_mod.SETTINGS_JSON
        plugins_mod.INSTALLED_JSON = self.claude_dir / "plugins" / "installed_plugins.json"
        plugins_mod.SETTINGS_JSON = self.claude_dir / "settings.json"

    def tearDown(self):
        plugins_mod.INSTALLED_JSON = self._orig_installed
        plugins_mod.SETTINGS_JSON = self._orig_settings

    def test_scan_plugins_basic_fields(self):
        rows = plugins_mod.scan_plugins()
        self.assertEqual(len(rows), 1)
        p = rows[0]
        self.assertEqual(p["name"], "widget")
        self.assertEqual(p["source"], "acme")
        self.assertEqual(p["version"], "1.0.0")
        self.assertEqual(p["description"], "Does widget things.")
        self.assertFalse(p["enabled"])  # settings.json says False

    def test_component_counts(self):
        rows = plugins_mod.scan_plugins()
        comp = rows[0]["components"]
        self.assertEqual(comp["skills"], 1)
        self.assertEqual(comp["agents"], 1)
        self.assertEqual(comp["tokens"], 100)  # 400 chars // 4

    def test_missing_installed_json_returns_empty(self):
        plugins_mod.INSTALLED_JSON = self.tmp / "does-not-exist.json"
        self.assertEqual(plugins_mod.scan_plugins(), [])


if __name__ == "__main__":
    unittest.main()
