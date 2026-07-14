"""Unit tests for the rolling-window primitives in `cointegration.py`.

These wrap the static OLS / z-score / half-life helpers in rolling
estimators. The tests are split out from `test_cointegration.py` so each
test file fits on one screen and the import surface is easier to audit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cointegration import half_life, rolling_hedge_ratio, rolling_zscore

from ._synthetic import make_cointegrated_pair


class TestRollingHedgeRatio:
    def test_warmup_yields_nans_then_fills(self):
        log_y, log_x = make_cointegrated_pair(n=200, seed=6)
        df = rolling_hedge_ratio(log_y, log_x, window=30)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["alpha", "beta", "r_squared"]
        # First 29 rows must be NaN (window=30 needs indices 30..n-1 filled).
        assert df["beta"].iloc[:29].isna().all()
        # After warmup, beta should be close to truth (mean over the post-warmup
        # window — 170 obs on a stable synthetic should converge tightly).
        valid = df["beta"].iloc[29:].dropna()
        assert len(valid) > 100
        assert abs(valid.mean() - 1.5) < 0.15

    def test_handles_pandas_series_input(self):
        log_y, log_x = make_cointegrated_pair(n=80, seed=7)
        ys = pd.Series(log_y)
        xs = pd.Series(log_x, index=ys.index)
        df = rolling_hedge_ratio(ys, xs, window=30)
        # Index is preserved.
        pd.testing.assert_index_equal(df.index, ys.index)

    def test_min_periods_shortens_warmup(self):
        log_y, log_x = make_cointegrated_pair(n=80, seed=8)
        # With window=30, min_periods=30 (default): beta is NaN through row 29.
        df_default = rolling_hedge_ratio(log_y, log_x, window=30)
        # With min_periods=10: beta appears earlier.
        df_early = rolling_hedge_ratio(log_y, log_x, window=30, min_periods=10)
        # The early variant should have strictly more non-NaN betas overall
        # (it starts computing at row 10; the default starts at row 30).
        assert df_early["beta"].notna().sum() > df_default["beta"].notna().sum()

    def test_raises_on_mismatched_shapes(self):
        with pytest.raises(ValueError):
            rolling_hedge_ratio(np.zeros(50), np.zeros(51), window=10)


class TestRollingZscore:
    def test_basic_shape(self):
        spread = np.cumsum(np.random.default_rng(9).normal(0.0, 0.01, 100))
        df = rolling_zscore(spread, window=20)
        assert list(df.columns) == ["mean", "std", "zscore"]
        assert len(df) == 100
        # First 19 rows are NaN due to rolling warmup.
        assert df["zscore"].iloc[:19].isna().all()

    def test_constant_spread_yields_nan_zscore(self):
        spread = np.full(50, 1.5)
        df = rolling_zscore(spread, window=10)
        # std == 0 -> zscore is NaN (we replace divide-by-zero).
        assert df["zscore"].iloc[9:].isna().all()

    def test_zscore_mean_reverts_around_zero(self):
        # Stationary AR(1) spread -> z-score should oscillate around 0.
        rng = np.random.default_rng(11)
        s = np.zeros(500)
        for i in range(1, 500):
            s[i] = 0.6 * s[i - 1] + rng.normal(0.0, 0.1)
        df = rolling_zscore(s, window=50)
        z = df["zscore"].dropna()
        # Mean of z-score should be near zero (within sampling noise).
        assert abs(z.mean()) < 0.5
        # Standard deviation should be near 1 (definition of z-score).
        assert 0.5 < z.std() < 1.5

    def test_pandas_series_index_preserved(self):
        # This is the bug fix from B1: rolling_zscore used to drop the index
        # when given a pd.Series, breaking downstream merges in build_signals.
        idx = pd.date_range("2024-01-01", periods=80, freq="1D")
        spread = pd.Series(np.cumsum(np.random.default_rng(13).normal(0, 0.01, 80)), index=idx)
        df = rolling_zscore(spread, window=20)
        pd.testing.assert_index_equal(df.index, idx)

    def test_min_periods_shortens_warmup(self):
        spread = np.cumsum(np.random.default_rng(14).normal(0, 0.01, 80))
        df_default = rolling_zscore(spread, window=20)
        df_early = rolling_zscore(spread, window=20, min_periods=5)
        # Earlier variant has more non-NaN rows.
        assert df_early["zscore"].notna().sum() > df_default["zscore"].notna().sum()


class TestHalfLife:
    def test_short_half_life_for_strongly_mean_reverting(self):
        # AR(1) with phi=0.5 -> half-life = log(2)/log(1/0.5) = 1.0 bar.
        s = np.zeros(500)
        rng = np.random.default_rng(12)
        for i in range(1, 500):
            s[i] = 0.5 * s[i - 1] + rng.normal(0.0, 0.05)
        hl = half_life(s)
        assert 0.5 < hl < 2.0

    def test_returns_inf_for_random_walk(self):
        # Random walk has b ≈ 0 (sampling noise); our near-unit-root guard
        # treats anything with |b| < 1e-3 as non-stationary.
        s = np.cumsum(np.random.default_rng(13).normal(0.0, 0.01, 300))
        hl = half_life(s)
        assert np.isinf(hl)

    def test_returns_nan_for_constant(self):
        # Constant series -> lstsq returns zero slope, then NaN.
        s = np.full(50, 3.14)
        hl = half_life(s)
        # Either NaN (degenerate) or inf (b=0) is acceptable.
        assert np.isnan(hl) or np.isinf(hl)

    def test_returns_nan_for_too_short(self):
        # < 3 observations -> NaN (can't fit AR(1) with differencing).
        s = np.array([1.0, 2.0])
        hl = half_life(s)
        assert np.isnan(hl)
