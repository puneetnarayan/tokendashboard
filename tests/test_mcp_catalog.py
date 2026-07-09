import json
import tempfile
import unittest
from pathlib import Path

from token_dashboard import mcp_catalog


class McpCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude_json = self.tmp / ".claude.json"
        self.claude_json.write_text(json.dumps({
            "mcpServers": {
                "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
            },
            "projects": {
                "/Users/tulen": {
                    "mcpServers": {
                        "framer": {"url": "https://mcp.unframer.co/mcp?id=x"}
                    }
                }
            }
        }), encoding="utf-8")
        self.known = self.tmp / "mcp_known_connectors.json"
        self.known.write_text(json.dumps({
            "connectors": [
                {"name": "Gmail", "description": "Mail read/search/draft.", "status": "connected"}
            ]
        }), encoding="utf-8")
        self._orig_claude_json = mcp_catalog.CLAUDE_JSON
        self._orig_known = mcp_catalog.KNOWN_CONNECTORS_JSON
        mcp_catalog.CLAUDE_JSON = self.claude_json
        mcp_catalog.KNOWN_CONNECTORS_JSON = self.known

    def tearDown(self):
        mcp_catalog.CLAUDE_JSON = self._orig_claude_json
        mcp_catalog.KNOWN_CONNECTORS_JSON = self._orig_known

    def test_local_servers_from_global_and_project(self):
        rows = mcp_catalog.scan_mcp()
        names = {r["name"] for r in rows}
        self.assertIn("playwright", names)
        self.assertIn("framer", names)
        playwright = next(r for r in rows if r["name"] == "playwright")
        self.assertEqual(playwright["kind"], "local")
        self.assertEqual(playwright["transport"], "stdio")
        framer = next(r for r in rows if r["name"] == "framer")
        self.assertEqual(framer["transport"], "http")

    def test_known_connectors_included(self):
        rows = mcp_catalog.scan_mcp()
        gmail = next(r for r in rows if r["name"] == "Gmail")
        self.assertEqual(gmail["kind"], "account")
        self.assertIsNone(gmail["file_path"])
        self.assertEqual(gmail["status"], "connected")

    def test_missing_claude_json_still_returns_known_connectors(self):
        mcp_catalog.CLAUDE_JSON = self.tmp / "nope.json"
        rows = mcp_catalog.scan_mcp()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Gmail")


if __name__ == "__main__":
    unittest.main()
