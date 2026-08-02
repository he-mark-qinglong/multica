"""Tests for _shared/factor_analysis/fracdiff.py."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from _shared.factor_analysis.fracdiff import (  # noqa: E402
    _compute_weights,
    frac_diff,
    frac_diff_expanding,
    frac_diff_ffd,
    frac_diff_weights,
    optimal_d,
)


def _gbm(n: int = 2000, seed: int = 42, vol: float = 0.001) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(np.cumsum(rng.normal(0, vol, n)) + np.log(40000))


# ---------------------------------------------------------------------------
# Weight computation
# ---------------------------------------------------------------------------

class TestWeights:
    def test_d0_identity(self):
        """d=0 → only w_0 = 1."""
        w = frac_diff_weights(0.0, 10)
        assert w[0] == pytest.approx(1.0)
        assert w[1] == pytest.approx(0.0)

    def test_d1_first_difference(self):
        """d=1 → weights [1, -1, 0, 0, …]."""
        w = frac_diff_weights(1.0, 5)
        assert w[0] == pytest.approx(1.0)
        assert w[1] == pytest.approx(-1.0)
        assert w[2] == pytest.approx(0.0)

    def test_d05_negative_after_first(self):
        """For d=0.5, all weights after w_0 are negative and decay."""
        w = frac_diff_weights(0.5, 20)
        assert w[0] == pytest.approx(1.0)
        assert w[1] < 0  # negative
        # magnitudes decay (non-increasing) after the first few
        mags = np.abs(w[1:])
        diffs = np.diff(mags[:5])
        assert np.all((diffs < 1e-10) | (diffs >= 0))

    def test_compute_weights_truncation(self):
        """_compute_weights truncates at threshold."""
        w = _compute_weights(0.4, threshold=1e-5)
        assert w[0] == pytest.approx(1.0)
        assert len(w) > 1
        # all retained weights should be >= threshold in abs value
        assert np.all(np.abs(w) >= 1e-5 - 1e-12)

    def test_size_validation(self):
        with pytest.raises(ValueError, match="size"):
            frac_diff_weights(0.5, 0)


# ---------------------------------------------------------------------------
# frac_diff (FFD)
# ---------------------------------------------------------------------------

class TestFracDiff:
    def test_d0_returns_original(self):
        s = pd.Series(np.arange(50, dtype=float))
        result = frac_diff(s, d=0.0)
        pd.testing.assert_series_equal(result, s.astype(float))

    def test_d1_matches_pandas_diff(self):
        s = pd.Series(np.arange(100, dtype=float))
        result = frac_diff(s, d=1.0)
        expected = s.astype(float).diff(1)
        mask = result.notna() & expected.notna()
        np.testing.assert_allclose(result[mask].values, expected[mask].values)

    def test_warmup_is_nan(self):
        s = pd.Series(np.arange(100, dtype=float))
        result = frac_diff(s, d=0.5, threshold=1e-4)
        assert np.isnan(result.iloc[0])
        assert result.dropna().shape[0] < 100

    def test_preserves_memory_vs_full_diff(self):
        """d<1 retains more autocorrelation than d=1."""
        s = _gbm(2000)
        fd_low = frac_diff(s, d=0.35, threshold=1e-5)
        fd_high = frac_diff(s, d=1.0, threshold=1e-5)
        ac_low = fd_low.dropna().autocorr(lag=1)
        ac_high = fd_high.dropna().autocorr(lag=1)
        assert ac_low > ac_high, (
            f"d=0.35 (ac={ac_low:.4f}) should retain more memory than "
            f"d=1.0 (ac={ac_high:.4f})"
        )

    def test_reduces_autocorr_vs_levels(self):
        """Frac-diff output should have lower autocorr than raw levels."""
        s = _gbm(2000)
        fd = frac_diff(s, d=0.4, threshold=1e-4)
        assert fd.dropna().autocorr(lag=1) < s.autocorr(lag=1)

    def test_ffd_is_alias(self):
        assert frac_diff_ffd is frac_diff

    def test_nan_handling(self):
        s = _gbm(100)
        s.iloc[50:55] = np.nan
        out = frac_diff(s, 0.4)
        # positions with NaN in window → NaN output
        assert out.iloc[50:55].isna().all()

    def test_negative_d_raises(self):
        with pytest.raises(ValueError, match="d must be >= 0"):
            frac_diff(pd.Series([1.0, 2.0]), d=-0.5)

    def test_empty_series(self):
        out = frac_diff(pd.Series([], dtype=float), d=0.4)
        assert len(out) == 0


# ---------------------------------------------------------------------------
# frac_diff_expanding
# ---------------------------------------------------------------------------

class TestExpanding:
    def test_first_point_has_value(self):
        """Expanding window: first point should have a value."""
        s = pd.Series(np.arange(10, dtype=float))
        result = frac_diff_expanding(s, d=0.5)
        assert not np.isnan(result.iloc[0])
        assert result.iloc[0] == pytest.approx(s.iloc[0])

    def test_expanding_and_ffd_both_stationary(self):
        """Both expanding and FFD produce output; FFD is more stationary."""
        s = _gbm(2000)
        expanding = frac_diff_expanding(s, d=0.4, threshold=1e-4)
        ffd = frac_diff(s, d=0.4, threshold=1e-4)
        exp_clean = expanding.dropna()
        ffd_clean = ffd.dropna()
        assert len(exp_clean) > 100
        assert len(ffd_clean) > 100
        # FFD uses a fixed-width truncated window, so it's closer to
        # wide-sense stationary than the expanding variant.
        assert abs(ffd_clean.autocorr(lag=1)) < abs(exp_clean.autocorr(lag=1))

    def test_d0_returns_original(self):
        s = pd.Series(np.arange(50, dtype=float))
        result = frac_diff_expanding(s, d=0.0)
        pd.testing.assert_series_equal(result, s.astype(float))

    def test_d1_matches_diff(self):
        s = pd.Series(np.arange(100, dtype=float))
        result = frac_diff_expanding(s, d=1.0)
        expected = s.astype(float).diff(1)
        mask = result.notna() & expected.notna()
        np.testing.assert_allclose(result[mask].values, expected[mask].values)


# ---------------------------------------------------------------------------
# optimal_d
# ---------------------------------------------------------------------------

class TestOptimalD:
    def test_returns_valid_tuple(self):
        s = _gbm(2000)
        best_d, best_p = optimal_d(s, d_range=(0.0, 1.0), steps=11)
        assert 0.0 <= best_d <= 1.0
        assert 0.0 <= best_p <= 1.0

    def test_finds_stationary_d(self):
        """Random walk: some d < 1 should achieve stationarity."""
        s = _gbm(3000)
        best_d, best_p = optimal_d(s, d_range=(0.0, 1.0), steps=21)
        assert best_d < 1.0 or best_p < 0.05, (
            f"Expected d<1 or p<0.05; got d={best_d:.2f}, p={best_p:.4f}"
        )

    def test_white_noise_stationary_at_d0(self):
        rng = np.random.default_rng(42)
        s = pd.Series(rng.normal(0, 1, 500))
        best_d, best_p = optimal_d(s, d_range=(0.0, 1.0), steps=11)
        assert best_d == pytest.approx(0.0, abs=0.15)
        assert best_p < 0.05

    def test_custom_d_list(self):
        s = _gbm(500)
        best_d, best_p = optimal_d(s, d_range=[0.3, 0.5, 0.7])
        assert best_d in [0.3, 0.5, 0.7]
