"""Combinatorial Purged Cross-Validation (CPCV) harness.

Replaces the leaking oos_walk_forward.py. Per López de Prado AFML Ch.7:
- Split data into N groups
- Pick K groups as test, N-K as train, for all C(N,K) combinations ("paths")
- Purge train bars within `purge_bars` of EVERY train/test boundary
  (each contiguous test segment, not just the global test min/max)
- Embargo: drop train bars within `embargo_bars` AFTER each test window
  (AFML Ch.7: post-test train rows are serially correlated with the test
  period; the test set itself is never truncated)

Per-fold refit (parameter fitting happens on train only), evaluate on test,
aggregate across all paths to get OOS Sharpe distribution.

Includes Deflated Sharpe Ratio (Bailey & López de Prado 2014) for
multiple-testing correction.
"""
import math
from dataclasses import dataclass, field
from itertools import combinations
import numpy as np
import pandas as pd
from scipy.stats import norm as _norm

# Euler-Mascheroni constant, used by the DSR extreme-value approximation.
_EULER_MASCHERONI = 0.5772156649015329


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
    # True when the probe detected that strategy_fn ignores data_train
    # (returns are identical regardless of train subset). In that case the
    # CPCV measures temporal stability of a fixed-parameter backtest, NOT
    # true out-of-sample generalization. Results are still meaningful but
    # should be labelled as "temporal-stability" not "OOS".
    is_temporal_stability_only: bool = False

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


def _test_segments(test_idx: np.ndarray) -> list[tuple[int, int]]:
    """Split test positions into contiguous (start, end) segments (inclusive)."""
    if len(test_idx) == 0:
        return []
    s = np.sort(test_idx)
    breaks = np.where(np.diff(s) > 1)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(s) - 1]))
    return [(int(s[i]), int(s[j])) for i, j in zip(starts, ends)]


def _purge_boundaries(
    train_idx: np.ndarray, test_idx: np.ndarray, purge_bars: int
) -> tuple[np.ndarray, np.ndarray]:
    """Drop train bars within `purge_bars` of EVERY test segment boundary.

    AFML Ch.7 purge: a train sample whose label horizon overlaps the test
    window leaks, so train bars within `purge_bars` of each contiguous test
    segment must be dropped (both sides — a superset of the t1-horizon rule).

    The previous implementation purged only around the global min/max of the
    merged test set; with k_test >= 2 and non-contiguous test groups, the
    interior train/test boundaries were never purged → systematic leakage.
    """
    if purge_bars <= 0 or len(train_idx) == 0 or len(test_idx) == 0:
        return train_idx, test_idx
    mask = np.ones(len(train_idx), dtype=bool)
    for seg_start, seg_end in _test_segments(test_idx):
        mask &= ~((train_idx >= seg_start - purge_bars)
                  & (train_idx <= seg_end + purge_bars))
    return train_idx[mask], test_idx


def _embargo(
    train_idx: np.ndarray, test_idx: np.ndarray, embargo_bars: int
) -> np.ndarray:
    """Drop train bars within `embargo_bars` AFTER each test segment.

    AFML Ch.7 embargo: train samples immediately following the test window
    are serially correlated with the test period and must be excluded from
    training. The TEST set is never touched.

    The previous implementation dropped the head of the test set instead,
    which neither blocked the leakage (post-test train rows remained) and
    destroyed OOS samples for nothing.
    """
    if embargo_bars <= 0 or len(train_idx) == 0 or len(test_idx) == 0:
        return train_idx
    mask = np.ones(len(train_idx), dtype=bool)
    for _, seg_end in _test_segments(test_idx):
        mask &= ~((train_idx > seg_end) & (train_idx <= seg_end + embargo_bars))
    return train_idx[mask]


def sharpe_from_returns(returns: np.ndarray, periods_per_year: int = 365) -> float:
    """Annualized Sharpe from per-period returns."""
    if len(returns) < 2 or np.std(returns) <= 1e-12:
        return 0.0
    return float(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(periods_per_year))


