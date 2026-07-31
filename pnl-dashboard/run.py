#!/usr/bin/env python3
"""pnl-dashboard — concrete entry point for SMA-35770-080 (Monitor #80).

Usage:
    python3 run.py [--root PATH] [--output PATH] [--state-dir PATH]
                   [--equity-max-points N] [--top-n N] [--quiet]

Defaults:
    --root             /Users/mark/multica/quant-loop/strategies
    --state-dir        /Users/mark/multica/pnl-dashboard/state
    --output           <state-dir>/last-snapshot.json
    --equity-max-points 5000
    --top-n            10

Outputs:
    <output>                            — freshest snapshot (overwritten).
    <state-dir>/snapshot-<ts>.json      — timestamped run record for history.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Local import — works whether invoked as `python3 run.py` or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pnl_dashboard as pd  # noqa: E402


DEFAULT_ROOT = "/Users/mark/multica/quant-loop/strategies"
DEFAULT_STATE_DIR = "/Users/mark/multica/pnl-dashboard/state"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def main() -> int:
    p = argparse.ArgumentParser(description="P&L dashboard (SMA-35770-080)")
    p.add_argument(
        "--root", default=DEFAULT_ROOT,
        help="Strategies root containing per-strategy results/ folders. "
             f"Default: {DEFAULT_ROOT}",
    )
    p.add_argument(
        "--output", "-o", default=None,
        help="Output path for the snapshot "
             "(default: <state-dir>/last-snapshot.json)",
    )
    p.add_argument(
        "--state-dir", default=DEFAULT_STATE_DIR,
        help=f"Directory for timestamped snapshots. "
             f"Default: {DEFAULT_STATE_DIR}",
    )
    p.add_argument(
        "--equity-max-points", type=int, default=500,
        help="Cap on equity-curve points (default: 500).",
    )
    p.add_argument(
        "--top-n", type=int, default=10,
        help="How many top winners and top losers to retain (default: 10).",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress stdout summary, only write files.",
    )
    args = p.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    started_utc = now_utc()
    snap = pd.build_snapshot(
        args.root,
        equity_max_points=args.equity_max_points,
        top_n=args.top_n,
    )
    elapsed = time.perf_counter() - started

    snapshot = snap.as_dict()
    snapshot["elapsed_sec"] = round(elapsed, 4)
    snapshot["run_ts_utc"] = started_utc.isoformat()

    last_path = Path(args.output) if args.output else state_dir / "last-snapshot.json"
    last_path.parent.mkdir(parents=True, exist_ok=True)
    last_path.write_text(json.dumps(snapshot, indent=2))

    snap_name = f"snapshot-{started_utc.strftime('%Y-%m-%dT%H-%M-%S')}Z.json"
    snap_path = state_dir / snap_name
    snap_path.write_text(json.dumps(snapshot, indent=2))

    if not args.quiet:
        print(json.dumps(snapshot, indent=2))

    # Non-zero exit only if the root was present but we got zero trades.
    root_present = Path(args.root).is_dir()
    if root_present and snap.n_trades == 0:
        return 2  # root exists, no usable trades — distinguish from success
    return 0


if __name__ == "__main__":
    sys.exit(main())
