"""VPVR-aware 1m strategy: KAMA reversal + RSI divergence entry, time-stop exit.

Fresh implementation. Uses Kaufman Adaptive Moving Average (KAMA) as the
primary trend/reversal proxy — KAMA flattens in chop and accelerates in
trends, so a "pivot" (close crossing KAMA *against* the recent direction)
combined with an RSI divergence flags a high-quality reversal.

Indicators:

  * ``kama``  — Kaufman Adaptive MA over ``kama.er_period`` bars with
                fast/slow smoothing constants ``fast/slow``.
  * ``rsi``   — Wilder RSI over ``rsi.period`` bars.
  * ``atr``   — Wilder ATR over 14 bars.
  * ``vpvr_poc/val/vah`` — 1m rolling volume profile.

Entry (long):
    close[t-1] < kama[t-1] AND close[t] > kama[t]   (upward KAMA pivot)
    AND rsi[t] < rsi_oversold + 5 AND rsi[t] > rsi_oversold   (still oversold)
    AND bullish RSI divergence in [t-lb, t]:
        rsi[t] > rsi[t-2] AND close[t] < close[t-2]   (higher-low on RSI, lower-low on price)
    AND vpvr_z_dist[t] < -vpvr_distance_z_min  (close below VAL — overshoot)

Entry (short) is the mirror.

Exit (first triggered):
    1. Time-stop: bars_held >= time_stop_bars → forced close at close[t].
    2. ATR trailing: close crosses entry ± atr_trailing_k * atr[t].
    3. Profit lock: once unrealized >= profit_lock_atr * atr, trail by 1x ATR from peak.

Sizing: 1% per signal, 5% gross cap. Costs 2bps round-trip.
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


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = -delta.clip(upper=0.0)
    avg_up = up.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_dn = dn.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_dn.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def kaufman_kama(close: pd.Series, er_period: int, fast: int, slow: int) -> pd.Series:
    """Kaufman Adaptive Moving Average.

    Efficiency ratio = |close - close.shift(er_period)| / sum(|close - close.shift(1)|, er_period)
    smoothing = (er * (2/(fast+1) - 2/(slow+1)) + 2/(slow+1))^2
    kama_t = kama_{t-1} + smoothing * (close - kama_{t-1})
    """
    direction = (close - close.shift(er_period)).abs()
    volatility = close.diff().abs().rolling(er_period, min_periods=er_period).sum()
    er = (direction / volatility.replace(0.0, np.nan)).fillna(0.0)
    sc = (er * (2.0 / (fast + 1) - 2.0 / (slow + 1)) + 2.0 / (slow + 1)) ** 2
    kama = pd.Series(np.nan, index=close.index, dtype="float64")
    # Initial seed = first non-NaN close.
    first_valid = close.first_valid_index()
    if first_valid is None:
        return kama
    kama.iloc[close.index.get_loc(first_valid)] = close.loc[first_valid]
    for i in range(close.index.get_loc(first_valid) + 1, len(close)):
        prev = kama.iloc[i - 1]
        if pd.isna(prev):
            kama.iloc[i] = close.iloc[i]
        else:
            kama.iloc[i] = prev + sc.iloc[i] * (close.iloc[i] - prev)
    return kama


def rolling_volume_profile(
    df: pd.DataFrame, window: int, n_bins: int, value_area_pct: float
) -> pd.DataFrame:
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    n = len(df)
    poc = np.full(n, np.nan); val = np.full(n, np.nan); vah = np.full(n, np.nan)
    for i in range(window, n):
        c = closes[i - window : i]; v = volumes[i - window : i]
        lo, hi = float(c.min()), float(c.max())
        if hi <= lo:
            poc[i] = lo; val[i] = lo; vah[i] = lo; continue
        edges = np.linspace(lo, hi, n_bins + 1)
        idx = np.clip(((c - lo) / (hi - lo) * n_bins).astype(int), 0, n_bins - 1)
        bin_vol = np.bincount(idx, weights=v, minlength=n_bins)
        if bin_vol.sum() <= 0: continue
        poc_bin = int(np.argmax(bin_vol))
        poc[i] = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
        target = float(value_area_pct) * bin_vol.sum()
        cum = bin_vol[poc_bin]; lo_bin = poc_bin; hi_bin = poc_bin
        while cum < target and (lo_bin > 0 or hi_bin < n_bins - 1):
            left = bin_vol[lo_bin - 1] if lo_bin > 0 else -1.0
            right = bin_vol[hi_bin + 1] if hi_bin < n_bins - 1 else -1.0
            if right >= left:
                hi_bin += 1; cum += bin_vol[hi_bin]
            else:
                lo_bin -= 1; cum += bin_vol[lo_bin]
        val[i] = edges[lo_bin]
        vah[i] = edges[hi_bin + 1]
    return pd.DataFrame({"vpvr_poc": poc, "vpvr_val": val, "vpvr_vah": vah}, index=df.index)


def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    kc = cfg["kama"]
    rc = cfg["rsi"]
    vp = cfg["vpvr"]

    out["atr"] = wilder_atr(out, 14)
    out["kama"] = kaufman_kama(out["close"], kc["er_period"], kc["fast"], kc["slow"])
    out["rsi"] = wilder_rsi(out["close"], rc["period"])

    prof = rolling_volume_profile(out, vp["window_bars"], vp["n_bins"], vp["value_area_pct"])
    out = pd.concat([out, prof], axis=1)
    half_range = ((out["vpvr_vah"] - out["vpvr_val"]) / 2.0).replace(0.0, np.nan)
    out["vpvr_z_dist"] = (out["close"] - out["vpvr_poc"]) / half_range

    have_kama = out["kama"].notna()
    have_rsi = out["rsi"].notna()
    have_dist = out["vpvr_z_dist"].notna()

    prev_close = out["close"].shift(1)
    prev_kama = out["kama"].shift(1)
    cross_up = (prev_close < prev_kama) & (out["close"] > out["kama"])
    cross_dn = (prev_close > prev_kama) & (out["close"] < out["kama"])

    lb = cfg["entry"]["rsi_divergence_lookback"]
    # Bullish divergence: price made a lower low (close[t] < min(close[t-lb..t-2])) while
    # RSI made a higher low (rsi[t] > min(rsi[t-lb..t-2])).
    window_close = out["close"].shift(2).rolling(lb, min_periods=lb).min()
    window_rsi = out["rsi"].shift(2).rolling(lb, min_periods=lb).min()
    bullish_div = (out["close"] < window_close) & (out["rsi"] > window_rsi)
    bearish_div = (out["close"] > out["close"].shift(2).rolling(lb, min_periods=lb).max()) & (
        out["rsi"] < out["rsi"].shift(2).rolling(lb, min_periods=lb).max()
    )

    out["long_entry"] = (
        have_kama & have_rsi & have_dist
        & cross_up
        & (out["rsi"] >= rc["oversold"])
        & (out["rsi"] <= rc["oversold"] + 15)
        & bullish_div.fillna(False)
        & (out["vpvr_z_dist"] < -cfg["entry"]["vpvr_distance_z_min"])
    )
    out["short_entry"] = (
        have_kama & have_rsi & have_dist
        & cross_dn
        & (out["rsi"] >= rc["overbought"] - 15)
        & (out["rsi"] <= rc["overbought"])
        & bearish_div.fillna(False)
        & (out["vpvr_z_dist"] > cfg["entry"]["vpvr_distance_z_min"])
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
    time_stop = cfg["exit"]["time_stop_bars"]
    trailing_k = cfg["exit"]["atr_trailing_k"]
    profit_lock_atr = cfg["exit"]["profit_lock_atr"]
    starting = cfg["starting_capital_usd"]
    symbol = cfg.get("_symbol", "?")

    equity = starting
    in_pos: Optional[str] = None
    entry_price = 0.0
    entry_idx = 0
    entry_date: Optional[pd.Timestamp] = None
    atr_at_entry = 0.0
    peak_price = 0.0
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
            # Track peak for profit lock.
            if in_pos == "long":
                peak_price = max(peak_price, price)
            else:
                peak_price = min(peak_price, price)

            atr_now = float(row["atr"]) if not pd.isna(row.get("atr", np.nan)) else atr_at_entry
            bars_held = i - entry_idx

            exit_now = False
            reason = ""
            exit_price_raw = price

            # 1. Time-stop.
            if not exit_now and bars_held >= time_stop:
                exit_now = True; reason = f"time_stop>={time_stop}b"
                exit_price_raw = price

            # 2. ATR trailing.
            if not exit_now:
                if in_pos == "long" and price < entry_price - trailing_k * atr_now:
                    exit_now = True; reason = f"atr_trailing<entry-{trailing_k}*ATR"; exit_price_raw = price
                elif in_pos == "short" and price > entry_price + trailing_k * atr_now:
                    exit_now = True; reason = f"atr_trailing>entry+{trailing_k}*ATR"; exit_price_raw = price

            # 3. Profit-lock: once unrealized >= profit_lock_atr, trail from peak.
            if not exit_now and profit_lock_atr > 0:
                if in_pos == "long":
                    unreal_atr = (peak_price - entry_price) / max(atr_at_entry, 1e-9)
                    floor = peak_price - 1.0 * atr_now
                    if unreal_atr >= profit_lock_atr and price < floor:
                        exit_now = True; reason = "profit_lock"; exit_price_raw = price
                else:
                    unreal_atr = (entry_price - peak_price) / max(atr_at_entry, 1e-9)
                    ceiling = peak_price + 1.0 * atr_now
                    if unreal_atr >= profit_lock_atr and price > ceiling:
                        exit_now = True; reason = "profit_lock"; exit_price_raw = price

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