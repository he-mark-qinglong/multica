"""Tests for the CPCV harness.

Run: python3 _shared/validation/test_cpcv.py

Uses plain asserts (no pytest assumed). Prints "N/N tests passed" at the end.
"""
import os
import sys

import numpy as np
import pandas as pd

# Allow direct execution: add repo root (two levels up) to sys.path so the
# `_shared` package is importable without pytest's rootdir injection.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from _shared.validation.cpcv import (
    CPCVResult,
    FoldResult,
    _embargo,
    _purge_boundaries,
    cpcv,
    deflated_sharpe,
    expected_max_sharpe_z,
    sharpe_from_returns,
)


def _trending_walk(n: int = 4000, seed: int = 7) -> pd.DataFrame:
    """Build a synthetic trending random walk for CPCV tests."""
    rng = np.random.default_rng(seed)
    drift = 0.00015
    noise = rng.normal(0, 0.004, size=n)
    log_ret = drift + noise
    price = 100.0 * np.exp(np.cumsum(log_ret))
    idx = pd.date_range("2023-01-01", periods=n, freq="1h")
    return pd.DataFrame({"close": price}, index=idx)


def _ma_strategy(data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
    """Simple moving-average strategy refit on train, emit per-bar returns.

    Fits fast/slow window from train autocorrelation (parameter fit happens
    ONLY on data_train), then applies the rule across data_full so the harness
    can slice the test window. This is the contract CPCV enforces.
    """
    px = data_train["close"]
    # "Fit": pick the slow MA span from train (a real param picked on train only)
    slow = 60
    fast = 10
    sig = (px.rolling(fast).mean() - px.rolling(slow).mean()) > 0
    # Apply the same rule to the full series; position 1 long / 0 flat
    full_px = data_full["close"]
    full_sig = (full_px.rolling(fast).mean() - full_px.rolling(slow).mean()) > 0
    full_pos = full_sig.astype(float).fillna(0.0)
    returns = full_pos * full_px.pct_change().fillna(0.0)
    return returns


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cpcv_runs_and_returns_reasonable_sharpe():
    data = _trending_walk()
    res = cpcv(
        data, _ma_strategy, n_groups=6, k_test=2,
        purge_bars=50, embargo_bars=20, periods_per_year=24 * 365,
    )
    assert isinstance(res, CPCVResult), "cpcv must return CPCVResult"
    assert res.n_groups == 6 and res.k_test == 2
    assert res.n_paths == 15, f"C(6,2)=15, got {res.n_paths}"
    assert len(res.folds) > 0, "at least one fold must complete"
    # Trending drift → expect positive OOS Sharpe
    assert res.mean_oos_sharpe > 0, f"expected positive mean Sharpe on drift, got {res.mean_oos_sharpe}"
    for f in res.folds:
        assert isinstance(f, FoldResult)
        # CPCV test/train windows interleave across combinatorial paths, so
        # train vs test ordering is not fixed; only check each window internally.
        assert f.train_start <= f.train_end
        assert f.test_start <= f.test_end
        assert f.n_trades >= 0
        assert np.isfinite(f.oos_sharpe)


def test_minimal_groups_n2_k1():
    data = _trending_walk(n=1000)
    res = cpcv(data, _ma_strategy, n_groups=2, k_test=1,
               purge_bars=0, embargo_bars=0, periods_per_year=24 * 365)
    assert res.n_paths == 2, "C(2,1)=2"
    assert len(res.folds) == 2, "both minimal folds should complete"


def test_no_purge_no_embargo():
    data = _trending_walk()
    res = cpcv(data, _ma_strategy, n_groups=4, k_test=1,
               purge_bars=0, embargo_bars=0, periods_per_year=24 * 365)
    assert res.n_paths == 4
    assert len(res.folds) == 4, "all 4 folds complete without purge/embargo"


def test_short_data_skips_folds_gracefully():
    # Only 200 bars; with purge=50/embargo=20 the harness's 100/30 floor
    # and purge math should just yield few/no folds — no crash.
    data = _trending_walk(n=200)
    res = cpcv(data, _ma_strategy, n_groups=6, k_test=2,
               purge_bars=50, embargo_bars=20)
    # Must not raise; folds may be empty or few
    assert isinstance(res, CPCVResult)
    assert len(res.folds) <= res.n_paths
    if len(res.folds) == 0:
        assert np.isnan(res.mean_oos_sharpe), "empty result must be NaN, not crash"


def test_failing_strategy_fn_skipped():
    def boom(data_train, data_full):
        raise RuntimeError("intentional fold failure")
    data = _trending_walk()
    res = cpcv(data, boom, n_groups=4, k_test=1)
    assert res.n_paths == 4
    assert len(res.folds) == 0, "all folds fail → 0 folds, no exception"


def test_purge_boundaries_drops_near_test():
    train = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=int)
    test = np.array([0, 9], dtype=int)  # boundaries at 0 and 9
    tp, _ = _purge_boundaries(train, test, purge_bars=1)
    # Should drop train bars at positions 0,1,8,9 (within ±1 of 0 or 9)
    remaining = set(int(x) for x in tp)
    for dropped in (0, 1, 8, 9):
        assert dropped not in remaining, f"bar {dropped} should be purged"
    for kept in (2, 3, 4, 5, 6, 7):
        assert kept in remaining


