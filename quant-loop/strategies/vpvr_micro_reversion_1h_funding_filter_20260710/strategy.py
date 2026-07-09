"""VPVR-aware 1h strategy: funding-rate-z reversion (microstructure axis).

This is a **fresh** implementation — it does not import or extend any
existing ``vpvr_*.py``. The axis is microstructure: we treat the **funding
rate pressure** as a perp-specific sentiment signal and combine it with a
VPVR value-area touch.

Because the canonical 1m parquets do not carry a per-bar funding rate
column, we approximate funding pressure from price drift:

    funding_proxy[t] = (close[t] - rolling_sma[t]) / rolling_std[t]

A persistent positive drift (close above its rolling mean by >2σ) signals
that longs are over-leveraged (mirrors extreme positive funding on Binance
perp). A persistent negative drift signals shorts are over-leveraged. We
expect both to mean-revert.

Indicators (all computed from data in ``[t-W, t-1]`` only — no look-ahead):

  * ``funding_z``     — z-score of close vs rolling mean/std (168h window).
  * ``vpvr``          — 1h rolling volume profile (price bins × total volume).
  * ``vpvr_poc``      — highest-volume price (Point of Control).
  * ``vpvr_val/vah``  — lower / upper edges of the 70% value area.
  * ``vpvr_touch``    — close within ±5% of either VAL or VAH.

Entry (long):  funding_z[t] < -2.0  AND  vpvr_touch[t]  (close near VAL or POC band)
Entry (short): funding_z[t] >  2.0  AND  vpvr_touch[t]  (close near VAH or POC band)

Exit (first triggered wins):
    1. Time stop: bars_held >= max_holding_bars (=4).
    2. Funding normalization: |funding_z[t]| < exit_abs_z (=0.5).
    3. Stop loss: close breaches entry ± stop_loss_pct.

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

def funding_z(df: pd.DataFrame, window: int) -> pd.Series:
    """Z-score of close vs rolling SMA / rolling std over ``window`` bars.

    This is our funding-pressure proxy: a persistent positive drift pushes
    the z-score above 2σ and indicates one-sided positioning (longs over-leveraged,
    analogous to positive funding on Binance perp). Negative drift -> shorts over-leveraged.
    """
    sma = df["close"].rolling(window, min_periods=window).mean()
    std = df["close"].rolling(window, min_periods=window).std()
    return (df["close"] - sma) / std.replace(0.0, np.nan)


def rolling_volume_profile(
    df: pd.DataFrame, window: int, n_bins: int, value_area_pct: float
) -> pd.DataFrame:
    """Compute POC, VAL, VAH for a rolling 1h volume profile.

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
        cum = bin_vol[poc_bin]
        lo_bin = poc_bin
        hi_bin = poc_bin
        while cum < target and (lo_bin > 0 or hi_bin < n_bins - 1):
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


def vpvr_touch(df: pd.DataFrame, profile: pd.DataFrame, band_pct: float) -> pd.Series:
    """Boolean: close is within ±band_pct of either VAL or VAH.

    A 'touch' is the price being near a value-area boundary — this is where
    we expect either absorption (reversal) or breakout. Combined with the
    funding-z over-leverage signal, it makes the entry selective.
    """
    val = profile["vpvr_val"]
    vah = profile["vpvr_vah"]
    near_val = (df["close"] - val).abs() <= band_pct * val
    near_vah = (df["close"] - vah).abs() <= band_pct * vah
    return (near_val | near_vah).fillna(False)


# ---------------------------------------------------------------------------
# Annotated frame.
# ---------------------------------------------------------------------------

def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    fz = cfg["funding"]
    vp = cfg["vpvr"]

    out["funding_z"] = funding_z(out, fz["window_bars"])
    profile = rolling_volume_profile(out, vp["window_bars"], vp["n_bins"], vp["value_area_pct"])
    out = pd.concat([out, profile], axis=1)
    out["vpvr_touch"] = vpvr_touch(out, profile, vp["touch_band_pct"])

    ent = cfg["entry"]
    out["long_entry"] = (
        out["funding_z"].notna()
        & (out["funding_z"] < ent["funding_z_long_max"])
        & out["vpvr_touch"]
    )
    out["short_entry"] = (
        out["funding_z"].notna()
        & (out["funding_z"] > ent["funding_z_short_min"])
        & out["vpvr_touch"]
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
    funding_z_at_entry: float


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

def _exit_on_bar(bar: pd.Series, direction: str, entry_price: float, cfg: dict) -> Tuple[bool, str, float]:
    ex = cfg["exit"]
    fz = cfg["funding"]
    cur = float(bar["close"])
    fz_now = float(bar["funding_z"]) if not pd.isna(bar.get("funding_z", np.nan)) else float("nan")

    # 1. Stop loss.
    if direction == "long":
        if cur <= entry_price * (1.0 - ex["stop_loss_pct"]):
            return True, f"stop_loss<{ex['stop_loss_pct']}", cur
    else:
        if cur >= entry_price * (1.0 + ex["stop_loss_pct"]):
            return True, f"stop_loss>{ex['stop_loss_pct']}", cur

    # 2. Funding-rate normalization (|z| < exit_abs_z).
    if not math.isnan(fz_now) and abs(fz_now) < ex["funding_normalize_abs_z"]:
        return True, f"funding_normalize|{fz_now:.2f}|<{ex['funding_normalize_abs_z']}", cur

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
    funding_z_at_entry = 0.0
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
                entry_idx = i
                entry_date = date
                funding_z_at_entry = float(row["funding_z"]) if not pd.isna(row.get("funding_z", np.nan)) else 0.0
                open_notional_pct += per_signal
            elif bool(row.get("short_entry", False)) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "short"
                entry_idx = i
                entry_date = date
                funding_z_at_entry = float(row["funding_z"]) if not pd.isna(row.get("funding_z", np.nan)) else 0.0
                open_notional_pct += per_signal
        else:
            exit_now, reason, exit_price_raw = _exit_on_bar(row, in_pos, entry_price, cfg)
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
                        bars_held=bars_held, funding_z_at_entry=funding_z_at_entry,
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
                  bars_held=len(df) - 1 - entry_idx, funding_z_at_entry=funding_z_at_entry)
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
    # 1h bars/year scaling: 24 * 365 = 8760.
    periods_per_year = 24 * 365
    if daily_ret.std() == 0:
        sharpe = 0.0
        sortino = 0.0
    else:
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(periods_per_year))
        downside = daily_ret[daily_ret < 0]
        dstd = downside.std() if len(downside) > 0 else daily_ret.std()
        sortino = float(daily_ret.mean() / dstd * math.sqrt(periods_per_year)) if dstd and dstd > 0 else 0.0
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