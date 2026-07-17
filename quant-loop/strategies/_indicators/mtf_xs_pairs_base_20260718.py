"""Shared multi-timeframe base for SMA-34875 campaign (mtf-1m-15m-2h).

Design
------
- Primary timeframe = 1m (native parquet per symbol).
- Real 15m and 2h bars are built by aggregating the same 1m parquet (NOT
  resampled elsewhere) so 15m/2h features are derived directly from the
  underlying 1m tape — no look-ahead, no duplicate data sources.
- All hypothesis-specific logic is parameterised by ``cfg["hypothesis"]``.
- ``run_backtest`` produces per-bar return, equity, and a daily-resampled
  Sharpe (smark directive 2026-07-18: daily-resampled is the only accepted
  Sharpe method).

Conventions
-----------
- df index = pd.DatetimeIndex (UTC, naive after load).
- Trade PnL = (a_return - b_return) * sign(pos) per bar.
- Fees + slippage deducted per side per leg per round trip.
- All rolling indicators are computed on bars that have already closed at
  index i (i.e. bar i's signal uses information up to bar i-1 inclusive).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "A"


# ---------------------------------------------------------------------------
# Aggregation: 1m -> 15m / 2h
# ---------------------------------------------------------------------------

def aggregate_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate OHLCV to a higher timeframe using the standard rule.

    Uses closed='left', label='left' so that a bar labelled 12:15 on the
    15m grid contains the 1m bars 12:15-12:29 — i.e. the bar at t uses
    information up to and including t. This avoids the common look-ahead
    bug of using closed='right' on the boundary.

    Accepts DataFrame with the standard OHLCV columns or a single-column
    DataFrame (e.g. z-score): the agg dict reduces each present column
    with its canonical reducer (open=first, high=max, low=min, close=last,
    volume=sum, anything else = last).
    """
    canonical = {"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"}
    agg = {c: canonical.get(c, "last") for c in df.columns}
    out = df.resample(rule, closed="left", label="left").agg(agg)
    # Pick any present column as the dropna subset
    drop_subset = [c for c in ("open", "close", "value", df.columns[0]) if c in out.columns]
    if not drop_subset:
        drop_subset = [df.columns[0]]
    return out.dropna(subset=drop_subset)


def align_lower_to_upper(lower: pd.DataFrame, upper: pd.Series) -> pd.Series:
    """Forward-fill an upper-timeframe series onto every lower-timeframe bar.

    Each lower bar at time t receives the upper-bar value whose label is the
    most recent upper-bar start <= t. No future leak.
    """
    s = upper.sort_index()
    return s.reindex(lower.index, method="ffill")


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - prev_close).abs()
    l_pc = (df["low"] - prev_close).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1).astype(float)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def pair_zscore(close_a: pd.Series, close_b: pd.Series, lookback: int) -> pd.Series:
    """Log-price z-score with rolling lookback."""
    log_ratio = np.log(close_a.astype(float)) - np.log(close_b.astype(float))
    mu = log_ratio.rolling(lookback, min_periods=lookback).mean()
    sd = log_ratio.rolling(lookback, min_periods=lookback).std(ddof=0)
    return ((log_ratio - mu) / sd.replace(0.0, np.nan)).rename("z")


