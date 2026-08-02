"""Meta-labeling strategy template (A11).

Primary signal: momentum sign — ROC > 0 -> long candidate, ROC < 0 ->
short candidate (de Prado's "primary model").

Meta layer: each candidate is validated with a triple-barrier label
(``_shared.strategy_kit.labels.triple_barrier_labels``) on its own side —
the trade is taken only when the barrier outcome for the candidate
direction is positive (label == +1, i.e. the take-profit barrier fires
first). In production this label is the training target of a meta-model
(``_shared.strategy_kit.meta_labeling``); this template uses the label
directly to demonstrate the wiring end-to-end, so it is **not** a
tradeable out-of-sample signal — it exists to be copied and fitted.

Reference: López de Prado (2018) "Advances in Financial Machine
Learning", ch. 3 (triple-barrier + meta-labeling).
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from _shared.run_backtest import Trade
from _shared.strategy_kit.indicators import roc
from _shared.strategy_kit.labels import BarrierConfig, triple_barrier_labels

DEFAULT_CONFIG: Dict = {
    "symbol": "SYNTH",
    "mom_window": 12,
    "tp": 0.02,
    "sl": 0.01,
    "max_bars": 24,
    "size_fraction": 0.5,
}


def _side_trades(df: pd.DataFrame, candidates: pd.Series, side: int,
                 cfg: Dict) -> List[Trade]:
    """Take every candidate whose same-side triple-barrier label is +1."""
    labels = triple_barrier_labels(
        df["close"].astype(float),
        BarrierConfig(tp=float(cfg["tp"]), sl=float(cfg["sl"]),
                      max_bars=int(cfg["max_bars"]), side=side),
        high=df["high"].astype(float), low=df["low"].astype(float),
    )
    direction = "long" if side == 1 else "short"
    idx = df.index
    trades: List[Trade] = []
    last_exit = -1
    for i in range(len(df)):
        if not candidates.iloc[i] or labels["label"].iloc[i] != 1:
            continue
        touch = labels["touch_time"].iloc[i]
        if pd.isna(touch) or touch not in idx:
            continue
        exit_i = idx.get_loc(touch)
        if exit_i <= i or i <= last_exit:
            continue
        trades.append(Trade(idx[i], idx[exit_i], direction,
                            float(cfg["size_fraction"])))
        last_exit = exit_i
    return trades


def generate_signals(bars: Dict[str, pd.DataFrame],
                     config: Dict) -> List[Trade]:
    """Momentum primary + meta-label filter -> closed trades."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    symbol = cfg["symbol"]
    df = bars[symbol]
    m = roc(df["close"].astype(float), int(cfg["mom_window"]))
    longs = _side_trades(df, (m > 0).fillna(False), +1, cfg)
    shorts = _side_trades(df, (m < 0).fillna(False), -1, cfg)
    return sorted(longs + shorts, key=lambda t: t.entry_ts)
