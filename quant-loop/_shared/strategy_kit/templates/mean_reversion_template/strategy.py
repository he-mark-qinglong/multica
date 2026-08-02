"""RSI mean-reversion strategy template (A11).

Long when RSI < ``oversold``, exit when RSI mean-reverts above
``exit_level`` (or time stop); short mirrored above ``overbought``.

Reference: Jegadeesh (1990) short-term reversal, JF; Wilder (1978) RSI.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from _shared.run_backtest import Trade
from _shared.strategy_kit.indicators import rsi

DEFAULT_CONFIG: Dict = {
    "symbol": "SYNTH",
    "rsi_period": 14,
    "oversold": 30.0,
    "overbought": 70.0,
    "exit_level": 50.0,
    "size_fraction": 0.5,
    "max_hold_bars": 100,
}


def generate_signals(bars: Dict[str, pd.DataFrame],
                     config: Dict) -> List[Trade]:
    """Emit closed trades fading RSI extremes back toward the midline."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    symbol = cfg["symbol"]
    df = bars[symbol]
    idx = df.index
    r = rsi(df["close"].astype(float), int(cfg["rsi_period"]))

    trades: List[Trade] = []
    open_dir: str | None = None
    entry_i = 0
    n = len(df)
    max_hold = int(cfg["max_hold_bars"])
    for i in range(n):
        rv = r.iloc[i]
        if open_dir is None:
            if rv < cfg["oversold"]:
                open_dir, entry_i = "long", i
            elif rv > cfg["overbought"]:
                open_dir, entry_i = "short", i
        else:
            reverted = (rv >= cfg["exit_level"]) if open_dir == "long" \
                else (rv <= cfg["exit_level"])
            expired = (i - entry_i) >= max_hold
            if (reverted or expired) and i > entry_i:
                trades.append(Trade(idx[entry_i], idx[i], open_dir,
                                    float(cfg["size_fraction"])))
                open_dir = None
    if open_dir is not None and n - 1 > entry_i:
        trades.append(Trade(idx[entry_i], idx[n - 1], open_dir,
                            float(cfg["size_fraction"])))
    return trades
