"""Tests for log_aggregator.

Run with: `python3 -m unittest tests.test_log_aggregator` from the log-aggregator
directory, or with pytest: `pytest -q tests/`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from log_aggregator import (  # noqa: E402
    LEVELS,
    LogParser,
    LogRecord,
    Summary,
    aggregate,
    aggregate_paths,
    iter_log_files,
)


class TestParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = LogParser()

    def test_jsonl(self):
        rec = self.parser.parse_line(
            '{"ts": "2024-08-03T10:14:22Z", "level": "ERROR", "message": "boom"}',
            source_hint="svc",
        )
        self.assertEqual(rec.level, "ERROR")
        self.assertEqual(rec.source, "svc")
        self.assertEqual(rec.message, "boom")
        self.assertGreater(rec.ts, 0)

    def test_python_logging(self):
        rec = self.parser.parse_line(
            "2024-08-03 10:14:22,123 INFO app.module: hello world",
            source_hint="py",
        )
        self.assertEqual(rec.level, "INFO")
        self.assertEqual(rec.source, "app.module")
        self.assertEqual(rec.message, "hello world")

    def test_syslog(self):
        rec = self.parser.parse_line(
            "Aug  3 10:14:22 host app[123]: WARN something went wrong",
            source_hint="syslog",
        )
        self.assertEqual(rec.level, "WARNING")  # WARN alias -> WARNING
        self.assertEqual(rec.source, "app")
        self.assertIn("something", rec.message)
        # Syslog year is ambiguous; parser should pick a sensible one.
        # Either this year or last year is acceptable, but it must not be 0.
        self.assertGreater(rec.ts, 0)

    def test_iso_with_level(self):
        rec = self.parser.parse_line(
            "2024-08-03T10:14:22.500+00:00 CRITICAL kernel: panic",
            source_hint="iso",
        )
        self.assertEqual(rec.level, "CRITICAL")
        self.assertEqual(rec.source, "iso")
        self.assertIn("panic", rec.message)

    def test_level_prefix_fallback(self):
        rec = self.parser.parse_line("ERROR no timestamp here", source_hint="misc")
        self.assertEqual(rec.level, "ERROR")
        self.assertEqual(rec.ts, 0.0)

    def test_plain(self):
        rec = self.parser.parse_line("just some text", source_hint="misc")
        self.assertEqual(rec.level, "OTHER")
        self.assertEqual(rec.source, "misc")

    def test_empty_line(self):
        rec = self.parser.parse_line("", source_hint="misc")
        self.assertEqual(rec.level, "OTHER")
        self.assertEqual(rec.message, "")


class TestAggregate(unittest.TestCase):
    def test_empty(self):
        s = aggregate([])
        self.assertEqual(s.total, 0)
        self.assertEqual(s.parsed, 0)
        self.assertEqual(s.unparsed, 0)

    def test_bucketing(self):
        records = [
            LogRecord(ts=1000.0, level="INFO", source="a", message="x"),
            LogRecord(ts=1060.0, level="INFO", source="a", message="y"),
            LogRecord(ts=1060.0, level="ERROR", source="b", message="z"),
            LogRecord(ts=1120.0, level="INFO", source="b", message="w"),
        ]
        s = aggregate(records)
        self.assertEqual(s.total, 4)
        self.assertEqual(s.parsed, 4)
        self.assertEqual(s.by_level["INFO"], 3)
        self.assertEqual(s.by_level["ERROR"], 1)
        self.assertEqual(s.by_source["a"], 2)
        self.assertEqual(s.by_source["b"], 2)
        # minute buckets at 60s boundaries:
        #   ts=1000 -> bucket 960
        #   ts=1060 -> bucket 1020  (1060 // 60 = 17, 17*60 = 1020)
        #   ts=1120 -> bucket 1080
        self.assertEqual(s.by_minute_level[960]["INFO"], 1)
        self.assertEqual(s.by_minute_level[1020]["INFO"], 1)
        self.assertEqual(s.by_minute_level[1020]["ERROR"], 1)
        self.assertEqual(s.by_minute_level[1080]["INFO"], 1)
        # duration
        self.assertAlmostEqual(s.duration_sec, 120.0, places=2)

    def test_unparsed(self):
        records = [
            LogRecord(ts=0.0, level="OTHER", source="x", message="", raw=""),
            LogRecord(ts=1000.0, level="INFO", source="x", message="ok"),
        ]
        s = aggregate(records)
        self.assertEqual(s.total, 2)
        self.assertEqual(s.parsed, 1)
        self.assertEqual(s.unparsed, 1)


class TestFileInventory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (Path(self.tmp) / "a.log").write_text(
            "2024-08-03 10:14:22,123 INFO a: line1\n"
            "2024-08-03 10:14:23,123 INFO a: line2\n"
        )
        (Path(self.tmp) / "b.log").write_text(
            "Aug  3 10:14:22 host b: INFO hello\n"
            "Aug  3 10:14:23 host b: ERROR boom\n"
        )

    def test_iter_log_files_directory(self):
        files = list(iter_log_files([self.tmp]))
        labels = {label for label, _ in files}
        self.assertIn("a", labels)
        self.assertIn("b", labels)

    def test_iter_log_files_glob(self):
        files = list(iter_log_files([str(Path(self.tmp) / "*.log")]))
        labels = {label for label, _ in files}
        self.assertIn("a", labels)
        self.assertIn("b", labels)

    def test_aggregate_paths(self):
        summary, inventory = aggregate_paths([self.tmp])
        self.assertEqual(summary.total, 4)
        self.assertEqual(summary.parsed, 4)
        self.assertEqual(summary.by_level["INFO"], 3)
        self.assertEqual(summary.by_level["ERROR"], 1)
        self.assertIn("a", inventory)
        self.assertIn("b", inventory)
        self.assertEqual(inventory["a"]["line_count"], 2)
        self.assertEqual(inventory["b"]["line_count"], 2)


class TestRunScript(unittest.TestCase):
    """Smoke test the run.py entrypoint on the bundled sample_logs."""

    def test_run_end_to_end(self):
        import subprocess
        sample = Path(__file__).resolve().parent.parent / "sample_logs"
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py"),
                 "--paths", str(sample),
                 "--state-dir", tmp,
                 "--quiet"],
                capture_output=True, text=True, env=env, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            last = json.loads(Path(tmp, "last-summary.json").read_text())
            self.assertGreater(last["total"], 0)
            self.assertGreater(last["parsed"], 0)
            self.assertIn("by_level", last)
            self.assertIn("file_inventory", last)
            # All three sample files should be discovered
            self.assertGreaterEqual(len(last["file_inventory"]), 3)


if __name__ == "__main__":
    unittest.main()