import json
import os
import tempfile
import unittest
from pathlib import Path

from token_dashboard.statusline import read_current_status, read_history


class StatuslineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.last = self.tmp / "last.json"
        self.hist = self.tmp / "history.jsonl"

    def test_current_returns_none_when_missing(self):
        self.assertIsNone(read_current_status(self.last))

    def test_current_parses_json(self):
        payload = {"model": {"id": "x"}, "context_window": {"used_percentage": 30}}
        self.last.write_text(json.dumps(payload))
        self.assertEqual(read_current_status(self.last), payload)

    def test_current_returns_none_on_bad_json(self):
        self.last.write_text("{not json")
        self.assertIsNone(read_current_status(self.last))

    def test_history_empty_when_missing(self):
        self.assertEqual(read_history(self.hist), [])

    def test_history_returns_entries(self):
        entries = [
            {"ts": "2026-04-25T10:00:00Z", "payload": {"x": 1}},
            {"ts": "2026-04-25T10:00:01Z", "payload": {"x": 2}},
        ]
        self.hist.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = read_history(self.hist)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["payload"]["x"], 1)
        self.assertEqual(result[1]["payload"]["x"], 2)

    def test_history_limit(self):
        entries = [{"ts": f"t{i}", "payload": {"i": i}} for i in range(50)]
        self.hist.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = read_history(self.hist, limit=10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0]["payload"]["i"], 40)  # last 10, oldest first
        self.assertEqual(result[-1]["payload"]["i"], 49)

    def test_history_skips_bad_lines(self):
        good = json.dumps({"ts": "t1", "payload": {"x": 1}})
        bad = "{garbage"
        self.hist.write_text(good + "\n" + bad + "\n" + good + "\n")
        result = read_history(self.hist)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
