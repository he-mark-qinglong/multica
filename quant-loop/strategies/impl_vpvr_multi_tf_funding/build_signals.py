"""Per-TF edge builders for vpvr_multi_tf_funding (SMA-34989).

Public API
----------
``build_signals_1m(df, params)``
    1m microstructure edge: LOID-confirmed iceberg bars x tick-level
    VPVR HVN/LVN proximity. Returns ``micro_long``, ``micro_short``,
    helper columns (``atr``, ``hvn_*``, ``lvn_*``, ``cluster_active``,
    ``side_bias``).

``build_signals_15m(df, params)``
    15m short-term edge: funding carry (rate > threshold) x HVN
    support. Returns ``carry_long`` (carry_short = 0 per cycle-46
    family exhaustion rule), plus HVN levels and ATR.

``build_signals_4h(df, params)``
    4h structural edge: funding-regime classifier (TREND_UP,
    TREND_DOWN, MEAN_REVERT, BLOCKED) x structural HVN/LVN zones.
    Returns ``struct_long``, ``struct_short``, ``regime``,
    ``funding_div``, ``z_funding``.

All three reuse the upstream modules **without modification**:

- ``/home/smark/trading/factors/iceberg_detector/iceberg_detector.py``
  (SMA-34796 / SMA-34910 LOID family)
- ``/_indicators/vpvr_levels.detect_vpvr_levels`` /
  ``compute_vpvr_levels`` (SMA-34790)

No-look-ahead invariant: rolling baselines are shifted by 1 bar,
snapshot grids are shifted by 1, funding is ``ffill``-ed onto each
TF's bar index and ``shift(1)``-ed before any threshold comparison.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Resolve upstream module paths (we deliberately import, not modify).
# ---------------------------------------------------------------------------
QUANT_LOOP = Path("/home/smark/multica/quant-loop")
_INDICATORS_DIR = QUANT_LOOP / "strategies" / "_indicators"
_ICEBERG_DIR = Path("/home/smark/trading/factors/iceberg_detector")
for _p in (str(_INDICATORS_DIR), str(_ICEBERG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from iceberg_detector import DetectorConfig, detect_iceberg_bars  # noqa: E402
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
    """Compute VPVR HVN/LVN zones for each snapshot bar using the
    trailing ``window`` bars (inclusive of the snapshot bar).

    Returns a DataFrame indexed by ``snapshot_idx`` with columns
    ``hvn_mid``, ``hvn_top``, ``hvn_bot``, ``lvn_mid``, ``lvn_top``,
    ``lvn_bot``.
    """
    pos = {ts: i for i, ts in enumerate(high.index)}
    out = {
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
        hvn_zones = profile.hvn_zones
        lvn_zones = profile.lvn_zones
        if hvn_zones:
            lo, hi, _ = hvn_zones[0]
            out["hvn_bot"][k] = lo
            out["hvn_top"][k] = hi
            out["hvn_mid"][k] = 0.5 * (lo + hi)
        if lvn_zones:
            lo, hi, _ = lvn_zones[0]
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


# ---------------------------------------------------------------------------
# 1m — microstructure (LOID x tick-level VPVR).
# ---------------------------------------------------------------------------

def build_signals_1m(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """1m edge: LOID-confirmed iceberg x tick-level VPVR HVN/LVN.

    Required df columns: ``open``, ``high``, ``low``, ``close``,
    ``volume`` (DatetimeIndex in UTC). Funding column optional and
    unused on this TF (carry lives at 15m/4h).

    Required params keys (with sensible defaults):
        ``iceberg_lookback``            (default 60)
        ``iceberg_min_periods``         (default 30)
        ``iceberg_volume_zscore``       (default 3.0)
        ``iceberg_max_range_ratio``     (default 0.75)
        ``vpvr_window_bars``            (default 240 = 4h @ 1m)
        ``vpvr_snapshot_every_bars``    (default 30)
        ``vpvr_bins``                   (default 24)
        ``vpvr_hvn_quantile``           (default 0.85)
        ``vpvr_lvn_quantile``           (default 0.15)
        ``vpvr_num_hvn``                (default 3)
        ``vpvr_num_lvn``                (default 3)
        ``atr_period``                  (default 14)
        ``hvn_atr_buffer``              (default 0.5)
        ``lvn_atr_buffer``              (default 0.5)

    Returns
    -------
    pd.DataFrame (index=df.index) with columns:
        ``iceberg_flag``     (bool)
        ``side_proxy``       (object)
        ``cluster_active``   (bool)  -- within an active LOID cluster
        ``hvn_mid``, ``hvn_top``, ``hvn_bot``, ``lvn_mid``, ``lvn_top``, ``lvn_bot``
        ``near_hvn``, ``near_lvn``
        ``micro_long``, ``micro_short``  -- per-bar edge output (0/1)
        ``atr``
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
    volume = df["volume"].astype(np.float64)

    # ATR (cycle-46, shifted).
    atr = _atr(high, low, close, int(params.get("atr_period", 14)))

    # ---- LOID / iceberg detector ----
    cfg = DetectorConfig(
        lookback=int(params.get("iceberg_lookback", 60)),
        min_periods=int(params.get("iceberg_min_periods", 30)),
        volume_zscore=float(params.get("iceberg_volume_zscore", 3.0)),
        max_range_ratio=float(params.get("iceberg_max_range_ratio", 0.75)),
    )
    iceberg_feats = detect_iceberg_bars(df, cfg)
    iceberg_flag = iceberg_feats["iceberg_flag"].astype(bool).reindex(
        df.index, fill_value=False
    )
    side_proxy = iceberg_feats["side_proxy"].reindex(df.index).fillna("unknown")

    # cluster_active: any bar whose volume_zscore exceeds the threshold
    # is treated as inside the cluster window. The detector returns a
    # bar-level flag rather than a windowed event stream in this build,
    # so we interpret cluster_active[t] = iceberg_flag[t] (each flagged
    # bar is the cluster representative in the per-bar signal model).
    cluster_active = iceberg_flag

    # ---- Rolling VPVR HVN/LVN ----
    stride = max(1, int(params.get("vpvr_snapshot_every_bars", 30)))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))

    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=int(params.get("vpvr_window_bars", 240)),
        bins=int(params.get("vpvr_bins", 24)),
        hvn_quantile=float(params.get("vpvr_hvn_quantile", 0.85)),
        lvn_quantile=float(params.get("vpvr_lvn_quantile", 0.15)),
        num_hvn=int(params.get("vpvr_num_hvn", 3)),
        num_lvn=int(params.get("vpvr_num_lvn", 3)),
    )
    snap_per_bar = _shifted_snapshot_per_bar(df, snap)

    hvn_mid = snap_per_bar["hvn_mid"]
    lvn_mid = snap_per_bar["lvn_mid"]

    atr_safe = atr.replace(0.0, np.nan)
    hvn_buf = float(params.get("hvn_atr_buffer", 0.5)) * atr_safe
    lvn_buf = float(params.get("lvn_atr_buffer", 0.5)) * atr_safe
    near_hvn = hvn_mid.notna() & ((close - hvn_mid).abs() <= hvn_buf)
    near_lvn = lvn_mid.notna() & ((close - lvn_mid).abs() <= lvn_buf)

    # ---- Per-bar edge output (Rule 1 in SPEC) ----
    # micro_long: cluster_active & near_hvn & side in {buy_absorption, mixed}
    # micro_short: cluster_active & near_lvn & side in {sell_absorption, mixed}
    side_long_ok = side_proxy.isin({"buy_absorption", "mixed"})
    side_short_ok = side_proxy.isin({"sell_absorption", "mixed"})
    micro_long = (cluster_active & near_hvn & side_long_ok).astype(int)
    micro_short = (cluster_active & near_lvn & side_short_ok).astype(int)

    return pd.DataFrame({
        "iceberg_flag": iceberg_flag,
        "side_proxy": side_proxy,
        "cluster_active": cluster_active,
        "hvn_mid": hvn_mid,
        "hvn_top": snap_per_bar["hvn_top"],
        "hvn_bot": snap_per_bar["hvn_bot"],
        "lvn_mid": lvn_mid,
        "lvn_top": snap_per_bar["lvn_top"],
        "lvn_bot": snap_per_bar["lvn_bot"],
        "near_hvn": near_hvn,
        "near_lvn": near_lvn,
        "micro_long": micro_long,
        "micro_short": micro_short,
        "atr": atr,
    })


