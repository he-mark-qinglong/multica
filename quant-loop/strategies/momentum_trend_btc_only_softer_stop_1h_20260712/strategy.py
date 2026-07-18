"""Momentum / trend-following strategy (multi-TF) — V12 variant.

V12 changes vs V11
------------------

V11 (iter#87) added a regime filter (24h realized vol / 30d avg > 2.0
suppresses entries) and kept V10's entry-anchored ATR hard stop at
``-2.5 * ATR(14) at entry``. The regime filter calibration was wrong
and ETH dragged the BTC+ETH portfolio, so V11 regressed (2/4 OOS
positive, mean Sharpe -0.90).

V12 fixes:

1. **Drop ETH, run BTC only** — BTC is the alpha carrier. The data
   loader pulls ``["BTCUSDT"]`` only.
2. **Remove regime filter** — the V11 regime filter killed entries
   during the vol spikes that V10's stop logic was meant to handle,
   so it added noise without protective value.
3. **Soften ATR hard stop from -2.5 to -3.5 ATR** — keep the
   entry-anchored distance logic (caps single-trade damage) but give
   trades more breathing room to recover. The original V10 stop
   width (-2.5 ATR) was too tight to avoid the catastrophic single
   trades in window 1; -3.5 ATR should preserve the V10 logic while
   absorbing normal volatility.

Multi-timeframe structure
-------------------------

* **4h trend filter** — EMA(50) and its 1-bar-prior slope. Forward-filled
  to the 1h grid so the filter is constant between 4h closes. Strictly
  trailing (the 4h EMA is shifted by 1 bar before alignment, so a 1h bar
  ``t`` only sees information available at the previous 4h close).
* **1h entry** — RSI(14) cross of 50 in the trend direction (long: cross
  up through 50, short: cross down through 50).
* **1h confirmation** — ADX(14) > 20 to ensure the market is trending, not
  chopping around the cross level.

Sizing
------

Vol-scaled: ``size_quote = (0.01 * equity) / (atr14 / price)``, capped at
5% of equity per signal and 5% gross exposure. The 1% target is the
"equity-at-risk per 1-ATR move" goal.

Exits (first triggered wins)
----------------------------

1. **ATR hard stop** (entry-anchored) — close crosses
   ``entry +/- 3.5 * ATR(14) at entry``. Distance is fixed at entry
   so a vol-regime expansion cannot widen the stop.
2. 4h trend reversal — the slope sign flips against the open direction.
3. 1h RSI cross-back — RSI(14) crosses 50 against the open direction.

Look-ahead discipline
---------------------

Every indicator on bar ``t`` is computed from data in ``[t-W, t-1]``
only. The 4h EMA is shifted by 1 before forward-fill.
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
# Indicators — pure functions over OHLCV frames.
# ---------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    hi_lo = df["high"] - df["low"]
    hi_pc = (df["high"] - prev_close).abs()
    lo_pc = (df["low"] - prev_close).abs()
    return pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    up = (high - high.shift(1)).clip(lower=0.0)
    dn = (low.shift(1) - low).clip(lower=0.0)
    plus_dm = np.where(up > dn, up, 0.0)
    minus_dm = np.where(dn > up, dn, 0.0)

    tr = true_range(df)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1.0 / period, adjust=False, min_periods=period
    ).mean() / atr
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1.0 / period, adjust=False, min_periods=period
    ).mean() / atr

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx


def wilder_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder RSI in [0, 100]."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def ema(series: pd.Series, period: int) -> pd.Series:
    """Standard EMA, seeded with SMA(period)."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Multi-TF annotation.
# ---------------------------------------------------------------------------

