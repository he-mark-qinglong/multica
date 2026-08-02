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
        eq *= 1.01 if i % 4 != 3 else 0.97  # up-trend with dips
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
