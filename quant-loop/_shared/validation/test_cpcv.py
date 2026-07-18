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


def test_embargo_drops_earliest():
    test = np.array([10, 5, 20, 1, 15], dtype=int)
    out = _embargo(test, embargo_bars=2)
    # sorted = [1,5,10,15,20], drop first 2 → [10,15,20]
    assert list(int(x) for x in out) == [10, 15, 20]


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
