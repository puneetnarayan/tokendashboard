import json
import tempfile
import unittest
from pathlib import Path

from token_dashboard import hooks_catalog
from token_dashboard import plugins as plugins_mod


class HooksCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = self.tmp / "settings.json"
        self.script = self.tmp / "hooks" / "my-hook.sh"
        self.script.parent.mkdir(parents=True)
        self.script.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
        self.settings.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"command": f"bash {self.script}"}]},
                    {"matcher": "mcp__.*", "hooks": [{"command": f"bash {self.script}"}]},  # dup, same script
                ],
                "SessionStart": [
                    {"hooks": [{"command": f"bash {self.tmp}/missing-hook.sh"}]},
                ],
            }
        }), encoding="utf-8")
        self.commands_dir = self.tmp / "commands"
        self.commands_dir.mkdir()
        (self.commands_dir / "rem.md").write_text("---\ndescription: reminders\n---", encoding="utf-8")

        self._orig_settings = hooks_catalog.SETTINGS_JSON
        self._orig_commands = hooks_catalog.COMMANDS_DIR
        hooks_catalog.SETTINGS_JSON = self.settings
        hooks_catalog.COMMANDS_DIR = self.commands_dir

        # scan_commands()/scan_agents() pull in cached_plugins(), which otherwise
        # reads the developer's real ~/.claude/plugins/installed_plugins.json —
        # redirect it to an empty tmp path so results are deterministic, and
        # reset the module-level cache so a stale real-world scan can't leak in.
        self._orig_installed_json = plugins_mod.INSTALLED_JSON
        self._orig_plugins_settings = plugins_mod.SETTINGS_JSON
        plugins_mod.INSTALLED_JSON = self.tmp / "plugins" / "installed_plugins.json"
        plugins_mod.SETTINGS_JSON = self.settings
        plugins_mod._cache = {"at": 0.0, "data": []}

    def tearDown(self):
        hooks_catalog.SETTINGS_JSON = self._orig_settings
        hooks_catalog.COMMANDS_DIR = self._orig_commands
        plugins_mod.INSTALLED_JSON = self._orig_installed_json
        plugins_mod.SETTINGS_JSON = self._orig_plugins_settings
        plugins_mod._cache = {"at": 0.0, "data": []}

    def test_hooks_deduped_across_matchers(self):
        rows = hooks_catalog.scan_hooks()
        # my-hook.sh appears under 2 matchers but must count once
        names = [r["name"] for r in rows if r["name"] == "my-hook.sh"]
        self.assertEqual(len(names), 1)

    def test_broken_hook_detected(self):
        rows = hooks_catalog.scan_hooks()
        broken = next(r for r in rows if r["name"] == "missing-hook.sh")
        self.assertEqual(broken["status"], "broken — script missing")

    def test_user_commands_scanned(self):
        rows = hooks_catalog.scan_commands()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "rem")
        self.assertEqual(rows[0]["source"], "user")


if __name__ == "__main__":
    unittest.main()
