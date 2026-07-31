"""Latency benchmark for order_to_fill_linker.

Measures the median / p95 / p99 cost of:

  * ``Linker.on_fill_report`` (the hot path) on a warm journal.

The benchmark writes to a tempdir, runs N rounds × M iterations
each, and emits a JSON evidence file at
``evidence/bench_order_to_fill_linker.json``. It does NOT touch
``state/order_to_fill.sqlite`` — the workspace is left clean.

Run::

    python3 order_to_fill_linker_p7exec_055/bench_order_to_fill_linker.py

The hot-path target is < 250us per call (MAP-P7 default policy).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from order_to_fill_linker_p7exec_055 import (  # noqa: E402
    FillReport,
    Linker,
    OrderIntent,
    OrderToFillJournal,
)


def _ts() -> int:
    return time.time_ns()


def _percentile(sorted_values, p):
    """Linear-interpolated percentile over a pre-sorted list."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_values[0])
    idx = (n - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def main() -> int:
    N_FILLS = 2000      # total FillReports per round
    N_ROUNDS = 5        # rounds, each with a fresh journal
    WARMUP = 200        # warm-up fills discarded from stats

    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "bench_o2fl.sqlite"

    rounds_out = []
    all_samples_us = []
    try:
        for round_idx in range(N_ROUNDS):
            journal = OrderToFillJournal(db_path)
            linker = Linker(journal)
            linker.recover_pending()

            # Pre-register N_FILLS intents so the linker has a fully
            # warm cache.
            for i in range(N_FILLS):
                linker.register_intent(
                    OrderIntent(
                        client_order_id=f"bench-coid-{i}",
                        symbol="BTCUSDT",
                        side="BUY",
                        intended_qty=0.01,
                        intent_ts_ns=_ts(),
                    )
                )
                linker.bind_order_id(f"bench-coid-{i}", 100_000_000 + i)

            samples_us = []
            for i in range(N_FILLS):
                report = FillReport(
                    ts_ns=_ts(),
                    order_id=100_000_000 + i,
                    client_order_id=f"bench-coid-{i}",
                    trade_id=f"bench-t-{round_idx}-{i}",
                    symbol="BTCUSDT",
                    side="BUY",
                    qty=0.01,
                    price=67123.4,
                    cum_filled_qty=0.01,
                    avg_fill_price=67123.4,
                    order_status="FILLED",
                )
                t0 = time.perf_counter_ns()
                linker.on_fill_report(report)
                t1 = time.perf_counter_ns()
                samples_us.append((t1 - t0) / 1000.0)

            samples_us.sort()
            measured = samples_us[WARMUP:]
            measured.sort()
            median_us = statistics.median(measured)
            p95_us = _percentile(measured, 95)
            p99_us = _percentile(measured, 99)
            mean_us = statistics.mean(measured)
            rounds_out.append({
                "round": round_idx,
                "n_samples": len(measured),
                "median_us": round(median_us, 3),
                "p95_us": round(p95_us, 3),
                "p99_us": round(p99_us, 3),
                "mean_us": round(mean_us, 3),
                "max_us": round(max(measured), 3),
            })
            all_samples_us.extend(measured)
            linker.close()

        all_samples_us.sort()
        agg_median = statistics.median(all_samples_us)
        agg_p95 = _percentile(all_samples_us, 95)
        agg_p99 = _percentile(all_samples_us, 99)
        agg_mean = statistics.mean(all_samples_us)
        agg_max = max(all_samples_us)

        evidence = {
            "bench_name": "order_to_fill_linker_p7exec_055",
            "host": "darwin",
            "python": sys.version.split()[0],
            "config": {
                "n_fills_per_round": N_FILLS,
                "n_rounds": N_ROUNDS,
                "warmup_discarded": WARMUP,
                "path_kind": "warm_journal_with_intents_prebound",
            },
            "rounds": rounds_out,
            "aggregate": {
                "total_samples": len(all_samples_us),
                "median_us": round(agg_median, 3),
                "p95_us": round(agg_p95, 3),
                "p99_us": round(agg_p99, 3),
                "mean_us": round(agg_mean, 3),
                "max_us": round(agg_max, 3),
                "budget_us": 250,
                "within_budget_p99": agg_p99 < 250,
            },
        }
        out_path = Path(__file__).parent / "evidence" / "bench_order_to_fill_linker.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(evidence, indent=2))
        print(f"BENCH OK — median={agg_median:.1f}us p95={agg_p95:.1f}us "
              f"p99={agg_p99:.1f}us; wrote {out_path}")
        print(f"  budget=250us; within_budget_p99={agg_p99 < 250}")
        return 0
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())