# ---------------------------------------------------------------------------
# 15m — funding-carry x HVN support.
# ---------------------------------------------------------------------------

def build_signals_15m(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """15m edge: funding-carry-asym x HVN support.

    Required df columns: ``open``, ``high``, ``low``, ``close``,
    ``volume``, ``funding`` (DatetimeIndex in UTC). Funding is the
    8h-event rate ffilled onto the bar index; this wrapper does the
    cycle-46 ``shift(1)`` for no-look-ahead.

    Returns
    -------
    pd.DataFrame (index=df.index) with columns:
        ``funding``                   (float, NaN-aware)
        ``funding_above_threshold``   (bool)
        ``hvn_mid``, ``hvn_top``, ``hvn_bot``
        ``support_zone``              (bool) -- near HVN
        ``carry_long``                (int, 0/1)
        ``atr``

    Note
    ----
    ``carry_short`` is **always 0** in v1 per the cycle-46 funding-
    carry family exhaustion rule. The 4h TREND_DOWN leg (when used)
    shorts through the 4h edge alone, not through 15m.
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("15m df must have a DatetimeIndex")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    if "funding" not in df.columns:
        raise ValueError("15m df must include a 'funding' column")

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    funding_raw = df["funding"].astype(np.float64)
    # No-look-ahead: bar ``t`` sees the funding rate paid strictly
    # before bar ``t``'s open.
    funding = funding_raw.shift(1)

    funding_threshold = float(params.get("funding_threshold", 0.0003))
    proximity_atr = float(params.get("proximity_atr", 1.0))
    atr_period = int(params.get("atr_period", 14))

    atr = _atr(high, low, close, atr_period)
    atr_safe = atr.replace(0.0, np.nan)

    funding_above_threshold = funding > funding_threshold

    # ---- VPVR HVN support zones ----
    stride = max(1, int(params.get("vpvr_snapshot_every_bars", 16)))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))
    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=int(params.get("vpvr_window_bars", 180)),
        bins=int(params.get("vpvr_bins", 24)),
        hvn_quantile=float(params.get("vpvr_hvn_quantile", 0.85)),
        lvn_quantile=float(params.get("vpvr_lvn_quantile", 0.15)),
        num_hvn=int(params.get("vpvr_num_hvn", 3)),
        num_lvn=int(params.get("vpvr_num_lvn", 3)),
    )
    snap_per_bar = _shifted_snapshot_per_bar(df, snap)
    hvn_mid = snap_per_bar["hvn_mid"]
    hvn_top = snap_per_bar["hvn_top"]
    hvn_bot = snap_per_bar["hvn_bot"]

    support_zone = (
        hvn_mid.notna()
        & ((close - hvn_mid).abs() <= proximity_atr * atr_safe)
    ).fillna(False)

    carry_long = (funding_above_threshold & support_zone).astype(int)

    return pd.DataFrame({
        "funding": funding,
        "funding_above_threshold": funding_above_threshold.fillna(False),
        "hvn_mid": hvn_mid,
        "hvn_top": hvn_top,
        "hvn_bot": hvn_bot,
        "support_zone": support_zone,
        "carry_long": carry_long,
        "carry_short": pd.Series(0, index=df.index, dtype=np.int64),
        "atr": atr,
    })


# ---------------------------------------------------------------------------
# 4h — structural regime (HVN/LVN x funding regime).
# ---------------------------------------------------------------------------

def build_signals_4h(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """4h edge: funding regime classifier x structural VPVR zones.

    Required df columns: ``open``, ``high``, ``low``, ``close``,
    ``volume``, ``funding`` (DatetimeIndex in UTC).

    Funding regime labels:
        TREND_UP      — z_funding > +1.5 & vol_regime_ok
        TREND_DOWN    — z_funding < -1.5 & vol_regime_ok
        MEAN_REVERT   — |z_funding| <= 1.5 & vol_regime_ok
        BLOCKED       — vol_regime_ok == False (funding div too choppy)

    Returns
    -------
    pd.DataFrame (index=df.index) with columns:
        ``funding``, ``funding_div`` (24h = 6 x 4h lookback),
        ``z_funding``, ``vol_regime_ok``, ``regime``,
        ``hvn_mid``, ``lvn_mid``, ``nearest_hvn_band``, ``nearest_lvn_band``,
        ``struct_long``, ``struct_short``, ``atr``.
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("4h df must have a DatetimeIndex")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    if "funding" not in df.columns:
        raise ValueError("4h df must include a 'funding' column")

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    funding_raw = df["funding"].astype(np.float64)
    funding = funding_raw.shift(1)

    z_threshold = float(params.get("regime_z_threshold", 1.5))
    vol_cap_bps = float(params.get("regime_vol_cap_bps", 15.0))
    z_lookback = int(params.get("z_lookback_bars", 180))
    atr_period = int(params.get("atr_period", 14))
    proximity_atr = float(params.get("proximity_atr", 1.0))

    atr = _atr(high, low, close, atr_period)
    atr_safe = atr.replace(0.0, np.nan)

    # Funding 24h divergence (6 x 4h bars).
    funding_div = funding - funding.shift(6)

    # Rolling mean / std for z-scoring on the 4h funding-div series.
    roll_mean = funding_div.rolling(z_lookback, min_periods=max(20, z_lookback // 4)).mean()
    roll_std = funding_div.rolling(z_lookback, min_periods=max(20, z_lookback // 4)).std()
    # shift(1) so the z used at bar t was computed strictly before t.
    roll_mean = roll_mean.shift(1)
    roll_std = roll_std.shift(1)
    z_funding = (funding_div - roll_mean) / roll_std.replace(0.0, np.nan)

    # Vol regime ok: rolling std of funding_div <= 15 bps (= 0.0015).
    vol_roll_std = funding_div.rolling(z_lookback, min_periods=max(20, z_lookback // 4)).std().shift(1)
    vol_regime_ok = vol_roll_std <= (vol_cap_bps / 10000.0)

    regime = pd.Series("BLOCKED", index=df.index, dtype=object)
    regime[vol_regime_ok & (z_funding > z_threshold)] = "TREND_UP"
    regime[vol_regime_ok & (z_funding < -z_threshold)] = "TREND_DOWN"
    regime[vol_regime_ok & (z_funding.abs() <= z_threshold)] = "MEAN_REVERT"

    # ---- VPVR structural zones (rolling 4h ~30d) ----
    stride = max(1, int(params.get("vpvr_snapshot_every_bars", 6)))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))
    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=int(params.get("vpvr_window_bars", 180)),
        bins=int(params.get("vpvr_bins", 24)),
        hvn_quantile=float(params.get("vpvr_hvn_quantile", 0.85)),
        lvn_quantile=float(params.get("vpvr_lvn_quantile", 0.15)),
        num_hvn=int(params.get("vpvr_num_hvn", 3)),
        num_lvn=int(params.get("vpvr_num_lvn", 3)),
    )
    snap_per_bar = _shifted_snapshot_per_bar(df, snap)
    hvn_mid = snap_per_bar["hvn_mid"]
    lvn_mid = snap_per_bar["lvn_mid"]

    buf = proximity_atr * atr_safe
    near_hvn = hvn_mid.notna() & ((close - hvn_mid).abs() <= buf)
    near_lvn = lvn_mid.notna() & ((close - lvn_mid).abs() <= buf)

    # Long allowed under TREND_UP or MEAN_REVERT; short under TREND_DOWN;
    # BLOCKED zeros both.
    long_ok_regime = regime.isin(["TREND_UP", "MEAN_REVERT"])
    short_ok_regime = regime.eq("TREND_DOWN")
    struct_long = (long_ok_regime & near_hvn).astype(int)
    struct_short = (short_ok_regime & near_lvn).astype(int)

    return pd.DataFrame({
        "funding": funding,
        "funding_div": funding_div,
        "z_funding": z_funding,
        "vol_regime_ok": vol_regime_ok,
        "regime": regime,
        "hvn_mid": hvn_mid,
        "lvn_mid": lvn_mid,
        "nearest_hvn_band": near_hvn,
        "nearest_lvn_band": near_lvn,
        "struct_long": struct_long,
        "struct_short": struct_short,
        "atr": atr,
    })


# ---------------------------------------------------------------------------
# Master dispatcher.
# ---------------------------------------------------------------------------

def build_signals(df: pd.DataFrame, tf: str, params: dict) -> pd.DataFrame:
    """Convenience dispatcher: route to the per-TF builder."""
    if tf == "1m":
        return build_signals_1m(df, params)
    if tf == "15m":
        return build_signals_15m(df, params)
    if tf == "4h":
        return build_signals_4h(df, params)
    raise ValueError(f"unsupported tf {tf!r} (expected 1m/15m/4h)")


__all__ = [
    "build_signals_1m",
    "build_signals_15m",
    "build_signals_4h",
    "build_signals",
]
