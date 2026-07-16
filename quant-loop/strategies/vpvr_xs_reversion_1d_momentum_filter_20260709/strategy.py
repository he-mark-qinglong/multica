"""VPVR cross-sectional 1d strategy: momentum filter + VPVR reversion entry.

This is a **portfolio-level** strategy. It operates on a panel of symbols
(BTCUSDT, ETHUSDT, SOLUSDT) simultaneously and uses cross-sectional ranking
to select which symbols to hold.

Indicators (per symbol, daily bars):

  * ``return_N``         — trailing N-day simple returns (N ∈ {30, 7, 3}).
  * ``momentum_score``   — weighted blend of the three returns.
  * ``vpvr_poc/val/vah`` — rolling 30-day 1d volume profile (per symbol).

Cross-sectional logic per rebalance day:

  1. Rank symbols by ``momentum_score``. Symbols in the bottom tertile
     (rank/momentum_score below the threshold) are *candidates* for long.
  2. Among the candidates, take a long position only if ``vpvr_z_dist < -threshold``,
     i.e. the symbol's close is below its value-area lower edge.
  3. Equal-weight across the selected symbols (per_signal_weight_pct per leg).

Vol-targeting exit (continuous, applied daily):

  For each held position, on every bar compute realized 30d vol (annualized).
  If vol > vol_target_annualized, reduce the position size proportionally so
  the marginal vol contribution equals the target.

Other exits (first triggered wins):
  * ``max_holding_days`` — force close after N days.
  * ``rebalance_stop_loss_pct`` — close at next bar if close < entry * (1 - pct).

Costs: 2 bps round-trip. Sizing: per_signal_weight_pct per leg, gross cap 100%.

The result is a single portfolio-level equity curve. Per-symbol trades are
still recorded for display-engine consumption.
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


def per_symbol_signals(df_1d: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df_1d.copy()
    mom = cfg["momentum"]
    df["return_30d"] = df["close"] / df["close"].shift(mom["lookback_30d"]) - 1.0
    df["return_7d"] = df["close"] / df["close"].shift(mom["lookback_7d"]) - 1.0
    df["return_3d"] = df["close"] / df["close"].shift(3) - 1.0
    df["momentum_score"] = (
        mom["w_30"] * df["return_30d"]
        + mom["w_7"] * df["return_7d"]
        + mom["w_3"] * df["return_3d"]
    )
    prof = rolling_volume_profile(df, cfg["vpvr"]["window_days"], cfg["vpvr"]["n_bins"], cfg["vpvr"]["value_area_pct"])
    df = pd.concat([df, prof], axis=1)
    half_range = ((df["vpvr_vah"] - df["vpvr_val"]) / 2.0).replace(0.0, np.nan)
    df["vpvr_z_dist"] = (df["close"] - df["vpvr_poc"]) / half_range
    df["realized_vol_30d"] = df["return_3d"].rolling(30, min_periods=10).std() * math.sqrt(252)
    return df


@dataclass
class Trade:
    symbol: str; direction: str
    entry_date: pd.Timestamp; entry_price: float
    exit_date: pd.Timestamp; exit_price: float
    reason: str; pnl: float; pnl_pct: float
    bars_held: int


@dataclass
class PortfolioPosition:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    weight: float  # fraction of equity
    scaled_weight: float  # current weight after vol-target scaling


@dataclass
class BacktestResult:
    n_trades: int
    win_rate: float
    profit_factor: float
    total_return: float
    annualized_sharpe: float
    annualized_sortino: float
    max_drawdown: float
    turnover_per_year: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)
    per_symbol: Dict[str, dict] = field(default_factory=dict)


def run_backtest(per_symbol_dfs: Dict[str, pd.DataFrame], cfg: dict) -> BacktestResult:
    """Portfolio backtest.

    We iterate over the union of dates. At each date we rebalance: close any
    positions that hit an exit condition, then open new positions according to
    the cross-sectional momentum + VPVR filter.
    """
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    starting = cfg["starting_capital_usd"]
    per_signal = cfg["sizing"]["per_signal_weight_pct"]
    max_gross = cfg["sizing"]["max_gross_exposure_pct"]
    threshold = cfg["entry"]["momentum_rank_threshold"]
    vpvr_dist_min = cfg["entry"]["vpvr_distance_z_min"]
    vol_target = cfg["exit"]["vol_target_annualized"]
    max_hold = cfg["exit"]["max_holding_days"]
    stop_loss = cfg["exit"]["rebalance_stop_loss_pct"]

    sigs: Dict[str, pd.DataFrame] = {sym: per_symbol_signals(df, cfg) for sym, df in per_symbol_dfs.items()}
    if not sigs:
        # No symbols at all — return a flat equity curve.
        return BacktestResult(n_trades=0, win_rate=0.0, profit_factor=0.0,
                              total_return=0.0, annualized_sharpe=0.0,
                              annualized_sortino=0.0, max_drawdown=0.0,
                              turnover_per_year=0.0,
                              equity_curve=pd.Series([starting], dtype="float64",
                                                     index=pd.DatetimeIndex([pd.Timestamp.utcnow().normalize()])),
                              trades=[], per_symbol={})
    all_dates = sorted(set().union(*[df.index for df in sigs.values()]))

    equity = float(starting)
    positions: Dict[str, PortfolioPosition] = {}
    trades: List[Trade] = []
    equity_path: List[Tuple[pd.Timestamp, float]] = []

    last_seen: Dict[str, float] = {}  # last close per symbol, for mark-to-market

    for date in all_dates:
        # 1. Mark existing positions to market; check exits.
        mtm_pnl_pct = 0.0
        to_close: List[str] = []
        for sym, pos in positions.items():
            df = sigs[sym]
            if date not in df.index:
                continue
            row = df.loc[date]
            price = float(row["close"])
            last_seen[sym] = price
            pnl_pct = (price / pos.entry_price - 1.0)
            unreal_dollar = pnl_pct * pos.scaled_weight * equity
            mtm_pnl_pct += pnl_pct * pos.scaled_weight

            bars_held = (date - pos.entry_date).days
            exit_now = False
            reason = ""
            exit_price = price

            if bars_held >= max_hold:
                exit_now = True; reason = f"max_hold>={max_hold}d"
            elif price < pos.entry_price * (1 - stop_loss):
                exit_now = True; reason = f"stop_loss>={stop_loss:.0%}"
            elif pnl_pct >= 0.05:  # take-profit at 5% — explicit reversion target
                exit_now = True; reason = "take_profit>=5%"

            if exit_now:
                exit_price_net = exit_price * (1 - cost_per_side)
                pnl_pct_net = (exit_price_net / pos.entry_price - 1.0)
                pnl_abs = pnl_pct_net * pos.scaled_weight * equity
                trades.append(
                    Trade(symbol=sym, direction="long",
                          entry_date=pos.entry_date, entry_price=pos.entry_price,
                          exit_date=date, exit_price=exit_price_net,
                          reason=reason, pnl=pnl_abs, pnl_pct=pnl_pct_net,
                          bars_held=bars_held)
                )
                equity += pnl_abs
                to_close.append(sym)

        for sym in to_close:
            positions.pop(sym, None)

        # 2. Update equity_path with the realized-only equity (no MTM).
        equity_path.append((date, equity))

        # 3. Open new positions on the cross-sectional signal.
        rows_today: List[Tuple[str, float, float]] = []  # (symbol, momentum, vpvr_dist)
        for sym, df in sigs.items():
            if date not in df.index:
                continue
            row = df.loc[date]
            if pd.isna(row.get("momentum_score")) or pd.isna(row.get("vpvr_z_dist")):
                continue
            rows_today.append((sym, float(row["momentum_score"]), float(row["vpvr_z_dist"])))

        if rows_today:
            # Rank by momentum (low = laggard). Lower-tertile = candidates.
            sorted_by_mom = sorted(rows_today, key=lambda r: r[1])
            n = len(sorted_by_mom)
            cutoff = int(math.floor(n * threshold))
            candidates = sorted_by_mom[:cutoff]
            for sym, mom, vpvr_z in candidates:
                if sym in positions:
                    continue
                if vpvr_z >= -vpvr_dist_min:  # need to be below VAL
                    continue
                # Vol-target scaling: shrink weight by realized_vol / target.
                df = sigs[sym]
                row = df.loc[date]
                rvol = float(row.get("realized_vol_30d", 1.0))
                if not np.isfinite(rvol) or rvol <= 0:
                    rvol = vol_target
                scale = min(1.0, vol_target / max(rvol, 1e-9))
                scaled = per_signal * scale
                if sum(p.scaled_weight for p in positions.values()) + scaled > max_gross + 1e-9:
                    continue
                entry_price = float(row["close"]) * (1 + cost_per_side)
                positions[sym] = PortfolioPosition(
                    symbol=sym, entry_date=date, entry_price=entry_price,
                    weight=per_signal, scaled_weight=scaled,
                )

    eq = pd.Series([v for _, v in equity_path], index=[d for d, _ in equity_path], name="equity")
    if eq.empty:
        eq = pd.Series([starting], index=[all_dates[0]], name="equity")

    return _summarize(trades, eq, all_dates, cfg)


def _summarize(trades: List[Trade], equity: pd.Series, dates, cfg: dict) -> BacktestResult:
    starting = cfg["starting_capital_usd"]
    n = len(trades)
    if n == 0:
        return BacktestResult(n_trades=0, win_rate=0.0, profit_factor=0.0,
                              total_return=0.0, annualized_sharpe=0.0,
                              annualized_sortino=0.0, max_drawdown=0.0,
                              turnover_per_year=0.0,
                              equity_curve=pd.Series([starting], index=[dates[0]]),
                              trades=[], per_symbol={})
    pnls = np.array([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    win_rate = float(len(wins)) / n
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")

    eq = equity.copy()
    reindexed = eq.reindex(dates).ffill().fillna(starting)
    daily_ret = reindexed.pct_change().fillna(0.0)
    if daily_ret.std() == 0:
        sharpe = 0.0; sortino = 0.0
    else:
        annual_scale = math.sqrt(252)
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

    per_symbol: Dict[str, dict] = {}
    for t in trades:
        d = per_symbol.setdefault(t.symbol, {"n_trades": 0, "pnl_sum": 0.0, "wins": 0})
        d["n_trades"] += 1
        d["pnl_sum"] += t.pnl_pct
        if t.pnl_pct > 0:
            d["wins"] += 1
    for sym, d in per_symbol.items():
        d["win_rate"] = d["wins"] / d["n_trades"] if d["n_trades"] else 0.0
        d["avg_pnl_pct"] = d["pnl_sum"] / d["n_trades"] if d["n_trades"] else 0.0

    return BacktestResult(
        n_trades=n, win_rate=win_rate, profit_factor=profit_factor,
        total_return=total_ret, annualized_sharpe=sharpe, annualized_sortino=sortino,
        max_drawdown=max_dd, turnover_per_year=turnover,
        equity_curve=reindexed, trades=trades, per_symbol=per_symbol,
    )