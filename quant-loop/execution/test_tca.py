"""Tests for execution/tca.py (E20) — synthetic fills, no I/O.

Run:
    python3 -m pytest execution/test_tca.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest  # noqa: E402

from execution.tca import (  # noqa: E402
    BUCKET_MAKER,
    BUCKET_TAKER,
    TCAFill,
    aggregate,
    arrival_slippage_bps,
    bucket_by_liquidity,
    decompose_fill,
    decompose_fills,
    vwap_slippage_bps,
)

# Arrival book: bid 99.99 / ask 100.01 -> mid 100.00, half-spread 1 bps.
BOOK = {"arrival_bid": 99.99, "arrival_ask": 100.01}


def _fill(**kw):
    base = dict(
        order_id="t1", ts_ns=1, symbol="BTCUSDT", side="BUY",
        qty=1.0, price=100.0, arrival_price=100.0,
    )
    base.update(kw)
    return TCAFill(**base)


# --- benchmark slippage ------------------------------------------------------


def test_arrival_slippage_sign_convention():
    # buy above arrival -> negative (cost); buy below -> improvement
    assert arrival_slippage_bps(
        side="BUY", fill_price=100.02, arrival_price=100.0,
    ) == pytest.approx(-2.0, abs=1e-9)
    assert arrival_slippage_bps(
        side="BUY", fill_price=99.98, arrival_price=100.0,
    ) == pytest.approx(2.0, abs=1e-9)
    # sell is mirrored
    assert arrival_slippage_bps(
        side="SELL", fill_price=100.02, arrival_price=100.0,
    ) == pytest.approx(2.0, abs=1e-9)
    assert arrival_slippage_bps(
        side="SELL", fill_price=99.98, arrival_price=100.0,
    ) == pytest.approx(-2.0, abs=1e-9)


def test_vwap_slippage_against_interval_benchmark():
    # fill below interval VWAP on a buy beats the benchmark
    assert vwap_slippage_bps(
        side="BUY", fill_price=100.0, interval_vwap=100.05,
    ) > 0
    assert vwap_slippage_bps(
        side="SELL", fill_price=100.0, interval_vwap=100.05,
    ) < 0


def test_benchmark_validation():
    with pytest.raises(ValueError):
        arrival_slippage_bps(side="LONG", fill_price=1.0,
                             arrival_price=1.0)
    with pytest.raises(ValueError):
        arrival_slippage_bps(side="BUY", fill_price=0.0,
                             arrival_price=1.0)
    with pytest.raises(ValueError):
        vwap_slippage_bps(side="BUY", fill_price=1.0,
                          interval_vwap=float("nan"))


# --- decomposition -------------------------------------------------------------


def test_taker_pays_half_spread_and_impact_is_remainder():
    # buy at 100.03 vs arrival mid 100.00: total = -3 bps,
    # spread leg = -1 bps (half-spread paid), impact = -2 bps
    row = decompose_fill(_fill(price=100.03, **BOOK))
    assert row.arrival_slippage_bps == pytest.approx(-3.0, abs=1e-9)
    assert row.spread_cost_bps == pytest.approx(-1.0, abs=1e-9)
    assert row.impact_bps == pytest.approx(-2.0, abs=1e-9)
    assert row.residual_bps == 0.0


def test_maker_earns_half_spread():
    # passive buy filled at the bid 99.99 vs arrival mid 100.00:
    # total = +1 bps, spread leg = +1 bps (earned), impact = 0
    row = decompose_fill(_fill(price=99.99, is_maker=True, **BOOK))
    assert row.spread_cost_bps == pytest.approx(1.0, abs=1e-9)
    assert row.impact_bps == pytest.approx(0.0, abs=1e-9)
    assert row.arrival_slippage_bps == pytest.approx(1.0, abs=1e-9)


def test_additive_identity_arrival_benchmark():
    for price, maker in ((100.03, False), (99.99, True), (100.0, False)):
        row = decompose_fill(_fill(price=price, is_maker=maker, **BOOK))
        assert row.spread_cost_bps + row.impact_bps == pytest.approx(
            row.arrival_slippage_bps, abs=1e-9,
        )


def test_vwap_residual_is_timing_drift_and_identity_holds():
    # market drifted up while we worked: interval VWAP 100.05,
    # fill at 100.03 -> arrival slip -3 bps, vwap slip +2 bps,
    # residual (drift) = +5 bps; legs sum to the vwap benchmark
    row = decompose_fill(_fill(
        price=100.03, interval_vwap=100.05, **BOOK,
    ))
    assert row.vwap_slippage_bps == pytest.approx(2.0, abs=1e-2)
    assert row.residual_bps == pytest.approx(
        row.vwap_slippage_bps - row.arrival_slippage_bps, abs=1e-9,
    )
    assert (row.spread_cost_bps + row.impact_bps
            + row.residual_bps) == pytest.approx(
        row.vwap_slippage_bps, abs=1e-9,
    )


def test_no_book_attributes_everything_to_impact():
    row = decompose_fill(_fill(price=100.03))
    assert row.spread_cost_bps == 0.0
    assert row.impact_bps == pytest.approx(-3.0, abs=1e-9)
    assert row.spread_cost_bps + row.impact_bps == pytest.approx(
        row.arrival_slippage_bps, abs=1e-9,
    )


def test_decompose_validation():
    with pytest.raises(ValueError):
        decompose_fill(_fill(qty=0.0))
    with pytest.raises(ValueError):
        decompose_fill(_fill(price=-1.0))
    with pytest.raises(ValueError):
        # crossed book
        decompose_fill(_fill(arrival_bid=100.01, arrival_ask=99.99))


# --- bucketing -------------------------------------------------------------


def _rows():
    fills = [
        # makers: two passive fills that earn the spread
        _fill(order_id="m1", price=99.99, is_maker=True, **BOOK),
        _fill(order_id="m2", price=99.99, is_maker=True, qty=3.0, **BOOK),
        # takers: one cheap, one expensive
        _fill(order_id="t1", price=100.02, **BOOK),
        _fill(order_id="t2", price=100.06, **BOOK),
    ]
    return decompose_fills(fills)


def test_bucket_by_liquidity_splits_rows():
    maker, taker = bucket_by_liquidity(_rows())
    assert maker.bucket == BUCKET_MAKER
    assert taker.bucket == BUCKET_TAKER
    assert maker.n_fills == 2 and taker.n_fills == 2
    assert maker.total_qty == pytest.approx(4.0)
    # makers earn on average; takers pay on average
    assert maker.mean_arrival_bps > 0
    assert taker.mean_arrival_bps < 0
    assert maker.mean_spread_cost_bps > 0
    assert taker.mean_spread_cost_bps < 0
    # qty-weighted maker bucket weights the 3-unit fill
    assert maker.qty_weighted_arrival_bps == pytest.approx(
        maker.mean_arrival_bps, abs=1e-9,
    )  # identical rows -> weighted == plain mean


def test_p90_cost_is_unsigned_and_extreme():
    _, taker = bucket_by_liquidity(_rows())
    # taker costs: +1 bps and +5 bps -> p90 near the 5 bps tail
    assert taker.p90_cost_bps > 4.0


def test_aggregate_report_shape():
    report = aggregate(_rows())
    d = report.to_dict()
    assert set(d) == {"overall", "maker", "taker"}
    assert report.overall.n_fills == 4
    assert report.maker.n_fills + report.taker.n_fills == 4
    assert report.overall.mean_vwap_bps is None  # no VWAP supplied


def test_aggregate_with_vwap_populates_mean_vwap():
    rows = decompose_fills([
        _fill(order_id="v1", price=100.03, interval_vwap=100.05, **BOOK),
        _fill(order_id="v2", price=100.01, interval_vwap=100.05,
              is_maker=True, **BOOK),
    ])
    report = aggregate(rows)
    assert report.overall.mean_vwap_bps is not None
    assert report.overall.mean_residual_bps > 0  # market drifted up


def test_empty_rows_yield_nan_bucket():
    report = aggregate([])
    assert report.overall.n_fills == 0
    assert report.maker.n_fills == 0
    assert report.taker.n_fills == 0
    assert report.overall.mean_vwap_bps is None
