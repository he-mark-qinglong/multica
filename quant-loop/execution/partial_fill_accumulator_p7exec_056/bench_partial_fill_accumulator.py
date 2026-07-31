"""bench_partial_fill_accumulator — P7-EXEC-056 latency benchmark.

Measures ``on_fill`` end-to-end latency (one INSERT event + one UPSERT
state, plus the pure fold) on a warm journal. The MAP-P7 latency
budget for hot-path components is < 250us per call (median).

Reports median, p95, p99, max across N fills for a single coid
(single-threaded hot path, matches the connector's contract per
``partial_fill_accumulator.py``'s class docstring).

Run::

    cd ~/multica/quant-loop/execution/partial_fill_accumulator_p7exec_056
    python3 bench_partial_fill_accumulator.py --n 5000 --output evidence/bench_partial_fill_accumulator.json

Exit code 0 on success; the bench report is JSON-serialised to
``--output`` (default ``evidence/bench_partial_fill_accumulator.json``).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from partial_fill_accumulator import (  # noqa: E402
    Accumulator,
    FillEvent,
    PartialFillJournal,
)


def _make_event(ts_ns: int, trade_id: str) -> FillEvent:
    return FillEvent(
        ts_ns=ts_ns,
        client_order_id="bench-coid",
        trade_id=trade_id,
        symbol="BTCUSDT",
        side="BUY",
        qty=0.001,
        price=50000.0 + (hash(trade_id) % 1000) * 0.01,
        liquidity="taker",
    )


def run_bench(n: int, warmup: int) -> dict:
    """Run ``n`` on_fill calls and return a latency report dict."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.sqlite"
        journal = PartialFillJournal(db_path)
        acc = Accumulator(journal)

        # Warmup: discard the first warmup measurements.
        for i in range(warmup):
            acc.on_fill(_make_event(_ts(), f"warm-{i}"))

        latencies_us: list[float] = []
        for i in range(n):
            t0 = time.perf_counter_ns()
            acc.on_fill(_make_event(_ts(), f"bench-{i}"))
            t1 = time.perf_counter_ns()
            latencies_us.append((t1 - t0) / 1000.0)  # ns → us

        # Snapshot a final state for the report.
        state = acc.snapshot("bench-coid")
        acc.close()

        latencies_us_sorted = sorted(latencies_us)
        return {
            "n": n,
            "warmup": warmup,
            "unit": "microseconds",
            "median": statistics.median(latencies_us),
            "p95": latencies_us_sorted[int(0.95 * n)],
            "p99": latencies_us_sorted[int(0.99 * n)],
            "max": max(latencies_us),
            "min": min(latencies_us),
            "final_fill_count": state.fill_count if state else None,
            "final_total_qty": state.total_qty if state else None,
            "budget_us": 250.0,
            "within_budget": statistics.median(latencies_us) < 250.0,
        }


def _ts() -> int:
    return time.time_ns()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--n", type=int, default=5000,
        help="Number of on_fill calls to measure (default: 5000).",
    )
    parser.add_argument(
        "--warmup", type=int, default=500,
        help="Number of warmup calls to discard (default: 500).",
    )
    parser.add_argument(
        "--output", type=Path,
        default=_HERE / "evidence" / "bench_partial_fill_accumulator.json",
        help="Output JSON path (default: evidence/bench_partial_fill_accumulator.json).",
    )
    args = parser.parse_args()

    report = run_bench(n=args.n, warmup=args.warmup)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print(json.dumps(report, indent=2))
    if not report["within_budget"]:
        print(
            f"WARN: median {report['median']:.1f}us exceeds 250us budget",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())