"""EMA-cross momentum strategy template (A11).

Signal: long while EMA(fast) > EMA(slow), short while below; flips close the
open trade at the flip bar. A time stop caps holding at ``max_hold_bars``.

Copy this directory, rename it ``<name>_<timeframe>_<yyyymmdd>``, tune
``config.json``, and walk equity with
``_shared.run_backtest.run_backtest(cost_mode="fill")`` — never inline an
equity walk here.

Reference: Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum", JFE.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from _shared.run_backtest import Trade
from _shared.strategy_kit.indicators import ema

DEFAULT_CONFIG: Dict = {
    "symbol": "SYNTH",
    "fast": 12,
    "slow": 48,
    "size_fraction": 0.5,
    "max_hold_bars": 200,
}


def generate_signals(bars: Dict[str, pd.DataFrame],
                     config: Dict) -> List[Trade]:
    """Emit closed trades from EMA fast/slow crosses on the primary symbol."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    symbol = cfg["symbol"]
    df = bars[symbol]
    idx = df.index
    close = df["close"].astype(float)
    sig = (ema(close, int(cfg["fast"])) > ema(close, int(cfg["slow"]))).astype(float)
    sig = sig.replace(0.0, float("nan")).ffill().fillna(0.0)
    pos = sig.map({1.0: "long", 0.0: "flat", -1.0: "short"})

    trades: List[Trade] = []
    open_dir: str | None = None
    entry_i = 0
    max_hold = int(cfg["max_hold_bars"])
    n = len(df)
    for i in range(n):
        want = pos.iloc[i]
        if open_dir is None and want in ("long", "short"):
            open_dir, entry_i = want, i
        elif open_dir is not None:
            expired = (i - entry_i) >= max_hold
            flipped = want != open_dir
            if (expired or flipped) and i > entry_i:
                trades.append(Trade(idx[entry_i], idx[i], open_dir,
                                    float(cfg["size_fraction"])))
                open_dir = want if (flipped and want in ("long", "short")) else None
                entry_i = i
    if open_dir is not None and n - 1 > entry_i:
        trades.append(Trade(idx[entry_i], idx[n - 1], open_dir,
                            float(cfg["size_fraction"])))
    return trades
