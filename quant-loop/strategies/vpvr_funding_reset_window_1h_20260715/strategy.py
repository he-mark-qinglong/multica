"""V3_funding_reset_window - VPVR reversal timed to perp-funding reset.

Iter#108 (Campaign SMA-34339, 2026-07-15, axis: funding reset window).

Genuinely new axis: PERP-FUNDING RESET timestamp (00/04/08/12/16/20 UTC,
8h cadence). Different from:
  - V1 (iter#106) macro-calendar FOMC/CPI (multi-day scheduled events)
  - V2 (iter#107) time-of-day session overlap (intra-day, daily recurring)
V3 uses the funding-reset timestamp as a market-microstructure boundary
that creates forced-positioning events (traders with the wrong side pay
funding, often closing / flipping at the reset).

Mechanism:
  1. Compute VPVR POC over 48-bar 1h window (2 days).
  2. POC distance: d_poc = (price - POC) / ATR(14).
  3. Funding z-score: rolling z of fundingRate over 96 bars (4d). Funding
     comes every 8h so we have one funding observation per 8 1h-bars in
     steady state (forward-filled from data_loader).
  4. Funding reset hours UTC: [0, 4, 8, 12, 16, 20]. Window = ±1h around
     each reset, i.e., the funding bar itself plus the bar before and after.
  5. Entry: |d_poc|>1.2 AND |funding_z|>1.2 AND in funding-reset window.
     Long : funding_z > 0 (longs pay) AND price stretched below POC (the
            long-paid-and-sold drop creates a reversion opportunity).
     Short: funding_z < 0 (shorts pay) AND price stretched above POC.
  6. Asymmetric exit: TP at +2x ATR, SL at -1x ATR. 2:1 reward/risk.
     Time-stop 6 bars (6h), vol-target 6 bars, max 12 bars (12h).
  7. Cooldown 4 bars.

Universe: BTCUSDT only (funding_analysis/ has BTCUSDT_funding.parquet).

Public API: VARIANT_KEY, build_signal(df, cfg), run_backtest(df, cfg).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "vpvr_funding_reset_window_1h_20260715"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wilder_atr(df, period):
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rolling_poc(close, volume, window, n_bins):
    n = len(close)
    poc = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        sub_c = close[i - window + 1: i + 1]
        sub_v = volume[i - window + 1: i + 1]
        if not np.isfinite(sub_c).all() or not np.isfinite(sub_v).all():
            continue
        p_lo, p_hi = float(np.nanmin(sub_c)), float(np.nanmax(sub_c))
        if p_hi <= p_lo:
            continue
        edges = np.linspace(p_lo, p_hi, n_bins + 1)
        idx = np.clip(np.searchsorted(edges, sub_c, side="right") - 1, 0, n_bins - 1)
        bin_v = np.zeros(n_bins)
        for k in range(n_bins):
            m = idx == k
            bin_v[k] = sub_v[m].sum() if m.any() else 0.0
        bin_centers = (edges[:-1] + edges[1:]) / 2.0
        poc[i] = float(bin_centers[int(np.argmax(bin_v))])
    return poc


def _funding_z(funding, window):
    n = len(funding)
    z = np.full(n, np.nan, dtype=np.float64)
    arr = funding.to_numpy()
    for i in range(window, n):
        seg = arr[i - window + 1: i + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) < window // 2:
            continue
        mu = float(np.mean(seg))
        sd = float(np.std(seg, ddof=0))
        if sd <= 0 or not np.isfinite(sd):
            continue
        z[i] = (arr[i] - mu) / sd if np.isfinite(arr[i]) else np.nan
    return z


def _reset_window_flag(index, reset_hours, pre_bars, post_bars):
    """Per-bar flag: 1 if bar is within ±bars of any reset hour (UTC)."""
    n = len(index)
    flag = np.zeros(n, dtype=np.int8)
    hours = index.hour.to_numpy()
    minutes = index.minute.to_numpy()
    # Identify reset bar: hour==reset_hour AND minute==0
    is_reset = np.zeros(n, dtype=bool)
    for r in reset_hours:
        is_reset |= ((hours == r) & (minutes == 0))
    reset_idx = np.where(is_reset)[0]
    for r in reset_idx:
        lo = max(0, r - pre_bars)
        hi = min(n, r + post_bars + 1)
        flag[lo:hi] = 1
    return flag


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

def build_signal(df, cfg):
    p = cfg["params"]
    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)

    poc = _rolling_poc(close.to_numpy(), volume.to_numpy(),
                       int(p["vpvr_window_bars"]), int(p["vpvr_bins"]))
    atr = _wilder_atr(df, int(p["atr_period"]))
    poc_dist = (close.to_numpy() - poc) / atr.to_numpy()

    funding_z = _funding_z(df["fundingRate"], int(p["funding_z_window_bars"]))
    win_flag = _reset_window_flag(
        df.index,
        p["funding_reset_hours_utc"],
        int(p["pre_reset_blackout_bars"]),
        int(p["post_reset_drift_bars"]),
    )

    sig = np.zeros(len(df), dtype=np.int8)
    last_entry_idx = -10_000
    min_gap = int(p["min_gap_bars_between_trades"])
    pd_thr = float(p["poc_distance_z_entry"])
    fz_thr = float(p["funding_z_entry_threshold"])

    for i in range(len(df)):
        if i - last_entry_idx < min_gap:
            continue
        if not np.isfinite(poc_dist[i]) or not np.isfinite(funding_z[i]) \
                or not np.isfinite(atr.iloc[i]) or not np.isfinite(poc[i]):
            continue
        if win_flag[i] != 1:
            continue
        pd_v = poc_dist[i]; fz_v = funding_z[i]
        # Long: longs paid (fz > 0), price stretched below POC (pd_v < -thr)
        if pd_v < -pd_thr and fz_v > fz_thr:
            sig[i] = +1
            last_entry_idx = i
        # Short: shorts paid (fz < 0), price stretched above POC (pd_v > +thr)
        elif pd_v > pd_thr and fz_v < -fz_thr:
            sig[i] = -1
            last_entry_idx = i
    return pd.Series(sig, index=df.index, dtype=np.int8)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

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
    poc_dist_at_entry: float
    funding_z_at_entry: float


def run_backtest(df, cfg):
    p = cfg["params"]
    sym = cfg.get("_symbol", "BTCUSDT")
    sig = build_signal(df, cfg).to_numpy().astype(np.int8)
    close = df["close"].astype(np.float64).to_numpy()
    atr = _wilder_atr(df, int(p["atr_period"])).to_numpy()

    close_s = df["close"].astype(np.float64)
    volume_s = df["volume"].astype(np.float64)
    poc = _rolling_poc(close_s.to_numpy(), volume_s.to_numpy(),
                       int(p["vpvr_window_bars"]), int(p["vpvr_bins"]))
    poc_dist = (close_s.to_numpy() - poc) / atr
    funding_z = _funding_z(df["fundingRate"], int(p["funding_z_window_bars"]))
    win_flag = _reset_window_flag(
        df.index,
        p["funding_reset_hours_utc"],
        int(p["pre_reset_blackout_bars"]),
        int(p["post_reset_drift_bars"]),
    )

    n = len(df)
    starting = float(cfg["starting_capital_usd"])
    fee = float(p["fee_bps_per_fill"]) / 10000.0
    slip = float(p["slippage_bps_per_fill"]) / 10000.0
    cost_round_trip = 2.0 * (fee + slip)

    tp_k = float(p["tp_atr_k"])
    sl_k = float(p["sl_atr_k"])
    time_stop_bars = int(p["time_stop_bars"])
    vol_target_bars = int(p["vol_target_horizon_bars"])
    max_holding = int(p["max_holding_bars"])
    risk_pct = float(p["risk_per_trade_pct"])

    target = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        if sig[i] != 0 and sig[i - 1] == 0:
            target[i] = sig[i]

    positions = np.zeros(n, dtype=np.int8)
    bars_held = np.zeros(n, dtype=np.int32)
    trades: List[Trade] = []
    entry_idx = None
    entry_price = 0.0
    entry_side = 0
    entry_pd = 0.0
    entry_fz = 0.0

    for i in range(1, n):
        prev = int(positions[i - 1])
        cur = prev
        cur_target = int(target[i])

        if cur_target != 0 and prev == 0:
            cur = cur_target
            entry_idx = i
            entry_price = float(close[i])
            entry_side = cur
            entry_pd = float(poc_dist[i]) if np.isfinite(poc_dist[i]) else 0.0
            entry_fz = float(funding_z[i]) if np.isfinite(funding_z[i]) else 0.0
            bars_held[i] = 1
        elif prev != 0:
            cur = prev
            bars_held[i] = int(bars_held[i - 1]) + 1
            held_now = bars_held[i]
            move = (close[i] - entry_price) * entry_side

            exit_now = False
            exit_reason = ""
            if move >= tp_k * atr[i]:
                exit_now = True; exit_reason = "tp_atr"
            elif move <= -sl_k * atr[i]:
                exit_now = True; exit_reason = "sl_atr"
            elif held_now >= time_stop_bars:
                exit_now = True; exit_reason = "time_stop"
            elif held_now >= vol_target_bars:
                exit_now = True; exit_reason = "vol_target_horizon"
            elif held_now >= max_holding:
                exit_now = True; exit_reason = "max_holding_cap"
            if exit_now:
                cur = 0

        if prev != 0 and cur == 0 and entry_idx is not None:
            exit_price = float(close[i])
            gross = (exit_price / entry_price - 1.0) * entry_side
            net = gross - cost_round_trip
            trades.append(Trade(
                variant=VARIANT_KEY, symbol=sym,
                direction="long" if entry_side == +1 else "short",
                entry_ts=df.index[entry_idx].isoformat(),
                entry_price=entry_price,
                exit_ts=df.index[i].isoformat(),
                exit_price=exit_price,
                pnl_pct=float(net),
                bars_held=int(bars_held[i - 1]) + 1,
                exit_reason=exit_reason,
                poc_dist_at_entry=entry_pd,
                funding_z_at_entry=entry_fz,
            ))
            entry_idx = None
            entry_price = 0.0
            entry_side = 0

        positions[i] = cur

    bar_return = np.zeros(n)
    for i in range(1, n):
        prev = int(positions[i - 1])
        if prev == 0:
            bar_return[i] = 0.0
        else:
            bar_return[i] = (close[i] / close[i - 1] - 1.0) * prev
    for t in trades:
        bh = max(int(t.bars_held), 1)
        amort = cost_round_trip / bh
        ei = df.index.get_indexer([pd.Timestamp(t.entry_ts)])[0]
        for k in range(bh):
            j = ei + k + 1
            if 0 <= j < n:
                bar_return[j] -= amort

    equity = np.empty(n)
    equity[0] = starting
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + risk_pct * bar_return[i])

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "trades": trades,
        "equity": equity,
        "bar_return": bar_return,
        "positions": positions,
        "n_bars": n,
        "span_start": df.index[0].isoformat(),
        "span_end": df.index[-1].isoformat(),
    }