"""V72 strategy (xs-basis z-score + VPVR confluence + funding filter, iter #72).

Axis = cross-asset BTC/ETH basis z-score mean reversion with VPVR level
filter AND a funding-rate regime filter that skips entries when perp basis
is destabilised.

Pair:
  BTCUSDT / ETHUSDT — both native 15m parquets with funding_rate column.

Funding filter:
  - funding_rate is supplied per 15m bar (forward-filled from 8h events).
  - Entry is permitted only when |funding_rate| < threshold (default 0.0005)
    on the BTC leg. Blowoffs (|funding| > 0.0005) are skipped because the
    perp basis destabilises and the pair z-score mean-reverts slower.

VPVR confluence:
  - BTC's close must lie within proximity_atr_k * ATR(14) of BTC's
    rolling VPVR POC over the most recent vpvr_window_bars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

VARIANT_KEY = "A"
TIMEFRAME_TAG = "15m"


@dataclass
class Trade:
    pair: str
    direction: str
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
    funding_at_entry: float | None
    exit_reason: str


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def true_range(df):
    prev_close = df["close"].shift(1)
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - prev_close).abs()
    l_pc = (df["low"] - prev_close).abs()
    return pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)


def wilder_atr(df, period):
    tr = true_range(df).astype(float)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rolling_vpvr(close, volume, window, n_bins):
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


def pair_zscore(close_a, close_b, lookback):
    log_ratio = np.log(close_a.astype(float)) - np.log(close_b.astype(float))
    mu = log_ratio.rolling(lookback, min_periods=lookback).mean()
    sd = log_ratio.rolling(lookback, min_periods=lookback).std(ddof=0)
    return ((log_ratio - mu) / sd.replace(0.0, np.nan)).rename("z")


def funding_filter_mask(bar_index, funding_series, threshold):
    """Build a per-bar boolean mask that is True when |funding_rate| < threshold.

    funding_series: pd.Series indexed by bar ts, value = funding rate (already
    forward-filled from 8h events onto the 15m grid).
    threshold: absolute funding_rate ceiling for entry.

    Returns pd.Series of bool aligned to ``bar_index``.
    """
    if funding_series is None or len(funding_series) == 0:
        return pd.Series(True, index=bar_index)
    fs = funding_series.copy()
    if fs.index.tz is not None:
        fs.index = fs.index.tz_convert(None)
    aligned = fs.reindex(bar_index, method="ffill").fillna(0.0)
    return (aligned.abs() < float(threshold)).rename("funding_allow")


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def _annualisation_factor(timeframe):
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


def _trade_to_dict(t):
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
        "funding_at_entry": t.funding_at_entry,
        "exit_reason": t.exit_reason,
    }


def run_pair_backtest(df_a, df_b, cfg, pair_label, funding_a=None, funding_b=None):
    ind = cfg["indicators"]
    common = df_a.index.intersection(df_b.index)
    a = df_a.loc[common]
    b = df_b.loc[common]
    if len(common) < int(ind["zscore_lookback_bars"]) + 10:
        raise SystemExit(pair_label + ": insufficient overlapping bars after resample (" + str(len(common)) + ")")
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
    require_funding_filter = bool(cfg["entry"].get("require_funding_filter", True))

    if require_funding_filter:
        allow_a = funding_filter_mask(common, funding_a, float(ind["funding_filter_threshold"]))
        allow_b = funding_filter_mask(common, funding_b, float(ind["funding_filter_threshold"]))
        funding_allow = (allow_a & allow_b).reindex(common).fillna(True)
        funding_value = allow_a.reindex(common)  # for logging
    else:
        funding_allow = pd.Series(True, index=common)
        funding_value = pd.Series(np.nan, index=common)

    trade_log = []
    pos = 0
    bars_held = 0
    entry_idx = None
    entry_z = None
    entry_a = None
    entry_b = None
    entry_funding = None
    n = len(common)
    pnl_pct_per_bar = np.zeros(n)
    for i in range(1, n):
        cur_pos = pos
        zi = float(z.iat[i]) if np.isfinite(z.iat[i]) else None
        # VPVR confluence gate (entry only — already-in-position handled below)
        if cur_pos == 0 and zi is not None and cfg["entry"].get("require_vpvr_confluence", True) and not bool(near_poc.iat[i]):
            cur_pos = 0
        # Funding-blowoff gate (entry only)
        if cur_pos == 0 and zi is not None and require_funding_filter and not bool(funding_allow.iat[i]):
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
            fa_raw = funding_a.reindex(common) if funding_a is not None else None
            entry_funding = float(fa_raw.iat[i]) if fa_raw is not None and pd.notna(fa_raw.iat[i]) else None
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
                    funding_at_entry=entry_funding,
                    exit_reason=exit_reason,
                ))
                pos = 0
                bars_held = 0
                entry_idx = None
                entry_z = None
                entry_a = None
                entry_b = None
                entry_funding = None
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
                funding_at_entry=entry_funding,
                exit_reason="confluence_lost",
            ))
            pos = 0
            bars_held = 0
            entry_idx = None
            entry_z = None
            entry_a = None
            entry_b = None
            entry_funding = None

    starting = float(cfg["starting_capital_usd"])
    equity = np.empty(n)
    equity[0] = starting
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + pnl_pct_per_bar[i])
    return {
        "pair": pair_label,
        "trades": [_trade_to_dict(t) for t in trade_log],
        "equity": equity,
        "bar_return": pnl_pct_per_bar,
        "n_trades": len(trade_log),
        "n_bars": n,
        "span_start": common[0].date().isoformat() if n else None,
        "span_end": common[-1].date().isoformat() if n else None,
    }


def run_backtest(data, cfg, funding=None):
    """Aggregate pair-level backtests into a portfolio."""
    pairs = list(cfg.get("pairs", []))
    results = []
    for pair in pairs:
        a_sym, b_sym = pair.split("/")
        if a_sym not in data or b_sym not in data:
            raise SystemExit("missing data for pair " + pair)
        fa = None if funding is None else funding.get(a_sym)
        fb = None if funding is None else funding.get(b_sym)
        res = run_pair_backtest(data[a_sym], data[b_sym], cfg, pair, funding_a=fa, funding_b=fb)
        results.append(res)

    min_bars = min(len(r["equity"]) for r in results) if results else 0
    if min_bars == 0:
        combined_equity = np.zeros(0)
    else:
        combined_equity = np.mean([r["equity"][:min_bars] for r in results], axis=0)
    return {"per_pair": results, "equity": combined_equity}
