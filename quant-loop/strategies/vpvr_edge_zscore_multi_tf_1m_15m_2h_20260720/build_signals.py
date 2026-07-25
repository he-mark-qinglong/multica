"""Per-TF signal builders for vpvr_edge_zscore_multi_tf (SMA-34991).

Public API
----------
``build_signals_1m(df, params)``
    1m execution layer. Reads 15m edge + 2h trend direction on the
    aligned 1m index; produces no signal of its own (no microstructure
    alpha per spec — 1m is execution only). Returns ATR for sizing
    and the per-bar alignment of the 15m/2h state.

``build_signals_15m(df, params)``
    15m primary signal. VPVR-distribution zscore (rolling window of
    ``zscore_window_bars``=100 on POC/VAH/VAL/HVN/LVN means).
    Entry: |z_15m| > 2.0 sigma with directional confirmation
    (LVN-from-below for longs, HVN-from-above for shorts) and a
    15m POC-slope sign agreement. Returns ``signal_15m`` in
    {-1, 0, +1}, plus the per-bar zscore components and ATR.

``build_signals_2h(df, params)``
    2h trend filter. EMA(20) slope (bps/bar) + 2h POC side vs price.
    Returns ``trend_dir_2h`` in {-1, 0, +1} (0 = neutral, ±1 = trend),
    plus confirm_z (the 2h zscore used to confirm 15m zscore).
    Confirm gate: |z_2h| > 1.0 (per spec) for a 2h-side alignment
    with the 15m direction.

All three reuse the upstream module without modification:

  - ``~/multica/quant-loop/strategies/_indicators/vpvr_levels.py``
    (compute_vpvr_levels for POC / VAH / VAL / HVN / LVN per snapshot).

No-look-ahead invariant: rolling baselines are shifted by 1 bar,
snapshot grids are shifted by 1, the EMA(20) slope is computed from
``close.shift(1)`` so today's price cannot leak into today's
trend-direction vote.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from _shared.paths import quant_loop_root
except ImportError:  # bare-script mode
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import quant_loop_root

QUANT_LOOP = quant_loop_root()
_INDICATORS_DIR = QUANT_LOOP / "strategies" / "_indicators"
if str(_INDICATORS_DIR) not in sys.path:
    sys.path.insert(0, str(_INDICATORS_DIR))

from vpvr_levels import compute_vpvr_levels  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — pure, vectorised.
# ---------------------------------------------------------------------------

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Standard rolling-mean ATR with ``close.shift(1)`` so today's
    range cannot leak into today's ATR. Cycle-46 convention.
    """
    h = high.astype(np.float64)
    l = low.astype(np.float64)
    c = close.astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _ema(close: pd.Series, period: int) -> pd.Series:
    """EMA on ``close.shift(1)`` so the value used at bar ``t`` reflects
    only data strictly before ``t``.
    """
    return close.astype(np.float64).shift(1).ewm(span=period, adjust=False).mean()


