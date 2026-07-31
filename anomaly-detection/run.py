#!/usr/bin/env python3
"""anomaly-detection — concrete entry point for SMA-35770-088 (Monitor #88).

Reads JSONL metric streams from one or more paths, runs the configured
detector bank, and writes a structured AnomalyReport.

Usage:
    python3 run.py [--paths PATH ...] [--output PATH] [--state-dir PATH]
                   [--detectors name:arg=arg ...] [--cooldown-sec N]
                   [--min-samples N] [--quiet]

If --paths is omitted, defaults to ./sample_metrics so the detector runs
out-of-the-box for evidence collection. Detectors default to the bank
returned by `default_detector_bank()`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Local import — works whether invoked as `python3 run.py` or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from anomaly_detector import (  # noqa: E402
    AnomalyDetector,
    BoundsDetector,
    DETECTORS,
    MetricPoint,
    default_detector_bank,
    detect_stream,
    parse_metric_point,
    stream_metrics,
)


DEFAULT_STATE_DIR = "/Users/mark/multica/anomaly-detection/state"


def _parse_kv(arg: str) -> dict:
    """Parse 'key=val,key=val' style detector options."""
    out: dict = {}
    for part in arg.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            # int / float coercion.
            try:
                out[k] = int(v)
                continue
            except ValueError:
                pass
            try:
                out[k] = float(v)
                continue
            except ValueError:
                pass
            out[k] = v
    return out


def _build_detectors(specs: list[str] | None) -> list:
    if not specs:
        return default_detector_bank()
    out = []
    for spec in specs:
        # name:arg=val,arg=val
        if ":" in spec:
            name, opts = spec.split(":", 1)
            kwargs = _parse_kv(opts)
        else:
            name, kwargs = spec, {}
        if name not in DETECTORS:
            raise SystemExit(f"unknown detector {name!r}; available: {sorted(DETECTORS)}")
        out.append(DETECTORS[name](**kwargs))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Anomaly detector (SMA-35770-088)")
    p.add_argument(
        "--paths", nargs="+", default=None,
        help="Files, directories, or glob patterns. Default: ./sample_metrics",
    )
    p.add_argument(
        "--detectors", nargs="*", default=None,
        help="Detector specs, e.g. zscore:window=80,z_threshold=2.5",
    )
    p.add_argument(
        "--cooldown-sec", type=float, default=60.0,
        help="Min seconds between consecutive fires on the same (metric, detector)",
    )
    p.add_argument(
        "--min-samples", type=int, default=10,
        help="Skip detection until each metric has this many samples",
    )
    p.add_argument(
        "--output", "-o", default=None,
        help="Write report to this path (default: <state-dir>/last-report.json)",
    )
    p.add_argument(
        "--state-dir", default=DEFAULT_STATE_DIR,
        help=f"Directory for snapshots (default: {DEFAULT_STATE_DIR})",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress stdout report, only write files",
    )
    args = p.parse_args()

    paths = args.paths or ["sample_metrics"]
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    detectors = _build_detectors(args.detectors)

    started = time.perf_counter()
    report = detect_stream(
        paths,
        detectors=detectors,
        cooldown_sec=args.cooldown_sec,
        min_samples=args.min_samples,
    )
    elapsed = time.perf_counter() - started

    payload = report.as_dict()
    payload["elapsed_sec"] = round(elapsed, 4)
    payload["points_per_sec"] = round(report.total_points / elapsed, 2) if elapsed > 0 else 0.0
    payload["detectors"] = [type(d).__name__ for d in detectors]
    payload["paths"] = list(paths)
    payload["run_ts_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    last_path = Path(args.output) if args.output else state_dir / "last-report.json"
    last_path.parent.mkdir(parents=True, exist_ok=True)
    last_path.write_text(json.dumps(payload, indent=2, default=str))

    snap_name = f"report-{time.strftime('%Y-%m-%dT%H-%M-%S', time.gmtime())}Z.json"
    snap_path = state_dir / snap_name
    snap_path.write_text(json.dumps(payload, indent=2, default=str))

    if not args.quiet:
        print(json.dumps(payload, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())