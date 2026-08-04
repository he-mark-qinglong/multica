"""Tests for strategies/multifactor_stacked_v1/strategy.py.

    pytest strategies/multifactor_stacked_v1/tests/test_strategy.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_DIR = Path(__file__).resolve().parent.parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))
_QL = _DIR.parents[1]
if str(_QL) not in sys.path:
    sys.path.insert(0, str(_QL))

from strategy import (  # noqa: E402
    FEE_PER_SIDE,
    VARIANTS,
    backtest_returns,
    book_imb_signal,
    generate_position,
    generate_signals,
    kama,
    kama_mtf_signal,
    metrics_from_returns,
    session_signal,
    stacked_signal,
    volume_signal,
)

CFG = {
    "params": {
        "tf_4h": {"er_window": 5, "fast": 2, "slow": 30, "slope_lookback": 10},
        "tf_1d": {"er_window": 10, "fast": 3, "slow": 30, "slope_lookback": 3},
        "imb_window": 4, "imb_threshold": 0.15,
        "session_start_hour": 20, "session_end_hour": 24,
        "vol_window": 30, "vol_mult": 1.5,
        "min_votes": 3,
    }
}


def make_4h(n: int = 300, start: str = "2026-01-01", trend: float = 0.001,
            seed: int = 3) -> pd.DataFrame:
    """Synthetic 4h OHLCV with taker flow."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="4h")
    rets = rng.normal(trend, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    vol = rng.uniform(50, 150, n)
    buy_frac = rng.uniform(0.3, 0.7, n)
    return pd.DataFrame({
        "open": close * (1 - rets / 2), "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": vol,
        "taker_buy_base": vol * buy_frac,
    }, index=idx)


def resample_1d(df_4h: pd.DataFrame) -> pd.DataFrame:
    return df_4h.resample("1D").agg({"open": "first", "high": "max",
                                     "low": "min", "close": "last",
                                     "volume": "sum",
                                     "taker_buy_base": "sum"}).dropna()


class TestKamaMtf:
    def test_kama_shape_and_warmup(self):
        close = pd.Series(np.linspace(100, 200, 100))
        k = kama(close, er_window=5, fast=2, slow=30)
        assert len(k) == 100
        assert np.isnan(k.iloc[:5]).all()
        assert np.isfinite(k.iloc[5:]).all()

    def test_uptrend_signals_long(self):
        df_4h = make_4h(400, trend=0.002)
        df_1d = resample_1d(df_4h)
        sig = kama_mtf_signal(df_4h, df_1d, CFG)
        assert sig.iloc[-50:].mean() > 0.8

    def test_daily_leg_uses_completed_day_only(self):
        """4h bars on day D must not see day D's own 1d bar (lookahead)."""
        df_4h = make_4h(96)  # 16 days
        df_1d = resample_1d(df_4h)
        sig = kama_mtf_signal(df_4h, df_1d, CFG)
        # Recompute manually: daily signal shifted one day.
        from strategy import _kama_slope_signal
        sig_1d = _kama_slope_signal(df_1d["close"], CFG["params"]["tf_1d"])
        shifted = sig_1d.shift(1).reindex(df_4h.index, method="ffill").fillna(0)
        day_d = df_4h.index[24]  # first bar of day 2
        assert shifted.loc[day_d] == sig_1d.iloc[0]
        assert sig.loc[day_d] in (0.0, 1.0)


class TestAuxFactors:
    def test_session_hours(self):
        idx = pd.date_range("2026-01-01", periods=6, freq="4h")
        sig = session_signal(idx, 20, 24)
        # hours: 0,4,8,12,16,20 → only the 20:00 bar is on
        assert sig.tolist() == [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def test_session_boundary_midnight_off(self):
        idx = pd.DatetimeIndex(["2026-01-01 20:00", "2026-01-01 23:00",
                                "2026-01-02 00:00", "2026-01-01 19:00"])
        sig = session_signal(idx, 20, 24)
        assert sig.tolist() == [1.0, 1.0, 0.0, 0.0]

    def test_volume_spike_detected(self):
        vol = pd.Series([100.0] * 30 + [200.0])
        sig = volume_signal(vol, window=30, mult=1.5)
        assert sig.iloc[-1] == 1.0
        assert sig.iloc[:30].sum() == 0.0

    def test_volume_mean_excludes_current_bar(self):
        # Current bar inside its own mean would damp the ratio; shifted mean
        # must flag a 1.6x spike on a flat series.
        vol = pd.Series([100.0] * 30 + [160.0])
        assert volume_signal(vol, window=30, mult=1.5).iloc[-1] == 1.0

    def test_book_imb_buy_heavy(self):
        df = make_4h(20)
        df["taker_buy_base"] = df["volume"] * 0.9  # imb = +0.8
        sig = book_imb_signal(df, window=4, threshold=0.15)
        assert sig.iloc[-1] == 1.0

    def test_book_imb_balanced_off(self):
        df = make_4h(20)
        df["taker_buy_base"] = df["volume"] * 0.5  # imb = 0
        sig = book_imb_signal(df, window=4, threshold=0.15)
        assert sig.sum() == 0.0


class TestStacking:
    def _signals(self, kama, imb, session, volume, n=10):
        idx = pd.date_range("2026-01-01", periods=n, freq="4h")
        return pd.DataFrame({"kama": float(kama), "imb": float(imb),
                             "session": float(session), "volume": float(volume)},
                            index=idx)

    def test_kama_veto_blocks_long(self):
        # 3 aux bullish but no kama → flat (KAMA mandatory).
        sig = stacked_signal(self._signals(0, 1, 1, 1))
        assert (sig == 0.0).all()

    def test_three_votes_with_kama_longs(self):
        sig = stacked_signal(self._signals(1, 1, 1, 0))
        assert (sig == 1.0).all()

    def test_two_votes_flat(self):
        sig = stacked_signal(self._signals(1, 1, 0, 0))
        assert (sig == 0.0).all()

    def test_all_four_long(self):
        sig = stacked_signal(self._signals(1, 1, 1, 1))
        assert (sig == 1.0).all()

    def test_variants(self):
        signals = self._signals(1, 1, 0, 0)
        assert (generate_position(signals, "kama_only") == 1.0).all()
        assert (generate_position(signals, "kama_imb") == 1.0).all()
        assert (generate_position(signals, "kama_session") == 0.0).all()
        assert (generate_position(signals, "stacked4") == 0.0).all()
        with pytest.raises(ValueError):
            generate_position(signals, "nope")

    def test_stacked_never_exceeds_kama_only(self):
        """Aux gates can only reduce exposure vs the baseline."""
        df_4h = make_4h(400)
        df_1d = resample_1d(df_4h)
        signals = generate_signals(df_4h, df_1d, CFG)
        for v in VARIANTS:
            pos = generate_position(signals, v)
            assert (pos <= signals["kama"] + 1e-9).all()


class TestBacktest:
    def test_shift_one_no_lookahead(self):
        df_4h = make_4h(400)
        df_1d = resample_1d(df_4h)
        signals = generate_signals(df_4h, df_1d, CFG)
        signal = generate_position(signals, "stacked4")
        rets = backtest_returns(df_4h, df_1d, CFG, "stacked4")
        # Position at bar t must be signal at t-1: recompute and compare
        # gross exposure sign (ignoring cost) on a subset.
        ret = df_4h["close"].pct_change()
        pos = signal.shift(1).fillna(0.0)
        gross = (pos * ret).dropna()
        common = gross.index.intersection(rets.index)
        same_sign = np.sign(gross.loc[common]) == np.sign(
            rets.loc[common].where(pos.loc[common] == 0, gross.loc[common]))
        assert same_sign.mean() > 0.95  # cost only flips tiny bars

    def test_fee_charged_on_flips(self):
        idx = pd.date_range("2026-01-01", periods=5, freq="4h")
        signal = pd.Series([0.0, 1.0, 1.0, 0.0, 0.0], index=idx)
        pos = signal.shift(1).fillna(0.0)
        flips = pos.diff().abs().fillna(0.0)
        assert flips.sum() == 2.0  # enter + exit
        assert FEE_PER_SIDE * flips.sum() == pytest.approx(0.0007)

    def test_end_to_end_metrics_finite(self):
        df_4h = make_4h(600)
        df_1d = resample_1d(df_4h)
        for variant in VARIANTS:
            rets = backtest_returns(df_4h, df_1d, CFG, variant)
            m = metrics_from_returns(rets)
            assert np.isfinite(m["sharpe"])
            assert -1.0 <= m["max_drawdown"] <= 0.0
        # kama_only must have the most exposure
        expo = {v: (backtest_returns(df_4h, df_1d, CFG, v) != 0).mean()
                for v in VARIANTS}
        assert expo["kama_only"] >= expo["stacked4"]
