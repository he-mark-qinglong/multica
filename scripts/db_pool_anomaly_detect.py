#!/usr/bin/env python3
"""
db_pool_anomaly_detect.py — Monitor #98 (MAP-P9 / SMA-35869) anomaly detector.

Statistical anomaly detection over the multica db-pool-monitor JSON snapshots.

Approach
--------
For each numeric metric in the snapshot series, compute a rolling baseline
(median + MAD) and flag the latest observation if its modified z-score
exceeds a threshold (default 3.5, per Iglewicz & Hoaglin 1993).  Modified
z-score is robust to outliers: it uses the median instead of the mean and
the median absolute deviation instead of the standard deviation.  When MAD
is zero (no spread in the window) we fall back to mean +/- k*stddev so a
flat-baseline metric can still flag a sudden spike.

This complements (does NOT replace) the existing rule-based judgments in
``db-pool-monitor/run.py``.  Rule-based alerts catch absolute threshold
breaches (e.g. stuck transactions > 0); statistical alerts catch *relative*
deviations even when no absolute threshold is tripped (e.g. active
connections jumping from a 1.0 median to 6 when the rule only fires at 10).

Inputs
------
- ``--snapshots-dir DIR``  directory of dbpool-*.json files (default
  ``/home/smark/multica/db-pool-monitor``).
- ``--window N``           rolling window size in samples (default 12).
- ``--threshold Z``        modified-z flag threshold (default 3.5).
- ``--min-samples N``      minimum samples required before flagging
  (default 8, below this detector is "warming up").

Outputs
-------
- ``--json``      machine-readable findings on stdout
- ``--md``        markdown summary on stdout
- ``--out PATH``  write findings JSON to a file (default: none)
- ``--summary PATH`` write a one-line summary suitable for log shipping

Exit codes
----------
0  no anomalies
1  anomalies detected
2  usage error / no snapshots found

Example
-------
::

    db_pool_anomaly_detect.py --json
    db_pool_anomaly_detect.py --window 24 --threshold 3.0 --json | jq
    db_pool_anomaly_detect.py --md --out /tmp/anomaly.json --summary /tmp/anom.sum
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from typing import Iterable

METRIC_KEYS = (
    "idle",
    "active",
    "idle_in_tx",
    "stuck_in_tx",
    "slow_queries",
    "oldest_tx_age_sec",
    "pool_util_pct",
    "acquired_app_estimate",
)

# Iglewicz & Hoaglin (1993) recommended threshold for modified z-score outlier
# detection.  Values >= 3.5 are flagged.
DEFAULT_THRESHOLD = 3.5

# 1 / 1.4826 ≈ 0.6745 — the constant that makes MAD a consistent estimator
# of sigma under a normal distribution.
MAD_TO_SIGMA = 0.6745

# Fallback: when MAD is zero (window is constant), use mean +/- k*stddev to
# keep the detector useful on flat-baseline metrics.
STDDEV_FALLBACK_K = 4.0


def load_snapshots(directory: str) -> list[tuple[str, dict]]:
    """Read all ``dbpool-*.json`` files in *directory* sorted by ts_epoch asc.

    Files that fail to parse are skipped with a stderr warning.  Files
    without a parseable timestamp are sorted last.
    """
    paths = sorted(glob.glob(os.path.join(directory, "dbpool-*.json")))
    out: list[tuple[float, str, dict]] = []
    for p in paths:
        try:
            with open(p) as fp:
                d = json.load(fp)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warn: skip {p}: {exc}", file=sys.stderr)
            continue
        ts_epoch = d.get("ts_epoch")
        if ts_epoch is None:
            ts_utc = d.get("ts_utc") or d.get("ts") or ""
            try:
                # Accept "Z" suffix without timezone.
                from datetime import datetime
                ts_epoch = datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts_epoch = float("inf")
        out.append((float(ts_epoch), p, d))
    out.sort(key=lambda x: x[0])
    return [(p, d) for _, p, d in out]


def _modified_z(value: float, baseline_values: list[float]) -> tuple[float, str]:
    """Return (modified_z, method) for *value* against *baseline_values*.

    method is "mad" (median+MAD) or "stddev" (mean+stddev fallback) so the
    caller can record which estimator was used.
    """
    if len(baseline_values) < 2:
        return 0.0, "insufficient"
    median = statistics.median(baseline_values)
    abs_dev = [abs(v - median) for v in baseline_values]
    mad = statistics.median(abs_dev)
    if mad > 0:
        z = MAD_TO_SIGMA * (value - median) / mad
        return z, "mad"
    # Fallback when MAD is zero — use stddev on a tight threshold.
    mean = statistics.mean(baseline_values)
    stdev = statistics.pstdev(baseline_values)
    if stdev > 0:
        z = (value - mean) / stdev
        return z, "stddev"
    # Both MAD and stddev are zero — every baseline observation is identical
    # to the median/mean.  Any deviation from the constant is an anomaly.
    if value != median:
        return float("inf"), "constant"
    return 0.0, "constant"


def detect_anomalies(
    snapshots: list[tuple[str, dict]],
    metric_keys: Iterable[str] = METRIC_KEYS,
    window: int = 12,
    threshold: float = DEFAULT_THRESHOLD,
    min_samples: int = 8,
) -> list[dict]:
    """Walk snapshots in order; for each, compare to the rolling baseline.

    A snapshot is flagged if ANY of its tracked metrics is anomalous against
    the previous ``window`` samples (excluding itself).  Returns the list of
    findings, each containing which metric tripped, the value, baseline
    summary, modified z-score, and the snapshot file.
    """
    series: dict[str, list[float]] = {k: [] for k in metric_keys}
    findings: list[dict] = []
    for entry in snapshots:
        # Accept either (path, dict) tuples (real disk read) or bare dicts
        # (synthetic test fixtures).
        if isinstance(entry, tuple):
            path, snap = entry
            file_label = os.path.basename(path) if path else None
        else:
            snap = entry
            file_label = snap.get("__file__")
        metrics = snap.get("metrics", {}) or {}
        per_metric = []
        # Some keys (pool_util_pct, acquired_app_estimate) live at the top
        # level, not inside ``metrics``.  Pick them up accordingly.
        for key in metric_keys:
            if key in metrics:
                value = metrics[key]
            elif key in snap:
                value = snap[key]
            else:
                continue
            if value is None:
                continue
            baseline = series[key][-window:]
            if len(baseline) >= min_samples:
                z, method = _modified_z(float(value), baseline)
                if math.isinf(z) or abs(z) >= threshold:
                    per_metric.append({
                        "metric": key,
                        "value": value,
                        "baseline_median": statistics.median(baseline),
                        "baseline_mad": statistics.median(
                            [abs(v - statistics.median(baseline)) for v in baseline]
                        ),
                        "modified_z": z,
                        "method": method,
                        "window": len(baseline),
                    })
            series[key].append(float(value))
        if per_metric:
            findings.append({
                "file": file_label,
                "ts_utc": snap.get("ts_utc") or snap.get("ts"),
                "verdict_recorded": snap.get("verdict"),
                "anomalous_metrics": per_metric,
            })
    return findings


def render_markdown(findings: list[dict], n_snapshots: int) -> str:
    """Render a human-readable summary suitable for posting to a runbook."""
    if not findings:
        return f"## Anomaly scan\n\n{n_snapshots} snapshots scanned, 0 anomalies detected.\n"
    lines = [f"## Anomaly scan", ""]
    lines.append(f"Scanned **{n_snapshots}** snapshots, **{len(findings)}** flagged.")
    lines.append("")
    lines.append("| ts_utc | file | metric | value | baseline_median | modified_z | method |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for f in findings:
        for m in f["anomalous_metrics"]:
            z = m["modified_z"]
            z_str = "inf" if math.isinf(z) else f"{z:.2f}"
            lines.append(
                f"| {f.get('ts_utc','')} | {f['file']} | {m['metric']} | "
                f"{m['value']} | {m['baseline_median']:.2f} | {z_str} | {m['method']} |"
            )
    lines.append("")
    by_metric: dict[str, int] = {}
    for f in findings:
        for m in f["anomalous_metrics"]:
            by_metric[m["metric"]] = by_metric.get(m["metric"], 0) + 1
    lines.append("### Per-metric counts")
    for k, v in sorted(by_metric.items(), key=lambda x: -x[1]):
        lines.append(f"- `{k}`: {v}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshots-dir", default="/home/smark/multica/db-pool-monitor",
                   help="directory containing dbpool-*.json snapshots")
    p.add_argument("--window", type=int, default=12, help="rolling window size (default 12)")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"modified-z flag threshold (default {DEFAULT_THRESHOLD})")
    p.add_argument("--min-samples", type=int, default=8,
                   help="minimum baseline samples before flagging (default 8)")
    p.add_argument("--json", action="store_true", help="emit findings JSON on stdout")
    p.add_argument("--md", action="store_true", help="emit markdown summary on stdout")
    p.add_argument("--out", help="write findings JSON to PATH")
    p.add_argument("--summary", help="write one-line summary to PATH")
    p.add_argument("--metrics", nargs="*", default=list(METRIC_KEYS),
                   help="restrict to a subset of metric keys")
    args = p.parse_args(argv)

    snapshots = load_snapshots(args.snapshots_dir)
    if not snapshots:
        print(f"ERROR: no snapshots found in {args.snapshots_dir}", file=sys.stderr)
        return 2

    findings = detect_anomalies(
        snapshots,
        metric_keys=args.metrics,
        window=args.window,
        threshold=args.threshold,
        min_samples=args.min_samples,
    )

    payload = {
        "snapshots_dir": args.snapshots_dir,
        "n_snapshots": len(snapshots),
        "window": args.window,
        "threshold": args.threshold,
        "min_samples": args.min_samples,
        "metrics": list(args.metrics),
        "n_anomalies": len(findings),
        "findings": findings,
    }

    if args.out:
        with open(args.out, "w") as fp:
            json.dump(payload, fp, indent=2, sort_keys=True)
            fp.write("\n")
    if args.summary:
        with open(args.summary, "w") as fp:
            fp.write(
                f"anomalies={len(findings)}/{len(snapshots)} "
                f"window={args.window} threshold={args.threshold}\n"
            )
    if args.json and not args.md:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    if args.md:
        sys.stdout.write(render_markdown(findings, len(snapshots)))
    if not (args.json or args.md):
        # Default: print a one-liner so cron can log it without flooding.
        print(
            f"scanned={len(snapshots)} anomalies={len(findings)} "
            f"window={args.window} threshold={args.threshold}"
        )
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())