def _vpvr_snapshot_levels(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    snapshot_idx: pd.DatetimeIndex,
    *,
    window: int,
    bins: int,
    hvn_quantile: float,
    lvn_quantile: float,
    num_hvn: int,
    num_lvn: int,
) -> pd.DataFrame:
    """Compute VPVR POC/VAH/VAL/HVN/LVN zones for each snapshot bar using
    the trailing ``window`` bars (inclusive of the snapshot bar).

    Returns a DataFrame indexed by ``snapshot_idx`` with columns
    ``poc``, ``vah``, ``val``, ``hvn_mid``, ``hvn_top``, ``hvn_bot``,
    ``lvn_mid``, ``lvn_top``, ``lvn_bot``.
    """
    pos = {ts: i for i, ts in enumerate(high.index)}
    out = {
        "poc": np.full(len(snapshot_idx), np.nan),
        "vah": np.full(len(snapshot_idx), np.nan),
        "val": np.full(len(snapshot_idx), np.nan),
        "hvn_mid": np.full(len(snapshot_idx), np.nan),
        "hvn_top": np.full(len(snapshot_idx), np.nan),
        "hvn_bot": np.full(len(snapshot_idx), np.nan),
        "lvn_mid": np.full(len(snapshot_idx), np.nan),
        "lvn_top": np.full(len(snapshot_idx), np.nan),
        "lvn_bot": np.full(len(snapshot_idx), np.nan),
    }
    for k, ts in enumerate(snapshot_idx):
        end = pos[ts]
        start = max(0, end - window + 1)
        if end - start + 1 < max(20, window // 4):
            continue
        try:
            profile = compute_vpvr_levels(
                high.iloc[start: end + 1],
                low.iloc[start: end + 1],
                volume.iloc[start: end + 1],
                num_bins=bins,
                hvn_quantile=hvn_quantile,
                lvn_quantile=lvn_quantile,
                num_hvn=num_hvn,
                num_lvn=num_lvn,
            )
        except (ValueError, ZeroDivisionError):
            continue
        out["poc"][k] = profile.poc_price
        out["vah"][k] = profile.vah_price
        out["val"][k] = profile.val_price
        if profile.hvn_zones:
            lo, hi, _ = profile.hvn_zones[0]
            out["hvn_bot"][k] = lo
            out["hvn_top"][k] = hi
            out["hvn_mid"][k] = 0.5 * (lo + hi)
        if profile.lvn_zones:
            lo, hi, _ = profile.lvn_zones[0]
            out["lvn_bot"][k] = lo
            out["lvn_top"][k] = hi
            out["lvn_mid"][k] = 0.5 * (lo + hi)
    return pd.DataFrame(out, index=snapshot_idx)


def _shifted_snapshot_per_bar(
    df: pd.DataFrame, snap: pd.DataFrame
) -> pd.DataFrame:
    """Shift VPVR snapshot by 1 and forward-fill to per-bar cadence.

    The level used to evaluate bar ``t`` was computed on data strictly
    before ``t``.
    """
    snap_s = snap.shift(1)
    return snap_s.reindex(df.index).ffill()


def _zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score: (x - rolling_mean(window)) / rolling_std(window).

    The baseline is shifted by 1 bar so the z at bar ``t`` reflects
    only data strictly before ``t``.
    """
    s = series.astype(np.float64)
    roll_mean = s.rolling(window, min_periods=max(20, window // 4)).mean().shift(1)
    roll_std = s.rolling(window, min_periods=max(20, window // 4)).std().shift(1)
    return (s - roll_mean) / roll_std.replace(0.0, np.nan)


def _deviation_zscore(close: pd.Series, anchor: pd.Series, window: int) -> pd.Series:
    """Rolling z-score of (close - anchor), where anchor is a level
    series (e.g. POC) that may itself be step-changing. This is the
    "VPVR-distribution deviation z-score": how many rolling-std devs
    is the current close away from the recent POC band?

    Both the rolling mean and std are computed on the (close - anchor)
    series, then shifted by 1 bar.
    """
    dev = (close.astype(np.float64) - anchor.astype(np.float64))
    roll_mean = dev.rolling(window, min_periods=max(20, window // 4)).mean().shift(1)
    roll_std = dev.rolling(window, min_periods=max(20, window // 4)).std().shift(1)
    return (dev - roll_mean) / roll_std.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# 15m — primary signal (VPVR-distribution zscore + POC-slope).
# ---------------------------------------------------------------------------

def build_signals_15m(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """15m primary signal: VPVR-distribution zscore.

    Construction
    ------------
    For each snapshot (every ``vpvr_snapshot_every_bars_15m``=16 bars)
    compute POC / VAH / VAL / HVN / LVN on the trailing
    ``vpvr_window_bars_15m``=180 bars. Forward-fill to per-bar cadence
    (shifted by 1 for no-look-ahead).

    The per-bar distribution zscore is the zscore of the *POC series*
    (the dominant structural price) on a rolling window of
    ``zscore_window_bars``=100. The trade direction is selected by:

      long  : z < -z_entry (price stretched below the recent POC mean)
              AND price near the LVN band from below
              AND 15m POC slope (last 5 bars) > 0
      short : z > +z_entry (price stretched above the recent POC mean)
              AND price near the HVN band from above
              AND 15m POC slope (last 5 bars) < 0

    Returns
    -------
    pd.DataFrame (index=df.index) with columns:
        ``poc``, ``vah``, ``val``, ``hvn_mid``, ``hvn_top``, ``hvn_bot``,
        ``lvn_mid``, ``lvn_top``, ``lvn_bot``,
        ``poc_slope_bps``  — 15m POC slope over last N bars (bps)
        ``z_15m``          — rolling z-score of the 15m POC series
        ``edge_15m``       — {-1, 0, +1} (LVN/HVN-confirmed directional edge)
        ``atr``            — 15m ATR (cycle-46 shifted)
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("15m df must have a DatetimeIndex")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)

    z_window = int(params.get("zscore_window_bars", 100))
    z_entry = float(params.get("zscore_entry_threshold_15m", 2.0))
    poc_slope_window = int(params.get("poc_slope_window_bars", 5))
    atr_period = int(params.get("atr_period_15m", 14))
    edge_buffer_atr = float(params.get("edge_buffer_atr_15m", 1.0))

    atr = _atr(high, low, close, atr_period)
    atr_safe = atr.replace(0.0, np.nan)

    # ---- VPVR snapshot per 16 bars ----
    stride = max(1, int(params.get("vpvr_snapshot_every_bars_15m", 16)))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))

    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=int(params.get("vpvr_window_bars_15m", 180)),
        bins=int(params.get("vpvr_bins", 24)),
        hvn_quantile=float(params.get("vpvr_hvn_quantile", 0.85)),
        lvn_quantile=float(params.get("vpvr_lvn_quantile", 0.15)),
        num_hvn=int(params.get("vpvr_num_hvn", 3)),
        num_lvn=int(params.get("vpvr_num_lvn", 3)),
    )
    snap_per_bar = _shifted_snapshot_per_bar(df, snap)
    poc = snap_per_bar["poc"]
    hvn_mid = snap_per_bar["hvn_mid"]
    hvn_top = snap_per_bar["hvn_top"]
    hvn_bot = snap_per_bar["hvn_bot"]
    lvn_mid = snap_per_bar["lvn_mid"]
    lvn_top = snap_per_bar["lvn_top"]
    lvn_bot = snap_per_bar["lvn_bot"]

    # ---- POC slope (bps per bar over last ``poc_slope_window``) ----
    # Use shifted POC (1 bar ahead of the signal use) so the slope
    # used at bar ``t`` is computed strictly from prior bars.
    poc_shifted = poc.shift(1)
    poc_slope_abs = poc_shifted.diff(poc_slope_window)
    poc_slope_bps = (poc_slope_abs / poc_shifted.replace(0.0, np.nan)) * 10000.0

    # ---- Rolling z-score of (close - POC) deviation ----
    # This is the "VPVR-distribution z-score": how far is price
    # stretched from the recent POC band, in rolling-std units.
    # z < -z_entry -> close stretched BELOW POC (mean-reversion long).
    # z > +z_entry -> close stretched ABOVE POC (mean-reversion short).
    z_15m = _deviation_zscore(close, poc_shifted, z_window)

    # ---- Edge detection ----
    buf = edge_buffer_atr * atr_safe
    near_lvn = lvn_mid.notna() & ((close - lvn_mid).abs() <= buf)
    near_hvn = hvn_mid.notna() & ((close - hvn_mid).abs() <= buf)
    # LVN touched from below: low of bar reached up into the LVN band
    # (LVN acting as support from below). Used as confluence for long.
    lvn_touched_from_below = lvn_top.notna() & (low.shift(1) <= lvn_top)
    # HVN touched from above: high of bar reached down into the HVN band
    # (HVN acting as resistance from above). Confluence for short.
    hvn_touched_from_above = hvn_bot.notna() & (high.shift(1) >= hvn_bot)

    # Primary signal: stretched from POC + POC slope agrees.
    long_primary = (
        z_15m.notna()
        & (z_15m < -z_entry)
        & poc_slope_bps.notna()
        & (poc_slope_bps > 0)
    )
    short_primary = (
        z_15m.notna()
        & (z_15m > z_entry)
        & poc_slope_bps.notna()
        & (poc_slope_bps < 0)
    )
    # LVN/HVN confluence filter (relaxed: either near or touched).
    long_confluence = near_lvn | lvn_touched_from_below
    short_confluence = near_hvn | hvn_touched_from_above

    long_ok = long_primary & long_confluence
    short_ok = short_primary & short_confluence

    edge_15m = pd.Series(0, index=df.index, dtype=np.int64)
    edge_15m = edge_15m.mask(long_ok, 1)
    edge_15m = edge_15m.mask(short_ok, -1)

    return pd.DataFrame({
        "poc": poc,
        "vah": snap_per_bar["vah"],
        "val": snap_per_bar["val"],
        "hvn_mid": hvn_mid,
        "hvn_top": hvn_top,
        "hvn_bot": hvn_bot,
        "lvn_mid": lvn_mid,
        "lvn_top": lvn_top,
        "lvn_bot": lvn_bot,
        "poc_slope_bps": poc_slope_bps,
        "z_15m": z_15m,
        "edge_15m": edge_15m,
        "atr": atr,
    })


