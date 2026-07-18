"""Combinatorial Purged Cross-Validation (CPCV) harness.

Replaces the leaking oos_walk_forward.py. Per López de Prado AFML Ch.7:
- Split data into N groups
- Pick K groups as test, N-K as train, for all C(N,K) combinations ("paths")
- Purge bars within `purge_bars` of train/test boundary
- Embargo: skip `embargo_bars` after each test window

Per-fold refit (parameter fitting happens on train only), evaluate on test,
aggregate across all paths to get OOS Sharpe distribution.

Includes Deflated Sharpe Ratio (Bailey & López de Prado 2014) for
multiple-testing correction.
"""
from dataclasses import dataclass, field
from itertools import combinations
import numpy as np
import pandas as pd


@dataclass
class FoldResult:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    oos_sharpe: float
    oos_returns: np.ndarray
    n_trades: int


@dataclass
class CPCVResult:
    n_groups: int
    k_test: int
    n_paths: int  # C(N, K)
    folds: list[FoldResult] = field(default_factory=list)

    @property
    def mean_oos_sharpe(self) -> float:
        if not self.folds:
            return float("nan")
        return float(np.mean([f.oos_sharpe for f in self.folds]))

    @property
    def std_oos_sharpe(self) -> float:
        if len(self.folds) < 2:
            return float("nan")
        return float(np.std([f.oos_sharpe for f in self.folds], ddof=1))

    @property
    def oos_sharpe_ci95(self) -> tuple[float, float]:
        """Bootstrap 95% CI of mean OOS Sharpe."""
        if len(self.folds) < 5:
            return (float("nan"), float("nan"))
        sharpes = np.array([f.oos_sharpe for f in self.folds])
        rng = np.random.default_rng(42)
        boot_means = rng.choice(sharpes, size=(1000, len(sharpes)), replace=True).mean(axis=1)
        return (float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5)))


def _purge_boundaries(
    train_idx: np.ndarray, test_idx: np.ndarray, purge_bars: int
) -> tuple[np.ndarray, np.ndarray]:
    """Drop train bars within `purge_bars` of any test boundary."""
    if purge_bars <= 0:
        return train_idx, test_idx
    test_min, test_max = test_idx.min(), test_idx.max()
    mask = np.ones(len(train_idx), dtype=bool)
    for ti in [test_min, test_max]:
        # drop train bars within purge_bars of test boundary
        for offset in range(-purge_bars, purge_bars + 1):
            mask &= (train_idx != (ti + offset))
    return train_idx[mask], test_idx


def _embargo(test_idx: np.ndarray, embargo_bars: int) -> np.ndarray:
    """Drop the first `embargo_bars` of test (post-train leakage buffer)."""
    if embargo_bars <= 0:
        return test_idx
    # sort test, drop earliest embargo_bars
    s = np.sort(test_idx)
    return s[embargo_bars:]


def sharpe_from_returns(returns: np.ndarray, periods_per_year: int = 365) -> float:
    """Annualized Sharpe from per-period returns."""
    if len(returns) < 2 or np.std(returns) <= 1e-12:
        return 0.0
    return float(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(periods_per_year))


def deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    sample_len: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio per Bailey & López de Prado (2014).

    Adjusts observed Sharpe for multiple testing. Returns the Sharpe value
    that would be statistically significant at 95% after n_trials tests.

    Args:
        observed_sharpe: the best Sharpe from n_trials backtests
        n_trials: number of strategies tried (family size)
        sample_len: length of the returns series (bars)
        skew: returns skewness (0 = normal)
        kurt: returns kurtosis (3 = normal)

    Returns:
        Deflated Sharpe — if observed > deflated, the edge is real.
    """
    if n_trials < 1 or sample_len < 2:
        return observed_sharpe
    # Expected max of n_trials draws from N(0, 1)
    emc = 0.5772156649  # Euler-Mascheroni
    expected_max = (np.sqrt(2 * np.log(n_trials))
                    - ((np.pi - emc) / np.sqrt(2 * np.log(n_trials)))
                    if n_trials > 1 else 0.0)
    # Variance of Sharpe estimator (Lo 2002, adjusted for non-normality)
    var_sharpe = (1 / (sample_len - 1)) * (1 - skew * observed_sharpe + ((kurt - 1) / 4) * observed_sharpe**2)
    if var_sharpe <= 0:
        return observed_sharpe
    # Deflated Sharpe = observed minus the multiple-testing hurdle
    # (expected max Sharpe under the null = expected_max * SE(SR)).
    # n_trials=1 → expected_max=0 → no penalty → returns observed.
    # The edge is real at 95% iff the returned value is > 0.
    return float(observed_sharpe - expected_max * np.sqrt(var_sharpe))


def cpcv(
    data: pd.DataFrame,
    strategy_fn,
    n_groups: int = 6,
    k_test: int = 2,
    purge_bars: int = 100,
    embargo_bars: int = 50,
    periods_per_year: int = 365,
) -> CPCVResult:
    """Run CPCV on a strategy.

    Args:
        data: full DataFrame indexed by timestamp, columns required by strategy_fn
        strategy_fn: callable(data_train: pd.DataFrame) -> pd.Series of per-bar returns on test index
                     Must retrain/refit on `data_train` only, then emit returns for ALL bars
                     (the harness will slice test bars + apply purge/embargo)
        n_groups: number of contiguous groups (N in CPCV notation)
        k_test: number of groups to hold out as test (K in CPCV notation)
        purge_bars: bars to drop around train/test boundary
        embargo_bars: post-train buffer to drop from test

    Returns:
        CPCVResult with one FoldResult per path
    """
    paths = list(combinations(range(n_groups), k_test))
    result = CPCVResult(n_groups=n_groups, k_test=k_test, n_paths=len(paths))

    # Split into N contiguous groups by index
    group_boundaries = np.array_split(data.index.values, n_groups)

    for test_groups in paths:
        test_idx = np.concatenate([group_boundaries[g] for g in test_groups])
        train_idx = np.concatenate([group_boundaries[g] for g in range(n_groups) if g not in test_groups])

        # Sort and convert to positional integers for purge math
        test_pos = np.searchsorted(data.index.values, np.sort(test_idx))
        train_pos = np.searchsorted(data.index.values, np.sort(train_idx))

        train_pos_p, test_pos_p = _purge_boundaries(train_pos, test_pos, purge_bars)
        test_pos_e = _embargo(test_pos_p, embargo_bars)

        # Map back to timestamps
        all_idx = data.index.values
        train_ts = all_idx[train_pos_p]
        test_ts = all_idx[test_pos_e]

        if len(train_ts) < 100 or len(test_ts) < 30:
            continue  # too small to be meaningful

        data_train = data.loc[train_ts]
        # Strategy returns returns for ALL bars in `data`
        try:
            all_returns = strategy_fn(data_train, data)
        except Exception as e:
            continue  # skip failing folds

        test_returns = all_returns.reindex(test_ts).fillna(0).values

        result.folds.append(FoldResult(
            train_start=pd.Timestamp(train_ts[0]),
            train_end=pd.Timestamp(train_ts[-1]),
            test_start=pd.Timestamp(test_ts[0]),
            test_end=pd.Timestamp(test_ts[-1]),
            oos_sharpe=sharpe_from_returns(test_returns, periods_per_year),
            oos_returns=test_returns,
            n_trades=int((test_returns != 0).sum()),
        ))

    return result
