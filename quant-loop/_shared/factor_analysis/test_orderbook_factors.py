"""pytest tests for _shared/factor_analysis/orderbook_factors.py.

Five+ tests per factor: formula correctness on hand-computed ladders,
degenerate-row handling (NaN, no fabricated zeros), causality (a row never
sees future rows), registry integration, and parameter binding.

Run:

    pytest _shared/factor_analysis/test_orderbook_factors.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _shared.factor_analysis import orderbook_factors as obf  # noqa: E402
from _shared.strategy_kit.factor_library import (  # noqa: E402
    compute_factor,
    get_factor_spec,
    list_factors,
)


def _snap(rows: list[dict]) -> pd.DataFrame:
    """Build a snapshot frame; missing levels default to 0 (empty)."""
    cols = [f"{s}_{k}{i}" for i in range(1, 6) for s in ("bid", "ask") for k in ("p", "q")]
    data = {c: [0.0] * len(rows) for c in cols}
    data["ts_ns"] = list(range(1_000_000_000, 1_000_000_000 + len(rows)))
    for r, row in enumerate(rows):
        for k, v in row.items():
            data[k][r] = float(v)
    return pd.DataFrame(data)


def _uniform_ladder(bid_p1=99.0, ask_p1=101.0, bid_q=10.0, ask_q=10.0,
                    n=5) -> dict:
    """Symmetric ladder: levels 1 bp-apart steps of 1% around the top."""
    row = {}
    for i in range(1, n + 1):
        row[f"bid_p{i}"] = bid_p1 - (i - 1) * 1.0
        row[f"bid_q{i}"] = bid_q
        row[f"ask_p{i}"] = ask_p1 + (i - 1) * 1.0
        row[f"ask_q{i}"] = ask_q
    return row


# ---------------------------------------------------------------------------
# 1. book_imbalance
# ---------------------------------------------------------------------------

class TestBookImbalance:
    def test_symmetric_book_is_zero(self):
        df = _snap([_uniform_ladder()])
        out = obf.book_imbalance(df, depth_bp=500.0)
        assert out.iloc[0] == pytest.approx(0.0)

    def test_bid_heavy_book_positive(self):
        row = _uniform_ladder(bid_q=30.0, ask_q=10.0)
        out = obf.book_imbalance(_snap([row]), depth_bp=500.0)
        assert out.iloc[0] == pytest.approx((30 * 5 - 10 * 5) / (30 * 5 + 10 * 5))

    def test_ask_heavy_book_negative(self):
        row = _uniform_ladder(bid_q=10.0, ask_q=30.0)
        out = obf.book_imbalance(_snap([row]), depth_bp=500.0)
        assert out.iloc[0] == pytest.approx(-0.5)

    def test_depth_band_excludes_far_levels(self):
        # mid = 100; 10 bp band -> bids >= 99.9, asks <= 100.1.
        # Only the top level (99.0 -> dist 1% = 100bp) is outside a 50bp band.
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        out = obf.book_imbalance(_snap([row]), depth_bp=50.0)
        # bid levels inside 50bp of mid: px >= 99.95 -> none (top bid 99.0)
        assert np.isnan(out.iloc[0])

    def test_band_counts_only_near_levels(self):
        # top bid 99.9, top ask 100.1, mid 100; 20bp band keeps only level 1
        row = _uniform_ladder(bid_p1=99.9, ask_p1=100.1, bid_q=10.0, ask_q=5.0)
        for i in range(2, 6):
            row[f"bid_p{i}"] = 99.9 - i  # far away -> excluded
            row[f"ask_p{i}"] = 100.1 + i
        out = obf.book_imbalance(_snap([row]), depth_bp=20.0)
        assert out.iloc[0] == pytest.approx((10.0 - 5.0) / 15.0)

    def test_empty_book_is_nan(self):
        out = obf.book_imbalance(_snap([{}]), depth_bp=10.0)
        assert np.isnan(out.iloc[0])

    def test_bounds(self):
        rows = [_uniform_ladder(bid_q=q1, ask_q=q2)
                for q1, q2 in [(50, 1), (1, 50), (10, 10), (30, 20)]]
        out = obf.book_imbalance(_snap(rows), depth_bp=500.0)
        assert ((out >= -1.0) & (out <= 1.0)).all()

    def test_causality_rowwise_independent(self):
        rows = [_uniform_ladder(bid_q=30.0, ask_q=10.0), _uniform_ladder()]
        full = obf.book_imbalance(_snap(rows), depth_bp=500.0)
        head = obf.book_imbalance(_snap(rows[:1]), depth_bp=500.0)
        assert full.iloc[0] == head.iloc[0]  # row 0 unaffected by row 1


# ---------------------------------------------------------------------------
# 2. microprice
# ---------------------------------------------------------------------------

class TestMicroprice:
    def test_equal_sizes_zero_deviation(self):
        row = _uniform_ladder(bid_p1=99.0, ask_p1=101.0, bid_q=10.0, ask_q=10.0)
        out = obf.microprice(_snap([row]))
        assert out.iloc[0] == pytest.approx(0.0, abs=1e-9)

    def test_heavy_bid_pulls_above_mid(self):
        # bid 99 x 30, ask 101 x 10: mp = (99*10 + 101*30)/40 = 100.5
        row = _uniform_ladder(bid_p1=99.0, ask_p1=101.0, bid_q=30.0, ask_q=10.0)
        out = obf.microprice(_snap([row]))
        expected_bp = (100.5 - 100.0) / 100.0 * 1e4
        assert out.iloc[0] == pytest.approx(expected_bp)
        assert out.iloc[0] > 0

    def test_heavy_ask_pulls_below_mid(self):
        row = _uniform_ladder(bid_p1=99.0, ask_p1=101.0, bid_q=10.0, ask_q=30.0)
        out = obf.microprice(_snap([row]))
        assert out.iloc[0] == pytest.approx((99.5 - 100.0) / 100.0 * 1e4)
        assert out.iloc[0] < 0

    def test_zero_quantities_nan(self):
        row = _uniform_ladder(bid_q=0.0, ask_q=0.0)
        out = obf.microprice(_snap([row]))
        assert np.isnan(out.iloc[0])

    def test_uses_only_top_level(self):
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        row["bid_q2"] = 10_000.0  # huge level 2 must not matter
        out = obf.microprice(_snap([row]))
        assert out.iloc[0] == pytest.approx(0.0, abs=1e-9)

    def test_causality(self):
        rows = [_uniform_ladder(bid_q=30.0, ask_q=10.0),
                _uniform_ladder(bid_q=10.0, ask_q=30.0)]
        full = obf.microprice(_snap(rows))
        head = obf.microprice(_snap(rows[:1]))
        assert full.iloc[0] == head.iloc[0]


# ---------------------------------------------------------------------------
# 3. depth_slope
# ---------------------------------------------------------------------------

class TestDepthSlope:
    def test_symmetric_book_zero(self):
        out = obf.depth_slope(_snap([_uniform_ladder()]), depth_bp=500.0)
        assert out.iloc[0] == pytest.approx(0.0)

    def test_bids_closer_positive(self):
        # bids hug the mid (99.9, 99.8...), asks far (100.5, 101.5...)
        row = {}
        for i in range(1, 6):
            row[f"bid_p{i}"] = 100.0 - i * 0.1
            row[f"bid_q{i}"] = 10.0
            row[f"ask_p{i}"] = 100.0 + i * 0.5
            row[f"ask_q{i}"] = 10.0
        out = obf.depth_slope(_snap([row]), depth_bp=500.0)
        assert out.iloc[0] > 0

    def test_asks_closer_negative(self):
        row = {}
        for i in range(1, 6):
            row[f"bid_p{i}"] = 100.0 - i * 0.5
            row[f"bid_q{i}"] = 10.0
            row[f"ask_p{i}"] = 100.0 + i * 0.1
            row[f"ask_q{i}"] = 10.0
        out = obf.depth_slope(_snap([row]), depth_bp=500.0)
        assert out.iloc[0] < 0

    def test_qty_weighting(self):
        # single level each side, equal distance -> 0; heavier far level on
        # bid side pushes bid mean distance up -> factor negative
        row = _uniform_ladder(bid_p1=99.0, ask_p1=101.0, bid_q=10.0, ask_q=10.0)
        out = obf.depth_slope(_snap([row]), depth_bp=500.0)
        assert out.iloc[0] == pytest.approx(0.0)
        row2 = dict(row)
        row2["bid_q5"] = 100.0  # far bid level dominates the mean distance
        out2 = obf.depth_slope(_snap([row2]), depth_bp=500.0)
        assert out2.iloc[0] < 0

    def test_empty_side_nan(self):
        row = _uniform_ladder(ask_q=0.0)  # ask prices exist but qty 0
        out = obf.depth_slope(_snap([row]), depth_bp=500.0)
        assert np.isnan(out.iloc[0])

    def test_band_filter(self):
        # nothing within 1 bp of mid -> NaN
        out = obf.depth_slope(_snap([_uniform_ladder()]), depth_bp=1.0)
        assert np.isnan(out.iloc[0])

    def test_causality(self):
        rows = [_uniform_ladder(), _uniform_ladder(bid_q=100.0)]
        full = obf.depth_slope(_snap(rows), depth_bp=500.0)
        head = obf.depth_slope(_snap(rows[:1]), depth_bp=500.0)
        assert full.iloc[0] == head.iloc[0]


# ---------------------------------------------------------------------------
# 4. wall_pressure
# ---------------------------------------------------------------------------

class TestWallPressure:
    def test_no_walls_zero(self):
        out = obf.wall_pressure(_snap([_uniform_ladder()]), threshold_x=3.0)
        assert out.iloc[0] == pytest.approx(0.0)

    def test_bid_wall_positive(self):
        # mean = 10; bid wall 50 >= 3*10 -> strength 50; pressure = 50/10 = 5
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        row["bid_q3"] = 50.0
        out = obf.wall_pressure(_snap([row]), threshold_x=3.0)
        # mean over populated levels: (9*10 + 50)/10 = 14; 50 >= 42 -> wall
        assert out.iloc[0] == pytest.approx(50.0 / 14.0)

    def test_ask_wall_negative(self):
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        row["ask_q2"] = 50.0
        out = obf.wall_pressure(_snap([row]), threshold_x=3.0)
        assert out.iloc[0] == pytest.approx(-50.0 / 14.0)

    def test_both_walls_cancel(self):
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        row["bid_q3"] = 50.0
        row["ask_q4"] = 50.0
        out = obf.wall_pressure(_snap([row]), threshold_x=3.0)
        assert out.iloc[0] == pytest.approx(0.0)

    def test_threshold_x_scales_walls(self):
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        row["bid_q3"] = 25.0  # 25 >= 2*mean(=10.5)? 25>=21 yes; >= 3*10.5=31.5 no
        out2 = obf.wall_pressure(_snap([row]), threshold_x=2.0)
        out3 = obf.wall_pressure(_snap([row]), threshold_x=3.0)
        assert out2.iloc[0] > 0
        assert out3.iloc[0] == pytest.approx(0.0)

    def test_largest_wall_wins(self):
        row = _uniform_ladder(bid_q=10.0, ask_q=10.0)
        row["bid_q2"] = 40.0
        row["bid_q4"] = 60.0
        out = obf.wall_pressure(_snap([row]), threshold_x=2.0)
        mean = (8 * 10 + 40 + 60) / 10
        assert out.iloc[0] == pytest.approx(60.0 / mean)

    def test_empty_book_nan(self):
        out = obf.wall_pressure(_snap([{}]), threshold_x=3.0)
        assert np.isnan(out.iloc[0])


# ---------------------------------------------------------------------------
# 5. ofi_bars
# ---------------------------------------------------------------------------

class TestOfiBars:
    def test_first_row_nan(self):
        out = obf.ofi_bars(_snap([_uniform_ladder()]), window=5)
        assert np.isnan(out.iloc[0])

    def test_size_increase_at_same_price(self):
        rows = [_uniform_ladder(bid_q=10.0), _uniform_ladder(bid_q=15.0)]
        out = obf.ofi_bars(_snap(rows), window=5)
        # e_bid = 15-10 = 5; e_ask = 0 -> ofi = 5
        assert out.iloc[1] == pytest.approx(5.0)

    def test_bid_improvement_adds_full_size(self):
        rows = [_uniform_ladder(bid_q=10.0, ask_q=10.0)]
        rows.append(_uniform_ladder(bid_p1=99.5, ask_p1=101.0, bid_q=12.0, ask_q=10.0))
        out = obf.ofi_bars(_snap(rows), window=5)
        # bid improved: e_bid = +12; ask unchanged price: e_ask = 0 -> ofi 12
        assert out.iloc[1] == pytest.approx(12.0)

    def test_bid_retreat_subtracts_old_size(self):
        rows = [_uniform_ladder(bid_p1=99.0, bid_q=10.0),
                _uniform_ladder(bid_p1=98.5, bid_q=20.0)]
        out = obf.ofi_bars(_snap(rows), window=5)
        # bid retreated: e_bid = -10 (old size); ask same price: e_ask = 0
        assert out.iloc[1] == pytest.approx(-10.0)

    def test_ask_retreat_is_bullish(self):
        rows = [_uniform_ladder(ask_p1=101.0, ask_q=8.0),
                _uniform_ladder(ask_p1=101.5, ask_q=8.0)]
        out = obf.ofi_bars(_snap(rows), window=5)
        # ask retreated: e_ask = -8 -> ofi = 0 - (-8) = +8
        assert out.iloc[1] == pytest.approx(8.0)

    def test_rolling_window_sums_then_forgets(self):
        rows = [_uniform_ladder(bid_q=q) for q in (10, 15, 20, 25)]
        out = obf.ofi_bars(_snap(rows), window=2)
        # ofi events: nan, +5, +5, +5 -> rolling-2 sums: nan, 5, 10, 10
        assert np.isnan(out.iloc[0])
        assert out.iloc[1] == pytest.approx(5.0)
        assert out.iloc[2] == pytest.approx(10.0)
        assert out.iloc[3] == pytest.approx(10.0)

    def test_causality_future_rows_do_not_change_past(self):
        rows = [_uniform_ladder(bid_q=10.0), _uniform_ladder(bid_q=15.0),
                _uniform_ladder(bid_q=100.0)]
        full = obf.ofi_bars(_snap(rows), window=3)
        head = obf.ofi_bars(_snap(rows[:2]), window=3)
        assert full.iloc[1] == head.iloc[1]


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    NAMES = ("book_imbalance", "microprice", "depth_slope",
             "wall_pressure", "ofi_bars")

    def test_all_five_registered(self):
        specs = list_factors()
        for name in self.NAMES:
            assert name in specs

    def test_direction_long_high(self):
        for name in self.NAMES:
            assert get_factor_spec(name).direction == +1

    def test_references_cite_papers(self):
        refs = " ".join(get_factor_spec(n).reference for n in self.NAMES)
        assert "Cont" in refs and "Cartea" in refs and "Kwon" in refs

    def test_compute_factor_via_registry(self):
        df = _snap([_uniform_ladder(bid_q=30.0, ask_q=10.0)])
        out = compute_factor("book_imbalance", df, depth_bp=500.0)
        assert out.iloc[0] > 0

    def test_missing_columns_raise(self):
        df = pd.DataFrame({"bid_p1": [1.0]})
        with pytest.raises(ValueError, match="missing columns"):
            compute_factor("book_imbalance", df, depth_bp=10.0)

    def test_param_schema_enforced(self):
        df = _snap([_uniform_ladder()])
        with pytest.raises(ValueError):
            compute_factor("ofi_bars", df, window=-3)

    def test_defaults_via_registry(self):
        df = _snap([_uniform_ladder()])
        out = compute_factor("microprice", df)
        assert out.iloc[0] == pytest.approx(0.0, abs=1e-9)