# ---------------------------------------------------------------------------
# 2h — trend filter (EMA(20) slope + POC side).
# ---------------------------------------------------------------------------

def build_signals_2h(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """2h trend filter: EMA(20) slope + POC side vs price.

    Construction
    ------------
    Compute the trailing EMA(20) on shifted close. Slope in bps per
    bar = (EMA - EMA.shift(N)) / EMA.shift(N) * 10_000, where N is
    derived from the spec's ``trend_slope_min_bps_per_bar`` (use N=20
    for a 20-bar-per-bar-of-2h trend window).

    2h POC side: price > 2h POC = "above side" (long bias), price < 2h
    POC = "below side" (short bias).

    Returns
    -------
    pd.DataFrame (index=df.index) with columns:
        ``poc_2h``              — 2h POC series
        ``ema_2h``              — EMA(20) on shifted close
        ``trend_slope_bps``     — EMA slope in bps per bar
        ``trend_dir_2h``        — {-1, 0, +1} per spec filter
        ``z_2h``                — 2h zscore (for confirm gate)
        ``atr``                 — 2h ATR
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("2h df must have a DatetimeIndex")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)

    ema_period = int(params.get("trend_ema_period", 20))
    slope_min_bps = float(params.get("trend_slope_min_bps_per_bar", 5.0))
    z_window = int(params.get("zscore_window_bars", 100))
    atr_period = int(params.get("atr_period_2h", 14))

    atr = _atr(high, low, close, atr_period)

    # ---- 2h VPVR snapshot (POC only for trend-side classification) ----
    stride = max(1, int(params.get("vpvr_snapshot_every_bars_2h", 6)))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))

    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=int(params.get("vpvr_window_bars_2h", 180)),
        bins=int(params.get("vpvr_bins", 24)),
        hvn_quantile=float(params.get("vpvr_hvn_quantile", 0.85)),
        lvn_quantile=float(params.get("vpvr_lvn_quantile", 0.15)),
        num_hvn=int(params.get("vpvr_num_hvn", 3)),
        num_lvn=int(params.get("vpvr_num_lvn", 3)),
    )
    snap_per_bar = _shifted_snapshot_per_bar(df, snap)
    poc_2h = snap_per_bar["poc"]

    # ---- EMA(20) on shifted close ----
    ema_2h = _ema(close, ema_period)

    # ---- Trend slope (bps per bar over 20 bars = ~40h of 2h bars) ----
    ema_shifted = ema_2h.shift(1)
    slope_window = ema_period  # 20 bars of 2h = 40h
    slope_abs = ema_shifted.diff(slope_window)
    trend_slope_bps = (slope_abs / ema_shifted.replace(0.0, np.nan)) * 10000.0 / slope_window

    # ---- 2h zscore (for confirm gate) ----
    z_2h = _deviation_zscore(close, poc_2h.shift(1), z_window)

    # ---- trend_dir: ±1 if EMA slope + POC side agree, else 0 ----
    poc_above = close > poc_2h
    poc_below = close < poc_2h
    long_trend = (trend_slope_bps > slope_min_bps) & poc_above
    short_trend = (trend_slope_bps < -slope_min_bps) & poc_below

    trend_dir_2h = pd.Series(0, index=df.index, dtype=np.int64)
    trend_dir_2h = trend_dir_2h.mask(long_trend, 1)
    trend_dir_2h = trend_dir_2h.mask(short_trend, -1)

    return pd.DataFrame({
        "poc_2h": poc_2h,
        "ema_2h": ema_2h,
        "trend_slope_bps": trend_slope_bps,
        "trend_dir_2h": trend_dir_2h,
        "z_2h": z_2h,
        "atr": atr,
    })


# ---------------------------------------------------------------------------
# 1m — execution layer.
# ---------------------------------------------------------------------------

def build_signals_1m(
    df: pd.DataFrame,
    params: dict,
    *,
    sig_15m: Optional[pd.DataFrame] = None,
    sig_2h: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """1m execution layer. Computes ATR for sizing + aligns the 15m
    edge and the 2h trend direction on the 1m bar index.

    The per-bar ``decision`` is:
        decision = edge_15m   if trend_dir_2h ==  edge_15m  (with confirm)
        decision = 0           otherwise

    Confirmation gate: |z_2h| > ``zscore_confirm_threshold_2h`` (1.0
    per spec) on the same side as the 15m edge.

    Returns
    -------
    pd.DataFrame (index=df.index) with columns:
        ``atr``           — 1m ATR (cycle-46 shifted)
        ``edge_15m_a``    — 15m edge ffill-aligned to 1m
        ``trend_dir_a``   — 2h trend direction ffill-aligned
        ``z_2h_a``        — 2h zscore ffill-aligned
        ``decision``      — final {-1, 0, +1} per bar
        ``conviction``    — "" or "high" (15m and 2h agree)
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("1m df must have a DatetimeIndex")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)

    atr_period = int(params.get("atr_period_15m", 14))
    atr = _atr(high, low, close, atr_period)

    if sig_15m is None or sig_2h is None:
        raise ValueError(
            "build_signals_1m requires sig_15m and sig_2h frames for "
            "edge / trend alignment — no signal is generated at 1m per spec."
        )

    # Align 15m and 2h signal frames to the 1m index via ffill.
    edge_15m_a = sig_15m["edge_15m"].reindex(df.index, method="ffill").fillna(0).astype(np.int64)
    trend_dir_a = sig_2h["trend_dir_2h"].reindex(df.index, method="ffill").fillna(0).astype(np.int64)
    z_2h_a = sig_2h["z_2h"].reindex(df.index, method="ffill").astype(np.float64)
    z_confirm_th = float(params.get("zscore_confirm_threshold_2h", 1.0))

    # Confirm: 2h zscore is on the same side as the 15m edge and
    # |z_2h| exceeds the confirm threshold. Otherwise the trend is
    # ambiguous and the decision is 0.
    confirm_long = (trend_dir_a == 1) & (z_2h_a >= z_confirm_th)
    confirm_short = (trend_dir_a == -1) & (z_2h_a <= -z_confirm_th)
    trend_confirmed = confirm_long | confirm_short

    decision = pd.Series(0, index=df.index, dtype=np.int64)
    long_ok = (edge_15m_a == 1) & confirm_long
    short_ok = (edge_15m_a == -1) & confirm_short
    decision = decision.mask(long_ok, 1)
    decision = decision.mask(short_ok, -1)

    conviction = pd.Series("", index=df.index, dtype=object)
    # "high" = 15m and 2h agree AND 2h trend is also confirmed.
    high_conv = (
        ((edge_15m_a == 1) & (trend_dir_a == 1) & confirm_long)
        | ((edge_15m_a == -1) & (trend_dir_a == -1) & confirm_short)
    )
    conviction = conviction.mask(high_conv, "high")

    return pd.DataFrame({
        "atr": atr,
        "edge_15m_a": edge_15m_a,
        "trend_dir_a": trend_dir_a,
        "z_2h_a": z_2h_a,
        "decision": decision,
        "conviction": conviction,
    })


# ---------------------------------------------------------------------------
# Master dispatcher (preserves the impl_vpvr_multi_tf_funding signature).
# ---------------------------------------------------------------------------

def build_signals(
    df_1m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_2h: pd.DataFrame,
    params: dict,
) -> dict:
    """Run all three per-TF builders in the canonical order.

    Returns a dict ``{"1m": sig_1m, "15m": sig_15m, "2h": sig_2h}``.
    """
    sig_15m = build_signals_15m(df_15m, params)
    sig_2h = build_signals_2h(df_2h, params)
    sig_1m = build_signals_1m(df_1m, params, sig_15m=sig_15m, sig_2h=sig_2h)
    return {"1m": sig_1m, "15m": sig_15m, "2h": sig_2h}


__all__ = [
    "build_signals_1m",
    "build_signals_15m",
    "build_signals_2h",
    "build_signals",
]