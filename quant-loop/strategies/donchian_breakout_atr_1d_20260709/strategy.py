"""Donchian breakout + ATR trailing trend-following strategy.

This is a **fresh** implementation. It does not import or extend anything
from ``vpvr_reversion_*``; the closest it shares with those strategies is the
file layout (``data_loader.py``, ``strategy.py``, ``run_backtest.py``,
``config.json``).

Design summary
---------------

For each daily bar ``t`` we compute, from data in ``[t-W, t-1]`` only:

- ``donchian_upper[t]`` = max(high[t-N+1 : t])        (N=20 by default)
- ``donchian_lower[t]`` = min(low[t-N+1  : t])
- ``atr[t]``            = Wilder ATR over ``atr_period`` bars
- ``atr_ma[t]``         = SMA(atr, ``atr_ma_period``)
- ``vol_ma[t]``         = SMA(volume, ``volume_ma_period``)
- ``adx[t]``            = Wilder ADX over ``adx_period`` bars

Long entry condition at bar ``t`` (all four must hold):

    close[t] > donchian_upper[t]
    atr[t]   > atr_ma_ratio_min * atr_ma[t]
    volume[t] > volume_ratio_min * vol_ma[t]
    adx[t]   > adx_min

Short entry is the mirror.

Sizing: 1% of current equity per signal, capped at 5% gross across open
positions. Per symbol at most 1 open position (long or short, never both).

Exits (first triggered wins):
    1. ATR trailing: close[t] crosses ``entry +/- k * atr[t]``
       (k = atr_trailing_k, default 3.0). Anchor is the *entry* level,
       not a ratcheting low/high — that ratchet variant is deferred to
       EPIC-D.
    2. Donchian opposite break: long closed when close < donchian_lower
       (short closed when close > donchian_upper).
    3. Time stop: position age > time_stop_bars AND unrealized pnl <
       time_stop_min_pnl_atr * atr[t] -> forced close at close[t].

Costs: ``fees_bps_per_side + slippage_bps_per_side`` applied at entry
and exit.
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
# Indicators — pure functions over an OHLCV frame.
# ---------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    """True range series. Assumes df has high, low, close and a sorted index."""
    prev_close = df["close"].shift(1)
    hi_lo = df["high"] - df["low"]
    hi_pc = (df["high"] - prev_close).abs()
    lo_pc = (df["low"] - prev_close).abs()
    tr = pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)
    return tr


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR. The first ``period`` bars are NaN because the smoothing
    window needs ``period`` bars to seed."""
    tr = true_range(df)
    # Wilder smoothing = EMA with alpha = 1/period.
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX.

    Returns the ADX series; returns the raw frame via ``wilder_dmi_adx_frame``
    if you also want DI+/DI- (not needed for our entry filter).
    """
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


def donchian_upper(df: pd.DataFrame, n: int) -> pd.Series:
    """Upper Donchian band — max of the prior ``n`` highs, shifted by 1 so
    the band at ``t`` is computed from bars ``[t-n, t-1]``."""
    return df["high"].rolling(n, min_periods=n).max().shift(1)


def donchian_lower(df: pd.DataFrame, n: int) -> pd.Series:
    return df["low"].rolling(n, min_periods=n).min().shift(1)


# ---------------------------------------------------------------------------
# Annotated frame — indicators + entry signals.
# ---------------------------------------------------------------------------

def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return ``df`` with indicator columns + boolean ``long_entry`` /
    ``short_entry`` series. Signals are pre-exit; the backtest loop decides
    whether to act on them (sizing/gross-cap)."""
    ind = cfg["indicators"]
    atr_p = ind["atr_period"]
    atr_ma_p = ind["atr_ma_period"]
    vol_ma_p = ind["volume_ma_period"]
    adx_p = ind["adx_period"]
    n = ind["donchian_n"]
    out = df.copy()
    out["atr"] = wilder_atr(out, atr_p)
    out["atr_ma"] = out["atr"].rolling(atr_ma_p, min_periods=atr_ma_p).mean()
    out["vol_ma"] = out["volume"].rolling(vol_ma_p, min_periods=vol_ma_p).mean()
    out["adx"] = wilder_adx(out, adx_p)
    out["donchian_upper"] = donchian_upper(out, n)
    out["donchian_lower"] = donchian_lower(out, n)

    vol_ok = out["volume"] > ind["volume_ratio_min"] * out["vol_ma"]
    atr_ok = out["atr"] > ind["atr_ma_ratio_min"] * out["atr_ma"]
    adx_ok = out["adx"] > ind["adx_min"]
    have_bands = out["donchian_upper"].notna() & out["donchian_lower"].notna()

    long_break = out["close"] > out["donchian_upper"]
    short_break = out["close"] < out["donchian_lower"]
    out["long_entry"] = long_break & vol_ok & atr_ok & adx_ok & have_bands
    out["short_entry"] = short_break & vol_ok & atr_ok & adx_ok & have_bands
    # Don't enter both at once; the loop enforces that anyway.
    out["entry_signal"] = out["long_entry"] | out["short_entry"]
    return out


# ---------------------------------------------------------------------------
# Backtest primitives.
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str  # "long" or "short"
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    reason: str
    pnl: float  # dollar PnL on the notional
    pnl_pct: float  # return on the entry price (after costs)
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