def expected_max_sharpe_z(n_trials: int) -> float:
    """Standardized expected maximum of ``n_trials`` iid N(0, 1) Sharpe estimates.

    Bailey & López de Prado (2014) extreme-value approximation:

        E[max z] = (1 - γ) Φ⁻¹(1 - 1/N) + γ Φ⁻¹(1 - 1/(N·e))

    where γ is the Euler-Mascheroni constant and Φ⁻¹ the standard normal
    quantile function. A single trial has no maximum to correct for, so the
    expected maximum under the null is 0 (the formula diverges at N=1).

    Numerically identical to purgedcv's ``_expected_max_z`` reference.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if n_trials == 1:
        return 0.0
    return float(
        (1.0 - _EULER_MASCHERONI) * _norm.ppf(1.0 - 1.0 / n_trials)
        + _EULER_MASCHERONI * _norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    sample_len: int,
    skew: float = 0.0,
    kurt: float = 3.0,
    trial_sharpe_var: float | None = None,
) -> float:
    """Deflated Sharpe value per Bailey & López de Prado (2014).

    Computes the multiple-testing hurdle

        SR* = sqrt(V̂[{SRₙ}]) · E[max z]          (see expected_max_sharpe_z)

    and returns ``observed_sharpe - SR*``. The edge survives the
    multiple-testing correction iff the returned value is > 0 — exactly
    equivalent to the DSR probability (PSR evaluated at SR*) being > 0.5,
    since PSR is monotonic in the observed Sharpe.

    Args:
        observed_sharpe: the best Sharpe from n_trials backtests
        n_trials: number of strategies tried (family size)
        sample_len: length of the returns series (bars)
        skew: returns skewness (0 = normal); only used by the fallback below
        kurt: returns kurtosis (3 = normal); only used by the fallback below
        trial_sharpe_var: V̂[{SRₙ}] — variance of the Sharpe estimates across
            the n_trials candidates, in the same Sharpe units as
            ``observed_sharpe``. This is the canonical input of the spec
            formula. When omitted (caller only has a single trial's Sharpe),
            falls back to the Lo (2002) variance of the Sharpe estimator
            itself, (1/(T-1))(1 - γ₃·SR + (γ₄-1)/4·SR²), documented here so
            the substitution is an explicit choice, not an oversight.

    Returns:
        Deflated Sharpe value — if > 0, the edge is real after deflation.
    """
    if n_trials < 1 or sample_len < 2:
        return observed_sharpe
    if trial_sharpe_var is not None:
        if not np.isfinite(trial_sharpe_var) or trial_sharpe_var < 0:
            raise ValueError(f"trial_sharpe_var must be finite and >= 0, got {trial_sharpe_var}")
        var = float(trial_sharpe_var)
    else:
        # Fallback: variance of the single Sharpe estimator (Lo 2002,
        # adjusted for non-normality). NOT the across-trial variance V̂[{SRₙ}]
        # of the spec — pass trial_sharpe_var whenever the family of trial
        # Sharpes is known.
        var = (1.0 / (sample_len - 1)) * (
            1 - skew * observed_sharpe + ((kurt - 1) / 4.0) * observed_sharpe**2
        )
    if var <= 0:
        return observed_sharpe  # zero spread → no hurdle
    sr_star = float(np.sqrt(var) * expected_max_sharpe_z(n_trials))
    # n_trials=1 → expected_max=0 → no penalty → returns observed.
    return float(observed_sharpe - sr_star)


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
        purge_bars: bars around each train/test boundary to drop from train
        embargo_bars: post-test train bars to drop (test set is never cut)

    Returns:
        CPCVResult with one FoldResult per path
    """
    paths = list(combinations(range(n_groups), k_test))
    result = CPCVResult(n_groups=n_groups, k_test=k_test, n_paths=len(paths))

    # ── Strategy-level leak probe ──────────────────────────────────────
    # Detect whether strategy_fn actually uses data_train by comparing
    # outputs from two disjoint train subsets. If identical, the function
    # is ignoring train data → CPCV is temporal-stability, not true OOS.
    if len(data) >= 400:
        try:
            mid = len(data) // 2
            probe_a = strategy_fn(data.iloc[:mid], data)
            probe_b = strategy_fn(data.iloc[mid:], data)
            if probe_a is not None and probe_b is not None:
                result.is_temporal_stability_only = probe_a.equals(probe_b)
        except Exception:
            pass  # probe failure is non-fatal
    if result.is_temporal_stability_only:
        import warnings
        warnings.warn(
            "CPCV strategy_fn ignores data_train — results are temporal-"
            "stability (fixed-parameter sub-period analysis), NOT true OOS. "
            "If parameters were selected via sweep/optimization on full data, "
            "the CPCV Sharpe is optimistically biased. Fix strategy_fn to "
            "refit on data_train for true out-of-sample validation.",
            stacklevel=2,
        )
    # ───────────────────────────────────────────────────────────────────

    # Split into N contiguous groups by index
    group_boundaries = np.array_split(data.index.values, n_groups)

    for test_groups in paths:
        test_idx = np.concatenate([group_boundaries[g] for g in test_groups])
        train_idx = np.concatenate([group_boundaries[g] for g in range(n_groups) if g not in test_groups])

        # Sort and convert to positional integers for purge math
        test_pos = np.searchsorted(data.index.values, np.sort(test_idx))
        train_pos = np.searchsorted(data.index.values, np.sort(train_idx))

        train_pos_p, test_pos_p = _purge_boundaries(train_pos, test_pos, purge_bars)
        train_pos_e = _embargo(train_pos_p, test_pos_p, embargo_bars)

        # Map back to timestamps
        all_idx = data.index.values
        train_ts = all_idx[train_pos_e]
        test_ts = all_idx[test_pos_p]

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
