"""Trend-dominant V2: 4h EMA trend + 1h pullback + 15m reclaim cascade.

Trend (DOMINANT, ~65% alpha weight)
-----------------------------------
* 4h EMA(50) with positive slope defines trend direction.
* 1h EMA(20) is the pullback reference.

Cross-axis (multi-TF momentum cascade, ~35% alpha weight)
---------------------------------------------------------
* 1h pullback condition: close < EMA20(1h) AND |close - EMA50(1h)| / ATR(1h) < 2.0
  (price is correcting but still close to the slow average — a healthy
  pullback, not a breakdown).
* 15m reclaim entry: close > previous 4-bar 15m high (break of micro structure).

Direction
---------
Long only. V2 is a long-only trend pullback strategy; shorting the
cascade is asymmetric and adds noise (cycle-46 lesson: trend filters
destroy carry on the short side).

Sizing: 1% risk per trade, ATR-based stop, asymmetric RR 1:2.

Exits (first triggered wins)
----------------------------
1. Stop: close < entry - 1.5 * ATR(15m) (long)
2. Target: close > entry + 3.0 * ATR(15m) (long) — RR 1:2
3. Ratcheting trailing: close < highest_since_entry - 2.0 * ATR(15m)
4. 4h trend reversal: 4h EMA50 slope flips negative
5. Time stop: bars_held > 96 (24 hours of 15m)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def annotate(df_15m: pd.DataFrame, df_1h: pd.DataFrame, df_4h: pd.DataFrame,
             cfg: dict) -> pd.DataFrame:
    sig = cfg["signal"]
    ema_p = sig["trend_ema_period"]
    pb_fast = sig["pullback_ema_fast"]
    pb_atr_p = sig["pullback_atr_period"]
    pb_prox = sig["pullback_proximity_atr"]
    reclaim_lb = sig["reclaim_lookback"]
    vol_p = sig["vol_period"]
    vol_min = sig["vol_ratio_min"]
    atr_p = 14

    out = df_15m.copy()
    out["atr14_15m"] = wilder_atr(out, atr_p)
    out["vol_ma_15m"] = out["volume"].rolling(vol_p, min_periods=vol_p).mean()
    out["vol_ratio_15m"] = out["volume"] / out["vol_ma_15m"]

    # Reclaim trigger: 15m close > max(high) of previous ``reclaim_lb`` bars.
    out["hh_reclaim"] = out["high"].rolling(reclaim_lb, min_periods=reclaim_lb).max().shift(1)

    # 4h trend filter.
    ema50_4h_raw = ema(df_4h["close"], ema_p)
    ema50_4h = ema50_4h_raw.shift(1)
    ema_slope = (ema50_4h - ema50_4h.shift(1)) / ema50_4h.shift(1)
    for s, name in [(ema50_4h, "ema50_4h"), (ema_slope, "ema50_4h_slope")]:
        s.name = name
        out = out.join(s.reindex(out.index, method="ffill"))
    out["trend_long_4h"] = (out["ema50_4h_slope"] > sig["trend_slope_min"])

    # 1h pullback reference.
    ema20_1h_raw = ema(df_1h["close"], pb_fast)
    ema20_1h = ema20_1h_raw.shift(1)
    ema50_1h_raw = ema(df_1h["close"], ema_p)
    ema50_1h = ema50_1h_raw.shift(1)
    atr1h_raw = wilder_atr(df_1h, pb_atr_p)
    atr1h = atr1h_raw.shift(1)
    for s, name in [(ema20_1h, "ema20_1h"), (ema50_1h, "ema50_1h"), (atr1h, "atr14_1h")]:
        s.name = name
        out = out.join(s.reindex(out.index, method="ffill"))

    out["pullback_under_ema20"] = out["close"] < out["ema20_1h"]
    out["pullback_proximity"] = (out["close"] - out["ema50_1h"]).abs() / out["atr14_1h"]
    out["pullback_ok"] = out["pullback_under_ema20"] & (out["pullback_proximity"] < pb_prox)

    have = (
        out["atr14_15m"].notna() & out["hh_reclaim"].notna()
        & out["ema50_4h_slope"].notna() & out["ema20_1h"].notna()
        & out["ema50_1h"].notna() & out["atr14_1h"].notna() & out["vol_ratio_15m"].notna()
    )
    vol_ok = out["vol_ratio_15m"] >= vol_min

    out["long_entry"] = (
        out["trend_long_4h"] & out["pullback_ok"]
        & (out["close"] > out["hh_reclaim"]) & vol_ok & have
    )
    out["entry_signal"] = out["long_entry"]
    return out


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    reason: str
    pnl_usd: float
    pnl_pct: float
    bars_held: int
    risk_per_trade: float
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


def _cost_per_side(cfg: dict) -> float:
    c = cfg["costs"]
    return (c["fee_bps_per_side"] + c["slippage_bps_per_side"]) / 10000.0


def _notional(equity: float, atr: float, price: float, cfg: dict) -> float:
    if atr <= 0 or price <= 0 or equity <= 0:
        return 0.0
    risk_pct = cfg["sizing"]["risk_per_trade"]
    stop_atr = cfg["exit"]["atr_stop"]
    risk_per_unit = (stop_atr * atr) / price
    if risk_per_unit <= 0:
        return 0.0
    raw = (risk_pct * equity) / risk_per_unit
    cap = cfg["sizing"]["max_notional_pct"] * equity
    return float(min(raw, cap))


def _exit_state(bar: pd.Series, direction: str, entry_price: float, atr: float,
                extreme: float, cfg: dict) -> Tuple[bool, str, float]:
    cur = float(bar["close"])
    ex = cfg["exit"]
    if direction == "long":
        if cur <= entry_price - ex["atr_stop"] * atr:
            return True, "stop", cur
        if cur >= entry_price + ex["atr_target"] * atr:
            return True, "target", cur
        if extreme > 0 and cur <= extreme - ex["atr_trailing"] * atr:
            return True, "trailing", cur
    slope = float(bar.get("ema50_4h_slope", 0.0))
    if direction == "long" and slope < 0.0:
        return True, "trend_reversal", cur
    return False, "", cur


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    cost = _cost_per_side(cfg)
    start_equity = float(cfg["sizing"]["starting_capital_usd"])
    sym = cfg.get("_symbol", "?")
    max_hold = int(cfg["exit"]["max_holding_bars"])

    equity = start_equity
    in_pos: Optional[str] = None
    entry_price = 0.0
    entry_idx = 0
    entry_date: Optional[pd.Timestamp] = None
    atr_at_entry = 0.0
    extreme = 0.0
    notional = 0.0

    trades: List[Trade] = []
    equity_path: List[Tuple[pd.Timestamp, float]] = []

    for i, (date, row) in enumerate(df.iterrows()):
        price = float(row["close"])
        if equity_path and equity_path[-1][0] == date:
            equity_path[-1] = (date, equity)
        else:
            equity_path.append((date, equity))

        if in_pos is None:
            long_sig = bool(row.get("long_entry", False))
            atr_now = float(row["atr14_15m"]) if not pd.isna(row.get("atr14_15m", np.nan)) else 0.0
            if atr_now <= 0:
                continue
            notional = _notional(equity, atr_now, price, cfg)
            if notional <= 0:
                continue
            if long_sig:
                entry_price = price * (1 + cost)
                in_pos = "long"
                atr_at_entry = atr_now
                entry_idx = i
                entry_date = date
                extreme = price
        else:
            if price > extreme:
                extreme = price
            if i - entry_idx >= max_hold:
                exit_now, reason, exit_raw = True, "time_stop", price
            else:
                exit_now, reason, exit_raw = _exit_state(row, in_pos, entry_price, atr_at_entry, extreme, cfg)
            if exit_now:
                exit_price_net = exit_raw * (1 - cost)
                pnl_pct = exit_price_net / entry_price - 1.0
                pnl_usd = pnl_pct * notional
                trades.append(Trade(
                    symbol=sym, direction=in_pos,
                    entry_date=entry_date, entry_price=entry_price,
                    exit_date=date, exit_price=exit_price_net, reason=reason,
                    pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                    bars_held=i - entry_idx,
                    risk_per_trade=float(cfg["sizing"]["risk_per_trade"]),
                    atr_at_entry=atr_at_entry,
                ))
                equity += pnl_usd
                equity_path[-1] = (date, equity)
                in_pos = None
                notional = 0.0
                extreme = 0.0

    if in_pos is not None:
        last = df.iloc[-1]
        lp = float(last["close"]) * (1 - cost)
        pnl_pct = lp / entry_price - 1.0
        pnl_usd = pnl_pct * notional
        trades.append(Trade(
            symbol=sym, direction=in_pos,
            entry_date=entry_date, entry_price=entry_price,
            exit_date=df.index[-1], exit_price=lp, reason="force_close",
            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
            bars_held=len(df) - 1 - entry_idx,
            risk_per_trade=float(cfg["sizing"]["risk_per_trade"]),
            atr_at_entry=atr_at_entry,
        ))
        equity += pnl_usd
        equity_path[-1] = (df.index[-1], equity)

    eq = pd.Series([v for _, v in equity_path], index=[d for d, _ in equity_path], name="equity")
    if eq.empty:
        eq = pd.Series([start_equity], index=[df.index[0]], name="equity")
    return _summarize(sym, trades, eq, df.index, start_equity)


def _summarize(sym: str, trades: List[Trade], equity: pd.Series,
               dates: pd.DatetimeIndex, start: float) -> BacktestResult:
    n = len(trades)
    if n == 0:
        return BacktestResult(
            symbol=sym, n_trades=0, win_rate=0.0, profit_factor=0.0,
            avg_holding_bars=0.0, total_return=0.0, annualized_sharpe=0.0,
            annualized_sortino=0.0, max_drawdown=0.0, turnover_per_year=0.0,
            equity_curve=pd.Series([start], index=[dates[0]]),
        )
    pnls = np.array([t.pnl_usd for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr = float(len(wins)) / n
    pf = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    avg_hold = float(np.mean([t.bars_held for t in trades]))
    eq = equity.reindex(dates).ffill().fillna(start)
    ret = eq.pct_change().fillna(0.0)
    bpy = 35040  # 15m bars per year
    if ret.std() == 0:
        sharpe = 0.0
        sortino = 0.0
    else:
        sharpe = float(ret.mean() / ret.std() * math.sqrt(bpy))
        down = ret[ret < 0]
        dstd = down.std() if len(down) > 0 else ret.std()
        sortino = float(ret.mean() / dstd * math.sqrt(bpy)) if dstd and dstd > 0 else 0.0
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    total = float(eq.iloc[-1] / start - 1.0)
    years = max((dates[-1] - dates[0]).days / 365.25, 1.0 / 365.25)
    turnover = n / years
    return BacktestResult(
        symbol=sym, n_trades=n, win_rate=wr, profit_factor=pf,
        avg_holding_bars=avg_hold, total_return=total,
        annualized_sharpe=sharpe, annualized_sortino=sortino,
        max_drawdown=dd, turnover_per_year=turnover,
        equity_curve=eq, trades=trades,
    )


def baseline_hold(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    start = float(cfg["sizing"]["starting_capital_usd"])
    cost = _cost_per_side(cfg)
    sym = cfg.get("_symbol", "?")
    if len(df) < 2:
        return BacktestResult(sym, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                              pd.Series([start], index=[df.index[0]]))
    first = float(df["close"].iloc[0]) * (1 + cost)
    last = float(df["close"].iloc[-1]) * (1 - cost)
    pnl = last / first - 1.0
    eq = pd.Series([start, start * (1 + pnl)], index=[df.index[0], df.index[-1]])
    reindexed = eq.reindex(df.index).ffill().fillna(start)
    return BacktestResult(
        symbol=sym, n_trades=1, win_rate=1.0 if pnl > 0 else 0.0,
        profit_factor=float("inf") if pnl > 0 else 0.0,
        avg_holding_bars=len(df) - 1, total_return=pnl,
        annualized_sharpe=0.0, annualized_sortino=0.0, max_drawdown=0.0,
        turnover_per_year=1.0, equity_curve=reindexed, trades=[],
    )