def zscore_slope(z: pd.Series, lookback: int) -> pd.Series:
    """Slope of z over the last ``lookback`` bars (linear-regression slope)."""
    idx = np.arange(lookback)
    out = np.full(len(z), np.nan)
    arr = z.to_numpy()
    for i in range(lookback - 1, len(arr)):
        if not np.all(np.isfinite(arr[i - lookback + 1: i + 1])):
            continue
        y = arr[i - lookback + 1: i + 1]
        x = idx
        x_mean = idx.mean()
        y_mean = y.mean()
        num = ((x - x_mean) * (y - y_mean)).sum()
        den = ((x - x_mean) ** 2).sum()
        out[i] = num / den if den > 0 else 0.0
    return pd.Series(out, index=z.index, name="z_slope")


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def rolling_vpvr_levels(close: pd.Series, volume: pd.Series, window: int,
                        n_bins: int) -> pd.DataFrame:
    """Rolling VPVR POC/VAH/VAL from the most-recent ``window`` bars.

    Uses strict-past window: bar i's POC is computed from bars [i-window, i)
    so signal-at-i never sees bar i's own close/volume. We return the
    DataFrame with index aligned to the original ``close`` index.
    """
    n = len(close)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val = np.full(n, np.nan)
    c = close.to_numpy()
    v = volume.to_numpy()
    for i in range(window, n):
        sub_c = c[i - window: i]
        sub_v = v[i - window: i]
        if not (np.isfinite(sub_c).any() and np.isfinite(sub_v).any()):
            continue
        p_lo = float(np.nanmin(sub_c))
        p_hi = float(np.nanmax(sub_c))
        if not (np.isfinite(p_lo) and np.isfinite(p_hi)) or p_hi <= p_lo:
            continue
        bins = np.linspace(p_lo, p_hi, n_bins + 1)
        idx = np.clip(np.digitize(sub_c, bins) - 1, 0, n_bins - 1)
        bin_v = np.zeros(n_bins)
        for k in range(n_bins):
            mask = idx == k
            bin_v[k] = float(sub_v[mask].sum()) if mask.any() else 0.0
        total = bin_v.sum()
        if total <= 0:
            continue
        # POC = bin with highest volume
        pk = int(np.argmax(bin_v))
        poc[i] = (bins[pk] + bins[pk + 1]) / 2.0
        # Value area = bins that together hold 70% of volume around POC
        order = np.argsort(-bin_v)
        cum = 0.0
        va_bins = set()
        for k in order:
            cum += bin_v[k]
            va_bins.add(int(k))
            if cum / total >= 0.70:
                break
        va_idx = sorted(va_bins)
        vah[i] = bins[max(va_idx) + 1]
        val[i] = bins[min(va_idx)]
    return pd.DataFrame({"poc": poc, "vah": vah, "val": val}, index=close.index)


def trend_direction(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """+1 if fast EMA > slow EMA, -1 if below, 0 otherwise."""
    f = ema(close, fast)
    s = ema(close, slow)
    diff = f - s
    return diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)).rename("trend")


# ---------------------------------------------------------------------------
# Trade + result containers
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    pair: str
    direction: str
    entry_ts: pd.Timestamp
    entry_price_a: float
    entry_price_b: float
    exit_ts: Optional[pd.Timestamp]
    exit_price_a: Optional[float]
    exit_price_b: Optional[float]
    pnl_pct: float
    bars_held: int
    z_at_entry: float
    z_at_exit: Optional[float]
    slope15m_at_entry: Optional[float]
    trend2h_at_entry: Optional[int]
    exit_reason: str


def _trade_dict(t: Trade) -> dict:
    d = asdict(t)
    for k, v in d.items():
        if isinstance(v, pd.Timestamp):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Hypothesis-specific signal builders
# ---------------------------------------------------------------------------

def build_h1_signals(d1m: dict, cfg: dict) -> dict:
    """H1 — 1m cross-pair z-score entry, 15m slope confirmation, 2h regime.

    For each pair (a/b), compute:
      - z1m: rolling z-score on log(close_a/close_b) over ``zscore_lookback_bars``
      - z_slope_15m: slope of z aggregated to 15m over the last ``slope_15m_lookback`` 15m bars
      - trend_2h: +1/-1 from fast/slow EMA on 2h bars
    Entry when |z1m| > entry_threshold AND z_slope_15m is negative (mean
    reversion), AND 2h trend agrees with side.
    """
    ind = cfg["indicators"]
    z_lookback = int(ind["zscore_lookback_bars"])
    slope_lookback = int(ind["slope_15m_lookback"])
    z_entry = float(ind["zscore_entry_threshold"])
    z_exit = float(ind["zscore_exit_threshold"])
    regime_break = float(ind["regime_break_threshold"])
    max_hold = int(ind["max_holding_bars"])

    out = {}
    for pair in cfg["pairs"]:
        a_sym, b_sym = pair.split("/")
        a, b = d1m[a_sym], d1m[b_sym]
        common = a.index.intersection(b.index)
        a = a.loc[common]
        b = b.loc[common]

        z = pair_zscore(a["close"], b["close"], z_lookback)

        # 15m aggregate of z
        z_15m = aggregate_ohlcv(z.rename("z").to_frame(), "15min")["z"]
        z_slope = zscore_slope(z_15m, slope_lookback).rename("z_slope")
        slope_1m = align_lower_to_upper(a, z_slope)

        # 2h trend on pair ratio close (geometric mean for symmetry)
        ratio_15m = (a["close"] / b["close"]).resample("2h", closed="left", label="left").mean().dropna()
        # build a 2h close index aligned
        ratio_2h = ratio_15m
        trend = trend_direction(ratio_2h,
                                 int(ind["trend_2h_fast"]),
                                 int(ind["trend_2h_slow"]))
        trend_1m = align_lower_to_upper(a, trend).fillna(0).astype(int)

        out[pair] = {
            "a": a, "b": b,
            "z": z,
            "z_slope_15m": slope_1m,
            "trend_2h": trend_1m,
            "params": {
                "z_entry": z_entry, "z_exit": z_exit,
                "regime_break": regime_break, "max_hold": max_hold,
            },
        }
    return out


