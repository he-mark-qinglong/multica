"""Unit tests for the canonical cost assembly (SMA-34967).

Run: pytest quant-loop/backtest/tests/test_factor_backtester.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.factor_backtester import CostModel  # noqa: E402


class TestBaseline:
    def test_ratified_standard_is_11_per_side_22_round_trip(self):
        m = CostModel.sma34900_baseline()
        assert m.commission_bps_per_side == 4.0
        assert m.slippage_bps_per_side == 7.0
        assert m.per_side_bps == 11.0
        assert m.round_trip_bps == 22.0
        assert math.isclose(m.round_trip_frac, 0.0022)

    def test_plugin_splits_fee_out(self):
        m = CostModel.from_sma34900_plugin(11.0)
        assert m.commission_bps_per_side == 4.0
        assert m.slippage_bps_per_side == 7.0
        assert "sma34900_plugin_fee_split" in m.hazard_flags

    def test_plugin_below_fee_rejected(self):
        with pytest.raises(ValueError):
            CostModel.from_sma34900_plugin(3.0)


class TestDoubleCountGuard:
    def test_plugin_plus_commission_counts_fee_once(self):
        """The SMA-34967 hazard: 11 bps plug-in + 4 bps commission must
        resolve to 22 bps RT, not 30."""
        cfg = {"fees_bps_per_side": 4.0, "slippage_bps_per_side": 11.0}
        m = CostModel.from_config(cfg)
        assert m.per_side_bps == 11.0
        assert m.round_trip_bps == 22.0
        assert "double_count_guarded" in m.hazard_flags

    def test_plugin_plus_commission_alt_key_spellings(self):
        cfg = {"fee_bps_per_fill": 4.0, "slippage_bps_per_fill": 11.0}
        m = CostModel.from_config(cfg)
        assert m.round_trip_bps == 22.0
        assert "double_count_guarded" in m.hazard_flags

    def test_plugin_alone_decomposes_without_double_count_flag(self):
        m = CostModel.from_config({"slippage_bps_per_side": 11.0})
        assert m.commission_bps_per_side == 4.0
        assert m.slippage_bps_per_side == 7.0
        assert m.round_trip_bps == 22.0
        assert "double_count_guarded" not in m.hazard_flags

    def test_explicit_includes_fee_flag(self):
        cfg = {"commission_bps": 4.0, "slippage_bps_per_side": 9.0,
               "slippage_includes_fee": True}
        m = CostModel.from_config(cfg)
        assert m.commission_bps_per_side == 4.0
        assert m.slippage_bps_per_side == 5.0
        assert m.round_trip_bps == 18.0

    def test_guard_never_exceeds_22_round_trip_on_plugin_path(self):
        for fee in (1.0, 4.0, 8.0):
            m = CostModel.from_config(
                {"fees_bps_per_side": fee, "slippage_bps_per_side": 11.0}
            )
            assert m.round_trip_bps == 22.0


class TestPlainWiringUnchanged:
    def test_cycle46_convention_passes_through(self):
        m = CostModel.from_config(
            {"fee_bps_per_fill": 4.0, "slippage_bps_per_fill": 1.0}
        )
        assert m.per_side_bps == 5.0
        assert m.round_trip_bps == 10.0
        assert m.hazard_flags == ()

    def test_decomposed_standard_passes_through_clean(self):
        m = CostModel.from_config(
            {"commission_bps_per_side": 4.0, "slippage_bps_per_side": 7.0}
        )
        assert m.round_trip_bps == 22.0
        assert m.hazard_flags == ()

    def test_missing_cost_keys_default_zero(self):
        m = CostModel.from_config({})
        assert m.round_trip_bps == 0.0


class TestLedgerNote:
    def test_note_format(self):
        m = CostModel.from_config(
            {"fees_bps_per_side": 4.0, "slippage_bps_per_side": 11.0}
        )
        note = m.ledger_note()
        assert "22bps RT" in note
        assert "double_count_guarded" in note


def _flat_curve(n_bars: int = 9, equity: float = 10_000.0):
    """Flat 1h equity curve + constant position, funding every 8h."""
    idx = pd.date_range("2026-07-01", periods=n_bars, freq="1h")
    eq = pd.Series(equity, index=idx)
    pos = pd.Series(1.0, index=idx)  # 1x long
    return idx, eq, pos


class TestMakerTakerCost:
    def test_side_specific_fees(self):
        m = CostModel(
            commission_bps_per_side=4.0,
            slippage_bps_per_side=7.0,
            maker_fee_bps_per_side=2.0,
            taker_fee_bps_per_side=4.0,
        )
        assert math.isclose(m.maker_taker_cost(10_000.0, "maker"), 2.0)
        assert math.isclose(m.maker_taker_cost(10_000.0, "taker"), 4.0)

    def test_fallback_to_uniform_commission(self):
        """Models without the side split keep the old uniform fee."""
        m = CostModel.sma34900_baseline()
        assert math.isclose(m.maker_taker_cost(10_000.0, "taker"), 4.0)
        assert math.isclose(m.maker_taker_cost(10_000.0, "maker"), 4.0)

    def test_partial_split_falls_back_per_side(self):
        m = CostModel(
            commission_bps_per_side=4.0,
            slippage_bps_per_side=7.0,
            maker_fee_bps_per_side=2.0,
        )
        assert math.isclose(m.maker_taker_cost(10_000.0, "maker"), 2.0)
        assert math.isclose(m.maker_taker_cost(10_000.0, "taker"), 4.0)

    def test_invalid_side_rejected(self):
        m = CostModel.sma34900_baseline()
        with pytest.raises(ValueError):
            m.maker_taker_cost(1_000.0, "mid")

    def test_from_config_reads_maker_taker_keys(self):
        m = CostModel.from_config(
            {
                "commission_bps_per_side": 4.0,
                "slippage_bps_per_side": 7.0,
                "maker_fee_bps_per_side": 2.0,
                "taker_fee_bps_per_side": 4.0,
            }
        )
        assert m.maker_fee_bps_per_side == 2.0
        assert m.taker_fee_bps_per_side == 4.0
        assert m.round_trip_bps == 22.0  # ratified total unchanged
        assert "maker/taker fee=2/4bps/side" in m.ledger_note()


class TestFundingCost:
    def test_long_pays_positive_rate(self):
        idx, eq, pos = _flat_curve()
        # 1 bp funding at the 8h settlement (bar index 8 = 08:00).
        fs = pd.Series(0.0001, index=[idx[8]] if len(idx) > 8 else [idx[-1]])
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        # cost = 1.0 * 10000 * 0.0001 = 1.0, applied from settlement onward.
        assert out.iloc[7] == 10_000.0
        assert math.isclose(out.iloc[8], 10_000.0 - 1.0)
        assert math.isclose(out.iloc[-1], 10_000.0 - 1.0)

    def test_short_receives_positive_rate(self):
        idx, eq, pos = _flat_curve()
        pos = -pos  # 1x short
        fs = pd.Series(0.0001, index=[idx[8]])
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        assert math.isclose(out.iloc[-1], 10_000.0 + 1.0)

    def test_settlements_accumulate(self):
        idx, eq, pos = _flat_curve(n_bars=25)
        # Two settlements at 08:00 and 16:00, 1 bp each.
        fs = pd.Series(0.0001, index=[idx[8], idx[16]])
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        assert math.isclose(out.iloc[8], 10_000.0 - 1.0)
        assert math.isclose(out.iloc[15], 10_000.0 - 1.0)
        assert math.isclose(out.iloc[16], 10_000.0 - 2.0)
        assert math.isclose(out.iloc[-1], 10_000.0 - 2.0)

    def test_settlement_between_bars_snaps_to_last_bar(self):
        idx, eq, pos = _flat_curve()
        # Settlement at 08:30 — no bar there; last bar <= t is 08:00 (iloc 8).
        fs = pd.Series(0.0001, index=[idx[8] + pd.Timedelta(minutes=30)])
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        assert math.isclose(out.iloc[8], 10_000.0 - 1.0)

    def test_settlement_before_first_bar_ignored(self):
        idx, eq, pos = _flat_curve()
        fs = pd.Series(0.0001, index=[idx[0] - pd.Timedelta(hours=8)])
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        pd.testing.assert_series_equal(out, eq)

    def test_no_funding_returns_curve_unchanged(self):
        _, eq, pos = _flat_curve()
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos)
        pd.testing.assert_series_equal(out, eq)
        assert out is not eq  # defensive copy

    def test_model_field_used_as_default_series(self):
        idx, eq, pos = _flat_curve()
        fs = pd.Series(0.0001, index=[idx[8]])
        m = CostModel(
            commission_bps_per_side=4.0,
            slippage_bps_per_side=7.0,
            funding_rate_series=fs,
        )
        out = m.apply_funding_cost(eq, pos)
        assert math.isclose(out.iloc[-1], 10_000.0 - 1.0)

    def test_from_config_passes_funding_series_through(self):
        idx, eq, pos = _flat_curve()
        fs = pd.Series(0.0001, index=[idx[8]])
        m = CostModel.from_config(
            {
                "commission_bps_per_side": 4.0,
                "slippage_bps_per_side": 7.0,
                "funding_rate_series": fs,
            }
        )
        assert m.funding_rate_series is fs
        out = m.apply_funding_cost(eq, pos)
        assert math.isclose(out.iloc[-1], 10_000.0 - 1.0)

    def test_unsorted_funding_index_handled(self):
        idx, eq, pos = _flat_curve(n_bars=25)
        fs = pd.Series([0.0001, 0.0001], index=[idx[16], idx[8]])  # unsorted
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        assert math.isclose(out.iloc[-1], 10_000.0 - 2.0)

    def test_scaled_position_scales_cost(self):
        idx, eq, pos = _flat_curve()
        pos = pos * 0.5  # half-size long
        fs = pd.Series(0.0002, index=[idx[8]])
        m = CostModel.sma34900_baseline()
        out = m.apply_funding_cost(eq, pos, fs)
        # 0.5 * 10000 * 0.0002 = 1.0
        assert math.isclose(out.iloc[-1], 10_000.0 - 1.0)


class TestBackwardCompatibility:
    def test_new_fields_do_not_break_equality(self):
        """Extension fields default to unset: old constructions stay equal."""
        a = CostModel(commission_bps_per_side=4.0, slippage_bps_per_side=7.0)
        b = CostModel.sma34900_baseline()
        assert a == b
        assert a.funding_rate_series is None
        assert a.maker_fee_bps_per_side is None
        assert a.taker_fee_bps_per_side is None

    def test_funding_series_excluded_from_equality(self):
        fs = pd.Series(0.0001, index=pd.date_range("2026-07-01", periods=3, freq="8h"))
        a = CostModel(4.0, 7.0, funding_rate_series=fs)
        b = CostModel(4.0, 7.0)
        assert a == b
