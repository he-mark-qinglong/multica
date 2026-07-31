#!/usr/bin/env python3
"""log-aggregator — concrete entry point for SMA-35770-089 (Monitor #89).

Usage:
    python3 run.py [--paths PATH ...] [--output PATH] [--state-dir PATH]

If `--paths` is omitted, it defaults to `sample_logs/` so the aggregator
runs out-of-the-box for evidence collection. For production use, point it at
the actual log directory (e.g. `/var/log/multica/`).

Outputs:
    <state-dir>/last-summary.json     — most recent Summary (overwritten)
    <state-dir>/summary-<ts>.json     — timestamped snapshot for history
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Local import — works whether invoked as `python3 run.py` or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_aggregator import aggregate_paths  # noqa: E402


DEFAULT_STATE_DIR = "/Users/mark/multica/log-aggregator/state"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def main() -> int:
    p = argparse.ArgumentParser(description="Log aggregator (SMA-35770-089)")
    p.add_argument(
        "--paths", nargs="+", default=None,
        help="Files, directories, or glob patterns to aggregate. "
             "Default: ./sample_logs",
    )
    p.add_argument(
        "--output", "-o", default=None,
        help="Write summary to this path (default: <state-dir>/last-summary.json)",
    )
    p.add_argument(
        "--state-dir", default=DEFAULT_STATE_DIR,
        help=f"Directory for snapshots (default: {DEFAULT_STATE_DIR})",
    )
    p.add_argument(
        "--recursive", dest="recursive", action="store_true", default=True,
        help="Recurse into directories (default: True)",
    )
    p.add_argument(
        "--no-recursive", dest="recursive", action="store_false",
        help="Do not recurse into directories.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress stdout summary, only write files",
    )
    args = p.parse_args()

    paths = args.paths or ["sample_logs"]
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    started_utc = now_utc()
    summary, inventory = aggregate_paths(paths, recursive=args.recursive)
    elapsed = time.perf_counter() - started

    summary_dict = summary.as_dict()
    summary_dict["elapsed_sec"] = round(elapsed, 4)
    summary_dict["lines_per_sec"] = round(summary.total / elapsed, 2) if elapsed > 0 else 0.0
    summary_dict["file_inventory"] = inventory
    summary_dict["run_ts_utc"] = started_utc.isoformat()
    summary_dict["paths"] = list(paths)
    summary_dict["recursive"] = bool(args.recursive)

    # Persist snapshots
    last_path = Path(args.output) if args.output else state_dir / "last-summary.json"
    last_path.parent.mkdir(parents=True, exist_ok=True)
    last_path.write_text(json.dumps(summary_dict, indent=2, default=str))

    snap_name = f"summary-{started_utc.strftime('%Y-%m-%dT%H-%M-%S')}Z.json"
    snap_path = state_dir / snap_name
    snap_path.write_text(json.dumps(summary_dict, indent=2, default=str))

    if not args.quiet:
        print(json.dumps(summary_dict, indent=2, default=str))

    # Non-zero exit only if parser fundamentally failed
    return 0 if summary.parsed > 0 or summary.total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())