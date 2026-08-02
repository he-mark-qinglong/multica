"""Tests for portfolio/reporting.py (I20)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.portfolio.attribution import drawdown_attribution
from _shared.portfolio.reporting import generate_report, sparkline
from _shared.portfolio.snapshot import PortfolioSnapshot


def _snapshots(n=12):
    out = []
    eq = 1000.0
    for i in range(n):
        eq *= 1.01 if i % 4 != 3 else (0.97 - (i // 4) * 0.002)  # up-trend with varying dips
        out.append(PortfolioSnapshot(
            ts=pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
            equity=eq, cash=eq * 0.5,
            positions={"BTC": 1.0}, prices={"BTC": 100.0},
            risk_metrics={},
        ))
    return out


def test_sparkline_shape():
    s = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0, 4.0])
    line = sparkline(s, width=6)
    assert len(line) == 6
    assert line[0] == "▁" and line[-1] == "█"
    assert sparkline(pd.Series(dtype=float)) == ""
    # Constant series doesn't divide by zero.
    assert sparkline(pd.Series([5.0, 5.0, 5.0])) == "▁▁▁"


def test_sparkline_downsamples():
    s = pd.Series(range(1000), dtype=float)
    assert len(sparkline(s, width=40)) == 40


def test_generate_report_full():
    snaps = _snapshots()
    idx = pd.DatetimeIndex([s.ts for s in snaps])
    rets = pd.DataFrame({
        "alpha": 0.005, "beta": -0.004,
    }, index=idx)
    dd = drawdown_attribution(rets)
    html = generate_report(
        snaps,
        strategy_pnl={"alpha": 500.0, "beta": -200.0, "gamma": 100.0},
        drawdown_attr=dd,
        title="Test Report",
    )
    assert isinstance(html, str)
    assert "<html>" in html and "</html>" in html
    assert "Test Report" in html
    assert "Equity curve" in html
    assert "Risk metrics" in html
    assert "Total return" in html and "Max drawdown" in html
    # Strategy ranking: alpha ranked #1.
    assert "Strategy ranking" in html
    assert html.index("alpha") < html.index("gamma") < html.index("beta")
    # Drawdown attribution table present with top detractor.
    assert "Drawdown attribution" in html
    assert "beta" in html


def test_generate_report_minimal():
    snaps = _snapshots(3)
    html = generate_report(snaps)
    assert "Strategy ranking" not in html
    assert "Drawdown attribution" not in html
    assert "Equity curve" in html


def test_generate_report_empty_raises():
    with pytest.raises(ValueError):
        generate_report([])


def test_html_escapes_title():
    html = generate_report(_snapshots(2), title="<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Extended metrics (≥ 20 risk/performance metrics)
# ---------------------------------------------------------------------------

from _shared.portfolio.reporting import _risk_stats  # noqa: E402


_EXPECTED_METRICS = {
    "Total return", "Ann. return (CAGR)", "Ann. volatility", "Max drawdown",
    "Ann. Sharpe", "Ann. Sortino", "Calmar ratio", "VaR 95%", "CVaR 95%",
    "Best return", "Worst return", "Win rate", "Profit factor",
    "Avg win", "Avg loss", "Skewness", "Excess kurtosis",
    "Max consec. losses", "Recovery factor", "Pain index", "Ulcer index",
}


def test_risk_stats_has_at_least_20_metrics():
    snaps = _snapshots(30)
    equity = pd.Series([s.equity for s in snaps])
    stats = _risk_stats(equity, periods_per_year=365)
    assert len(stats) >= 20
    assert _EXPECTED_METRICS.issubset(set(stats.keys()))


def test_report_contains_extended_metrics():
    html = generate_report(_snapshots(30))
    for metric in ("Ann. Sortino", "Calmar ratio", "VaR 95%", "CVaR 95%",
                   "Profit factor", "Skewness", "Ulcer index"):
        assert metric in html, f"missing metric '{metric}' in report"


def test_risk_stats_formatting_no_errors():
    """Stats should all be finite floats, not NaN/inf."""
    import math
    snaps = _snapshots(30)
    equity = pd.Series([s.equity for s in snaps])
    stats = _risk_stats(equity, periods_per_year=365)
    for name, val in stats.items():
        assert isinstance(val, float), f"{name} is not float"
        assert math.isfinite(val), f"{name} is not finite: {val}"


def test_sortino_calmar_relationships():
    """Calmar > 0 when profitable, Sortino and Sharpe positive."""
    import numpy as np
    # Deterministic: 80 varied positive returns + 20 varied negative returns,
    # shuffled.  Guarantees net-positive trend with drawdowns and non-zero
    # Sortino denominator (varying negative magnitudes).
    rng = np.random.RandomState(42)
    rets = np.concatenate([
        rng.uniform(0.003, 0.015, 80),    # 80 up days
        rng.uniform(-0.025, -0.005, 20),  # 20 down days
    ])
    rng.shuffle(rets)
    equity = pd.Series(np.cumprod(1.0 + rets) * 1000.0)
    stats = _risk_stats(equity, periods_per_year=365)
    assert stats["Calmar ratio"] > 0
    assert stats["Ann. Sharpe"] > 0
    assert stats["Ann. Sortino"] > 0


def test_win_rate_in_valid_range():
    snaps = _snapshots(30)
    equity = pd.Series([s.equity for s in snaps])
    stats = _risk_stats(equity, periods_per_year=365)
    assert 0.0 <= stats["Win rate"] <= 1.0


def test_var_cvar_positive():
    snaps = _snapshots(30)
    equity = pd.Series([s.equity for s in snaps])
    stats = _risk_stats(equity, periods_per_year=365)
    assert stats["VaR 95%"] >= 0
    assert stats["CVaR 95%"] >= 0


def test_best_worst_signs():
    snaps = _snapshots(30)
    equity = pd.Series([s.equity for s in snaps])
    stats = _risk_stats(equity, periods_per_year=365)
    assert stats["Best return"] > 0
    assert stats["Worst return"] < 0
