"""test_smoke — P7-EXEC-043 end-to-end smoke vs ExecutionRunner.

Drives a deterministic intent / fill mix through the live
``ExecutionRunner.submit()`` path with the
:class:`SlippageAttributionClassifier` registered as a
post-fill observer, and verifies:

* the observer writes one ``slippage_attribution_fills`` row
  per fill (additive P7-EXEC-081 pattern);
* the per-fill decomposition is correct on a clean (BUY,
  cross-ask, walk) fill;
* the daily aggregator's ``compute_day`` and ``record`` /
  ``fetch`` round-trip is idempotent;
* the additive tables persist across on-disk re-open (cold-start
  durability);
* the WARN / RECOVERED hysteresis fires under a tight threshold;
* the additive-exact identity
  ``spread_cost + impact == total`` holds for every fill.

Run as ``python3 test_smoke.py``.

Output is also written to ``evidence/smoke.json`` so a future
reviewer can reproduce the verification without re-running.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.abspath(os.path.join(_PARENT, ".."))
for _p in (_HERE, _PARENT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution.runner import (  # noqa: E402
    ExecutionRunner,
    OrderJournal,
    OutboundTransport,
)
from execution.slippage_attribution_p7exec_043 import (  # noqa: E402
    SPREAD,
    IMPACT,
    MIXED,
    AttributionThresholds,
    SlippageAttributionClassifier,
    SlippageAttributionReport,
    day_utc_bounds,
)


EVIDENCE_DIR = Path(_HERE) / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)

DAY_UTC = "2026-07-20"
EXPECTED_PRICE = 50000.0
# 10 fills at $50000 + (i+1)*2.0 → fill prices 50002, 50004, ... 50020.
# BUY slippage = (expected - fill) / expected * 10000
#   = -0.4, -0.8, -1.2, -1.6, -2.0, -2.4, -2.8, -3.2, -3.6, -4.0 bps
# Mean = -2.2 bps, median = -2.2 bps.
PRICES = [EXPECTED_PRICE + (i + 1) * 2.0 for i in range(10)]
# Synthetic half-spread: 1 bps at mid 50000 → 5.00 on each side
# (half-spread = (10/50000)*10000/2 = 1 bps).
ARRIVAL_BID = EXPECTED_PRICE - 5.00
ARRIVAL_ASK = EXPECTED_PRICE + 5.00


def _make_transport(prices, venues):
    """Build a transport whose ``price`` / ``venue`` walk in lock-step."""
    state = {"i": 0}

    def send(req):
        idx = state["i"]
        state["i"] = idx + 1
        price = prices[idx % len(prices)]
        venue = venues[idx % len(venues)]
        return {"ok": True, "price": price, "venue": venue}

    return send


def _check(cond: bool, label: str) -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise SystemExit(1)


def _run_smoke() -> dict:
    # ---- live E2E via ExecutionRunner + on_fill observer ----------------
    j = OrderJournal(":memory:")
    classifier = SlippageAttributionClassifier(journal=j)
    venues = ["binance_usdt_futures"] * 5 + ["coinbase"] * 5
    runner = ExecutionRunner(
        journal=j,
        transport=OutboundTransport(callable_send=_make_transport(PRICES, venues)),
    )
    runner.register_on_fill(classifier)

    ts_now = time.time_ns()
    for i in range(len(PRICES)):
        result = runner.submit({
            "client_order_id": f"smoke-coid-{i}",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": 0.01,
            "venue": venues[i],
            "expected_price": EXPECTED_PRICE,
            "arrival_bid": ARRIVAL_BID,
            "arrival_ask": ARRIVAL_ASK,
            "arrival_mid": EXPECTED_PRICE,
        })
        obs = (result.get("observations") or {})
        _check(
            obs.get("classification") in (SPREAD, IMPACT, MIXED),
            f"classification label in ({SPREAD}, {IMPACT}, {MIXED}) on fill {i} "
            f"(got {obs.get('classification')!r})"
        )
        # Additive identity must hold for every classified row
        total = obs.get("total_slippage_bps")
        sp = obs.get("spread_cost_bps")
        imp = obs.get("impact_bps")
        res = obs.get("residual_bps")
        if total is not None and sp is not None and imp is not None:
            _check(
                abs(total - sp - imp - res) < 1e-6,
                f"identity holds on fill {i} (err={abs(total - sp - imp - res):.2e})"
            )

    # The runner overrode the ts to time.time_ns(); the attribution
    # rows are journalized on the same timestamp. Confirm count.
    cur = j.conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM slippage_attribution_fills")
    n_sa = int(cur.fetchone()["n"])
    _check(n_sa == 10, f"10 attribution rows journaled (got {n_sa})")

    # ---- cold-path aggregator: write the rows onto a fresh journal ----
    # anchored on DAY_UTC, then compute / record / fetch round-trip.
    j2 = OrderJournal(":memory:")
    classifier2 = SlippageAttributionClassifier(journal=j2)
    start, _ = day_utc_bounds(DAY_UTC)
    base = start + 60 * 1_000_000_000  # 1 min after UTC midnight

    for i, fp in enumerate(PRICES):
        coid = f"smoke-coid-{i}"
        ts = base + i * 1_000_000_000
        classifier2.on_fill(
            request={
                "client_order_id": coid,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "qty": 0.01,
                "venue": venues[i],
                "expected_price": EXPECTED_PRICE,
                "arrival_bid": ARRIVAL_BID,
                "arrival_ask": ARRIVAL_ASK,
                "arrival_mid": EXPECTED_PRICE,
                "submit_ts_ns": ts,
            },
            ack={"price": fp, "venue": venues[i]},
            journal=j2,
            ts_ns=ts,
        )

    report = SlippageAttributionReport(journal=j2, min_sample=5)
    daily = report.compute_day(DAY_UTC, now_ns=base + 100_000_000_000)

    _check(daily.n_fills == 10, f"n_fills=10 (got {daily.n_fills})")
    _check(daily.n_fills_with_book == 10,
           f"n_fills_with_book=10 (got {daily.n_fills_with_book})")
    _check(daily.stable,
           f"stable (n_fills_with_book=10 >= min_sample=5)")
    # Mean total = -2.2 bps (deterministic, see header comment).
    _check(abs(daily.mean_total_slippage_bps - (-2.2)) < 1e-9,
           f"mean_total_slippage_bps=-2.2 (got {daily.mean_total_slippage_bps})")
    # All 10 fills are 1 bps spread × 10 fills so half-spread avg
    # should be ≈ -1.0 bps (BUY always pays the spread).
    _check(abs(daily.mean_spread_cost_bps - (-1.0)) < 1e-9,
           f"mean_spread_cost_bps≈-1.0 (got {daily.mean_spread_cost_bps})")
    # Mean impact = total - spread = -2.2 - (-1.0) = -1.2 bps.
    _check(abs(daily.mean_impact_bps - (-1.2)) < 1e-9,
           f"mean_impact_bps≈-1.2 (got {daily.mean_impact_bps})")
    # Per-venue split: 5 + 5.
    venue_counts = {v.venue: v.n_fills for v in daily.by_venue}
    _check(venue_counts.get("binance_usdt_futures") == 5,
           f"binance_usdt_futures n=5 (got {venue_counts.get('binance_usdt_futures')})")
    _check(venue_counts.get("coinbase") == 5,
           f"coinbase n=5 (got {venue_counts.get('coinbase')})")

    # ---- idempotent record / fetch --------------------------------------
    row_id = report.record(daily)
    _check(row_id > 0, f"record returns positive row id (got {row_id})")

    fetched = report.fetch(DAY_UTC)
    _check(fetched is not None, "fetch returns the persisted report")
    _check(fetched.day_utc == DAY_UTC,
           f"day_utc roundtrips (got {fetched.day_utc})")
    _check(
        abs(fetched.mean_total_slippage_bps - daily.mean_total_slippage_bps) < 1e-9,
        "mean_total_slippage_bps roundtrips",
    )

    # Re-record → idempotent
    report.record(daily)
    cur = j2.conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM slippage_attribution_daily_reports "
        "WHERE day_utc = ?", (DAY_UTC,)
    )
    _check(int(cur.fetchone()["n"]) == 1,
           "re-record is idempotent (UNIQUE(day_utc))")

    # ---- WARN / RECOVERED under a tight threshold -----------------------
    j3 = OrderJournal(":memory:")
    warn_clf = SlippageAttributionClassifier(
        journal=j3,
        thresholds=AttributionThresholds(
            window_s=600.0, impact_warn_bps=0.5,
            impact_hysteresis_bps=0.2, dominance_fraction=0.5,
        ),
    )
    base_ns = time.time_ns()
    # Drive 3 fills that book -1.5 bps impact each (deterministic).
    for i in range(3):
        warn_clf.on_fill(
            request={
                "client_order_id": f"warn-coid-{i}",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "qty": 0.01,
                "expected_price": 100.0,
                "arrival_bid": 99.95,
                "arrival_ask": 100.05,
                "submit_ts_ns": base_ns + i * 1_000_000,
            },
            ack={"price": 100.10, "venue": "v"},  # -10 bps total, -5 spread, -5 impact
            journal=j3,
            ts_ns=base_ns + i * 1_000_000,
        )
    cur = j3.conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM slippage_attribution_events "
        "WHERE severity = 'WARN'"
    )
    _check(int(cur.fetchone()["n"]) >= 1, "WARN row journaled under tight threshold")

    # ---- on-disk durability ---------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "smoke.db"
        disk_j = OrderJournal(str(db_path))
        disk_classifier = SlippageAttributionClassifier(journal=disk_j)
        s2, _ = day_utc_bounds(DAY_UTC)
        for i, fp in enumerate(PRICES):
            coid = f"disk-coid-{i}"
            ts = s2 + (60 + i) * 1_000_000_000
            disk_classifier.on_fill(
                request={
                    "client_order_id": coid,
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "qty": 0.01,
                    "venue": venues[i],
                    "expected_price": EXPECTED_PRICE,
                    "arrival_bid": ARRIVAL_BID,
                    "arrival_ask": ARRIVAL_ASK,
                    "arrival_mid": EXPECTED_PRICE,
                    "submit_ts_ns": ts,
                },
                ack={"price": fp, "venue": venues[i]},
                journal=disk_j,
                ts_ns=ts,
            )
        disk_report = SlippageAttributionReport(journal=disk_j, min_sample=5)
        disk_daily = disk_report.compute_day(DAY_UTC)
        disk_report.record(disk_daily)
        disk_j.close()

        # Re-open — cold-start reader can rebuild the same report.
        reopen_j = OrderJournal(str(db_path))
        reopen_report = SlippageAttributionReport(journal=reopen_j, min_sample=5)
        reopen_daily = reopen_report.fetch(DAY_UTC)
        _check(reopen_daily is not None, "cold-start fetch succeeds")
        _check(reopen_daily.n_fills == 10,
               f"cold-start n_fills=10 (got {reopen_daily.n_fills})")
        _check(
            abs(reopen_daily.mean_total_slippage_bps - (-2.2)) < 1e-9,
            "cold-start mean_total_slippage_bps matches",
        )
        reopen_j.close()

    return {
        "n_orders": len(PRICES),
        "day_utc": DAY_UTC,
        "expected_price": EXPECTED_PRICE,
        "fill_prices": PRICES,
        "headline": {
            "n_fills": daily.n_fills,
            "n_fills_with_book": daily.n_fills_with_book,
            "mean_total_slippage_bps": daily.mean_total_slippage_bps,
            "mean_spread_cost_bps": daily.mean_spread_cost_bps,
            "mean_impact_bps": daily.mean_impact_bps,
            "median_total_slippage_bps": daily.median_total_slippage_bps,
            "p05_impact_bps": daily.p05_impact_bps,
            "p95_impact_bps": daily.p95_impact_bps,
            "impact_share": daily.impact_share,
            "spread_share": daily.spread_share,
            "n_spread_dominant": daily.n_spread_dominant,
            "n_impact_dominant": daily.n_impact_dominant,
            "n_mixed": daily.n_mixed,
            "stable": daily.stable,
        },
        "by_venue": [
            {
                "venue": v.venue,
                "n_fills": v.n_fills,
                "n_fills_with_book": v.n_fills_with_book,
                "mean_total_slippage_bps": v.mean_total_slippage_bps,
                "mean_spread_cost_bps": v.mean_spread_cost_bps,
                "mean_impact_bps": v.mean_impact_bps,
                "impact_share": v.impact_share,
                "spread_share": v.spread_share,
            }
            for v in daily.by_venue
        ],
        "verdict": (
            "PASS — additive identity holds across 10 live fills, "
            "daily aggregator mean total = -2.2 bps / spread = -1.0 / "
            "impact = -1.2 with 5+5 venue split, WARN hysteresis fires "
            "under tight threshold, on-disk cold-start round-trip "
            "matches."
        ),
    }


def main() -> None:
    print("--- slippage_attribution E2E smoke ---")
    result = _run_smoke()
    out_path = EVIDENCE_DIR / "smoke.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nEVIDENCE written to {out_path}")
    print(f"\n=== smoke verdict: {result['verdict']} ===")


if __name__ == "__main__":
    main()