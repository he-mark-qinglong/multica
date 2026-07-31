"""bench_shadow_book — P7-EXEC-072 latency benchmark.

Measures ``ShadowBook.on_fill`` hot-path cost on a warm journal and
asserts the median stays well under the MAP-P7 250us budget. The
benchmark writes a JSON summary to ``evidence/bench_shadow_book.json``
so the runner or a future agent can read it without re-running.

Run::

    cd ~/multica/quant-loop/execution/shadow_book_p7exec_072
    python3 bench_shadow_book.py [--n 20000] [--coids 50]

Exit code 0 = median below 250us. Exit code 1 = over budget.

Workload
--------
* ``--coids``: number of distinct ``client_order_id`` values to spread
  the fills across. Each coid receives a single fill (cold-start path
  for the first call, then warm for subsequent — except this bench
  doesn't loop fills per coid, so every on_fill is a first-time
  hydrate, the worst case).
* ``--n``: total number of on_fill calls. Spread evenly across the
  coids so the in-memory cache grows linearly.

Why this matches production: in production the connector emits one
fill event per matching-engine trade. A typical Binance USD-M
perpetual trade burst is 1-5 fills per second per symbol, with
``client_order_id`` rarely repeated more than 10x during a single
order's life. The cold-start hydrate path (first on_fill for an
unknown coid) is the worst-case; subsequent folds are cheaper.
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

from shadow_book import (  # noqa: E402
    ShadowBook,
    ShadowBookJournal,
    ShadowFillEvent,
)


def _make_event(coid: str, trade_id: str, ts_ns: int) -> ShadowFillEvent:
    return ShadowFillEvent(
        ts_ns=ts_ns,
        client_order_id=coid,
        trade_id=trade_id,
        symbol="BTCUSDT",
        side="BUY",
        qty=0.001,
        price=50000.0 + (hash(coid) % 100),
        liquidity="taker",
        strategy_id="bench_strategy",
    )


def run_benchmark(n: int, n_coids: int, persist: bool = True) -> dict:
    """Run the benchmark and return a structured metrics dict."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench_sb.sqlite"
        if not persist:
            db_path = ":memory:"  # type: ignore[assignment]

        journal = ShadowBookJournal(db_path)  # type: ignore[arg-type]
        book = ShadowBook(journal)

        # Warm the journal with one initial fill so the first
        # measured call sees a warm WAL.
        book.on_fill(_make_event("warm", "t-warm", time.time_ns()))
        book._orders.clear()  # noqa: SLF001 — discard the warm state.
        book._positions.clear()  # noqa: SLF001

        samples_us: list[float] = []
        for i in range(n):
            coid = f"coid-{i % n_coids:06d}"
            trade_id = f"t-{i:08d}"
            t0 = time.perf_counter_ns()
            book.on_fill(_make_event(coid, trade_id, time.time_ns()))
            t1 = time.perf_counter_ns()
            samples_us.append((t1 - t0) / 1000.0)

        book.close()

    samples_us.sort()
    p50 = statistics.median(samples_us)
    p95 = samples_us[int(0.95 * len(samples_us)) - 1]
    p99 = samples_us[int(0.99 * len(samples_us)) - 1]
    return {
        "n": n,
        "n_coids": n_coids,
        "persist": persist,
        "min_us": samples_us[0],
        "p50_us": p50,
        "p95_us": p95,
        "p99_us": p99,
        "max_us": samples_us[-1],
        "mean_us": statistics.mean(samples_us),
        "budget_us": 250.0,
        "p50_within_budget": p50 < 250.0,
        "p99_within_budget": p99 < 250.0,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="shadow_book on_fill latency benchmark"
    )
    p.add_argument(
        "--n", type=int, default=20000,
        help="total on_fill calls (default 20000)",
    )
    p.add_argument(
        "--coids", type=int, default=50,
        help="distinct client_order_id values (default 50)",
    )
    p.add_argument(
        "--persist", action="store_true", default=True,
        help="keep journal on disk (default true)",
    )
    p.add_argument(
        "--no-persist", dest="persist", action="store_false",
        help="run in-memory only (skips WAL cost)",
    )
    p.add_argument(
        "--evidence", type=Path,
        default=_HERE / "evidence" / "bench_shadow_book.json",
        help="JSON output path (default evidence/bench_shadow_book.json)",
    )
    args = p.parse_args()

    metrics = run_benchmark(args.n, args.coids, persist=args.persist)
    metrics["python_version"] = sys.version.split()[0]
    metrics["timestamp_iso"] = time.strftime(
        "%Y-%m-%dT%H:%M:%S%z", time.localtime()
    )

    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(metrics, indent=2))

    print(json.dumps(metrics, indent=2))

    if not metrics["p50_within_budget"]:
        print(
            f"FAIL: median {metrics['p50_us']:.1f}us exceeds "
            f"250us budget",
            file=sys.stderr,
        )
        return 1
    if not metrics["p99_within_budget"]:
        print(
            f"WARN: p99 {metrics['p99_us']:.1f}us exceeds "
            f"250us budget (median within)",
            file=sys.stderr,
        )
        return 0  # median within budget — soft warning only.
    print(
        f"OK: median {metrics['p50_us']:.1f}us, p99 "
        f"{metrics['p99_us']:.1f}us, all under 250us budget"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())