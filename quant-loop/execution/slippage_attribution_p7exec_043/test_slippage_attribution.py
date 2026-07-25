"""Unit tests for slippage_attribution — P7-EXEC-043.

Pure-function tests for the decomposition, plus observer
round-trip tests against an in-memory journal. Run as

    python3 test_slippage_attribution.py

Every test is a plain assertion; ``unittest`` is intentionally
not pulled in so the test file stays importable in any
restricted pytest-less environment (matches the sibling
P7-EXEC convention).
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.abspath(os.path.join(_PARENT, ".."))
for _p in (_HERE, _PARENT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution.slippage_attribution_p7exec_043 import (  # noqa: E402
    DEFAULT_ATTRIBUTION_THRESHOLDS,
    IMPACT,
    MIXED,
    NO_BOOK,
    SPREAD,
    AttributionRecord,
    AttributionRow,
    AttributionThresholds,
    DailyAttributionReport,
    FillRecord,
    SlippageAttributionClassifier,
    SlippageAttributionReport,
    SymbolDailyAttribution,
    VenueDailyAttribution,
    aggregate_by_symbol,
    aggregate_by_venue,
    aggregate_overall,
    attribute_fill,
    attribute_fills,
    bootstrap_journal,
    day_utc_bounds,
    half_spread_bps,
    spread_cost_bps,
    total_slippage_bps,
)
from execution.runner import (  # noqa: E402
    ComponentResult,
    OrderJournal,
)


# ---------- Test harness ----------

N_PASS = 0
N_FAIL = 0


def _check(cond: bool, label: str) -> None:
    global N_PASS, N_FAIL
    if cond:
        N_PASS += 1
        print(f"  PASS  {label}")
    else:
        N_FAIL += 1
        print(f"  FAIL  {label}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------- 1. half_spread_bps ----------

def test_half_spread_basic() -> None:
    _section("half_spread_bps")
    s = half_spread_bps(99.95, 100.05)
    # (0.10 / 100.00) * 10000 / 2 = 5 bps (half-spread)
    _check(abs(s - 5.0) < 1e-9, "half_spread at 99.95/100.05 ≈ 5 bps")
    # At 1000 mid with a 0.10 spread → (0.10/1000)*10000/2 = 0.5 bps.
    # The math is "bps of mid", not "bps of fixed unit", so a wider
    # mid has a smaller half-spread for the same absolute spread.
    s2 = half_spread_bps(999.95, 1000.05)
    _check(abs(s2 - 0.5) < 1e-6, "half_spread at 999.95/1000.05 ≈ 0.5 bps")
    # Symmetry check at the SAME mid: 10 bps total spread on a
    # 100 mid → 5 bps half. Confirms the formula is consistent.
    s3 = half_spread_bps(95.0, 105.0)  # mid 100, spread 10
    _check(abs(s3 - 500.0) < 1e-9,
           "half_spread on 10-wide spread at mid 100 ≈ 500 bps")


def test_half_spread_rejects_invalid() -> None:
    try:
        half_spread_bps(-1.0, 100.0)
        _check(False, "rejects negative bid")
    except ValueError:
        _check(True, "rejects negative bid")
    try:
        half_spread_bps(100.0, 99.0)
        _check(False, "rejects crossed book")
    except ValueError:
        _check(True, "rejects crossed book")
    try:
        half_spread_bps(0.0, 100.0)
        _check(False, "rejects zero bid")
    except ValueError:
        _check(True, "rejects zero bid")


# ---------- 2. spread_cost_bps ----------

def test_spread_cost_signs() -> None:
    _section("spread_cost_bps sign convention")
    c_buy = spread_cost_bps(side="BUY", arrival_bid=99.95, arrival_ask=100.05)
    c_sell = spread_cost_bps(side="SELL", arrival_bid=99.95, arrival_ask=100.05)
    _check(c_buy < 0, "BUY spread_cost_bps <= 0")
    _check(c_sell < 0, "SELL spread_cost_bps <= 0")
    _check(abs(c_buy - c_sell) < 1e-9, "BUY/SELL spread_cost equal magnitude")


def test_spread_cost_case_insensitive() -> None:
    c1 = spread_cost_bps(side="buy", arrival_bid=99.95, arrival_ask=100.05)
    c2 = spread_cost_bps(side="BUY", arrival_bid=99.95, arrival_ask=100.05)
    _check(c1 == c2, "case-insensitive side normalization")


def test_spread_cost_rejects_garbage() -> None:
    for bad in ("", "BUY/SELL", "long", "HOLD"):
        try:
            spread_cost_bps(side=bad, arrival_bid=99.95, arrival_ask=100.05)
            _check(False, f"rejects side={bad!r}")
        except ValueError:
            _check(True, f"rejects side={bad!r}")


# ---------- 3. total_slippage_bps ----------

def test_total_slippage_signs() -> None:
    _section("total_slippage_bps sign convention")
    # BUY at 100.07 vs expected 100.00 → trader paid 7 bps
    s_buy = total_slippage_bps(
        side="BUY", expected_price=100.00, fill_price=100.07
    )
    _check(abs(s_buy - (-7.0)) < 1e-9, "BUY adverse: -7 bps")
    # BUY at 99.97 vs expected 100.00 → trader improved 3 bps
    s_buy_improve = total_slippage_bps(
        side="BUY", expected_price=100.00, fill_price=99.97
    )
    _check(abs(s_buy_improve - 3.0) < 1e-9, "BUY improvement: +3 bps")
    # SELL at 99.93 vs expected 100.00 → trader paid 7 bps
    s_sell = total_slippage_bps(
        side="SELL", expected_price=100.00, fill_price=99.93
    )
    _check(abs(s_sell - (-7.0)) < 1e-9, "SELL adverse: -7 bps")
    # SELL at 100.03 vs expected 100.00 → trader improved 3 bps
    s_sell_improve = total_slippage_bps(
        side="SELL", expected_price=100.00, fill_price=100.03
    )
    _check(abs(s_sell_improve - 3.0) < 1e-9, "SELL improvement: +3 bps")


def test_total_slippage_rejects_invalid() -> None:
    try:
        total_slippage_bps(
            side="BUY", expected_price=100.0, fill_price=0.0
        )
        _check(False, "rejects zero fill price")
    except ValueError:
        _check(True, "rejects zero fill price")
    try:
        total_slippage_bps(
            side="BUY", expected_price=100.0, fill_price=float("nan")
        )
        _check(False, "rejects NaN")
    except ValueError:
        _check(True, "rejects NaN")
    try:
        total_slippage_bps(
            side="LONG", expected_price=100.0, fill_price=100.0
        )
        _check(False, "rejects malformed side")
    except ValueError:
        _check(True, "rejects malformed side")


# ---------- 4. attribute_fill ----------

def test_attribute_fill_clean_decomposition() -> None:
    _section("attribute_fill — clean two-leg decomposition")
    # Concrete: arrival book mid = 100, half-spread = 5 bps,
    # BUY fills @ 100.07 (crossed ask + 2 bps walked)
    # total = -7, spread_cost = -5, impact = -2, residual = 0
    rec = FillRecord(
        timestamp=1, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=100.07, quantity=0.01,
        arrival_bid=99.95, arrival_ask=100.05,
        arrival_mid=100.0, venue="binance_usdt_futures",
        client_order_id="coid-1",
    )
    row = attribute_fill(rec)
    _check(abs(row.spread_bps - 5.0) < 1e-6,
           f"spread_bps ≈ 5.0 (got {row.spread_bps:.6f})")
    _check(abs(row.spread_cost_bps + 5.0) < 1e-6,
           f"spread_cost_bps ≈ -5.0 (got {row.spread_cost_bps:.6f})")
    _check(abs(row.impact_bps + 2.0) < 1e-6,
           f"impact_bps ≈ -2.0 (got {row.impact_bps:.6f})")
    _check(abs(row.total_slippage_bps + 7.0) < 1e-6,
           f"total_slippage_bps ≈ -7.0 (got {row.total_slippage_bps:.6f})")
    _check(abs(row.residual_bps) < 1e-6,
           f"residual_bps == 0 (got {row.residual_bps:.2e})")
    _check(row.classification == SPREAD,
           f"SPREAD dominant (got {row.classification})")


def test_attribute_fill_sell_clean() -> None:
    # SELL at 99.93 vs expected 100.00, bid 99.95 / ask 100.05
    # total = (99.93 - 100.00)/100 * 10000 = -7 bps
    # spread_cost = -5 bps, impact = -2 bps, residual = 0
    rec = FillRecord(
        timestamp=2, side="SELL", symbol="ETHUSDT",
        expected_price=100.0, fill_price=99.93, quantity=1.0,
        arrival_bid=99.95, arrival_ask=100.05,
        client_order_id="coid-2",
    )
    row = attribute_fill(rec)
    _check(abs(row.total_slippage_bps + 7.0) < 1e-6,
           f"SELL total ≈ -7 (got {row.total_slippage_bps:.6f})")
    _check(abs(row.spread_cost_bps + 5.0) < 1e-6,
           f"SELL spread_cost ≈ -5 (got {row.spread_cost_bps:.6f})")
    _check(abs(row.impact_bps + 2.0) < 1e-6,
           f"SELL impact ≈ -2 (got {row.impact_bps:.6f})")


def test_attribute_fill_improvement_classified() -> None:
    # BUY at 99.97 vs expected 100.00 → improvement +3 bps
    # spread_cost = -5, impact = -5 + 3 = +8 (improvement > spread)
    rec = FillRecord(
        timestamp=3, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=99.97, quantity=0.01,
        arrival_bid=99.95, arrival_ask=100.05,
        client_order_id="coid-3",
    )
    row = attribute_fill(rec)
    _check(abs(row.total_slippage_bps - 3.0) < 1e-6,
           f"total ≈ +3 (got {row.total_slippage_bps:.6f})")
    _check(abs(row.spread_cost_bps + 5.0) < 1e-6,
           f"spread_cost ≈ -5 (got {row.spread_cost_bps:.6f})")
    _check(abs(row.impact_bps - 8.0) < 1e-6,
           f"impact ≈ +8 (got {row.impact_bps:.6f})")


def test_attribute_fill_no_book_classified() -> None:
    _section("attribute_fill — NO_BOOK path")
    rec = FillRecord(
        timestamp=4, side="SELL", symbol="BTCUSDT",
        expected_price=100.0, fill_price=99.5, quantity=0.01,
        client_order_id="coid-4",
    )
    row = attribute_fill(rec)
    _check(row.classification == NO_BOOK,
           f"NO_BOOK (got {row.classification})")
    _check(row.spread_bps == 0.0,
           "no spread when no book")
    _check(row.impact_bps == 0.0,
           "no impact when no book")
    # SELL @ 99.5 vs expected 100 → trader paid 50 bps (adverse,
    # negative). total_slippage_bps = -50.
    _check(abs(row.total_slippage_bps + 50.0) < 1e-6,
           "total still reported from price diff")


def test_attribute_fill_no_arrival_is_observer_only() -> None:
    # The pure helper does NOT have a NO_ARRIVAL classification
    # (NO_ARRIVAL is reserved for the observer's response when a
    # request misses the expected_price / mark_price / arrival_mid
    # resolution chain). The pure helper reports degenerate row.
    rec = FillRecord(
        timestamp=5, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=100.0, quantity=0.01,
        client_order_id="coid-5",
    )
    row = attribute_fill(rec)
    _check(row.classification != "NO_ARRIVAL",
           "pure helper does not emit NO_ARRIVAL")


def test_attribute_fill_crossed_book_falls_back_to_no_book() -> None:
    _section("attribute_fill — crossed-book fallback")
    rec = FillRecord(
        timestamp=6, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=100.05, quantity=0.01,
        arrival_bid=100.05, arrival_ask=99.95,  # crossed!
        client_order_id="coid-6",
    )
    row = attribute_fill(rec)
    _check(row.classification == NO_BOOK,
           f"crossed → NO_BOOK (got {row.classification})")
    _check(row.spread_bps == 0.0, "no spread on crossed book")


def test_attribute_fill_negative_inputs_rejected() -> None:
    for bad_qty in (0.0, -0.01):
        try:
            FillRecord(
                timestamp=0, side="BUY", symbol="BTCUSDT",
                expected_price=100.0, fill_price=100.0, quantity=bad_qty,
                client_order_id="bad",
            )
            # Just creating the dataclass does not raise; the call
            # to attribute_fill does.
            rec = FillRecord(
                timestamp=0, side="BUY", symbol="BTCUSDT",
                expected_price=100.0, fill_price=100.0, quantity=bad_qty,
                client_order_id="bad",
            )
            attribute_fill(rec)
            _check(False, f"rejects qty={bad_qty}")
        except ValueError:
            _check(True, f"rejects qty={bad_qty}")


def test_attribute_fill_additive_identity_holds() -> None:
    """For a wide grid, spread_cost + impact == total (within ε)."""
    sides = ("BUY", "SELL")
    for expected in (50.0, 100.0, 1000.0, 50000.0):
        for delta_bps in (-30, -10, -1, 0, 1, 10, 30):
            fill = expected * (1 + delta_bps / 10000.0)
            spread_bps_value = 5.0
            bid = expected * (1 - spread_bps_value / 20000.0)
            ask = expected * (1 + spread_bps_value / 20000.0)
            for s in sides:
                rec = FillRecord(
                    timestamp=0, side=s, symbol="BTCUSDT",
                    expected_price=expected, fill_price=fill,
                    quantity=0.001, arrival_bid=bid, arrival_ask=ask,
                    client_order_id="coid-grid",
                )
                row = attribute_fill(rec)
                identity = abs(
                    row.total_slippage_bps
                    - row.spread_cost_bps
                    - row.impact_bps
                    - row.residual_bps
                )
                _check(identity < 1e-6,
                       f"identity holds for side={s} exp={expected} "
                       f"delta={delta_bps}bps (err={identity:.2e})")


# ---------- 5. Classification ----------

def test_classification_thresholds() -> None:
    _section("classification buckets")
    # Spread dominates: half-spread 6 bps vs total 10 bps,
    # leaving 4 bps of impact.
    rec = FillRecord(
        timestamp=0, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=100.10,  # -10 bps
        quantity=0.01,
        arrival_bid=99.94, arrival_ask=100.06,  # half-spread = 6 bps
        client_order_id="cls-1",
    )
    row = attribute_fill(rec)
    _check(row.classification == SPREAD,
           f"spread-dominant → SPREAD (got {row.classification})")

    # Impact dominates: very narrow half-spread, big adverse.
    rec2 = FillRecord(
        timestamp=0, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=100.10,  # -10 bps
        quantity=0.01,
        arrival_bid=99.998, arrival_ask=100.002,  # half-spread ≈ 0.2 bps
        client_order_id="cls-2",
    )
    row2 = attribute_fill(rec2)
    _check(row2.classification == IMPACT,
           f"impact-dominant → IMPACT (got {row2.classification})")


# ---------- 6. Aggregation ----------

def test_aggregate_overall_and_breakdowns() -> None:
    _section("aggregate — overall / by venue / by symbol")
    rows = [
        # BTCUSDT / binance_usdt_futures: spread-dominant
        FillRecord(
            timestamp=1, side="BUY", symbol="BTCUSDT",
            expected_price=100.0, fill_price=100.07, quantity=1.0,
            arrival_bid=99.95, arrival_ask=100.05,
            venue="binance_usdt_futures", client_order_id="a",
        ),
        FillRecord(
            timestamp=2, side="SELL", symbol="BTCUSDT",
            expected_price=100.0, fill_price=99.93, quantity=1.0,
            arrival_bid=99.95, arrival_ask=100.05,
            venue="binance_usdt_futures", client_order_id="b",
        ),
        # ETHUSDT / coinbase: impact-dominant
        FillRecord(
            timestamp=3, side="BUY", symbol="ETHUSDT",
            expected_price=2000.0, fill_price=2002.0, quantity=2.0,
            arrival_bid=1999.95, arrival_ask=2000.05,
            venue="coinbase", client_order_id="c",
        ),
        # Bookless fill (NO_BOOK)
        FillRecord(
            timestamp=4, side="SELL", symbol="ETHUSDT",
            expected_price=2000.0, fill_price=1999.5, quantity=0.5,
            venue="coinbase", client_order_id="d",
        ),
    ]
    rows_attributed = attribute_fills(rows)

    n, n_book, mean_total, mean_spread, mean_impact, *_ = aggregate_overall(
        rows_attributed
    )
    _check(n == 4, f"n=4 (got {n})")
    _check(n_book == 3, f"n_fills_with_book=3 (got {n_book})")
    _check(mean_total < 0, "mean_total negative (adverse)")
    _check(mean_spread < 0, "mean_spread negative")
    _check(mean_impact < 0, "mean_impact negative")

    by_venue = aggregate_by_venue(rows_attributed)
    _check(len(by_venue) == 2, f"2 venues (got {len(by_venue)})")
    _check(by_venue[0].n_fills >= by_venue[-1].n_fills,
           "by_venue sorted descending by n_fills")

    by_symbol = aggregate_by_symbol(rows_attributed)
    symbols = {s.symbol for s in by_symbol}
    _check(symbols == {"BTCUSDT", "ETHUSDT"},
           f"symbols covered (got {symbols})")


def test_aggregate_zero_fill_handles_gracefully() -> None:
    n, n_book, mean_total, *_ = aggregate_overall([])
    _check(n == 0 and n_book == 0 and mean_total == 0.0,
           "empty input → all zeros, no crash")
    _check(aggregate_by_venue([]) == (), "empty by_venue → empty tuple")
    _check(aggregate_by_symbol([]) == (), "empty by_symbol → empty tuple")


def test_classification_mixed() -> None:
    # Roughly equal spread and impact
    # half-spread = 5, total = -8 → impact = -3
    # spread_frac = 5/8 = 62.5% (still SPREAD by 50% threshold)
    rec = FillRecord(
        timestamp=0, side="BUY", symbol="BTCUSDT",
        expected_price=100.0, fill_price=100.08, quantity=0.01,
        arrival_bid=99.95, arrival_ask=100.05,  # 5 bps half
        client_order_id="mix-1",
    )
    row = attribute_fill(rec)
    # |spread|/|total| = 5/8 = 0.625 > 0.5 → SPREAD, not MIXED
    _check(row.classification == SPREAD,
           f"62.5% spread → SPREAD (got {row.classification})")


# ---------- 7. day_utc_bounds ----------

def test_day_utc_bounds_basic() -> None:
    _section("day_utc_bounds")
    start, end = day_utc_bounds("2026-07-25")
    _check(end - start == 86_400 * 1_000_000_000,
           "24h span in ns")
    # Sanity: epoch-based check (1970-01-01 is start 0).
    start_epoch, _ = day_utc_bounds("1970-01-01")
    _check(start_epoch == 0, f"epoch day starts at 0 (got {start_epoch})")
    # Sanity: 2026-07-25 should match calendar.timegm((2026,7,25,0,0,0,0,0,0)).
    import calendar as _cal
    expected_s = _cal.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0))
    _check(start // 1_000_000_000 == expected_s,
           f"2026-07-25 start matches timegm ({start // 1_000_000_000} == {expected_s})")
    # Malformed inputs (note: "2026-7-25" parses via the
    # int-cast so it IS accepted — single-digit month is legal).
    for bad in ("2026/07/25", "25-07-2026", "abc", "2026-13-01", "2026-02-30", ""):
        try:
            day_utc_bounds(bad)
            _check(False, f"rejects {bad!r}")
        except ValueError:
            _check(True, f"rejects {bad!r}")


def test_day_utc_bounds_consistent() -> None:
    a = day_utc_bounds("2026-01-01")
    b = day_utc_bounds("2026-01-02")
    _check(a[1] == b[0], f"adjacent day boundaries meet (got {a[1]} vs {b[0]})")


# ---------- 8. Observer ----------

def test_observer_round_trip_basic() -> None:
    _section("observer round-trip")
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(journal=journal)

    request = {
        "client_order_id": "coid-obs-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.01,
        "expected_price": 100.0,
        "arrival_bid": 99.95,
        "arrival_ask": 100.05,
        "arrival_mid": 100.0,
    }
    ack = {"price": 100.07, "venue": "binance_usdt_futures"}
    result = classifier.on_fill(request, ack, journal, ts_ns=1_700_000_000_000_000_000)
    obs = result.observation
    _check(obs is not None, "observation non-None")
    _check(obs["classification"] == SPREAD,
           f"classification=SPREAD (got {obs['classification']})")
    _check(abs(obs["spread_cost_bps"] + 5.0) < 1e-6,
           f"spread_cost_bps ≈ -5 (got {obs['spread_cost_bps']:.6f})")
    _check(abs(obs["impact_bps"] + 2.0) < 1e-6,
           f"impact_bps ≈ -2 (got {obs['impact_bps']:.6f})")

    # Verify the row landed
    cur = journal.conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM slippage_attribution_fills"
    )
    _check(int(cur.fetchone()["n"]) == 1,
           "row journaled once")


def test_observer_no_book_path() -> None:
    _section("observer — NO_BOOK path")
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(journal=journal)
    request = {
        "client_order_id": "coid-obs-2",
        "symbol": "BTCUSDT", "side": "SELL", "qty": 0.5,
        "expected_price": 2000.0,
    }
    ack = {"price": 1999.5, "venue": "binance_usdt_futures"}
    result = classifier.on_fill(request, ack, journal, ts_ns=1)
    obs = result.observation
    _check(obs["classification"] == NO_BOOK,
           f"NO_BOOK (got {obs['classification']})")
    _check(obs["spread_cost_bps"] == 0.0, "spread cost zero on NO_BOOK")
    # SELL @ 1999.5 vs expected 2000 → trader paid 2.5 bps (adverse).
    _check(abs(obs["total_slippage_bps"] + 2.5) < 1e-6,
           "total still reported even without book")


def test_observer_no_arrival_path() -> None:
    _section("observer — NO_ARRIVAL path (strategy mis-config)")
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(journal=journal)
    request = {
        "client_order_id": "coid-obs-3",
        "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01,
        # No expected_price, no mark_price, no arrival_mid
        "arrival_bid": 99.95, "arrival_ask": 100.05,
    }
    ack = {"price": 100.05, "venue": "binance_usdt_futures"}
    result = classifier.on_fill(request, ack, journal, ts_ns=1)
    obs = result.observation
    _check(obs["classification"] == "NO_ARRIVAL",
           f"NO_ARRIVAL (got {obs['classification']})")


def test_observer_warn_and_recover() -> None:
    _section("observer — WARN / RECOVERED hysteresis")
    import time as _t
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    # Tight thresholds so the WARN fires fast.
    classifier = SlippageAttributionClassifier(
        journal=journal,
        thresholds=AttributionThresholds(
            window_s=600.0, impact_warn_bps=2.0,
            impact_hysteresis_bps=0.5, dominance_fraction=0.5,
        ),
    )
    # Use current-time ts_ns so the window-pruning does not kick
    # out the early fills before the threshold is crossed.
    base_ns = _t.time_ns()
    warn_fired = False
    for i in range(5):
        request = {
            "client_order_id": f"coid-warn-{i}",
            "symbol": "BTCUSDT", "side": "SELL", "qty": 0.01,
            "expected_price": 100.0, "arrival_bid": 99.95,
            "arrival_ask": 100.05,
        }
        ack = {"price": 99.85, "venue": "binance_usdt_futures"}  # -15 bps
        result = classifier.on_fill(
            request, ack, journal, ts_ns=base_ns + i * 1_000_000,
        )
        obs = result.observation
        if obs and "slippage_attribution_warn" in obs:
            _check(
                obs["slippage_attribution_warn"]["severity"] == "WARN",
                f"WARN fired on fill {i} "
                f"(severity={obs['slippage_attribution_warn']['severity']})"
            )
            warn_fired = True
            break
    _check(warn_fired, "WARN fires on first adverse fill")
    # Now feed an improving fill → RECOVERED (mean climbs above
    # recover_threshold = -(2.0 - 0.5) = -1.5 bps).
    request = {
        "client_order_id": "coid-imp",
        "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01,
        "expected_price": 100.0, "arrival_bid": 99.95,
        "arrival_ask": 100.05,
    }
    ack = {"price": 99.90, "venue": "binance_usdt_futures"}  # +10 bps impact
    result = classifier.on_fill(
        request, ack, journal, ts_ns=base_ns + 100_000_000,
    )
    obs = result.observation
    _check(
        obs and "slippage_attribution_warn" in obs
        and obs["slippage_attribution_warn"]["severity"] == "RECOVERED",
        f"RECOVERED fires on improving fill "
        f"(got {obs.get('slippage_attribution_warn') if obs else 'None'})"
    )


def test_observer_idempotent_on_client_order_id() -> None:
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(journal=journal)
    request = {
        "client_order_id": "coid-dup",
        "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01,
        "expected_price": 100.0, "arrival_bid": 99.95,
        "arrival_ask": 100.05,
    }
    ack = {"price": 100.07, "venue": "v"}
    classifier.on_fill(request, ack, journal, ts_ns=1)
    classifier.on_fill(request, ack, journal, ts_ns=2)
    cur = journal.conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM slippage_attribution_fills"
    )
    _check(int(cur.fetchone()["n"]) == 1,
           "duplicate coid → single row (UNIQUE(client_order_id))")


def test_observer_recover_rebuilds_window() -> None:
    import time as _t
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(
        journal=journal,
        thresholds=AttributionThresholds(window_s=60.0, impact_warn_bps=2.0),
    )
    base_ns = _t.time_ns()
    for i in range(3):
        request = {
            "client_order_id": f"coid-rec-{i}",
            "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01,
            "expected_price": 100.0, "arrival_bid": 99.95,
            "arrival_ask": 100.05,
            "submit_ts_ns": base_ns + i * 1_000_000,
        }
        ack = {"price": 100.07, "venue": "v"}
        classifier.on_fill(
            request, ack, journal, ts_ns=base_ns + i * 1_000_000,
        )
    # Cold-start a fresh observer and rebuild from the shared journal.
    fresh = SlippageAttributionClassifier(
        journal=journal,
        thresholds=AttributionThresholds(window_s=60.0, impact_warn_bps=2.0),
    )
    n = fresh.recover()
    _check(n == 3, f"recover scanned 3 rows (got {n})")
    snap = fresh.snapshot()
    _check("BTCUSDT" in snap, "snapshot has BTCUSDT")
    if "BTCUSDT" in snap:
        _check(snap["BTCUSDT"]["n"] == 3,
               f"window n=3 (got {snap['BTCUSDT']['n']})")
    else:
        _check(False, "window n=3 (symbol absent)")


# ---------- 9. Cold-path aggregator ----------

def test_aggregator_compute_day_round_trip() -> None:
    _section("aggregator — compute_day + record + fetch")
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(journal=journal)
    # 4 fills across 2 venues, all on 2026-07-25 UTC
    base_ns = day_utc_bounds("2026-07-25")[0] + 3_600_000_000_000  # +1h
    fills = [
        ("a", "BTCUSDT", "BUY", 100.0, 100.07, "v1"),
        ("b", "BTCUSDT", "SELL", 100.0, 99.93, "v1"),
        ("c", "ETHUSDT", "BUY", 2000.0, 2002.0, "v2"),
        ("d", "ETHUSDT", "SELL", 2000.0, 1999.5, "v2"),
    ]
    for i, (coid, sym, side, ep, fp, ven) in enumerate(fills):
        request = {
            "client_order_id": coid, "symbol": sym, "side": side,
            "qty": 0.01, "expected_price": ep,
            "arrival_bid": ep * 0.9995, "arrival_ask": ep * 1.0005,
            "submit_ts_ns": base_ns + i * 1_000_000_000,
        }
        ack = {"price": fp, "venue": ven}
        classifier.on_fill(request, ack, journal, base_ns + i * 1_000_000_000)

    report = SlippageAttributionReport(journal=journal, min_sample=2)
    day = report.compute_day("2026-07-25", now_ns=base_ns + 100_000_000_000)
    _check(day.n_fills == 4, f"n_fills=4 (got {day.n_fills})")
    _check(day.n_fills_with_book == 4,
           f"n_fills_with_book=4 (got {day.n_fills_with_book})")
    _check(day.stable, "stable (n_fills_with_book=4 >= min_sample=2)")
    _check(day.mean_total_slippage_bps < 0, "mean_total negative")

    row_id = report.record(day)
    _check(row_id > 0, f"record returns positive row id (got {row_id})")

    fetched = report.fetch("2026-07-25")
    _check(fetched is not None, "fetch returns the row")
    _check(fetched.day_utc == "2026-07-25",
           f"day_utc roundtrips (got {fetched.day_utc})")
    _check(fetched.n_fills == day.n_fills,
           f"n_fills roundtrips (got {fetched.n_fills})")

    # Re-record → idempotent
    report.record(day)
    cur = journal.conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM slippage_attribution_daily_reports "
        "WHERE day_utc = ?", ("2026-07-25",)
    )
    _check(int(cur.fetchone()["n"]) == 1,
           "re-record is idempotent (UNIQUE(day_utc))")


def test_aggregator_handles_empty_day() -> None:
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    report = SlippageAttributionReport(journal=journal, min_sample=5)
    day = report.compute_day("2026-07-25", now_ns=1)
    _check(day.n_fills == 0, "empty n_fills")
    _check(not day.stable, "empty day is not stable")
    report.record(day)
    fetched = report.fetch("2026-07-25")
    _check(fetched is not None, "empty day still persisted")


def test_fetch_missing_day_returns_none() -> None:
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    report = SlippageAttributionReport(journal=journal)
    _check(report.fetch("1999-01-01") is None,
           "fetch of unrecorded day → None")


def test_aggregator_min_sample_validation() -> None:
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    try:
        SlippageAttributionReport(journal=journal, min_sample=-1)
        _check(False, "rejects negative min_sample")
    except ValueError:
        _check(True, "rejects negative min_sample")


def test_attribution_thresholds_validation() -> None:
    try:
        AttributionThresholds(impact_hysteresis_bps=10, impact_warn_bps=5)
        _check(False, "rejects hysteresis > warn")
    except ValueError:
        _check(True, "rejects hysteresis > warn")
    try:
        AttributionThresholds(dominance_fraction=1.5)
        _check(False, "rejects dominance_fraction > 1")
    except ValueError:
        _check(True, "rejects dominance_fraction > 1")
    try:
        AttributionThresholds(impact_warn_bps=-1)
        _check(False, "rejects negative warn")
    except ValueError:
        _check(True, "rejects negative warn")


def test_attribute_fills_returns_list() -> None:
    rows = attribute_fills([
        FillRecord(
            timestamp=1, side="BUY", symbol="BTCUSDT",
            expected_price=100.0, fill_price=100.07, quantity=0.01,
            arrival_bid=99.95, arrival_ask=100.05, client_order_id="x",
        ),
    ])
    _check(len(rows) == 1 and rows[0].classification == SPREAD,
           f"attribute_fills returns list of AttributionRow (got {rows[0].classification})")


def test_observer_bad_input_surfaced_not_raised() -> None:
    _section("observer — malformed input handled, never raises")
    journal = OrderJournal(":memory:")
    bootstrap_journal(journal)
    classifier = SlippageAttributionClassifier(journal=journal)
    # qty=0 → raises inside attribute_fill, observer surfaces via observation
    request = {
        "client_order_id": "coid-bad",
        "symbol": "BTCUSDT", "side": "BUY", "qty": 0.0,
        "expected_price": 100.0, "arrival_bid": 99.95,
        "arrival_ask": 100.05,
    }
    ack = {"price": 100.07, "venue": "v"}
    try:
        result = classifier.on_fill(request, ack, journal, ts_ns=1)
        _check("_slippage_attribution_error" in (result.observation or {}),
               "bad input → error surfaced in observation, not raised")
    except Exception as e:  # pragma: no cover
        _check(False, f"observer raised on bad input: {e!r}")


# ---------- main ----------

def main() -> None:
    test_half_spread_basic()
    test_half_spread_rejects_invalid()
    test_spread_cost_signs()
    test_spread_cost_case_insensitive()
    test_spread_cost_rejects_garbage()
    test_total_slippage_signs()
    test_total_slippage_rejects_invalid()
    test_attribute_fill_clean_decomposition()
    test_attribute_fill_sell_clean()
    test_attribute_fill_improvement_classified()
    test_attribute_fill_no_book_classified()
    test_attribute_fill_no_arrival_is_observer_only()
    test_attribute_fill_crossed_book_falls_back_to_no_book()
    test_attribute_fill_negative_inputs_rejected()
    test_attribute_fill_additive_identity_holds()
    test_classification_thresholds()
    test_classification_mixed()
    test_aggregate_overall_and_breakdowns()
    test_aggregate_zero_fill_handles_gracefully()
    test_day_utc_bounds_basic()
    test_day_utc_bounds_consistent()
    test_observer_round_trip_basic()
    test_observer_no_book_path()
    test_observer_no_arrival_path()
    test_observer_warn_and_recover()
    test_observer_idempotent_on_client_order_id()
    test_observer_recover_rebuilds_window()
    test_aggregator_compute_day_round_trip()
    test_aggregator_handles_empty_day()
    test_fetch_missing_day_returns_none()
    test_aggregator_min_sample_validation()
    test_attribution_thresholds_validation()
    test_attribute_fills_returns_list()
    test_observer_bad_input_surfaced_not_raised()

    print()
    print(f"=== {N_PASS} passed, {N_FAIL} failed ===")
    sys.exit(0 if N_FAIL == 0 else 1)


if __name__ == "__main__":
    main()