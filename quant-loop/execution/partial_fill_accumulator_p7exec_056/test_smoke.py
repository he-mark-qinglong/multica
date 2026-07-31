"""test_smoke — P7-EXEC-056 integration smoke.

End-to-end smoke against a synthetic connector feed that mimics the
live-paper connector pattern in
``SPEC_live_paper_connector_binance_usdm.md §4.1``: a stream of fill
events for several ``client_order_id`` values, interleaved with
occasional terminal-status updates. Verifies the full chain:

* ``PartialFillJournal`` survives process restart (close + reopen).
* Multiple coids are independent in the same journal.
* Cold-start ``replay`` rebuilds every tracked order.
* Per-coid aggregation matches the connector's expected VWAP.

This is the integration smoke the deliverable spec calls for; the unit
tests in ``test_partial_fill_accumulator.py`` cover the per-method
behaviour. Run::

    cd ~/multica/quant-loop/execution/partial_fill_accumulator_p7exec_056
    python3 test_smoke.py

Exit code 0 = pass.
"""
from __future__ import annotations

import json
import os
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


def _ts_seq() -> int:
    """Monotonically increasing timestamp for fills in a single run."""
    return time.time_ns()


def _make_feed():
    """Yield (FillEvent_or_None, finalize_dict_or_None) tuples."""
    # Order 1: BTC long, 3 fills, FILLED.
    # Expected: total=0.030, notional=(0.01*50000)+(0.005*50100)+(0.015*50050)
    #                    = 500 + 250.5 + 750.75 = 1501.25
    # VWAP = 1501.25 / 0.030 = 50041.666...
    yield FillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-1",
        trade_id="t-1",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.010,
        price=50000.0,
        liquidity="taker",
    ), None
    yield FillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-1",
        trade_id="t-2",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.005,
        price=50100.0,
        liquidity="taker",
    ), None
    yield FillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-1",
        trade_id="t-3",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.015,
        price=50050.0,
        liquidity="maker",
    ), None

    # Order 2: ETH short, 2 fills + CANCELED.
    # Expected: total=2.5, notional=(1.0*3000)+(1.5*2990)=3000+4485=7485
    # VWAP=7485/2.5=2994
    yield FillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-eth-1",
        trade_id="t-4",
        symbol="ETHUSDT",
        side="SELL",
        qty=1.0,
        price=3000.0,
        liquidity="taker",
    ), None
    yield FillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-eth-1",
        trade_id="t-5",
        symbol="ETHUSDT",
        side="SELL",
        qty=1.5,
        price=2990.0,
        liquidity="maker",
    ), None

    # Terminal updates.
    yield None, {"coid": "smoke-coid-btc-1", "status": "FILLED"}
    yield None, {"coid": "smoke-coid-eth-1", "status": "CANCELED"}


