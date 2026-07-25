"""Fee-shock replay for validation reports.

Replays an existing (cost-inclusive) equity curve against *additional*
round-trip cost tiers, debiting each trade's exit day at the SAME notional
basis as the equity curve. This is the corrected 口径 from SMA-36566:
the drag per trade is ``extra_round_trip_bps / 1e4`` of NAV — do NOT scale
by any per-trade fraction (the historical 0.005 factor understated cost
200× and produced "fee-robust" illusions, KILLing the mtf_xs_pairs family
on re-audit).

Contract (locked by ``validation/generic_harness.py``):

    fee_shock_sweep(equity, trades, bps_levels) -> {
        str(float(bps)): {
            "extra_round_trip_bps": float,
            "sharpe_daily_resampled": float,
            "annualized_return": float,
            "total_return": float,
            "max_drawdown_pct": float,
            "n_trades": int,
            "mean_daily_drag_pct": float,
        }, ...
    }

- ``equity``: pd.Series of NAV, any regular bar frequency, tz-naive index
  (callers strip tz before invoking — the harness does).
- ``trades``: iterable of dicts with an ``exit_ts`` key (anything
  ``pd.to_datetime`` accepts). Cost is debited on the exit *day*.
- ``bps_levels``: extra round-trip cost tiers in bps (e.g. 4/24/60),
  applied ON TOP of whatever cost the equity already embeds.
"""
from __future__ import annotations

import math
from typing import Iterable

import pandas as pd

__all__ = ["fee_shock_sweep"]


def _daily_frame(equity: pd.Series) -> pd.DataFrame:
    daily_eq = equity.resample("1D").last().dropna()
    return pd.DataFrame({"equity": daily_eq, "ret": daily_eq.pct_change().fillna(0.0)})


def _trade_day_counts(trades: Iterable[dict], index: pd.DatetimeIndex) -> pd.Series:
    exits = pd.to_datetime([t["exit_ts"] for t in trades], errors="coerce")
    exits = exits[~exits.isna()]
    if len(exits) == 0:
        return pd.Series(0.0, index=index)
    if exits.tz is not None:
        exits = exits.tz_convert(None)
    counts = exits.floor("D").value_counts()
    if counts.index.tz is not None:
        counts.index = counts.index.tz_convert(None)
    return counts.reindex(index, fill_value=0.0)


def _metrics(daily_ret: pd.Series, start_equity: float) -> dict:
    adj_eq = (1.0 + daily_ret).cumprod() * float(start_equity)
    rets = adj_eq.pct_change().dropna()
    sharpe = 0.0
    if len(rets) > 1 and float(rets.std(ddof=1)) > 1e-12:
        sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(365.0))
    total = float(adj_eq.iloc[-1] / adj_eq.iloc[0] - 1.0) if len(adj_eq) > 1 else 0.0
    span = (adj_eq.index[-1] - adj_eq.index[0]).total_seconds() / (365.25 * 86400) if len(adj_eq) > 1 else 0.0
    ann = float((1.0 + total) ** (1.0 / span) - 1.0) if span > 0 and total > -1.0 else -1.0 if total <= -1.0 else 0.0
    max_dd = float((adj_eq / adj_eq.cummax() - 1.0).min()) if len(adj_eq) > 1 else 0.0
    return {
        "sharpe_daily_resampled": sharpe,
        "annualized_return": ann,
        "total_return": total,
        "max_drawdown_pct": max_dd,
    }


def fee_shock_sweep(
    equity: pd.Series,
    trades: Iterable[dict],
    bps_levels: Iterable[float],
) -> dict[str, dict]:
    """Replay ``equity`` under each extra round-trip cost tier.

    Returns the locked contract dict keyed by ``str(float(bps))``.
    """
    trades = list(trades)
    frame = _daily_frame(equity)
    counts = _trade_day_counts(trades, frame.index)
    out: dict[str, dict] = {}
    for bps in (float(b) for b in bps_levels):
        drag = counts * (bps / 10_000.0)
        adj_ret = frame["ret"] - drag
        m = _metrics(adj_ret, float(frame["equity"].iloc[0]) if len(frame) else 1.0)
        m["extra_round_trip_bps"] = bps
        m["n_trades"] = len(trades)
        m["mean_daily_drag_pct"] = float(drag.mean())
        out[str(bps)] = m
    return out
