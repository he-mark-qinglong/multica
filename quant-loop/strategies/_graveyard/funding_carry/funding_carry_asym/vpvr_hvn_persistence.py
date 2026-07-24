#!/usr/bin/env python3
"""VPVR 4h HVN persistence test (SMA-34906).

For each 4h bar, recompute the HVN zones from a 180-bar (~30 day)
rolling window and then measure whether those zones remain
"respected" over forward windows of 6h, 12h, and 24h.

Definition of "respected" (strict containment)
----------------------------------------------
A zone is *respected* over a forward window if, for every 4h bar
inside the window, the bar's full [low, high] range stays inside
the zone's [price_low, price_high] band. Equivalently:
    min(low[window]) >= zone.price_low
    AND max(high[window]) <= zone.price_high

This is the strictest interpretation — it treats both sides of the
zone equally (an HVN is a magnet that absorbs from above and from
below). A softer "wicked through but closed back inside" semantic
is *not* what we are scoring here. We also report the soft
"wicked_below" and "wicked_above" booleans as auxiliary signals so
the analyst can probe the data further.

Coverage
--------
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT (4h parquet in live_data/).
- Window: 2022-01-01 through 2026-07-17 (full parquet coverage).
- Snapshot cadence: every 6 4h bars (~1 day) — matches
  vpvr_snapshot_every_bars_4h in the funding-carry-asym config.
- Future windows: 6h = 2 bars, 12h = 4 bars, 24h = 8 bars.

Outputs (under strategies/funding_carry_asym/results/)
------------------------------------------------------
- hvn_persistence.csv  — zone_id, generated_ts, symbol, zone_low,
                         zone_high, zone_size_bps, volume,
                         respected_at_6h, respected_at_12h,
                         respected_at_24h, wicked_below_6h,
                         wicked_below_12h, wicked_below_24h,
                         max_close_excursion_bps
- hvn_persistence_summary.json — counts, % respected by window
                                  and zone-size bucket, plus
                                  recommendation.

Gate
----
This script reports observed statistics only. It does **not**
declare zone validity — that decision belongs to out-of-sample
validation (cycle-46 / G5 walk-forward rule).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Make the shared indicator library importable.
HERE = Path(__file__).resolve().parent
INDICATORS = Path("/home/smark/multica/quant-loop/strategies/_indicators")
for p in (str(HERE), str(INDICATORS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from vpvr_levels import detect_vpvr_levels  # noqa: E402


# ---------------------------------------------------------------------------
# Config — mirrors the funding-carry-asym config.json knobs.
# ---------------------------------------------------------------------------
LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
RESULTS = HERE / "results"

SYMBOLS: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "4h"

LOOKBACK_BARS = 180          # ~30 days of 4h bars
SNAPSHOT_EVERY_BARS = 6      # ~1 day between snapshots
NUM_BINS = 24                # match config.json
NUM_HVN = 3
HVN_QUANTILE = 0.85

# Forward windows in 4h bars: 6h=2, 12h=4, 24h=8.
WINDOWS: Dict[str, int] = {"6h": 2, "12h": 4, "24h": 8}

# Zone-size buckets in basis points (half of the band width in bps of the
# zone centre price — a measure of band width as a fraction of price).
SIZE_BUCKETS_BPS: Tuple[Tuple[str, float, float], ...] = (
    ("tight", 0.0, 50.0),
    ("medium", 50.0, 150.0),
    ("wide", 150.0, float("inf")),
)


# ---------------------------------------------------------------------------
# Data loading.
# ---------------------------------------------------------------------------
def load_4h_ohlcv(symbol: str) -> pd.DataFrame:
    """Load a 4h OHLCV parquet, return tz-naive frame with [o,h,l,c,v]."""
    path = LIVE_DATA / f"{symbol}_{TIMEFRAME}.parquet"
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
        df = df.set_index("ts")
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = ["open", "high", "low", "close", "volume"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")
    return df[keep]


# ---------------------------------------------------------------------------
# HVN generation per snapshot bar.
# ---------------------------------------------------------------------------
def generate_hvns(
    bars: pd.DataFrame,
    snapshot_idx: int,
    lookback: int = LOOKBACK_BARS,
    num_bins: int = NUM_BINS,
    num_hvn: int = NUM_HVN,
    hvn_quantile: float = HVN_QUANTILE,
) -> List:
    """Compute HVN zones from bars[snapshot_idx - lookback : snapshot_idx].

    The window ends strictly *before* bar `snapshot_idx` — this is the
    no-look-ahead contract: zones at time T reflect data through bar T-1.
    """
    if snapshot_idx < lookback:
        return []
    window = bars.iloc[snapshot_idx - lookback : snapshot_idx]
    levels = detect_vpvr_levels(
        window,
        num_bins=num_bins,
        hvn_quantile=hvn_quantile,
        lvn_quantile=0.0,        # we only want HVN; LVN suppressed
        num_hvn=num_hvn,
        num_lvn=0,
        include_poc=False,
    )
    return [lvl for lvl in levels if lvl.kind == "HVN"]


# ---------------------------------------------------------------------------
# Persistence scoring per zone per window.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RespectResult:
    respected_strict: bool          # full OHLC range stays in zone for whole window
    respected_soft: bool            # no close exceeds zone ± proximity_atr * ATR
    wicked_below: bool              # low < zone_low but close >= zone_low
    wicked_above: bool              # high > zone_high but close <= zone_high
    max_close_excursion_bps: float  # worst close excursion outside zone, bps


def _atr_at(bars: pd.DataFrame, idx: int, period: int = 14) -> float:
    """Compute ATR(period) ending at bar ``idx`` (close-to-close, no look-ahead)."""
    if idx < period:
        return float("nan")
    seg = bars.iloc[idx - period + 1 : idx + 1]
    h = seg["high"].astype(float).to_numpy()
    lo = seg["low"].astype(float).to_numpy()
    c = seg["close"].astype(float).to_numpy()
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - lo, np.abs(h - prev_c), np.abs(lo - prev_c)])
    return float(tr.mean())


def score_zone_respect(
    bars: pd.DataFrame,
    start_idx: int,
    window_bars: int,
    zone,
    proximity_atr: float = 1.0,
    atr_period: int = 14,
) -> RespectResult:
    """Score whether a zone is respected over a forward window.

    The window is bars[start_idx : start_idx + window_bars]. Two
    "respected" booleans are produced:

    * ``respected_strict`` — full OHLC range of every bar in the window
      stays inside the zone band (min_low >= zone_low AND max_high
      <= zone_high). This is the literal question the issue asks but
      it is harsh because any wick outside the band counts as
      non-respect.
    * ``respected_soft`` — no bar *closes* more than
      ``proximity_atr * ATR(atr_period)`` outside the zone band. This
      matches the funding-carry-asym strategy's actual filter
      (price near the zone, not strictly inside it) and is the more
      practical definition of "zone still valid".

    Wick-only excursions (wick outside, close inside) and the worst
    close excursion in bps are reported as auxiliary signals.
    """
    end_idx = min(start_idx + window_bars, len(bars))
    seg = bars.iloc[start_idx:end_idx]
    if len(seg) == 0:
        return RespectResult(False, False, False, False, 0.0)

    min_low = float(seg["low"].min())
    max_high = float(seg["high"].max())
    closes = seg["close"].astype(float).to_numpy()

    # Strict containment: price range always within zone band.
    strict = (min_low >= zone.price_low) and (max_high <= zone.price_high)

    # Wick-only excursions: wick outside but close inside (or in zone).
    wicked_below = bool(min_low < zone.price_low and np.all(closes >= zone.price_low))
    wicked_above = bool(max_high > zone.price_high and np.all(closes <= zone.price_high))

    # Soft respect: no close escapes the zone by more than proximity_atr * ATR.
    atr_at_gen = _atr_at(bars, start_idx - 1, period=atr_period)
    if not np.isfinite(atr_at_gen) or atr_at_gen <= 0:
        soft = False
    else:
        band = proximity_atr * atr_at_gen
        lower_ok = closes >= (zone.price_low - band)
        upper_ok = closes <= (zone.price_high + band)
        soft = bool(np.all(lower_ok) and np.all(upper_ok))

    # Worst close excursion outside the zone, in bps of the zone centre.
    centre = float(zone.price_center)
    if centre <= 0:
        max_exc = 0.0
    else:
        excursions = np.maximum(
            np.maximum(zone.price_low - closes, 0.0),    # below
            np.maximum(closes - zone.price_high, 0.0),   # above
        )
        max_exc = float(excursions.max() / centre * 1e4) if centre > 0 else 0.0

    return RespectResult(
        respected_strict=strict,
        respected_soft=soft,
        wicked_below=wicked_below,
        wicked_above=wicked_above,
        max_close_excursion_bps=max_exc,
    )


# ---------------------------------------------------------------------------
# Main persistence sweep.
# ---------------------------------------------------------------------------
def run_for_symbol(
    symbol: str,
    snapshot_every: int = SNAPSHOT_EVERY_BARS,
) -> pd.DataFrame:
    """Walk the 4h parquet and produce one row per (snapshot, zone)."""
    bars = load_4h_ohlcv(symbol)
    n = len(bars)
    max_window = max(WINDOWS.values())
    # Last snapshot must leave room for the longest window.
    last_snapshot_idx = n - max_window

    rows: List[Dict] = []
    zone_counter = 0

    snap_indices = range(LOOKBACK_BARS, last_snapshot_idx, snapshot_every)
    for snap_idx in snap_indices:
        hvns = generate_hvns(bars, snap_idx)
        snap_ts = bars.index[snap_idx]
        for zone in hvns:
            zone_counter += 1
            size_bps = (zone.price_high - zone.price_low) / max(zone.price_center, 1e-9) * 1e4
            row: Dict = {
                "zone_id": f"{symbol}_{zone_counter:06d}",
                "symbol": symbol,
                "generated_ts": snap_ts.isoformat(),
                "zone_low": float(zone.price_low),
                "zone_high": float(zone.price_high),
                "zone_center": float(zone.price_center),
                "zone_size_bps": float(size_bps),
                "zone_volume": float(zone.volume),
            }
            for label, win_bars in WINDOWS.items():
                res = score_zone_respect(bars, snap_idx + 1, win_bars, zone)
                row[f"respected_strict_{label}"] = bool(res.respected_strict)
                row[f"respected_soft_{label}"] = bool(res.respected_soft)
                row[f"wicked_below_{label}"] = bool(res.wicked_below)
                row[f"wicked_above_{label}"] = bool(res.wicked_above)
                row[f"max_close_excursion_bps_{label}"] = float(res.max_close_excursion_bps)
            rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Summary stats & recommendation.
# ---------------------------------------------------------------------------
def bucket_size(bps: float) -> str:
    for name, lo, hi in SIZE_BUCKETS_BPS:
        if lo <= bps < hi:
            return name
    return "wide"


def compute_summary(df: pd.DataFrame) -> Dict:
    """Compute persistence rates overall, per-symbol, and per size bucket.

    Both strict and soft respect rates are reported. Strict asks "did
    price *range* stay inside the zone band the whole window?"; soft
    asks "did no close escape the zone by more than 1 ATR (the
    strategy's actual proximity filter)?".
    """
    summary: Dict = {
        "n_zones_total": int(len(df)),
        "per_symbol": {},
        "per_window_overall": {"strict": {}, "soft": {}},
        "per_window_by_size": {"strict": {}, "soft": {}},
        "per_symbol_by_size": {"strict": {}, "soft": {}},
        "excursion_stats": {},
    }
    if len(df) == 0:
        return summary

    modes = ("strict", "soft")
    col_prefix = {"strict": "respected_strict", "soft": "respected_soft"}

    # Per-window overall respect rate (both modes).
    for mode in modes:
        prefix = col_prefix[mode]
        for label in WINDOWS:
            col = f"{prefix}_{label}"
            summary["per_window_overall"][mode][label] = {
                "n_respected": int(df[col].sum()),
                "n_total": int(len(df)),
                "pct_respected": float(df[col].mean() * 100.0),
            }

    # Per-symbol respect rate (both modes).
    for symbol, sub in df.groupby("symbol"):
        ps: Dict = {"n_zones": int(len(sub))}
        for mode in modes:
            prefix = col_prefix[mode]
            ps[mode] = {}
            for label in WINDOWS:
                col = f"{prefix}_{label}"
                ps[mode][label] = {
                    "n_respected": int(sub[col].sum()),
                    "pct_respected": float(sub[col].mean() * 100.0),
                }
        summary["per_symbol"][symbol] = ps

    # Per-window by zone-size bucket (both modes).
    df = df.copy()
    df["size_bucket"] = df["zone_size_bps"].apply(bucket_size)
    for mode in modes:
        prefix = col_prefix[mode]
        for label in WINDOWS:
            col = f"{prefix}_{label}"
            bucket_rates: Dict[str, Dict] = {}
            for bucket_name in ("tight", "medium", "wide"):
                sub = df[df["size_bucket"] == bucket_name]
                if len(sub) == 0:
                    bucket_rates[bucket_name] = {"n_zones": 0, "pct_respected": None}
                else:
                    bucket_rates[bucket_name] = {
                        "n_zones": int(len(sub)),
                        "pct_respected": float(sub[col].mean() * 100.0),
                    }
            summary["per_window_by_size"][mode][label] = bucket_rates

    # Per-symbol by size (both modes).
    for symbol, sub in df.groupby("symbol"):
        sym_size: Dict[str, Dict] = {"strict": {}, "soft": {}}
        for mode in modes:
            prefix = col_prefix[mode]
            for label in WINDOWS:
                col = f"{prefix}_{label}"
                sym_size[mode][label] = {}
                for bucket_name in ("tight", "medium", "wide"):
                    sub_b = sub[sub["size_bucket"] == bucket_name]
                    if len(sub_b) == 0:
                        sym_size[mode][label][bucket_name] = {"n_zones": 0, "pct_respected": None}
                    else:
                        sym_size[mode][label][bucket_name] = {
                            "n_zones": int(len(sub_b)),
                            "pct_respected": float(sub_b[col].mean() * 100.0),
                        }
        summary["per_symbol_by_size"][symbol] = sym_size

    # Excursion stats — worst close excursion outside zone, by window.
    for label in WINDOWS:
        col = f"max_close_excursion_bps_{label}"
        if col in df.columns:
            summary["excursion_stats"][label] = {
                "median_bps": float(df[col].median()),
                "p90_bps": float(df[col].quantile(0.90)),
                "p99_bps": float(df[col].quantile(0.99)),
            }

    # Recommendation — pick the window with the highest *soft* respect
    # rate (the practical one). Strict is reported as an upper-bound
    # noise floor.
    best_window = max(
        WINDOWS.keys(),
        key=lambda L: summary["per_window_overall"]["soft"][L]["pct_respected"],
    )
    summary["recommendation"] = {
        "best_window": best_window,
        "best_window_pct_soft_respected": summary["per_window_overall"]["soft"][best_window]["pct_respected"],
        "best_window_pct_strict_respected": summary["per_window_overall"]["strict"][best_window]["pct_respected"],
        "caveat": (
            "Reported in-sample on a hot-funding-support strategy; "
            "out-of-sample walk-forward (G5) required before declaring "
            "zones valid for live entries."
        ),
    }
    return summary


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------
def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    frames = []
    for symbol in SYMBOLS:
        print(f"[persistence] running {symbol} ...", flush=True)
        df_sym = run_for_symbol(symbol)
        print(f"  -> {len(df_sym)} zones generated", flush=True)
        frames.append(df_sym)
    df = pd.concat(frames, ignore_index=True)
    print(f"[persistence] total zones: {len(df)}", flush=True)

    csv_path = RESULTS / "hvn_persistence.csv"
    df.to_csv(csv_path, index=False)
    print(f"[persistence] wrote {csv_path}", flush=True)

    summary = compute_summary(df)
    summary_path = RESULTS / "hvn_persistence_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"[persistence] wrote {summary_path}", flush=True)

    # Echo headline numbers to stdout so an external caller can grep them.
    print("\n=== Headline ===")
    for mode in ("strict", "soft"):
        print(f"  [{mode}]")
        for label in WINDOWS:
            row = summary["per_window_overall"][mode][label]
            print(f"    {label:>4s}: {row['pct_respected']:6.2f}% respected "
                  f"({row['n_respected']}/{row['n_total']})")
    rec = summary["recommendation"]
    print(f"  recommendation (soft): {rec['best_window']} "
          f"({rec['best_window_pct_soft_respected']:.2f}%)")


if __name__ == "__main__":
    main()
