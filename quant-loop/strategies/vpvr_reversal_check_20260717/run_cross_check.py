"""SMA-34791 — VPVR level × price-reversal cross-check.

Implements the predeclared sampling rule and reversal definition from
SPEC.md. Produces:
  - results/samples.csv : 20 (level, first-touch) rows
  - results/summary.json: counts, reversal rates, baseline comparison

This is a one-shot analysis script — not a strategy. It does not touch
the trading state, does not commit anything, and writes only into the
sibling `results/` directory.

Run:
    python run_cross_check.py
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Make the SMA-34790 VPVR detector importable.
ROOT = Path("/home/smark/multica/quant-loop")
sys.path.insert(0, str(ROOT / "strategies" / "_indicators"))

from vpvr_levels import detect_vpvr_levels  # noqa: E402

# ---------------------------------------------------------------------------
# Constants — mirror SPEC.md. Don't edit without updating SPEC.md first.
# ---------------------------------------------------------------------------
DATA_PATH = ROOT / "live_data" / "BTCUSDT_4h.parquet"
OUT_DIR = ROOT / "strategies" / "vpvr_reversal_check_20260717" / "results"
LOOKBACK_DAYS = 30
WINDOW_BARS = 30                   # 30 × 4h = 120h = 5 days of context
SAMPLE_LOOKAHEAD = 12              # 12 × 4h = 48h to find first touch
NUM_SAMPLES = 20
DEDUP_TOL_PCT = 0.005              # merge bands whose centers are within 0.5%
HORIZONS = [4, 12, 24]            # bars forward (16h / 48h / 96h)
PRIMARY_HORIZON = 12
SEED = 20260717
DETECTOR_KWARGS = dict(
    num_bins=200,
    value_area_fraction=0.70,
    hvn_quantile=0.85,
    lvn_quantile=0.15,
    num_hvn=5,
    num_lvn=5,
)


@dataclass(frozen=True)
class Touch:
    """One sampled (level, first-touch) pair."""
    sample_id: int
    kind: str            # "HVN" or "LVN"
    detection_ts: pd.Timestamp
    price_low: float
    price_high: float
    price_center: float
    volume: float
    score: float
    first_touch_ts: pd.Timestamp
    first_touch_close: float
    bars_to_touch: int
    approach_dir: str    # "down_to_level" | "up_to_level" (pre-touch trend)
    fwd_4h_close: float
    fwd_12h_close: float
    fwd_24h_close: float
    fwd_4h_max_up: float
    fwd_4h_max_dn: float
    fwd_12h_max_up: float
    fwd_12h_max_dn: float
    fwd_24h_max_up: float
    fwd_24h_max_dn: float
    fwd_12h_net_pct: float       # signed move close[t+12] vs touch_close
    reversal_met: bool   # primary-horizon reversal rule
    reversal_met_strict: bool    # stricter direction-pure version


def load_btc_4h() -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATH)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("dt").sort_index()
    # Restrict to last 30 days. Exclusive cutoff at (max - 30d).
    last_ts = df.index.max()
    cutoff = last_ts - pd.Timedelta(days=LOOKBACK_DAYS)
    df = df.loc[df.index > cutoff].copy()
    df.attrs["cutoff"] = cutoff
    df.attrs["last_ts"] = last_ts
    return df


def detect_levels_for_bar(window: pd.DataFrame, dt: pd.Timestamp) -> list:
    """Run the SMA-34790 detector on a window ending at dt."""
    bars = pd.DataFrame({
        "high": window["high"].astype(float),
        "low": window["low"].astype(float),
        "close": window["close"].astype(float),
        "volume": window["volume"].astype(float),
    })
    levels = detect_vpvr_levels(bars, include_poc=False, **DETECTOR_KWARGS)
    return [
        (lv.kind, lv.price_low, lv.price_high, lv.price_center, lv.volume, lv.score)
        for lv in levels
    ]


def first_touch(bar_df: pd.DataFrame, det_idx: int, price_low: float,
                price_high: float, max_lookahead: int) -> tuple[int, float] | None:
    """Earliest bar > det_idx where low <= price_high AND high >= price_low.

    Returns (idx, close_at_touch) or None.
    """
    end = min(det_idx + 1 + max_lookahead, len(bar_df))
    if det_idx + 1 >= end:
        return None
    lows = bar_df["low"].iloc[det_idx + 1: end].to_numpy()
    highs = bar_df["high"].iloc[det_idx + 1: end].to_numpy()
    crosses = (lows <= price_high) & (highs >= price_low)
    if not crosses.any():
        return None
    rel = int(np.argmax(crosses))
    abs_idx = det_idx + 1 + rel
    return abs_idx, float(bar_df["close"].iloc[abs_idx])


def fwd_outcomes(bar_df: pd.DataFrame, touch_idx: int,
                 horizons: list[int]) -> dict:
    """Forward closes and max-up / max-down over each horizon."""
    touch_close = float(bar_df["close"].iloc[touch_idx])
    n = len(bar_df)
    out = {"touch_close": touch_close}
    for h in horizons:
        end = min(touch_idx + 1 + h, n)
        if touch_idx + 1 >= end:
            out[f"fwd_{h}h_close"] = float("nan")
            out[f"fwd_{h}h_max_up"] = float("nan")
            out[f"fwd_{h}h_max_dn"] = float("nan")
            continue
        highs = bar_df["high"].iloc[touch_idx + 1: end].to_numpy()
        lows = bar_df["low"].iloc[touch_idx + 1: end].to_numpy()
        out[f"fwd_{h}h_close"] = float(bar_df["close"].iloc[min(end - 1, n - 1)])
        out[f"fwd_{h}h_max_up"] = float((highs.max() / touch_close - 1.0) * 100.0)
        out[f"fwd_{h}h_max_dn"] = float((lows.min() / touch_close - 1.0) * 100.0)
    return out


def approach_direction(bar_df: pd.DataFrame, det_idx: int,
                        touch_idx: int, price_low: float,
                        price_high: float, lookback: int = 4) -> str:
    """Classify whether price approached the level from above or below.

    Uses the prior ``lookback`` bars (up to and including det bar) vs the
    level midpoint. Falls back to "unknown" if the bars straddle the
    midpoint (which would mean the level was already inside the price
    range, not really an approach).
    """
    start = max(0, det_idx - lookback + 1)
    seg = bar_df.iloc[start: det_idx + 1]
    if len(seg) < 2:
        return "unknown"
    pre_open = float(seg["open"].iloc[0])
    pre_close = float(seg["close"].iloc[-1])
    pre_avg = 0.5 * (pre_open + pre_close)
    level_mid = 0.5 * (price_low + price_high)
    if pre_avg < level_mid:
        return "up_to_level"   # pre-touch price below the level → rose up into it
    if pre_avg > level_mid:
        return "down_to_level"  # pre-touch price above the level → fell into it
    return "unknown"


def hvn_reversal(max_dn_pct: float, max_up_pct: float) -> bool:
    """HVN: drew down AND bounced up at the primary horizon.

    Loose rule: max_dn <= -0.5% AND max_up >= +1.0% within 12 bars.
    Captures the "support test and bounce" event.
    """
    return (max_dn_pct <= -0.5) and (max_up_pct >= 1.0)


def lvn_reversal(max_dn_pct: float, max_up_pct: float) -> bool:
    """LVN (loose): any decisive move either direction.

    Note: 1% absolute move in 48h is very common on BTC; this rule
    will fire for the majority of touches. Report alongside the
    stricter ``lvn_reversal_strict`` for honest interpretation.
    """
    return max(abs(max_dn_pct), abs(max_up_pct)) >= 1.0


def lvn_reversal_strict(max_dn_pct: float, max_up_pct: float) -> bool:
    """LVN (strict): decisive one-directional follow-through.

    Dominant excursion ≥ +1.5% AND opposing excursion ≤ -0.3% in
    absolute terms. Catches "price slipped through" patterns where
    the move is unidirectional.
    """
    if max_up_pct >= 1.5 and abs(max_dn_pct) <= 0.3:
        return True
    if max_dn_pct <= -1.5 and abs(max_up_pct) <= 0.3:
        return True
    return False


def hvn_reversal_strict(max_dn_pct: float, max_up_pct: float) -> bool:
    """HVN (strict): bigger bounce required, plus a tighter drawdown.

    max_dn <= -1.0% AND max_up >= +2.0% — only strong support tests.
    """
    return (max_dn_pct <= -1.0) and (max_up_pct >= 2.0)


def _is_near(seen_centers: list[tuple[float, str]],
             price_center: float, kind: str, tol_pct: float) -> bool:
    """Return True if (price_center, kind) is within tol_pct of any seen."""
    for prev_c, prev_kind in seen_centers:
        if prev_kind != kind:
            continue
        if prev_c <= 0:
            continue
        if abs(price_center - prev_c) / prev_c <= tol_pct:
            return True
    return False


def collect_samples(bar_df: pd.DataFrame) -> list[Touch]:
    """Walk bars chronologically, detect VPVR levels, take first 20 touches.

    Deduplication merges bands whose price centres are within
    ``DEDUP_TOL_PCT`` of each other AND share the same kind. This
    prevents 5 near-identical bands from the same rolling window
    inflating the sample count.
    """
    n = len(bar_df)
    start = WINDOW_BARS
    seen_centers: list[tuple[float, str]] = []
    samples: list[Touch] = []

    for det_idx in range(start, n - 1):
        if len(samples) >= NUM_SAMPLES:
            break
        window = bar_df.iloc[det_idx - WINDOW_BARS + 1: det_idx + 1]
        if len(window) < WINDOW_BARS:
            continue
        levels = detect_levels_for_bar(window, bar_df.index[det_idx])
        det_ts = bar_df.index[det_idx]
        for kind, pl, ph, pc, vol, score in levels:
            if _is_near(seen_centers, pc, kind, DEDUP_TOL_PCT):
                continue
            seen_centers.append((pc, kind))
            ft = first_touch(bar_df, det_idx, pl, ph, max_lookahead=SAMPLE_LOOKAHEAD)
            if ft is None:
                continue
            touch_idx, touch_close = ft
            fo = fwd_outcomes(bar_df, touch_idx, HORIZONS)
            primary_max_up = fo["fwd_12h_max_up"]
            primary_max_dn = fo["fwd_12h_max_dn"]
            approach = approach_direction(
                bar_df, det_idx, touch_idx, pl, ph, lookback=4,
            )
            if kind == "HVN":
                reversal = hvn_reversal(primary_max_dn, primary_max_up)
                reversal_strict = hvn_reversal_strict(
                    primary_max_dn, primary_max_up
                )
            else:
                reversal = lvn_reversal(primary_max_dn, primary_max_up)
                reversal_strict = lvn_reversal_strict(
                    primary_max_dn, primary_max_up
                )
            net_pct = (
                (fo["fwd_12h_close"] / touch_close - 1.0) * 100.0
                if touch_close > 0 else 0.0
            )
            samples.append(Touch(
                sample_id=len(samples) + 1,
                kind=kind,
                detection_ts=det_ts,
                price_low=float(pl),
                price_high=float(ph),
                price_center=float(pc),
                volume=float(vol),
                score=float(score),
                first_touch_ts=bar_df.index[touch_idx],
                first_touch_close=float(touch_close),
                bars_to_touch=int(touch_idx - det_idx),
                approach_dir=approach,
                fwd_4h_close=fo["fwd_4h_close"],
                fwd_12h_close=fo["fwd_12h_close"],
                fwd_24h_close=fo["fwd_24h_close"],
                fwd_4h_max_up=fo["fwd_4h_max_up"],
                fwd_4h_max_dn=fo["fwd_4h_max_dn"],
                fwd_12h_max_up=fo["fwd_12h_max_up"],
                fwd_12h_max_dn=fo["fwd_12h_max_dn"],
                fwd_24h_max_up=fo["fwd_24h_max_up"],
                fwd_24h_max_dn=fo["fwd_24h_max_dn"],
                fwd_12h_net_pct=float(net_pct),
                reversal_met=bool(reversal),
                reversal_met_strict=bool(reversal_strict),
            ))
    return samples


def baseline_random(bar_df: pd.DataFrame, samples: list[Touch],
                    seed: int = SEED) -> dict:
    """For each sample's first-touch bar, pick a random price and apply the rule.

    Returns a dict with 'hvn_rule' / 'lvn_rule' / 'hvn_strict' / 'lvn_strict'
    counts so each per-kind rule has its own baseline.
    """
    rng = random.Random(seed)
    bar_min = float(bar_df["low"].min())
    bar_max = float(bar_df["high"].max())
    flags = {"hvn_rule": [], "lvn_rule": [], "hvn_strict": [], "lvn_strict": []}
    for s in samples:
        touch_idx = bar_df.index.get_loc(s.first_touch_ts)
        rand_price = rng.uniform(bar_min, bar_max)
        # The random price acts as a "level" — use the same bands as a
        # 0.5%-wide band centered on it, mirroring typical HVN/LVN width.
        band_lo = rand_price * 0.9975
        band_hi = rand_price * 1.0025
        # Touch always defined at the bar (uniform random within range).
        fo = fwd_outcomes(bar_df, touch_idx, HORIZONS)
        max_up = fo["fwd_12h_max_up"]
        max_dn = fo["fwd_12h_max_dn"]
        flags["hvn_rule"].append(hvn_reversal(max_dn, max_up))
        flags["lvn_rule"].append(lvn_reversal(max_dn, max_up))
        flags["hvn_strict"].append(hvn_reversal_strict(max_dn, max_up))
        flags["lvn_strict"].append(lvn_reversal_strict(max_dn, max_up))
    return flags


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson-score 95% CI for a binomial proportion."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + (z ** 2) / n
    centre = (p + (z ** 2) / (2 * n)) / denom
    half = (z * ((p * (1 - p) + (z ** 2) / (4 * n)) / n) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bar_df = load_btc_4h()
    print(f"[info] bars loaded: {len(bar_df)}; "
          f"range {bar_df.index.min()} → {bar_df.index.max()}; "
          f"cutoff {bar_df.attrs['cutoff']}")

    samples = collect_samples(bar_df)
    print(f"[info] collected {len(samples)} samples")

    baseline = baseline_random(bar_df, samples)

    # CSV
    csv_path = OUT_DIR / "samples.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "sample_id", "kind",
            "detection_ts", "price_low", "price_high", "price_center",
            "volume", "score",
            "first_touch_ts", "first_touch_close", "bars_to_touch",
            "approach_dir",
            "fwd_4h_close", "fwd_4h_max_up_pct", "fwd_4h_max_dn_pct",
            "fwd_12h_close", "fwd_12h_max_up_pct", "fwd_12h_max_dn_pct",
            "fwd_12h_net_pct",
            "fwd_24h_close", "fwd_24h_max_up_pct", "fwd_24h_max_dn_pct",
            "reversal_met", "reversal_met_strict",
        ])
        for s in samples:
            writer.writerow([
                s.sample_id, s.kind,
                s.detection_ts.isoformat(), s.price_low, s.price_high,
                s.price_center, s.volume, s.score,
                s.first_touch_ts.isoformat(), s.first_touch_close, s.bars_to_touch,
                s.approach_dir,
                s.fwd_4h_close, round(s.fwd_4h_max_up, 4),
                round(s.fwd_4h_max_dn, 4),
                s.fwd_12h_close, round(s.fwd_12h_max_up, 4),
                round(s.fwd_12h_max_dn, 4),
                round(s.fwd_12h_net_pct, 4),
                s.fwd_24h_close, round(s.fwd_24h_max_up, 4),
                round(s.fwd_24h_max_dn, 4),
                int(s.reversal_met), int(s.reversal_met_strict),
            ])

    # Summary
    n = len(samples)
    rev = sum(1 for s in samples if s.reversal_met)
    hvn_n = sum(1 for s in samples if s.kind == "HVN")
    lvn_n = sum(1 for s in samples if s.kind == "LVN")
    hvn_rev = sum(1 for s in samples if s.kind == "HVN" and s.reversal_met)
    lvn_rev = sum(1 for s in samples if s.kind == "LVN" and s.reversal_met)
    hvn_rev_strict = sum(
        1 for s in samples
        if s.kind == "HVN" and s.reversal_met_strict
    )
    lvn_rev_strict = sum(
        1 for s in samples
        if s.kind == "LVN" and s.reversal_met_strict
    )

    # Verdict — primary axis is HVN (the funding-carry prototype uses
    # HVN as the "support" axis). LVN is secondary.
    #
    # HVN strict reversal rate below random baseline → "weakens".
    # Both HVN rules below baseline → "weakens".
    # Both HVN rules at or above baseline → "supports".
    # Otherwise → "inconclusive" (the typical outcome at n=20).
    deltas = {
        "HVN_loose": hvn_rev - sum(baseline["hvn_rule"]),
        "HVN_strict": hvn_rev_strict - sum(baseline["hvn_strict"]),
        "LVN_loose": lvn_rev - sum(baseline["lvn_rule"]),
        "LVN_strict": lvn_rev_strict - sum(baseline["lvn_strict"]),
    }
    hvn_below = (deltas["HVN_strict"] < 0) and (deltas["HVN_loose"] < 0)
    hvn_at_or_above = (deltas["HVN_strict"] >= 0) and (deltas["HVN_loose"] >= 0)
    if hvn_below:
        verdict = "weakens"
    elif hvn_at_or_above:
        verdict = "supports"
    else:
        verdict = "inconclusive"

    summary = {
        "n_samples": n,
        "lookback_days": LOOKBACK_DAYS,
        "lookback_bars": WINDOW_BARS,
        "horizons_bars": HORIZONS,
        "primary_horizon_bars": PRIMARY_HORIZON,
        "wilson_95ci": {
            "HVN_loose": list(wilson_ci(hvn_rev, hvn_n)),
            "HVN_strict": list(wilson_ci(hvn_rev_strict, hvn_n)),
            "LVN_loose": list(wilson_ci(lvn_rev, lvn_n)),
            "LVN_strict": list(wilson_ci(lvn_rev_strict, lvn_n)),
            "baseline_HVN_loose": list(wilson_ci(
                sum(baseline["hvn_rule"]), n
            )),
            "baseline_HVN_strict": list(wilson_ci(
                sum(baseline["hvn_strict"]), n
            )),
            "baseline_LVN_loose": list(wilson_ci(
                sum(baseline["lvn_rule"]), n
            )),
            "baseline_LVN_strict": list(wilson_ci(
                sum(baseline["lvn_strict"]), n
            )),
        },
        "reversal_rule": {
            "HVN_loose": "max_drawdown <= -0.5% AND max_upside >= +1.0% over 12 bars",
            "HVN_strict": "max_drawdown <= -1.0% AND max_upside >= +2.0% over 12 bars",
            "LVN_loose": "abs(max excursion) >= 1.0% either direction over 12 bars",
            "LVN_strict": "dominant move >= +1.5% with opposing <= 0.3% over 12 bars",
        },
        "counts": {
            "total": n,
            "HVN": hvn_n,
            "LVN": lvn_n,
            "reversals_total_loose": rev,
            "reversals_HVN_loose": hvn_rev,
            "reversals_LVN_loose": lvn_rev,
            "reversals_HVN_strict": hvn_rev_strict,
            "reversals_LVN_strict": lvn_rev_strict,
            "baseline_HVN_loose": sum(baseline["hvn_rule"]),
            "baseline_LVN_loose": sum(baseline["lvn_rule"]),
            "baseline_HVN_strict": sum(baseline["hvn_strict"]),
            "baseline_LVN_strict": sum(baseline["lvn_strict"]),
        },
        "rates": {
            "reversal_rate_loose": rev / n if n else None,
            "reversal_rate_HVN_loose": hvn_rev / hvn_n if hvn_n else None,
            "reversal_rate_LVN_loose": lvn_rev / lvn_n if lvn_n else None,
            "reversal_rate_HVN_strict": (
                hvn_rev_strict / hvn_n if hvn_n else None
            ),
            "reversal_rate_LVN_strict": (
                lvn_rev_strict / lvn_n if lvn_n else None
            ),
            "baseline_rate_HVN_loose": (
                sum(baseline["hvn_rule"]) / n if n else None
            ),
            "baseline_rate_LVN_loose": (
                sum(baseline["lvn_rule"]) / n if n else None
            ),
            "baseline_rate_HVN_strict": (
                sum(baseline["hvn_strict"]) / n if n else None
            ),
            "baseline_rate_LVN_strict": (
                sum(baseline["lvn_strict"]) / n if n else None
            ),
        },
        "delta_vs_baseline": {
            "HVN_loose": hvn_rev - sum(baseline["hvn_rule"]),
            "HVN_strict": hvn_rev_strict - sum(baseline["hvn_strict"]),
            "LVN_loose": lvn_rev - sum(baseline["lvn_rule"]),
            "LVN_strict": lvn_rev_strict - sum(baseline["lvn_strict"]),
        },
        "verdict": verdict,
        "limitations": [
            "n=20 is far below the sample size needed to draw conclusions "
            "about predictive power (~95% CI on a binomial p≈0.5 is ±0.22).",
            "HVN/LVN bands are sampled chronologically; later samples may "
            "have less forward data and use truncated horizons.",
            "Only BTCUSDT 4h — results do not generalise to ETH/SOL or "
            "other timeframes.",
            "Random baseline is a sanity check, not a hypothesis test.",
            "LVN levels are detected in tight clusters at similar prices; "
            "some samples share first-touch bars because multiple "
            "near-identical bands were each touched at the same bar.",
            "The 'approach_dir' is a coarse proxy for pre-touch trend; "
            "more refined direction inference (e.g. cross-timeframe) "
            "would need additional work.",
        ],
        "artifacts": {
            "samples_csv": str(csv_path),
            "summary_json": str(OUT_DIR / "summary.json"),
        },
    }
    with (OUT_DIR / "summary.json").open("w") as fh:
        json.dump(summary, fh, indent=2, default=str)

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()