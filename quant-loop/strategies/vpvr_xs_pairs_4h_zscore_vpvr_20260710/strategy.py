"""V3 strategy: xs-pair z-score + VPVR confluence (vpvr_xs_pairs_4h_zscore_vpvr_20260710).

Axis = cross-asset pair z-score with VPVR level filter.

Pair data:
    BTCUSDT / ETHUSDT — BTC and ETH are loaded as 1h parquets and resampled
    to 4h on the fly (no native 4h parquet exists in this workspace).
    BTCUSDT / SOLUSDT — SOL uses the native 4h parquet directly.
    ETHUSDT / SOLUSDT — likewise, ETH from 1h resample, SOL native 4h.

For each pair (A, B):
  z_t = (log(A_t/B_t) - rolling_mean(log(A/B))) / rolling_std(log(A/B))
        over ``zscore_lookback_bars``.

Entry:
  - When z_t >= +zscore_entry_threshold → pair is rich on A vs B → SHORT A, LONG B.
  - When z_t <= -zscore_entry_threshold → SHORT B, LONG A (symmetric).
  - Confluence: A's close must lie within ``proximity_atr_k * ATR(14)`` of
    A's rolling VPVR POC over the most recent ``vpvr_window_bars``.

Exit:
  - z-score reverts to within ±zscore_exit_threshold (mean-reversion captured).
  - z-score breaks the regime-switch threshold in the same direction as entry
    (pair divergence accelerating) → stop loss.
  - Time stop = max_holding_bars.

Returns
-------
A dict result with per-pair trades, equity, bar_return, etc.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

VARIANT_KEY = "A"
TIMEFRAME_TAG = "4h"


@dataclass
class Trade:
    pair: str
    direction: str  # "short_a_long_b" or "long_a_short_b"
    entry_ts: pd.Timestamp
    entry_price_a: float
    entry_price_b: float
    exit_ts: pd.Timestamp | None
    exit_price_a: float | None
    exit_price_b: float | None
    pnl_pct: float
    bars_held: int
    z_at_entry: float
    z_at_exit: float | None
    exit_reason: str


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - prev_close).abs()
    l_pc = (df["low"] - prev_close).abs()
    return pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = true_range(df).astype(float)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def resample_ohlcv(df_1h: pd.DataFrame, rule: str = "4h") -> pd.DataFrame:
    """Resample 1h OHLCV to multi-hour bars."""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    return df_1h.resample(rule).agg(agg).dropna(subset=["open"])


def _rolling_vpvr(close: np.ndarray, volume: np.ndarray, window: int, n_bins: int) -> dict:
    """Rolling VPVR POC computed strictly from past bars (no look-ahead)."""
    n = len(close)
    poc = np.full(n, np.nan)
    for i in range(window, n):
        sub_c = close[i - window: i]
        sub_v = volume[i - window: i]
        p_lo = float(np.nanmin(sub_c))
        p_hi = float(np.nanmax(sub_c))
        if not np.isfinite(p_lo) or not np.isfinite(p_hi) or p_hi <= p_lo:
            continue
        bins = np.linspace(p_lo, p_hi, n_bins + 1)
        idx = np.clip(np.digitize(sub_c, bins) - 1, 0, n_bins - 1)
        bin_v = np.zeros(n_bins)
        for k in range(n_bins):
            mask = idx == k
            bin_v[k] = sub_v[mask].sum() if mask.any() else 0.0
        poc[i] = float((bins[int(np.argmax(bin_v))] + bins[int(np.argmax(bin_v)) + 1]) / 2.0)
    return {"poc": poc}


def pair_zscore(close_a: pd.Series, close_b: pd.Series, lookback: int) -> pd.Series:
    log_ratio = np.log(close_a.astype(float)) - np.log(close_b.astype(float))
    mu = log_ratio.rolling(lookback, min_periods=lookback).mean()
    sd = log_ratio.rolling(lookback, min_periods=lookback).std(ddof=0)
    return ((log_ratio - mu) / sd.replace(0.0, np.nan)).rename("z")


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def _annualisation_factor(timeframe: str) -> float:
    tf = timeframe.strip().lower()
    if tf.endswith("m"):
        minutes = int(tf[:-1])
        return math.sqrt(60 * 24 * 365 / minutes)
    if tf.endswith("h"):
        hours = int(tf[:-1])
        return math.sqrt(24 * 365 / hours)
    if tf.endswith("d"):
        days = int(tf[:-1])
        return math.sqrt(365 / days)
    raise ValueError(tf)


def _trade_to_dict(t: Trade) -> dict:
    return {
        "variant": VARIANT_KEY,
        "pair": t.pair,
        "direction": t.direction,
        "entry_ts": t.entry_ts.isoformat() if t.entry_ts is not None else None,
        "entry_price_a": t.entry_price_a,
        "entry_price_b": t.entry_price_b,
        "exit_ts": t.exit_ts.isoformat() if t.exit_ts is not None else None,
        "exit_price_a": t.exit_price_a,
        "exit_price_b": t.exit_price_b,
        "pnl_pct": t.pnl_pct,
        "bars_held": t.bars_held,
        "z_at_entry": t.z_at_entry,
        "z_at_exit": t.z_at_exit,
        "exit_reason": t.exit_reason,
    }


def run_pair_backtest(df_a: pd.DataFrame, df_b: pd.DataFrame, cfg: dict, pair_label: str) -> dict:
    ind = cfg["indicators"]
    common = df_a.index.intersection(df_b.index)
    a = df_a.loc[common]
    b = df_b.loc[common]
    if len(common) < int(ind["zscore_lookback_bars"]) + 10:
        raise SystemExit(f"{pair_label}: insufficient overlapping bars after resample ({len(common)})")
    z = pair_zscore(a["close"], b["close"], int(ind["zscore_lookback_bars"]))

    a_atr = wilder_atr(a, int(ind["atr_period"]))
    profile = _rolling_vpvr(a["close"].to_numpy(), a["volume"].to_numpy(),
                            int(ind["vpvr_window_bars"]), int(ind["vpvr_n_bins"]))
    poc = pd.Series(profile["poc"], index=a.index)
    proximity = float(ind["vpvr_proximity_atr_k"]) * a_atr
    near_poc = (a["close"] - poc).abs() <= proximity

    entry_thr = float(ind["zscore_entry_threshold"])
    exit_thr = float(cfg["exit"]["zscore_exit_threshold"])
    regime_thr = float(cfg["exit"]["regime_switch_zscore_threshold"])
    max_holding = int(cfg["exit"]["max_holding_bars"])

    trade_log = []
    pos = 0
    bars_held = 0
    entry_idx = None
    entry_z = None
    entry_a = None
    entry_b = None
    n = len(common)
    pnl_pct_per_bar = np.zeros(n)
    for i in range(1, n):
        cur_pos = pos
        zi = float(z.iat[i]) if np.isfinite(z.iat[i]) else None
        if cur_pos == 0 and zi is not None and cfg["entry"].get("require_vpvr_confluence", True) and not bool(near_poc.iat[i]):
            cur_pos = 0
        if cur_pos == 0 and zi is not None:
            if zi >= entry_thr:
                cur_pos = -1
            elif zi <= -entry_thr:
                cur_pos = +1
        if cur_pos != 0 and pos == 0:
            entry_idx = i
            entry_z = zi
            entry_a = float(a["close"].iat[i])
            entry_b = float(b["close"].iat[i])
            bars_held = 1
            pos = cur_pos
        elif pos != 0:
            bars_held += 1
            a_ret = float(a["close"].iat[i]) / float(a["close"].iat[i - 1]) - 1.0
            b_ret = float(b["close"].iat[i]) / float(b["close"].iat[i - 1]) - 1.0
            pnl_pct_per_bar[i] = pos * (a_ret - b_ret) / 2.0
            exit_reason = None
            if (cfg["entry"].get("require_vpvr_confluence", True)
                    and not bool(near_poc.iat[i])):
                exit_reason = "confluence_lost"
            if abs(zi) <= exit_thr:
                exit_reason = "z_mean_revert"
            elif (pos == +1 and zi <= -regime_thr) or (pos == -1 and zi >= +regime_thr):
                exit_reason = "regime_break"
            elif bars_held >= max_holding:
                exit_reason = "max_holding"
            if exit_reason is not None:
                exit_a = float(a["close"].iat[i])
                exit_b = float(b["close"].iat[i])
                if pos == +1:
                    pct = (exit_a / entry_a - 1.0) - (exit_b / entry_b - 1.0)
                else:
                    pct = -(exit_a / entry_a - 1.0) + (exit_b / entry_b - 1.0)
                cost = 2.0 * 2.0 * (float(cfg["fees_bps_per_side"]) + float(cfg["slippage_bps_per_side"])) / 10_000.0
                net = pct - cost
                trade_log.append(Trade(
                    pair=pair_label,
                    direction="long_a_short_b" if pos == +1 else "short_a_long_b",
                    entry_ts=a.index[entry_idx],
                    entry_price_a=entry_a,
                    entry_price_b=entry_b,
                    exit_ts=a.index[i],
                    exit_price_a=exit_a,
                    exit_price_b=exit_b,
                    pnl_pct=net,
                    bars_held=bars_held,
                    z_at_entry=entry_z,
                    z_at_exit=zi,
                    exit_reason=exit_reason,
                ))
                pos = 0
                bars_held = 0
                entry_idx = None
                entry_z = None
                entry_a = None
                entry_b = None
        elif pos != 0 and cur_pos == 0:
            exit_a = float(a["close"].iat[i])
            exit_b = float(b["close"].iat[i])
            if pos == +1:
                pct = (exit_a / entry_a - 1.0) - (exit_b / entry_b - 1.0)
            else:
                pct = -(exit_a / entry_a - 1.0) + (exit_b / entry_b - 1.0)
            cost = 2.0 * 2.0 * (float(cfg["fees_bps_per_side"]) + float(cfg["slippage_bps_per_side"])) / 10_000.0
            net = pct - cost
            trade_log.append(Trade(
                pair=pair_label,
                direction="long_a_short_b" if pos == +1 else "short_a_long_b",
                entry_ts=a.index[entry_idx],
                entry_price_a=entry_a,
                entry_price_b=entry_b,
                exit_ts=a.index[i],
                exit_price_a=exit_a,
                exit_price_b=exit_b,
                pnl_pct=net,
                bars_held=bars_held,
                z_at_entry=entry_z,
                z_at_exit=zi,
                exit_reason="confluence_lost",
            ))
            pos = 0
            bars_held = 0
            entry_idx = None
            entry_z = None
            entry_a = None
            entry_b = None

    starting = float(cfg["starting_capital_usd"])
    if not trade_log:
        return {
            "pair": pair_label,
            "trades": [],
            "equity": np.full(n, starting),
            "bar_return": pnl_pct_per_bar,
            "n_trades": 0,
            "n_bars": n,
            "span_start": common[0].date().isoformat() if n else None,
            "span_end": common[-1].date().isoformat() if n else None,
        }

    bar_r = pnl_pct_per_bar.copy()
    equity = np.empty(n)
    equity[0] = starting
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + bar_r[i])
    return {
        "pair": pair_label,
        "trades": [_trade_to_dict(t) for t in trade_log],
        "equity": equity,
        "bar_return": bar_r,
        "n_trades": len(trade_log),
        "n_bars": n,
        "span_start": common[0].date().isoformat(),
        "span_end": common[-1].date().isoformat(),
    }


def run_backtest(data: dict, cfg: dict) -> dict:
    """Aggregate pair-level backtests into a portfolio dict."""
    pairs = list(cfg.get("pairs", []))
    results = []
    for pair in pairs:
        a_sym, b_sym = pair.split("/")
        if a_sym not in data or b_sym not in data:
            raise SystemExit(f"missing data for pair {pair}")
        res = run_pair_backtest(data[a_sym], data[b_sym], cfg, pair)
        results.append(res)
    return {"per_pair": results}