def build_h2_signals(d1m: dict, cfg: dict) -> dict:
    """H2 — VPVR edge touch on 15m/2h + 1m micro-reversion.

    For each pair, compute 15m and 2h VPVR POC/VAH/VAL from the recent
    window. On 1m bars: enter when close_a touches VAH (short) or VAL
    (long) of either 15m or 2h profile, with confirmation that the
    micro-reversion (close - open of last 1m bar) is consistent.
    """
    ind = cfg["indicators"]
    vpvr_window_15m = int(ind["vpvr_window_bars_15m"])
    vpvr_window_2h = int(ind["vpvr_window_bars_2h"])
    n_bins = int(ind["vpvr_n_bins"])
    z_entry = float(ind["zscore_entry_threshold"])
    z_exit = float(ind["zscore_exit_threshold"])
    z_lookback = int(ind["zscore_lookback_bars"])
    max_hold = int(ind["max_holding_bars"])
    touch_atr_k = float(ind["touch_atr_k"])

    out = {}
    for pair in cfg["pairs"]:
        a_sym, b_sym = pair.split("/")
        a, b = d1m[a_sym], d1m[b_sym]
        common = a.index.intersection(b.index)
        a = a.loc[common]
        b = b.loc[common]

        # 15m and 2h aggregates of a
        a_15m = aggregate_ohlcv(a, "15min")
        a_2h = aggregate_ohlcv(a, "2h")
        prof_15m = rolling_vpvr_levels(a_15m["close"], a_15m["volume"], vpvr_window_15m, n_bins)
        prof_2h = rolling_vpvr_levels(a_2h["close"], a_2h["volume"], vpvr_window_2h, n_bins)
        prof_15m_1m = prof_15m.reindex(a.index, method="ffill")
        prof_2h_1m = prof_2h.reindex(a.index, method="ffill")

        atr_15m = wilder_atr(a_15m, 14)
        atr_1m = align_lower_to_upper(a, atr_15m)

        # 1m z-score of the pair
        z = pair_zscore(a["close"], b["close"], z_lookback)

        out[pair] = {
            "a": a, "b": b,
            "prof_15m": prof_15m_1m,
            "prof_2h": prof_2h_1m,
            "atr": atr_1m,
            "z": z,
            "params": {
                "z_entry": z_entry, "z_exit": z_exit,
                "max_hold": max_hold, "touch_atr_k": touch_atr_k,
            },
        }
    return out


