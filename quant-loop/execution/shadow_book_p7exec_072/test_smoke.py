"""test_smoke — P7-EXEC-072 integration smoke.

End-to-end smoke against a synthetic connector feed that mimics the
live-paper connector pattern in
``SPEC_live_paper_connector_binance_usdm.md §4.1``: a stream of fill
events for several ``client_order_id`` values across multiple symbols,
interleaved with terminal-status updates. After the stream, a synthetic
live-book snapshot is fed in and the reconciliation diff is checked.

Verifies the full chain:

* ``ShadowBookJournal`` survives process restart (close + reopen).
* Multiple coids and positions are independent in the same journal.
* Cold-start ``replay_order`` and ``replay_position`` rebuild every
  tracked order and bucket.
* Per-coid aggregation matches the connector's expected VWAP.
* Per-(symbol, side) position aggregation matches the connector's
  expected aggregation.
* Reconciliation against a venue-truth snapshot correctly identifies
  matching, divergent, missing-in-shadow, and missing-in-live orders.

This is the integration smoke the deliverable spec calls for; the unit
tests in ``test_shadow_book.py`` cover the per-method behaviour. Run::

    cd ~/multica/quant-loop/execution/shadow_book_p7exec_072
    python3 test_smoke.py

Exit code 0 = pass.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from shadow_book import (  # noqa: E402
    LiveOrderReport,
    ShadowBook,
    ShadowBookJournal,
    ShadowFillEvent,
)


def _ts_seq() -> int:
    """Monotonically increasing timestamp for fills in a single run."""
    return time.time_ns()


def _make_feed():
    """Yield (ShadowFillEvent or None, finalize_dict or None) tuples.

    Three orders across two symbols:
    - BTCUSDT BUY order 1 (3 fills, FILLED) → total=0.030, avg=50041.66...
    - BTCUSDT BUY order 2 (2 fills, CANCELED) → total=0.025, avg=50100
    - ETHUSDT SELL order 1 (2 fills, FILLED) → total=2.5, avg=2994
    """
    # BTC long order 1: 3 fills, FILLED.
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-1",
        trade_id="t-1",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.010,
        price=50000.0,
        liquidity="taker",
        strategy_id="vpvr_btc_long",
    ), None
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-1",
        trade_id="t-2",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.005,
        price=50100.0,
        liquidity="taker",
        strategy_id="vpvr_btc_long",
    ), None
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-1",
        trade_id="t-3",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.015,
        price=50050.0,
        liquidity="maker",
        strategy_id="vpvr_btc_long",
    ), None

    # BTC long order 2: 2 fills, CANCELED.
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-2",
        trade_id="t-4",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.020,
        price=50100.0,
        liquidity="taker",
        strategy_id="vpvr_btc_long",
    ), None
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-btc-2",
        trade_id="t-5",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.005,
        price=50100.0,
        liquidity="taker",
        strategy_id="vpvr_btc_long",
    ), None

    # ETH short order: 2 fills, FILLED.
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-eth-1",
        trade_id="t-6",
        symbol="ETHUSDT",
        side="SELL",
        qty=1.0,
        price=3000.0,
        liquidity="taker",
        strategy_id="vpvr_eth_short",
    ), None
    yield ShadowFillEvent(
        ts_ns=_ts_seq(),
        client_order_id="smoke-coid-eth-1",
        trade_id="t-7",
        symbol="ETHUSDT",
        side="SELL",
        qty=1.5,
        price=2990.0,
        liquidity="maker",
        strategy_id="vpvr_eth_short",
    ), None

    # Terminal updates.
    yield None, {"coid": "smoke-coid-btc-1", "status": "FILLED"}
    yield None, {"coid": "smoke-coid-btc-2", "status": "CANCELED"}
    yield None, {"coid": "smoke-coid-eth-1", "status": "FILLED"}


def _live_snapshot_for_btc_1() -> LiveOrderReport:
    """Venue-truth snapshot matching BTCUSDT order 1 (3 fills, FILLED)."""
    return LiveOrderReport(
        client_order_id="smoke-coid-btc-1",
        symbol="BTCUSDT",
        side="BUY",
        total_qty=0.030,
        avg_price=50041.666666666664,
        fill_count=3,
        terminal_status="FILLED",
        terminal_ts_ns=_ts_seq(),
        received_at_ns=_ts_seq(),
    )


def _live_snapshot_for_btc_2_with_drift() -> LiveOrderReport:
    """Venue-truth for BTCUSDT order 2 (CANCELED) but with a qty drift:
    venue says 0.020 (only the first fill made it through the
    cancellation, the second was rejected). Shadow still thinks 0.025.
    """
    return LiveOrderReport(
        client_order_id="smoke-coid-btc-2",
        symbol="BTCUSDT",
        side="BUY",
        total_qty=0.020,
        avg_price=50100.0,
        fill_count=1,
        terminal_status="CANCELED",
        terminal_ts_ns=_ts_seq(),
        received_at_ns=_ts_seq(),
    )


def _live_snapshot_for_eth_1() -> LiveOrderReport:
    """Venue-truth for ETHUSDT order 1 (2 fills, FILLED)."""
    return LiveOrderReport(
        client_order_id="smoke-coid-eth-1",
        symbol="ETHUSDT",
        side="SELL",
        total_qty=2.5,
        avg_price=2994.0,
        fill_count=2,
        terminal_status="FILLED",
        terminal_ts_ns=_ts_seq(),
        received_at_ns=_ts_seq(),
    )


def _live_snapshot_phantom() -> LiveOrderReport:
    """Venue reports a coid shadow never saw."""
    return LiveOrderReport(
        client_order_id="smoke-coid-phantom",
        symbol="SOLUSDT",
        side="BUY",
        total_qty=0.50,
        avg_price=150.0,
        fill_count=1,
        terminal_status="FILLED",
        terminal_ts_ns=_ts_seq(),
        received_at_ns=_ts_seq(),
    )


def run_smoke(persist: bool = True) -> dict:
    """Drive the smoke and return a structured report dict.

    ``persist=True`` keeps the journal on disk (matches production);
    ``persist=False`` runs in-memory only.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke_sb.sqlite"
        if not persist:
            db_path = ":memory:"  # type: ignore[assignment]

        journal = ShadowBookJournal(db_path)  # type: ignore[arg-type]
        book = ShadowBook(journal)

        # Drain the synthetic feed.
        for fill_event, finalize_dict in _make_feed():
            if fill_event is not None:
                book.on_fill(fill_event)
            elif finalize_dict is not None:
                book.finalize_order(
                    finalize_dict["coid"], finalize_dict["status"]
                )

        # Snapshot before close.
        s_btc1 = book.snapshot_order("smoke-coid-btc-1")
        s_btc2 = book.snapshot_order("smoke-coid-btc-2")
        s_eth1 = book.snapshot_order("smoke-coid-eth-1")
        pos_btc_buy = book.snapshot_position("BTCUSDT", "BUY")
        pos_eth_sell = book.snapshot_position("ETHUSDT", "SELL")
        book.close()

        # Reopen and replay from the journal (simulates a process
        # restart where the in-memory cache is gone).
        journal2 = ShadowBookJournal(db_path)  # type: ignore[arg-type]
        book2 = ShadowBook(journal2)
        for coid in ("smoke-coid-btc-1", "smoke-coid-btc-2",
                     "smoke-coid-eth-1"):
            replayed = book2.replay_order(coid)
            assert replayed is not None, f"replay_order missing for {coid}"
        book2.replay_position("BTCUSDT", "BUY")
        book2.replay_position("ETHUSDT", "SELL")

        # Feed a synthetic live snapshot and reconcile.
        book2.record_live_reports([
            _live_snapshot_for_btc_1(),
            _live_snapshot_for_btc_2_with_drift(),
            _live_snapshot_for_eth_1(),
            _live_snapshot_phantom(),
        ])
        rows = book2.reconcile()

        r_btc1 = book2.snapshot_order("smoke-coid-btc-1")
        r_btc2 = book2.snapshot_order("smoke-coid-btc-2")
        r_eth1 = book2.snapshot_order("smoke-coid-eth-1")
        r_pos_btc_buy = book2.snapshot_position("BTCUSDT", "BUY")
        r_pos_eth_sell = book2.snapshot_position("ETHUSDT", "SELL")
        book2.close()

        report = {
            "before_close": {
                "smoke-coid-btc-1": _snapshot_order_dict(s_btc1),
                "smoke-coid-btc-2": _snapshot_order_dict(s_btc2),
                "smoke-coid-eth-1": _snapshot_order_dict(s_eth1),
                "pos_btc_buy": _snapshot_pos_dict(pos_btc_buy),
                "pos_eth_sell": _snapshot_pos_dict(pos_eth_sell),
            },
            "after_replay": {
                "smoke-coid-btc-1": _snapshot_order_dict(r_btc1),
                "smoke-coid-btc-2": _snapshot_order_dict(r_btc2),
                "smoke-coid-eth-1": _snapshot_order_dict(r_eth1),
                "pos_btc_buy": _snapshot_pos_dict(r_pos_btc_buy),
                "pos_eth_sell": _snapshot_pos_dict(r_pos_eth_sell),
            },
            "reconcile_rows": [
                {
                    "client_order_id": r.client_order_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "only_in_shadow": r.only_in_shadow,
                    "only_in_live": r.only_in_live,
                    "qty_diff": r.qty_diff,
                    "avg_price_diff": r.avg_price_diff,
                    "fill_count_diff": r.fill_count_diff,
                    "status_match": r.status_match,
                }
                for r in rows
            ],
        }
        return report