def run_smoke(persist: bool = True) -> dict:
    """Drive the smoke and return a structured report dict.

    ``persist=True`` keeps the journal on disk (matches production);
    ``persist=False`` runs in-memory only.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke.sqlite"
        if not persist:
            db_path = ":memory:"  # type: ignore[assignment]

        journal = PartialFillJournal(db_path)  # type: ignore[arg-type]
        acc = Accumulator(journal)

        # Drain the synthetic feed.
        for fill_event, finalize_dict in _make_feed():
            if fill_event is not None:
                acc.on_fill(fill_event)
            elif finalize_dict is not None:
                acc.finalize(
                    finalize_dict["coid"], finalize_dict["status"]
                )

        # Snapshot before close.
        s_btc = acc.snapshot("smoke-coid-btc-1")
        s_eth = acc.snapshot("smoke-coid-eth-1")
        acc.close()

        # Reopen and replay from the journal (simulates a process
        # restart where the in-memory cache is gone).
        journal2 = PartialFillJournal(db_path)  # type: ignore[arg-type]
        acc2 = Accumulator(journal2)
        for coid in ("smoke-coid-btc-1", "smoke-coid-eth-1"):
            replayed = acc2.replay(coid)
            assert replayed is not None, f"replay missing for {coid}"

        r_btc = acc2.snapshot("smoke-coid-btc-1")
        r_eth = acc2.snapshot("smoke-coid-eth-1")
        acc2.close()

        report = {
            "before_close": {
                "smoke-coid-btc-1": {
                    "total_qty": s_btc.total_qty if s_btc else None,
                    "avg_price": s_btc.avg_price if s_btc else None,
                    "notional_usd": s_btc.notional_usd if s_btc else None,
                    "fill_count": s_btc.fill_count if s_btc else None,
                    "terminal_status": (
                        s_btc.terminal_status if s_btc else None
                    ),
                },
                "smoke-coid-eth-1": {
                    "total_qty": s_eth.total_qty if s_eth else None,
                    "avg_price": s_eth.avg_price if s_eth else None,
                    "notional_usd": s_eth.notional_usd if s_eth else None,
                    "fill_count": s_eth.fill_count if s_eth else None,
                    "terminal_status": (
                        s_eth.terminal_status if s_eth else None
                    ),
                },
            },
            "after_replay": {
                "smoke-coid-btc-1": {
                    "total_qty": r_btc.total_qty if r_btc else None,
                    "avg_price": r_btc.avg_price if r_btc else None,
                    "notional_usd": r_btc.notional_usd if r_btc else None,
                    "fill_count": r_btc.fill_count if r_btc else None,
                    "terminal_status": (
                        r_btc.terminal_status if r_btc else None
                    ),
                },
                "smoke-coid-eth-1": {
                    "total_qty": r_eth.total_qty if r_eth else None,
                    "avg_price": r_eth.avg_price if r_eth else None,
                    "notional_usd": r_eth.notional_usd if r_eth else None,
                    "fill_count": r_eth.fill_count if r_eth else None,
                    "terminal_status": (
                        r_eth.terminal_status if r_eth else None
                    ),
                },
            },
        }
        return report


def main() -> int:
    report = run_smoke(persist=True)
    print(json.dumps(report, indent=2))

    failures: list[str] = []

    # Pre-close expectations.
    btc = report["before_close"]["smoke-coid-btc-1"]
    if abs(btc["total_qty"] - 0.030) > 1e-9:
        failures.append(f"btc total_qty {btc['total_qty']} != 0.030")
    if abs(btc["avg_price"] - 50041.666666666664) > 1e-6:
        failures.append(
            f"btc avg_price {btc['avg_price']} != 50041.6666..."
        )
    if abs(btc["notional_usd"] - 1501.25) > 1e-6:
        failures.append(f"btc notional_usd {btc['notional_usd']} != 1501.25")
    if btc["fill_count"] != 3:
        failures.append(f"btc fill_count {btc['fill_count']} != 3")
    if btc["terminal_status"] != "FILLED":
        failures.append(
            f"btc terminal_status {btc['terminal_status']} != FILLED"
        )

    eth = report["before_close"]["smoke-coid-eth-1"]
    if abs(eth["total_qty"] - 2.5) > 1e-9:
        failures.append(f"eth total_qty {eth['total_qty']} != 2.5")
    if abs(eth["avg_price"] - 2994.0) > 1e-6:
        failures.append(f"eth avg_price {eth['avg_price']} != 2994")
    if eth["fill_count"] != 2:
        failures.append(f"eth fill_count {eth['fill_count']} != 2")
    if eth["terminal_status"] != "CANCELED":
        failures.append(
            f"eth terminal_status {eth['terminal_status']} != CANCELED"
        )

    # Replay symmetry: replayed state must equal pre-close state.
    for coid in ("smoke-coid-btc-1", "smoke-coid-eth-1"):
        before = report["before_close"][coid]
        after = report["after_replay"][coid]
        for key in ("total_qty", "avg_price", "notional_usd",
                    "fill_count", "terminal_status"):
            if before[key] != after[key]:
                failures.append(
                    f"{coid}.{key}: before_close={before[key]} "
                    f"after_replay={after[key]}"
                )

    if failures:
        print("SMOKE FAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("SMOKE PASS: 5 fills across 2 coids + restart replay OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())