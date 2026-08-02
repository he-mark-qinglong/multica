"""HTML portfolio report generator (I20).

Renders a self-contained HTML report from a sequence of
:class:`~_shared.portfolio.snapshot.PortfolioSnapshot` plus optional
attribution inputs:

  * equity curve as an ASCII sparkline inside ``<pre>`` (no JS, no
    external assets — the report works offline and in email),
  * summary risk-metric table (total return, max drawdown, annualized
    vol, Sharpe),
  * strategy ranking table (from per-strategy PnL),
  * drawdown attribution table (from
    :func:`_shared.portfolio.attribution.drawdown_attribution`).

Pure function: ``generate_report(...) -> str``. All numbers escaped via
``html.escape``; only our own formatted numerics go into tables.

References:
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 17
    (what belongs in a performance report).
"""
from __future__ import annotations

import html
import math
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from _shared.portfolio.attribution import DrawdownAttribution
from _shared.portfolio.snapshot import PortfolioSnapshot

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(series: pd.Series, width: int = 60) -> str:
    """ASCII/Unicode sparkline of a numeric series, downsampled to width."""
    vals = series.to_numpy(dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return ""
    if len(vals) > width:
        idx = np.linspace(0, len(vals) - 1, width).round().astype(int)
        vals = vals[idx]
    lo, hi = float(vals.min()), float(vals.max())
    if hi == lo:
        return _SPARK_CHARS[0] * len(vals)
    norm = (vals - lo) / (hi - lo)
    return "".join(_SPARK_CHARS[min(int(v * (len(_SPARK_CHARS) - 1)),
                                    len(_SPARK_CHARS) - 1)] for v in norm)


def _risk_stats(equity: pd.Series, periods_per_year: int) -> dict:
    """Compute the extended risk/performance metric set (≥ 20 metrics).

    Returns an ordered dict of metric name → value. All values are raw
    floats (formatting is applied at render time by :func:`_fmt_metric`).
    """
    rets = equity.pct_change().dropna()
    n = len(rets)
    ppy = periods_per_year

    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) > 1 else 0.0

    # CAGR (annualised compound return)
    years = len(equity) / ppy if ppy > 0 else 1.0
    if len(equity) > 1 and equity.iloc[0] > 0 and years > 0:
        cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)
    else:
        cagr = 0.0

    running_max = equity.cummax()
    drawdowns = (equity / running_max - 1.0) if len(equity) > 1 else pd.Series(dtype=float)
    max_dd = float(drawdowns.min()) if len(drawdowns) > 0 else 0.0

    vol = float(rets.std(ddof=1) * math.sqrt(ppy)) if n > 1 else 0.0
    mean_ret = float(rets.mean()) if n > 0 else 0.0
    ann_ret = mean_ret * ppy

    sharpe = (
        float(rets.mean() / rets.std(ddof=1) * math.sqrt(ppy))
        if n > 1 and rets.std(ddof=1) > 0 else 0.0
    )

    # Downside deviation (only negative returns)
    downside = rets[rets < 0]
    downside_dev = float(downside.std(ddof=1) * math.sqrt(ppy)) if len(downside) > 1 else 0.0

    sortino = (
        float(rets.mean() / downside.std(ddof=1) * math.sqrt(ppy))
        if len(downside) > 1 and downside.std(ddof=1) > 0 else 0.0
    )

    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    # VaR / CVaR (historical, 95%)
    if n > 0:
        var_95 = abs(float(np.percentile(rets, 5)))
        var_threshold = np.percentile(rets, 5)
        tail = rets[rets <= var_threshold]
        cvar_95 = abs(float(tail.mean())) if len(tail) > 0 else var_95
    else:
        var_95 = 0.0
        cvar_95 = 0.0

    # Best / worst single-period return
    best_ret = float(rets.max()) if n > 0 else 0.0
    worst_ret = float(rets.min()) if n > 0 else 0.0

    # Win rate / profit factor
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    win_rate = float(len(wins) / n) if n > 0 else 0.0
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) > 0 else 0.0
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 0.0
    avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0

    # Higher moments
    skew = float(rets.skew()) if n > 2 else 0.0
    kurt = float(rets.kurt()) if n > 3 else 0.0  # excess kurtosis

    # Max consecutive losses
    max_consec_losses = float(_max_consecutive(rets < 0))

    # Recovery factor: net profit / max drawdown
    recovery = float(total_ret / abs(max_dd)) if max_dd < 0 else 0.0

    # Pain index: average drawdown
    pain_index = float(drawdowns.mean()) if len(drawdowns) > 0 else 0.0

    # Ulcer index: root-mean-square of drawdowns
    ulcer = float(np.sqrt(np.mean(drawdowns ** 2))) if len(drawdowns) > 0 else 0.0

    # Omega ratio: E[gains] / E[losses] above/below zero threshold
    gains = rets[rets > 0]
    losses_abs = rets[rets < 0].abs()
    omega = float(gains.mean() / losses_abs.mean()) if len(losses_abs) > 0 and losses_abs.mean() > 0 else 0.0

    # Tail ratio: |95th percentile| / |5th percentile| of returns
    if n > 0:
        p95 = float(np.percentile(rets, 95))
        p5 = float(np.percentile(rets, 5))
        tail_ratio = abs(p95) / abs(p5) if abs(p5) > 1e-20 else 0.0
    else:
        tail_ratio = 0.0

    return {
        "Total return": total_ret,
        "Ann. return (CAGR)": cagr,
        "Ann. volatility": vol,
        "Max drawdown": max_dd,
        "Ann. Sharpe": sharpe,
        "Ann. Sortino": sortino,
        "Calmar ratio": calmar,
        "VaR 95%": var_95,
        "CVaR 95%": cvar_95,
        "Best return": best_ret,
        "Worst return": worst_ret,
        "Win rate": win_rate,
        "Profit factor": profit_factor,
        "Avg win": avg_win,
        "Avg loss": avg_loss,
        "Skewness": skew,
        "Excess kurtosis": kurt,
        "Max consec. losses": max_consec_losses,
        "Recovery factor": recovery,
        "Pain index": pain_index,
        "Ulcer index": ulcer,
        "Downside deviation": downside_dev,
        "Omega ratio": omega,
        "Tail ratio": tail_ratio,
    }


