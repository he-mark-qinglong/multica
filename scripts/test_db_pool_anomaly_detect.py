#!/usr/bin/env python3
"""
test_db_pool_anomaly_detect.py — unit tests for the Monitor #98 anomaly detector.

Runs under the standard library's unittest so it does not require pytest.
Synthesizes JSON snapshots in a tempdir, then exercises:

* detect_anomalies() with no anomalies in series
* detect_anomalies() with a single-metric spike (true positive)
* detect_anomalies() with a constant-baseline and sudden spike
  (MAD==0 fallback path)
* detect_anomalies() with min_samples warm-up
* load_snapshots() skip-on-bad-json
* render_markdown() output shape

EVIDENCE is collected at the bottom of this file as a tiny evidence block
that the test prints when run with --evidence.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_pool_anomaly_detect as detector  # noqa: E402


def _snapshot(ts: datetime, metrics: dict, util: float = 0.0,
              verdict: str = "no-op") -> dict:
    """Build a snapshot dict shaped like db-pool-monitor writes."""
    return {
        "ts_epoch": int(ts.timestamp()),
        "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": "test-run",
        "autopilot_id": "test",
        "host_used": "test",
        "daemon_alive": True,
        "daemon_pid": 0,
        "metrics": metrics,
        "pool_util_pct": util,
        "verdict": verdict,
    }


def _write(tmpdir: str, snapshots: list[dict]) -> list[tuple[str, dict]]:
    """Persist *snapshots* as dbpool-*.json files and return [(path, dict),...]."""
    out: list[tuple[str, dict]] = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, snap in enumerate(snapshots):
        ts = base + timedelta(minutes=i)
        snap["ts_epoch"] = int(ts.timestamp())
        snap["ts_utc"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        path = os.path.join(tmpdir, f"dbpool-{snap['ts_utc']}.json")
        with open(path, "w") as fp:
            json.dump(snap, fp)
        out.append((path, snap))
    return out


class TestAnomalyDetector(unittest.TestCase):
    def test_no_anomalies_on_stable_series(self) -> None:
        # Stable metric, tiny noise — should not flag.
        snaps = []
        for i in range(20):
            snaps.append(_snapshot(
                datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc),
                {"active": 1 + (i % 2), "idle": 20, "idle_in_tx": 0,
                 "stuck_in_tx": 0, "slow_queries": 0, "oldest_tx_age_sec": None},
                util=4.0,
            ))
        findings = detector.detect_anomalies(snaps, window=10, threshold=3.5)
        self.assertEqual(findings, [], "stable series must yield zero findings")

    def test_spike_is_detected(self) -> None:
        # 14 samples of active=1, then one huge spike.
        snaps = []
        for i in range(14):
            snaps.append(_snapshot(
                datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc),
                {"active": 1, "idle": 24, "idle_in_tx": 0, "stuck_in_tx": 0,
                 "slow_queries": 0, "oldest_tx_age_sec": None},
                util=4.0,
            ))
        snaps.append(_snapshot(
            datetime(2026, 1, 1, 0, 14, tzinfo=timezone.utc),
            {"active": 80, "idle": 5, "idle_in_tx": 0, "stuck_in_tx": 0,
             "slow_queries": 0, "oldest_tx_age_sec": None},
            util=90.0,
        ))
        findings = detector.detect_anomalies(snaps, window=10, threshold=3.5)
        self.assertGreaterEqual(len(findings), 1, "spike must be flagged")
        flagged = {m["metric"] for f in findings for m in f["anomalous_metrics"]}
        self.assertIn("active", flagged)
        self.assertIn("pool_util_pct", flagged)

    def test_constant_baseline_fallback(self) -> None:
        # 12 snapshots with active=2 (constant), then one snap at active=99.
        snaps = []
        for i in range(12):
            snaps.append(_snapshot(
                datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc),
                {"active": 2, "idle": 22, "idle_in_tx": 0, "stuck_in_tx": 0,
                 "slow_queries": 0, "oldest_tx_age_sec": None},
                util=4.0,
            ))
        snaps.append(_snapshot(
            datetime(2026, 1, 1, 0, 12, tzinfo=timezone.utc),
            {"active": 99, "idle": 1, "idle_in_tx": 0, "stuck_in_tx": 0,
             "slow_queries": 0, "oldest_tx_age_sec": None},
            util=100.0,
        ))
        findings = detector.detect_anomalies(snaps, window=10, threshold=3.5)
        self.assertGreaterEqual(len(findings), 1,
                               "constant-baseline spike must be flagged")
        for f in findings:
            methods = {m["method"] for m in f["anomalous_metrics"]}
            # Either "constant" (any deviation on flat series) or fallback path.
            self.assertTrue(methods & {"constant", "stddev"}, methods)

    def test_min_samples_warmup(self) -> None:
        # Only 5 baseline samples — below the 8-sample warm-up; must not flag.
        snaps = [_snapshot(
            datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc),
            {"active": 1 if i < 5 else 999, "idle": 22,
             "idle_in_tx": 0, "stuck_in_tx": 0, "slow_queries": 0,
             "oldest_tx_age_sec": None},
            util=4.0,
        ) for i in range(6)]
        findings = detector.detect_anomalies(snaps, window=10, threshold=3.5,
                                             min_samples=8)
        self.assertEqual(findings, [],
                         "detector must remain silent during warm-up")

    def test_load_snapshots_skips_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # One good snapshot
            snap = _snapshot(datetime(2026, 1, 1, tzinfo=timezone.utc),
                             {"active": 1, "idle": 10})
            with open(os.path.join(tmp, "dbpool-2026-01-01T00-00-00Z.json"),
                      "w") as fp:
                fp.write(json.dumps(snap))
            # One corrupt snapshot
            with open(os.path.join(tmp, "dbpool-2026-01-01T00-01-00Z.json"),
                      "w") as fp:
                fp.write("{not json")
            loaded = detector.load_snapshots(tmp)
            self.assertEqual(len(loaded), 1, "corrupt json must be skipped")

    def test_markdown_render_shape(self) -> None:
        snaps = [_snapshot(
            datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc),
            {"active": 1 if i < 14 else 50, "idle": 22,
             "idle_in_tx": 0, "stuck_in_tx": 0, "slow_queries": 0,
             "oldest_tx_age_sec": None},
            util=4.0,
        ) for i in range(15)]
        findings = detector.detect_anomalies(snaps, window=10, threshold=3.5)
        md = detector.render_markdown(findings, n_snapshots=len(snaps))
        self.assertIn("## Anomaly scan", md)
        self.assertIn("Per-metric counts", md)

    def test_real_world_series(self) -> None:
        # End-to-end with realistic copy of production snapshots.
        snaps = []
        for i, active in enumerate([1, 1, 1, 1, 3, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1]):
            snaps.append(_snapshot(
                datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc),
                {"active": active, "idle": 24 - active,
                 "idle_in_tx": 0, "stuck_in_tx": 0, "slow_queries": 0,
                 "oldest_tx_age_sec": None},
                util=max(0, 100 * active / 25.0),
            ))
        # Spike at the end (a "real" anomaly that the rule-based monitor
        # would NOT catch because abs(active=7) < 10).
        snaps.append(_snapshot(
            datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
            {"active": 7, "idle": 18, "idle_in_tx": 0, "stuck_in_tx": 0,
             "slow_queries": 0, "oldest_tx_age_sec": None},
            util=28.0,
        ))
        findings = detector.detect_anomalies(snaps, window=10, threshold=3.5,
                                             min_samples=8)
        self.assertGreaterEqual(len(findings), 1)
        # The active spike should be flagged in the last snapshot.
        last = findings[-1]
        last_active = next((m for m in last["anomalous_metrics"]
                            if m["metric"] == "active"), None)
        self.assertIsNotNone(last_active)


class TestEVIDENCE(unittest.TestCase):
    """Emits a tiny EVIDENCE block when --evidence is passed."""

    def test_evidence_summary(self) -> None:
        if "--evidence" not in sys.argv:
            self.skipTest("pass --evidence to print summary")
        sys.argv.remove("--evidence")
        suite = unittest.TestLoader().loadTestsFromTestCase(TestAnomalyDetector)
        runner = unittest.TextTestRunner(verbosity=1)
        result = runner.run(suite)
        self.assertTrue(result.wasSuccessful(), "evidence tests must pass")


if __name__ == "__main__":
    print(
        "EVIDENCE: db_pool_anomaly_detect.py + test_db_pool_anomaly_detect.py "
        "ready.  Run detector with --json for raw findings, --md for "
        "markdown, see runbook entry db-pool-anomaly.md.",
        file=sys.stderr,
    )
    unittest.main()
