"""Tests for the Lighter zero-fee simulation adapter."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_QL = str(Path(__file__).resolve().parents[2])
if _QL not in sys.path:
    sys.path.insert(0, _QL)

from _shared.execution.lighter_adapter import (
    LighterAdapter,
    LighterConfig,
    LatencyFill,
    lighter_venue,
    lighter_cost_model,
)
from _shared.execution.cost_model import Venue


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestLighterConfig:
    def test_defaults_are_zero_fee(self):
        c = LighterConfig()
        assert c.maker_fee_bps == 0.0
        assert c.taker_fee_bps == 0.0

    def test_defaults_match_protocol_latency(self):
        """Lighter Standard: maker 200ms, taker 300ms."""
        c = LighterConfig()
        assert c.maker_latency_ms == 200.0
        assert c.taker_latency_ms == 300.0
        assert c.cancel_latency_ms == 200.0

    def test_is_frozen(self):
        c = LighterConfig()
        with pytest.raises(Exception):
            c.maker_fee_bps = 5.0  # type: ignore


# ---------------------------------------------------------------------------
# Fee economics
# ---------------------------------------------------------------------------

class TestFeeEconomics:
    def test_round_trip_fee_zero(self):
        adapter = LighterAdapter()
        assert adapter.round_trip_fee_bps("taker") == 0.0
        assert adapter.round_trip_fee_bps("maker") == 0.0

    def test_round_trip_fee_nonzero(self):
        adapter = LighterAdapter(LighterConfig(taker_fee_bps=5.0, maker_fee_bps=2.0))
        assert adapter.round_trip_fee_bps("taker") == 10.0
        assert adapter.round_trip_fee_bps("maker") == 4.0

    def test_fee_bps_per_side(self):
        adapter = LighterAdapter(LighterConfig(taker_fee_bps=4.0, maker_fee_bps=1.0))
        assert adapter.fee_bps("taker") == 4.0
        assert adapter.fee_bps("maker") == 1.0

    def test_fee_bps_invalid_side(self):
        adapter = LighterAdapter()
        with pytest.raises(ValueError):
            adapter.fee_bps("other")

    def test_round_trip_cost_with_extra_slippage(self):
        adapter = LighterAdapter(LighterConfig(
            taker_fee_bps=0.0, extra_slippage_bps=3.0,
        ))
        # fee 0 + slip 3 bps/side * 2 = 6 bps RT
        assert adapter.round_trip_cost_bps("taker") == pytest.approx(6.0)

    def test_round_trip_cost_with_latency_overlay(self):
        adapter = LighterAdapter(LighterConfig(taker_fee_bps=0.0))
        # 0 fee + 0 slip + 1.5 bps latency = 1.5 bps RT
        assert adapter.round_trip_cost_bps("taker", latency_bps=1.5) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Venue / CostModel integration
# ---------------------------------------------------------------------------

class TestVenueIntegration:
    def test_lighter_venue(self):
        v = lighter_venue()
        assert isinstance(v, Venue)
        assert v.name == "lighter_standard"
        assert v.taker_fee_bps == 0.0
        assert v.maker_fee_bps == 0.0
        assert not v.has_bnb_discount
        assert v.fixed_pure_slippage_bps is None

    def test_lighter_venue_custom_config(self):
        v = lighter_venue(LighterConfig(taker_fee_bps=4.0, maker_fee_bps=2.0))
        assert v.taker_fee_bps == 4.0
        assert v.maker_fee_bps == 2.0

    def test_adapter_venue_property(self):
        adapter = LighterAdapter()
        assert isinstance(adapter.venue, Venue)

    def test_lighter_cost_model(self):
        cm = lighter_cost_model(LighterConfig(taker_fee_bps=4.0, maker_fee_bps=2.0))
        assert cm.commission_bps_per_side == 4.0
        assert cm.taker_fee_bps_per_side == 4.0
        assert cm.maker_fee_bps_per_side == 2.0
        assert cm.slippage_bps_per_side == 0.0


# ---------------------------------------------------------------------------
# Latency simulation
# ---------------------------------------------------------------------------

class TestLatencySlippage:
    """Test latency cost estimation from the trade tape."""

    @pytest.fixture
    def simple_tape(self):
        """A tape where price steps up 1 tick per ms.

        At ts=0:00.000 price=100
        At ts=0:00.100 price=100.1
        At ts=0:00.200 price=100.2
        ...
        Latency slippage for 300ms delay from ts=0:00.000:
          ref=100, fill at 300ms = 100.3 → +3 bp
        """
        times = pd.date_range("2026-01-01 00:00:00", periods=1000, freq="1ms", tz="UTC")
        prices = 100.0 + np.arange(1000) * 0.001  # +0.001 per ms → 1bp/ms
        return pd.DataFrame({"ts": times, "price": prices})

    def test_single_fill_upward_drift(self, simple_tape):
        """300ms taker delay on an uptrending tape → positive slippage (cost for buyer)."""
        adapter = LighterAdapter(LighterConfig(taker_latency_ms=300))
        signal_ts = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
        result = adapter.estimate_latency_slippage(
            signal_ts,
            simple_tape,
            side="taker",
        )
        assert result.filled
        assert result.ref_price == pytest.approx(100.0)
        # Fill at ts + 300ms → price = 100.3
        assert result.fill_price == pytest.approx(100.3, abs=0.01)
        # Slippage: (100.3/100 - 1) * 1e4 = 30 bps
        assert result.latency_bps == pytest.approx(30.0, abs=0.5)

    def test_single_fill_maker_less_delay(self, simple_tape):
        """200ms maker delay → less slippage than 300ms taker."""
        adapter = LighterAdapter()
        signal_ts = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
        maker = adapter.estimate_latency_slippage(signal_ts, simple_tape, side="maker")
        taker = adapter.estimate_latency_slippage(signal_ts, simple_tape, side="taker")
        assert abs(maker.latency_bps) < abs(taker.latency_bps)

    def test_fill_before_any_trade(self, simple_tape):
        """Signal before the first trade → NaN."""
        adapter = LighterAdapter()
        early = pd.Timestamp("2025-12-31 23:59:59", tz="UTC")
        result = adapter.estimate_latency_slippage(early, simple_tape, side="taker")
        assert not result.filled
        assert np.isnan(result.latency_bps)

    def test_fill_after_last_trade(self, simple_tape):
        """Signal + delay beyond the tape → NaN fill."""
        adapter = LighterAdapter(LighterConfig(taker_latency_ms=999_999))
        late = pd.Timestamp("2026-01-01 00:00:01", tz="UTC")  # 1s after start
        result = adapter.estimate_latency_slippage(late, simple_tape, side="taker")
        assert not result.filled

    def test_batch_matches_single(self, simple_tape):
        """Batch results match per-signal single calls."""
        adapter = LighterAdapter()
        signals = pd.DatetimeIndex([
            "2026-01-01 00:00:00",
            "2026-01-01 00:00:00.100",
            "2026-01-01 00:00:00.200",
        ]).tz_localize("UTC")

        batch = adapter.batch_latency_slippage(signals, simple_tape, side="taker")
        assert len(batch) == 3
        for ts in signals:
            single = adapter.estimate_latency_slippage(ts, simple_tape, side="taker")
            batch_val = batch.loc[ts]
            if np.isnan(single.latency_bps):
                assert np.isnan(batch_val)
            else:
                assert batch_val == pytest.approx(single.latency_bps, abs=0.01)


# ---------------------------------------------------------------------------
# Describe
# ---------------------------------------------------------------------------

class TestDescribe:
    def test_describe_zero_fee(self):
        adapter = LighterAdapter()
        desc = adapter.describe()
        assert "Lighter" in desc
        assert "0bp" in desc

    def test_describe_custom(self):
        adapter = LighterAdapter(LighterConfig(taker_fee_bps=5.0))
        desc = adapter.describe()
        assert "5bp" in desc
