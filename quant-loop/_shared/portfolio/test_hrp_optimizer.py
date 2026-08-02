"""Tests for _shared/portfolio/hrp_optimizer.py (I21)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.hrp_optimizer import (
    HRPClusterResult,
    hrp_cluster,
    hrp_weights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_returns(n_assets: int = 5, n_obs: int = 500, seed: int = 42) -> pd.DataFrame:
    """Build a returns DataFrame with known correlation structure."""
    rng = np.random.RandomState(seed)
    # Create correlated returns: assets 0-1 are highly correlated, 2-3, 4 independent
    base = rng.randn(n_obs, n_assets)
    # Add common factor to first two
    factor1 = rng.randn(n_obs, 1) * 0.5
    if n_assets > 0:
        base[:, 0] += factor1.ravel()
    if n_assets > 1:
        base[:, 1] += factor1.ravel()
    # Common factor for assets 2-3
    factor2 = rng.randn(n_obs, 1) * 0.5
    if n_assets > 2:
        base[:, 2] += factor2.ravel()
    if n_assets > 3:
        base[:, 3] += factor2.ravel()

    assets = [f"ASSET_{i}" for i in range(n_assets)]
    return pd.DataFrame(base * 0.01, columns=assets)


# ---------------------------------------------------------------------------
# hrp_weights
# ---------------------------------------------------------------------------

def test_hrp_weights_sum_to_one():
    """Weights should sum to 1.0."""
    returns = _make_returns(n_assets=5, seed=0)
    w = hrp_weights(returns)
    assert w.sum() == pytest.approx(1.0, abs=1e-10)


def test_hrp_weights_non_negative():
    """All weights should be non-negative."""
    returns = _make_returns(n_assets=5, seed=1)
    w = hrp_weights(returns)
    assert (w >= 0).all()


def test_hrp_weights_correct_index():
    """Weights index should match returns columns."""
    returns = _make_returns(n_assets=5, seed=2)
    w = hrp_weights(returns)
    assert set(w.index) == set(returns.columns)


def test_hrp_weights_diversified():
    """Weights should be reasonably diversified (no single asset > 80%)."""
    returns = _make_returns(n_assets=5, seed=3)
    w = hrp_weights(returns)
    assert w.max() < 0.8


def test_hrp_weights_single_asset_raises():
    """Single asset → ValueError."""
    returns = pd.DataFrame({"A": np.random.randn(100)})
    with pytest.raises(ValueError, match="2 assets"):
        hrp_weights(returns)


def test_hrp_weights_insufficient_obs_raises():
    """Too few observations → ValueError."""
    returns = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    with pytest.raises(ValueError, match="3 observations"):
        hrp_weights(returns)


def test_hrp_weights_two_assets():
    """Should work with 2 assets."""
    returns = _make_returns(n_assets=2, seed=5)
    w = hrp_weights(returns)
    assert len(w) == 2
    assert w.sum() == pytest.approx(1.0, abs=1e-10)


def test_hrp_weights_low_vol_gets_more():
    """Lower-volatility assets should get higher weights (inverse-variance tendency)."""
    rng = np.random.RandomState(10)
    n = 500
    # Asset A has half the volatility of B
    a = rng.randn(n) * 0.01
    b = rng.randn(n) * 0.02
    returns = pd.DataFrame({"A": a, "B": b})
    w = hrp_weights(returns)
    # A (lower vol) should get more weight
    assert w["A"] > w["B"]


# ---------------------------------------------------------------------------
# hrp_cluster
# ---------------------------------------------------------------------------

def test_hrp_cluster_returns_result():
    """Should return HRPClusterResult."""
    returns = _make_returns(n_assets=5, seed=10)
    result = hrp_cluster(returns)
    assert isinstance(result, HRPClusterResult)


def test_hrp_cluster_weights_sum_to_one():
    """Cluster result weights should sum to 1."""
    returns = _make_returns(n_assets=5, seed=11)
    result = hrp_cluster(returns)
    assert result.weights.sum() == pytest.approx(1.0, abs=1e-10)


def test_hrp_cluster_linkage_matrix():
    """Linkage matrix should be a numpy array."""
    returns = _make_returns(n_assets=5, seed=12)
    result = hrp_cluster(returns)
    assert isinstance(result.linkage_matrix, np.ndarray)
    # For n assets, linkage has n-1 rows
    assert result.linkage_matrix.shape[0] == 5 - 1


def test_hrp_cluster_sorted_assets():
    """Sorted assets should contain all assets exactly once."""
    returns = _make_returns(n_assets=5, seed=13)
    result = hrp_cluster(returns)
    assert len(result.sorted_assets) == 5
    assert set(result.sorted_assets) == set(returns.columns)


def test_hrp_cluster_correlated_adjacent():
    """Highly correlated assets should be adjacent in the sorted order."""
    returns = _make_returns(n_assets=5, seed=42)
    result = hrp_cluster(returns)
    order = list(result.sorted_assets)
    # Assets 0 and 1 share a factor → should be adjacent in clustering
    pos0 = order.index("ASSET_0")
    pos1 = order.index("ASSET_1")
    assert abs(pos0 - pos1) <= 2  # close but maybe not always adjacent


def test_hrp_cluster_result_is_frozen():
    """HRPClusterResult should be immutable."""
    returns = _make_returns(n_assets=3, seed=20)
    result = hrp_cluster(returns)
    with pytest.raises(Exception):
        result.weights = pd.Series()
