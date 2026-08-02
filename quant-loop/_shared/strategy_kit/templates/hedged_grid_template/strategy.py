"""Hedged grid strategy template (A11).

Grid anchored on a rolling mean: long when price dips ``grid_k`` * ATR
below the anchor, take profit back at the anchor, stop at ``2 * grid_k``
* ATR below it; short mirrored above. Long and short legs are tracked
independently ("hedged": both may be open at once), one open trade per
direction.

Reference: grid/market-making placement per Guéant, Lehalle &
Fernandez-Tapia (2013) "Dealing with the inventory risk" (anchor +
symmetric depth); ATR bands per Keltner (1960).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from _shared.run_backtest import Trade
from _shared.strategy_kit.indicators import atr, sma

DEFAULT_CONFIG: Dict = {
    "symbol": "SYNTH",
    "anchor_window": 50,
    "atr_period": 14,
    "grid_k": 1.5,
    "size_fraction": 0.2,
    "max_hold_bars": 100,
}


def _leg(idx: pd.DatetimeIndex, close: pd.Series, trigger: pd.Series,
         take_profit: pd.Series, stop: pd.Series, direction: str,
         size: float, max_hold: int) -> List[Trade]:
    """Walk one grid leg: enter past the trigger band, exit at the anchor
    (take profit), the stop band, or the time stop."""
    trades: List[Trade] = []
    entry_i: Optional[int] = None
    n = len(close)
    for i in range(n):
        c = close.iloc[i]
        if entry_i is None:
            t = trigger.iloc[i]
            if pd.notna(t) and ((direction == "long" and c < t)
                                or (direction == "short" and c > t)):
                entry_i = i
        else:
            tp, st = take_profit.iloc[entry_i], stop.iloc[entry_i]
            hit_tp = c >= tp if direction == "long" else c <= tp
            hit_sl = c <= st if direction == "long" else c >= st
            expired = (i - entry_i) >= max_hold
            if (hit_tp or hit_sl or expired) and i > entry_i:
                trades.append(Trade(idx[entry_i], idx[i], direction, size))
                entry_i = None
    if entry_i is not None and n - 1 > entry_i:
        trades.append(Trade(idx[entry_i], idx[n - 1], direction, size))
    return trades


def generate_signals(bars: Dict[str, pd.DataFrame],
                     config: Dict) -> List[Trade]:
    """Emit both grid legs; legs are independent (hedged overlap allowed)."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    symbol = cfg["symbol"]
    df = bars[symbol]
    idx = df.index
    close = df["close"].astype(float)
    anchor = sma(close, int(cfg["anchor_window"]))
    band = float(cfg["grid_k"]) * atr(
        df["high"].astype(float), df["low"].astype(float), close,
        int(cfg["atr_period"]))

    size = float(cfg["size_fraction"])
    max_hold = int(cfg["max_hold_bars"])
    longs = _leg(idx, close, anchor - band, anchor, anchor - 2 * band,
                 "long", size, max_hold)
    shorts = _leg(idx, close, anchor + band, anchor, anchor + 2 * band,
                  "short", size, max_hold)
    return sorted(longs + shorts, key=lambda t: t.entry_ts)