def build_h3_signals(d1m: dict, cfg: dict, funding: dict) -> dict:
    """H3 — 2h funding regime filter + 1m BTC/SOL pair entry.

    2h funding EMA on BTC and SOL (forward-filled from 8h events) defines
    risk-on/risk-off regime: enter only when |funding_ema| < threshold.
    1m z-score entry on BTC/SOL pair only; 15m ATR scales position
    size (multiplier applied later in sizing block).
    """
    ind = cfg["indicators"]
    z_lookback = int(ind["zscore_lookback_bars"])
    z_entry = float(ind["zscore_entry_threshold"])
    z_exit = float(ind["zscore_exit_threshold"])
    max_hold = int(ind["max_holding_bars"])
    fund_thr = float(ind["funding_filter_threshold"])
    fund_ema_n = int(ind["funding_ema_window"])

    out = {}
    for pair in cfg["pairs"]:
        a_sym, b_sym = pair.split("/")
        a, b = d1m[a_sym], d1m[b_sym]
        common = a.index.intersection(b.index)
        a = a.loc[common]
        b = b.loc[common]

        z = pair_zscore(a["close"], b["close"], z_lookback)

        # 2h funding EMA from 8h events
        def _fund_2h(sym: str) -> pd.Series:
            f = funding.get(sym)
            if f is None or len(f) == 0:
                return pd.Series(1.0, index=common, name="fund_allow")  # allow by default
            f = f.copy()
            # Strip tz to match the (naive) common index so resample/reindex align
            if f.index.tz is not None:
                f.index = f.index.tz_convert(None)
            ema_e = f.ewm(span=max(fund_ema_n, 2), adjust=False).mean()
            # aggregate to 2h via mean of events in each 2h bin
            ema_2h = ema_e.resample("2h", closed="left", label="left").mean().dropna()
            ema_1m = ema_2h.reindex(common, method="ffill")
            return (ema_1m.abs() < fund_thr).fillna(True).astype(int)

        fund_a = _fund_2h(a_sym)
        fund_b = _fund_2h(b_sym)
        fund_allow = (fund_a.astype(bool) & fund_b.astype(bool)).astype(int)

        # 15m ATR for sizing
        b_15m = aggregate_ohlcv(b, "15min")
        atr_15m_b = wilder_atr(b_15m, 14)
        atr_1m = align_lower_to_upper(b, atr_15m_b)
        # size scale: higher ATR -> smaller size. Normalise to ~[0.5, 2.0].
        atr_med = atr_1m.rolling(int(ind["atr_normalize_window"]), min_periods=240).median()
        size_scale = (atr_med / atr_1m.replace(0.0, np.nan)).clip(0.5, 2.0).fillna(1.0)

        out[pair] = {
            "a": a, "b": b,
            "z": z,
            "fund_allow": fund_allow,
            "size_scale": size_scale,
            "params": {
                "z_entry": z_entry, "z_exit": z_exit,
                "max_hold": max_hold,
            },
        }
    return out


def build_h4_signals(d1m: dict, cfg: dict) -> dict:
    """H4 — Multi-pair portfolio on 1m z-score, 15m noise filter, 2h trend cap.

    Per the campaign H4 spec (smark directive 2026-07-18):
      - 1m z-score entries (cross-pair log-price z on 1m close)
      - 15m noise filter: require price-vs-15m-EMA direction confirmation
        (for a long_a_short_b: close_a above its 15m EMA AND close_b below
        its 15m EMA — i.e. the spread expansion is consistent with each
        leg's own local trend)
      - 2h trend cap: only enter when 2h trend matches trade direction
        (long requires 2h trend >= +1, short requires 2h trend <= -1).
        No counter-trend entries.

    Output per pair includes ``price_ema_15m`` (sign of close - EMA15 for
    each leg) used downstream by ``_backtest_pair`` as the 15m filter.
    """
    ind = cfg["indicators"]
    z_lookback = int(ind["zscore_lookback_bars"])
    z_entry = float(ind["zscore_entry_threshold"])
    z_exit = float(ind["zscore_exit_threshold"])
    max_hold = int(ind["max_holding_bars"])
    ema_fast = int(ind["ema_15m_fast"])
    ema_slow = int(ind["ema_15m_slow"])
    trend_fast = int(ind["trend_2h_fast"])
    trend_slow = int(ind["trend_2h_slow"])
    max_pairs = int(cfg.get("sizing", {}).get("max_pairs_active", 3))

    out = {}
    for pair in cfg["pairs"]:
        a_sym, b_sym = pair.split("/")
        a, b = d1m[a_sym], d1m[b_sym]
        common = a.index.intersection(b.index)
        a = a.loc[common]
        b = b.loc[common]
        z = pair_zscore(a["close"], b["close"], z_lookback)

        # 15m EMA per leg — direction filter
        a_15m = aggregate_ohlcv(a, "15min")
        b_15m = aggregate_ohlcv(b, "15min")
        # Fast vs slow EMA cross on 15m bars
        ema_a_fast = ema(a_15m["close"], ema_fast)
        ema_a_slow = ema(a_15m["close"], ema_slow)
        ema_b_fast = ema(b_15m["close"], ema_fast)
        ema_b_slow = ema(b_15m["close"], ema_slow)
        # 1 = up, -1 = down, 0 = undefined (NaN)
        trend_a_15m = (ema_a_fast - ema_a_slow).apply(
            lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
        ).rename("trend_a_15m")
        trend_b_15m = (ema_b_fast - ema_b_slow).apply(
            lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
        ).rename("trend_b_15m")
        trend_a_1m = align_lower_to_upper(a, trend_a_15m).fillna(0).astype(int)
        trend_b_1m = align_lower_to_upper(a, trend_b_15m).fillna(0).astype(int)

        # 2h trend on pair ratio (geometric mean for symmetry) — trend cap
        ratio_2h = (a["close"] / b["close"]).resample("2h", closed="left", label="left").mean().dropna()
        trend = trend_direction(ratio_2h, trend_fast, trend_slow)
        trend_1m = align_lower_to_upper(a, trend).fillna(0).astype(int)

        out[pair] = {
            "a": a, "b": b,
            "z": z,
            "price_ema_15m": {
                "trend_a": trend_a_1m,
                "trend_b": trend_b_1m,
            },
            "trend_2h": trend_1m,
            "params": {
                "z_entry": z_entry, "z_exit": z_exit,
                "max_hold": max_hold, "max_pairs_active": max_pairs,
            },
        }
    return out


