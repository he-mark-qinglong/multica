"""Unit tests for historical_aggregator (Monitor #79)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Allow ``python3 -m unittest`` from the package root.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import historical_aggregator as ha  # noqa: E402


# ---------------------------------------------------------------------------
# Sample-data helpers
# ---------------------------------------------------------------------------

SAMPLES = HERE.parent / "sample_logs"


def _write_tmp_files(tmp: Path) -> list[Path]:
    """Copy the bundled sample_logs into a tmp dir as if they were backups.

    ``snapshot_a`` -> ``error-patterns.jsonl.pre-08-00-patrol``
    ``snapshot_b`` -> ``error-patterns.jsonl.pre-13-00-patrol``
    ``snapshot_c`` -> ``error-patterns.jsonl``  (the live file)

    This mirrors a realistic directory layout: timestamps embedded in backup
    names + the live file last.
    """
    paths = [
        (tmp / "error-patterns.jsonl.pre-08-00-patrol", SAMPLES / "snapshot_a.jsonl"),
        (tmp / "error-patterns.jsonl.pre-13-00-patrol", SAMPLES / "snapshot_b.jsonl"),
        (tmp / "error-patterns.jsonl", SAMPLES / "snapshot_c.jsonl"),
    ]
    for dst, src in paths:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return [p for p, _ in paths]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseOne(unittest.TestCase):
    def test_parses_valid_record(self):
        line = json.dumps({
            "ts": "2026-07-20T08:00:00+08:00",
            "source": "patrol_self_report",
            "category": "data_integrity",
            "pattern": "file_overwrite_recovery",
            "signature": "x",
        })
        rec = ha._parse_one(line)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.source, "patrol_self_report")
        self.assertEqual(rec.pattern, "file_overwrite_recovery")

    def test_skips_blank(self):
        self.assertIsNone(ha._parse_one(""))
        self.assertIsNone(ha._parse_one("   "))

    def test_skips_non_json(self):
        self.assertIsNone(ha._parse_one("not json at all"))

    def test_skips_non_dict(self):
        self.assertIsNone(ha._parse_one("[1, 2, 3]"))
        self.assertIsNone(ha._parse_one("42"))

    def test_skips_missing_required_fields(self):
        # missing 'pattern'
        bad = json.dumps({"ts": "t", "source": "s", "category": "c"})
        self.assertIsNone(ha._parse_one(bad))


class TestParseSnapshot(unittest.TestCase):
    def test_parses_bundled_snapshot(self):
        snap = ha.parse_snapshot(SAMPLES / "snapshot_a.jsonl")
        self.assertEqual(snap.label, "snapshot_a.jsonl")
        self.assertEqual(snap.line_count, 5)
        self.assertEqual(snap.parsed_count, 5)
        self.assertEqual(snap.skipped_count, 0)
        self.assertEqual(snap.first_ts, "2026-07-20T08:00:00+08:00")
        self.assertEqual(snap.last_ts, "2026-07-20T08:02:00+08:00")

    def test_parses_snapshot_with_garbage_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"ts":"t","source":"s","category":"c","pattern":"p"}\n')
            f.write("not-json-line\n")
            f.write("\n")
            f.write('{"ts":"t2","source":"s2","category":"c2","pattern":"p2"}\n')
            tmp = f.name
        try:
            snap = ha.parse_snapshot(Path(tmp))
            self.assertEqual(snap.line_count, 4)
            self.assertEqual(snap.parsed_count, 2)
            self.assertEqual(snap.skipped_count, 2)
        finally:
            os.unlink(tmp)


class TestFileOrdering(unittest.TestCase):
    def test_iter_orders_backups_then_live(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_tmp_files(tmp)
            labels = [label for label, _ in ha.iter_snapshot_files(tmp)]
            # backups sorted lex = chronological, live last
            self.assertEqual(labels, [
                "error-patterns.jsonl.pre-08-00-patrol",
                "error-patterns.jsonl.pre-13-00-patrol",
                "error-patterns.jsonl",
            ])


class TestRegressions(unittest.TestCase):
    def test_flags_only_patterns_that_doubled(self):
        # Two snapshots: A has pat_x=1, B has pat_x=2 -> doubles, regression.
        s_a = ha.Snapshot(label="a", path="a")
        s_b = ha.Snapshot(label="b", path="b")
        s_a.records = [ha.Record(ts="t", source="s", category="c", pattern="pat_x")]
        s_b.records = [
            ha.Record(ts="t", source="s", category="c", pattern="pat_x"),
            ha.Record(ts="t", source="s", category="c", pattern="pat_x"),
        ]
        s_a.parsed_count = 1
        s_b.parsed_count = 2
        agg = ha.aggregate_snapshots([s_a, s_b])
        regs = [r for r in agg.regressions if r["pattern"] == "pat_x"]
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["from_count"], 1)
        self.assertEqual(regs[0]["to_count"], 2)


class TestNewAndResolved(unittest.TestCase):
    def test_new_in_latest_and_resolved_in_latest(self):
        s_a = ha.Snapshot(label="a", path="a")
        s_b = ha.Snapshot(label="b", path="b")
        s_a.records = [
            ha.Record(ts="t", source="s", category="c", pattern="only_a"),
            ha.Record(ts="t", source="s", category="c", pattern="both"),
        ]
        s_b.records = [
            ha.Record(ts="t", source="s", category="c", pattern="both"),
            ha.Record(ts="t", source="s", category="c", pattern="only_b"),
        ]
        s_a.parsed_count = 2
        s_b.parsed_count = 2
        agg = ha.aggregate_snapshots([s_a, s_b])
        self.assertIn("only_b", agg.new_patterns)
        self.assertIn("only_a", agg.resolved_patterns)
        self.assertNotIn("both", agg.new_patterns)
        self.assertNotIn("both", agg.resolved_patterns)


class TestEndToEndTmp(unittest.TestCase):
    def test_aggregate_directory_on_tmp_layout(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_tmp_files(tmp)
            agg = ha.aggregate_directory(tmp)
            self.assertEqual(len(agg.snapshots), 3)
            t = agg.totals()
            # 5 + 7 + 4 = 16 raw lines, all valid in our sample data
            self.assertEqual(t["lines_total"], 16)
            self.assertEqual(t["parsed_total"], 16)
            self.assertEqual(t["skipped_total"], 0)
            # unique patterns in our samples: file_overwrite_recovery, ECONNRESET,
            # backoff_429, lan_disk_below_30pct, tokyo_timeout_60034,
            # lan_outage_partial_8090_3210, rate_limit_60s, codex_401 = 8
            self.assertEqual(t["unique_patterns"], 8)
            # file_overwrite_recovery appears only in a and b -> resolved in c
            self.assertIn("file_overwrite_recovery", agg.resolved_patterns)
            # codex_401 / rate_limit_60s only in c -> new in c
            self.assertIn("codex_401", agg.new_patterns)
            self.assertIn("rate_limit_60s", agg.new_patterns)
            # earliest / latest bracketing
            self.assertEqual(agg.earliest_ts, "2026-07-20T08:00:00+08:00")
            self.assertEqual(agg.latest_ts, "2026-07-22T14:01:30+08:00")


class TestCLI(unittest.TestCase):
    def test_cli_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_tmp_files(tmp)
            rc = ha.main(["--output", "json", str(tmp)])
            self.assertEqual(rc, 0)

    def test_cli_text_output(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_tmp_files(tmp)
            rc = ha.main([str(tmp)])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
