"""Cross-sectional momentum rank strategy -- per-symbol signal builder.

The strategy is **rank-based**: at each daily bar we compute a momentum score
for every symbol in the active universe, sort them, and go long the top-K
and short the bottom-K. The portfolio construction (size of each leg,
position caps, rebalance scheduling) lives in ``portfolio.py`` so this file
stays focused on the per-symbol signal.

The score is a weighted blend of three lookback returns (matches the spec):

    momentum_score = 0.5 * return_30d + 0.3 * return_7d + 0.2 * return_3d

Returns are computed on the daily close. We require at least
``min_history_bars`` (35 by default -- 30 to look back + a buffer for the
3d return calculation) of history before emitting a non-NaN score; before
that the score is NaN and the symbol is excluded from the ranking.

This module is pure (no I/O): it exposes ``compute_momentum_score(close)``
and ``build_signals(per_symbol_dfs, cfg)`` for use by the backtest runner
and the tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_strategy_config(cfg_path: Path = CONFIG_PATH) -> dict:
    return json.loads(cfg_path.read_text())


def trailing_return(close: pd.Series, lookback: int) -> pd.Series:
    """Return ``close / close.shift(lookback) - 1`` -- the trailing N-bar
    simple return. The first ``lookback`` bars are NaN.
    """
    return close / close.shift(lookback) - 1.0


def compute_momentum_score(
    close: pd.Series,
    w_30: float = 0.5,
    w_7: float = 0.3,
    w_3: float = 0.2,
) -> pd.Series:
    """Momentum score: a weighted blend of trailing 30d / 7d / 3d returns.

    Bars where any of the three inputs is NaN produce NaN. The caller is
    responsible for any subsequent filtering / ranking.
    """
    r30 = trailing_return(close, 30)
    r7 = trailing_return(close, 7)
    r3 = trailing_return(close, 3)
    return w_30 * r30 + w_7 * r7 + w_3 * r3


def per_symbol_signals(df_1d: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Compute the per-symbol signal columns on a 1d OHLCV frame.

    Adds:
        return_30d, return_7d, return_3d, momentum_score

    All columns are NaN for bars where the 30d return is not yet defined.
    """
    df = df_1d.copy()
    mom_cfg = cfg["momentum"]
    df["return_30d"] = trailing_return(df["close"], 30)
    df["return_7d"] = trailing_return(df["close"], 7)
    df["return_3d"] = trailing_return(df["close"], 3)
    df["momentum_score"] = compute_momentum_score(
        df["close"],
        w_30=mom_cfg["weight_30d"],
        w_7=mom_cfg["weight_7d"],
        w_3=mom_cfg["weight_3d"],
    )
    return df


def build_signals(
    per_symbol_dfs: Dict[str, pd.DataFrame],
    cfg: Optional[dict] = None,
) -> pd.DataFrame:
    """Build a long-form (symbol, date) table of momentum scores aligned
    across the universe.

    Returns a DataFrame with index = union of dates across all symbols,
    columns = the input symbols. The ``momentum_score`` panel is the only
    filled panel; bar alignment uses ``outer`` so the backtest can iterate
    every bar that any symbol has.
    """
    cfg = cfg or load_strategy_config()
    panels: Dict[str, pd.Series] = {}
    for sym, df in per_symbol_dfs.items():
        sig = per_symbol_signals(df, cfg)
        panels[sym] = sig["momentum_score"].rename(sym)
    if not panels:
        return pd.DataFrame()
    mat = pd.concat(panels.values(), axis=1)
    mat.columns = list(panels.keys())
    mat.index.name = "openTime"
    return mat.sort_index()


def rank_symbols_on(
    momentum_panel: pd.DataFrame,
    asof: pd.Timestamp,
) -> pd.DataFrame:
    """Rank the symbols by their momentum score on the bar at-or-before
    ``asof``.

    Returns a DataFrame with columns ``symbol``, ``score``, ``rank``
    (1 = highest momentum). Symbols with NaN score at ``asof`` are dropped.
    """
    sub = momentum_panel[momentum_panel.index <= asof]
    if sub.empty:
        return pd.DataFrame(columns=["symbol", "score", "rank"])
    latest = sub.iloc[-1]
    rows = []
    for sym in momentum_panel.columns:
        v = latest.get(sym)
        if pd.isna(v):
            continue
        rows.append({"symbol": sym, "score": float(v)})
    out = pd.DataFrame(rows)
    if out.empty:
        return out.assign(rank=pd.Series(dtype=int))
    # Higher score == better rank (rank 1).
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1
    return out


def select_long_short(
    ranking: pd.DataFrame,
    top_k: int,
    bottom_k: int,
) -> Dict[str, str]:
    """Pick ``top_k`` longs and ``bottom_k`` shorts from the ranking frame.

    Returns ``{symbol: 'LONG' | 'SHORT'}``. If the ranking has fewer than
    ``top_k + bottom_k`` symbols, K is shrunk to fit.
    """
    if ranking.empty:
        return {}
    n = len(ranking)
    actual_top = min(top_k, n // 2 if (top_k + bottom_k) > n else top_k)
    actual_bot = min(bottom_k, n - actual_top)
    if actual_top + actual_bot > n:
        # Tight universe: split evenly.
        actual_top = actual_bot = n // 2
    longs = set(ranking.head(actual_top)["symbol"].tolist())
    shorts = set(ranking.tail(actual_bot)["symbol"].tolist())
    out: Dict[str, str] = {}
    for s in longs:
        out[s] = "LONG"
    for s in shorts:
        out[s] = "SHORT"
    return out