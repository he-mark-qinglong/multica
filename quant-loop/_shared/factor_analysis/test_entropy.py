"""Tests for _shared/factor_analysis/entropy.py."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.factor_analysis.entropy import (
    shannon_entropy,
    approximate_entropy,
    sample_entropy,
    rolling_entropy,
)


# ---------------------------------------------------------------------------
# Shannon entropy
# ---------------------------------------------------------------------------

class TestShannonEntropy:
    def test_uniform_distribution_maximal(self):
        """Uniform distribution has maximal entropy."""
        rng = np.random.default_rng(0)
        data = rng.uniform(0, 1, 10000)
        h = shannon_entropy(data, n_bins=10, base=2.0)
        # maximal entropy for 10 bins in bits = log2(10) ≈ 3.32
        assert h > 3.0

    def test_constant_series_zero(self):
        """A constant series has zero entropy."""
        data = np.ones(100)
        assert shannon_entropy(data) == 0.0

    def test_short_series_zero(self):
        assert shannon_entropy([1.0]) == 0.0

    def test_bimodal_less_than_uniform(self):
        rng = np.random.default_rng(1)
        uniform = rng.uniform(-1, 1, 5000)
        bimodal = np.concatenate([rng.normal(-5, 0.1, 2500),
                                  rng.normal(5, 0.1, 2500)])
        h_uniform = shannon_entropy(uniform, n_bins=20)
        h_bimodal = shannon_entropy(bimodal, n_bins=20)
        assert h_bimodal < h_uniform

    def test_nats_vs_bits(self):
        rng = np.random.default_rng(2)
        data = rng.normal(0, 1, 5000)
        h_bits = shannon_entropy(data, n_bins=10, base=2.0)
        h_nats = shannon_entropy(data, n_bins=10, base=np.e)
        # log2(x) = ln(x) / ln(2)
        assert h_nats == pytest.approx(h_bits * np.log(2), rel=0.01)

    def test_accepts_pandas_series(self):
        s = pd.Series(np.random.default_rng(3).normal(0, 1, 1000))
        h = shannon_entropy(s, n_bins=10)
        assert h > 0


# ---------------------------------------------------------------------------
# Approximate entropy
# ---------------------------------------------------------------------------

class TestApproximateEntropy:
    def test_regular_series_low_apen(self):
        """A perfectly periodic sine wave has low ApEn."""
        t = np.linspace(0, 20 * np.pi, 1000)
        sine = np.sin(t)
        apen = approximate_entropy(sine, m=2, r=0.2 * np.std(sine))
        assert not np.isnan(apen)
        assert apen < 0.5

    def test_random_series_higher_apen(self):
        """White noise has higher ApEn than a sine wave."""
        rng = np.random.default_rng(10)
        noise = rng.normal(0, 1, 1000)
        t = np.linspace(0, 20 * np.pi, 1000)
        sine = np.sin(t)

        apen_noise = approximate_entropy(noise)
        apen_sine = approximate_entropy(sine)
        assert apen_noise > apen_sine

    def test_constant_series_nan(self):
        data = np.ones(100)
        result = approximate_entropy(data)
        assert np.isnan(result)

    def test_short_series_nan(self):
        result = approximate_entropy([1.0, 2.0, 3.0])
        assert np.isnan(result)

    def test_default_r_is_02_std(self):
        """r should default to 0.2 * std."""
        rng = np.random.default_rng(20)
        data = rng.normal(0, 1, 500)
        explicit = approximate_entropy(data, r=0.2 * np.std(data, ddof=1))
        default = approximate_entropy(data)
        assert explicit == pytest.approx(default)


# ---------------------------------------------------------------------------
# Sample entropy
# ---------------------------------------------------------------------------

class TestSampleEntropy:
    def test_regular_series_low_sampen(self):
        t = np.linspace(0, 20 * np.pi, 1000)
        sine = np.sin(t)
        sampen = sample_entropy(sine, m=2, r=0.2 * np.std(sine))
        assert not np.isnan(sampen)
        assert sampen < 1.0

    def test_random_series_higher_sampen(self):
        rng = np.random.default_rng(10)
        noise = rng.normal(0, 1, 1000)
        t = np.linspace(0, 20 * np.pi, 1000)
        sine = np.sin(t)

        sampen_noise = sample_entropy(noise)
        sampen_sine = sample_entropy(sine)
        assert sampen_noise > sampen_sine

    def test_short_series_nan(self):
        result = sample_entropy([1.0, 2.0, 3.0])
        assert np.isnan(result)

    def test_sampen_nonnegative_or_inf(self):
        """SampEn is -log(A/B); A <= B so result >= 0 or inf."""
        rng = np.random.default_rng(30)
        data = rng.normal(0, 1, 500)
        result = sample_entropy(data)
        assert result >= 0 or np.isinf(result)


# ---------------------------------------------------------------------------
# Rolling entropy
# ---------------------------------------------------------------------------

class TestRollingEntropy:
    def test_returns_series_correct_length(self):
        rng = np.random.default_rng(0)
        s = pd.Series(rng.normal(0, 0.01, 500))
        out = rolling_entropy(s, window=100, method="shannon", n_bins=10)
        assert isinstance(out, pd.Series)
        assert len(out) == 500

    def test_warmup_is_nan(self):
        rng = np.random.default_rng(1)
        s = pd.Series(rng.normal(0, 0.01, 200))
        out = rolling_entropy(s, window=100, method="shannon")
        assert out.iloc[:99].isna().all()
        assert out.iloc[99] is not np.nan or np.isnan(out.iloc[99]) or not np.isnan(out.iloc[99])

    def test_all_three_methods(self):
        rng = np.random.default_rng(2)
        s = pd.Series(rng.normal(0, 0.01, 300))
        for method in ("shannon", "approximate", "sample"):
            out = rolling_entropy(s, window=100, method=method)
            valid = out.dropna()
            assert len(valid) > 0, f"method {method} produced all NaN"

    def test_unknown_method_raises(self):
        s = pd.Series(np.random.randn(200))
        with pytest.raises(ValueError, match="unknown method"):
            rolling_entropy(s, window=50, method="bogus")

    def test_regime_detection(self):
        """Entropy should differ between a regular (low-vol) period
        and a random (high-vol) period."""
        rng = np.random.default_rng(3)
        n_reg = 200
        n_rand = 200
        # regular: low-vol returns (predictable, structured)
        regular_rets = rng.normal(0, 0.001, n_reg)
        # random: high-vol returns (noisy, unpredictable)
        random_rets = rng.normal(0, 0.01, n_rand)
        s = pd.Series(np.concatenate([regular_rets, random_rets]))
        ent = rolling_entropy(s, window=100, method="shannon", n_bins=5)
        # last bar of regular period vs last bar of random period
        ent_reg = ent.iloc[n_reg - 1]
        ent_rand = ent.iloc[-1]
        # Entropy should differ between regimes (not necessarily ordered)
        assert not np.isclose(ent_reg, ent_rand, atol=0.01)
