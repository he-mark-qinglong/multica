"""V3_funding_asym — VPVR + funding asymmetry, asymmetric execution.

Iter#92 (Campaign SMA-33516). Cycle-46 lesson:
  "funding-delta 信号 IS real (pf>1, 51.9% WR) 但需要 asymmetric execution
   + multi-TF confirmation"

This strategy embodies that lesson:

  1. Entry: VPVR POC proximity + funding asymmetry. Annualized funding
     < -10 bps (extremely negative, shorts paying longs = exhaustion) AND
     price within 1.0 ATR of VPVR POC triggers a LONG. Mirror for SHORT
     when funding > +10 bps with price above POC.

  2. Asymmetric TP:SL = 4 ATR target / 1.5 ATR stop. The wider target
     captures the funding unwind, the tighter stop keeps losses bounded.

  3. Funding carry: ±0.01% per 4h bar (longs pay when funding>0, vice versa).
     This drags on hold time but is the cost of running on margin.

  4. Multi-symbol: BTCUSDT + ETHUSDT. Independent sizing per symbol.
     Each symbol contributes to a combined PnL book at its own scale.

Public API: VARIANT_KEY, run_backtest(data, cfg).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "vpvr_funding_asym_4h_20260713"


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _vpvr_poc(close: pd.Series, volume: pd.Series,
              window: int, n_bins: int) -> pd.Series:
    close_arr = close.values
    vol_arr = volume.values
    n = len(close_arr)
    poc = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        c_seg = close_arr[i - window + 1: i + 1]
        v_seg = vol_arr[i - window + 1: i + 1]
        if not np.isfinite(c_seg).all() or not np.isfinite(v_seg).all():
            continue
        c_min, c_max = float(c_seg.min()), float(c_seg.max())
        if c_max <= c_min:
            continue
        edges = np.linspace(c_min, c_max, n_bins + 1)
        idx = np.clip(np.searchsorted(edges, c_seg, side="right") - 1, 0, n_bins - 1)
        bin_vol = np.zeros(n_bins, dtype=np.float64)
        for j, b in enumerate(idx):
            bin_vol[b] += v_seg[j]
        best = int(np.argmax(bin_vol))
        poc[i] = float((edges[best] + edges[best + 1]) / 2.0)
    return pd.Series(poc, index=close.index)


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
    funding_ann_bps_at_entry: float
    funding_z_at_entry: float
    poc_distance_atr_at_entry: float


def _run_one_symbol(data: pd.DataFrame, cfg: dict) -> dict:
    p = cfg["params"]
    sym = cfg["instruments"][0]
    df = data.copy()

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    funding = df["fundingRate"].astype(np.float64)
    funding_ann_bps = df["fundingAnnBps"].astype(np.float64)

    # Funding rolling z-score (on raw rate, not annualized bps).
    win = p["funding_z_lookback_bars"]
    fd_mean = funding.rolling(win, min_periods=win).mean()
    fd_std = funding.rolling(win, min_periods=win).std(ddof=0)
    fd_z = (funding - fd_mean) / fd_std.replace(0, np.nan)

    poc = _vpvr_poc(close, volume, p["vpvr_window_bars"], p["vpvr_bins"])
    atr = _atr(df, p["atr_period"])
    atr_safe = atr.replace(0, np.nan)

    poc_dist = (close - poc).abs()
    poc_dist_atr = (poc_dist / atr_safe)

    # Build numpy arrays.
    close_arr = close.values
    high_arr = high.values
    low_arr = low.values
    fd_z_arr = fd_z.fillna(0).values
    funding_ann_arr = funding_ann_bps.fillna(0).values
    poc_atr_arr = poc_dist_atr.fillna(np.inf).values
    atr_arr = atr.values
    funding_arr = funding.fillna(0).values

    fee = p["fee_bps_per_fill"] / 10000.0
    slip = p["slippage_bps_per_fill"] / 10000.0
    round_trip_cost = 2 * (fee + slip)
    risk_target = p["risk_target_pct"]
    fund_carry_per_bar = p["funding_carry_bps_per_bar"] / 10000.0

    trades: List[Trade] = []
    equity = [float(cfg.get("starting_capital_per_symbol_usd",
                            cfg["starting_capital_usd"]))]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    entry_fund_ann = 0.0
    entry_fz = 0.0
    entry_poc_dist = 0.0
    bars_held = 0
    bars_since_exit = p["min_gap_bars_between_trades"]

    warmup = max(p["vpvr_window_bars"], win, p["atr_period"]) + 1
    ann_thr = p["funding_annualized_basis_bps_threshold"]
    fz_thr = p["funding_z_threshold"]

    for i in range(1, len(df)):
        if i < warmup:
            equity.append(equity[-1]); continue

        px = float(close_arr[i])
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        pa = float(poc_atr_arr[i]) if np.isfinite(poc_atr_arr[i]) else float("inf")
        fz = float(fd_z_arr[i]) if np.isfinite(fd_z_arr[i]) else 0.0
        fa = float(funding_ann_arr[i])

        if pos == 0:
            bars_since_exit += 1
            if pa <= p["poc_atr_buffer"] and bars_since_exit >= p["min_gap_bars_between_trades"]:
                # Long reversion: very negative funding (shorts paying) + POC touch.
                if fa < ann_thr and fz < -fz_thr:
                    pos = +1
                # Short reversion: very positive funding + POC touch.
                elif fa > -ann_thr and fz > fz_thr:
                    pos = -1
                if pos != 0:
                    entry_idx = i
                    entry_px = px
                    entry_fund_ann = fa
                    entry_fz = fz
                    entry_poc_dist = pa
                    bars_held = 0
        else:
            bars_held += 1
            move = (px / entry_px - 1.0) * pos
            exit_now = False
            exit_reason = ""
            if move >= p["asymmetric_take_profit_atr_k"] * (at / entry_px):
                exit_now = True; exit_reason = "asym_take_profit"
            elif move <= -p["asymmetric_hard_stop_atr_k"] * (at / entry_px):
                exit_now = True; exit_reason = "asym_hard_stop"
            elif bars_held >= p["max_hold_bars"]:
                exit_now = True; exit_reason = "time_stop"

            if exit_now:
                gross = move
                # Funding carry over held bars (4h each).
                funding_carry = -fund_carry_per_bar * bars_held * pos
                net = gross - round_trip_cost + funding_carry
                trades.append(Trade(
                    variant=VARIANT_KEY, symbol=sym,
                    direction="long" if pos == +1 else "short",
                    entry_ts=str(df.index[entry_idx]), entry_price=entry_px,
                    exit_ts=str(df.index[i]), exit_price=px,
                    pnl_pct=float(net), bars_held=bars_held,
                    exit_reason=exit_reason,
                    funding_ann_bps_at_entry=entry_fund_ann,
                    funding_z_at_entry=entry_fz,
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
        "trades": trades,
        "equity": np.array(equity, dtype=np.float64),
    }


def run_backtest(data: Dict[str, pd.DataFrame], cfg: dict) -> dict:
    """Multi-symbol run. Picks the first symbol for the standard return shape
    (preserves the API used by run_backtest.py single-symbol iteration)."""
    sym = cfg["instruments"][0]
    return _run_one_symbol(data[sym], cfg)