"""Minimal state machine for funding_carry_asym (SMA-34793 prototype).

Public API:
    VARIANT_KEY
    run_backtest(df: pd.DataFrame, cfg: dict) -> dict

The state machine is intentionally thin: it accepts the
``build_signals`` output, reads the per-bar ``signal`` (-1/0/+1),
and runs a basic TP / hard-stop / time-stop loop with a configurable
cooldown. Costs follow the cycle-46 convention (4 bps fee + 1 bp
slippage per fill, applied round-trip) plus a per-bar funding carry
of 0.01% (the long pays when funding > 0; this is exactly the cost
the entry signal is harvesting).

Sharpe computation lives in ``run_backtest.py`` to keep this file
focused on entry/exit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from build_signals import build_signals

VARIANT_KEY = "funding_carry_asym"


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
    funding_at_entry: float
    support_level_price: float
    support_distance_atr: float
    near_support: bool


def _state_machine(
    df: pd.DataFrame,
    sig_df: pd.DataFrame,
    cfg: dict,
    p: dict,
    max_hold: int,
) -> dict:
    """Generic state machine driven by a single ``signal`` column
    (-1 / 0 / +1) from ``sig_df``. Emits trades + equity curve.
    """
    sym = cfg["instruments"][0]
    close = df["close"].astype(np.float64)

    sig_arr = sig_df["signal"].astype(np.int64).values
    close_arr = close.values
    funding_arr = sig_df["funding"].astype(np.float64).fillna(0.0).values
    support_px_arr = sig_df["support_level_price"].astype(np.float64).values
    support_dist_arr = sig_df["support_distance_atr"].astype(np.float64).values
    near_support_arr = sig_df["near_support"].astype(bool).values
    atr_arr = sig_df["atr"].astype(np.float64).values

    fee = float(p.get("fee_bps_per_fill", 4.0)) / 10000.0
    slip = float(p.get("slippage_bps_per_fill", 1.0)) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    risk_target = float(p.get("risk_target_pct", 0.005))
    cooldown = int(p.get("cooldown_bars", 5))
    fund_carry_per_bar = float(p.get("funding_carry_bps_per_bar", 0.01)) / 10000.0

    trades: List[Trade] = []
    equity = [float(cfg.get("starting_capital_usd", 100000.0))]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    bars_held = 0
    bars_since_exit = cooldown
    entry_fd = 0.0
    entry_sup_px = 0.0
    entry_sup_dist = 0.0
    entry_near = False

    warmup = int(p.get("vpvr_window_bars", 180)) + int(p.get("atr_period", 14)) + 1

    for i in range(1, len(df)):
        if i < warmup:
            equity.append(equity[-1])
            continue

        px = float(close_arr[i])
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        sig_i = int(sig_arr[i])

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= cooldown and sig_i == 1 and at > 0:
                pos = +1
                entry_idx = i
                entry_px = px
                bars_held = 0
                entry_fd = float(funding_arr[i])
                entry_sup_px = float(support_px_arr[i]) if np.isfinite(support_px_arr[i]) else float("nan")
                entry_sup_dist = float(support_dist_arr[i]) if np.isfinite(support_dist_arr[i]) else float("nan")
                entry_near = bool(near_support_arr[i])
        else:
            bars_held += 1
            move = (px / entry_px - 1.0) * pos
            exit_now = False
            exit_reason = ""
            if move >= float(p.get("take_profit_atr_k", 1.5)) * (at / entry_px):
                exit_now = True
                exit_reason = "take_profit"
            elif move <= -float(p.get("hard_stop_atr_k", 1.0)) * (at / entry_px):
                exit_now = True
                exit_reason = "hard_stop"
            elif bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                gross = move
                # Long pays funding carry while funding > 0; the entry
                # condition guarantees funding > 0 so this is the
                # expected cost of harvesting the asymmetry.
                funding_carry = -fund_carry_per_bar * bars_held * pos
                net = gross - round_trip_cost + funding_carry
                trades.append(Trade(
                    variant=VARIANT_KEY,
                    symbol=sym,
                    direction="long",
                    entry_ts=str(df.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(df.index[i]),
                    exit_price=px,
                    pnl_pct=float(net),
                    bars_held=bars_held,
                    exit_reason=exit_reason,
                    funding_at_entry=entry_fd,
                    support_level_price=entry_sup_px,
                    support_distance_atr=entry_sup_dist,
                    near_support=entry_near,
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

    n_long = int((sig_df["signal"] == 1).sum())

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": len(df),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "trades": [asdict(t) for t in trades],
        "equity": np.array(equity, dtype=np.float64),
        "diagnostics": {
            "n_long_signals": n_long,
        },
    }


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """Run the funding-carry-asym prototype on a single bar frame.

    `df` must include OHLCV + a ``funding`` column (see
    ``data_loader.py`` for the canonical load). The harness caller
    passes `cfg["params"]` plus the chosen max_hold_bars for this
    timeframe.
    """
    p = cfg["params"]
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "ts" in df.columns:
            df = df.set_index("ts")
        elif "open_time" in df.columns:
            df = df.set_index("open_time")
        else:
            raise ValueError("df must have a DatetimeIndex or ts/open_time column")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    for col in ("open", "high", "low", "close", "volume", "funding"):
        if col in df.columns:
            df[col] = df[col].astype(np.float64)

    sig = build_signals(df, p)
    max_hold = int(p.get("max_hold_bars", 6))
    out = _state_machine(df, sig, cfg, p, max_hold)
    out["diagnostics"]["signal_bars_with_positive_signal"] = int((sig["signal"] == 1).sum())
    out["diagnostics"]["signal_bars_funding_above_threshold"] = int(sig["funding_above_threshold"].sum())
    out["diagnostics"]["signal_bars_near_support"] = int(sig["near_support"].sum())
    return out


__all__ = ["VARIANT_KEY", "Trade", "run_backtest"]
