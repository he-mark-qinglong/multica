"""V1_funding_term_curve — VPVR + funding-term curve steepness z-spread.

Iter#97 (Campaign SMA-33616, 2026-07-14).

Spirit v1.0 §cycle-46 lesson: ``funding-rate-delta`` signal IS real (pf>1,
51.9% WR) but the single-point delta is noisy. Instead of using the raw
funding delta (funding_now - funding_{-1 bar}), this strategy uses the
**funding term curve steepness**:

  z_spread_t = (funding_1h(t) - funding_8h_rolling_mean(t)) / funding_8h_rolling_std(t)

This compares the *current* short-horizon funding to the *8h rolling
distribution*. Funding is paid every 8h on Binance USD-margined perps; we
forward-fill the 8h rate to 1h bar frequency. The 8h rolling mean /
std then act as a smoothed "term rate benchmark". A 2σ deviation means
the market is paying an extreme carry premium right now vs. the recent
8h average — classic funding-crowding / exhaustion regime.

Entry decision:
  - z_spread_t >  +2.0  → longs paying extreme premium. Expect unwind →
                          SHORT, **provided** price > VPVR POC (price
                          already on upper side of value).
  - z_spread_t <  -2.0  → shorts paying extreme premium. Expect squeeze →
                          LONG,  **provided** price < VPVR POC.

Filter rationale (cycle-46 lesson 2): trend filters destroy carry when
applied blindly, but using POC purely as a directional filter (price > POC
for shorts, price < POC for longs) keeps us aligned with where the value
center is without imposing an asymmetric TP/SL.

Exit (asymmetric execution cycle-46 lesson 1): ATR-trail at k=2.5; the trail
ratchets only on the favorable side. Time-stop = 24 bars (1h = 24 hours = 1
day, capturing intraday funding unwind). Funding carry is charged per-bar
(longs pay funding_rate when positive, shorts receive).

Multi-symbol: BTCUSDT + ETHUSDT, independent sizing per symbol, combined
PnL book at book-level (per-symbol trades CSV, aggregate metrics JSON).

Public API: VARIANT_KEY, run_backtest(data, cfg).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "vpvr_funding_term_curve_1h_20260714"


# ---------------------------------------------------------------------------
# Core indicators
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder-style ATR computed on a price DataFrame."""
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _vpvr_poc(close: pd.Series, volume: pd.Series,
              window: int, n_bins: int) -> pd.Series:
    """Rolling VPVR POC computed strictly from past bars (no look-ahead).

    ``poc[i]`` is the POC of bars [i - window + 1, i] inclusive — using
    data up to and including bar i. We shift by 1 elsewhere so the signal
    is usable without peeking at close[i].
    """
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


def _z_spread(funding: pd.Series, lookback: int) -> pd.Series:
    """Funding-term curve steepness.

        z_spread_t = (funding_t - rolling_mean_{lookback}(funding)) / rolling_std_{lookback}(funding)
    """
    mu = funding.rolling(lookback, min_periods=lookback).mean()
    sd = funding.rolling(lookback, min_periods=lookback).std(ddof=0)
    return ((funding - mu) / sd.replace(0.0, np.nan)).astype(np.float64)


# ---------------------------------------------------------------------------
# Trade record
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
    funding_z_at_entry: float
    poc_dist_atr_at_entry: float
    funding_paid_pct: float = 0.0


# ---------------------------------------------------------------------------
# Per-symbol backtest
# ---------------------------------------------------------------------------