# ---------------------------------------------------------------------------
# Per-pair backtest loop (1m bar-by-bar)
# ---------------------------------------------------------------------------

def _backtest_pair(signals: dict, pair: str, sizing_scale: Optional[pd.Series] = None,
                   fee_bps: float = 1.0, slip_bps: float = 1.0) -> dict:
    a = signals["a"]
    b = signals["b"]
    common = a.index
    n = len(common)
    p = signals["params"]
    z = signals["z"]
    z_entry = float(p["z_entry"])
    z_exit = float(p.get("z_exit", 0.5))
    regime_break = float(p.get("regime_break", 3.0))
    max_hold = int(p["max_hold"])
    slope = signals.get("z_slope_15m")
    trend = signals.get("trend_2h")

    trade_log = []
    pos = 0
    bars_held = 0
    entry_idx = None
    entry_a = entry_b = None
    entry_z = None
    entry_slope = None
    entry_trend = None
    pnl_per_bar = np.zeros(n)
    # H4-only 15m direction filter (price vs 15m EMA on each leg)
    pe15 = signals.get("price_ema_15m")
    for i in range(1, n):
        zi = float(z.iat[i]) if np.isfinite(z.iat[i]) else None
        sl = float(slope.iat[i]) if slope is not None and np.isfinite(slope.iat[i]) else None
        tr = int(trend.iat[i]) if trend is not None and np.isfinite(trend.iat[i]) else 0

        if pos == 0 and zi is not None:
            direction = 0
            if zi <= -z_entry:
                direction = +1
            elif zi >= +z_entry:
                direction = -1

            # hypothesis-specific entry filters
            allow = True
            if "z_slope_15m" in p or slope is not None:  # H1 — z-slope confirm
                if direction == +1 and (sl is None or sl >= 0):
                    allow = False
                if direction == -1 and (sl is None or sl <= 0):
                    allow = False
            if pe15 is not None:  # H4 — 15m close-vs-EMA direction filter
                ta = int(pe15["trend_a"].iat[i]) if np.isfinite(pe15["trend_a"].iat[i]) else 0
                tb = int(pe15["trend_b"].iat[i]) if np.isfinite(pe15["trend_b"].iat[i]) else 0
                # long_a_short_b: a in uptrend (+1), b in downtrend (-1)
                # short_a_long_b: a in downtrend (-1), b in uptrend (+1)
                if direction == +1 and not (ta >= 1 and tb <= -1):
                    allow = False
                if direction == -1 and not (ta <= -1 and tb >= 1):
                    allow = False
            if trend is not None:  # H1, H4 — 2h regime cap (no counter-trend)
                if direction == +1 and tr < 0:
                    allow = False
                if direction == -1 and tr > 0:
                    allow = False
            fund_allow = signals.get("fund_allow")
            if fund_allow is not None:
                if int(fund_allow.iat[i]) == 0:
                    allow = False
            # H2 VPVR edge-touch confirmation (must touch VAH/VAL)
            prof_15m = signals.get("prof_15m")
            prof_2h = signals.get("prof_2h")
            atr_1m = signals.get("atr")
            if prof_15m is not None and prof_2h is not None and atr_1m is not None:
                touch_k = float(p.get("touch_atr_k", 0.7))
                atr_v = float(atr_1m.iat[i]) if np.isfinite(atr_1m.iat[i]) else 0.0
                cl = float(a["close"].iat[i])
                vah15 = float(prof_15m["vah"].iat[i]) if np.isfinite(prof_15m["vah"].iat[i]) else np.nan
                val15 = float(prof_15m["val"].iat[i]) if np.isfinite(prof_15m["val"].iat[i]) else np.nan
                vah2h = float(prof_2h["vah"].iat[i]) if np.isfinite(prof_2h["vah"].iat[i]) else np.nan
                val2h = float(prof_2h["val"].iat[i]) if np.isfinite(prof_2h["val"].iat[i]) else np.nan
                tol = max(touch_k * atr_v, 1e-9)
                touches_long = any(np.isfinite(x) and abs(cl - x) <= tol for x in (val15, val2h))
                touches_short = any(np.isfinite(x) and abs(cl - x) <= tol for x in (vah15, vah2h))
                if direction == +1 and not touches_long:
                    allow = False
                if direction == -1 and not touches_short:
                    allow = False

            if allow and direction != 0:
                pos = direction
                entry_idx = i
                entry_a = float(a["close"].iat[i])
                entry_b = float(b["close"].iat[i])
                entry_z = zi
                entry_slope = sl
                entry_trend = tr
                bars_held = 1
        elif pos != 0:
            bars_held += 1
            a_ret = float(a["close"].iat[i]) / float(a["close"].iat[i - 1]) - 1.0
            b_ret = float(b["close"].iat[i]) / float(b["close"].iat[i - 1]) - 1.0
            scale = float(sizing_scale.iat[i]) if sizing_scale is not None and np.isfinite(sizing_scale.iat[i]) else 1.0
            pnl_per_bar[i] = pos * (a_ret - b_ret) / 2.0 * scale

            exit_reason = None
            if abs(zi) <= z_exit:
                exit_reason = "z_mean_revert"
            elif (pos == +1 and zi <= -regime_break) or (pos == -1 and zi >= +regime_break):
                exit_reason = "regime_break"
            elif bars_held >= max_hold:
                exit_reason = "max_holding"
            if exit_reason:
                exit_a = float(a["close"].iat[i])
                exit_b = float(b["close"].iat[i])
                if pos == +1:
                    pct = (exit_a / entry_a - 1.0) - (exit_b / entry_b - 1.0)
                else:
                    pct = -(exit_a / entry_a - 1.0) + (exit_b / entry_b - 1.0)
                cost = 2.0 * 2.0 * (fee_bps + slip_bps) / 10_000.0
                net = pct - cost
                trade_log.append(Trade(
                    pair=pair,
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
                    slope15m_at_entry=entry_slope,
                    trend2h_at_entry=entry_trend,
                    exit_reason=exit_reason,
                ))
                pos = 0
                bars_held = 0
                entry_idx = entry_a = entry_b = entry_z = entry_slope = entry_trend = None
    return {
        "pair": pair,
        "trades": [_trade_dict(t) for t in trade_log],
        "bar_return": pnl_per_bar,
        "n_bars": n,
        "span_start": a.index[0].date().isoformat() if n else None,
        "span_end": a.index[-1].date().isoformat() if n else None,
    }


