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
    rets = equity.pct_change().dropna()
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) > 1 else 0.0
    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min()) if len(equity) > 1 else 0.0
    vol = float(rets.std(ddof=1) * math.sqrt(periods_per_year)) if len(rets) > 1 else 0.0
    sharpe = (
        float(rets.mean() / rets.std(ddof=1) * math.sqrt(periods_per_year))
        if len(rets) > 1 and rets.std(ddof=1) > 0 else 0.0
    )
    return {
        "Total return": total_ret,
        "Max drawdown": max_dd,
        "Ann. volatility": vol,
        "Ann. Sharpe": sharpe,
    }


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
            [[k, _pct(v) if k != "Ann. Sharpe" else f"{v:.2f}"]
             for k, v in stats.items()],
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
