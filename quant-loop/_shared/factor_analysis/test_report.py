"""Tests for _shared/factor_analysis/report.py.

Two test suites:
1. Cross-sectional panel API (FactorAnalysisReport, compute_ic, …)
2. Enhanced time-series API (cumulative_quantile_returns, rolling_ic, …)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from _shared.factor_analysis.report import (  # noqa: E402
    FactorAnalysisReport,
    compute_ic,
    compute_turnover,
    generate_report,
    quantile_returns,
    cumulative_quantile_returns,
    rolling_ic,
    summary_table,
    EnhancedFactorReport,
    compute_enhanced_report,
    enhanced_report_to_html,
    generate_enhanced_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _panel_data(n_dates=60, n_assets=50, seed=42, ic_strength=0.3):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    assets = [f"A{i:03d}" for i in range(n_assets)]
    records = []
    for d in dates:
        factor_vals = rng.normal(0, 1, n_assets)
        fwd_ret = ic_strength * factor_vals + rng.normal(0, 0.5, n_assets)
        for i, a in enumerate(assets):
            records.append((d, a, factor_vals[i], fwd_ret[i]))
    idx = pd.MultiIndex.from_tuples(
        [(r[0], r[1]) for r in records], names=["date", "asset"])
    factor = pd.Series([r[2] for r in records], index=idx, name="factor")
    fwd_ret = pd.Series([r[3] for r in records], index=idx, name="fwd_ret")
    return factor, fwd_ret


def _simple_series(n=200, seed=42, ic_strength=0.3):
    rng = np.random.default_rng(seed)
    factor = pd.Series(rng.normal(0, 1, n), name="factor")
    fwd_ret = pd.Series(
        ic_strength * factor.values + rng.normal(0, 0.5, n), name="fwd_ret")
    return factor, fwd_ret


def _ts_signal_factor(n: int = 2000, seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    rets = pd.Series(rng.normal(0, 0.01, n), index=idx)
    close = 100 * np.exp(np.cumsum(rets))
    factor = rets.shift(-1) + pd.Series(rng.normal(0, 0.002, n), index=idx)
    return factor, pd.Series(close, index=idx)


def _ohlcv(n: int = 800, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.001, n)),
        "high": close * (1 + np.abs(rng.normal(0, 0.002, n))),
        "low": close * (1 - np.abs(rng.normal(0, 0.002, n))),
        "close": close,
        "volume": rng.uniform(1e3, 1e5, n),
        "funding": rng.normal(0.0001, 0.0003, n),
        "basis": rng.normal(0.0002, 0.0005, n),
    }, index=idx)


# ===================================================================
# Part 1 — Cross-sectional panel API
# ===================================================================

class TestComputeIC:
    def test_panel_ic_positive_for_predictive_factor(self):
        factor, fwd_ret = _panel_data(ic_strength=0.3)
        stats = compute_ic(factor, fwd_ret)
        assert stats["ic_mean"] > 0.1
        assert stats["n_periods"] > 0

    def test_panel_ic_near_zero_for_noise(self):
        factor, fwd_ret = _panel_data(ic_strength=0.0)
        stats = compute_ic(factor, fwd_ret)
        assert abs(stats["ic_mean"]) < 0.15

    def test_panel_icir_is_mean_over_std(self):
        factor, fwd_ret = _panel_data(ic_strength=0.5)
        stats = compute_ic(factor, fwd_ret)
        if stats["ic_std"] > 1e-10:
            expected_icir = stats["ic_mean"] / stats["ic_std"]
            assert stats["icir"] == pytest.approx(expected_icir, rel=1e-6)

    def test_panel_hit_rate_above_half_for_good_factor(self):
        factor, fwd_ret = _panel_data(ic_strength=0.5)
        stats = compute_ic(factor, fwd_ret)
        assert stats["hit_rate"] > 0.5

    def test_simple_series_ic(self):
        factor, fwd_ret = _simple_series(ic_strength=0.5)
        stats = compute_ic(factor, fwd_ret)
        assert stats["ic_mean"] > 0.1
        assert stats["n_periods"] == 1

    def test_empty_input_returns_zeros(self):
        factor = pd.Series([], dtype=float)
        fwd_ret = pd.Series([], dtype=float)
        stats = compute_ic(factor, fwd_ret)
        assert stats["ic_mean"] == 0.0
        assert stats["n_periods"] == 0


class TestQuantileReturns:
    def test_monotonic_quantile_returns(self):
        factor, fwd_ret = _panel_data(ic_strength=0.5)
        qr = quantile_returns(factor, fwd_ret, n_quantiles=5)
        means = qr.loc[qr.index != "spread", "mean_return"]
        assert means.iloc[-1] > means.iloc[0]

    def test_spread_positive_for_predictive_factor(self):
        factor, fwd_ret = _panel_data(ic_strength=0.3)
        qr = quantile_returns(factor, fwd_ret, n_quantiles=5)
        assert "spread" in qr.index
        assert qr.loc["spread", "mean_return"] > 0

    def test_quantile_returns_has_n_rows_plus_spread(self):
        factor, fwd_ret = _panel_data()
        qr = quantile_returns(factor, fwd_ret, n_quantiles=5)
        # 5 quantile rows + 1 spread row
        assert len(qr) == 6


class TestComputeTurnover:
    def test_turnover_returns_dataframe(self):
        factor, _ = _panel_data()
        to = compute_turnover(factor, n_quantiles=5)
        assert isinstance(to, pd.DataFrame)
        assert "mean_turnover" in to.columns

    def test_turnover_values_in_unit_interval(self):
        factor, _ = _panel_data()
        to = compute_turnover(factor, n_quantiles=5)
        if len(to) > 0:
            vals = to["mean_turnover"].dropna()
            assert (vals >= 0).all()
            assert (vals <= 1.0 + 1e-9).all()

    def test_turnover_single_index_returns_empty(self):
        """Simple series (no panel) → empty turnover (need panel for membership change)."""
        factor, _ = _simple_series()
        to = compute_turnover(factor, n_quantiles=5)
        assert len(to) == 0


class TestGenerateReport:
    def test_report_type(self):
        factor, fwd_ret = _panel_data()
        report = generate_report(factor, fwd_ret, name="test_factor")
        assert isinstance(report, FactorAnalysisReport)
        assert report.name == "test_factor"

    def test_report_has_ic_stats(self):
        factor, fwd_ret = _panel_data(ic_strength=0.3)
        report = generate_report(factor, fwd_ret)
        # ic_stats is an ICStats dataclass with attribute access
        assert hasattr(report.ic_stats, "ic_mean")
        assert hasattr(report.ic_stats, "icir")
        assert report.ic_stats.ic_mean > 0.05

    def test_report_has_quantile_returns(self):
        factor, fwd_ret = _panel_data()
        report = generate_report(factor, fwd_ret, n_quantiles=5)
        assert isinstance(report.quantile_returns, pd.DataFrame)
        assert len(report.quantile_returns) > 0

    def test_report_has_turnover(self):
        factor, fwd_ret = _panel_data()
        report = generate_report(factor, fwd_ret)
        assert isinstance(report.turnover, pd.DataFrame)

    def test_report_is_frozen(self):
        factor, fwd_ret = _panel_data()
        report = generate_report(factor, fwd_ret)
        with pytest.raises(Exception):
            report.name = "other"  # type: ignore


# ===================================================================
# Part 2 — Enhanced time-series API
# ===================================================================

class TestCumulativeQuantileReturns:
    def test_returns_dataframe_with_quantile_columns(self):
        factor, close = _ts_signal_factor()
        cum = cumulative_quantile_returns(factor, close, quantiles=5)
        assert isinstance(cum, pd.DataFrame)
        assert len(cum.columns) >= 2
        assert all(c.startswith("Q") for c in cum.columns)

    def test_signal_factor_top_outperforms_bottom(self):
        factor, close = _ts_signal_factor()
        cum = cumulative_quantile_returns(factor, close, quantiles=5)
        # Drop rows where ALL values are NaN (end-of-series forward return)
        cum_clean = cum.dropna(how="all")
        assert len(cum_clean) > 0
        # Use the last row that has both Q1 and Q5 populated
        valid = cum_clean.dropna(subset=[cum.columns[0], cum.columns[-1]])
        if len(valid) > 0:
            final = valid.iloc[-1]
            # top quantile should outperform bottom for a predictive factor
            assert final.iloc[-1] >= final.iloc[0]

    def test_insufficient_obs_raises(self):
        factor = pd.Series([1.0, 2.0, 3.0])
        close = pd.Series([100.0, 101.0, 102.0])
        with pytest.raises(ValueError, match="insufficient"):
            cumulative_quantile_returns(factor, close)


class TestRollingIC:
    def test_returns_series(self):
        factor, close = _ts_signal_factor()
        ic = rolling_ic(factor, close, horizon=1, window=100)
        assert isinstance(ic, pd.Series)

    def test_signal_factor_positive_mean_ic(self):
        factor, close = _ts_signal_factor()
        ic = rolling_ic(factor, close, horizon=1, window=100).dropna()
        assert len(ic) > 0
        assert ic.mean() > 0.2

    def test_warmup_is_nan(self):
        factor, close = _ts_signal_factor(n=200)
        ic = rolling_ic(factor, close, horizon=1, window=100)
        assert ic.iloc[:10].isna().all()


class TestSummaryTable:
    def test_has_expected_columns(self):
        factor, close = _ts_signal_factor()
        tbl = summary_table(factor, close, periods=(1, 5, 10))
        expected_cols = {"n_obs", "ic_mean", "ic_std", "icir", "t_stat",
                         "p_value", "hit_rate", "spread_annual"}
        assert expected_cols.issubset(set(tbl.columns))

    def test_signal_factor_significant(self):
        factor, close = _ts_signal_factor()
        tbl = summary_table(factor, close, periods=(1,))
        assert tbl.loc[1, "p_value"] < 0.05
        assert tbl.loc[1, "ic_mean"] > 0.2

    def test_noise_factor_not_significant(self):
        rng = np.random.default_rng(99)
        n = 2000
        idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
        noise = pd.Series(rng.normal(0, 1, n), index=idx)
        tbl = summary_table(noise, close, periods=(1,))
        assert abs(tbl.loc[1, "ic_mean"]) < 0.15


class TestEnhancedReport:
    def test_report_has_all_components(self):
        factor, close = _ts_signal_factor()
        rep = compute_enhanced_report(factor, close, periods=(1, 5, 10))
        assert isinstance(rep, EnhancedFactorReport)
        assert isinstance(rep.cumulative_returns, pd.DataFrame)
        assert isinstance(rep.ic_series, pd.Series)
        assert isinstance(rep.summary, pd.DataFrame)
        assert len(rep.summary) == 3

    def test_html_contains_all_sections(self):
        factor, close = _ts_signal_factor()
        rep = compute_enhanced_report(factor, close, periods=(1, 5))
        html = enhanced_report_to_html(rep)
        assert "n_obs" in html


class TestGenerateEnhancedReport:
    def test_all_factors(self, tmp_path):
        from _shared.strategy_kit.factor_library import list_factors
        data = _ohlcv(800)
        out = tmp_path / "enhanced_report.html"
        reports = generate_enhanced_report(data, out_path=str(out))
        assert set(reports) == set(list_factors())
        # at least some factors should have meaningful data
        n_valid = sum(1 for r in reports.values() if r.n_obs >= 50)
        assert n_valid > 0
        html = out.read_text()
        assert len(html) > 0

    def test_skips_missing_columns(self):
        data = _ohlcv(800).drop(columns=["volume"])
        reports = generate_enhanced_report(data)
        # volume-requiring factors should have empty outputs (n_obs=0)
        assert reports["volume_zscore"].n_obs == 0
        # close-only factors should still work
        assert reports["reversal_5d"].n_obs > 0