# ---------------------------------------------------------------------------
# Portfolio aggregation
# ---------------------------------------------------------------------------

def build_portfolio(per_pair: list, starting_capital: float = 100000.0) -> dict:
    """Equal-weight across active pairs. Use mean of per-bar returns."""
    n_bars = min(p["n_bars"] for p in per_pair) if per_pair else 0
    if n_bars == 0:
        return {"equity": np.zeros(0), "bar_return": np.zeros(0)}
    returns = np.mean([p["bar_return"][:n_bars] for p in per_pair], axis=0)
    equity = np.empty(n_bars)
    equity[0] = starting_capital
    for i in range(1, n_bars):
        equity[i] = equity[i - 1] * (1.0 + returns[i])
    return {"equity": equity, "bar_return": returns, "n_bars": n_bars}


def build_h4_portfolio(per_pair: list, cfg: dict,
                       starting_capital: float = 100000.0) -> dict:
    """H4 portfolio with correlation-aware sizing + gross/net exposure caps.

    Sizing model (per campaign spec — portfolio-level position management):
      1. Each pair has a base notional_pct of capital. Sum of base
         notionals must NOT exceed gross_cap (default 0.06 = 6% of
         capital). If sum > gross_cap, scale every pair's notional
         down proportionally.
      2. Correlation-aware scaling: when rolling pairwise correlation
         (default 60-day window on daily-resampled pair returns) is
         high, the diversification benefit is small and we shrink
         gross exposure toward net_cap (default 0.04 = 4% of capital)
         in proportion to mean off-diagonal correlation.
      3. Per-pair bar returns are scaled by the resulting per-pair
         notional / starting_capital before being averaged into the
         portfolio bar return. So portfolio pnl = sum(pair_pnl_i) and
         |portfolio_pnl| / capital <= gross_cap at every bar.

    Returns the same shape as ``build_portfolio`` plus sizing metadata.
    """
    n_bars = min(p["n_bars"] for p in per_pair) if per_pair else 0
    if n_bars == 0:
        return {
            "equity": np.zeros(0), "bar_return": np.zeros(0), "n_bars": 0,
            "sizing": {},
        }

    sizing_cfg = cfg.get("sizing", {})
    base_notional_pct = float(sizing_cfg.get("per_pair_notional_pct", 0.02))
    gross_cap = float(sizing_cfg.get("gross_cap", 0.06))
    net_cap = float(sizing_cfg.get("net_cap", 0.04))
    corr_window_days = int(sizing_cfg.get("corr_window_days", 60))
    corr_high = float(sizing_cfg.get("corr_high_threshold", 0.6))
    max_pairs_active = int(sizing_cfg.get("max_pairs_active", 3))

    # Cap active pairs
    active = per_pair[:max_pairs_active]
    n_active = len(active)
    gross_base = base_notional_pct * n_active
    if gross_base > gross_cap:
        per_pair_notional = (gross_cap / n_active) * np.ones(n_active)
    else:
        per_pair_notional = np.full(n_active, base_notional_pct)

    # Correlation-aware scaling: compute daily-resampled returns per pair
    # then rolling pairwise correlation mean. We use the whole-history
    # mean as the portfolio-level scaling factor (no per-bar conditional
    # to keep the backtest simple and deterministic for OOS reproducibility).
    daily_returns_per_pair = []
    first_index = None
    for pr in active:
        idx = pd.date_range("2022-01-01", periods=pr["n_bars"], freq="1min")
        dr = daily_returns(pr["bar_return"], idx)
        daily_returns_per_pair.append(dr)
        if first_index is None:
            first_index = pr
    if daily_returns_per_pair and len(daily_returns_per_pair) >= 2:
        # Align all daily-return series on their common dates
        aligned = pd.concat(daily_returns_per_pair, axis=1).dropna()
        if len(aligned) > corr_window_days:
            recent = aligned.tail(corr_window_days)
        else:
            recent = aligned
        corr = recent.corr().to_numpy()
        n_c = corr.shape[0]
        if n_c > 1:
            off_diag_sum = corr.sum() - np.trace(corr)
            mean_off_corr = float(off_diag_sum / (n_c * (n_c - 1)))
        else:
            mean_off_corr = 0.0
        # Mean correlation in [0, 1] for shrinkage
        corr_strength = max(0.0, min(1.0, abs(mean_off_corr)))
        # When corr is high (>= corr_high), shrink gross to net_cap
        if mean_off_corr >= corr_high:
            shrink = (mean_off_corr - corr_high) / max(1.0 - corr_high, 1e-9)
            shrink = min(max(shrink, 0.0), 1.0)
            target_gross = gross_base + (gross_cap - gross_base) * shrink  # no shrink if 0
            target_gross = gross_base - shrink * (gross_base - net_cap)
            target_gross = max(net_cap, min(gross_cap, target_gross))
        else:
            target_gross = gross_base
        # Scale per-pair notional to hit target_gross
        if target_gross < gross_base and n_active > 0:
            per_pair_notional = (target_gross / n_active) * np.ones(n_active)
    else:
        mean_off_corr = 0.0

    # Build portfolio bar return = sum of (per-pair notional * per-pair bar return)
    pair_notional_pct = {active[i]["pair"]: float(per_pair_notional[i])
                         for i in range(n_active)}
    portfolio_return = np.zeros(n_bars)
    for i, pr in enumerate(active):
        if i >= n_active:
            break
        portfolio_return += per_pair_notional[i] * pr["bar_return"][:n_bars]

    equity = np.empty(n_bars)
    equity[0] = starting_capital
    for i in range(1, n_bars):
        equity[i] = equity[i - 1] * (1.0 + portfolio_return[i])

    return {
        "equity": equity,
        "bar_return": portfolio_return,
        "n_bars": n_bars,
        "sizing": {
            "per_pair_notional_pct": pair_notional_pct,
            "gross_notional_pct": float(sum(pair_notional_pct.values())),
            "mean_off_diag_corr": float(mean_off_corr),
            "max_pairs_active": int(max_pairs_active),
            "gross_cap": float(gross_cap),
            "net_cap": float(net_cap),
        },
    }