def _max_consecutive(mask: pd.Series) -> int:
    """Longest run of ``True`` values in a boolean series."""
    if len(mask) == 0:
        return 0
    max_run = current = 0
    for v in mask:
        if v:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


# Metrics that are naturally a ratio (no % formatting)
_RATIO_METRICS = frozenset({
    "Ann. Sharpe", "Ann. Sortino", "Calmar ratio",
    "Profit factor", "Recovery factor", "Skewness",
    "Excess kurtosis", "Max consec. losses", "Ulcer index",
    "Pain index", "Omega ratio", "Tail ratio",
})


def _fmt_metric(name: str, value: float) -> str:
    """Format a metric value for the HTML table."""
    if name in _RATIO_METRICS:
        return f"{value:.3f}"
    return _pct(value)


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    out = ["<table><thead><tr>"]
    out += [f"<th>{html.escape(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif;
       margin: 2em; color: #222; }
h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 1.6em; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: 4px 10px; text-align: right; }
th:first-child, td:first-child { text-align: left; }
pre.spark { font-size: 14px; letter-spacing: 1px; background: #f7f7f7;
            padding: 8px; overflow-x: auto; }
"""


def generate_report(
    snapshots: Sequence[PortfolioSnapshot],
    strategy_pnl: Optional[Mapping[str, float]] = None,
    drawdown_attr: Optional[DrawdownAttribution] = None,
    periods_per_year: int = 365,
    title: str = "Portfolio Report",
) -> str:
    """Render the full HTML report. Snapshots must be ts-ordered."""
    if not snapshots:
        raise ValueError("snapshots sequence is empty")

    equity = pd.Series(
        [s.equity for s in snapshots],
        index=pd.DatetimeIndex([s.ts for s in snapshots]),
        name="equity",
    )
    stats = _risk_stats(equity, periods_per_year)

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p>{equity.index[0]} &rarr; {equity.index[-1]} "
        f"({len(snapshots)} snapshots)</p>",
        "<h2>Equity curve</h2>",
        f"<pre class='spark'>{sparkline(equity)}</pre>",
        "<h2>Risk metrics</h2>",
        _table(
            ["Metric", "Value"],
            [[k, _fmt_metric(k, v)] for k, v in stats.items()],
        ),
    ]

    if strategy_pnl:
        ranked = sorted(strategy_pnl.items(), key=lambda kv: kv[1], reverse=True)
        parts.append("<h2>Strategy ranking</h2>")
        parts.append(_table(
            ["Rank", "Strategy", "PnL"],
            [[str(i + 1), html.escape(k), f"{v:,.2f}"]
             for i, (k, v) in enumerate(ranked)],
        ))

    if drawdown_attr is not None:
        rows = [
            [html.escape(k), f"{v:,.2f}",
             _pct(drawdown_attr.contribution_shares.get(k, 0.0))]
            for k, v in sorted(
                drawdown_attr.contributions.items(), key=lambda kv: kv[1]
            )
        ]
        parts.append("<h2>Drawdown attribution</h2>")
        parts.append(
            f"<p>Max drawdown {_pct(drawdown_attr.max_drawdown)} "
            f"({drawdown_attr.peak} &rarr; {drawdown_attr.trough}); "
            f"top detractor: <b>{html.escape(drawdown_attr.top_detractor)}</b></p>"
        )
        parts.append(_table(["Contributor", "PnL", "Share of loss"], rows))

    parts.append("</body></html>")
    return "".join(parts)