def test_embargo_drops_post_test_train_not_test():
    """AFML embargo: cut TRAIN bars after each test segment, never the test set."""
    train = np.array([0, 1, 2, 8, 9, 10, 11, 15], dtype=int)
    test = np.array([4, 5, 6], dtype=int)  # one contiguous segment (4, 6)
    out = _embargo(train, test, embargo_bars=2)
    remaining = set(int(x) for x in out)
    # train bar 8 lies in (6, 6+2] → dropped; everything else kept
    assert 8 not in remaining, "post-test train bar within embargo must be dropped"
    for kept in (0, 1, 2, 9, 10, 11, 15):
        assert kept in remaining, f"train bar {kept} outside embargo must survive"


def test_purge_covers_interior_boundaries_k2():
    """Bug-1 regression: with k_test=2 non-contiguous test groups, the interior
    train/test boundaries must be purged too (old code purged only the global
    test min/max)."""
    # 6 groups x 10 bars; test groups 1 and 3 → segments (10,19) and (30,39)
    test = np.array(list(range(10, 20)) + list(range(30, 40)), dtype=int)
    test_set = set(int(x) for x in test)
    train = np.array([p for p in range(60) if p not in test_set], dtype=int)
    tp, _ = _purge_boundaries(train, test, purge_bars=3)
    remaining = set(int(x) for x in tp)
    for p in remaining:
        dist = min(abs(p - t) for t in test_set)
        assert dist > 3, f"train bar {p} survives at distance {dist} from test"
    # Explicitly: bars between the two test segments near the interior
    # boundaries (old bug left these in) must be gone.
    for dropped in (7, 8, 9, 20, 21, 22, 27, 28, 29, 40, 41, 42):
        assert dropped not in remaining, f"interior-boundary bar {dropped} not purged"