# ---------------------------------------------------------------------------
# Metrics — daily-resampled Sharpe is the only accepted method
# ---------------------------------------------------------------------------

def daily_returns(bar_return: np.ndarray, index: pd.DatetimeIndex) -> pd.Series:
    if len(bar_return) == 0 or len(index) == 0:
        return pd.Series(dtype=float)
    eq = np.empty(len(bar_return))
    eq[0] = 1.0
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    eq_s = pd.Series(eq, index=index)
    # Aggregate bar-equity into daily equity by taking the last bar per day
    daily_eq = eq_s.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    return daily_ret


def sharpe_daily_resampled(bar_return: np.ndarray, index: pd.DatetimeIndex) -> dict:
    """Sharpe computed on daily-resampled returns (smark directive 2026-07-18).

    Returns dict with: sharpe_daily_resampled, annualized_return_daily,
    span, n_days.
    """
    dr = daily_returns(bar_return, index)
    if len(dr) < 5:
        return {
            "sharpe_daily_resampled": 0.0,
            "annualized_return_daily": 0.0,
            "n_days": int(len(dr)),
            "span": [None, None],
        }
    mu = float(dr.mean())
    sd = float(dr.std(ddof=1))
    sharpe = (mu / sd) * math.sqrt(365.0) if sd > 0 else 0.0
    # CAGR from cumulative growth
    total = float((1.0 + dr).prod() - 1.0)
    n_days = len(dr)
    cagr = (1.0 + total) ** (365.0 / n_days) - 1.0 if n_days > 0 and (1.0 + total) > 0 else -1.0
    return {
        "sharpe_daily_resampled": float(sharpe),
        "annualized_return_daily": float(cagr),
        "n_days": int(n_days),
        "span": [str(dr.index[0].date()), str(dr.index[-1].date())],
    }


