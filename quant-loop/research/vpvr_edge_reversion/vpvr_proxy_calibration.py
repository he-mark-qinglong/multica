"""
VPVR proxy calibration (audit Item 1) — quantify bp deviation between:
  (A) klines-bucketed proxy used in vpvr_edge_precheck.py
      (each 1m bar's quote_volume is added entirely to the bucket containing its close price)
  (B) tick-synthetic proxy: spread each 1m bar's quote_volume across the buckets it traverses
      proportionally to a synthetic intra-bar path (open → close with uniform intra-bar visits).

For the same set of BTC dates, computes HVN / LVN positions in (A) and (B) and reports the
bp-level deviation of (B-A) for each dated setup. This addresses audit Item 1's CONCERN about
the klines-proxy not being a faithful tick-level VPVR — quantifying the worst-case bp error.

NOTE: real Bookmap/TradingView tick-level VPVR uses Binance aggTrades (microscopic tick stream
~100/sec/machine). This script uses a klines-based tick SINTETIC; the real verification requires
Binance aggTrades data + TradingView VPVR screenshot side-by-side, which is OUT of scope here.

Conventions preserved from vpvr_edge_precheck.py:
  - 200 buckets across [low.min(), high.max()] of the day
  - Same smoothing kernel (Gaussian, sigma=3 buckets)
  - Same find_hvn_lvn (argmax HVN, 20th-percentile LVN on each side, closest to HVN)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
DATA = ROOT / "data/perp_1m"
OUT = ROOT / "research/vpvr_edge_reversion"

# Representative BTC days — pick 4 dates spanning different vol regimes to stress-test
# the proxy sensitivity: low-vol / normal / high-vol / trend-day
SAMPLE_DAYS = [
    "2024-08-05",  # post-ETF low-vol summer
    "2024-11-13",  # post-election rally, high vol
    "2025-01-20",  # inauguration-day sell-off, extreme vol
    "2025-03-10",  # mid-range trend day
]


def load_one_day(symbol: str, day: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA / f"{symbol}_1m.parquet",
                         columns=["open_time", "open", "high", "low", "close", "volume", "quote_volume"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[(df["ts"] >= pd.Timestamp(day, tz="UTC")) &
            (df["ts"] < pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1))]
    return df.reset_index(drop=True)


def vpvr_profile_close(bars: pd.DataFrame, n_buckets: int = 200):
    """Method A: klines-proxy (single-bucket assignment to close). Same as precheck.py."""
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    edges = np.linspace(lo, hi, n_buckets + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    mids = bars["close"].to_numpy()
    vols = bars["quote_volume"].to_numpy()
    idx = np.clip(np.searchsorted(edges, mids, side="right") - 1, 0, n_buckets - 1)
    bv = np.zeros(n_buckets)
    np.add.at(bv, idx, vols)
    return centers, bv


def vpvr_profile_spread(bars: pd.DataFrame, n_buckets: int = 200, intra_steps: int = 20):
    """Method B: tick-synthetic — spread each bar's quote_volume across the buckets it
    traverses using a uniform intra-bar synthetic path of `intra_steps` points."""
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    edges = np.linspace(lo, hi, n_buckets + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    n_bars = len(bars)
    if n_bars == 0:
        return centers, np.zeros(n_buckets)
    bv = np.zeros(n_buckets)
    # Per-bar processing — vectorized inner loop
    opens = bars["open"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    lows_ = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    vols = bars["quote_volume"].to_numpy(dtype=float)

    # Synthetic path: open → low → high → close (rough W shape; not a true tick but
    # traverses each bucket touched by [low, high] at least once).
    t = np.linspace(0.0, 1.0, intra_steps)
    for i in range(n_bars):
        o_, h_, l_, c_, v_ = opens[i], highs[i], lows_[i], closes[i], vols[i]
        # Sub-path: open → low → high → close
        if intra_steps <= 4:
            path = np.array([o_, l_, h_, c_])[:max(2, intra_steps)]
        else:
            n3 = intra_steps // 3
            # interpolate open→low, low→high, high→close
            seg1 = np.linspace(o_, l_, n3)
            seg2 = np.linspace(l_, h_, n3)
            seg3 = np.linspace(h_, c_, max(2, intra_steps - 2 * n3))
            path = np.concatenate([seg1, seg2, seg3])
        idx = np.searchsorted(edges, path, side="right") - 1
        idx = np.clip(idx, 0, n_buckets - 1)
        # Uniform weight per step
        step_v = v_ / len(path)
        np.add.at(bv, idx, step_v)
    return centers, bv


def find_hvn_lvn(prices, vol):
    kernel = np.exp(-0.5 * (np.arange(-9, 10) / 3) ** 2)
    kernel = kernel / kernel.sum()
    sv = np.convolve(vol, kernel, mode="same")
    hvn_idx = int(np.argmax(sv))
    hvn_price = float(prices[hvn_idx])
    if hvn_idx < 5:
        lvn_lo = float(prices[0])
    else:
        seg = sv[:hvn_idx]
        thr = np.percentile(seg, 20)
        cands = np.where(seg <= thr)[0]
        lvn_lo = float(prices[cands[np.argmax(cands)]] if len(cands) else prices[0])
    if hvn_idx >= len(sv) - 5:
        lvn_hi = float(prices[-1])
    else:
        seg = sv[hvn_idx + 1:]
        thr = np.percentile(seg, 20)
        cands = np.where(seg <= thr)[0] + hvn_idx + 1
        lvn_hi = float(prices[cands[np.argmin(cands)]] if len(cands) else prices[-1])
    return hvn_price, lvn_lo, lvn_hi


def main():
    SYM = "BTCUSDT"
    rows = []
    for day in SAMPLE_DAYS:
        bars = load_one_day(SYM, day)
        if len(bars) < 200:
            print(f"  [skip] {SYM} {day}: only {len(bars)} bars")
            continue

        pA, vA = vpvr_profile_close(bars)
        pB, vB = vpvr_profile_spread(bars)

        hA, lA_lo, lA_hi = find_hvn_lvn(pA, vA)
        hB, lB_lo, lB_hi = find_hvn_lvn(pB, vB)

        # bp deviation (B - A), in bp relative to HVN_A
        ref = hA
        dhvn = (hB - hA) / ref * 1e4
        dlvn_lo = (lB_lo - lA_lo) / ref * 1e4
        dlvn_hi = (lB_hi - lA_hi) / ref * 1e4
        # half-range shifts (geometry matters for TP1 edge)
        hA_half_lo_bp = (hA - lA_lo) / ref * 1e4
        hB_half_lo_bp = (hB - lB_lo) / ref * 1e4
        dhalf_lo = hB_half_lo_bp - hA_half_lo_bp

        rows.append({
            "symbol": SYM,
            "date": day,
            "n_bars": len(bars),
            "hvn_A": hA, "lvn_lo_A": lA_lo, "lvn_hi_A": lA_hi,
            "hvn_B": hB, "lvn_lo_B": lB_lo, "lvn_hi_B": lB_hi,
            "dhvn_bp": dhvn,
            "dlvn_lo_bp": dlvn_lo,
            "dlvn_hi_bp": dlvn_hi,
            "half_lo_A_bp": hA_half_lo_bp,
            "half_lo_B_bp": hB_half_lo_bp,
            "dhalf_lo_bp": dhalf_lo,
        })
        print(f"\n[{SYM} {day}] (n={len(bars)})")
        print(f"  Method A (close-bucket): HVN=${hA:,.1f}  LVN_lo=${lA_lo:,.1f}  LVN_hi=${lA_hi:,.1f}  half_lo_bp={hA_half_lo_bp:.1f}")
        print(f"  Method B (tick-spread):  HVN=${hB:,.1f}  LVN_lo=${lB_lo:,.1f}  LVN_hi=${lB_hi:,.1f}  half_lo_bp={hB_half_lo_bp:.1f}")
        print(f"  Deviation (B - A):       dHVN={dhvn:+.1f}bp  dLVN_lo={dlvn_lo:+.1f}bp  dLVN_hi={dlvn_hi:+.1f}bp  dhalf_lo={dhalf_lo:+.1f}bp")

    df = pd.DataFrame(rows)
    out_fp = OUT / "proxy_calibration.csv"
    df.to_csv(out_fp, index=False)
    print(f"\nSaved → {out_fp}")
    print("\nSummary statistics (across sampled days):")
    for col in ("dhvn_bp", "dlvn_lo_bp", "dlvn_hi_bp", "dhalf_lo_bp"):
        v = df[col]
        print(f"  {col}: mean={v.mean():+.2f}bp  std={v.std():.2f}bp  abs_max={v.abs().max():.2f}bp")


if __name__ == "__main__":
    main()
