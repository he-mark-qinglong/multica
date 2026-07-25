"""VPVR-aware 5m strategy: VWAP rejection + volume spike entry, ATR trailing exit.

This is a **fresh** implementation — it does not import or extend
``vpvr_reversion_1d_20260621.*``. The closest it shares with the parent is
the file layout. The logic and indicators are entirely new:

Indicators (all computed from data in ``[t-W, t-1]`` only — no look-ahead):

  * ``vwap``        — rolling VWAP using ``typical * volume`` over ``vwap_lookback_bars``.
  * ``vwap_z``      — z-score of close vs VWAP (``(close - vwap) / rolling_std``).
  * ``vol_spike``   — ``volume > volume_spike_ratio * rolling_mean(volume, n)``.
  * ``vpvr``        — 5m rolling volume profile (price bins × total volume).
  * ``vpvr_poc``    — the highest-volume price (Point of Control) in the window.
  * ``vpvr_val/vah``— the lower / upper edges of the 70% value area.
  * ``vpvr_z_dist`` — z-score of (close - vpvr_poc) over the value-area range.
  * ``atr``         — Wilder ATR over ``atr_period`` bars.

Entry (long):
    vwap_z[t] < -vwap_rejection_z   (close well below VWAP — rejection)
    AND vol_spike[t]
    AND vpvr_z_dist[t] < -vpvr_distance_z_min  (close near or below VAL — value area)

Entry (short) is the mirror.

Exit (first triggered wins):
    1. ATR trailing: close crosses ``entry ± k * atr[t]`` (k=3.0).
    2. VWAP re-touch: |close - vwap| < target_vwap_touch_atr * atr  → take profit.
    3. Time stop: position held > max_holding_bars.

Costs: ``fees_bps_per_side + slippage_bps_per_side`` applied both sides.
Position sizing: 1% per signal, capped at 5% gross across open positions.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Indicators (pure).
# ---------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    hi_lo = df["high"] - df["low"]
    hi_pc = (df["high"] - prev_close).abs()
    lo_pc = (df["low"] - prev_close).abs()
    tr = pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)
    return tr


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rolling_vwap(df: pd.DataFrame, window: int) -> pd.Series:
    """Rolling VWAP using typical price (HLC/3) × volume."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    sum_pv = pv.rolling(window, min_periods=window).sum()
    sum_v = df["volume"].rolling(window, min_periods=window).sum()
    vwap = sum_pv / sum_v.replace(0.0, np.nan)
    return vwap


def rolling_vwap_z(df: pd.DataFrame, window: int, vwap_window: int) -> pd.Series:
    """Z-score of close around the rolling VWAP.

    Std is computed off the rolling sample of close deviations from VWAP.
    """
    vwap = rolling_vwap(df, vwap_window)
    dev = df["close"] - vwap
    std = dev.rolling(window, min_periods=window).std()
    z = dev / std.replace(0.0, np.nan)
    return z


def volume_spike(df: pd.DataFrame, window: int, ratio: float) -> pd.Series:
    """Boolean: ``volume > ratio * rolling_mean(volume, window)``."""
    vma = df["volume"].rolling(window, min_periods=window).mean()
    return df["volume"] > (ratio * vma)


