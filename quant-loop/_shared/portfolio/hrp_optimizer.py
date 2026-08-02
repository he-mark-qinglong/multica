"""Hierarchical Risk Parity (HRP) portfolio optimizer (I17).

HRP is a deterministic allocation algorithm that does **not** require
matrix inversion, making it numerically stable for singular or
near-singular covariance matrices where ERC / mean-variance optimisers
break down. The three-stage pipeline (López de Prado, 2016):

  1. **Tree clustering** — convert the correlation-distance matrix into
     a hierarchical cluster tree via single-linkage agglomerative
     clustering on SciPy's :func:`scipy.cluster.hierarchy.linkage`.
  2. **Quasi-diagonalisation** — reorder rows/columns of the covariance
     matrix so that similar assets sit next to each other, concentrating
     large values along the diagonal.
  3. **Recursive bisection** — traverse the cluster tree top-down,
     splitting each subset into two halves and splitting the risk
     budget inversely proportional to each half's cluster variance.

The result is a weight vector ``w`` (sums to 1, all non-negative) that
respects the hierarchical structure of correlations — assets in the
same cluster receive correlated risk budgets, not naive equal weight.

Why add HRP alongside the existing ERC optimiser?
  * ERC (``_shared.market_making.portfolio_risk.erc_weights``) solves a
    convex program and needs a well-conditioned covariance matrix.
  * HRP works even when the covariance is rank-deficient (fewer
    observations than assets), ill-conditioned, or contains near-zero
    diagonal entries — common in production with short rolling windows.
  * HRP and ERC produce meaningfully different allocations; running
    both and comparing is a standard robustness check.

References:
  - López de Prado (2016), "Building Diversified Portfolios that
    Outperform Out-of-Sample", Journal of Portfolio Management 42(4).
  - Raffinot (2017), "Hierarchical Clustering-Based Asset Allocation",
    Journal of Portfolio Management 44(2).
  - López de Prado (2018), "Advances in Financial Machine Learning",
    Ch. 16 (HRP step-by-step with code).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.spatial.distance import squareform


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HRPClusterResult:
    """HRP allocation with clustering diagnostics.

    Attributes
    ----------
    weights:
        Portfolio weights indexed by asset name (sums to 1.0, all ≥ 0).
    linkage_matrix:
        SciPy ``(n-1) × 4`` linkage matrix for dendrogram plotting.
    sorted_assets:
        Asset names in quasi-diagonal order (most-similar assets adjacent).
    """

    weights: pd.Series
    linkage_matrix: np.ndarray
    sorted_assets: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Stage 1: Correlation distance & clustering
# ---------------------------------------------------------------------------

def _correlation_distance(corr: pd.DataFrame) -> pd.DataFrame:
    """Distance matrix from a correlation matrix.

    The standard HRP distance (López de Prado, 2016, eq. 3):

        d_{ij} = sqrt( 0.5 * (1 - ρ_{ij}) )

    This maps ρ ∈ [-1, 1] onto d ∈ [0, 1], with d = 0 for perfectly
    correlated and d = 1 for perfectly anti-correlated assets.
    """
    d = np.sqrt(np.clip(0.5 * (1.0 - corr.values), 0.0, 1.0))
    return pd.DataFrame(d, index=corr.index, columns=corr.columns)


def _linkage(dist: pd.DataFrame) -> np.ndarray:
    """Single-linkage linkage matrix from a distance matrix.

    Returns SciPy's ``(n-1) × 4`` linkage matrix (Z) suitable for
    :func:`scipy.cluster.hierarchy.dendrogram` and :func:`to_tree`.
    """
    dist_values = dist.values
    # Force exact symmetry and zero diagonal (floating-point drift can
    # otherwise produce tiny negatives that break squareform).
    dist_values = (dist_values + dist_values.T) / 2.0
    np.fill_diagonal(dist_values, 0.0)
    condensed = squareform(dist_values, checks=False)
    return linkage(condensed, method="single")


# ---------------------------------------------------------------------------
# Stage 2: Quasi-diagonalisation
# ---------------------------------------------------------------------------

def _get_quasi_diag(linkage_matrix: np.ndarray) -> List[int]:
    """Return the leaf order that quasi-diagonalises the matrix.

    Walks the linkage tree in depth-first order, collecting leaf
    indices. The result is a permutation of ``range(n)`` that places
    most-similar assets adjacently.
    """
    tree, _ = to_tree(linkage_matrix, rd=True)

    def _leaves(node) -> List[int]:
        if node.is_leaf():
            return [node.id]
        return _leaves(node.left) + _leaves(node.right)

    return _leaves(tree)


# ---------------------------------------------------------------------------
# Stage 3: Recursive bisection
# ---------------------------------------------------------------------------

def _cluster_variance(cov_block: np.ndarray) -> float:
    """Inverse-variance-weighted variance of a sub-cluster.

    This is the variance of the inverse-vol portfolio restricted to the
    assets in ``cov_block``, matching the original HRP paper's
    ``getRecBip`` helper.
    """
    inv_diag = 1.0 / np.diag(cov_block)
    w = inv_diag / inv_diag.sum()
    return float(w @ cov_block @ w)


def _get_rec_bisection(
    cov: pd.DataFrame, corr: pd.DataFrame, sort_ix: List[int]
) -> pd.Series:
    """Allocate weights via top-down recursive bisection.

    Parameters
    ----------
    cov : pd.DataFrame
        Full covariance matrix in original asset order.
    corr : pd.DataFrame
        Correlation matrix (consumed by Stage 1 clustering; accepted
        here for API symmetry with the paper).
    sort_ix : list of int
        Quasi-diagonal leaf indices (positions into ``cov``).

    Returns
    -------
    pd.Series
        Weights indexed by original column position, summing to 1.
    """
    # ``corr`` is part of the published signature; the clustering
    # information it encodes was already consumed in Stage 1.
    _ = corr

    n = len(sort_ix)
    w = np.ones(n)
    clusters: List[List[int]] = [list(range(n))]  # positions within sort_ix

    while clusters:
        new_clusters: List[List[int]] = []
        for c in clusters:
            if len(c) <= 1:
                continue
            mid = len(c) // 2
            left = c[:mid]
            right = c[mid:]

            # Original column indices for each half
            left_cols = [sort_ix[k] for k in left]
            right_cols = [sort_ix[k] for k in right]

            var_left = _cluster_variance(
                cov.iloc[left_cols, left_cols].values
            )
            var_right = _cluster_variance(
                cov.iloc[right_cols, right_cols].values
            )

            denom = var_left + var_right
            alpha = 1.0 - var_left / denom if denom > 0 else 0.5

            for k in left:
                w[k] *= alpha
            for k in right:
                w[k] *= (1.0 - alpha)

            new_clusters.append(left)
            new_clusters.append(right)
        clusters = new_clusters

    return pd.Series(w, index=list(range(n)))


def _hrp_allocation(cov: pd.DataFrame, indices: List[int]) -> pd.Series:
    """Map recursive-bisection weights to asset labels and normalise.

    Parameters
    ----------
    cov : pd.DataFrame
        Covariance matrix in original asset order.
    indices : list of int
        Quasi-diagonal leaf order (column positions).

    Returns
    -------
    pd.Series
        Weights indexed by asset name, summing to 1, all non-negative.
    """
    w = _get_rec_bisection(cov, pd.DataFrame(), indices)
    # ``w`` is positional (0..n-1 in quasi-diag order).
    # Map to asset labels: position k → cov.columns[indices[k]].
    assets = [cov.columns[idx] for idx in indices]
    result = pd.Series(w.values, index=assets)
    result = result / result.sum()
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def hrp_weights(returns: pd.DataFrame) -> pd.Series:
    """Compute Hierarchical Risk Parity weights from asset returns.

    Parameters
    ----------
    returns : pd.DataFrame
        Asset/strategy returns, columns = asset names, rows =
        time-indexed observations.

    Returns
    -------
    pd.Series
        Weights indexed by asset name (sums to 1, all ≥ 0).

    Notes
    -----
    Unlike ERC, HRP does not require the covariance matrix to be
    invertible — it operates on correlations and diagonal variances
    only. Singular or rank-deficient matrices are handled gracefully.

    Raises
    ------
    ValueError
        If the returns frame is empty.
    """
    result = hrp_cluster(returns)
    return result.weights


def hrp_cluster(returns: pd.DataFrame) -> HRPClusterResult:
    """Compute HRP weights with full clustering diagnostics.

    Parameters
    ----------
    returns : pd.DataFrame
        Asset/strategy returns, columns = asset names, rows =
        time-indexed observations.

    Returns
    -------
    HRPClusterResult
        Weights, SciPy linkage matrix, and quasi-diagonal asset order.

    Raises
    ------
    ValueError
        If the returns frame is empty.
    """
    if returns.empty or len(returns.columns) == 0:
        raise ValueError("returns frame is empty")

    if returns.shape[1] < 2:
        raise ValueError("Need at least 2 assets for HRP")
    if returns.shape[0] < 3:
        raise ValueError("Need at least 3 observations for HRP")

    assets = list(returns.columns)
    n = len(assets)

    cov = returns.cov()
    diag = np.diag(cov.values)

    # Guard: constant/zero covariance → equal weight (HRP is undefined
    # when all distances are zero / correlations are all 1).
    if np.all(diag <= 1e-20):
        eq = 1.0 / n
        return HRPClusterResult(
            weights=pd.Series({a: eq for a in assets}),
            linkage_matrix=np.empty((0, 4)),
            sorted_assets=tuple(assets),
        )

    # Correlation matrix from covariance
    std = np.sqrt(np.maximum(diag, 1e-20))
    corr_values = cov.values / np.outer(std, std)
    np.fill_diagonal(corr_values, 1.0)
    corr = pd.DataFrame(corr_values, index=assets, columns=assets)

    # Stage 1: cluster
    dist = _correlation_distance(corr)
    z = _linkage(dist)

    # Stage 2: quasi-diagonal order
    sort_ix = _get_quasi_diag(z)
    sorted_assets = tuple(assets[i] for i in sort_ix)

    # Stage 3: recursive bisection allocation
    weights = _hrp_allocation(cov, sort_ix)

    # Ensure weights are in original asset order (not cluster order)
    weights = weights.reindex(assets)

    return HRPClusterResult(
        weights=weights,
        linkage_matrix=z,
        sorted_assets=sorted_assets,
    )
