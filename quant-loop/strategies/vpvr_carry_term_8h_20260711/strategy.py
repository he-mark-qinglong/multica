"""V8 strategy — multi-venue carry + term-structure + VPVR POC.

Iter#72 (Campaign SMA-32405). Genuinely new vs V2 (iter#74 funding
1d):

  * 8h timeframe (vs 1d) — captures intraday funding regimes more
    granularly.
  * Cross-venue FUNDING SPREAD (Binance vs synthetic alt-venue) is the
    primary entry signal (not single-venue funding z-score).
  * Term-basis proxy is current funding - rolling mean (not z-score
    against z-window).
  * Exit is ATR(14) trailing at 2x, with spread-decay override + max
    holding cap (not vol-target horizon).
  * Funding carry is accrued explicitly per bar (8h funding event).

Public API: VARIANT_KEY, run_backtest(df, cfg).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "vpvr_carry_term_8h_20260711"


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - prev_close).abs()
    l_pc = (df["low"] - prev_close).abs()
    return pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1).astype(np.float64)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rolling_vpvr(close: np.ndarray, volume: np.ndarray, window: int, n_bins: int,
                  value_area_pct: float) -> dict:
    n = len(close)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val_ = np.full(n, np.nan)
    for i in range(window - 1, n):
        sub_c = close[i - window + 1: i + 1]
        sub_v = volume[i - window + 1: i + 1]
        p_lo = float(np.nanmin(sub_c))
        p_hi = float(np.nanmax(sub_c))
        if not np.isfinite(p_lo) or not np.isfinite(p_hi) or p_hi <= p_lo:
            continue
        bins = np.linspace(p_lo, p_hi, n_bins + 1)
        idx = np.clip(np.digitize(sub_c, bins) - 1, 0, n_bins - 1)
        bin_v = np.zeros(n_bins)
        for k in range(n_bins):
            mask = idx == k
            bin_v[k] = sub_v[mask].sum() if mask.any() else 0.0
        bin_centers = (bins[:-1] + bins[1:]) / 2.0
        poc[i] = float(bin_centers[int(np.argmax(bin_v))])
        total = float(bin_v.sum())
        if total <= 0:
            continue
        order = np.argsort(-bin_v)
        selected = set()
        running = 0.0
        for b in order:
            selected.add(int(b))
            running += float(bin_v[b])
            if running / total >= value_area_pct:
                break
        val_[i] = float(bins[min(selected)])
        vah[i] = float(bins[max(selected) + 1])
    return {"poc": poc, "vah": vah, "val": val_}


def _term_basis_z(df: pd.DataFrame, cfg_tb: dict) -> pd.Series:
    """Funding-minus-rolling-mean z-score (term-structure proxy)."""
    fund = df["fundingRate_binance"].astype(np.float64)
    w = int(cfg_tb["window_bars"])
    roll_mean = fund.rolling(w, min_periods=w).mean()
    term = fund - roll_mean
    zw = int(cfg_tb["z_window_bars"])
    mu = term.rolling(zw, min_periods=zw).mean()
    sd = term.rolling(zw, min_periods=zw).std(ddof=0)
    return ((term - mu) / sd.replace(0.0, np.nan)).rename("term_basis_z")


def build_signal(df: pd.DataFrame, cfg: dict) -> pd.Series:
    ind = cfg["vpvr"]
    ent = cfg["entry"]

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    atr = wilder_atr(df, int(cfg["sizing"]["atr_period"]))
    profile = _rolling_vpvr(close.to_numpy(), volume.to_numpy(),
                            int(ind["window_bars"]), int(ind["n_bins"]),
                            float(ind["value_area_pct"]))
    poc = pd.Series(profile["poc"], index=df.index)
    near_poc = (close - poc).abs() <= float(ent["require_poc_touch_atr_k"]) * atr

    spread_bps = df["funding_spread_bps"].astype(np.float64)
    term_z = _term_basis_z(df, cfg["term_basis"])

    sig = np.zeros(len(df), dtype=np.int8)
    last_entry = -10_000
    min_gap = int(ent["min_gap_bars_between_trades"])
    spread_thr = float(ent["spread_threshold_bps"])
    term_thr = float(ent["term_basis_z_min"])
    pos_side = ent["side_when_pos_spread"]
    neg_side = ent["side_when_neg_spread"]

    spread_arr = spread_bps.to_numpy()
    term_arr = term_z.to_numpy()
    near_arr = near_poc.to_numpy()
    for i in range(len(df)):
        if i - last_entry < min_gap:
            continue
        if not (np.isfinite(spread_arr[i]) and np.isfinite(term_arr[i]) and near_arr[i]):
            continue
        if spread_arr[i] >= spread_thr and term_arr[i] >= term_thr:
            sig[i] = +1 if pos_side == "long" else -1
            last_entry = i
        elif spread_arr[i] <= -spread_thr and term_arr[i] <= -term_thr:
            sig[i] = +1 if neg_side == "long" else -1
            last_entry = i
    return pd.Series(sig, index=df.index, dtype=np.int8)


def _annualisation_factor(timeframe: str) -> float:
    tf = timeframe.strip().lower()
    if tf.endswith("h"):
        h = int(tf[:-1])
        return math.sqrt(24 * 365 / h)
    if tf.endswith("d"):
        d = int(tf[:-1])
        return math.sqrt(365 / d)
    if tf.endswith("m"):
        m = int(tf[:-1])
        return math.sqrt(60 * 24 * 365 / m)
    raise ValueError(tf)


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
    funding_carry_pnl_pct: float
    spread_at_entry_bps: float


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    sig = build_signal(df, cfg).to_numpy().astype(np.int8)
    close = df["close"].astype(np.float64).to_numpy()
    atr = wilder_atr(df, int(cfg["sizing"]["atr_period"])).to_numpy()
    fund_binance = df["fundingRate_binance"].astype(np.float64).to_numpy()
    spread = df["funding_spread_bps"].astype(np.float64).to_numpy()

    n = len(df)
    starting = float(cfg["starting_capital_usd"])
    fee_bps = float(cfg["fees_bps_per_side"])
    slip_bps = float(cfg["slippage_bps_per_side"])
    cost_round_trip = 2.0 * (fee_bps + slip_bps) / 10000.0

    atr_trail_k = float(cfg["exit"]["atr_trail_k"])
    spread_decay = bool(cfg["exit"]["spread_decay_exit"])
    spread_decay_thr = float(cfg["exit"]["spread_decay_threshold_bps"])
    max_holding = int(cfg["exit"]["max_holding_bars"])
    min_holding = int(cfg["exit"]["min_holding_bars"])
    hard_stop_k = float(cfg["exit"]["hard_stop_atr_k"])

    target = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        if sig[i] != 0 and sig[i - 1] == 0:
            target[i] = sig[i]

    positions = np.zeros(n, dtype=np.int8)
    bars_held = np.zeros(n, dtype=np.int32)
    trades: List[Trade] = []
    entry_idx: Optional[int] = None
    entry_price = 0.0
    entry_side = 0
    entry_spread = 0.0
    trail_high = 0.0  # running peak since entry
    trail_low = np.inf  # running trough since entry (for short trail)

    for i in range(1, n):
        prev = int(positions[i - 1])
        cur = prev
        cur_target = int(target[i])

        if cur_target != 0 and prev == 0:
            cur = cur_target
            entry_idx = i
            entry_price = float(close[i])
            entry_side = cur
            entry_spread = float(spread[i]) if np.isfinite(spread[i]) else 0.0
            bars_held[i] = 1
            trail_high = float(close[i])
            trail_low = float(close[i])
        elif prev != 0:
            cur = prev
            bars_held[i] = int(bars_held[i - 1]) + 1
            held = bars_held[i]

            if close[i] > trail_high:
                trail_high = float(close[i])
            if close[i] < trail_low:
                trail_low = float(close[i])

            exit_now = False
            reason = ""
            move = (close[i] - entry_price) * entry_side

            if move <= -hard_stop_k * atr[i]:
                exit_now = True; reason = "hard_stop"
            elif entry_side == +1 and close[i] <= trail_high - atr_trail_k * atr[i]:
                exit_now = True; reason = "atr_trail"
            elif entry_side == -1 and close[i] >= trail_low + atr_trail_k * atr[i]:
                exit_now = True; reason = "atr_trail"
            elif spread_decay and held >= min_holding and np.isfinite(spread[i]) and \
                 abs(spread[i]) <= spread_decay_thr:
                exit_now = True; reason = "spread_decay"
            elif held >= max_holding:
                exit_now = True; reason = "max_holding"

            if exit_now:
                cur = 0

        if prev != 0 and cur == 0 and entry_idx is not None:
            exit_price = float(close[i])
            gross = (exit_price / entry_price - 1.0) * entry_side
            # Accrued funding carry: while held, every 8h bar pays the
            # position-side funding rate.
            carry_pnl = 0.0
            for k in range(entry_idx + 1, i + 1):
                if k < n:
                    carry_pnl += -float(fund_binance[k]) * int(positions[k - 1] if k > 0 else 0)
            net = gross - cost_round_trip + carry_pnl
            trades.append(Trade(
                variant=VARIANT_KEY,
                symbol=cfg.get("_symbol", "?"),
                direction="long" if entry_side == +1 else "short",
                entry_ts=df.index[entry_idx].isoformat(),
                entry_price=entry_price,
                exit_ts=df.index[i].isoformat(),
                exit_price=exit_price,
                pnl_pct=float(net),
                bars_held=int(bars_held[i - 1]) + 1,
                exit_reason=reason,
                funding_carry_pnl_pct=float(carry_pnl),
                spread_at_entry_bps=float(entry_spread),
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
            # Funding carry per bar (8h event).
            bar_return[i] += -float(fund_binance[i]) * prev
    for t in trades:
        bh = max(int(t.bars_held), 1)
        amort = cost_round_trip / bh
        ei = df.index.get_indexer([pd.Timestamp(t.entry_ts)])[0]
        for k in range(bh):
            j = ei + k + 1
            if j < n:
                bar_return[j] -= amort

    equity = np.empty(n)
    equity[0] = starting
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + bar_return[i])

    return {
        "trades": trades,
        "equity": equity,
        "bar_return": bar_return,
        "positions": positions,
        "n_bars": n,
        "span_start": df.index[0].isoformat(),
        "span_end": df.index[-1].isoformat(),
    }