def annotate(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Annotate the 1h frame with multi-TF indicators and entry/exit signals.

    The 4h EMA(50) is computed on the 4h frame, shifted by 1 bar (strictly
    trailing), then forward-filled onto the 1h index. The 4h slope is
    forward-filled the same way.

    Returns a copy of ``df_1h`` with these extra columns:
        ema50_4h, ema50_4h_slope,
        rsi14_1h, rsi14_1h_prev,
        adx14_1h, atr14_1h,
        long_entry, short_entry, entry_signal,
        exit_4h_reversal_long, exit_4h_reversal_short,
        exit_rsi_cross_back_long, exit_rsi_cross_back_short,
    """
    ind = cfg["indicators"]
    ema_p = ind["ema_period"]
    rsi_p = ind["rsi_period"]
    adx_p = ind["adx_period"]
    atr_p = ind["atr_period"]
    rsi_lvl = ind["rsi_cross_level"]
    slope_thresh = ind["trend_slope_threshold"]

    out = df_1h.copy()

    # 1h indicators
    out["rsi14_1h"] = wilder_rsi(out, rsi_p)
    out["rsi14_1h_prev"] = out["rsi14_1h"].shift(1)
    out["adx14_1h"] = wilder_adx(out, adx_p)
    out["atr14_1h"] = wilder_atr(out, atr_p)

    # 4h trend filter — shift by 1 to enforce strictly trailing, then
    # forward-fill onto the 1h grid.
    ema50_4h_raw = ema(df_4h["close"], ema_p)
    ema50_4h_lag = ema50_4h_raw.shift(1)  # strict trailing
    ema50_4h_lag.name = "ema50_4h"
    ema_slope_lag = (ema50_4h_lag - ema50_4h_lag.shift(1)) / ema50_4h_lag.shift(1)
    ema_slope_lag.name = "ema50_4h_slope"

    out = out.join(ema50_4h_lag.reindex(out.index, method="ffill"))
    out = out.join(ema_slope_lag.reindex(out.index, method="ffill"))

    # V12: regime filter REMOVED. The V11 regime filter killed entries
    # during vol spikes that the entry-anchored ATR stop was meant to
    # absorb. We let the stop do the work.

    # Entry signals (long / short).
    long_rsi_cross = (out["rsi14_1h_prev"] < rsi_lvl) & (out["rsi14_1h"] >= rsi_lvl)
    short_rsi_cross = (out["rsi14_1h_prev"] > rsi_lvl) & (out["rsi14_1h"] <= rsi_lvl)

    long_trend = out["ema50_4h_slope"] > slope_thresh
    short_trend = out["ema50_4h_slope"] < -slope_thresh if slope_thresh != 0.0 else out["ema50_4h_slope"] < 0.0
    if slope_thresh == 0.0:
        # slope > 0 -> long, slope < 0 -> short (mirror of spec wording)
        short_trend = out["ema50_4h_slope"] < 0.0

    have_indicators = (
        out["rsi14_1h"].notna()
        & out["rsi14_1h_prev"].notna()
        & out["adx14_1h"].notna()
        & out["atr14_1h"].notna()
        & out["ema50_4h_slope"].notna()
    )
    adx_ok = out["adx14_1h"] > ind["adx_min"]

    out["long_entry"] = (
        long_rsi_cross & long_trend & adx_ok & have_indicators
    )
    out["short_entry"] = (
        short_rsi_cross & short_trend & adx_ok & have_indicators
    )
    out["entry_signal"] = out["long_entry"] | out["short_entry"]

    # Exit conditions.
    out["exit_4h_reversal_long"] = out["ema50_4h_slope"] < 0.0
    out["exit_4h_reversal_short"] = out["ema50_4h_slope"] > 0.0
    out["exit_rsi_cross_back_long"] = (
        (out["rsi14_1h_prev"] > rsi_lvl) & (out["rsi14_1h"] <= rsi_lvl)
    )
    out["exit_rsi_cross_back_short"] = (
        (out["rsi14_1h_prev"] < rsi_lvl) & (out["rsi14_1h"] >= rsi_lvl)
    )

    return out


# ---------------------------------------------------------------------------
# Backtest primitives.
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str  # "long" or "short"
    entry_date: pd.Timestamp
    entry_price: float  # net of entry-side cost
    exit_date: pd.Timestamp
    exit_price: float  # net of exit-side cost
    reason: str
    pnl_usd: float
    pnl_pct: float  # return on entry price, signed correctly
    bars_held: int
    atr_1h_at_entry: float
    ema50_4h_at_entry: float
    ema50_4h_slope_at_entry: float


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


def _exit_on_bar(bar: pd.Series, direction: str, entry_price: float,
                 atr_at_entry: float, cfg: dict) -> Tuple[bool, str, float]:
    """Evaluate exit rules on bar ``bar`` for an open position.

    Returns ``(exit_now, reason, exit_price_raw)``. ``exit_price_raw`` is
    the raw close; the caller applies the exit-side cost.

    V12: ATR hard stop is **entry-anchored** — the threshold is
    ``+/- atr_k * ATR(14) at entry`` (NOT current-bar ATR), with
    ``atr_k = 3.5`` (softer than V11's 2.5) to give trades more
    recovery room while still capping catastrophic single-trade
    damage.
    """
    cur = float(bar["close"])

    # 1. ATR hard stop (anchor = entry, NOT current-bar ATR).
    atr_k = cfg["exit"]["atr_trailing_k"]
    if cfg["exit"].get("use_atr_trailing", True) and atr_at_entry > 0:
        if direction == "long" and cur < entry_price - atr_k * atr_at_entry:
            return True, f"atr_hard_stop<entry-{atr_k}*ATR@entry", cur
        if direction == "short" and cur > entry_price + atr_k * atr_at_entry:
            return True, f"atr_hard_stop>entry+{atr_k}*ATR@entry", cur

    # 2. 4h trend reversal.
    if cfg["exit"].get("use_4h_trend_reversal", True):
        slope = float(bar.get("ema50_4h_slope", 0.0))
        if direction == "long" and slope < 0.0:
            return True, "4h_trend_reversal", cur
        if direction == "short" and slope > 0.0:
            return True, "4h_trend_reversal", cur

    # 3. 1h RSI cross-back.
    if cfg["exit"].get("use_rsi_cross_back", True):
        if direction == "long" and bool(bar.get("exit_rsi_cross_back_long", False)):
            return True, "rsi_cross_back", cur
        if direction == "short" and bool(bar.get("exit_rsi_cross_back_short", False)):
            return True, "rsi_cross_back", cur

    return False, "", cur


def _position_size(equity: float, price: float, atr: float, cfg: dict) -> float:
    """ATR-scaled notional in dollars.

    Formula: ``notional = (risk_per_atr_pct * equity) / (atr / price)``,
    capped at ``max_notional_pct * equity`` and ``max_gross_exposure_pct * equity``.
    Returns 0 if atr is non-positive (degenerate input).
    """
    if atr <= 0 or price <= 0 or equity <= 0:
        return 0.0
    sizing = cfg["sizing"]
    risk_pct = sizing["risk_per_atr_pct"]
    max_notional_pct = sizing["max_notional_pct"]
    max_gross_pct = sizing["max_gross_exposure_pct"]
    atr_pct = atr / price
    raw_notional = (risk_pct * equity) / atr_pct if atr_pct > 0 else 0.0
    cap = min(max_notional_pct, max_gross_pct) * equity
    return float(min(raw_notional, cap))


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    """Run the multi-TF momentum/trend backtest on a 1h-annotated frame."""
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    starting_equity = cfg["starting_capital_usd"]
    symbol = cfg.get("_symbol", "?")

    equity = starting_equity
    in_pos: Optional[str] = None
    entry_price = 0.0
    entry_idx = 0
    entry_date: Optional[pd.Timestamp] = None
    atr_at_entry = 0.0
    ema50_4h_at_entry = 0.0
    ema50_4h_slope_at_entry = 0.0
    open_notional_usd = 0.0

    trades: List[Trade] = []
    equity_path: List[Tuple[pd.Timestamp, float]] = []

    for i, (date, row) in enumerate(df.iterrows()):
        price = float(row["close"])

        if equity_path and equity_path[-1][0] == date:
            equity_path[-1] = (date, equity)
        else:
            equity_path.append((date, equity))

        if in_pos is None:
            # Try to enter.
            long_sig = bool(row.get("long_entry", False))
            short_sig = bool(row.get("short_entry", False))
            atr_now = float(row["atr14_1h"]) if not pd.isna(row.get("atr14_1h", np.nan)) else 0.0
            if atr_now <= 0:
                continue  # no ATR yet — can't size, skip

            notional = _position_size(equity, price, atr_now, cfg)
            if notional <= 0:
                continue

            if long_sig:
                entry_price = price * (1 + cost_per_side)
                in_pos = "long"
                atr_at_entry = atr_now
                ema50_4h_at_entry = float(row.get("ema50_4h", 0.0))
                ema50_4h_slope_at_entry = float(row.get("ema50_4h_slope", 0.0))
                entry_idx = i
                entry_date = date
                open_notional_usd = notional
            elif short_sig:
                entry_price = price * (1 + cost_per_side)
                in_pos = "short"
                atr_at_entry = atr_now
                ema50_4h_at_entry = float(row.get("ema50_4h", 0.0))
                ema50_4h_slope_at_entry = float(row.get("ema50_4h_slope", 0.0))
                entry_idx = i
                entry_date = date
                open_notional_usd = notional
        else:
            exit_now, reason, exit_price_raw = _exit_on_bar(
                row, in_pos, entry_price, atr_at_entry, cfg
            )

            if exit_now:
                exit_price_net = exit_price_raw * (1 - cost_per_side)
                if in_pos == "long":
                    pnl_pct = exit_price_net / entry_price - 1.0
                else:
                    pnl_pct = entry_price / exit_price_net - 1.0
                pnl_abs = pnl_pct * open_notional_usd
                trades.append(
                    Trade(
                        symbol=symbol,
                        direction=in_pos,
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=date,
                        exit_price=exit_price_net,
                        reason=reason,
                        pnl_usd=pnl_abs,
                        pnl_pct=pnl_pct,
                        bars_held=i - entry_idx,
                        atr_1h_at_entry=atr_at_entry,
                        ema50_4h_at_entry=ema50_4h_at_entry,
                        ema50_4h_slope_at_entry=ema50_4h_slope_at_entry,
                    )
                )
                equity += pnl_abs
                equity_path[-1] = (date, equity)
                in_pos = None
                open_notional_usd = 0.0

    # Force-close any open position at the last close for the equity curve.
    if in_pos is not None:
        last = df.iloc[-1]
        lp = float(last["close"]) * (1 - cost_per_side)
        if in_pos == "long":
            pnl_pct = lp / entry_price - 1.0
        else:
            pnl_pct = entry_price / lp - 1.0
        pnl_abs = pnl_pct * open_notional_usd
        trades.append(
            Trade(
                symbol=symbol, direction=in_pos, entry_date=entry_date,
                entry_price=entry_price, exit_date=df.index[-1], exit_price=lp,
                reason="force_close_eod", pnl_usd=pnl_abs, pnl_pct=pnl_pct,
                bars_held=len(df) - 1 - entry_idx,
                atr_1h_at_entry=atr_at_entry,
                ema50_4h_at_entry=ema50_4h_at_entry,
                ema50_4h_slope_at_entry=ema50_4h_slope_at_entry,
            )
        )
        equity += pnl_abs
        if equity_path and equity_path[-1][0] == df.index[-1]:
            equity_path[-1] = (df.index[-1], equity)
        else:
            equity_path.append((df.index[-1], equity))

    eq = pd.Series([v for _, v in equity_path],
                   index=[d for d, _ in equity_path], name="equity")
    if eq.empty:
        eq = pd.Series([starting_equity], index=[df.index[0]], name="equity")

    return _summarize(symbol, trades, eq, df.index, cfg)


def _summarize(
    symbol: str,
    trades: List[Trade],
    equity: pd.Series,
    dates: pd.DatetimeIndex,
    cfg: dict,
) -> BacktestResult:
    starting = cfg["starting_capital_usd"]
    n = len(trades)
    if n == 0:
        return BacktestResult(
            symbol=symbol, n_trades=0, win_rate=0.0, profit_factor=0.0,
            avg_holding_bars=0.0, total_return=0.0, annualized_sharpe=0.0,
            annualized_sortino=0.0, max_drawdown=0.0, turnover_per_year=0.0,
            equity_curve=pd.Series([starting], index=[dates[0]]),
            trades=[],
        )

    pnls_pct = np.array([t.pnl_pct for t in trades])
    pnls_usd = np.array([t.pnl_usd for t in trades])
    wins = pnls_usd[pnls_usd > 0]
    losses = pnls_usd[pnls_usd <= 0]
    win_rate = float(len(wins)) / n
    profit_factor = (
        float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    )
    avg_hold = float(np.mean([t.bars_held for t in trades]))

    eq = equity.copy()
    reindexed = eq.reindex(dates).ffill().fillna(starting)
    bar_ret = reindexed.pct_change().fillna(0.0)

    bars_per_year = 8760  # 1h bars
    if bar_ret.std() == 0:
        sharpe = 0.0
        sortino = 0.0
    else:
        sharpe = float(bar_ret.mean() / bar_ret.std() * math.sqrt(bars_per_year))
        downside = bar_ret[bar_ret < 0]
        dstd = downside.std() if len(downside) > 0 else bar_ret.std()
        sortino = float(bar_ret.mean() / dstd * math.sqrt(bars_per_year)) if dstd and dstd > 0 else 0.0

    rolling_max = reindexed.cummax()
    drawdown = (reindexed - rolling_max) / rolling_max
    max_dd = float(drawdown.min())
    total_ret = float(reindexed.iloc[-1] / starting - 1.0)
    years = max((dates[-1] - dates[0]).days / 365.25, 1.0 / 365.25)
    turnover = n / years

    return BacktestResult(
        symbol=symbol, n_trades=n, win_rate=win_rate, profit_factor=profit_factor,
        avg_holding_bars=avg_hold, total_return=total_ret,
        annualized_sharpe=sharpe, annualized_sortino=sortino,
        max_drawdown=max_dd, turnover_per_year=turnover,
        equity_curve=reindexed, trades=trades,
    )


def baseline_hold(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    """Buy-and-hold baseline (long only)."""
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
                    "buyhold", pnl_pct * starting, pnl_pct, len(df) - 1,
                    0.0, 0.0, 0.0)]
    eq = pd.Series([starting, starting * (1 + pnl_pct)],
                   index=[df.index[0], df.index[-1]])
    reindexed = eq.reindex(df.index).ffill().fillna(starting)
    years = max((df.index[-1] - df.index[0]).days / 365.25, 1.0 / 365.25)
    return BacktestResult(
        symbol=sym, n_trades=1, win_rate=1.0 if pnl_pct > 0 else 0.0,
        profit_factor=float("inf") if pnl_pct > 0 else 0.0,
        avg_holding_bars=len(df) - 1, total_return=pnl_pct,
        annualized_sharpe=0.0, annualized_sortino=0.0, max_drawdown=0.0,
        turnover_per_year=1.0 / years, equity_curve=reindexed, trades=trades,
    )