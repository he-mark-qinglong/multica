"""Minimal example strategy implementing strategy contract v2.

A deliberately trivial single-symbol channel breakout:

- **Entry**: close breaks above the highest close of the prior
  ``entry_lookback`` bars → long ``size_fraction``.
- **Exit**: close breaks below the lowest close of the prior
  ``exit_lookback`` bars, or after ``max_hold_bars``.

This module exists as a *template* for new high-frequency strategies
(Phase D of PLAN_20260724): it shows the required
``generate_signals(bars, config) -> list[Trade]`` shape, uses only
shared infrastructure (``_shared.run_backtest.Trade``), and keeps all
parameters in ``DEFAULT_CONFIG``. It is NOT a tradeable edge — 1m/5m
klines price-reversal signals are structurally falsified (see plan §1).
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from _shared.run_backtest import Trade

DEFAULT_CONFIG: Dict[str, Any] = {
    "symbol": "SYNTH",
    "entry_lookback": 20,
    "exit_lookback": 10,
    "max_hold_bars": 30,
    "size_fraction": 0.95,
}


def generate_signals(
    bars: Dict[str, pd.DataFrame],
    config: Dict[str, Any],
) -> List[Trade]:
    """Contract v2 entry point: bars dict + config -> list[Trade]."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    symbol = str(cfg["symbol"])
    if symbol not in bars:
        raise KeyError(f"symbol {symbol!r} not in bars ({sorted(bars)})")
    df = bars[symbol]
    close = df["close"].to_numpy(dtype=float)
    index = df.index
    n = len(df)

    entry_lb = max(1, int(cfg["entry_lookback"]))
    exit_lb = max(1, int(cfg["exit_lookback"]))
    max_hold = int(cfg["max_hold_bars"])
    size = float(cfg["size_fraction"])

    trades: List[Trade] = []
    i = entry_lb
    while i < n - 1:
        # Entry: close above the prior entry_lookback-bar high (no look-ahead:
        # the window ends at i-1).
        window_hi = close[i - entry_lb:i].max()
        if close[i] > window_hi:
            entry_ts = index[i]
            exit_i = min(i + max_hold, n - 1)
            for j in range(i + 1, min(i + max_hold + 1, n)):
                lo_start = max(0, j - exit_lb)
                if close[j] < close[lo_start:j].min():
                    exit_i = j
                    break
            trades.append(Trade(
                entry_ts=entry_ts,
                exit_ts=index[exit_i],
                direction="long",
                size_fraction=size,
            ))
            i = exit_i + 1
        else:
            i += 1
    return trades


__all__ = ["DEFAULT_CONFIG", "generate_signals"]
