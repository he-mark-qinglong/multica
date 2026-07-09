"""VPVR-aware 15m strategy: Donchian breakout + ADX range filter entry, regime-switch exit.

Fresh implementation. Distinct from the parent 1d reversion family.

Indicators (computed from [t-W, t-1] only):

  * ``donchian_upper/lower``  — max/min of prior ``donchian_n`` highs/lows, shifted.
  * ``atr``                   — Wilder ATR over ``atr_period``.
  * ``atr_ma``                — SMA of ATR over ``atr_ma_period`` (long-run vol baseline).
  * ``vol_ma``                — SMA of volume over ``volume_ma_period``.
  * ``adx``                   — Wilder ADX over ``adx_period``.
  * ``vpvr_poc/val/vah``      — rolling 15m volume profile (POC, value-area edges).

Entry (long):
    close[t] > donchian_upper[t] + buffer_atr * atr[t]
    AND atr[t] between [atr_ma_ratio_low, atr_ma_ratio_high] * atr_ma[t]   (regime band)
    AND adx[t] between [adx_min, adx_max]                                 (range filter)
    AND volume[t] > volume_ratio_min * vol_ma[t]
    AND vpvr_z_dist[t] > 0 (close above POC)  → breakout of the high-vol region.

Entry (short) is the mirror.

Exit (regime-switch + target):
    1. Regime-switch: atr/atr_ma[t] > regime_atr_ma_high → vol-expansion event,
       force-close (don't stay in if the regime has shifted away from the entry regime).
    2. Regime-switch: atr/atr_ma[t] < regime_atr_ma_low → vol-collapse, force-close.
    3. ATR trailing: close crosses entry ± atr_trailing_k * atr.
    4. VPVR-POC target: when target_vpvr_poc=true and price has reached POC.
    5. Time stop: bars_held >= max_holding_bars.

Sizing: 1% per signal, capped at 5% gross. Costs: 2 bps round-trip.
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


def wilder_adx(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"]; low = df["low"]; prev_close = df["close"].shift(1)
    up = (high - high.shift(1)).clip(lower=0.0)
    dn = (low.shift(1) - low).clip(lower=0.0)
    plus_dm = np.where(up > dn, up, 0.0)
    minus_dm = np.where(dn > up, dn, 0.0)
    tr = true_range(df)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def donchian_upper(df: pd.DataFrame, n: int) -> pd.Series:
    return df["high"].rolling(n, min_periods=n).max().shift(1)


def donchian_lower(df: pd.DataFrame, n: int) -> pd.Series:
    return df["low"].rolling(n, min_periods=n).min().shift(1)


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
    ind = cfg["indicators"]
    ent = cfg["entry"]
    ex = cfg["exit"]
    vp = cfg["vpvr"]

    out["atr"] = wilder_atr(out, ind["atr_period"])
    out["atr_ma"] = out["atr"].rolling(ind["atr_ma_period"], min_periods=ind["atr_ma_period"]).mean()
    out["atr_ratio"] = out["atr"] / out["atr_ma"].replace(0.0, np.nan)
    out["vol_ma"] = out["volume"].rolling(ind["volume_ma_period"], min_periods=ind["volume_ma_period"]).mean()
    out["adx"] = wilder_adx(out, ind["adx_period"])
    out["donchian_upper"] = donchian_upper(out, ind["donchian_n"])
    out["donchian_lower"] = donchian_lower(out, ind["donchian_n"])

    prof = rolling_volume_profile(out, vp["window_bars"], vp["n_bins"], vp["value_area_pct"])
    out = pd.concat([out, prof], axis=1)
    half_range = ((out["vpvr_vah"] - out["vpvr_val"]) / 2.0).replace(0.0, np.nan)
    out["vpvr_z_dist"] = (out["close"] - out["vpvr_poc"]) / half_range

    vol_ok = out["volume"] > ind["volume_ratio_min"] * out["vol_ma"]
    atr_band = out["atr_ratio"].between(ind["atr_ma_ratio_low"], ind["atr_ma_ratio_high"])
    adx_band = out["adx"].between(ind["adx_min"], ind["adx_max"])
    have_bands = out["donchian_upper"].notna() & out["donchian_lower"].notna()

    upper_with_buf = out["donchian_upper"] + ent["donchian_break_buffer_atr"] * out["atr"]
    lower_with_buf = out["donchian_lower"] - ent["donchian_break_buffer_atr"] * out["atr"]

    out["long_entry"] = (
        (out["close"] > upper_with_buf)
        & vol_ok & atr_band & adx_band & have_bands
        & out["vpvr_z_dist"].notna()
        & (out["vpvr_z_dist"] > 0.5)  # close above the value-area upper edge → real breakout
    )
    out["short_entry"] = (
        (out["close"] < lower_with_buf)
        & vol_ok & atr_band & adx_band & have_bands
        & out["vpvr_z_dist"].notna()
        & (out["vpvr_z_dist"] < -0.5)  # close below the value-area lower edge → real breakdown
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


def _exit_on_bar(bar: pd.Series, direction: str, entry_price: float, atr_at_entry: float, cfg: dict) -> Tuple[bool, str, float]:
    ex = cfg["exit"]
    cur = float(bar["close"])
    atr_now = float(bar["atr"]) if not pd.isna(bar.get("atr", np.nan)) else atr_at_entry

    # 1. Regime-switch (vol-expansion or vol-collapse).
    if ex.get("regime_switch", True):
        atr_ratio = float(bar.get("atr_ratio", np.nan))
        if not pd.isna(atr_ratio):
            if atr_ratio > ex["regime_atr_ma_high"]:
                return True, f"regime_switch>={ex['regime_atr_ma_high']}", cur
            if atr_ratio < ex["regime_atr_ma_low"]:
                return True, f"regime_switch<={ex['regime_atr_ma_low']}", cur

    # 2. ATR trailing.
    if direction == "long":
        if cur < entry_price - ex["atr_trailing_k"] * atr_now:
            return True, f"atr_trailing<entry-{ex['atr_trailing_k']}*ATR", cur
    else:
        if cur > entry_price + ex["atr_trailing_k"] * atr_now:
            return True, f"atr_trailing>entry+{ex['atr_trailing_k']}*ATR", cur

    # 3. VPVR-VAH / VAL reversion target (mean-reversion to value-area edge).
    if ex.get("target_vpvr_poc", True):
        if direction == "long" and not pd.isna(bar.get("vpvr_val", np.nan)):
            # Long position: target the value-area low (mean-reversion after breakout).
            target = float(bar["vpvr_val"])
            if cur <= target:
                return True, "vpvr_val_target", target
        if direction == "short" and not pd.isna(bar.get("vpvr_vah", np.nan)):
            target = float(bar["vpvr_vah"])
            if cur >= target:
                return True, "vpvr_vah_target", target

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
                entry_idx = i; entry_date = date
                open_notional_pct += per_signal
            elif bool(row.get("short_entry", False)) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "short"
                atr_at_entry = float(row["atr"]) if not pd.isna(row.get("atr", np.nan)) else 0.0
                entry_idx = i; entry_date = date
                open_notional_pct += per_signal
        else:
            exit_now, reason, exit_price_raw = _exit_on_bar(row, in_pos, entry_price, atr_at_entry, cfg)
            bars_held = i - entry_idx
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
        # 15m bars → 252*24*4 bars/yr.
        annual_scale = math.sqrt(252 * 24 * 4)
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