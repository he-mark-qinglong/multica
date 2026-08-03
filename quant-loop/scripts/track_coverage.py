#!/usr/bin/env python3
"""Coverage trend tracker — records pytest coverage over time.

Run after CI to append a data point to coverage_history.jsonl.
Trend data can be visualized to detect coverage regression.

Usage:
    python3 scripts/track_coverage.py
    # Reads .coverage, computes line coverage, appends to history.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = REPO_ROOT / ".coverage_history.jsonl"


def run_coverage() -> dict:
    """Run pytest with coverage and parse results."""
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "_shared/", "strategies/",
            "--cov=_shared",
            "--cov-report=json:.coverage_tmp.json",
            "--cov-report=term-missing",
            "-q", "--tb=no",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )

    cov_file = REPO_ROOT / ".coverage_tmp.json"
    if not cov_file.exists():
        return {
            "error": "coverage file not generated",
            "stdout_tail": result.stdout[-500:] if result.stdout else "",
        }

    import json
    cov_data = json.loads(cov_file.read_text())
    cov_file.unlink()  # cleanup

    total = cov_data.get("totals", {})
    return {
        "line_coverage_pct": round(total.get("percent_covered", 0), 2),
        "covered_lines": total.get("covered_lines", 0),
        "missing_lines": total.get("missing_lines", 0),
        "num_statements": total.get("num_statements", 0),
        "num_files": total.get("num_files", 0),
    }


def append_history(metrics: dict):
    """Append a coverage data point to history."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **metrics,
    }
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def trend_summary(last_n: int = 10) -> dict:
    """Summarize recent coverage trend."""
    if not HISTORY_FILE.exists():
        return {"trend": "no history"}

    entries = []
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if not entries:
        return {"trend": "empty history"}

    recent = entries[-last_n:]
    coverages = [e.get("line_coverage_pct", 0) for e in recent if "line_coverage_pct" in e]

    if len(coverages) < 2:
        return {"trend": "insufficient data", "latest": coverages[-1] if coverages else None}

    delta = coverages[-1] - coverages[0]
    return {
        "trend": "improving" if delta > 0.5 else ("declining" if delta < -0.5 else "stable"),
        "latest": coverages[-1],
        "delta_over_period": round(delta, 2),
        "data_points": len(coverages),
    }


if __name__ == "__main__":
    print("Running coverage analysis...", flush=True)
    metrics = run_coverage()

    if "error" in metrics:
        print(f"ERROR: {metrics['error']}")
        print(metrics.get("stdout_tail", ""))
        sys.exit(1)

    entry = append_history(metrics)
    print(f"\nCoverage recorded: {metrics['line_coverage_pct']}% "
          f"({metrics['covered_lines']}/{metrics['num_statements']} lines)")

    trend = trend_summary()
    if trend.get("trend") != "no history":
        print(f"Trend: {trend['trend']} "
              f"(latest={trend.get('latest', '?')}%, "
              f"Δ={trend.get('delta_over_period', '?')}% over {trend.get('data_points', 0)} runs)")
