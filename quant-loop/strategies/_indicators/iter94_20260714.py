"""Indicators for iter#94+ (campaign SMA-33569).

Shared indicator primitives for the three iter#94 variants that the
VPVR specialist (B2 owner) wires into ``strategy.py``:

  V1 — vpvr_regime_blend_4h_20260714
       ADX + realized volatility → 3-state regime router
       (TREND / RANGE / BREAKOUT)

  V2 — vpvr_obi_micro_v2_1m_20260714
       Order-Book Imbalance proxy (close-open)/(high-low) z-score
       + VPVR POC proximity filter

  V3 — vpvr_mtf_consensus_v2_4h_20260714
       1m / 15m / 4h consensus (≥ 2/3 same direction)
       + vol-target sizing helper

All functions here are pure: numpy/pandas in, pandas Series / numpy
array out, no I/O, no globals. The B2 owner (vpvr-specialist) is
expected to import them and call them inside the per-variant
``build_signals`` / sizing logic.

Convention follows the per-strategy ``indicators.py`` modules:
``BARS_PER_YEAR_*`` constants live here as a single source of truth,
Wilder smoothing uses ``ewm(alpha=1/N, adjust=False, min_periods=N)``,
and rolling indicators ``shift(1)`` so the value at bar ``t`` reflects
bars ``[t-W, t-1]`` (no look-ahead).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Union

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants — single source of truth for annualisation factors.
# ---------------------------------------------------------------------------
# 24/7 crypto, 365.25 days/yr.
BARS_PER_YEAR_4H: int = int(365.25 * 6)         # 2191 → 2190 per spec
BARS_PER_YEAR_15M: int = int(365.25 * 24 * 4)   # 35046 → 35040 per spec
BARS_PER_YEAR_1M: int = int(365.25 * 24 * 60)   # 525960
BARS_PER_YEAR_1H: int = int(365.25 * 24)        # 8766

# Regime-router thresholds (from SPEC.md of vpvr_regime_blend_4h_20260714).
ADX_TREND_THRESHOLD: float = 25.0
RV_BREAKOUT_THRESHOLD_BPS: float = 350.0
RV_RANGE_THRESHOLD_BPS: float = 220.0


# ===========================================================================
# Helpers — Wilder smoothing + TR / DM building blocks.
# ===========================================================================

def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range series.

    ``tr[t] = max(high[t]-low[t], |high[t]-close[t-1]|, |low[t]-close[t-1]|)``.
    The first bar degenerates to ``high-low`` because no prior close exists.
    """
    prev_close = close.shift(1)
    hi_lo = high - low
    hi_pc = (high - prev_close).abs()
    lo_pc = (low - prev_close).abs()
    return pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing: ``ewm(alpha=1/period, adjust=False, min_periods=period)``.

    First ``period`` values are NaN because the smoothing window needs
    ``period`` bars to seed.
    """
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int) -> pd.Series:
    """Wilder ATR. First ``period`` bars are NaN.

    Used by V2 POC proximity and V3 sizing (realised vol is the lighter
    proxy; ATR is the heavy one — both live here so consumers don't
    reimplement).
    """
    tr = _true_range(high, low, close)
    return _wilder_smooth(tr, period)


# ===========================================================================
# V1 — ADX + realised volatility + 3-state regime router.
# ===========================================================================

def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Average Directional Index (Wilder ADX).

    Returns a ``pd.Series`` in ``[0, 100]`` (or NaN while the smoothing
    window is still seeding). Output at bar ``t`` uses data through bar
    ``t-1`` because the +DM / -DM step needs ``prev_high`` /
    ``prev_low``; we do NOT add an extra ``shift(1)`` because the
    directional information is already one-bar lagged by construction.

    Steps (Wilder):
        1. ``+DM[t] = max(high[t]-high[t-1], 0)`` if that exceeds
           ``low[t-1]-low[t]`` else 0; mirror for ``-DM``.
        2. ``TR`` = standard true range.
        3. Smooth TR / +DM / -DM with ``ewm(alpha=1/period)``.
        4. ``+DI = 100 * smooth(+DM) / smooth(TR)`` and mirror ``-DI``.
        5. ``DX  = 100 * |+DI - -DI| / (+DI + -DI)`` (NaN-safe).
        6. ``ADX = Wilder smooth of DX`` over ``period`` bars.
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index, name="plus_dm",
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index, name="minus_dm",
    )

    tr = _true_range(high, low, close)
    str_ = _wilder_smooth(tr, period)
    spdm = _wilder_smooth(plus_dm, period)
    smdm = _wilder_smooth(minus_dm, period)

    # Avoid div-by-zero: if TR smoothing is zero (all-flat bars) DI = 0.
    plus_di = 100.0 * spdm / str_.replace(0.0, np.nan)
    minus_di = 100.0 * smdm / str_.replace(0.0, np.nan)

    di_sum = plus_di.fillna(0.0) + minus_di.fillna(0.0)
    di_diff = (plus_di - minus_di).abs()
    dx = 100.0 * di_diff / di_sum.replace(0.0, np.nan)
    return _wilder_smooth(dx, period).rename("adx")


def realized_vol_bps(close: pd.Series, window: int,
                     bars_per_year: int = BARS_PER_YEAR_4H) -> pd.Series:
    """Rolling realised volatility of log returns, expressed in basis
    points (annualised).

        rv_bps[t] = std(log(close[t-window+1..t])) * sqrt(bars_per_year) * 1e4

    Default ``bars_per_year`` is the 4h annualisation factor because
    V1 feeds it into the 4h regime router. For 1m / 15m / 1h frames
    pass the matching ``BARS_PER_YEAR_*`` constant.

    Output at bar ``t`` uses returns through bar ``t``; we ``shift(1)``
    so the value at ``t`` reflects only bars ``[t-window, t-1]`` (no
    look-ahead on the current bar's return).
    """
    log_close = np.log(close.astype(float))
    log_ret = log_close.diff()
    rolling_std = log_ret.rolling(window=window, min_periods=window).std()
    rv = rolling_std * np.sqrt(bars_per_year) * 1e4
    return rv.shift(1).rename("realized_vol_bps")


# Regime-state output as a string Series — the B2 owner can do a
# ``state == "TREND"`` comparison without importing any enum machinery.
REGIME_LABELS: tuple = ("TREND", "RANGE", "BREAKOUT")


def regime_router(close: pd.Series, high: pd.Series, low: pd.Series,
                  period: int = 14,
                  rv_window: int = 30,
                  bars_per_year: int = BARS_PER_YEAR_4H,
                  adx_trend: float = ADX_TREND_THRESHOLD,
                  rv_breakout_bps: float = RV_BREAKOUT_THRESHOLD_BPS,
                  rv_range_bps: float = RV_RANGE_THRESHOLD_BPS,
                  ) -> pd.Series:
    """3-state regime router (TREND / RANGE / BREAKOUT).

    Decision order, matching SPEC.md:

        ADX(t) > adx_trend              → "TREND"
        RV(t)  > rv_breakout_bps        → "BREAKOUT"
        RV(t) <= rv_range_bps           → "RANGE"
        otherwise                       → "RANGE"   (mid-vol fallback)

    The router returns a ``pd.Series`` of strings drawn from
    ``REGIME_LABELS``. Bars where ADX or RV is still NaN (smoothing
    window seeding) propagate NaN.

    Defaults are the 4h parameters from SPEC.md of
    ``vpvr_regime_blend_4h_20260714``:
        ADX(14), RV(window=30), trend @ 25, breakout @ 350bps,
        range @ 220bps.
    """
    a = adx(high, low, close, period=period)
    rv = realized_vol_bps(close, window=rv_window, bars_per_year=bars_per_year)

    out = pd.Series(np.nan, index=close.index, dtype=object, name="regime")
    valid = a.notna() & rv.notna()

    is_trend = valid & (a > adx_trend)
    is_breakout = valid & ~is_trend & (rv > rv_breakout_bps)
    is_range = valid & ~is_trend & ~is_breakout  # covers both sub-cases

    out = out.mask(is_trend, "TREND")
    out = out.mask(is_breakout, "BREAKOUT")
    out = out.mask(is_range, "RANGE")
    return out


# ===========================================================================
# V2 — OBI proxy z-score + VPVR POC proximity.
# ===========================================================================

def obi_zscore(close: pd.Series, open_: pd.Series,
               high: pd.Series, low: pd.Series,
               window: int = 20) -> pd.Series:
    """OBI proxy: rolling z-score of ``(close - open) / (high - low)``.

    Definition (matches the task brief):
        raw[t] = (close[t] - open[t]) / (high[t] - low[t])
        mean[t] = rolling_mean(raw, window).shift(1)
        std[t]  = rolling_std (raw, window).shift(1)
        z[t]    = (raw[t-1] - mean[t]) / std[t]

    The ``shift(1)`` aligns the *normalisation window* to bars
    ``[t-window, t-1]`` so today's bar (``t``) does not participate
    in its own z-score — avoids look-ahead on the entry bar.

    Bars where ``high == low`` (doji / data gap) yield raw = NaN, which
    propagates; bars where ``std`` is 0 (constant raw across the
    window) yield ``z = NaN`` rather than ``inf``.
    """
    rng = (high - low).astype(float)
    raw = (close.astype(float) - open_.astype(float)) / rng.replace(0.0, np.nan)

    mean = raw.rolling(window=window, min_periods=window).mean().shift(1)
    std = raw.rolling(window=window, min_periods=window).std(ddof=0).shift(1)

    z = (raw - mean) / std.replace(0.0, np.nan)
    return z.rename("obi_zscore")


def vpvr_poc_proximity(price: pd.Series, poc: pd.Series, atr: pd.Series,
                       threshold: float = 0.3) -> pd.Series:
    """Boolean proximity filter: True if ``|price - poc| < threshold * atr``.

    Convention used by ``vpvr_obi_micro_v2_1m_20260714`` (V2): pass on
    the entry bar if the latest price sits within ``threshold`` ATR of
    the rolling VPVR POC. This keeps entries near *structural* support
    / resistance rather than chasing the tape mid-air.

    NaN inputs propagate to NaN output (NOT False), so the B2 owner can
    distinguish "filter not yet seeded" from "filter failed".
    """
    num = (price - poc).abs()
    raw = num < (threshold * atr)
    # Mask to NaN wherever any input is NaN — pandas would otherwise
    # turn NaN < threshold into False, which conflates "filter failed"
    # with "filter not seeded yet".
    valid = price.notna() & poc.notna() & atr.notna()
    out = raw.where(valid, np.nan)
    out.name = "poc_proximity"
    return out


# ===========================================================================
# V3 — multi-TF consensus + vol-target sizing.
# ===========================================================================

def mtf_consensus_signals(per_tf_signals: List[pd.Series],
                          threshold: float = 2.0 / 3.0,
                          ) -> pd.Series:
    """Multi-timeframe consensus direction in {-1, 0, +1}.

    ``per_tf_signals`` is a list of per-TF direction Series (one per
    timeframe — e.g. 1m / 15m / 4h). Each input value must be one of
    ``-1, 0, +1`` (or NaN). The output at bar ``t`` is the dominant
    direction if and only if the share of agreeing (non-zero) votes is
    strictly above ``threshold``:

        long_share  = count(+1) / count(non-zero)
        short_share = count(-1) / count(non-zero)

        if max(long_share, short_share) > threshold:
            return +1 or -1 respectively
        else:
            return 0

    For the canonical V3 use (1m + 15m + 4h) the default threshold is
    ``2/3``: at least 2 of 3 TFs must agree.

    Notes on alignment: callers are responsible for ``merge_asof``-
    aligning the 1m / 15m / 4h signals onto a common index BEFORE
    calling this. The function uses ``pd.concat(...).groupby(level=0)``
    so an index mismatch will raise a clear ``ValueError``.
    """
    if not per_tf_signals:
        raise ValueError("per_tf_signals must be a non-empty list of Series")

    # Reject index mismatch up-front — ``pd.concat(axis=1)`` would
    # otherwise silently outer-join and fill missing bars with NaN,
    # which the B2 owner almost certainly did not intend.
    ref_index = per_tf_signals[0].index
    for i, s in enumerate(per_tf_signals[1:], start=1):
        if not s.index.equals(ref_index):
            raise ValueError(
                f"per_tf_signals[{i}].index does not match "
                f"per_tf_signals[0].index; callers must align via "
                f"merge_asof or reindex before calling"
            )

    # Concat into one frame, count signs row-wise.
    stacked = pd.concat(per_tf_signals, axis=1)
    # Coerce non {-1, 0, +1, NaN} values defensively.
    cleaned = stacked.where(stacked.isin([-1.0, 0.0, 1.0]) | stacked.isna())
    counts = cleaned.apply(pd.Series.value_counts, axis=1)
    # `counts` has index [-1.0, 0.0, 1.0] if present. Build a tidy frame.
    long_vote = counts.get(1.0, pd.Series(0, index=cleaned.index))
    short_vote = counts.get(-1.0, pd.Series(0, index=cleaned.index))
    non_zero = long_vote + short_vote

    long_share = long_vote / non_zero.replace(0, np.nan)
    short_share = short_vote / non_zero.replace(0, np.nan)

    out = pd.Series(0, index=cleaned.index, dtype=int, name="mtf_consensus")
    take_long = (long_share > threshold) & (long_share >= short_share)
    take_short = (short_share > threshold) & (short_share > long_share)
    out = out.mask(take_long, 1).mask(take_short, -1)
    return out


def vol_target_size(target_vol: float, realized_vol: float,
                    nav: float = 1.0, price: float = 1.0,
                    floor: float = 0.0, cap: float = np.inf,
                    ) -> float:
    """Vol-target position size in *units of the underlying*.

    Definition (matches the V3 brief):
        units = nav * (target_vol / realized_vol) / price

    Returns 0 when any of ``nav``, ``price``, ``realized_vol`` is
    non-positive or non-finite — the strategy layer treats 0 as
    "do not trade this bar".

    Optional ``floor`` / ``cap`` clamp the output; default is open
    interval ``[0, +inf)``.
    """
    if not all(np.isfinite(x) for x in (target_vol, realized_vol, nav, price)):
        return 0.0
    if target_vol <= 0.0 or realized_vol <= 0.0 or nav <= 0.0 or price <= 0.0:
        return 0.0
    raw = nav * (target_vol / realized_vol) / price
    if raw < floor:
        return float(floor)
    if raw > cap:
        return float(cap)
    return float(raw)


def vol_target_size_series(target_vol: float,
                           realized_vol_series: pd.Series,
                           nav: float = 1.0,
                           price_series: pd.Series | None = None,
                           ) -> pd.Series:
    """Vectorised wrapper around ``vol_target_size`` for backtests.

    When ``price_series`` is None we assume the realised vol is already
    price-normalised (returns, not absolute price moves) and treat
    ``price = 1.0`` so the output is in units of NAV-fraction.

    NaN inputs propagate to NaN output.
    """
    if price_series is None:
        prices = pd.Series(1.0, index=realized_vol_series.index)
    else:
        prices = price_series

    out = pd.Series(np.nan, index=realized_vol_series.index, name="vol_target_size")
    for idx in out.index:
        rv = realized_vol_series.loc[idx]
        px = prices.loc[idx]
        if pd.isna(rv) or pd.isna(px):
            continue
        out.loc[idx] = vol_target_size(
            target_vol=target_vol,
            realized_vol=float(rv),
            nav=nav,
            price=float(px),
        )
    return out


# ===========================================================================
# Exposed public API — keep the import surface explicit.
# ===========================================================================

__all__ = [
    # constants
    "BARS_PER_YEAR_4H", "BARS_PER_YEAR_15M", "BARS_PER_YEAR_1M", "BARS_PER_YEAR_1H",
    "ADX_TREND_THRESHOLD", "RV_BREAKOUT_THRESHOLD_BPS", "RV_RANGE_THRESHOLD_BPS",
    "REGIME_LABELS",
    # V1
    "adx", "realized_vol_bps", "regime_router",
    # V2
    "obi_zscore", "vpvr_poc_proximity",
    # V3
    "mtf_consensus_signals", "vol_target_size", "vol_target_size_series",
]