def _run_one_symbol(df: pd.DataFrame, cfg: dict) -> dict:
    """Single-symbol backtest. df has cols [open, high, low, close, volume,
    fundingRate]; index is 1h timestamps."""
    p = cfg["params"]
    sym = cfg["instruments"][0]

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    funding = df["fundingRate"].astype(np.float64)

    atr = _atr(df, p["atr_period"])
    poc = _vpvr_poc(close, volume, p["vpvr_window_bars"], p["vpvr_bins"])
    # Use POC shifted by 1 so the signal is formed without peeking at bar i
    poc_safe = poc.shift(1)
    z = _z_spread(funding, p["funding_z_lookback_bars"])

    close_arr = close.values
    high_arr = high.values
    low_arr = low.values
    atr_arr = atr.values
    poc_arr = poc_safe.values
    z_arr = z.values
    funding_arr = funding.values

    fee = p["fee_bps_per_fill"] / 10000.0
    slip = p["slippage_bps_per_fill"] / 10000.0
    round_trip_cost = 2 * (fee + slip)
    warmup = int(p["warmup_bars"])
    starting_cap = float(cfg.get(
        "starting_capital_per_symbol_usd", cfg["starting_capital_usd"]))

    trades: List[Trade] = []
    equity = [starting_cap]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    entry_funding_z = 0.0
    entry_poc_dist_atr = 0.0
    bars_held = 0
    peak_favorable = 0.0  # max favorable excursion (in pct terms, signed)
    funding_paid_running = 0.0
    cooldown = p["min_gap_bars_between_trades"]

    for i in range(1, len(close_arr)):
        px = float(close_arr[i])
        hi = float(high_arr[i])
        lo = float(low_arr[i])
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        poc_v = float(poc_arr[i]) if np.isfinite(poc_arr[i]) else px
        z_v = float(z_arr[i]) if np.isfinite(z_arr[i]) else 0.0
        fund_v = float(funding_arr[i]) if np.isfinite(funding_arr[i]) else 0.0

        # Charge funding carry continuously while in position (per bar)
        if pos != 0:
            # If long and funding > 0, long pays; if long and funding < 0, long receives.
            funding_paid_running += -pos * fund_v

        if pos == 0:
            cooldown += 1
            if cooldown < p["min_gap_bars_between_trades"] or at <= 0.0:
                equity.append(equity[-1])
                continue

            # Entry: extreme term-curve steepness + POC directional filter
            cand = 0
            if z_v >= p["z_entry_threshold"] and px > poc_v:
                cand = -1  # longs paying extreme — short into the unwind
            elif z_v <= -p["z_entry_threshold"] and px < poc_v:
                cand = +1  # shorts paying extreme — long the squeeze

            if cand != 0:
                pos = cand
                entry_idx = i
                entry_px = px
                entry_funding_z = z_v
                entry_poc_dist_atr = (px - poc_v) / at if at > 0 else 0.0
                bars_held = 0
                peak_favorable = 0.0
                funding_paid_running = 0.0
                equity.append(equity[-1])
                continue
            else:
                equity.append(equity[-1])
                continue

        # In position — apply ATR trailing stop
        bars_held += 1
        move = (px / entry_px - 1.0) * pos
        if move > peak_favorable:
            peak_favorable = move
        at_move = at / entry_px if entry_px > 0 else 0.0

        # Trailing-stop trigger: peak_favorable retraces by k * at_move
        trailing_hit = False
        if peak_favorable > 0 and at_move > 0.0:
            if move <= peak_favorable - p["atr_trail_k"] * at_move:
                trailing_hit = True

        exit_now = False
        exit_reason = ""
        if trailing_hit:
            exit_now = True
            exit_reason = "atr_trail"
        elif bars_held >= p["max_hold_bars"]:
            exit_now = True
            exit_reason = "time_stop"
        elif lo <= entry_px * (1.0 + pos * (-p["hard_stop_atr_k"] * at_move)) and pos != 0:
            # Symmetric hard-stop on close within bar (using low/high)
            exit_now = True
            exit_reason = "hard_stop"

        if exit_now:
            gross = move
            net = gross - round_trip_cost + funding_paid_running
            trades.append(Trade(
                variant=VARIANT_KEY, symbol=sym,
                direction="long" if pos == +1 else "short",
                entry_ts=str(close.index[entry_idx]), entry_price=entry_px,
                exit_ts=str(close.index[i]), exit_price=px,
                pnl_pct=float(net), bars_held=bars_held,
                exit_reason=exit_reason,
                funding_z_at_entry=entry_funding_z,
                poc_dist_atr_at_entry=float(entry_poc_dist_atr),
                funding_paid_pct=float(funding_paid_running),
            ))
            equity.append(equity[-1] * (1.0 + net))
            pos = 0
            entry_idx = None
            cooldown = 0
            peak_favorable = 0.0
            funding_paid_running = 0.0
            continue

        equity.append(equity[-1])  # mark-to-market not included for "actuals"

    # Force-close any open position at end
    if pos != 0 and entry_idx is not None:
        px = float(close_arr[-1])
        move = (px / entry_px - 1.0) * pos
        gross = move
        net = gross - round_trip_cost + funding_paid_running
        trades.append(Trade(
            variant=VARIANT_KEY, symbol=sym,
            direction="long" if pos == +1 else "short",
            entry_ts=str(close.index[entry_idx]), entry_price=entry_px,
            exit_ts=str(close.index[-1]), exit_price=px,
            pnl_pct=float(net), bars_held=bars_held,
            exit_reason="eod_close",
            funding_z_at_entry=entry_funding_z,
            poc_dist_atr_at_entry=float(entry_poc_dist_atr),
            funding_paid_pct=float(funding_paid_running),
        ))
        equity.append(equity[-1] * (1.0 + net))

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": len(close_arr),
        "span_start": str(close.index[0]),
        "span_end": str(close.index[-1]),
        "trades": trades,
        "equity": np.array(equity, dtype=np.float64),
    }


def run_backtest(data: Dict[str, pd.DataFrame], cfg: dict) -> dict:
    """Top-level entry point used by run_backtest.py.

    ``data`` is {symbol: df}.
    """
    sym = cfg["instruments"][0]
    return _run_one_symbol(data[sym], cfg)
