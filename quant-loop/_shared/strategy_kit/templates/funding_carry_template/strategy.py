"""Funding-carry strategy template (A11).

Every ``rebalance_bars`` bars, inspect the trailing mean funding rate
(``funding`` column, per-bar rate): persistently positive funding means
crowded longs — go short and collect; persistently negative — go long.
Positions are held ``hold_bars`` then closed.

When the frame has no ``funding`` column the factor degrades to zero and
the strategy emits no trades (spot-only data carries no funding signal).

Reference: funding as carry in the spirit of Moskowitz, Ooi & Pedersen
(2012) JFE; crowding interpretation per Bojraj & Titman (2019).
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from _shared.run_backtest import Trade
from _shared.strategy_kit.factor_library import compute_factor

DEFAULT_CONFIG: Dict = {
    "symbol": "SYNTH",
    "funding_window": 24,
    "entry_threshold": 0.0005,
    "rebalance_bars": 8,
    "hold_bars": 24,
    "size_fraction": 0.3,
}


def generate_signals(bars: Dict[str, pd.DataFrame],
                     config: Dict) -> List[Trade]:
    """Emit funding-carry trades: short high funding, long negative funding."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    symbol = cfg["symbol"]
    df = bars[symbol]
    idx = df.index
    f = compute_factor("funding_level", df, window=int(cfg["funding_window"]))

    trades: List[Trade] = []
    open_until = -1
    n = len(df)
    thr = float(cfg["entry_threshold"])
    hold = int(cfg["hold_bars"])
    for i in range(0, n, int(cfg["rebalance_bars"])):
        if i <= open_until:
            continue
        fv = f.iloc[i]
        if fv > thr:
            direction = "short"
        elif fv < -thr:
            direction = "long"
        else:
            continue
        exit_i = min(i + hold, n - 1)
        if exit_i <= i:
            continue
        trades.append(Trade(idx[i], idx[exit_i], direction,
                            float(cfg["size_fraction"])))
        open_until = exit_i
    return trades
