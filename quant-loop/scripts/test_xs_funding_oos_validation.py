"""Tests for scripts/xs_funding_oos_validation.py — pure-function core."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import dataclasses

import numpy as np
import pandas as pd
import pytest

import scripts.xs_funding_oos_validation as oos


# ---------------------------------------------------------------------------
# Newey-West / naive t
# ---------------------------------------------------------------------------

class TestNeweyWest:
    def test_lag0_close_to_naive(self):
        rng = np.random.default_rng(7)
        x = rng.normal(0.001, 0.02, size=500)
        t_nw = oos.newey_west_t(x, lag=0)
        t_nv = oos.naive_t(x)
        # lag-0 HAC uses population variance → t_nw = t_nv * sqrt(n/(n-1))
        assert t_nw == pytest.approx(t_nv * np.sqrt(len(x) / (len(x) - 1)), rel=1e-9)

    def test_positive_autocorrelation_shrinks_t(self):
        # Overlapping-window returns: MA(9) of iid increments → strongly
        # autocorrelated; NW t must be materially below the naive t.
        rng = np.random.default_rng(11)
        eps = rng.normal(0.002, 0.01, size=2000)
        x = np.convolve(eps, np.ones(10), mode="valid")
        t_nv = oos.naive_t(x)
        t_nw = oos.newey_west_t(x, lag=18)
        assert t_nv > 2.0  # naive is fooled by the overlap
        assert t_nw < t_nv * 0.6

    def test_too_short_series_returns_nan(self):
        assert np.isnan(oos.newey_west_t(np.array([0.01]), lag=4))
        assert np.isnan(oos.naive_t(np.array([0.01])))

    def test_zero_variance_returns_nan(self):
        x = np.full(50, 0.01)
        assert np.isnan(oos.newey_west_t(x, lag=4))
        assert np.isnan(oos.naive_t(x))


# ---------------------------------------------------------------------------
# Non-overlapping event filter
# ---------------------------------------------------------------------------

class TestNonOverlap:
    def test_enforces_horizon_gap(self):
        idx = pd.to_datetime(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-10", "2025-01-11"],
            utc=True,
        )
        events = pd.Series(np.ones(len(idx)), index=idx)
        kept = oos.filter_non_overlapping(events, horizon_h=72)
        assert list(kept.index) == [idx[0], idx[3]]  # 01-01 → +72h → next ≥ 01-04

    def test_empty_input(self):
        events = pd.Series(dtype=float)
        kept = oos.filter_non_overlapping(events, horizon_h=72)
        assert len(kept) == 0


# ---------------------------------------------------------------------------
# Signed forward returns
# ---------------------------------------------------------------------------

class TestSignedReturns:
    def _close(self):
        idx = pd.date_range("2025-01-01", periods=40, freq="8h", tz="UTC")
        # Deterministic uptrend: forward returns are always positive.
        return pd.Series(np.linspace(100.0, 120.0, len(idx)), index=idx)

    def test_momentum_follows_sign_of_diff(self):
        close = self._close()
        idx = close.index[[5, 15]]
        events = pd.Series([+0.001, -0.001], index=idx)  # diffs
        r = oos.signed_forward_returns(events, close, 24, direction_mult=+1.0)
        # Uptrend: +diff → long → positive; -diff → short → negative.
        assert r.iloc[0] > 0
        assert r.iloc[1] < 0

    def test_reversal_is_exact_negative_of_momentum(self):
        close = self._close()
        idx = close.index[[5, 15]]
        events = pd.Series([+0.001, -0.001], index=idx)
        mom = oos.signed_forward_returns(events, close, 24, direction_mult=+1.0)
        rev = oos.signed_forward_returns(events, close, 24, direction_mult=-1.0)
        pd.testing.assert_series_equal(mom, -rev)

    def test_event_beyond_price_coverage_dropped(self):
        close = self._close()
        events = pd.Series([+0.001], index=[close.index[-1]])  # no t+24h price
        r = oos.signed_forward_returns(events, close, 24, direction_mult=+1.0)
        assert len(r) == 0


# ---------------------------------------------------------------------------
# Train-only selection discipline
# ---------------------------------------------------------------------------

def _synthetic_dataset(seed: int, n_bars: int = 700):
    """One-symbol synthetic dataset spanning the train/test boundary."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-06-01", periods=n_bars, freq="8h", tz="UTC")
    assert idx[0] < oos.TRAIN_END < idx[-1]
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n_bars))), index=idx)
    diff = pd.Series(rng.normal(0, 5e-4, n_bars), index=idx)
    closes = {"AAA": close}
    diffs = {("binance", "hyperliquid"): {"AAA": diff}, ("binance", "bybit"): {"AAA": diff * 0.5}}
    return diffs, closes


class TestSelectConfig:
    @pytest.fixture(autouse=True)
    def _one_symbol(self, monkeypatch):
        monkeypatch.setattr(oos, "SYMBOLS", ["AAA"])

    def test_selection_ignores_test_data(self):
        diffs, closes = _synthetic_dataset(seed=3)
        cfg_full, _ = oos.select_config(diffs, closes)
        # Truncate everything at TRAIN_END: expanding thresholds at train
        # timestamps only see pre-t data, so the selection must be identical.
        diffs_tr = {p: {s: d[d.index < oos.TRAIN_END] for s, d in m.items()} for p, m in diffs.items()}
        closes_tr = {s: c[c.index < oos.TRAIN_END] for s, c in closes.items()}
        cfg_tr, _ = oos.select_config(diffs_tr, closes_tr)
        assert cfg_full == cfg_tr

    def test_config_is_frozen_dataclass(self):
        cfg = oos.Config(pair=("binance", "hyperliquid"), quantile=0.9, horizon_h=24, direction_mult=1.0)
        assert dataclasses.is_dataclass(cfg)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.quantile = 0.95  # type: ignore[misc]

    def test_horizon_bars(self):
        cfg = oos.Config(pair=("a", "b"), quantile=0.9, horizon_h=72, direction_mult=1.0)
        assert cfg.horizon_bars == 9


# ---------------------------------------------------------------------------
# Multiple-testing helpers
# ---------------------------------------------------------------------------

class TestMultipleTesting:
    def test_bonferroni_critical_values(self):
        # 36 tests → |t| crit ≈ 3.2; 42 tests → ≈ 3.24; single test → 1.96.
        assert oos.bonferroni_alpha(1) == pytest.approx(1.96, abs=0.01)
        assert 3.1 < oos.bonferroni_alpha(36) < 3.3
        assert oos.bonferroni_alpha(42) > oos.bonferroni_alpha(36)

    def test_dsr_view_positive_edge_beats_critical(self):
        # Shared deflated_sharpe returns observed SR minus the multi-trial
        # hurdle; significant iff the returned value is > 0.
        rng = np.random.default_rng(5)
        strong = rng.normal(0.02, 0.01, size=200)  # SR ≈ 2 >> hurdle
        # Deterministic weak edge: mean 5bp, std 1% → SR ≈ 0.05 < hurdle.
        weak = np.tile([0.0105, -0.0095], 100)
        d_strong = oos.deflated_sharpe_view(strong, n_trials=36)
        d_weak = oos.deflated_sharpe_view(weak, n_trials=36)
        assert d_strong["dsr"] > 0
        assert d_weak["sr"] > 0 > d_weak["dsr"]
