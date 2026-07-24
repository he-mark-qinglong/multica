"""Iceberg + large-order detector for SMA-34992 (LOID V4).

First attempt with REAL aggTrades + `is_buyer_maker` field. Prior iterations
(V1/V2/V3/V4 in SMA-34929) failed because the staged OHLCV had no taker-side
field, so direction was blind long (88 trades / 13.6% WR / PF 0.075 on 1m 90d).

This module is pure-Python / numpy / pandas only — no I/O. Caller loads the
hive-partitioned parquet, passes the DataFrame to `detect()`, gets back a
dict with per-minute composite and deterministic stats.

Public API:
    signed_side(is_buyer_maker: np.ndarray) -> np.ndarray  # +1 / -1 / 0
    DetectorConfig(...)                                   # dataclass of knobs
    detect(trades: pd.DataFrame, cfg: DetectorConfig) -> {
        "per_trade_z": np.ndarray,
        "composite_by_minute": pd.DataFrame,
        "stats": dict,
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_COLS = ["ts", "price", "qty", "is_buyer_maker", "first_id", "last_id"]


class SchemaError(ValueError):
    pass


def signed_side(is_buyer_maker: np.ndarray) -> np.ndarray:
    """+1 if TAKER bought (is_buyer_maker=False), -1 if TAKER sold (True), 0 for NaN."""
    out = np.where(is_buyer_maker == False, 1, -1)  # noqa: E712 — be strict about bool
    return out.astype(np.int8)


@dataclass(frozen=True)
class DetectorConfig:
    lookback: int = 1000            # rolling z-score window in TRADES
    large_z: float = 3.0            # z threshold for "large" trade
    whale_z: float = 5.0            # z threshold for "whale" trade
    cluster_min_trades: int = 5     # minimum cluster size
    cluster_cv_max: float = 0.10   # max coefficient of variation to count as iceberg
    cluster_signal_weight: float = 2.0  # weight per cluster in composite


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def _validate_schema(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise SchemaError(f"missing required columns: {missing}")
    extra = [c for c in df.columns if c not in REQUIRED_COLS]
    if extra:
        raise SchemaError(f"unexpected columns: {extra}")
    if df["qty"].isna().any() or (df["qty"] <= 0).any():
        raise SchemaError("qty must be > 0 everywhere (no NaN, no zero/negative)")
    if df["is_buyer_maker"].dtype != bool:
        raise SchemaError(f"is_buyer_maker must be bool dtype, got {df['is_buyer_maker'].dtype}")
    # ts must be integer ms (timestamp[ms,UTC] from parquet reads OK; numpy int64 OK)
    if not np.issubdtype(df["ts"].dtype, np.integer):
        raise SchemaError(f"ts must be integer ms, got {df['ts'].dtype}")


# ---------------------------------------------------------------------------
# Rolling z-score (shifted: row i uses rows i-lookback .. i-1)
# ---------------------------------------------------------------------------

def _rolling_zscore_shifted(qty: np.ndarray, lookback: int) -> np.ndarray:
    """Per-trade z-score using only the PRECEDING `lookback` trades (no look-ahead).

    Returns float array of length len(qty). First `lookback` entries are NaN.
    """
    n = len(qty)
    out = np.full(n, np.nan, dtype=np.float64)
    if n <= lookback:
        return out
    qty = qty.astype(np.float64)
    # Cumulative sums so window stats are O(1) per row.
    csum = np.cumsum(qty, dtype=np.float64)
    csum2 = np.cumsum(qty * qty, dtype=np.float64)
    for i in range(lookback, n):
        s = csum[i - 1] - (csum[i - lookback - 1] if i - lookback - 1 >= 0 else 0.0)
        s2 = csum2[i - 1] - (csum2[i - lookback - 1] if i - lookback - 1 >= 0 else 0.0)
        mean = s / lookback
        var = (s2 / lookback) - mean * mean
        std = float(np.sqrt(var)) if var > 0 else 0.0
        if std <= 1e-12:
            out[i] = np.nan
        else:
            out[i] = (qty[i] - mean) / std
    return out


# ---------------------------------------------------------------------------
# Iceberg clusters
# ---------------------------------------------------------------------------

def _detect_clusters(
    df: pd.DataFrame, cfg: DetectorConfig
) -> pd.DataFrame:
    """Find same-millisecond+price clusters with ≥cfg.cluster_min_trades and CV≤cfg.cluster_cv_max.

    Returns DataFrame columns [ts, price, n_trades, mean_qty, std_qty, cv, signed_qty, side].
    Empty DataFrame if no clusters.
    """
    if len(df) == 0:
        return pd.DataFrame(
            columns=["ts", "price", "n_trades", "mean_qty", "std_qty", "cv", "signed_qty", "side"]
        )
    grp = df.groupby(["ts", "price"], sort=False)
    agg = grp.agg(
        n_trades=("qty", "size"),
        mean_qty=("qty", "mean"),
        std_qty=("qty", "std"),
        signed_qty=("signed_qty", "sum"),
    ).reset_index()
    # std with 1 obs is NaN → fill 0
    agg["std_qty"] = agg["std_qty"].fillna(0.0)
    agg["cv"] = agg["std_qty"] / agg["mean_qty"].replace(0, np.nan)
    agg["cv"] = agg["cv"].fillna(0.0)
    clusters = agg[
        (agg["n_trades"] >= cfg.cluster_min_trades) & (agg["cv"] <= cfg.cluster_cv_max)
    ].copy()
    clusters["side"] = np.where(clusters["signed_qty"] > 0, 1, -1)
    return clusters


# ---------------------------------------------------------------------------
# Per-minute composite
# ---------------------------------------------------------------------------

def _composite_by_minute(
    df: pd.DataFrame,
    z: np.ndarray,
    side: np.ndarray,
    clusters: pd.DataFrame,
    cfg: DetectorConfig,
) -> pd.DataFrame:
    """Build per-minute composite DataFrame.

    composite[minute] = sum(side[i] * z[i] for large trades in minute)
                      + sum(side[c] * cluster_signal_weight for clusters in minute)

    Minute key = floor(ts / 60000) since session start (int). Session start
    is the min ts in the input.
    """
    if len(df) == 0:
        return pd.DataFrame(columns=["minute_offset", "minute_ts_utc", "composite", "n_large", "n_whale", "n_iceberg"])

    session_start_ms = int(df["ts"].iloc[0])
    minute_offset = ((df["ts"].astype("int64") - session_start_ms) // 60000).astype("int64")
    minute_ts_utc = pd.to_datetime(session_start_ms + minute_offset * 60000, unit="ms", utc=True)

    is_large = (np.abs(z) >= cfg.large_z) & np.isfinite(z)
    is_whale = (np.abs(z) >= cfg.whale_z) & np.isfinite(z)
    large_contrib = np.where(is_large, side * np.nan_to_num(z, nan=0.0), 0.0)

    work = pd.DataFrame(
        {
            "minute_offset": minute_offset.values,
            "minute_ts_utc": minute_ts_utc.values,
            "large_contrib": large_contrib,
            "n_large": is_large.astype(int),
            "n_whale": is_whale.astype(int),
        }
    )

    # Aggregate large-term contributions
    large_agg = (
        work.groupby(["minute_offset", "minute_ts_utc"], as_index=False)
        .agg(composite=("large_contrib", "sum"), n_large=("n_large", "sum"), n_whale=("n_whale", "sum"))
    )

    # Iceberg contributions
    if len(clusters) > 0:
        iceberg_minute = ((clusters["ts"].astype("int64") - session_start_ms) // 60000).astype("int64")
        iceberg_minute_ts = pd.to_datetime(session_start_ms + iceberg_minute * 60000, unit="ms", utc=True)
        ice_work = pd.DataFrame(
            {
                "minute_offset": iceberg_minute.values,
                "minute_ts_utc": iceberg_minute_ts.values,
                "iceberg_contrib": clusters["side"].values * cfg.cluster_signal_weight,
            }
        )
        ice_agg = (
            ice_work.groupby(["minute_offset", "minute_ts_utc"], as_index=False)
            .agg(iceberg_contrib=("iceberg_contrib", "sum"))
        )
        ice_count = (
            ice_work.groupby(["minute_offset", "minute_ts_utc"], as_index=False)
            .agg(n_iceberg=("iceberg_contrib", "size"))
        )
        merged = large_agg.merge(ice_agg, on=["minute_offset", "minute_ts_utc"], how="outer")
        merged = merged.merge(ice_count, on=["minute_offset", "minute_ts_utc"], how="outer")
        merged["iceberg_contrib"] = merged["iceberg_contrib"].fillna(0.0)
        merged["n_iceberg"] = merged["n_iceberg"].fillna(0).astype(int)
        merged["composite"] = merged["composite"].fillna(0.0) + merged["iceberg_contrib"]
        merged = merged.drop(columns=["iceberg_contrib"])
    else:
        merged = large_agg.copy()
        merged["n_iceberg"] = 0

    merged["n_large"] = merged["n_large"].fillna(0).astype(int)
    merged["n_whale"] = merged["n_whale"].fillna(0).astype(int)
    return merged[["minute_offset", "minute_ts_utc", "composite", "n_large", "n_whale", "n_iceberg"]].sort_values("minute_offset").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect(trades: pd.DataFrame, cfg: DetectorConfig | None = None) -> dict[str, Any]:
    """Run the detector on a trades DataFrame.

    Returns:
        {
            "per_trade_z": np.ndarray (length len(df); NaN for first lookback rows),
            "composite_by_minute": pd.DataFrame [minute_offset, minute_ts_utc, composite, n_large, n_whale, n_iceberg],
            "stats": {
                "n_trades": int,
                "cluster_count": int,
                "n_large": int,
                "n_whale": int,
                "candidate_count": int,        # alias of cluster_count (per spec naming)
                "mean_duration_ms": float,     # 0 for same-ms clusters by construction
                "mean_price_anchor_strength": float,  # 1 - mean cluster CV
            },
        }
    """
    if cfg is None:
        cfg = DetectorConfig()
    _validate_schema(trades)

    df = trades.copy()
    side = signed_side(df["is_buyer_maker"].values)
    df = df.assign(signed_qty=side * df["qty"].values)
    z = _rolling_zscore_shifted(df["qty"].values, cfg.lookback)

    clusters = _detect_clusters(df, cfg)
    composite = _composite_by_minute(df, z, side, clusters, cfg)

    is_large = (np.abs(z) >= cfg.large_z) & np.isfinite(z)
    is_whale = (np.abs(z) >= cfg.whale_z) & np.isfinite(z)

    mean_cv = float(clusters["cv"].mean()) if len(clusters) else 0.0
    stats = {
        "n_trades": int(len(df)),
        "cluster_count": int(len(clusters)),
        "n_large": int(is_large.sum()),
        "n_whale": int(is_whale.sum()),
        "candidate_count": int(len(clusters)),  # per spec: candidate = cluster
        "mean_duration_ms": 0.0,  # same-ms clusters have 0 duration by construction
        "mean_price_anchor_strength": max(0.0, 1.0 - mean_cv),
    }

    return {
        "per_trade_z": z,
        "composite_by_minute": composite,
        "stats": stats,
    }


__all__ = ["signed_side", "DetectorConfig", "detect", "SchemaError"]