def _exit_on_bar(bar: pd.Series, direction: str, entry_price: float, atr_at_entry: float, cfg: dict) -> Tuple[bool, str, float]:
    """Evaluate exit rules on bar ``bar`` for an open position.

    Returns ``(exit_now, reason, exit_price)``. ``exit_price`` is the raw
    price; the caller applies the exit-side transaction cost.
    """
    exit_cfg = cfg["exit"]
    cur = float(bar["close"])
    atr_now = float(bar["atr"]) if not pd.isna(bar.get("atr", np.nan)) else atr_at_entry

    # 1. ATR trailing stop (anchor = entry)
    if direction == "long":
        if cur < entry_price - exit_cfg["atr_trailing_k"] * atr_now:
            return True, f"atr_trailing<entry-{exit_cfg['atr_trailing_k']}*ATR", cur
    else:  # short
        if cur > entry_price + exit_cfg["atr_trailing_k"] * atr_now:
            return True, f"atr_trailing>entry+{exit_cfg['atr_trailing_k']}*ATR", cur

    # 2. Opposite Donchian break
    if exit_cfg.get("use_opposite_donchian", True):
        if direction == "long" and not pd.isna(bar.get("donchian_lower", np.nan)):
            if cur < float(bar["donchian_lower"]):
                return True, "donchian_opposite_break", cur
        elif direction == "short" and not pd.isna(bar.get("donchian_upper", np.nan)):
            if cur > float(bar["donchian_upper"]):
                return True, "donchian_opposite_break", cur
    return False, "", cur


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    """Daily-bar trend backtest.

    Position sizing is 1% of current equity per signal, but the loop tracks a
    running gross-exposure cap of 5% across open positions so multiple
    correlated entries cannot stack beyond that.
    """
    df = annotate(df, cfg)
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    time_stop_bars = cfg["exit"]["time_stop_bars"]
    time_stop_pnl = cfg["exit"]["time_stop_min_pnl_atr"]
    per_signal = cfg["sizing"]["per_signal_weight_pct"]
    max_gross = cfg["sizing"]["max_gross_exposure_pct"]
    starting_equity = cfg["starting_capital_usd"]
    symbol = cfg.get("_symbol", "?")

    equity = starting_equity
    in_pos: Optional[str] = None  # None / "long" / "short"
    entry_price = 0.0
    entry_idx = 0
    entry_date: Optional[pd.Timestamp] = None
    atr_at_entry = 0.0
    open_notional_pct = 0.0  # gross exposure fraction

    trades: List[Trade] = []
    equity_path = []  # list of (date, equity)

    for i, (date, row) in enumerate(df.iterrows()):
        price = float(row["close"])

        # Record equity on every bar so the curve is dense. Unrealized PnL is
        # NOT compounded into equity for this bootstrap; realized PnL is the
        # source of truth so partial exits are visible in the equity path.
        # ``equity_path`` is keyed by date — if the same date ever appears
        # twice we overwrite the previous entry rather than producing a
        # duplicate-index series downstream.
        if equity_path and equity_path[-1][0] == date:
            equity_path[-1] = (date, equity)
        else:
            equity_path.append((date, equity))

        if in_pos is None:
            # Try to enter
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

            if not exit_now and bars_held >= time_stop_bars:
                # Time stop: only trigger if unrealized pnl < 1.5*ATR
                unreal = (price / entry_price - 1.0) * open_notional_pct * equity
                if in_pos == "short":
                    unreal = -unreal
                if unreal < time_stop_pnl * atr_at_entry * open_notional_pct * equity / max(open_notional_pct * equity, 1e-9):
                    exit_now = True
                    reason = f"time_stop>={time_stop_bars}d"

            if exit_now:
                exit_price_net = exit_price_raw * (1 - cost_per_side)
                pnl_pct = (exit_price_net / entry_price - 1.0) * (1 if in_pos == "long" else -1.0)
                pnl_abs = pnl_pct * open_notional_pct * equity
                trades.append(
                    Trade(
                        symbol=symbol,
                        direction=in_pos,
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=date,
                        exit_price=exit_price_net,
                        reason=reason,
                        pnl=pnl_abs,
                        pnl_pct=pnl_pct,
                        bars_held=bars_held,
                        atr_at_entry=atr_at_entry,
                    )
                )
                equity += pnl_abs
                # Record the post-exit equity on this bar (overwrites the
                # in-position entry above).
                equity_path[-1] = (date, equity)
                in_pos = None
                open_notional_pct = 0.0

    # Build equity curve (realized-only). If the final position was still open,
    # force-close it at the last close for the equity curve.
    if in_pos is not None:
        last = df.iloc[-1]
        lp = float(last["close"]) * (1 - cost_per_side)
        pnl_pct = (lp / entry_price - 1.0) * (1 if in_pos == "long" else -1.0)
        pnl_abs = pnl_pct * open_notional_pct * equity
        trades.append(
            Trade(
                symbol=symbol, direction=in_pos, entry_date=entry_date,
                entry_price=entry_price, exit_date=df.index[-1], exit_price=lp,
                reason="force_close_eod", pnl=pnl_abs, pnl_pct=pnl_pct,
                bars_held=len(df) - 1 - entry_idx, atr_at_entry=atr_at_entry,
            )
        )
        equity += pnl_abs
        if equity_path and equity_path[-1][0] == df.index[-1]:
            equity_path[-1] = (df.index[-1], equity)
        else:
            equity_path.append((df.index[-1], equity))

    eq = pd.Series([v for _, v in equity_path], index=[d for d, _ in equity_path], name="equity")
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
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(252))
        downside = daily_ret[daily_ret < 0]
        dstd = downside.std() if len(downside) > 0 else daily_ret.std()
        sortino = float(daily_ret.mean() / dstd * math.sqrt(252)) if dstd and dstd > 0 else 0.0
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
    """Buy on the first bar, sell on the last. Sanity check."""
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
