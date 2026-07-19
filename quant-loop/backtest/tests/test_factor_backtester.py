"""Unit tests for the canonical cost assembly (SMA-34967).

Run: pytest quant-loop/backtest/tests/test_factor_backtester.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

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