def profit_factor_and_mdd(bar_return: np.ndarray, starting_capital: float) -> dict:
    """PF from bar returns split by positive/negative; MDD from equity curve."""
    if len(bar_return) == 0:
        return {"profit_factor": 0.0, "max_drawdown_pct": 0.0}
    pos = bar_return[bar_return > 0].sum()
    neg = -bar_return[bar_return < 0].sum()
    pf = float(pos / neg) if neg > 0 else float("inf")
    eq = np.empty(len(bar_return))
    eq[0] = starting_capital
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    return {"profit_factor": float(pf), "max_drawdown_pct": float(dd.min())}


# ---------------------------------------------------------------------------
# Public entrypoint: run_backtest(data_1m, cfg, funding=None)
# ---------------------------------------------------------------------------

def run_backtest(d1m: dict, cfg: dict, funding: Optional[dict] = None) -> dict:
    """Dispatch to hypothesis-specific signal builder then backtest."""
    hyp = cfg["hypothesis"]
    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))

    # Normalise indices to tz-naive so cross-TF resample/reindex is consistent.
    d1m_norm = {}
    for sym, df in d1m.items():
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_convert(None)
        d1m_norm[sym] = df

    if hyp == "H1":
        signals_by_pair = build_h1_signals(d1m_norm, cfg)
    elif hyp == "H2":
        signals_by_pair = build_h2_signals(d1m_norm, cfg)
    elif hyp == "H3":
        if funding is None:
            funding = {}
        # also normalise funding indices
        f_norm = {}
        for sym, f in funding.items():
            if isinstance(f.index, pd.DatetimeIndex) and f.index.tz is not None:
                f = f.copy()
                f.index = f.index.tz_convert(None)
            f_norm[sym] = f
        signals_by_pair = build_h3_signals(d1m_norm, cfg, f_norm)
    elif hyp == "H4":
        signals_by_pair = build_h4_signals(d1m_norm, cfg)
    else:
        raise SystemExit("unknown hypothesis: " + str(hyp))

    per_pair = []
    for pair, sig in signals_by_pair.items():
        size_scale = sig.get("size_scale")
        result = _backtest_pair(sig, pair, sizing_scale=size_scale,
                                fee_bps=fee_bps, slip_bps=slip_bps)
        per_pair.append(result)

    starting_cap = float(cfg.get("starting_capital_usd", 100000.0))
    if hyp == "H4":
        portfolio = build_h4_portfolio(per_pair, cfg, starting_capital=starting_cap)
    else:
        portfolio = build_portfolio(per_pair, starting_capital=starting_cap)
    return {"per_pair": per_pair, "portfolio": portfolio}