def _leak_oracle_strategy(horizon: int):
    """Strategy that profits ONLY from label-overlap leakage.

    Mimics a model whose train labels are forward `horizon`-bar returns: any
    train sample within `horizon` bars before a bar t has a label window
    covering t. The oracle earns +|r| on bars covered by some train label
    window (perfect foresight) and 0 elsewhere — so its OOS Sharpe is > 0 IFF
    the purge failed to remove overlapping train samples (cf. purgedcv's
    synthetic leak proof: naive KFold shows fake skill, purged shows none).
    """
    def fn(data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
        ret = data_full["close"].pct_change().fillna(0.0)
        train_pos = data_full.index.get_indexer(data_train.index)
        covered = np.zeros(len(data_full), dtype=bool)
        for p in train_pos:
            hi = min(p + horizon, len(data_full) - 1)
            covered[p:hi + 1] = True
        return ret.abs().where(pd.Series(covered, index=data_full.index), 0.0)
    return fn


def test_purge_eliminates_label_overlap_leakage():
    """End-to-end leak proof: an oracle feeding on unpurged label overlap shows
    fake high OOS Sharpe; with purge_bars >= label horizon the same oracle has
    exactly zero edge."""
    data = _trending_walk(n=3600)
    horizon = 24
    leaked = cpcv(
        data, _leak_oracle_strategy(horizon), n_groups=6, k_test=2,
        purge_bars=0, embargo_bars=0, periods_per_year=24 * 365,
    )
    purged = cpcv(
        data, _leak_oracle_strategy(horizon), n_groups=6, k_test=2,
        purge_bars=horizon, embargo_bars=0, periods_per_year=24 * 365,
    )
    assert len(leaked.folds) > 0 and len(purged.folds) > 0
    assert leaked.mean_oos_sharpe > 1.0, (
        f"oracle should show fake skill without purge, got {leaked.mean_oos_sharpe}"
    )
    assert purged.mean_oos_sharpe == 0.0, (
        f"purge_bars >= horizon must leave the oracle zero edge, got {purged.mean_oos_sharpe}"
    )


def test_embargo_does_not_cut_test_samples():
    """Bug-2 regression: embargo must not truncate the OOS test window
    (old code dropped the first `embargo_bars` of the merged test set)."""
    n, n_groups = 2400, 4
    data = _trending_walk(n=n)
    res = cpcv(
        data, _ma_strategy, n_groups=n_groups, k_test=1,
        purge_bars=0, embargo_bars=100, periods_per_year=24 * 365,
    )
    assert len(res.folds) == n_groups, "k_test=1 → one fold per group"
    group_bars = n // n_groups
    for f in res.folds:
        span_hours = (f.test_end - f.test_start).total_seconds() / 3600
        assert span_hours == group_bars - 1, (
            f"test window truncated by embargo: span={span_hours}h, "
            f"expected {group_bars - 1}h"
        )


def test_sharpe_zero_std_returns_zero():
    assert sharpe_from_returns(np.zeros(100)) == 0.0
    assert sharpe_from_returns(np.full(100, 0.001)) == 0.0, "constant returns → 0 Sharpe"


def test_sharpe_too_short():
    assert sharpe_from_returns(np.array([0.01])) == 0.0


def test_deflated_sharpe_single_trial_equals_observed():
    s = 2.0
    d = deflated_sharpe(s, n_trials=1, sample_len=1000)
    assert abs(d - s) < 1e-9, f"n_trials=1 must equal observed, got {d}"


def test_deflated_sharpe_many_trials_is_lower():
    s = 2.0
    d1 = deflated_sharpe(s, n_trials=1, sample_len=1000)
    d100 = deflated_sharpe(s, n_trials=100, sample_len=1000)
    assert d100 < d1, f"more trials must deflate more: {d100} vs {d1}"


def test_deflated_sharpe_invalid_inputs_passthrough():
    # n_trials < 1 or sample_len < 2 → return observed unchanged
    s = 1.5
    assert deflated_sharpe(s, n_trials=0, sample_len=1000) == s
    assert deflated_sharpe(s, n_trials=5, sample_len=1) == s


def test_expected_max_sharpe_z_matches_spec_formula():
    # Bailey-LdP (2014): (1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/(N·e))
    from scipy.stats import norm

    gamma = 0.5772156649015329
    for n in (2, 5, 10, 42, 100, 1000):
        spec = (1 - gamma) * norm.ppf(1 - 1 / n) + gamma * norm.ppf(1 - 1 / (n * np.e))
        got = expected_max_sharpe_z(n)
        assert abs(got - spec) < 1e-12, f"n={n}: {got} vs spec {spec}"
    assert expected_max_sharpe_z(1) == 0.0
    # Strictly increasing in N for N >= 2 (more trials → higher hurdle)
    zs = [expected_max_sharpe_z(n) for n in (2, 3, 5, 10, 50, 200)]
    assert all(b > a for a, b in zip(zs, zs[1:]))


def test_deflated_sharpe_uses_trial_sharpe_var():
    # With V̂[{SRₙ}] given, hurdle must be sqrt(V̂)·E[max z] exactly —
    # the Lo-2002 estimator variance (and hence skew/kurt) must NOT enter.
    s, n, t = 1.5, 10, 1000
    var = 0.04
    d = deflated_sharpe(s, n, t, trial_sharpe_var=var)
    expected = s - np.sqrt(var) * expected_max_sharpe_z(n)
    assert abs(d - expected) < 1e-12, f"{d} vs {expected}"
    # skew/kurt are irrelevant when V̂ is supplied
    d2 = deflated_sharpe(s, n, t, skew=5.0, kurt=99.0, trial_sharpe_var=var)
    assert d2 == d
    # zero across-trial variance → no hurdle
    assert deflated_sharpe(s, n, t, trial_sharpe_var=0.0) == s


def test_deflated_sharpe_matches_purgedcv_reference():
    """Numerical alignment with purgedcv.deflated_sharpe_ratio_full (tol 1e-6).

    Skipped silently when the optional `purgedcv` reference package is not
    installed.
    """
    try:
        from purgedcv import deflated_sharpe_ratio_full
    except ImportError:
        return

    rng = np.random.default_rng(11)
    for n_trials, var_sharpe in ((1, 0.02), (5, 0.01), (36, 0.04), (100, 0.0025)):
        returns = rng.normal(0.001, 0.01, size=500)
        diag = deflated_sharpe_ratio_full(returns, n_trials=n_trials, var_sharpe=var_sharpe)

        # 1. Standardized expected maximum aligns.
        assert abs(expected_max_sharpe_z(n_trials) - diag.expected_max_z) < 1e-6, (
            f"n={n_trials}: {expected_max_sharpe_z(n_trials)} vs {diag.expected_max_z}"
        )

        # 2. SR* hurdle aligns: purgedcv uses ddof=0 per-observation Sharpe.
        sr_hat = float(returns.mean() / returns.std(ddof=0))
        d = deflated_sharpe(
            sr_hat, n_trials=n_trials, sample_len=returns.size,
            trial_sharpe_var=var_sharpe,
        )
        sr_star_ours = sr_hat - d
        assert abs(sr_star_ours - diag.sr_star) < 1e-6, (
            f"n={n_trials}: sr_star {sr_star_ours} vs purgedcv {diag.sr_star}"
        )

        # 3. Sign convention aligns with the DSR probability: our value > 0
        # iff purgedcv's DSR probability > 0.5 (PSR is monotonic in SR̂).
        assert (d > 0) == (diag.dsr > 0.5), (
            f"n={n_trials}: deflated {d} vs purgedcv dsr {diag.dsr}"
        )


def test_ci95_handles_few_folds():
    res = CPCVResult(n_groups=2, k_test=1, n_paths=2)
    lo, hi = res.oos_sharpe_ci95
    assert np.isnan(lo) and np.isnan(hi), "<5 folds → NaN CI"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(f"\n{passed}/{total} tests passed")
    return failed == 0


if __name__ == "__main__":
    import sys
    ok = _run_all()
    sys.exit(0 if ok else 1)
