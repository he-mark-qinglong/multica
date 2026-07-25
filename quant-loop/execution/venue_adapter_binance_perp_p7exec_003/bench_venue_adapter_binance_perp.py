"""bench_venue_adapter_binance_perp — P7-EXEC-003 benchmark.

Measures:

1. ``validate_perp_intent`` pure-helper cost (no journal).
2. ``sign_binance_perp_request`` HMAC-SHA256 cost.
3. ``classify_binance_perp_rest_ack`` pure-helper cost.
4. ``parse_wss_userdata_message`` pure parser cost.
5. ``BinancePerpAdapter.on_request`` non-perp passthrough cost.
6. ``BinancePerpAdapter.on_request`` perp-tagged journal cost.
7. ``BinancePerpAdapter.on_fill`` terminal journal cost.
8. ``BinancePerpAdapter.apply_wss_event`` journal cost.
9. End-to-end ``ExecutionRunner.submit()`` with paper transport.

The hot-path budget is 250us per call for MAP-P7 components on
the runner's critical section.  The benchmark verifies medians
for all component-owned paths are <=250us.  The full runner
``submit()`` cycle carries shared overhead (canonical intent +
fill journal + transport + adapter hooks) and is measured against
an honest soft budget of 1500us.

Run as ``python3 bench_venue_adapter_binance_perp.py``.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from venue_adapter_binance_perp_p7exec_003 import (  # noqa: E402
    DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
    BinancePerpAdapter,
    BinancePerpPaperTransport,
    BinancePerpPaperTransportFillModel as FillModel,
    bootstrap_journal,
    classify_binance_perp_rest_ack,
    parse_wss_userdata_message,
    policy_fingerprint,
    register_with_runner,
    sign_binance_perp_request,
    validate_perp_intent,
)
from runner import (  # noqa: E402
    ExecutionRunner,
    OrderJournal,
    OutboundTransport,
)


EVIDENCE_DIR = Path(_HERE) / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)

BUDGET_US = 250.0
RUNNER_E2E_BUDGET_US = 1500.0
N_ESTIMATES = 5_000


def _percentile(samples, pct):
    if not samples:
        return 0.0
    samples = sorted(samples)
    rank = pct * (len(samples) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(samples) - 1)
    frac = rank - lo
    return samples[lo] * (1.0 - frac) + samples[hi] * frac


def _stats(samples_us):
    return {
        "n": len(samples_us),
        "median_us": round(_percentile(samples_us, 0.50), 3),
        "p95_us": round(_percentile(samples_us, 0.95), 3),
        "p99_us": round(_percentile(samples_us, 0.99), 3),
        "max_us": round(max(samples_us), 3) if samples_us else 0.0,
        "min_us": round(min(samples_us), 3) if samples_us else 0.0,
    }


def _perp(coid: str) -> dict:
    return {
        "client_order_id": coid,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.05,
        "price": 50000.0,
        "venue": "binance_usdt_futures",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
    }


def _non_perp(coid: str) -> dict:
    return {
        "client_order_id": coid,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.05,
        "price": 50000.0,
        "venue": "coinbase_advanced",
    }


def _ack(coid: str, oid: int) -> dict:
    return {
        "symbol": "BTCUSDT",
        "orderId": oid,
        "clientOrderId": coid,
        "price": "50000.0",
        "origQty": "0.05",
        "executedQty": "0.05",
        "cumQty": "0.05",
        "status": "FILLED",
        "timeInForce": "GTC",
        "type": "LIMIT",
        "side": "BUY",
        "avgPrice": "50000.0",
        "commission": "0.000025",
    }


def _bench_validate(n: int):
    req = _perp("bench")
    samples = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        validate_perp_intent(req, DEFAULT_BINANCE_PERP_ADAPTER_POLICY)
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_sign(n: int):
    params = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": "0.05",
        "price": "50000",
    }
    samples = []
    for i in range(n):
        t0 = time.perf_counter_ns()
        sign_binance_perp_request(
            params,
            api_secret="bench-secret-not-real",
            timestamp_ns=1_700_000_000_000_000_000 + i,
            recv_window_ms=5000,
        )
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_classify(n: int):
    ack = _ack("bench", 1)
    samples = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        classify_binance_perp_rest_ack(ack)
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_parse_wss(n: int):
    raw = json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "T": 1700000000000,
        "o": {
            "s": "BTCUSDT",
            "c": "bench",
            "S": "BUY",
            "i": 1,
            "X": "FILLED",
            "executedQty": "0.05",
            "ap": "50000.0",
            "n": "0.000025",
        },
    })
    samples = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        parse_wss_userdata_message(raw)
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_on_request_passthrough(n: int):
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    adapter = BinancePerpAdapter(journal=j)
    samples = []
    base = 1_700_000_000_000_000_000
    for i in range(n):
        t0 = time.perf_counter_ns()
        adapter.on_request(_non_perp(f"non-{i}"), j, base + i)
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_on_request_perp(n: int):
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    adapter = BinancePerpAdapter(journal=j)
    samples = []
    base = 1_700_000_000_000_000_000
    for i in range(n):
        t0 = time.perf_counter_ns()
        adapter.on_request(_perp(f"req-{i}"), j, base + i)
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_on_fill_perp(n: int):
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    adapter = BinancePerpAdapter(journal=j)
    base = 1_700_000_000_000_000_000
    reqs = [_perp(f"fill-{i}") for i in range(n)]
    for i, req in enumerate(reqs):
        adapter.on_request(req, j, base + i)
    samples = []
    for i, req in enumerate(reqs):
        t0 = time.perf_counter_ns()
        adapter.on_fill(
            req, _ack(req["client_order_id"], 1000 + i),
            j, base + n + i,
        )
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_apply_wss(n: int):
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    adapter = BinancePerpAdapter(journal=j)
    base = 1_700_000_000_000_000_000
    for i in range(n):
        adapter.on_request(_perp(f"wss-{i}"), j, base + i)
    samples = []
    for i in range(n):
        parsed = {
            "kind": "ORDER_TRADE_UPDATE",
            "client_order_id": f"wss-{i}",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "status_raw": "FILLED",
            "filled_qty": 0.05,
            "avg_price": 50000.0,
            "commission": 0.000025,
            "venue_order_id": str(2000 + i),
        }
        t0 = time.perf_counter_ns()
        adapter.apply_wss_event(parsed, ts_ns=base + n + i)
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _bench_runner_e2e(n: int):
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    adapter = BinancePerpAdapter(journal=j)
    paper = BinancePerpPaperTransport()
    runner = ExecutionRunner(
        journal=j,
        transport=OutboundTransport(callable_send=paper),
    )
    register_with_runner(runner, adapter)
    samples = []
    for i in range(n):
        t0 = time.perf_counter_ns()
        runner.submit(_perp(f"e2e-{i}"))
        samples.append((time.perf_counter_ns() - t0) / 1_000.0)
    return _stats(samples)


def _run_bench() -> dict:
    summary = {
        "issue": "SMA-36190",
        "component": "venue_adapter_binance_perp",
        "budget_us": BUDGET_US,
        "runner_e2e_soft_budget_us": RUNNER_E2E_BUDGET_US,
        "n_estimates": N_ESTIMATES,
        "policy_fingerprint": policy_fingerprint(
            DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
        ),
        "hardware": platform.processor() or platform.machine(),
        "python": platform.python_version(),
    }
    phases = (
        ("pure_validate", _bench_validate),
        ("pure_sign", _bench_sign),
        ("pure_classify", _bench_classify),
        ("pure_parse_wss", _bench_parse_wss),
        ("on_request_passthrough", _bench_on_request_passthrough),
        ("on_request_perp", _bench_on_request_perp),
        ("on_fill_perp", _bench_on_fill_perp),
        ("apply_wss_event", _bench_apply_wss),
        ("runner_e2e", _bench_runner_e2e),
    )
    for name, fn in phases:
        print(f"benchmarking {name}...")
        stats = fn(N_ESTIMATES)
        summary[name] = stats
        print(
            f"  median={stats['median_us']:.3f}us "
            f"p95={stats['p95_us']:.3f}us p99={stats['p99_us']:.3f}us"
        )
    hot = (
        "pure_validate", "pure_sign", "pure_classify",
        "pure_parse_wss", "on_request_passthrough",
        "on_request_perp", "on_fill_perp", "apply_wss_event",
    )
    for name in hot:
        summary[f"{name}_within_budget"] = (
            summary[name]["median_us"] <= BUDGET_US
        )
    summary["runner_e2e_within_soft_budget"] = (
        summary["runner_e2e"]["median_us"] <= RUNNER_E2E_BUDGET_US
    )
    return summary


def _write_evidence(summary: dict) -> None:
    out = EVIDENCE_DIR / "bench.json"
    with open(out, "w") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
    print(f"bench evidence -> {out}")


def main() -> int:
    summary = _run_bench()
    _write_evidence(summary)
    gates = [
        value for key, value in summary.items()
        if key.endswith("_within_budget")
        or key.endswith("_within_soft_budget")
    ]
    if not gates or not all(gates):
        failing = [
            key for key, value in summary.items()
            if (key.endswith("_within_budget")
                or key.endswith("_within_soft_budget"))
            and not value
        ]
        print(f"FAIL: hot-path budget gate(s) violated: {failing}")
        return 1
    print(
        "PASS: all component hot paths <=250us median; "
        f"runner e2e <= {RUNNER_E2E_BUDGET_US:.0f}us median."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
