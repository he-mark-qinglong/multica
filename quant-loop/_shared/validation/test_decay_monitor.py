"""Tests for ``_shared/validation/decay_monitor.py`` (G20)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import math

import numpy as np
import pandas as pd
import pytest

from _shared.validation.decay_monitor import (
    DecayReport,
    half_life_years,
    ic_slope_per_year,
    monitor_decay,
    rolling_ic,
    rolling_sharpe,
)

N = 400  # ~13 months of daily bars


def _idx(n=N):
    return pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")


def _make_series(ic_fn, noise=0.05, seed=3):
    """Signal + forward returns with windowed rank-IC following ic_fn(t).

    forward = ic_t * z + noise * eps, signal = z — monotone relation, so
    windowed Spearman ≈ Pearson of the construction.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(N)
    z = rng.normal(size=N)
    eps = rng.normal(size=N)
    fwd = ic_fn(t) * z + noise * eps
    return pd.Series(z, index=_idx()), pd.Series(fwd, index=_idx())


def test_perfect_signal_alive_and_high_ic():
    sig, fwd = _make_series(lambda t: np.full(N, 0.8))
    rep = monitor_decay(sig, fwd, window=30, recent=10)
    assert isinstance(rep, DecayReport)
    assert rep.status == "alive"
    assert rep.recent_ic > 0.8
    assert abs(rep.ic_slope_per_year) < 0.05


def test_dead_signal_detected():
    sig, fwd = _make_series(lambda t: np.zeros(N))
    rep = monitor_decay(sig, fwd, window=30, recent=10)
    assert rep.status == "dead"
    assert rep.recent_ic <= 0.0 or abs(rep.recent_ic) < 0.1


def test_decaying_signal_detected_with_negative_slope():
    # Strong IC for the first 60% of the sample, fading to ~0.
    strength = np.clip(1.0 - np.arange(N) / (0.6 * N), 0.0, 1.0) * 0.8
    sig, fwd = _make_series(lambda t: strength[t])
    rep = monitor_decay(sig, fwd, window=30, recent=10)
    assert rep.status in ("decaying", "dead")
    assert rep.ic_slope_per_year < 0
    assert rep.early_ic > rep.recent_ic


def test_half_life_recovers_exponential_decay():
    # Construct rolling IC ≈ 0.6 * exp(-λt), λ = ln2 / 0.5 (half-life 0.5y).
    hl_true = 0.5
    lam = math.log(2.0) / hl_true
    years = np.arange(N) / 365.25
    idx = _idx()
    ic_series = pd.Series(0.6 * np.exp(-lam * years) + 0.001, index=idx)
    hl = half_life_years(ic_series)
    assert hl == pytest.approx(hl_true, rel=0.05)


def test_half_life_none_when_ic_rising():
    idx = _idx()
    ic_series = pd.Series(np.linspace(0.1, 0.5, N), index=idx)
    assert half_life_years(ic_series) is None


def test_rolling_ic_matches_spearman():
    rng = np.random.default_rng(11)
    sig = pd.Series(rng.normal(size=50), index=_idx(50))
    fwd = pd.Series(rng.normal(size=50), index=_idx(50))
    ic = rolling_ic(sig, fwd, window=20)
    assert math.isnan(ic.iloc[18])
    # Manual Spearman over the first full window.
    sw = sig.iloc[:20].rank()
    rw = fwd.iloc[:20].rank()
    expected = np.corrcoef(sw, rw)[0, 1]
    assert ic.iloc[19] == pytest.approx(expected)


def test_rolling_sharpe_sign_and_scale():
    idx = _idx(120)
    rets = pd.Series(np.full(120, 0.001), index=idx)
    # Constant returns → zero std → NaN, not inf.
    sharpe = rolling_sharpe(rets, window=30)
    assert sharpe.dropna().empty
    rng = np.random.default_rng(5)
    rets2 = pd.Series(rng.normal(0.002, 0.01, 120), index=idx)
    sharpe2 = rolling_sharpe(rets2, window=30, periods_per_year=365.0)
    assert (sharpe2.dropna() != 0).any()


def test_ic_slope_per_year_linear_case():
    idx = _idx(365)
    ic = pd.Series(0.5 - np.arange(365) / 365.0 * 0.25, index=idx)
    slope = ic_slope_per_year(ic)
    assert slope == pytest.approx(-0.25, rel=0.01)


def test_short_series_degrades_gracefully():
    idx = _idx(5)
    sig = pd.Series([1, 2, 3, 4, 5], index=idx, dtype=float)
    fwd = pd.Series([1, 2, 3, 4, 5], index=idx, dtype=float)
    rep = monitor_decay(sig, fwd, window=3, recent=2)
    assert rep.status in ("alive", "decaying", "dead")


def test_report_is_frozen():
    sig, fwd = _make_series(lambda t: np.full(N, 0.5))
    rep = monitor_decay(sig, fwd, window=30)
    with pytest.raises(Exception):
        rep.status = "dead"
