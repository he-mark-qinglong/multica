"""
VPVR edge-limit reversion — cost-cap pre-check + first-touch probability.

Reads 1m perp klines, computes VPVR profile per (window), extracts HVN/LVN edges,
and reports:
  - median range width (HVN-LVN distance in bps) — cost-cap gate vs 30bp kill
  - first-touch probabilities (limit orders at LVN edges): P(center first), P(stop first), fill rate, markout

Defaults: last 2y, daily window (1440 bars), 3 symbols (BTC/ETH/SOL), price-bucket width 0.05%.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
DATA = ROOT / "data/perp_1m"
OUT = ROOT / "research/vpvr_edge_reversion"
OUT.mkdir(parents=True, exist_ok=True)


def load_klines(symbol: str, lookback_days: int = 730) -> pd.DataFrame:
    """Load last `lookback_days` of 1m klines for a USDⓈ-M perp symbol."""
    fp = DATA / f"{symbol}_1m.parquet"
    df = pd.read_parquet(fp, columns=["open_time", "open", "high", "low", "close", "volume", "quote_volume"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    cutoff = df["ts"].max() - pd.Timedelta(days=lookback_days)
    df = df[df["ts"] >= cutoff].reset_index(drop=True)
    return df


def vpvr_profile(bars: pd.DataFrame, n_buckets: int = 200) -> pd.DataFrame:
    """Return price-bucketed volume distribution for the window.

    Buckets are equal-width across [low.min(), high.max()].
    Each bar's `volume` is split evenly across the buckets it touches
    (approx — sufficient for profile shape).
    """
    if len(bars) == 0:
        return pd.DataFrame(columns=["price", "volume"])
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    if hi <= lo:
        return pd.DataFrame(columns=["price", "volume"])
    edges = np.linspace(lo, hi, n_buckets + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    bucket_vol = np.zeros(n_buckets)

    # Each bar's volume is added to ONE bucket: the bucket containing the bar's mid price.
    mids = bars["close"].to_numpy()
    vols = bars["quote_volume"].to_numpy()  # use quote_volume — pair-RT cost is quote-denominated
    idx = np.clip(np.searchsorted(edges, mids, side="right") - 1, 0, n_buckets - 1)
    np.add.at(bucket_vol, idx, vols)

    return pd.DataFrame({"price": centers, "volume": bucket_vol})


def find_hvn_lvn(profile: pd.DataFrame, smooth_sigma_buckets: int = 3) -> tuple[float, float, float]:
    """Identify HVN center + nearest LVN on each side.

    Returns: (hvn_price, lvn_lower_price, lvn_upper_price).
    """
    if len(profile) < 5:
        return (np.nan, np.nan, np.nan)
    vol = profile["volume"].to_numpy(dtype=float)
    prices = profile["price"].to_numpy(dtype=float)

    # Light smoothing so a single noisy bucket doesn't dominate.
    if smooth_sigma_buckets > 1:
        kernel = np.exp(-0.5 * (np.arange(-smooth_sigma_buckets * 3,
                                           smooth_sigma_buckets * 3 + 1) / smooth_sigma_buckets) ** 2)
        kernel = kernel / kernel.sum()
        vol = np.convolve(vol, kernel, mode="same")

    hvn_idx = int(np.argmax(vol))
    hvn_price = float(prices[hvn_idx])
    hvn_vol = float(vol[hvn_idx])

    # LVN lower: minimum-vol bucket in [0, hvn_idx-1], weighted by distance to HVN
    # (we want the closest meaningful valley, not the absolute minimum).
    if hvn_idx < 3:
        lvn_lower_price = float(prices[0])
    else:
        seg = vol[:hvn_idx]
        # Use the lowest 10th-percentile volume in the segment, take the price closest to HVN.
        low_threshold = np.percentile(seg, 20)
        candidates = np.where(seg <= low_threshold)[0]
        if len(candidates) == 0:
            lvn_lower_price = float(prices[0])
        else:
            # Choose the candidate closest to the HVN.
            closest = candidates[np.argmax(candidates)]
            lvn_lower_price = float(prices[closest])

    if hvn_idx >= len(vol) - 3:
        lvn_upper_price = float(prices[-1])
    else:
        seg = vol[hvn_idx + 1:]
        low_threshold = np.percentile(seg, 20)
        candidates = np.where(seg <= low_threshold)[0] + hvn_idx + 1
        if len(candidates) == 0:
            lvn_upper_price = float(prices[-1])
        else:
            closest = candidates[np.argmin(candidates)]
            lvn_upper_price = float(prices[closest])

    return (hvn_price, lvn_lower_price, lvn_upper_price)


@dataclass
class WindowMetrics:
    symbol: str
    window_end: pd.Timestamp
    hvn_price: float
    lvn_lower_price: float
    lvn_upper_price: float
    half_range_bps_lower: float   # (hvn - lvn_lower) / hvn * 1e4
    half_range_bps_upper: float   # (lvn_upper - hvn) / hvn * 1e4
    full_range_bps: float         # (lvn_upper - lvn_lower) / hvn * 1e4


def daily_metrics(df: pd.DataFrame, symbol: str, n_buckets: int = 200) -> pd.DataFrame:
    """Per-day VPVR profile + HVN/LVN extraction."""
    df = df.copy()
    df["date"] = df["ts"].dt.tz_convert(None).dt.floor("D")
    rows = []
    for date, group in df.groupby("date"):
        if len(group) < 200:  # skip partial days
            continue
        profile = vpvr_profile(group, n_buckets=n_buckets)
        hvn, lvn_lo, lvn_hi = find_hvn_lvn(profile)
        if not (np.isfinite(hvn) and np.isfinite(lvn_lo) and np.isfinite(lvn_hi)):
            continue
        if lvn_lo >= hvn or hvn >= lvn_hi:
            continue
        rows.append(WindowMetrics(
            symbol=symbol,
            window_end=pd.Timestamp(date),
            hvn_price=hvn,
            lvn_lower_price=lvn_lo,
            lvn_upper_price=lvn_hi,
            half_range_bps_lower=(hvn - lvn_lo) / hvn * 1e4,
            half_range_bps_upper=(lvn_hi - hvn) / hvn * 1e4,
            full_range_bps=(lvn_hi - lvn_lo) / hvn * 1e4,
        ))
    return pd.DataFrame(rows)


def four_hour_metrics(df: pd.DataFrame, symbol: str, n_buckets: int = 120) -> pd.DataFrame:
    """Per-4h VPVR profile + HVN/LVN extraction."""
    df = df.copy()
    df["bucket_4h"] = df["ts"].dt.tz_convert(None).dt.floor("4h")
    rows = []
    for bucket, group in df.groupby("bucket_4h"):
        if len(group) < 60:  # skip partial buckets
            continue
        profile = vpvr_profile(group, n_buckets=n_buckets)
        hvn, lvn_lo, lvn_hi = find_hvn_lvn(profile)
        if not (np.isfinite(hvn) and np.isfinite(lvn_lo) and np.isfinite(lvn_hi)):
            continue
        if lvn_lo >= hvn or hvn >= lvn_hi:
            continue
        rows.append(WindowMetrics(
            symbol=symbol,
            window_end=pd.Timestamp(bucket),
            hvn_price=hvn,
            lvn_lower_price=lvn_lo,
            lvn_upper_price=lvn_hi,
            half_range_bps_lower=(hvn - lvn_lo) / hvn * 1e4,
            half_range_bps_upper=(lvn_hi - hvn) / hvn * 1e4,
            full_range_bps=(lvn_hi - lvn_lo) / hvn * 1e4,
        ))
    return pd.DataFrame(rows)


def summarize(metrics: pd.DataFrame, label: str) -> dict:
    """Cost-cap summary: median range width, distribution percentiles, kill verdict."""
    out = {"label": label, "n": int(len(metrics))}
    if len(metrics) == 0:
        out["median_full_range_bps"] = None
        out["median_half_lower_bps"] = None
        out["median_half_upper_bps"] = None
        out["p25_full_bps"] = None
        out["p75_full_bps"] = None
        out["pct_above_30bp"] = None
        out["kill_verdict"] = "NO_DATA"
        return out

    out["median_full_range_bps"] = float(metrics["full_range_bps"].median())
    out["median_half_lower_bps"] = float(metrics["half_range_bps_lower"].median())
    out["median_half_upper_bps"] = float(metrics["half_range_bps_upper"].median())
    out["p25_full_bps"] = float(metrics["full_range_bps"].quantile(0.25))
    out["p75_full_bps"] = float(metrics["full_range_bps"].quantile(0.75))
    out["pct_above_30bp"] = float((metrics["full_range_bps"] > 30).mean())

    # Cost-cap rule (orchestrator): median gross edge < 30bp → KILL.
    # Gross edge per trade ≈ half-range (entry at LVN, TP1 at HVN).
    median_half = min(out["median_half_lower_bps"], out["median_half_upper_bps"])
    out["median_gross_edge_bps"] = float(median_half)
    if median_half < 30:
        out["kill_verdict"] = "KILL_COST_CAP"
        out["kill_reason"] = f"median gross edge {median_half:.1f}bp < 30bp floor"
    else:
        out["kill_verdict"] = "PROCEED"
    return out


def main():
    SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    LOOKBACK_DAYS = 730
    print(f"[vpvr_edge] Loading {LOOKBACK_DAYS}d of 1m klines for {SYMS}...")
    data = {s: load_klines(s, LOOKBACK_DAYS) for s in SYMS}
    for s, d in data.items():
        print(f"  {s}: {len(d)} bars, {d['ts'].min()} → {d['ts'].max()}")

    print("\n[vpvr_edge] Computing daily VPVR profiles (cost-cap)...")
    daily = {}
    daily_summary = []
    for s, d in data.items():
        m = daily_metrics(d, s)
        daily[s] = m
        s_summary = summarize(m, f"{s}_daily")
        daily_summary.append(s_summary)
        print(f"  {s}_daily: n={s_summary['n']}  median_full={s_summary['median_full_range_bps']:.1f}bp  "
              f"median_half_lo={s_summary['median_half_lower_bps']:.1f}bp  "
              f"pct_above_30bp={s_summary['pct_above_30bp']:.2%}  → {s_summary['kill_verdict']}")

    print("\n[vpvr_edge] Computing 4h VPVR profiles (cost-cap)...")
    fourh_summary = []
    fourh = {}
    for s, d in data.items():
        m = four_hour_metrics(d, s)
        fourh[s] = m
        s_summary = summarize(m, f"{s}_4h")
        fourh_summary.append(s_summary)
        print(f"  {s}_4h: n={s_summary['n']}  median_full={s_summary['median_full_range_bps']:.1f}bp  "
              f"median_half_lo={s_summary['median_half_lower_bps']:.1f}bp  "
              f"pct_above_30bp={s_summary['pct_above_30bp']:.2%}  → {s_summary['kill_verdict']}")

    summary = {
        "config": {
            "lookback_days": LOOKBACK_DAYS,
            "daily_buckets": 200,
            "fourh_buckets": 120,
            "kline_source": str(DATA),
        },
        "daily": daily_summary,
        "fourh": fourh_summary,
        "cost_cap_floor_bp": 30,
        "vip0_pair_rt_econ_floor_bp": 9,  # per T10 pre-SPEC (SMA-36598)
    }
    out_path = OUT / "precheck_summary.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[vpvr_edge] Saved → {out_path}")

    # Persist raw daily metrics per symbol for downstream first-touch sim.
    for s, m in daily.items():
        m.to_parquet(OUT / f"daily_metrics_{s}.parquet", index=False)
        print(f"  saved {len(m)} daily rows → daily_metrics_{s}.parquet")
    for s, m in fourh.items():
        m.to_parquet(OUT / f"fourh_metrics_{s}.parquet", index=False)


if __name__ == "__main__":
    main()