def rolling_volume_profile(
    df: pd.DataFrame, window: int, n_bins: int, value_area_pct: float
) -> pd.DataFrame:
    """Compute POC, VAL, VAH for a rolling 5m volume profile.

    For each bar ``t`` we use the prior ``window`` bars (close prices + volumes)
    and bin them into ``n_bins`` price buckets. POC = bin with max volume,
    then expand outward from POC until the cumulative volume covers
    ``value_area_pct`` of total → VAL/VAH are the bin edges.
    """
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    n = len(df)
    poc = np.full(n, np.nan)
    val = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    for i in range(window, n):
        c = closes[i - window : i]
        v = volumes[i - window : i]
        lo, hi = float(c.min()), float(c.max())
        if hi <= lo:
            poc[i] = lo
            val[i] = lo
            vah[i] = lo
            continue
        edges = np.linspace(lo, hi, n_bins + 1)
        idx = np.clip(((c - lo) / (hi - lo) * n_bins).astype(int), 0, n_bins - 1)
        bin_vol = np.bincount(idx, weights=v, minlength=n_bins)
        if bin_vol.sum() <= 0:
            continue
        poc_bin = int(np.argmax(bin_vol))
        poc[i] = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
        target = float(value_area_pct) * bin_vol.sum()
        # Expand from POC outward until cumulative volume covers target.
        cum = bin_vol[poc_bin]
        lo_bin = poc_bin
        hi_bin = poc_bin
        while cum < target and (lo_bin > 0 or hi_bin < n_bins - 1):
            # Expand the side with more volume next.
            left = bin_vol[lo_bin - 1] if lo_bin > 0 else -1.0
            right = bin_vol[hi_bin + 1] if hi_bin < n_bins - 1 else -1.0
            if right >= left:
                hi_bin += 1
                cum += bin_vol[hi_bin]
            else:
                lo_bin -= 1
                cum += bin_vol[lo_bin]
        val[i] = edges[lo_bin]
        vah[i] = edges[hi_bin + 1]
    out = pd.DataFrame({"vpvr_poc": poc, "vpvr_val": val, "vpvr_vah": vah}, index=df.index)
    return out


def vpvr_distance_z(df: pd.DataFrame, profile: pd.DataFrame, window: int) -> pd.Series:
    """Signed z-score of (close - poc) scaled by the rolling value-area half-width."""
    half_range = ((profile["vpvr_vah"] - profile["vpvr_val"]) / 2.0).replace(0.0, np.nan)
    return (df["close"] - profile["vpvr_poc"]) / half_range


# ---------------------------------------------------------------------------
# Annotated frame.
# ---------------------------------------------------------------------------

def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    ent = cfg["entry"]
    ex = cfg["exit"]
    vp = cfg["vpvr"]

    out["atr"] = wilder_atr(out, 14)
    out["vwap"] = rolling_vwap(out, ent["vwap_lookback_bars"])
    out["vwap_z"] = rolling_vwap_z(out, 60, ent["vwap_lookback_bars"])
    out["vol_spike"] = volume_spike(out, 60, ent["volume_spike_ratio"])

    profile = rolling_volume_profile(out, vp["window_bars"], vp["n_bins"], vp["value_area_pct"])
    out = pd.concat([out, profile], axis=1)
    out["vpvr_z_dist"] = vpvr_distance_z(out, profile, vp["window_bars"])

    out["long_entry"] = (
        out["vwap_z"].notna()
        & (out["vwap_z"] < -ent["vwap_rejection_z"])
        & out["vol_spike"].fillna(False)
        & out["vpvr_z_dist"].notna()
        & (out["vpvr_z_dist"] < -ent["vpvr_distance_z_min"])
        & out["atr"].notna()
    )
    out["short_entry"] = (
        out["vwap_z"].notna()
        & (out["vwap_z"] > ent["vwap_rejection_z"])
        & out["vol_spike"].fillna(False)
        & out["vpvr_z_dist"].notna()
        & (out["vpvr_z_dist"] > ent["vpvr_distance_z_min"])
        & out["atr"].notna()
    )
    out["entry_signal"] = out["long_entry"] | out["short_entry"]
    return out


# ---------------------------------------------------------------------------
# Trade + result dataclasses.
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    reason: str
    pnl: float
    pnl_pct: float
    bars_held: int
    atr_at_entry: float


@dataclass
class BacktestResult:
    symbol: str
    n_trades: int
    win_rate: float
    profit_factor: float
    avg_holding_bars: float
    total_return: float
    annualized_sharpe: float
    annualized_sortino: float
    max_drawdown: float
    turnover_per_year: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Exit logic + backtest loop.
# ---------------------------------------------------------------------------

