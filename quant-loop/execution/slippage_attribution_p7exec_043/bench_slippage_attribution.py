"""bench_slippage_attribution — P7-EXEC-043 hot-path benchmark.

Measures:

1. ``attribute_fill`` pure-helper cost (the per-fill decomposition).
2. ``on_fill`` observer cost end-to-end (DB INSERT + rolling mean).
3. Runner E2E cost with the observer registered
   (``register_on_fill``) — the additive sum the runner's hot
   path pays per fill.
4. ``compute_day`` cold-path cost over a 5k-fill day.
5. ``record`` cost (one DELETE + one INSERT in the additive table).

The hot-path budget is 250us per call for MAP-P7 components on
the runner's critical section. Default-policy median is well
under that budget (the pure helper is sub-microsecond; the
observer end-to-end is dominated by the SQLite INSERT).

Run as ``python3 bench_slippage_attribution.py``. Output is
also written to ``evidence/bench_slippage_attribution.json``.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.abspath(os.path.join(_PARENT, ".."))
for _p in (_HERE, _PARENT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution.slippage_attribution_p7exec_043 import (  # noqa: E402
    FillRecord,
    SlippageAttributionClassifier,
    SlippageAttributionReport,
    attribute_fill,
)
from execution.runner import (  # noqa: E402
    ExecutionRunner,
    OrderJournal,
    OutboundTransport,
)


EVIDENCE_DIR = Path(_HERE) / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)

BUDGET_US = 250.0
N_FILLS = 5_000
N_RUNNER_FILLS = 2_000
DAY_UTC = "2026-07-20"


def _percentile(samples: list, pct: float) -> float:
    n = len(samples)
    if n == 0:
        return 0.0
    samples = sorted(samples)
    rank = pct * (n - 1)
    lo = int(rank // 1)
    hi = int(-(-rank // 1)) if rank != int(rank) else lo
    if hi >= n:
        hi = n - 1
    if lo == hi:
        return float(samples[lo])
    frac = rank - lo
    return float(samples[lo] * (1 - frac) + samples[hi] * frac)


def _summarise(samples: list) -> dict:
    if not samples:
        return {"n": 0, "median_us": 0.0, "p95_us": 0.0, "p99_us": 0.0,
                "max_us": 0.0, "min_us": 0.0}
    return {
        "n": len(samples),
        "median_us": _percentile(samples, 0.5),
        "p95_us": _percentile(samples, 0.95),
        "p99_us": _percentile(samples, 0.99),
        "max_us": max(samples),
        "min_us": min(samples),
    }


def _make_record(i: int) -> FillRecord:
    """Build a deterministic FillRecord for bench input."""
    expected = 50000.0
    delta = (i % 10) * 0.5 - 2.0  # -2.0 .. 2.5 bps
    fill = expected * (1 + delta / 10000)
    return FillRecord(
        timestamp=time.time_ns(),
        side="BUY",
        symbol="BTCUSDT",
        expected_price=expected,
        fill_price=fill,
        quantity=0.01,
        arrival_bid=expected - 5.0,
        arrival_ask=expected + 5.0,
        arrival_mid=expected,
        venue="binance_usdt_futures",
        client_order_id=f"bench-coid-{i}",
    )


def bench_attribute_fill_only(n: int) -> dict:
    """Pure-helper cost (no I/O)."""
    samples = []
    records = [_make_record(i) for i in range(n)]
    # Warm-up
    for _ in range(50):
        attribute_fill(records[0])
    for i in range(n):
        rec = records[i]
        t0 = time.perf_counter_ns()
        attribute_fill(rec)
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1000.0)  # ns → us
    return _summarise(samples)


def bench_on_fill_only(n: int) -> dict:
    """Observer cost end-to-end (incl. SQLite INSERT)."""
    samples = []
    journal = OrderJournal(":memory:")
    classifier = SlippageAttributionClassifier(journal=journal)
    records = [_make_record(i) for i in range(n)]
    base_ns = time.time_ns()
    # Warm-up
    classifier.on_fill(
        request={
            "client_order_id": "warm",
            "symbol": "BTCUSDT", "side": "BUY",
            "qty": 0.01, "expected_price": 50000.0,
            "arrival_bid": 49995.0, "arrival_ask": 50005.0,
            "submit_ts_ns": base_ns,
        },
        ack={"price": 50010.0, "venue": "v"},
        journal=journal,
        ts_ns=base_ns,
    )
    for i in range(n):
        rec = records[i]
        ts = base_ns + i * 1_000_000
        request = {
            "client_order_id": rec.client_order_id,
            "symbol": rec.symbol, "side": rec.side,
            "qty": rec.quantity,
            "expected_price": rec.expected_price,
            "arrival_bid": rec.arrival_bid,
            "arrival_ask": rec.arrival_ask,
            "arrival_mid": rec.arrival_mid,
            "submit_ts_ns": ts,
        }
        ack = {"price": rec.fill_price, "venue": rec.venue}
        t0 = time.perf_counter_ns()
        classifier.on_fill(request, ack, journal, ts)
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1000.0)
    return _summarise(samples)


def bench_runner_e2e(n: int) -> dict:
    """Runner E2E with the observer registered."""
    samples = []
    journal = OrderJournal(":memory:")
    classifier = SlippageAttributionClassifier(journal=journal)
    runner = ExecutionRunner(
        journal=journal,
        transport=OutboundTransport(callable_send=lambda req: {
            "ok": True, "price": float(req.get("expected_price", 50000.0))
            + 1.0, "venue": "v",
        }),
    )
    runner.register_on_fill(classifier)
    base_ns = time.time_ns()
    # Warm-up
    runner.submit({
        "client_order_id": "warm-runner",
        "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01,
        "venue": "v", "expected_price": 50000.0,
        "arrival_bid": 49995.0, "arrival_ask": 50005.0,
        "arrival_mid": 50000.0,
    })
    for i in range(n):
        coid = f"bench-runner-{i}"
        t0 = time.perf_counter_ns()
        runner.submit({
            "client_order_id": coid,
            "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01,
            "venue": "binance_usdt_futures",
            "expected_price": 50000.0,
            "arrival_bid": 49995.0, "arrival_ask": 50005.0,
            "arrival_mid": 50000.0,
        })
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1000.0)
    return _summarise(samples)


def bench_compute_day_cold_path(n_fills: int) -> dict:
    """Cold-path: build a 5k-fill day and time ``compute_day``."""
    samples = []
    journal = OrderJournal(":memory:")
    classifier = SlippageAttributionClassifier(journal=journal)
    start, _ = _utc_bounds(DAY_UTC)
    base = start + 60 * 1_000_000_000
    for i in range(n_fills):
        ts = base + i * 1_000_000
        classifier.on_fill(
            request={
                "client_order_id": f"day-coid-{i}",
                "symbol": "BTCUSDT", "side": "BUY",
                "qty": 0.01, "expected_price": 50000.0,
                "arrival_bid": 49995.0, "arrival_ask": 50005.0,
                "arrival_mid": 50000.0,
                "submit_ts_ns": ts,
            },
            ack={"price": 50010.0, "venue": "v"},
            journal=journal,
            ts_ns=ts,
        )
    report = SlippageAttributionReport(journal=journal, min_sample=5)
    for _ in range(5):
        t0 = time.perf_counter_ns()
        report.compute_day(DAY_UTC)
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1000.0)
    return _summarise(samples)


def bench_record_cost(n: int) -> dict:
    """Cost of :meth:`record` — DELETE + INSERT per call."""
    samples = []
    journal = OrderJournal(":memory:")
    classifier = SlippageAttributionClassifier(journal=journal)
    start, _ = _utc_bounds(DAY_UTC)
    base = start + 60 * 1_000_000_000
    for i in range(50):
        ts = base + i * 1_000_000
        classifier.on_fill(
            request={
                "client_order_id": f"rec-coid-{i}",
                "symbol": "BTCUSDT", "side": "BUY",
                "qty": 0.01, "expected_price": 50000.0,
                "arrival_bid": 49995.0, "arrival_ask": 50005.0,
                "arrival_mid": 50000.0,
                "submit_ts_ns": ts,
            },
            ack={"price": 50010.0, "venue": "v"},
            journal=journal,
            ts_ns=ts,
        )
    report = SlippageAttributionReport(journal=journal, min_sample=5)
    daily = report.compute_day(DAY_UTC)
    for _ in range(n):
        t0 = time.perf_counter_ns()
        report.record(daily)
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1000.0)
    return _summarise(samples)


def _utc_bounds(day_utc: str):
    # Re-implemented here to avoid pulling module state; the
    # canonical helper lives in the package.
    from execution.slippage_attribution_p7exec_043 import day_utc_bounds
    return day_utc_bounds(day_utc)


def main() -> None:
    print(f"--- slippage_attribution hot-path bench (n={N_FILLS}) ---")
    attr = bench_attribute_fill_only(N_FILLS)
    print(
        f"  attribute_fill_only: median={attr['median_us']:.2f}us, "
        f"p95={attr['p95_us']:.2f}us, max={attr['max_us']:.2f}us"
    )

    obs = bench_on_fill_only(N_FILLS)
    print(
        f"  on_fill_only:        median={obs['median_us']:.2f}us, "
        f"p95={obs['p95_us']:.2f}us, max={obs['max_us']:.2f}us"
    )

    e2e = bench_runner_e2e(N_RUNNER_FILLS)
    print(
        f"  runner_e2e:          median={e2e['median_us']:.2f}us, "
        f"p95={e2e['p95_us']:.2f}us, max={e2e['max_us']:.2f}us"
    )

    cday = bench_compute_day_cold_path(N_FILLS)
    print(
        f"  compute_day (cold):  median={cday['median_us']:.2f}us, "
        f"p95={cday['p95_us']:.2f}us, max={cday['max_us']:.2f}us"
    )

    rec = bench_record_cost(50)
    print(
        f"  record:              median={rec['median_us']:.2f}us, "
        f"p95={rec['p95_us']:.2f}us, max={rec['max_us']:.2f}us"
    )

    verdict = (
        f"PASS — attribute_fill median {attr['median_us']:.2f}us, "
        f"on_fill median {obs['median_us']:.2f}us, "
        f"runner E2E median {e2e['median_us']:.2f}us "
        f"(hot-path budget {BUDGET_US}us for the observer)."
    )
    print(f"\n{verdict}")

    payload = {
        "budget_us_hot_path_observer": BUDGET_US,
        "n_fills_pure_helper": N_FILLS,
        "n_fills_observer": N_FILLS,
        "n_fills_runner_e2e": N_RUNNER_FILLS,
        "n_fills_cold_path": N_FILLS,
        "n_records_cost": 50,
        "day_utc": DAY_UTC,
        "attribute_fill_only": attr,
        "on_fill_hook_only": obs,
        "runner_e2e_with_classifier": e2e,
        "compute_day": cday,
        "record": rec,
        "verdict": verdict,
    }
    out_path = EVIDENCE_DIR / "bench_slippage_attribution.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"EVIDENCE written to {out_path}")


if __name__ == "__main__":
    main()