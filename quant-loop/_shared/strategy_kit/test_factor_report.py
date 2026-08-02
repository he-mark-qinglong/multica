"""Tests for _shared/strategy_kit/factor_report.py."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit.factor_report import (
    FactorReport, compute_factor_report, report_to_html,
    generate_library_report,
)
from _shared.strategy_kit.factor_library import list_factors

HEADINGS = [
    "Quantile Portfolio Returns",
    "IC by Period",
    "Quantile Turnover",
    "Factor Rank Autocorrelation",
]


def _ohlcv(n: int = 800, seed: int = 11) -> pd.DataFrame:
    """Synthetic OHLCV + funding/basis frame (same style as
    test_factor_library)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.002, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.002, n))),
            "close": close,
            "volume": rng.uniform(1e3, 1e5, n),
            "funding": rng.normal(0.0001, 0.0003, n),
            "basis": rng.normal(0.0002, 0.0005, n),
        },
        index=idx,
    )


def _signal_factor(n: int = 2000, seed: int = 0):
    """factor_t = next-bar return + small noise → known positive IC."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    rets = pd.Series(rng.normal(0, 0.01, n), index=idx)
    close = 100 * np.exp(np.cumsum(rets))
    factor = rets.shift(-1) + pd.Series(rng.normal(0, 0.002, n), index=idx)
    return factor, pd.Series(close, index=idx)


# ---------------------------------------------------------------------------
# Component correctness on factors with known properties
# ---------------------------------------------------------------------------

def test_signal_factor_positive_ic_and_monotonic_quantiles():
    factor, close = _signal_factor()
    rep = compute_factor_report(factor, close, quantiles=5, periods=(1, 5, 10))
    assert isinstance(rep, FactorReport)
    # IC at horizon 1 must be clearly positive
    assert rep.ic_by_period.loc[1, "ic"] > 0.3
    # quantile mean forward returns (h=1) must be monotonically increasing
    means = rep.quantile_returns["1"].drop(labels=["top_minus_bottom"])
    diffs = np.diff(means.values)
    assert (diffs > -1e-4).all(), f"non-monotonic quantile means: {means.values}"
    # spread row = top - bottom must be positive
    assert rep.quantile_returns.loc["top_minus_bottom", "1"] > 0.0


def test_white_noise_factor_ic_near_zero():
    rng = np.random.default_rng(1)
    n = 2000
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    rets = rng.normal(0, 0.01, n)
    close = pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)
    factor = pd.Series(rng.normal(0, 1, n), index=idx)
    rep = compute_factor_report(factor, close, quantiles=5, periods=(1, 5))
    for h in (1, 5):
        assert abs(rep.ic_by_period.loc[h, "ic"]) < 0.1


def test_turnover_bounds_and_ordering():
    """Turnover ∈ [0,1]; persistent factor turns over less than noise."""
    _, close = _signal_factor()
    n = len(close)
    idx = close.index
    rng = np.random.default_rng(2)
    slow = pd.Series(np.convolve(
        rng.normal(0, 1, n), np.ones(50) / 50, mode="same"), index=idx)
    noise = pd.Series(rng.normal(0, 1, n), index=idx)
    rep_slow = compute_factor_report(slow, close)
    rep_noise = compute_factor_report(noise, close)
    for rep in (rep_slow, rep_noise):
        t = rep.quantile_turnover["turnover"]
        assert ((t >= 0.0) & (t <= 1.0)).all()
    assert (rep_slow.quantile_turnover.loc["overall", "turnover"]
            < rep_noise.quantile_turnover.loc["overall", "turnover"])


def test_rank_autocorrelation_ordering():
    """Slowly-varying factor: rank autocorr near 1; white noise near 0."""
    _, close = _signal_factor()
    n = len(close)
    idx = close.index
    rng = np.random.default_rng(3)
    slow = pd.Series(np.convolve(
        rng.normal(0, 1, n), np.ones(50) / 50, mode="same"), index=idx)
    noise = pd.Series(rng.normal(0, 1, n), index=idx)
    ra_slow = compute_factor_report(slow, close).rank_autocorrelation
    ra_noise = compute_factor_report(noise, close).rank_autocorrelation
    assert ra_slow.loc[1, "rank_autocorr"] > 0.9
    assert abs(ra_noise.loc[1, "rank_autocorr"]) < 0.2
    assert (ra_slow.loc[1, "rank_autocorr"]
            > ra_noise.loc[1, "rank_autocorr"])


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def test_html_contains_all_four_sections():
    factor, close = _signal_factor()
    rep = compute_factor_report(factor, close)
    html = report_to_html(rep)
    for heading in HEADINGS:
        assert heading in html


def test_skipped_report_html():
    rep = FactorReport(
        name="x", direction=1, n_obs=0, quantiles=5,
        quantile_returns=pd.DataFrame(), ic_by_period=pd.DataFrame(),
        quantile_turnover=pd.DataFrame(), rank_autocorrelation=pd.DataFrame(),
        skipped=True, skip_reason="missing required columns ['volume']")
    html = report_to_html(rep)
    assert "skipped" in html and "missing required columns" in html


# ---------------------------------------------------------------------------
# Robustness / causality
# ---------------------------------------------------------------------------

def test_constant_factor_raises():
    _, close = _signal_factor()
    factor = pd.Series(1.0, index=close.index)
    with pytest.raises(ValueError, match="constant"):
        compute_factor_report(factor, close)


def test_insufficient_obs_raises():
    _, close = _signal_factor()
    factor = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    with pytest.raises(ValueError, match="insufficient"):
        compute_factor_report(factor.iloc[:15], close.iloc[:15])


def test_inf_values_dropped():
    factor, close = _signal_factor()
    factor = factor.copy()
    factor.iloc[10] = np.inf
    rep = compute_factor_report(factor, close)
    expected = int(factor.replace([np.inf, -np.inf], np.nan).notna().sum())
    assert rep.n_obs == expected


# ---------------------------------------------------------------------------
# Library-wide report
# ---------------------------------------------------------------------------

def test_generate_library_report_all_factors(tmp_path):
    data = _ohlcv(n=800)
    out = tmp_path / "factor_report.html"
    reports = generate_library_report(data, out_path=str(out))
    assert set(reports) == set(list_factors())
    for name, rep in reports.items():
        assert not rep.skipped, f"{name} unexpectedly skipped: {rep.skip_reason}"
        assert rep.n_obs >= 50
    html = out.read_text()
    for heading in HEADINGS:
        assert heading in html
    for name in list_factors():
        assert name in html


def test_generate_library_report_skips_missing_columns():
    data = _ohlcv(n=800).drop(columns=["volume"])
    reports = generate_library_report(data)
    assert reports["volume_zscore"].skipped
    assert "missing required columns" in reports["volume_zscore"].skip_reason
    # close-only factors still report fine
    assert not reports["reversal_5d"].skipped