def _exit_on_bar(bar: pd.Series, direction: str, entry_price: float, atr_at_entry: float, cfg: dict) -> Tuple[bool, str, float]:
    ex = cfg["exit"]
    cur = float(bar["close"])
    atr_now = float(bar["atr"]) if not pd.isna(bar.get("atr", np.nan)) else atr_at_entry

    # 1. ATR trailing (anchor = entry).
    if direction == "long":
        if cur < entry_price - ex["atr_trailing_k"] * atr_now:
            return True, f"atr_trailing<entry-{ex['atr_trailing_k']}*ATR", cur
    else:
        if cur > entry_price + ex["atr_trailing_k"] * atr_now:
            return True, f"atr_trailing>entry+{ex['atr_trailing_k']}*ATR", cur

    # 2. VWAP re-touch: profit-take when close crosses back through vwap within 1 ATR.
    if not pd.isna(bar.get("vwap", np.nan)):
        vwap = float(bar["vwap"])
        if direction == "long" and cur >= vwap and (vwap - entry_price) <= ex["target_vwap_touch_atr"] * atr_now:
            return True, "vwap_retouch", cur
        if direction == "short" and cur <= vwap and (entry_price - vwap) <= ex["target_vwap_touch_atr"] * atr_now:
            return True, "vwap_retouch", cur

    return False, "", cur


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    df = annotate(df, cfg)
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    per_signal = cfg["sizing"]["per_signal_weight_pct"]
    max_gross = cfg["sizing"]["max_gross_exposure_pct"]
    max_hold = cfg["exit"]["max_holding_bars"]
    starting = cfg["starting_capital_usd"]
    symbol = cfg.get("_symbol", "?")

    equity = starting
    in_pos: Optional[str] = None
    entry_price = 0.0
    entry_idx = 0
    entry_date: Optional[pd.Timestamp] = None
    atr_at_entry = 0.0
    open_notional_pct = 0.0
    trades: List[Trade] = []
    equity_path: List[Tuple[pd.Timestamp, float]] = []

    for i, (date, row) in enumerate(df.iterrows()):
        price = float(row["close"])

        if equity_path and equity_path[-1][0] == date:
            equity_path[-1] = (date, equity)
        else:
            equity_path.append((date, equity))

        if in_pos is None:
            if bool(row.get("long_entry", False)) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "long"
                atr_at_entry = float(row["atr"]) if not pd.isna(row.get("atr", np.nan)) else 0.0
                entry_idx = i
                entry_date = date
                open_notional_pct += per_signal
            elif bool(row.get("short_entry", False)) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "short"
                atr_at_entry = float(row["atr"]) if not pd.isna(row.get("atr", np.nan)) else 0.0
                entry_idx = i
                entry_date = date
                open_notional_pct += per_signal
        else:
            exit_now, reason, exit_price_raw = _exit_on_bar(row, in_pos, entry_price, atr_at_entry, cfg)
            bars_held = i - entry_idx

            if not exit_now and bars_held >= max_hold:
                exit_now = True
                reason = f"time_stop>={max_hold}b"

            if exit_now:
                exit_price_net = exit_price_raw * (1 - cost_per_side)
                pnl_pct = (exit_price_net / entry_price - 1.0) * (1 if in_pos == "long" else -1.0)
                pnl_abs = pnl_pct * open_notional_pct * equity
                trades.append(
                    Trade(
                        symbol=symbol, direction=in_pos,
                        entry_date=entry_date, entry_price=entry_price,
                        exit_date=date, exit_price=exit_price_net,
                        reason=reason, pnl=pnl_abs, pnl_pct=pnl_pct,
                        bars_held=bars_held, atr_at_entry=atr_at_entry,
                    )
                )
                equity += pnl_abs
                equity_path[-1] = (date, equity)
                in_pos = None
                open_notional_pct = 0.0

    if in_pos is not None:
        last = df.iloc[-1]
        lp = float(last["close"]) * (1 - cost_per_side)
        pnl_pct = (lp / entry_price - 1.0) * (1 if in_pos == "long" else -1.0)
        pnl_abs = pnl_pct * open_notional_pct * equity
        trades.append(
            Trade(symbol=symbol, direction=in_pos, entry_date=entry_date,
                  entry_price=entry_price, exit_date=df.index[-1], exit_price=lp,
                  reason="force_close_eod", pnl=pnl_abs, pnl_pct=pnl_pct,
                  bars_held=len(df) - 1 - entry_idx, atr_at_entry=atr_at_entry)
        )
        equity += pnl_abs
        if equity_path and equity_path[-1][0] == df.index[-1]:
            equity_path[-1] = (df.index[-1], equity)
        else:
            equity_path.append((df.index[-1], equity))

    eq = pd.Series([v for _, v in equity_path], index=[d for d, _ in equity_path], name="equity")
    if eq.empty:
        eq = pd.Series([starting], index=[df.index[0]], name="equity")
    return _summarize(symbol, trades, eq, df.index, cfg)