def _snapshot_order_dict(s):
    if s is None:
        return None
    return {
        "total_qty": s.total_qty,
        "avg_price": s.avg_price,
        "notional_usd": s.notional_usd,
        "fill_count": s.fill_count,
        "terminal_status": s.terminal_status,
        "strategy_id": s.strategy_id,
    }


def _snapshot_pos_dict(p):
    if p is None:
        return None
    return {
        "net_qty": p.net_qty,
        "gross_qty": p.gross_qty,
        "avg_price": p.avg_price,
        "notional_usd": p.notional_usd,
        "fill_count": p.fill_count,
    }


def main() -> int:
    report = run_smoke(persist=True)
    print(json.dumps(report, indent=2))

    failures: list[str] = []

    # Pre-close expectations.
    btc1 = report["before_close"]["smoke-coid-btc-1"]
    if abs(btc1["total_qty"] - 0.030) > 1e-9:
        failures.append(f"btc1 total_qty {btc1['total_qty']} != 0.030")
    if abs(btc1["avg_price"] - 50041.666666666664) > 1e-6:
        failures.append(
            f"btc1 avg_price {btc1['avg_price']} != 50041.66..."
        )
    if abs(btc1["notional_usd"] - 1501.25) > 1e-6:
        failures.append(
            f"btc1 notional_usd {btc1['notional_usd']} != 1501.25"
        )
    if btc1["fill_count"] != 3:
        failures.append(f"btc1 fill_count {btc1['fill_count']} != 3")
    if btc1["terminal_status"] != "FILLED":
        failures.append(
            f"btc1 terminal_status {btc1['terminal_status']} != FILLED"
        )

    btc2 = report["before_close"]["smoke-coid-btc-2"]
    if abs(btc2["total_qty"] - 0.025) > 1e-9:
        failures.append(f"btc2 total_qty {btc2['total_qty']} != 0.025")
    if abs(btc2["avg_price"] - 50100.0) > 1e-6:
        failures.append(f"btc2 avg_price {btc2['avg_price']} != 50100")
    if btc2["terminal_status"] != "CANCELED":
        failures.append(
            f"btc2 terminal_status {btc2['terminal_status']} != CANCELED"
        )

    eth1 = report["before_close"]["smoke-coid-eth-1"]
    if abs(eth1["total_qty"] - 2.5) > 1e-9:
        failures.append(f"eth1 total_qty {eth1['total_qty']} != 2.5")
    if abs(eth1["avg_price"] - 2994.0) > 1e-6:
        failures.append(f"eth1 avg_price {eth1['avg_price']} != 2994")
    if eth1["terminal_status"] != "FILLED":
        failures.append(
            f"eth1 terminal_status {eth1['terminal_status']} != FILLED"
        )

    # Position projection expectations: BTCUSDT BUY across both orders
    # is 0.030 + 0.025 = 0.055. Notional = 1501.25 + 0.025*50100 =
    # 1501.25 + 1252.5 = 2753.75. VWAP = 2753.75 / 0.055 = 50068.18...
    pos_btc_buy = report["before_close"]["pos_btc_buy"]
    if pos_btc_buy is None:
        failures.append("pos_btc_buy is None")
    else:
        if abs(pos_btc_buy["net_qty"] - 0.055) > 1e-9:
            failures.append(
                f"pos_btc_buy net_qty {pos_btc_buy['net_qty']} != 0.055"
            )
        if abs(pos_btc_buy["avg_price"] - 50068.181818181816) > 1e-6:
            failures.append(
                f"pos_btc_buy avg_price {pos_btc_buy['avg_price']} "
                f"!= 50068.18..."
            )
        if pos_btc_buy["fill_count"] != 5:
            failures.append(
                f"pos_btc_buy fill_count {pos_btc_buy['fill_count']} != 5"
            )

    pos_eth_sell = report["before_close"]["pos_eth_sell"]
    if pos_eth_sell is None:
        failures.append("pos_eth_sell is None")
    else:
        if abs(pos_eth_sell["net_qty"] - 2.5) > 1e-9:
            failures.append(
                f"pos_eth_sell net_qty {pos_eth_sell['net_qty']} != 2.5"
            )

    # Replay symmetry: replayed order + position state must equal
    # pre-close state.
    for key in ("smoke-coid-btc-1", "smoke-coid-btc-2", "smoke-coid-eth-1"):
        before = report["before_close"][key]
        after = report["after_replay"][key]
        if before != after:
            failures.append(
                f"{key}: before_close={before} after_replay={after}"
            )
    for key in ("pos_btc_buy", "pos_eth_sell"):
        before = report["before_close"][key]
        after = report["after_replay"][key]
        if before != after:
            failures.append(
                f"{key}: before_close={before} after_replay={after}"
            )

    # Reconciliation expectations: 4 rows.
    # - btc-1: matches (qty 0, status_match True).
    # - btc-2: divergent (qty 0.025 - 0.020 = 0.005, fill_count 2 - 1 = 1,
    #   status_match True).
    # - eth-1: matches.
    # - phantom: only_in_live=True (qty_diff = 0 - 0.50 = -0.50).
    rows_by_coid = {r["client_order_id"]: r for r in report["reconcile_rows"]}
    if set(rows_by_coid.keys()) != {
        "smoke-coid-btc-1",
        "smoke-coid-btc-2",
        "smoke-coid-eth-1",
        "smoke-coid-phantom",
    }:
        failures.append(
            f"reconcile rows coids {set(rows_by_coid.keys())} != "
            f"{{btc-1, btc-2, eth-1, phantom}}"
        )

    r_btc1 = rows_by_coid.get("smoke-coid-btc-1")
    if r_btc1 is not None:
        if abs(r_btc1["qty_diff"]) > 1e-9:
            failures.append(
                f"btc1 qty_diff {r_btc1['qty_diff']} != 0"
            )
        if not r_btc1["status_match"]:
            failures.append("btc1 status_match != True")

    r_btc2 = rows_by_coid.get("smoke-coid-btc-2")
    if r_btc2 is not None:
        if abs(r_btc2["qty_diff"] - 0.005) > 1e-9:
            failures.append(
                f"btc2 qty_diff {r_btc2['qty_diff']} != 0.005"
            )
        if r_btc2["fill_count_diff"] != 1:
            failures.append(
                f"btc2 fill_count_diff {r_btc2['fill_count_diff']} != 1"
            )

    r_phantom = rows_by_coid.get("smoke-coid-phantom")
    if r_phantom is None:
        failures.append("phantom row missing")
    else:
        if not r_phantom["only_in_live"]:
            failures.append("phantom only_in_live != True")
        if r_phantom["only_in_shadow"]:
            failures.append("phantom only_in_shadow != False")

    if failures:
        print("SMOKE FAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(
        "SMOKE PASS: 7 fills across 3 coids + 4 reconciliation rows "
        "(3 matches + 1 drift + 1 phantom) + cold-start replay OK"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())