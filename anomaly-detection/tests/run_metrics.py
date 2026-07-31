"""Concrete-metrics benchmark for anomaly-detection (SMA-35770-088).

Runs three scenarios and prints a deterministic metrics report:

1. E2E on bundled sample_metrics/ via run.py
2. Synthetic stress: 100k points, mixed metrics, default bank
3. Detector matrix: per-detector flag-rate on a known-spike series

Usage: `python3 tests/run_metrics.py`
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anomaly_detector import (  # noqa: E402
    AnomalyDetector,
    EWMAZScoreDetector,
    IQRDetector,
    MetricPoint,
    RateOfChangeDetector,
    RobustZScoreDetector,
    ZScoreDetector,
)


def _hdr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _metric(name: str, value, suffix: str = "") -> str:
    return f"  {name:<32} {value}{suffix}"


def scenario_e2e() -> dict:
    """Run run.py against the bundled sample_metrics and read the report."""
    _hdr("Scenario 1 — E2E run.py against sample_metrics/")
    sample = ROOT / "sample_metrics"
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "run.py"),
             "--paths", str(sample),
             "--state-dir", tmp,
             "--quiet",
             "--min-samples", "5",
             "--cooldown-sec", "0"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        elapsed = time.perf_counter() - started
        if proc.returncode != 0:
            print("STDOUT:", proc.stdout)
            print("STDERR:", proc.stderr)
            raise SystemExit(proc.returncode)
        report = json.loads(Path(tmp, "last-report.json").read_text())
    summary = {
        "scenario": "e2e_cli_run_py_sample_metrics",
        "files_scanned": sum(1 for _ in sample.glob("*.jsonl")),
        "elapsed_sec": round(elapsed, 4),
        "total_points": report["total_points"],
        "anomaly_count": report["anomaly_count"],
        "suppressed_count": sum(report["suppressed"].values()),
        "by_severity": report["by_severity"],
        "by_detector": report["by_detector"],
        "by_metric": report["by_metric"],
        "points_per_sec": report["points_per_sec"],
    }
    print(_metric("files scanned", summary["files_scanned"]))
    print(_metric("elapsed (incl. python startup)", f"{summary['elapsed_sec']:.4f}", "s"))
    print(_metric("total points", summary["total_points"]))
    print(_metric("anomaly count", summary["anomaly_count"]))
    print(_metric("suppressed", summary["suppressed_count"]))
    print(_metric("points/sec", summary["points_per_sec"]))
    print(_metric("by severity", summary["by_severity"]))
    print(_metric("by detector", summary["by_detector"]))
    print(_metric("by metric", summary["by_metric"]))
    return summary


def scenario_stress() -> dict:
    """100k synthetic points, mixed metrics, full default bank."""
    _hdr("Scenario 2 — 100k synthetic points, default detector bank")
    rng = random.Random(20260726)
    metrics = ["cpu_pct", "api_ms", "db_pool_in_use", "trade_volume", "memory_pct"]
    detectors = [
        ZScoreDetector(window=50, z_threshold=3.0),
        RobustZScoreDetector(window=50, z_threshold=3.5),
        EWMAZScoreDetector(halflife=20, z_threshold=3.0),
        IQRDetector(window=50, k=1.5),
        RateOfChangeDetector(window=50, z_threshold=3.0),
    ]
    runner = AnomalyDetector(detectors=detectors, cooldown_sec=0.0, min_samples=10)
    started = time.perf_counter()
    n = 100_000
    ts_base = 1_700_000_000.0
    injected_spikes = 0
    for i in range(n):
        name = metrics[i % len(metrics)]
        # 99.5% normal, 0.5% spike
        if rng.random() < 0.005:
            v = rng.uniform(50, 200)
            injected_spikes += 1
        else:
            v = rng.gauss(50.0, 5.0)
        runner.update(MetricPoint(ts=ts_base + i, name=name, value=v))
    elapsed = time.perf_counter() - started
    summary = {
        "scenario": "stress_100k_default_bank",
        "n_points": n,
        "injected_spikes": injected_spikes,
        "elapsed_sec": round(elapsed, 4),
        "points_per_sec": round(n / elapsed, 2),
        "anomaly_count": len(runner.report.anomalies),
        "by_severity": runner.report.by_severity(),
        "by_detector": runner.report.by_detector(),
        "by_metric": runner.report.by_metric(),
        "detectors": [type(d).__name__ for d in detectors],
    }
    print(_metric("n points", summary["n_points"]))
    print(_metric("injected spike ratio", "0.5%"))
    print(_metric("elapsed", f"{summary['elapsed_sec']:.4f}", "s"))
    print(_metric("points/sec", summary["points_per_sec"]))
    print(_metric("anomalies emitted", summary["anomaly_count"]))
    print(_metric("by severity", summary["by_severity"]))
    print(_metric("by detector", summary["by_detector"]))
    print(_metric("by metric", summary["by_metric"]))
    return summary


def scenario_matrix() -> dict:
    """Per-detector flag rate on a clean series with known spike."""
    _hdr("Scenario 3 — per-detector flag matrix on a known-spike series")
    rng = random.Random(123)
    # 100 points normal + 1 spike + 50 more normal -> 151 total.
    series = [50.0 + rng.gauss(0, 1.0) for _ in range(100)]
    spike_at = 100
    spike_value = 200.0
    series.append(spike_value)
    series.extend([50.0 + rng.gauss(0, 1.0) for _ in range(50)])
    detectors = [
        ("zscore", ZScoreDetector(window=30, z_threshold=3.0)),
        ("robust_zscore", RobustZScoreDetector(window=30, z_threshold=3.5)),
        ("ewma_zscore", EWMAZScoreDetector(halflife=20, z_threshold=3.0)),
        ("iqr", IQRDetector(window=30, k=1.5)),
        ("rate_of_change", RateOfChangeDetector(window=30, z_threshold=3.0)),
    ]
    matrix = {}
    for name, det in detectors:
        runner = AnomalyDetector(detectors=[det], cooldown_sec=0.0, min_samples=10)
        for i, v in enumerate(series):
            runner.update(MetricPoint(ts=1.0 + i, name="x", value=v))
        fired_at = [a.ts - 1.0 for a in runner.report.anomalies]
        # Identify whether the spike was detected.
        spike_caught = any(abs(ts - spike_at) < 1.0 for ts in fired_at)
        matrix[name] = {
            "anomaly_count": len(runner.report.anomalies),
            "fired_at_indices": [int(t) for t in fired_at],
            "spike_caught": spike_caught,
        }
    summary = {
        "scenario": "per_detector_matrix_clean_series_with_one_spike",
        "series_length": len(series),
        "spike_index": spike_at,
        "spike_value": spike_value,
        "matrix": matrix,
    }
    print(_metric("series length", summary["series_length"]))
    print(_metric("spike at index", summary["spike_index"]))
    print(_metric("spike value", summary["spike_value"]))
    print("  Per-detector flag matrix:")
    for name, m in matrix.items():
        verdict = "PASS" if m["spike_caught"] else "MISS"
        print(f"    {name:<18} anomalies={m['anomaly_count']:<3} fired_at={m['fired_at_indices']} spike={verdict}")
    return summary


def main() -> int:
    summaries = [
        scenario_e2e(),
        scenario_stress(),
        scenario_matrix(),
    ]
    out_path = ROOT / "tests" / "last_metrics.json"
    out_path.write_text(json.dumps(summaries, indent=2))
    print()
    print("=" * 72)
    print(f"Wrote summary -> {out_path}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())