def _summarize(symbol: str, trades: List[Trade], equity: pd.Series, dates: pd.DatetimeIndex, cfg: dict) -> BacktestResult:
    starting = cfg["starting_capital_usd"]
    n = len(trades)
    if n == 0:
        return BacktestResult(
            symbol=symbol, n_trades=0, win_rate=0.0, profit_factor=0.0,
            avg_holding_bars=0.0, total_return=0.0, annualized_sharpe=0.0,
            annualized_sortino=0.0, max_drawdown=0.0, turnover_per_year=0.0,
            equity_curve=pd.Series([starting], index=[dates[0]]), trades=[],
        )
    pnls = np.array([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    win_rate = float(len(wins)) / n
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    avg_hold = float(np.mean([t.bars_held for t in trades]))

    eq = equity.copy()
    reindexed = eq.reindex(dates).ffill().fillna(starting)
    daily_ret = reindexed.pct_change().fillna(0.0)
    if daily_ret.std() == 0:
        sharpe = 0.0
        sortino = 0.0
    else:
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(252 * 24 * 12))  # 5m bars/year scaling
        downside = daily_ret[daily_ret < 0]
        dstd = downside.std() if len(downside) > 0 else daily_ret.std()
        sortino = float(daily_ret.mean() / dstd * math.sqrt(252 * 24 * 12)) if dstd and dstd > 0 else 0.0
    rolling_max = reindexed.cummax()
    drawdown = (reindexed - rolling_max) / rolling_max
    max_dd = float(drawdown.min())
    total_ret = float(reindexed.iloc[-1] / starting - 1)
    years = max((dates[-1] - dates[0]).days / 365.25, 1.0 / 365.25)
    turnover = n / years

    return BacktestResult(
        symbol=symbol, n_trades=n, win_rate=win_rate, profit_factor=profit_factor,
        avg_holding_bars=avg_hold, total_return=total_ret, annualized_sharpe=sharpe,
        annualized_sortino=sortino, max_drawdown=max_dd, turnover_per_year=turnover,
        equity_curve=reindexed, trades=trades,
    )


def baseline_hold(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    starting = cfg["starting_capital_usd"]
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    sym = cfg.get("_symbol", "?")
    if len(df) < 2:
        return BacktestResult(sym, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                              pd.Series([starting], index=[df.index[0]]), [])
    first_close = float(df["close"].iloc[0]) * (1 + cost_per_side)
    last_close = float(df["close"].iloc[-1]) * (1 - cost_per_side)
    pnl_pct = last_close / first_close - 1.0
    trades = [Trade(sym, "long", df.index[0], first_close, df.index[-1], last_close,
                    "buyhold", pnl_pct * starting, pnl_pct, len(df) - 1, 0.0)]
    eq = pd.Series([starting, starting * (1 + pnl_pct)], index=[df.index[0], df.index[-1]])
    reindexed = eq.reindex(df.index).ffill().fillna(starting)
    return BacktestResult(
        symbol=sym, n_trades=1, win_rate=1.0 if pnl_pct > 0 else 0.0,
        profit_factor=float("inf") if pnl_pct > 0 else 0.0,
        avg_holding_bars=len(df) - 1, total_return=pnl_pct,
        annualized_sharpe=0.0, annualized_sortino=0.0, max_drawdown=0.0,
        turnover_per_year=1.0 / max((df.index[-1] - df.index[0]).days / 365.25, 1.0),
        equity_curve=reindexed, trades=trades,
    )