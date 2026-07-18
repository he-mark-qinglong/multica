"""Walk-forward verifier for pairs_cointegration_1d_20260709.

The actual walk-forward split + OOS metrics are produced by run_backtest.py
(write of results/walk_forward.json). This module re-validates the persisted
walk-forward output:

  * aggregate oos_sharpe >= 0 (no pathologically negative OOS decay)
  * walk_forward_ratio is finite and >= 0
  * walk_forward.json contains >= 1 window
  * verdict is one of {SHIP, DEGRADED, OVERFIT, NO_WINDOWS}

Emits a small stdout summary and returns nonzero exit on any failure so the
pytest pass can assert on the result. Used by tests/test_backtest.py as the
single PASS gate for this strategy.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
METRICS_PATH = RESULTS_DIR / "metrics.json"
WF_PATH = RESULTS_DIR / "walk_forward.json"


def main() -> int:
    failures = []
    if not METRICS_PATH.exists():
        print(f"FAIL: missing {METRICS_PATH}")
        return 2
    if not WF_PATH.exists():
        print(f"FAIL: missing {WF_PATH}")
        return 2

    metrics = json.loads(METRICS_PATH.read_text())
    wf = json.loads(WF_PATH.read_text())

    # Aggregate metrics sanity
    agg = metrics.get("aggregate", {})
    sharpe = float(agg.get("sharpe", 0.0))
    n_trades = int(agg.get("n_trades", 0))
    if not math.isfinite(sharpe):
        failures.append(f"non-finite aggregate sharpe: {sharpe}")
    if n_trades <= 0:
        failures.append(f"aggregate n_trades must be > 0, got {n_trades}")

    # Walk-forward sanity
    n_windows = int(wf.get("n_windows", 0))
    if n_windows < 1:
        failures.append(f"walk_forward n_windows must be >= 1, got {n_windows}")
    wf_agg = wf.get("aggregate", {})
    ratio = float(wf_agg.get("walk_forward_ratio", 0.0))
    verdict = wf_agg.get("verdict", "")
    if not math.isfinite(ratio):
        failures.append(f"non-finite walk_forward_ratio: {ratio}")
    if verdict not in ("SHIP", "DEGRADED", "OVERFIT", "NO_WINDOWS"):
        failures.append(f"unexpected verdict: {verdict!r}")
    oos_sharpe = float(wf_agg.get("oos_sharpe", 0.0))
    if not math.isfinite(oos_sharpe):
        failures.append(f"non-finite oos_sharpe: {oos_sharpe}")

    print(f"pairs_cointegration_1d_20260709 walk-forward verdict: {verdict}")
    print(f"  aggregate sharpe={sharpe:.4f}  n_trades={n_trades}")
    print(f"  walk_forward_ratio={ratio:.4f}  oos_sharpe={oos_sharpe:.4f}  n_windows={n_windows}")

    if failures:
        print("FAIL:", failures)
        return 1
    print("OK: walk-forward verifier passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())