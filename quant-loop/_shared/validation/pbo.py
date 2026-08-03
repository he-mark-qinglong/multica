"""Probability of Backtest Overfitting (PBO) — Bailey et al. (2017).

PBO directly measures the probability that a strategy's backtest
performance is due to overfitting rather than genuine edge. It's the
gold standard for validation, more rigorous than DSR alone.

Method (Combinatorially Symmetric Cross-Validation):
1. Split data into N=16 blocks, use J=N/2 for IS and N/2 for OOS
2. For all C(N, J) = C(16,8) = 12,870 combinations:
   a. Rank strategies by IS Sharpe
   b. Take the IS-optimal strategy
   c. Compute its OOS Sharpe rank
   d. Convert rank to [0,1] via the rank-logit transform: λ = ln(r/(1-r))
3. PBO = fraction of combinations where λ < 0 (IS-optimal underperforms OOS median)

PBO > 0.5 → strategy selection is dominated by overfitting.
PBO < 0.5 → selection has genuine out-of-sample edge.

Usage:
    pbo = compute_pbo(is_sharpes, oos_sharpes_matrix)
    print(f"PBO = {pbo:.3f}")

Reference: Bailey, Borwein, López de Prado, Salehipour (2017),
"An Evaluation of the Probability of Backtest Overfitting"
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Sequence

import numpy as np


def _rank_logit_transform(ranks: np.ndarray) -> np.ndarray:
    """Transform ranks [0,1] via λ = ln(r/(1-r)).

    This maps the (0,1) interval to (-∞,+∞), making it symmetric
    around 0. Values < 0 mean below-median OOS performance.
    """
    # Clip to avoid log(0) or log(inf)
    r = np.clip(ranks, 1e-10, 1 - 1e-10)
    return np.log(r / (1 - r))


def compute_pbo(
    is_sharpes: np.ndarray,
    oos_sharpes_matrix: np.ndarray,
) -> dict:
    """Compute Probability of Backtest Overfitting.

    Args:
        is_sharpes: 1D array of IS Sharpe ratios for N strategies.
            Shape: (N_strategies,).
        oos_sharpes_matrix: 2D array of OOS Sharpe ratios for N strategies
            across C combinations. Shape: (N_strategies, N_combinations).
            Each column is one IS/OOS split.

    Returns:
        dict with:
            pbo: float in [0,1]. >0.5 means overfit-dominated.
            lambda_distribution: array of logit-rank values.
            mean_logit: mean of λ distribution.
            median_logit: median of λ distribution.
            n_combinations: number of IS/OOS splits evaluated.
    """
    n_strategies, n_combinations = oos_sharpes_matrix.shape
    lambdas = []

    for c in range(n_combinations):
        # IS ranking (1 = best)
        is_ranks = _rank_array(is_sharpes[:, c] if is_sharpes.ndim > 1 else is_sharpes)
        best_is_idx = int(np.argmax(is_sharpes[:, c] if is_sharpes.ndim > 1 else is_sharpes))

        # OOS rank of the IS-optimal strategy
        oos_sharpes = oos_sharpes_matrix[:, c]
        oos_rank = _rank_array(oos_sharpes)
        best_oos_rank = oos_rank[best_is_idx]

        # Normalize to [0,1]: rank 1 (best) → high percentile
        r = (n_strategies + 1 - best_oos_rank) / (n_strategies + 1)
        lam = float(_rank_logit_transform(np.array([r]))[0])
        lambdas.append(lam)

    lambdas = np.array(lambdas)
    pbo = float(np.mean(lambdas < 0))

    return {
        "pbo": pbo,
        "lambda_distribution": lambdas,
        "mean_logit": float(np.mean(lambdas)),
        "median_logit": float(np.median(lambdas)),
        "n_combinations": n_combinations,
        "verdict": "OVERFIT-DOMINATED" if pbo > 0.5 else "GENUINE EDGE",
    }


def _rank_array(arr: np.ndarray) -> np.ndarray:
    """Rank array (1 = highest value). Ties get average rank."""
    from scipy.stats import rankdata
    return rankdata(-arr, method="average")  # negative → highest gets rank 1


def cscv_pbo(
    strategy_returns: np.ndarray,
    n_blocks: int = 16,
) -> dict:
    """Combinatorially Symmetric Cross-Validation PBO.

    Full CSCV procedure:
    1. Split each strategy's return series into N blocks
    2. For all C(N, N/2) combinations of IS/OOS blocks:
       a. Compute IS and OOS Sharpe for each strategy
       b. Find IS-optimal strategy
       c. Record its OOS Sharpe rank
    3. Compute PBO from the distribution of OOS ranks

    Args:
        strategy_returns: 2D array (n_bars, n_strategies).
        n_blocks: number of blocks to split into (default 16).

    Returns:
        PBO result dict.
    """
    n_bars, n_strategies = strategy_returns.shape
    n_blocks = min(n_blocks, n_bars // 10)  # ensure enough data per block
    n_blocks = max(n_blocks, 4)  # minimum 4 blocks
    block_size = n_bars // n_blocks

    # Truncate to full blocks
    usable = block_size * n_blocks
    truncated = strategy_returns[:usable]

    # Reshape into blocks
    blocks = truncated.reshape(n_blocks, block_size, n_strategies)
    # Per-block Sharpe (annualized)
    block_sharpes = np.zeros((n_blocks, n_strategies))
    for b in range(n_blocks):
        r = blocks[b]
        mu = r.mean(axis=0)
        sigma = r.std(axis=0, ddof=1)
        block_sharpes[b] = np.where(sigma > 1e-10, mu / sigma * np.sqrt(252), 0)

    # All C(N, N/2) IS/OOS combinations
    half = n_blocks // 2
    combos = list(combinations(range(n_blocks), half))

    # For each combination, compute IS and OOS Sharpe for each strategy
    all_is_sharpes = np.zeros((n_strategies, len(combos)))
    all_oos_sharpes = np.zeros((n_strategies, len(combos)))

    for c_idx, is_blocks in enumerate(combos):
        oos_blocks = tuple(b for b in range(n_blocks) if b not in is_blocks)

        for s in range(n_strategies):
            # IS Sharpe: average block Sharpes
            is_sharpe = np.mean([block_sharpes[b, s] for b in is_blocks])
            oos_sharpe = np.mean([block_sharpes[b, s] for b in oos_blocks])
            all_is_sharpes[s, c_idx] = is_sharpe
            all_oos_sharpes[s, c_idx] = oos_sharpe

    return compute_pbo(all_is_sharpes, all_oos_sharpes)


def minimum_backtest_length(
    n_trials: int,
    sharpe: float,
    annualized_vol: float = 0.15,
    significance: float = 0.05,
) -> float:
    """Minimum backtest length (in years) to avoid false discovery.

    From Bailey & López de Prado (2014): the minimum sample length
    needed to achieve statistical significance at the given level,
    accounting for multiple testing.

    Args:
        n_trials: number of strategies tried.
        sharpe: observed annualized Sharpe ratio.
        annualized_vol: annualized volatility of returns.
        significance: p-value threshold (default 5%).

    Returns:
        Minimum backtest length in years.
    """
    from scipy.stats import norm

    # Expected max Sharpe under null (n_trials iid)
    euler = 0.5772156649
    expected_max = ((1 - euler) * norm.ppf(1 - 1/n_trials)
                    + euler * norm.ppf(1 - 1/(n_trials * math.e)))

    # Required samples: solve for N such that P(SR > expected_max) = significance
    # Under null, SR ~ N(0, 1/sqrt(T)) per year
    # So need: sharpe * sqrt(T) > z_{1-significance} + expected_max
    z = norm.ppf(1 - significance)
    required_t = ((z + expected_max) / max(sharpe, 1e-6)) ** 2

    return float(required_t)
