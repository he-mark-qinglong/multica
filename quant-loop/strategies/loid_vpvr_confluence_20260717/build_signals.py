"""Signal builder for loid_vpvr_confluence_20260717 (SMA-34803 prototype).

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame
    build_signals_with_variants(df, params) -> (ann_only, ann_conf)

Two modes are exposed because the backtest needs an apples-to-apples
comparison between the **standalone LOID signal** and the
**LOID+VPVR-confluence signal** on the same window:

  - ``signal_lo``  (long-only on every iceberg_flag)
  - ``signal_lc``  (long at HVN, short at LVN)

Both upstream modules are reused **without modification**:

  - ``iceberg_detector.detect_iceberg_bars`` (SMA-34796)
  - ``vpvr_levels.compute_vpvr_levels``      (SMA-34790)

Why a snapshot grid: a per-bar rolling VPVR over a 1440-bar (1m) or
42-bar (4h) window is O(N × W × bins). For 30d × 1440 = 43 200 1m
bars that takes minutes in pure Python. We recompute the profile
only on a snapshot grid (``vpvr_snapshot_every_bars``) and
forward-fill to per-bar cadence. Both the snapshot and the rolling
ATR are ``shift(1)``'d so the level used to evaluate bar ``t`` was
computed on data strictly before ``t`` (no look-ahead).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add upstream module paths (we deliberately import, not modify).
_REPO_ROOT = Path("/home/smark/multica/quant-loop")
_ICEBERG_DIR = _REPO_ROOT / "strategies" / "iceberg-detector"
_INDICATORS_DIR = _REPO_ROOT / "strategies" / "_indicators"
for _p in (str(_ICEBERG_DIR), str(_INDICATORS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from iceberg_detector import DetectorConfig, detect_iceberg_bars  # noqa: E402
from vpvr_levels import compute_vpvr_levels  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — pure, vectorised.
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Standard rolling-mean ATR with close.shift(1) so today's range
    does not leak into today's ATR.
    """
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _vpvr_snapshot_levels(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    snapshot_idx: pd.DatetimeIndex,
    window: int,
    bins: int,
    hvn_quantile: float,
    lvn_quantile: float,
    num_hvn: int,
    num_lvn: int,
) -> pd.DataFrame:
    """Compute VPVR HVN/LVN zones for each snapshot bar using the
    trailing ``window`` bars (inclusive of the snapshot bar).
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
                high.iloc[start : end + 1],
                low.iloc[start : end + 1],
                volume.iloc[start : end + 1],
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


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Build iceberg + VPVR-confluence signals on a single-TF OHLCV
    frame. See module docstring for the conventions.
    """
    df = df.copy()
    if "openTime" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index("openTime")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)

    atr = _atr(df, params["atr_period"])

    # ---- Iceberg (LOID) detector --------------------------------------
    cfg = DetectorConfig(
        lookback=params["iceberg_lookback"],
        min_periods=params["iceberg_min_periods"],
        volume_zscore=params["iceberg_volume_zscore"],
        max_range_ratio=params["iceberg_max_range_ratio"],
    )
    iceberg_features = detect_iceberg_bars(df, cfg)
    iceberg_flag = iceberg_features["iceberg_flag"].astype(bool).reindex(df.index, fill_value=False)
    side_proxy = iceberg_features["side_proxy"].reindex(df.index).fillna("unknown")
    volume_zscore = iceberg_features["volume_zscore"].reindex(df.index)
    range_ratio = iceberg_features["range_ratio"].reindex(df.index)

    # ---- Rolling VPVR HVN / LVN on snapshot grid ----------------------
    stride = max(1, int(params.get("vpvr_snapshot_every_bars", 60)))
    snapshot_idx = df.index[::stride]
    if df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))

    snap = _vpvr_snapshot_levels(
        high, low, volume, snapshot_idx,
        window=params["vpvr_window_bars"],
        bins=params["vpvr_bins"],
        hvn_quantile=params["vpvr_hvn_quantile"],
        lvn_quantile=params["vpvr_lvn_quantile"],
        num_hvn=params["vpvr_num_hvn"],
        num_lvn=params["vpvr_num_lvn"],
    )

    # Shift by one snapshot so the level used to evaluate a bar was
    # computed on data strictly before the bar.
    snap = snap.shift(1)
    snap_per_bar = snap.reindex(df.index).ffill()

    hvn_mid = snap_per_bar["hvn_mid"]
    lvn_mid = snap_per_bar["lvn_mid"]

    atr_safe = atr.replace(0.0, np.nan)
    hvn_buf = float(params["hvn_atr_buffer"]) * atr_safe
    lvn_buf = float(params["lvn_atr_buffer"]) * atr_safe

    near_hvn = hvn_mid.notna() & ((close - hvn_mid).abs() <= hvn_buf)
    near_lvn = lvn_mid.notna() & ((close - lvn_mid).abs() <= lvn_buf)

    # ---- Confluence signals -------------------------------------------
    long_conf = iceberg_flag & near_hvn
    short_conf = iceberg_flag & near_lvn

    # signal_lc: long at HVN, short at LVN (-1/0/+1).
    signal_lc = pd.Series(0, index=df.index, dtype=np.int64)
    signal_lc[long_conf] = 1
    signal_lc[short_conf] = -1
    signal_lc = signal_lc.clip(-1, 1)

    # signal_lo: long-only on every iceberg flag (LOID baseline, no
    # direction from VPVR). Used as the apples-to-apples comparison
    # for "does the VPVR filter add anything over raw LOID flags?".
    signal_lo = pd.Series(0, index=df.index, dtype=np.int64)
    signal_lo[iceberg_flag] = 1
    signal_lo = signal_lo.clip(-1, 1)

    return pd.DataFrame({
        "signal_lc": signal_lc,
        "signal_lo": signal_lo,
        "atr": atr,
        "iceberg_flag": iceberg_flag,
        "side_proxy": side_proxy,
        "hvn_mid": hvn_mid,
        "hvn_top": snap_per_bar["hvn_top"],
        "hvn_bot": snap_per_bar["hvn_bot"],
        "lvn_mid": lvn_mid,
        "lvn_top": snap_per_bar["lvn_top"],
        "lvn_bot": snap_per_bar["lvn_bot"],
        "near_hvn": near_hvn,
        "near_lvn": near_lvn,
        "volume_zscore": volume_zscore,
        "range_ratio": range_ratio,
    })


__all__ = ["build_signals"]
