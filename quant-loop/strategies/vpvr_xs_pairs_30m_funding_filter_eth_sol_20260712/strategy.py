"""V5 strategy (xs-pair z-score + VPVR confluence + funding-blowoff filter, iter #81).

Axis = cross-asset pair z-score with VPVR level filter AND a funding-rate
regime filter that skips entries when perp basis is destabilised.

Pair:
  BTCUSDT / SOLUSDT — BTC is loaded as a native 30m parquet; SOL is
  resampled on-the-fly from a 15m parquet (no native 30m parquet for SOL
  in this workspace).

Funding filter:
  - Funding events are 8h. We forward-fill funding_rate onto the 30m bar
    index, then take an EMA over ``funding_8h_ema_window`` events.
  - Entry is permitted only when abs(funding_8h_ema) < threshold
    (default 0.0005). Blowoffs (|funding| > 0.0005) are skipped because
    the perp basis destabilises and pair z-score mean-reverts slower.

VPVR confluence:
  - BTC's close must lie within proximity_atr_k * ATR(14) of BTC's
    rolling VPVR POC over the most recent vpvr_window_bars.

Returns
-------
A dict result with keys: trades, equity, bar_return, per_pair.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

VARIANT_KEY = "A"
TIMEFRAME_TAG = "30m"


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
    funding_ema_at_entry: float | None
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


def resample_ohlcv(df, rule="30min"):
    """Resample OHLCV to multi-hour bars."""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    return df.resample(rule).agg(agg).dropna(subset=["open"])


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


def funding_ema_filter(bar_index, funding_series, ema_window, threshold):
    """Build a per-bar boolean mask that is True when entry is allowed.

    funding_series: pd.Series indexed by event timestamp, value = funding rate.
    ema_window: number of 8h events for the EMA smoothing.
    threshold: |funding_8h_ema| must be < threshold to allow entries.

    Returns
    -------
    (allow, ema_bars): allow is a pd.Series of bool aligned to ``bar_index``
    (ffill), ema_bars is a per-bar pd.Series of the funding EMA value.
    """
    if funding_series is None or len(funding_series) == 0:
        allow = pd.Series(True, index=bar_index)
        ema = pd.Series(np.nan, index=bar_index, name="funding_8h_ema")
        return allow, ema

    fs = funding_series.copy()
    if fs.index.tz is not None:
        fs.index = fs.index.tz_convert(None)

    ema_events = fs.ewm(span=max(int(ema_window), 2), adjust=False).mean()
    ema_bars = ema_events.reindex(bar_index, method="ffill")
    allow = (ema_bars.abs() < float(threshold)).fillna(True)
    return allow, ema_bars.rename("funding_8h_ema")


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
        "funding_ema_at_entry": t.funding_ema_at_entry,
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
        allow_a, ema_a = funding_ema_filter(
            common, funding_a, int(ind["funding_8h_ema_window"]),
            float(ind["funding_filter_threshold"]),
        )
        allow_b, ema_b = funding_ema_filter(
            common, funding_b, int(ind["funding_8h_ema_window"]),
            float(ind["funding_filter_threshold"]),
        )
        funding_allow = (allow_a & allow_b).reindex(common).fillna(True)
        funding_ema = ema_a.reindex(common)
    else:
        funding_allow = pd.Series(True, index=common)
        funding_ema = pd.Series(np.nan, index=common, name="funding_8h_ema")

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
        # VPVR confluence gate
        if cur_pos == 0 and zi is not None and cfg["entry"].get("require_vpvr_confluence", True) and not bool(near_poc.iat[i]):
            cur_pos = 0
        # Funding-blowoff gate
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
            entry_funding = float(funding_ema.iat[i]) if pd.notna(funding_ema.iat[i]) else None
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
                    funding_ema_at_entry=entry_funding,
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
                funding_ema_at_entry=entry_funding,
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
    """Aggregate pair-level backtests into a portfolio.

    ``data`` is a dict of symbol -> DataFrame (already on the 30m timeframe).
    ``funding`` is an optional dict of symbol -> pd.Series of funding events.
    Returns a combined per-pair result + portfolio equity.
    """
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
