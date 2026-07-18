"""vpvr_volume_edge_3tf_v1_20260711 — VPVR Volume Edge, 3-TF cascade.

Trend (4h, ~30% alpha weight)
-----------------------------
* 4h EMA(50) slope filter defines direction.

VPVR filter (15m, ~70% alpha weight — DOMINANT)
-----------------------------------------------
* Build a price × volume histogram over a 96-bar 15m lookback (50 bins).
* POC = price bin with the highest traded volume.
* Value area = bins around POC covering ``value_area_pct`` of volume.
* Long filter: 15m close > POC and close within value area upper half
  (acceptance above POC).
* Short filter: 15m close < POC and close within value area lower half
  (acceptance below POC).

Volume edge entry (1m)
----------------------
* 1m volume > vol_ratio_min × MA(vol_ma_period_1m).
* Long entry: trend-up AND 15m long-filter AND 1m vol-spike on bar.
* Short entry: trend-down AND 15m short-filter AND 1m vol-spike on bar.

Exits (first triggered wins, ATR-anchored)
------------------------------------------
1. Stop: close against direction by ``atr_stop`` × ATR(1m).
2. Target: close with direction by ``atr_target`` × ATR(1m).
3. Trailing: close against highest/lowest since entry by ``atr_trailing``.
4. Regime flip: 4h EMA50 slope flips against direction.
5. Time stop: bars_held > ``max_holding_bars_1m``.

Long+short.
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


def vpvr_poc_value_area(
    closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, volumes: np.ndarray,
    n_bins: int = 50, value_area_pct: float = 0.7,
) -> Tuple[float, float, float]:
    """Approximate VPVR POC and value-area boundaries from OHLCV bars.

    Each bar contributes its volume uniformly to bins it spans between
    low and high. POC = bin with max volume. Value area = contiguous bins
    around POC covering ``value_area_pct`` of total volume.
    """
    if len(closes) == 0 or volumes.sum() <= 0:
        return float("nan"), float("nan"), float("nan")
    pmin = float(np.nanmin(lows))
    pmax = float(np.nanmax(highs))
    if pmax <= pmin:
        return float("nan"), float("nan"), float("nan")
    edges = np.linspace(pmin, pmax, n_bins + 1)
    bin_vol = np.zeros(n_bins, dtype=float)
    for i in range(len(closes)):
        c, h, l, v = closes[i], highs[i], lows[i], volumes[i]
        if not np.isfinite(c) or not np.isfinite(h) or not np.isfinite(l) or v <= 0:
            continue
        lo_bin = max(int(np.floor((l - pmin) / (pmax - pmin) * n_bins)), 0)
        hi_bin = min(int(np.floor((h - pmin) / (pmax - pmin) * n_bins)), n_bins - 1)
        if hi_bin < lo_bin:
            hi_bin = lo_bin
        n_spanned = hi_bin - lo_bin + 1
        if n_spanned <= 0:
            continue
        per = v / n_spanned
        bin_vol[lo_bin:hi_bin + 1] += per
    if bin_vol.sum() <= 0:
        return float("nan"), float("nan"), float("nan")
    poc_bin = int(np.argmax(bin_vol))
    poc_price = float((edges[poc_bin] + edges[poc_bin + 1]) / 2.0)
    target = value_area_pct * bin_vol.sum()
    cum = bin_vol[poc_bin]
    lo = poc_bin
    hi = poc_bin
    while cum < target and (lo > 0 or hi < n_bins - 1):
        left = bin_vol[lo - 1] if lo > 0 else -1.0
        right = bin_vol[hi + 1] if hi < n_bins - 1 else -1.0
        if right >= left:
            hi += 1
            cum += bin_vol[hi]
        else:
            lo -= 1
            cum += bin_vol[lo]
    val_lo = float(edges[lo])
    val_hi = float(edges[hi + 1])
    return poc_price, val_lo, val_hi


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


def annotate(df_1m: pd.DataFrame, df_15m: pd.DataFrame, df_4h: pd.DataFrame,
             cfg: dict) -> pd.DataFrame:
    sig = cfg["signal"]
    ema_p = sig["trend_ema_period_4h"]
    slope_min = sig["trend_slope_min"]
    vol_ma_p = sig["vol_ma_period_1m"]
    vol_ratio_min = sig["vol_spike_ratio_min"]
    poc_lb_15m = sig["poc_lookback_15m"]
    n_bins = sig["vol_bins_15m"]
    vap = sig["value_area_pct"]

    out = df_1m.copy()
    out["atr14_1m"] = wilder_atr(out, 14)
    out["vol_ma_1m"] = out["volume"].rolling(vol_ma_p, min_periods=vol_ma_p).mean()
    out["vol_ratio_1m"] = out["volume"] / out["vol_ma_1m"]

    # 4h trend filter — EMA50 + slope.
    ema50_4h_raw = ema(df_4h["close"], ema_p)
    ema50_4h = ema50_4h_raw.shift(1)
    slope = (ema50_4h - ema50_4h.shift(1)) / ema50_4h.shift(1)
    for s, name in [(ema50_4h, "ema50_4h"), (slope, "ema50_4h_slope")]:
        s.name = name
        out = out.join(s.reindex(out.index, method="ffill"))
    out["trend_long_4h"] = out["ema50_4h_slope"] > slope_min
    out["trend_short_4h"] = out["ema50_4h_slope"] < -slope_min if slope_min != 0 else out["ema50_4h_slope"] < 0.0

    # 15m VPVR filter — rolling POC + value area, evaluated bar-by-bar.
    closes = df_15m["close"].to_numpy(dtype=float)
    highs = df_15m["high"].to_numpy(dtype=float)
    lows = df_15m["low"].to_numpy(dtype=float)
    vols = df_15m["volume"].to_numpy(dtype=float)
    poc_arr = np.full(len(df_15m), np.nan)
    vl_arr = np.full(len(df_15m), np.nan)
    vh_arr = np.full(len(df_15m), np.nan)
    for i in range(poc_lb_15m, len(df_15m) + 1):
        j = i - 1
        lo = max(0, i - poc_lb_15m)
        poc, vl, vh = vpvr_poc_value_area(
            closes[lo:i], highs[lo:i], lows[lo:i], vols[lo:i],
            n_bins=n_bins, value_area_pct=vap,
        )
        poc_arr[j] = poc
        vl_arr[j] = vl
        vh_arr[j] = vh
    poc_s = pd.Series(poc_arr, index=df_15m.index, name="poc_15m").shift(1)
    vl_s = pd.Series(vl_arr, index=df_15m.index, name="val_lo_15m").shift(1)
    vh_s = pd.Series(vh_arr, index=df_15m.index, name="val_hi_15m").shift(1)
    for s in (poc_s, vl_s, vh_s):
        out = out.join(s.reindex(out.index, method="ffill"))

    span = (out["val_hi_15m"] - out["val_lo_15m"]).abs()
    out["val_span_15m"] = span
    # Within value area upper half (above mid) and close > POC.
    val_mid = (out["val_hi_15m"] + out["val_lo_15m"]) / 2.0
    span_valid = span.fillna(0.0) > 0
    poc_valid = out["poc_15m"].notna()
    vl_valid = out["val_lo_15m"].notna()
    vh_valid = out["val_hi_15m"].notna()
    out["long_vpvr_ok"] = (
        (out["close"] > out["poc_15m"])
        & (out["close"] >= val_mid)
        & (out["close"] <= out["val_hi_15m"])
        & span_valid & poc_valid & vl_valid & vh_valid
    )
    out["short_vpvr_ok"] = (
        (out["close"] < out["poc_15m"])
        & (out["close"] <= val_mid)
        & (out["close"] >= out["val_lo_15m"])
        & span_valid & poc_valid & vl_valid & vh_valid
    )

    have = (
        out["atr14_1m"].notna() & out["vol_ratio_1m"].notna()
        & out["ema50_4h_slope"].notna() & out["poc_15m"].notna()
    )
    vol_ok = out["vol_ratio_1m"] >= vol_ratio_min

    out["long_entry"] = (
        out["trend_long_4h"] & out["long_vpvr_ok"] & vol_ok & have
    )
    out["short_entry"] = (
        out["trend_short_4h"] & out["short_vpvr_ok"] & vol_ok & have
    )
    out["entry_signal"] = out["long_entry"] | out["short_entry"]
    return out


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
    else:  # short
        if cur >= entry_price + ex["atr_stop"] * atr:
            return True, "stop", cur
        if cur <= entry_price - ex["atr_target"] * atr:
            return True, "target", cur
        if extreme > 0 and cur >= extreme + ex["atr_trailing"] * atr:
            return True, "trailing", cur
    slope = float(bar.get("ema50_4h_slope", 0.0))
    if direction == "long" and slope < 0.0:
        return True, "trend_reversal", cur
    if direction == "short" and slope > 0.0:
        return True, "trend_reversal", cur
    return False, "", cur


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    cost = _cost_per_side(cfg)
    start_equity = float(cfg["sizing"]["starting_capital_usd"])
    sym = cfg.get("_symbol", "?")
    max_hold = int(cfg["exit"]["max_holding_bars_1m"])

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
            atr_now = float(row["atr14_1m"]) if not pd.isna(row.get("atr14_1m", np.nan)) else 0.0
            if atr_now <= 0:
                continue
            notional = _notional(equity, atr_now, price, cfg)
            if notional <= 0:
                continue
            long_sig = bool(row.get("long_entry", False))
            short_sig = bool(row.get("short_entry", False))
            if long_sig:
                entry_price = price * (1 + cost)
                in_pos = "long"
                atr_at_entry = atr_now
                entry_idx = i
                entry_date = date
                extreme = price
            elif short_sig:
                entry_price = price * (1 - cost)
                in_pos = "short"
                atr_at_entry = atr_now
                entry_idx = i
                entry_date = date
                extreme = price
        else:
            if in_pos == "long" and price > extreme:
                extreme = price
            elif in_pos == "short" and (extreme == 0.0 or price < extreme):
                extreme = price
            if i - entry_idx >= max_hold:
                exit_now, reason, exit_raw = True, "time_stop", price
            else:
                exit_now, reason, exit_raw = _exit_state(row, in_pos, entry_price, atr_at_entry, extreme, cfg)
            if exit_now:
                if in_pos == "long":
                    exit_price_net = exit_raw * (1 - cost)
                else:
                    exit_price_net = exit_raw * (1 + cost)
                pnl_pct = (exit_price_net / entry_price - 1.0) if in_pos == "long" else (entry_price / exit_price_net - 1.0)
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
        lp = float(last["close"])
        if in_pos == "long":
            exit_price_net = lp * (1 - cost)
        else:
            exit_price_net = lp * (1 + cost)
        pnl_pct = (exit_price_net / entry_price - 1.0) if in_pos == "long" else (entry_price / exit_price_net - 1.0)
        pnl_usd = pnl_pct * notional
        trades.append(Trade(
            symbol=sym, direction=in_pos,
            entry_date=entry_date, entry_price=entry_price,
            exit_date=df.index[-1], exit_price=exit_price_net, reason="force_close",
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
    bpy = 525600  # 1m bars per year
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
