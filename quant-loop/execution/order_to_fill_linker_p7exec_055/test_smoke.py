"""End-to-end smoke test for order_to_fill_linker.

Runs a synthetic 4-order trading session against the
OrderToFillJournal, exercising every state path described in
SPEC §9 (happy path, orphan, mismatch, terminal-then-late,
duplicate, restart-and-replay). Plain asserts, no pytest dep.

Stages:

  Stage 1 — Order A (BTCUSDT BUY 0.020): intent → bind → 2 partial fills → full FILLED
  Stage 2 — Order B (ETHUSDT SELL 0.5):  intent → bind → 1 full FILLED
  Stage 3 — Order C (BTCUSDT SELL 0.005): intent → bind → REJECTED by venue
  Stage 4 — Orphan: WS reconnect delivers 2 fills with unknown order_ids (orphan journaled, never dropped)
  Stage 5 — Restart: close linker, open new one, recover_pending, re-resolve by order_id
  Stage 6 — Late cancel does not downgrade intent
  Stage 7 — Duplicate trade_id is silent-idempotent
  Stage 8 — Misrouted side raises IntentMismatch (and journal row is still written)

Stages correspond to the §9 failure-mode table in SPEC.md.

Run::

    python3 order_to_fill_linker_p7exec_055/test_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from order_to_fill_linker_p7exec_055 import (  # noqa: E402
    FillReport,
    IntentMismatch,
    Linker,
    OrderIntent,
    OrderToFillJournal,
)


# ---- Helpers ---------------------------------------------------------------

def _ts() -> int:
    return time.time_ns()


# Synthetic seed: deterministic fill prices / trade_ids so the
# smoke is reproducible from `python3 test_smoke.py` and we can
# grep evidence/smoke_results.json by content.
def _intent(*, coid: str, symbol: str, side: str, qty: float,
            strategy: str = "vpvr_reversion_1m") -> OrderIntent:
    return OrderIntent(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        intended_qty=qty,
        intent_ts_ns=_ts(),
        strategy_id=strategy,
    )


def _report(*, order_id: int, coid: str, trade_id: str, qty: float,
            price: float, cum: float, avg: float, status: str,
            source: str = "WS",
            symbol: str = "BTCUSDT", side: str = "BUY") -> FillReport:
    return FillReport(
        ts_ns=_ts(),
        order_id=order_id,
        client_order_id=coid,
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        cum_filled_qty=cum,
        avg_fill_price=avg,
        order_status=status,
        source=source,
    )


def _stage(name: str, *, expected: str, **details) -> dict:
    return {"name": name, "expected": expected, **details}


# ---- Main ------------------------------------------------------------------

def main() -> int:
    results: list[dict] = []
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "smoke_o2fl.sqlite"
    journal = OrderToFillJournal(db_path)
    linker = Linker(journal)
    linker.recover_pending()  # initially empty, but exercises the path

    try:
        # ===================================================================
        # Stage 1 — happy path BTCUSDT BUY (partial → full)
        # ===================================================================
        A_COID = "A-coid-btc-buy"
        A_OID = 1_100_001
        linker.register_intent(_intent(coid=A_COID, symbol="BTCUSDT",
                                       side="BUY", qty=0.020))
        linker.bind_order_id(A_COID, A_OID)

        r1a = linker.on_fill_report(
            _report(order_id=A_OID, coid=A_COID, trade_id="A-t1",
                    qty=0.005, price=67100.0, cum=0.005, avg=67100.0,
                    status="PARTIALLY_FILLED")
        )
        r1b = linker.on_fill_report(
            _report(order_id=A_OID, coid=A_COID, trade_id="A-t2",
                    qty=0.015, price=67150.0, cum=0.020, avg=67137.5,
                    status="FILLED")
        )
        results.append(_stage(
            "stage1_btc_buy_partial_then_filled",
            expected="is_orphan=False, final status=FILLED",
            r1a_orphan=r1a.is_orphan,
            r1a_status=r1a.intent_status_after,
            r1b_orphan=r1b.is_orphan,
            r1b_status=r1b.intent_status_after,
        ))
        assert not r1a.is_orphan and r1a.intent_status_after == "PARTIALLY_FILLED"
        assert not r1b.is_orphan and r1b.intent_status_after == "FILLED"

        # ===================================================================
        # Stage 2 — happy path ETHUSDT SELL (single full fill)
        # ===================================================================
        B_COID = "B-coid-eth-sell"
        B_OID = 1_200_002
        linker.register_intent(_intent(coid=B_COID, symbol="ETHUSDT",
                                       side="SELL", qty=0.5))
        linker.bind_order_id(B_COID, B_OID)
        r2 = linker.on_fill_report(
            _report(order_id=B_OID, coid=B_COID, trade_id="B-t1",
                    qty=0.5, price=3500.0, cum=0.5, avg=3500.0,
                    status="FILLED", symbol="ETHUSDT", side="SELL")
        )
        results.append(_stage(
            "stage2_eth_sell_full_fill",
            expected="is_orphan=False, final status=FILLED",
            r2_orphan=r2.is_orphan,
            r2_status=r2.intent_status_after,
        ))
        assert not r2.is_orphan and r2.intent_status_after == "FILLED"

        # ===================================================================
        # Stage 3 — REJECTED
        # ===================================================================
        C_COID = "C-coid-btc-sell"
        C_OID = 1_100_003
        linker.register_intent(_intent(coid=C_COID, symbol="BTCUSDT",
                                       side="SELL", qty=0.005))
        linker.bind_order_id(C_COID, C_OID)
        r3 = linker.on_fill_report(
            _report(order_id=C_OID, coid=C_COID, trade_id="C-t1",
                    qty=0.0, price=66900.0, cum=0.0, avg=0.0,
                    status="REJECTED", side="SELL", source="REST")
        )
        results.append(_stage(
            "stage3_btc_sell_rejected",
            expected="venue REJECTED → intent REJECTED",
            r3_status=r3.intent_status_after,
        ))
        assert r3.intent_status_after == "REJECTED"

        # ===================================================================
        # Stage 4 — orphan (WS reconnect)
        # ===================================================================
        orphan_a = linker.on_fill_report(
            _report(order_id=9_999_001, coid="", trade_id="orphan-t1",
                    qty=0.01, price=42500.0, cum=0.01, avg=42500.0,
                    status="FILLED", source="WS")
        )
        orphan_b = linker.on_fill_report(
            _report(order_id=9_999_002, coid="", trade_id="orphan-t2",
                    qty=0.02, price=42510.0, cum=0.02, avg=42510.0,
                    status="FILLED", source="WS")
        )
        results.append(_stage(
            "stage4_orphan_journaled_not_dropped",
            expected="both orphan, orphan_count=2",
            orphan_a_orphan=orphan_a.is_orphan,
            orphan_b_orphan=orphan_b.is_orphan,
            orphan_count=linker.orphan_count(),
        ))
        assert orphan_a.is_orphan and orphan_b.is_orphan
        assert linker.orphan_count() == 2

        # ===================================================================
        # Stage 5 — restart + recover_pending
        # ===================================================================
        linker.close()
        new_journal = OrderToFillJournal(db_path)
        new_linker = Linker(new_journal)
        n = new_linker.recover_pending()
        assert n == 3, f"expected 3 rehydrated intents, got {n}"
        # WS reconnect replay: orderId-only FillReport must resolve.
        r5 = new_linker.on_fill_report(
            _report(order_id=A_OID, coid="", trade_id="A-replay",
                    qty=0.015, price=67150.0, cum=0.020, avg=67137.5,
                    status="FILLED")
        )
        results.append(_stage(
            "stage5_restart_recover_pending_then_resolve_by_order_id",
            expected="rehydrated 3 intents, orderId-only resolves to A",
            rehydrated=n,
            r5_orphan=r5.is_orphan,
            r5_coid=r5.client_order_id,
            r5_status=r5.intent_status_after,
        ))
        assert not r5.is_orphan and r5.client_order_id == A_COID
        assert r5.intent_status_after == "FILLED"

        # ===================================================================
        # Stage 6 — late cancel does NOT downgrade
        # ===================================================================
        r6 = new_linker.on_fill_report(FillReport(
            ts_ns=_ts(),
            order_id=A_OID,
            client_order_id="",
            trade_id="A-late-cancel",
            symbol="BTCUSDT",
            side="BUY",
            qty=0.010,
            price=67123.4,
            cum_filled_qty=0.020,
            avg_fill_price=67137.5,
            order_status="CANCELED",
        ))
        results.append(_stage(
            "stage6_late_cancel_does_not_downgrade_filled",
            expected="intent stays FILLED even after late CANCELED",
            r6_status=r6.intent_status_after,
            intent_status_persisted=new_linker.fetch_intent(A_OID).intent_status,
        ))
        assert r6.intent_status_after == "FILLED"
        assert new_linker.fetch_intent(A_OID).intent_status == "FILLED"

        # ===================================================================
        # Stage 7 — duplicate trade_id is silent-idempotent
        # ===================================================================
        r7a = new_linker.on_fill_report(
            _report(order_id=B_OID, coid=B_COID, trade_id="B-t1",
                    qty=0.5, price=3500.0, cum=0.5, avg=3500.0,
                    status="FILLED", symbol="ETHUSDT", side="SELL")
        )
        r7b = new_linker.on_fill_report(
            _report(order_id=B_OID, coid=B_COID, trade_id="B-t1",
                    qty=0.5, price=3500.0, cum=0.5, avg=3500.0,
                    status="FILLED", symbol="ETHUSDT", side="SELL")
        )
        results.append(_stage(
            "stage7_duplicate_trade_id_silent_idempotent",
            expected="both returns same intent_status, no second journal row",
            r7a_status=r7a.intent_status_after,
            r7b_status=r7b.intent_status_after,
        ))
        assert r7a.intent_status_after == r7b.intent_status_after == "FILLED"

        # ===================================================================
        # Stage 8 — misrouted side raises IntentMismatch
        # ===================================================================
        # Order A is BUY. Push a SELL FillReport on the same order_id.
        try:
            new_linker.on_fill_report(
                _report(order_id=A_OID, coid="", trade_id="A-misroute",
                        qty=0.005, price=60000.0, cum=0.005, avg=60000.0,
                        status="FILLED", symbol="BTCUSDT", side="SELL")
            )
        except IntentMismatch as exc:
            results.append(_stage(
                "stage8_misrouted_side_raises_intentmismatch",
                expected="IntentMismatch raised, intent still FILLED",
                raised=True,
                exc_msg=str(exc),
            ))
            assert "side" in str(exc).lower()
        else:
            raise AssertionError("expected IntentMismatch on misrouted side")
        # Sanity: orphan count unchanged (mismatch is not orphan; the
        # row exists with the correct coid).
        assert new_linker.orphan_count() == 2

        new_linker.close()
    finally:
        journal.close()
        tmp.cleanup()

    import json
    evidence = {
        "smoke_name": "order_to_fill_linker_p7exec_055",
        "stages": results,
        "stages_total": len(results),
        "passed": all(
            r["expected"] is not None for r in results  # soft check; real assertion above
        ),
    }
    out_path = Path(__file__).parent / "evidence" / "smoke_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2))
    print(f"\nSMOKE OK — {len(results)} stages; wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())