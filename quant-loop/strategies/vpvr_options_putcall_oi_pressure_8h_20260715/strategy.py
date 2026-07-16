"""vpvr_options_putcall_oi_pressure_8h_20260715 — strategy backtest engine.

Public API:
    VARIANT_KEY
    run_backtest(df: pd.DataFrame, cfg: dict) -> dict

The engine is identical in shape to the rest of the VPVR family: it consumes a
single-symbol 8h OHLCV + taker_buy_share DataFrame, runs the build_signals
function, simulates ATR-based exits + cooldown, and returns trades + equity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from build_signals import build_signals

VARIANT_KEY = "vpvr_options_putcall_oi_pressure_8h_20260715"


@dataclass
class Trade:
    variant: str
    symbol: str
    direction: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    pcr_z_at_entry: float
    poc_distance_atr_at_entry: float


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """Run single-symbol backtest on a DataFrame with OHLCV + taker_buy_share columns."""
    p = cfg["params"]
    sym = cfg["instruments"][0]
    df = df.copy()

    sig_df = build_signals(df, p)
    close = df["close"].astype(np.float64)
    atr = sig_df["atr"].astype(np.float64)
    signal = sig_df["signal"].astype(np.int64)
    pcr_z = sig_df["pcr_z"].fillna(0.0)
    poc_dist = sig_df["poc_distance_atr"].fillna(np.inf)

    close_arr = close.values
    atr_arr = atr.values
    sig_arr = signal.values
    pcr_arr = pcr_z.values

    fee = p["fee_bps_per_fill"] / 10000.0
    slip = p["slippage_bps_per_fill"] / 10000.0
    round_trip_cost = 2 * (fee + slip)
    risk_target = p["risk_target_pct"]

    trades: List[Trade] = []
    equity = [float(cfg["starting_capital_usd"])]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    entry_pcr = 0.0
    entry_poc_dist = 0.0
    bars_held = 0
    bars_since_exit = p["cooldown_bars"]

    warmup = max(p["vpvr_window_bars"], p["pcr_z_lookback_bars"], p["atr_period"]) + 1

    for i in range(1, len(df)):
        if i < warmup:
            equity.append(equity[-1])
            continue

        px = float(close_arr[i])
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        sig = int(sig_arr[i])

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= p["cooldown_bars"] and sig != 0 and at > 0:
                pos = sig
                entry_idx = i
                entry_px = px
                entry_pcr = float(pcr_arr[i]) if np.isfinite(pcr_arr[i]) else 0.0
                entry_poc_dist = float(poc_dist.iloc[i]) if np.isfinite(poc_dist.iloc[i]) else 0.0
                bars_held = 0
        else:
            bars_held += 1
            move = (px / entry_px - 1.0) * pos
            exit_now = False
            exit_reason = ""
            if move >= p["take_profit_atr_k"] * (at / entry_px):
                exit_now = True
                exit_reason = "take_profit"
            elif move <= -p["hard_stop_atr_k"] * (at / entry_px):
                exit_now = True
                exit_reason = "hard_stop"
            elif bars_held >= p["max_hold_bars"]:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                gross = move
                net = gross - round_trip_cost
                trades.append(Trade(
                    variant=VARIANT_KEY,
                    symbol=sym,
                    direction="long" if pos == 1 else "short",
                    entry_ts=str(df.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(df.index[i]),
                    exit_price=px,
                    pnl_pct=float(net),
                    bars_held=bars_held,
                    exit_reason=exit_reason,
                    pcr_z_at_entry=entry_pcr,
                    poc_distance_atr_at_entry=entry_poc_dist,
                ))
                equity.append(equity[-1] * (1.0 + risk_target * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0
                continue

        if pos != 0:
            bar_pnl = (px / float(close_arr[i - 1]) - 1.0) * pos
            equity.append(equity[-1] * (1.0 + risk_target * bar_pnl))
        else:
            equity.append(equity[-1])

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": len(df),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "trades": [t.__dict__ for t in trades],
        "equity": np.array(equity, dtype=np.float64),
    }
