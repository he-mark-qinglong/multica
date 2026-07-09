"""VPVR-aware 1m strategy: high-volume-node breakout entry, trailing + profit-lock exit.

Fresh implementation. Distinct from the 1d reversion family and the 1m KAMA
reversal (V3). This strategy uses the *distribution* of volume across price
bins to identify structural support/resistance (HVN) and trades breakouts of
those nodes with volume confirmation.

Indicators (computed from [t-W, t-1] only):

  * ``vpvr_poc``    — point of control of the rolling 1m volume profile.
  * ``hvn_upper``   — highest price whose volume-bin exceeds ``hvn_z_threshold * mean``.
  * ``hvn_lower``   — lowest price whose volume-bin exceeds ``hvn_z_threshold * mean``.
  * ``atr``         — Wilder ATR over 14 bars.
  * ``vol_ma``      — SMA of volume over ``vol_ma_period``.

Entry (long):
    close[t] > hvn_upper[t] + hvn_break_buffer_atr * atr[t]
    AND volume[t] > volume_spike_ratio * vol_ma[t]

Entry (short) is the mirror.

Exit (first triggered wins):
    1. ATR trailing: close crosses entry ± atr_trailing_k * atr.
    2. Profit-lock: once unrealized >= profit_lock_atr * atr_at_entry, trail by
       profit_lock_trail_atr * atr from the highest (long) / lowest (short) price
       observed since entry.
    3. Time-stop: bars_held >= max_holding_bars.

Sizing: 1% per signal, 5% gross cap. Costs: 2 bps round-trip.
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


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    hi_lo = df["high"] - df["low"]
    hi_pc = (df["high"] - prev_close).abs()
    lo_pc = (df["low"] - prev_close).abs()
    return pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rolling_hvn_levels(
    df: pd.DataFrame, window: int, n_bins: int, z_threshold: float
) -> pd.DataFrame:
    """For each bar, compute the upper and lower HVN levels (highest and lowest
    price whose volume-bin exceeds ``z_threshold * mean_bin_vol``) from the
    rolling ``window`` bars.

    Returns ``hvn_upper, hvn_lower``. We use the *edge* of each qualifying bin
    so the breakout level is interpretable.
    """
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    n = len(df)
    upper = np.full(n, np.nan); lower = np.full(n, np.nan); poc = np.full(n, np.nan)
    for i in range(window, n):
        c = closes[i - window : i]; v = volumes[i - window : i]
        lo, hi = float(c.min()), float(c.max())
        if hi <= lo:
            upper[i] = lo; lower[i] = lo; poc[i] = lo
            continue
        edges = np.linspace(lo, hi, n_bins + 1)
        idx = np.clip(((c - lo) / (hi - lo) * n_bins).astype(int), 0, n_bins - 1)
        bin_vol = np.bincount(idx, weights=v, minlength=n_bins).astype(float)
        if bin_vol.sum() <= 0: continue
        mean_v = bin_vol.mean()
        threshold = z_threshold * mean_v
        # The bin index whose volume exceeds the threshold.
        hvn_bins = np.where(bin_vol >= threshold)[0]
        if len(hvn_bins) == 0:
            upper[i] = hi; lower[i] = lo
        else:
            upper[i] = edges[int(hvn_bins.max()) + 1]
            lower[i] = edges[int(hvn_bins.min())]
        # POC.
        poc_bin = int(np.argmax(bin_vol))
        poc[i] = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
    return pd.DataFrame({"hvn_upper": upper, "hvn_lower": lower, "vpvr_poc": poc}, index=df.index)


def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    ent = cfg["entry"]; ex = cfg["exit"]; vp = cfg["vpvr"]
    out["atr"] = wilder_atr(out, 14)
    out["vol_ma"] = out["volume"].rolling(ent["vol_ma_period"], min_periods=ent["vol_ma_period"]).mean()
    out["vol_spike"] = out["volume"] > ent["volume_spike_ratio"] * out["vol_ma"]
    hvn = rolling_hvn_levels(out, vp["window_bars"], vp["n_bins"], vp["hvn_z_threshold"])
    out = pd.concat([out, hvn], axis=1)

    have_hvn = out["hvn_upper"].notna() & out["hvn_lower"].notna()
    buf = ent["hvn_break_buffer_atr"] * out["atr"]
    out["long_entry"] = (
        have_hvn & out["vol_spike"].fillna(False) & out["atr"].notna()
        & (out["close"] > out["hvn_upper"] + buf)
    )
    out["short_entry"] = (
        have_hvn & out["vol_spike"].fillna(False) & out["atr"].notna()
        & (out["close"] < out["hvn_lower"] - buf)
    )
    out["entry_signal"] = out["long_entry"] | out["short_entry"]
    return out


@dataclass
class Trade:
    symbol: str; direction: str
    entry_date: pd.Timestamp; entry_price: float
    exit_date: pd.Timestamp; exit_price: float
    reason: str; pnl: float; pnl_pct: float
    bars_held: int; atr_at_entry: float


@dataclass
class BacktestResult:
    symbol: str; n_trades: int; win_rate: float; profit_factor: float
    avg_holding_bars: float; total_return: float
    annualized_sharpe: float; annualized_sortino: float
    max_drawdown: float; turnover_per_year: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    df = annotate(df, cfg)
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    per_signal = cfg["sizing"]["per_signal_weight_pct"]
    max_gross = cfg["sizing"]["max_gross_exposure_pct"]
    trailing_k = cfg["exit"]["atr_trailing_k"]
    lock_atr = cfg["exit"]["profit_lock_atr"]
    lock_trail = cfg["exit"]["profit_lock_trail_atr"]
    max_hold = cfg["exit"]["max_holding_bars"]
    starting = cfg["starting_capital_usd"]
    symbol = cfg.get("_symbol", "?")

    equity = starting
    in_pos: Optional[str] = None
    entry_price = 0.0; entry_idx = 0
    entry_date: Optional[pd.Timestamp] = None
    atr_at_entry = 0.0
    peak_price = 0.0  # for profit-lock
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
                peak_price = price
                entry_idx = i; entry_date = date
                open_notional_pct += per_signal
            elif bool(row.get("short_entry", False)) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "short"
                atr_at_entry = float(row["atr"]) if not pd.isna(row.get("atr", np.nan)) else 0.0
                peak_price = price
                entry_idx = i; entry_date = date
                open_notional_pct += per_signal
        else:
            if in_pos == "long":
                peak_price = max(peak_price, price)
            else:
                peak_price = min(peak_price, price)

            atr_now = float(row["atr"]) if not pd.isna(row.get("atr", np.nan)) else atr_at_entry
            bars_held = i - entry_idx

            exit_now = False; reason = ""; exit_price_raw = price

            # 1. ATR trailing.
            if in_pos == "long" and price < entry_price - trailing_k * atr_now:
                exit_now = True; reason = f"atr_trailing<entry-{trailing_k}*ATR"
            elif in_pos == "short" and price > entry_price + trailing_k * atr_now:
                exit_now = True; reason = f"atr_trailing>entry+{trailing_k}*ATR"

            # 2. Profit-lock.
            if not exit_now and lock_atr > 0 and atr_at_entry > 0:
                if in_pos == "long":
                    unreal_atr = (peak_price - entry_price) / atr_at_entry
                    floor = peak_price - lock_trail * atr_now
                    if unreal_atr >= lock_atr and price < floor:
                        exit_now = True; reason = "profit_lock"
                else:
                    unreal_atr = (entry_price - peak_price) / atr_at_entry
                    ceiling = peak_price + lock_trail * atr_now
                    if unreal_atr >= lock_atr and price > ceiling:
                        exit_now = True; reason = "profit_lock"

            # 3. Time-stop.
            if not exit_now and bars_held >= max_hold:
                exit_now = True; reason = f"time_stop>={max_hold}b"

            if exit_now:
                exit_price_net = exit_price_raw * (1 - cost_per_side)
                pnl_pct = (exit_price_net / entry_price - 1.0) * (1 if in_pos == "long" else -1.0)
                pnl_abs = pnl_pct * open_notional_pct * equity
                trades.append(
                    Trade(symbol=symbol, direction=in_pos,
                          entry_date=entry_date, entry_price=entry_price,
                          exit_date=date, exit_price=exit_price_net,
                          reason=reason, pnl=pnl_abs, pnl_pct=pnl_pct,
                          bars_held=bars_held, atr_at_entry=atr_at_entry)
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
        return BacktestResult(symbol=symbol, n_trades=0, win_rate=0.0, profit_factor=0.0,
                              avg_holding_bars=0.0, total_return=0.0, annualized_sharpe=0.0,
                              annualized_sortino=0.0, max_drawdown=0.0, turnover_per_year=0.0,
                              equity_curve=pd.Series([starting], index=[dates[0]]), trades=[])
    pnls = np.array([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    win_rate = float(len(wins)) / n
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    avg_hold = float(np.mean([t.bars_held for t in trades]))
    eq = equity.copy()
    reindexed = eq.reindex(dates).ffill().fillna(starting)
    daily_ret = reindexed.pct_change().fillna(0.0)
    if daily_ret.std() == 0:
        sharpe = 0.0; sortino = 0.0
    else:
        annual_scale = math.sqrt(252 * 24 * 60)  # 1m bars
        sharpe = float(daily_ret.mean() / daily_ret.std() * annual_scale)
        downside = daily_ret[daily_ret < 0]
        dstd = downside.std() if len(downside) > 0 else daily_ret.std()
        sortino = float(daily_ret.mean() / dstd * annual_scale) if dstd and dstd > 0 else 0.0
    rolling_max = reindexed.cummax()
    drawdown = (reindexed - rolling_max) / rolling_max
    max_dd = float(drawdown.min())
    total_ret = float(reindexed.iloc[-1] / starting - 1)
    years = max((dates[-1] - dates[0]).days / 365.25, 1.0 / 365.25)
    turnover = n / years
    return BacktestResult(symbol=symbol, n_trades=n, win_rate=win_rate, profit_factor=profit_factor,
                          avg_holding_bars=avg_hold, total_return=total_ret,
                          annualized_sharpe=sharpe, annualized_sortino=sortino,
                          max_drawdown=max_dd, turnover_per_year=turnover,
                          equity_curve=reindexed, trades=trades)


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
    return BacktestResult(symbol=sym, n_trades=1, win_rate=1.0 if pnl_pct > 0 else 0.0,
                          profit_factor=float("inf") if pnl_pct > 0 else 0.0,
                          avg_holding_bars=len(df) - 1, total_return=pnl_pct,
                          annualized_sharpe=0.0, annualized_sortino=0.0, max_drawdown=0.0,
                          turnover_per_year=1.0 / max((df.index[-1] - df.index[0]).days / 365.25, 1.0),
                          equity_curve=reindexed, trades=trades)