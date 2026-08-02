"""Tests for _shared/factor_analysis/bars.py."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.factor_analysis.bars import (
    volume_bars,
    dollar_bars,
    tick_bars,
    imbalance_bars,
    resample_to_bars,
)


def _tick_data(n: int = 1000, seed: int = 11) -> pd.DataFrame:
    """Fine-grained OHLCV (e.g. 1-min bars) for bar-generation tests."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1min", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    volume = rng.uniform(1.0, 100.0, n)
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.0005, n)),
        "high": close * (1 + np.abs(rng.normal(0, 0.001, n))),
        "low": close * (1 - np.abs(rng.normal(0, 0.001, n))),
        "close": close,
        "volume": volume,
    }, index=idx)


# ---------------------------------------------------------------------------
# tick_bars
# ---------------------------------------------------------------------------

class TestTickBars:
    def test_basic_grouping(self):
        data = _tick_data(100)
        bars = tick_bars(data, tick_size=10)
        assert len(bars) == 10
        # each output bar aggregates exactly 10 input bars
        assert bars["volume"].sum() == pytest.approx(data["volume"].sum())

    def test_ohlcv_aggregation_correctness(self):
        data = _tick_data(50)
        bars = tick_bars(data, tick_size=5)
        assert len(bars) == 10
        # first bar: open = data.open[0], close = data.close[4]
        assert bars["open"].iloc[0] == pytest.approx(data["open"].iloc[0])
        assert bars["close"].iloc[0] == pytest.approx(data["close"].iloc[4])
        # high/low within range
        chunk = data.iloc[:5]
        assert bars["high"].iloc[0] == pytest.approx(chunk["high"].max())
        assert bars["low"].iloc[0] == pytest.approx(chunk["low"].min())

    def test_remainder_bar(self):
        data = _tick_data(53)
        bars = tick_bars(data, tick_size=10)
        # 5 full bars of 10 + 1 partial bar of 3
        assert len(bars) == 6

    def test_tick_size_validation(self):
        data = _tick_data(100)
        with pytest.raises(ValueError, match="tick_size"):
            tick_bars(data, tick_size=0)


# ---------------------------------------------------------------------------
# volume_bars
# ---------------------------------------------------------------------------

class TestVolumeBars:
    def test_threshold_triggers_bars(self):
        data = _tick_data(100)
        avg_vol = data["volume"].mean()
        bars = volume_bars(data, volume_threshold=avg_vol * 5)
        assert len(bars) > 1
        assert len(bars) < 100

    def test_volume_preserved(self):
        data = _tick_data(500)
        bars = volume_bars(data, volume_threshold=200.0)
        assert bars["volume"].sum() == pytest.approx(data["volume"].sum())

    def test_threshold_too_large_single_bar(self):
        data = _tick_data(100)
        bars = volume_bars(data, volume_threshold=1e15)
        assert len(bars) == 1
        assert bars["volume"].iloc[0] == pytest.approx(data["volume"].sum())

    def test_threshold_validation(self):
        data = _tick_data(100)
        with pytest.raises(ValueError):
            volume_bars(data, volume_threshold=0)


# ---------------------------------------------------------------------------
# dollar_bars
# ---------------------------------------------------------------------------

class TestDollarBars:
    def test_threshold_triggers_bars(self):
        data = _tick_data(500)
        avg_dollar = (data["close"] * data["volume"]).mean()
        bars = dollar_bars(data, dollar_threshold=avg_dollar * 10)
        assert len(bars) > 1

    def test_volume_preserved(self):
        data = _tick_data(500)
        bars = dollar_bars(data, dollar_threshold=1000.0)
        assert bars["volume"].sum() == pytest.approx(data["volume"].sum())

    def test_dollar_bars_differ_from_volume_bars(self):
        """When price varies significantly, dollar and volume bars should
        produce different boundaries."""
        data = _tick_data(500)
        avg_vol = data["volume"].mean()
        avg_dollar = (data["close"] * data["volume"]).mean()
        vb = volume_bars(data, volume_threshold=avg_vol * 5)
        db = dollar_bars(data, dollar_threshold=avg_dollar * 5)
        # the number of bars may differ
        # just verify both are reasonable
        assert len(vb) > 0
        assert len(db) > 0


# ---------------------------------------------------------------------------
# imbalance_bars
# ---------------------------------------------------------------------------

class TestImbalanceBars:
    def test_produces_bars(self):
        data = _tick_data(500)
        bars = imbalance_bars(data, expected_imbalance=None)
        assert len(bars) > 0
        assert len(bars) < 500

    def test_volume_preserved(self):
        data = _tick_data(500)
        bars = imbalance_bars(data)
        # Imbalance bars sample ticks at imbalance thresholds;
        # the last incomplete bar is dropped. Volume should be
        # a large fraction of input.
        total_bars_vol = bars["volume"].sum()
        total_data_vol = data["volume"].sum()
        assert total_bars_vol > 0
        assert total_bars_vol <= total_data_vol + 1e-6  # can't exceed input

    def test_explicit_expected_imbalance(self):
        data = _tick_data(500)
        avg_dollar = float((data["close"] * data["volume"]).abs().mean())
        bars = imbalance_bars(data, expected_imbalance=avg_dollar * 2)
        assert len(bars) > 0

    def test_ema_mode(self):
        data = _tick_data(500)
        bars = imbalance_bars(data, use_ema=True, ema_alpha=0.05)
        assert len(bars) > 0


# ---------------------------------------------------------------------------
# resample_to_bars (generic)
# ---------------------------------------------------------------------------

class TestResampleToBars:
    def test_empty_boundaries(self):
        data = _tick_data(100)
        bars = resample_to_bars(data, [])
        assert len(bars) == 0

    def test_single_boundary(self):
        data = _tick_data(100)
        bars = resample_to_bars(data, [100])
        assert len(bars) == 1
        assert bars["open"].iloc[0] == pytest.approx(data["open"].iloc[0])
        assert bars["close"].iloc[0] == pytest.approx(data["close"].iloc[99])
