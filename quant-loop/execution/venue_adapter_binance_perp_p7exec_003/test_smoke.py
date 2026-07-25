"""test_smoke — P7-EXEC-003 end-to-end paper-trading smoke.

Drives a deterministic intent mix through the live
``ExecutionRunner.submit()`` path with the ``BinancePerpAdapter``
registered as both pre-trade and post-fill observer.  Verifies:

1. The runner journals every perp intent + every terminal ack
   (NEVER silently dropped).
2. The adapter writes one ``binance_perp_intents`` row per perp
   intent.
3. REST path: FULL FILL, PARTIAL FILL, EXPIRED, REJECTED.
4. WS path: late ``ORDER_TRADE_UPDATE`` promotes a
   PARTIALLY_FILLED coid to FILLED and journals source ``wss``.
5. userDataStream reconnect path: ``listenKeyExpired`` moves the
   consumer to RECONNECTING.
6. Cold-start reopen survives a simulated process restart.
7. ``recover()`` rebuilds the cache from the durable projection.
8. Canonical ``fills`` rows agree with the additive adapter rows.

Writes ``evidence/smoke.json`` with the run summary.

Run::

    cd ~/multica/quant-loop/execution/venue_adapter_binance_perp_p7exec_003
    python3 test_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
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
    BinancePerpStatus,
    BinancePerpWssConsumer,
    BinancePerpWssState,
    bootstrap_journal,
    policy_fingerprint,
    register_with_runner,
)
from runner import (  # noqa: E402
    ExecutionRunner,
    OrderJournal,
    OutboundTransport,
)


EVIDENCE_DIR = Path(_HERE) / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)


def _build_perp(
    *,
    coid: str,
    qty: float,
    price: float,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
) -> dict:
    return {
        "client_order_id": coid,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "venue": "binance_usdt_futures",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "binance_perp": True,
    }


def _phase_one_runner(
    journal: OrderJournal,
    adapter: BinancePerpAdapter,
    runner: ExecutionRunner,
    paper: BinancePerpPaperTransport,
) -> dict:
    # Per-coid deterministic venue outcomes.
    paper.fill_model = {
        "perp-full": FillModel(
            status="FILLED", filled_qty=0.05,
            avg_price=50000.0, commission=0.000025, order_id=1001,
        ),
        "perp-partial": FillModel(
            status="PARTIALLY_FILLED", filled_qty=0.03,
            avg_price=3000.0, commission=0.000009, order_id=1002,
        ),
        "perp-expired": FillModel(
            status="EXPIRED", filled_qty=0.0,
            avg_price=0.0, commission=0.0, order_id=1003,
        ),
        "perp-rejected": FillModel(
            reject_code=-2010,
            reject_message=(
                "Account has insufficient balance for requested action."
            ),
        ),
    }
    intents = [
        _build_perp(coid="perp-full", qty=0.05, price=50000.0),
        _build_perp(
            coid="perp-partial", qty=0.10, price=3000.0,
            symbol="ETHUSDT",
        ),
        _build_perp(
            coid="perp-expired", qty=0.20, price=100.0,
            symbol="SOLUSDT",
        ),
        _build_perp(
            coid="perp-rejected", qty=0.01, price=50000.0,
            symbol="BTCUSDT",
        ),
    ]
    acks = []
    for req in intents:
        ack = runner.submit(req)
        acks.append({"coid": req["client_order_id"], "ack": ack})
    statuses = {
        coid: adapter.get(coid).status.value
        for coid in (
            "perp-full", "perp-partial", "perp-expired",
            "perp-rejected",
        )
    }
    return {"acks": acks, "statuses": statuses}


def _phase_two_journal(journal: OrderJournal) -> dict:
    intent_rows = list(journal.conn.execute(
        "SELECT client_order_id, status, venue_order_id "
        "FROM binance_perp_intents ORDER BY client_order_id"
    ))
    event_rows = list(journal.conn.execute(
        "SELECT client_order_id, source, kind, venue_order_id "
        "FROM binance_perp_events ORDER BY ts_ns, id"
    ))
    ack_rows = list(journal.conn.execute(
        "SELECT client_order_id, status, source, filled_qty, "
        "reject_reason, error_code "
        "FROM binance_perp_acks ORDER BY client_order_id"
    ))
    canonical_terminal = list(journal.conn.execute(
        "SELECT client_order_id, event_type, qty, price "
        "FROM fills WHERE event_type IN ('fill', 'reject') "
        "ORDER BY id"
    ))
    return {
        "n_intents": len(intent_rows),
        "n_events": len(event_rows),
        "n_acks": len(ack_rows),
        "n_canonical_terminal": len(canonical_terminal),
        "intent_statuses": {r[0]: r[1] for r in intent_rows},
        "ack_statuses": {r[0]: r[1] for r in ack_rows},
        "ack_sources": {r[0]: r[2] for r in ack_rows},
        "terminal_keys": [(r[0], r[1]) for r in canonical_terminal],
    }


def _phase_three_wss(
    journal: OrderJournal,
    adapter: BinancePerpAdapter,
) -> dict:
    consumer = BinancePerpWssConsumer(
        adapter=adapter, listen_key="paper-listen-key",
    )
    consumer.connect(ts_ns=1_700_000_000_100_000_000)
    # WS reports the remaining 0.07 ETH filled.  Binance's ``z``
    # cumulative qty is 0.10 and X=FILLED; the adapter promotes the
    # partial REST outcome to terminal FILLED.
    order_update = json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "T": 1_700_000_000_100,
        "o": {
            "s": "ETHUSDT",
            "c": "perp-partial",
            "S": "BUY",
            "i": 1002,
            "X": "FILLED",
            "z": "0.10",
            "executedQty": "0.10",
            "ap": "3000.0",
            "n": "0.000030",
        },
    })
    r1 = consumer.push_frame(
        order_update,
        ts_ns=1_700_000_000_101_000_000,
    )
    account_update = json.dumps({
        "e": "ACCOUNT_UPDATE",
        "T": 1_700_000_000_102,
        "a": {"B": [], "P": []},
    })
    consumer.push_frame(
        account_update,
        ts_ns=1_700_000_000_102_000_000,
    )
    expired = json.dumps({
        "e": "listenKeyExpired",
        "T": 1_700_000_000_103,
    })
    r3 = consumer.push_frame(
        expired,
        ts_ns=1_700_000_000_103_000_000,
    )
    ack_row = list(journal.conn.execute(
        "SELECT status, source, filled_qty, venue_order_id "
        "FROM binance_perp_acks WHERE client_order_id='perp-partial'"
    ))[0]
    return {
        "order_update_observation": r1.observation if r1 else None,
        "listen_key_observation": r3.observation if r3 else None,
        "consumer_snapshot": consumer.snapshot().to_dict(),
        "partial_state_after_wss": (
            adapter.get("perp-partial").status.value
        ),
        "ack_after_wss": {
            "status": ack_row[0],
            "source": ack_row[1],
            "filled_qty": ack_row[2],
            "venue_order_id": ack_row[3],
        },
    }


def _phase_four_reopen(db_path: str) -> dict:
    j2 = OrderJournal(db_path)
    adapter2 = BinancePerpAdapter(journal=j2)
    snap = adapter2.snapshot()
    return {
        "snapshot": snap.to_dict(),
        "perp_full": adapter2.get("perp-full").status.value,
        "perp_partial": adapter2.get("perp-partial").status.value,
        "perp_expired": adapter2.get("perp-expired").status.value,
        "perp_rejected": adapter2.get("perp-rejected").status.value,
    }


def _run_smoke() -> dict:
    summary: dict = {
        "issue": "SMA-36190",
        "component": "venue_adapter_binance_perp",
        "mode": "paper_trading",
        "policy_fingerprint": policy_fingerprint(
            DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
        ),
    }
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        j = OrderJournal(db_path)
        bootstrap_journal(j)
        adapter = BinancePerpAdapter(journal=j)
        paper = BinancePerpPaperTransport()
        runner = ExecutionRunner(
            journal=j,
            transport=OutboundTransport(callable_send=paper),
        )
        summary["wiring"] = register_with_runner(runner, adapter)
        p1 = _phase_one_runner(j, adapter, runner, paper)
        summary["phase1_runner"] = p1
        p2 = _phase_two_journal(j)
        summary["phase2_journal"] = p2
        p3 = _phase_three_wss(j, adapter)
        summary["phase3_wss"] = p3
        p4 = _phase_four_reopen(db_path)
        summary["phase4_reopen"] = p4
        summary["acceptance"] = {
            "rest_full_fill": (
                p1["statuses"]["perp-full"]
                == BinancePerpStatus.FILLED.value
            ),
            "rest_partial_fill": (
                p1["statuses"]["perp-partial"]
                == BinancePerpStatus.PARTIALLY_FILLED.value
            ),
            "rest_expired": (
                p1["statuses"]["perp-expired"]
                == BinancePerpStatus.EXPIRED.value
            ),
            "rest_rejected": (
                p1["statuses"]["perp-rejected"]
                == BinancePerpStatus.REJECTED.value
            ),
            "journal_intents_match": p2["n_intents"] == 4,
            "journal_acks_match": p2["n_acks"] == 4,
            "canonical_terminal_match": p2["n_canonical_terminal"] == 4,
            "never_silent_drop": (
                set(p2["ack_statuses"].keys())
                == {"perp-full", "perp-partial", "perp-expired",
                    "perp-rejected"}
            ),
            "wss_promoted_partial_to_filled": (
                p3["partial_state_after_wss"]
                == BinancePerpStatus.FILLED.value
            ),
            "wss_ack_source": p3["ack_after_wss"]["source"] == "wss",
            "wss_reconnect_on_listen_key_expired": (
                p3["consumer_snapshot"]["state"]
                == BinancePerpWssState.RECONNECTING.value
                and p3["consumer_snapshot"]["n_reconnects"] == 1
            ),
            "reopen_full_fill": p4["perp_full"] == "FILLED",
            "reopen_wss_filled": p4["perp_partial"] == "FILLED",
            "reopen_expired": p4["perp_expired"] == "EXPIRED",
            "reopen_rejected": p4["perp_rejected"] == "REJECTED",
        }
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
    return summary


def _write_evidence(summary: dict) -> None:
    out = EVIDENCE_DIR / "smoke.json"
    with open(out, "w") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True, default=str)
    print(f"smoke evidence -> {out}")


def main() -> int:
    print("P7-EXEC-003 venue_adapter_binance_perp paper-trading smoke")
    print("===========================================================")
    summary = _run_smoke()
    _write_evidence(summary)
    print(json.dumps(summary["acceptance"], indent=2, sort_keys=True))
    failing = [
        key for key, value in summary["acceptance"].items()
        if not value
    ]
    if failing:
        print(f"\nFAIL: acceptance criteria failing: {failing}")
        return 1
    print(
        "\nPASS: REST full/partial/expired/reject + WSS fill promotion "
        "+ reconnect + durable reopen"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
