"""
VPVR edge-limit reversion — round-2 (finer VPVR + 4h window + z-confirm trigger).

Round-1 was KILLED on 9/9 (sym × horizon) cells under honest +1d shift on vanilla
200-bucket daily kline proxy. This round-2 attempts a methodology change per smark
directive (SMA-36661):

  (1) Finer VPVR: target bucket width 5bp (well below 10bp ceiling). Round-1 used
      200 buckets across [low.min, high.max] of each day → ~25-50bp wide per bucket.
      Round-2 uses bucket width = 5bp, so profile has 100-200 buckets per day.
  (2) Intra-bar uniform distribution: round-1 assigned bar volume to its close-price
      bucket only. Round-2 distributes each bar's volume uniformly across its
      [low, high] range, giving a finer proxy of where volume actually traded.
  (3) 4h profile window (vs daily in round-1). Profile updates 6× more often,
      so HVN/LVN reflect the most recent 4h consensus, not yesterday's.
  (4) Confirmation trigger: 24h-prior return z-score must clear threshold in the
      direction of the LVN touch. Long (entry at lower LVN) requires z < -threshold
      (price dropped INTO the LVN, suggesting reversion candidate). Short requires
      z > +threshold. Filters out free-fall/walk-about noise.

Discipline preserved from round-1:
  +4h shift (signal_time = end of profile-building 4h bucket)
  First-touch probability framing
  Cost assumption changed: maker 0.8bp / taker 2bp per side per SMA-36660
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica_workspaces/f9a9d34e-b809-4564-b0c0-b781a70a3f25/e247b18a/workdir")
# IMPORTANT: keep ROOT pointing at quant-loop so DATA + OUT paths resolve.
QUANT_LOOP = Path("/Users/mark/multica/quant-loop")
DATA = QUANT_LOOP / "data/perp_1m"
OUT = QUANT_LOOP / "research/vpvr_edge_reversion"

# Round-2 cost assumption (SMA-36660): maker 0.8bp / taker 2bp per side.
COST_MAKER_BP_PER_SIDE = 0.8
COST_TAKER_BP_PER_SIDE = 2.0
# A TP1 hit: enter maker (LVN edge), exit taker (HVN center).
# A SL hit: enter maker, exit taker (SL level).
# A dropout hit: enter maker, exit taker (dropout level).
# So round-trip cost per leg = 0.8 + 2.0 = 2.8bp.
COST_RT_BP = COST_MAKER_BP_PER_SIDE + COST_TAKER_BP_PER_SIDE


@dataclass
class Setup:
    symbol: str
    window_end: pd.Timestamp  # signal_time = end of profile-building 4h bucket
    direction: str            # "long" | "short"
    hvn: float
    entry_price: float        # LVN edge
    tp1_price: float          # HVN center
    tp2_price: float          # opposite LVN edge
    sl_price: float           # opposite LVN + full_range (runaway side)
    entry_dropout_price: float  # opposite side from entry by full_range (defensive)
    half_range_bps: float
    full_range_bps: float
    z24_at_signal: float      # 24h-prior return z-score (confirmation trigger)
    z_threshold: float        # threshold used (so we can vary later)


def vpvr_profile_finer(bars: pd.DataFrame, target_bucket_bp: float = 5.0) -> pd.DataFrame:
    """Return price-bucketed volume distribution with intra-bar uniform spread.

    Each bar's `quote_volume` is distributed uniformly across its [low, high]
    range across all buckets whose centers fall within [low, high]. This is a
    finer proxy than close-only assignment: bars with wide ranges contribute
    to many buckets instead of one.

    target_bucket_bp: target bucket width in basis points. Actual bucket count
    is computed from the bar range so width ≈ target_bucket_bp.
    """
    if len(bars) == 0:
        return pd.DataFrame(columns=["price", "volume"])
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    if hi <= lo:
        return pd.DataFrame(columns=["price", "volume"])

    mid = 0.5 * (lo + hi)
    bucket_abs = mid * target_bucket_bp / 1e4
    if bucket_abs <= 0:
        return pd.DataFrame(columns=["price", "volume"])
    n_buckets = max(20, int(np.ceil((hi - lo) / bucket_abs)))
    # Cap to avoid pathological cases (very quiet windows).
    n_buckets = min(n_buckets, 5000)

    edges = np.linspace(lo, hi, n_buckets + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    bucket_vol = np.zeros(n_buckets)

    lows = bars["low"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    vols = bars["quote_volume"].to_numpy(dtype=float)

    # Vectorized intra-bar distribution: for each bar, compute the bucket range
    # it covers (vectorized via searchsorted), then vectorized per-bar weight
    # computation across the touched bucket indices.
    bucket_lo_arr = edges[:-1]  # (n_buckets,)
    bucket_hi_arr = edges[1:]   # (n_buckets,)

    # Clip bar ranges to window.
    lows_c = np.clip(lows, lo, hi)
    highs_c = np.clip(highs, lo, hi)
    spans = highs_c - lows_c
    valid = (spans > 0) & (vols > 0)

    i_lo_per_bar = np.clip(np.searchsorted(edges, lows_c[valid], side="right") - 1,
                            0, n_buckets - 1)
    i_hi_per_bar = np.clip(np.searchsorted(edges, highs_c[valid], side="left"),
                            0, n_buckets)

    valid_vols = vols[valid]
    valid_lows = lows_c[valid]
    valid_highs = highs_c[valid]
    valid_spans = spans[valid]

    # Per-bar vectorized weight computation: for each bar i, compute weights
    # across its covered buckets [i_lo_per_bar[i], i_hi_per_bar[i]).
    for i in range(len(valid_vols)):
        i_lo = int(i_lo_per_bar[i])
        i_hi = int(i_hi_per_bar[i])
        if i_hi <= i_lo:
            continue
        bl = bucket_lo_arr[i_lo:i_hi]
        bh = bucket_hi_arr[i_lo:i_hi]
        overlap = np.maximum(0.0, np.minimum(bh, valid_highs[i]) - np.maximum(bl, valid_lows[i]))
        w = overlap / valid_spans[i]
        bucket_vol[i_lo:i_hi] += w * valid_vols[i]

    return pd.DataFrame({"price": centers, "volume": bucket_vol})


def find_hvn_lvn(profile: pd.DataFrame, smooth_sigma_buckets: int = 2) -> tuple[float, float, float]:
    """Identify HVN center + nearest LVN on each side (lighter smoothing for finer profile)."""
    if len(profile) < 5:
        return (np.nan, np.nan, np.nan)
    vol = profile["volume"].to_numpy(dtype=float)
    prices = profile["price"].to_numpy(dtype=float)

    if smooth_sigma_buckets > 1:
        kernel = np.exp(-0.5 * (np.arange(-smooth_sigma_buckets * 3,
                                           smooth_sigma_buckets * 3 + 1) / smooth_sigma_buckets) ** 2)
        kernel = kernel / kernel.sum()
        vol = np.convolve(vol, kernel, mode="same")

    hvn_idx = int(np.argmax(vol))
    hvn_price = float(prices[hvn_idx])

    if hvn_idx < 3:
        lvn_lower_price = float(prices[0])
    else:
        seg = vol[:hvn_idx]
        low_threshold = np.percentile(seg, 20)
        candidates = np.where(seg <= low_threshold)[0]
        if len(candidates) == 0:
            lvn_lower_price = float(prices[0])
        else:
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
    half_range_bps_lower: float
    half_range_bps_upper: float
    full_range_bps: float
    n_buckets: int


def fourh_metrics_finer(df: pd.DataFrame, symbol: str, target_bucket_bp: float = 5.0) -> pd.DataFrame:
    """Per-4h VPVR profile with finer buckets + intra-bar distribution.

    No-look-ahead: profile uses bars of 4h bucket B; signal_time = end of bucket B.
    The future horizon (4h or 1d) starts at signal_time and uses bars AFTER bucket B.
    """
    df = df.copy()
    df["bucket_4h"] = df["ts"].dt.tz_convert(None).dt.floor("4h")
    rows = []
    for bucket, group in df.groupby("bucket_4h"):
        if len(group) < 60:
            continue
        profile = vpvr_profile_finer(group, target_bucket_bp=target_bucket_bp)
        hvn, lvn_lo, lvn_hi = find_hvn_lvn(profile)
        if not (np.isfinite(hvn) and np.isfinite(lvn_lo) and np.isfinite(lvn_hi)):
            continue
        if lvn_lo >= hvn or hvn >= lvn_hi:
            continue
        # +4h shift (look-ahead fix 2026-07-26): signal_time = END of profile-building
        # bucket, NOT start. Profile uses bucket B's bars; future bars must come from
        # bucket B+1 onwards. Without this +4h shift, future bars include bucket B's
        # own bars (which built the profile) — pure 4h look-ahead. Same bug class as
        # round-1's daily_metrics +1d shift correction.
        bucket_end = pd.Timestamp(bucket) + pd.Timedelta(hours=4)
        rows.append(WindowMetrics(
            symbol=symbol,
            window_end=bucket_end,
            hvn_price=hvn,
            lvn_lower_price=lvn_lo,
            lvn_upper_price=lvn_hi,
            half_range_bps_lower=(hvn - lvn_lo) / hvn * 1e4,
            half_range_bps_upper=(lvn_hi - hvn) / hvn * 1e4,
            full_range_bps=(lvn_hi - lvn_lo) / hvn * 1e4,
            n_buckets=len(profile),
        ))
    return pd.DataFrame(rows)


def compute_z24_at_signal(df_indexed: pd.DataFrame, signal_time: pd.Timestamp,
                          lookback_bars: int = 1440, min_bars: int = 200) -> float:
    """24h-prior return z-score using rolling 4h return std as denominator.

    Returns the z-score of (signal_close − lookback_close) / rolling_std.
    Uses last `lookback_bars` bars BEFORE signal_time (no future leak).
    Returns NaN if insufficient history.
    """
    prior = df_indexed.loc[df_indexed.index < signal_time].tail(lookback_bars + 1)
    if len(prior) < min_bars:
        return np.nan
    closes = prior["close"].to_numpy(dtype=float)
    # Rolling 4h return std (24 lags of 1m bars = 240 bars; use 60 for shorter std window).
    rets_4h = np.diff(closes[-(60 * 4 + 1):]) / closes[-(60 * 4 + 1):-1]
    std_4h = float(np.nanstd(rets_4h))
    if std_4h <= 0 or not np.isfinite(std_4h):
        return np.nan
    sig_close = float(prior["close"].iloc[-1])
    look_close = float(prior["close"].iloc[0])
    z = (sig_close - look_close) / look_close / std_4h
    return float(z)


def make_setups(metrics_df: pd.DataFrame, df_indexed: pd.DataFrame,
                z_threshold: float = 1.0) -> list[Setup]:
    """Build long + short setups with z-score confirmation trigger.

    Long entry (lower LVN): require z24 < -threshold (price dropped into LVN).
    Short entry (upper LVN): require z24 > +threshold (price rose into LVN).
    """
    setups = []
    for _, row in metrics_df.iterrows():
        hvn = row["hvn_price"]
        lo_lvn = row["lvn_lower_price"]
        hi_lvn = row["lvn_upper_price"]
        full_range_bps = row["full_range_bps"]
        if not (np.isfinite(hvn) and np.isfinite(lo_lvn) and np.isfinite(hi_lvn)):
            continue
        if lo_lvn >= hvn or hvn >= hi_lvn:
            continue
        full_range_abs = full_range_bps / 1e4 * hvn

        z24 = compute_z24_at_signal(df_indexed, row["window_end"])
        if not np.isfinite(z24):
            continue  # filter setups without z (insufficient prior history)

        # LONG (lower LVN entry): price dropped into LVN → z24 < -threshold
        if z24 < -z_threshold:
            setups.append(Setup(
                symbol=row["symbol"],
                window_end=row["window_end"],
                direction="long",
                hvn=hvn,
                entry_price=lo_lvn,
                tp1_price=hvn,
                tp2_price=hi_lvn,
                sl_price=hi_lvn + full_range_abs,
                entry_dropout_price=lo_lvn - full_range_abs,
                half_range_bps=row["half_range_bps_lower"],
                full_range_bps=full_range_bps,
                z24_at_signal=z24,
                z_threshold=z_threshold,
            ))

        # SHORT (upper LVN entry): price rose into LVN → z24 > +threshold
        if z24 > z_threshold:
            setups.append(Setup(
                symbol=row["symbol"],
                window_end=row["window_end"],
                direction="short",
                hvn=hvn,
                entry_price=hi_lvn,
                tp1_price=hvn,
                tp2_price=lo_lvn,
                sl_price=lo_lvn - full_range_abs,
                entry_dropout_price=hi_lvn + full_range_abs,
                half_range_bps=row["half_range_bps_upper"],
                full_range_bps=full_range_bps,
                z24_at_signal=z24,
                z_threshold=z_threshold,
            ))
    return setups


def simulate_setup(setup: Setup, future_bars: pd.DataFrame) -> dict:
    """Same first-touch logic as round-1 but with round-2 cost deduction."""
    n = len(future_bars)
    if n == 0:
        return {"status": "no_future_data"}

    highs = future_bars["high"].to_numpy(dtype=float)
    lows = future_bars["low"].to_numpy(dtype=float)
    closes = future_bars["close"].to_numpy(dtype=float)
    times = future_bars["ts"].to_numpy()

    entry = setup.entry_price
    tp1 = setup.tp1_price
    tp2 = setup.tp2_price
    sl = setup.sl_price
    dropout = setup.entry_dropout_price

    if setup.direction == "long":
        fill_mask = lows <= entry
    else:
        fill_mask = highs >= entry

    fill_indices = np.where(fill_mask)[0]
    if len(fill_indices) == 0:
        return {
            "status": "no_fill",
            "scenario_a": "no_fill",
            "scenario_b": "no_fill",
            "bars_to_fill": n,
            "horizon_used": n,
        }

    fill_idx = int(fill_indices[0])
    fill_time = pd.Timestamp(times[fill_idx])

    after_highs = highs[fill_idx:]
    after_lows = lows[fill_idx:]
    after_closes = closes[fill_idx:]

    if setup.direction == "long":
        tp1_mask = after_highs >= tp1
        tp2_mask = after_highs >= tp2
        sl_mask = after_highs >= sl
        dropout_mask = after_lows <= dropout
    else:
        tp1_mask = after_lows <= tp1
        tp2_mask = after_lows <= tp2
        sl_mask = after_lows <= sl
        dropout_mask = after_highs >= dropout

    tp1_idxs = np.where(tp1_mask)[0]
    tp2_idxs = np.where(tp2_mask)[0]
    sl_idxs = np.where(sl_mask)[0]
    drop_idxs = np.where(dropout_mask)[0]

    first_tp1 = int(tp1_idxs[0]) if len(tp1_idxs) else None
    first_tp2 = int(tp2_idxs[0]) if len(tp2_idxs) else None
    first_sl = int(sl_idxs[0]) if len(sl_idxs) else None
    first_drop = int(drop_idxs[0]) if len(drop_idxs) else None

    # Scenario A (literal): TP1 / TP2 / SL tracked; downside = MTM at horizon.
    candidates_a = []
    if first_tp1 is not None:
        candidates_a.append((first_tp1, "tp1_first"))
    if first_tp2 is not None:
        candidates_a.append((first_tp2, "tp2_first"))
    if first_sl is not None:
        candidates_a.append((first_sl, "sl_first"))
    if candidates_a:
        idx_a, status_a = min(candidates_a)
        if status_a == "tp1_first":
            mark_a = setup.half_range_bps
        elif status_a == "tp2_first":
            mark_a = (setup.tp2_price - entry) / entry * 1e4 if setup.direction == "long" else (entry - setup.tp2_price) / entry * 1e4
        else:
            mark_a = -setup.full_range_bps
    else:
        status_a = "no_exit_in_horizon"
        last_close = closes[-1]
        if setup.direction == "long":
            mark_a = (last_close - entry) / entry * 1e4
        else:
            mark_a = (entry - last_close) / entry * 1e4

    # Scenario B (defensive): TP1 vs. SL vs. DROP (level break either side).
    candidates_b = []
    if first_tp1 is not None:
        candidates_b.append((first_tp1, "tp1_first"))
    if first_sl is not None:
        candidates_b.append((first_sl, "sl_first"))
    if first_drop is not None:
        candidates_b.append((first_drop, "dropout_first"))
    if candidates_b:
        idx_b, status_b = min(candidates_b)
        if status_b == "tp1_first":
            mark_b = setup.half_range_bps
        elif status_b == "sl_first":
            mark_b = -setup.full_range_bps
        else:
            mark_b = -setup.full_range_bps
    else:
        status_b = "no_exit_in_horizon"
        last_close = closes[-1]
        if setup.direction == "long":
            mark_b = (last_close - entry) / entry * 1e4
        else:
            mark_b = (entry - last_close) / entry * 1e4

    last_close = closes[-1]
    if setup.direction == "long":
        mtm = (last_close - entry) / entry * 1e4
    else:
        mtm = (entry - last_close) / entry * 1e4

    # Apply round-2 cost (maker entry + taker exit). Subtract RT cost from each exit mark.
    mark_a_net = mark_a - COST_RT_BP
    mark_b_net = mark_b - COST_RT_BP

    return {
        "scenario_a": status_a,
        "scenario_a_markout_bps": float(mark_a),
        "scenario_a_markout_net_bps": float(mark_a_net),
        "scenario_b": status_b,
        "scenario_b_markout_bps": float(mark_b),
        "scenario_b_markout_net_bps": float(mark_b_net),
        "mtm_at_horizon_end_bps": float(mtm),
        "bars_to_fill": fill_idx,
        "horizon_used": n - fill_idx,
        "fill_time": fill_time.isoformat(),
    }


def first_touch_run(symbol: str, lookback_days: int = 730,
                    horizon_bars: int = 240,
                    target_bucket_bp: float = 5.0,
                    z_threshold: float = 1.0) -> pd.DataFrame:
    print(f"[round2] {symbol}: loading {lookback_days}d of 1m klines (4h finer VPVR, z>={z_threshold})...")
    df = pd.read_parquet(DATA / f"{symbol}_1m.parquet",
                         columns=["open_time", "open", "high", "low", "close", "volume", "quote_volume"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    cutoff = df["ts"].max() - pd.Timedelta(days=lookback_days)
    df = df[df["ts"] >= cutoff].reset_index(drop=True)

    print(f"  building finer 4h metrics (target bucket={target_bucket_bp}bp)...")
    metrics = fourh_metrics_finer(df, symbol, target_bucket_bp=target_bucket_bp)
    metrics["window_end"] = pd.to_datetime(metrics["window_end"]).dt.tz_localize("UTC")
    print(f"  4h windows with valid HVN/LVN: {len(metrics)}")

    df_idx = df.set_index("ts")
    setups = make_setups(metrics, df_idx, z_threshold=z_threshold)
    print(f"  setups after z-confirm filter (|z24|>{z_threshold}): {len(setups)}")

    rows = []
    for setup in setups:
        signal_time = setup.window_end
        future = df_idx.loc[df_idx.index > signal_time].head(horizon_bars).reset_index()
        if len(future) < 60:
            continue
        result = simulate_setup(setup, future)
        rows.append({**asdict(setup), **result})
    out = pd.DataFrame(rows)
    return out, len(metrics)


def summarize_first_touch(ft: pd.DataFrame, symbol: str) -> dict:
    if len(ft) == 0:
        return {"symbol": symbol, "n_setups": 0}

    def agg(sub: pd.DataFrame, scenario_status: str, scenario_markout: str) -> dict:
        n = len(sub)
        if n == 0:
            return {}
        fill_rate = float((sub[scenario_status] != "no_fill").mean())
        outcomes = sub[scenario_status].value_counts(normalize=True).to_dict()

        filled = sub[sub[scenario_status] != "no_fill"]
        if len(filled):
            tp1_rate = float((filled[scenario_status] == "tp1_first").mean())
            tp2_rate = float((filled[scenario_status] == "tp2_first").mean())
            sl_rate = float((filled[scenario_status] == "sl_first").mean())
            dropout_rate = float((filled[scenario_status] == "dropout_first").mean())
            no_exit_rate = float((filled[scenario_status] == "no_exit_in_horizon").mean())
            mean_mark = float(filled[scenario_markout].mean())
            med_mark = float(filled[scenario_markout].median())
        else:
            tp1_rate = tp2_rate = sl_rate = dropout_rate = no_exit_rate = 0.0
            mean_mark = med_mark = 0.0

        return {
            "n_setups": int(n),
            "fill_rate": fill_rate,
            "tp1_first_rate": tp1_rate,
            "tp2_first_rate": tp2_rate,
            "sl_first_rate": sl_rate,
            "dropout_first_rate": dropout_rate,
            "no_exit_in_horizon_rate": no_exit_rate,
            "mean_markout_filled_bps": mean_mark,
            "median_markout_filled_bps": med_mark,
            "outcomes_breakdown": outcomes,
        }

    out = {"symbol": symbol}
    for direction in ("long", "short"):
        sub = ft[ft["direction"] == direction]
        out[direction] = {
            "scenario_a_literal": agg(sub, "scenario_a", "scenario_a_markout_net_bps"),
            "scenario_b_defensive": agg(sub, "scenario_b", "scenario_b_markout_net_bps"),
        }
    out["combined"] = {
        "scenario_a_literal": agg(ft, "scenario_a", "scenario_a_markout_net_bps"),
        "scenario_b_defensive": agg(ft, "scenario_b", "scenario_b_markout_net_bps"),
    }
    return out


def main():
    SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    HORIZONS_BARS = {"4h": 240, "1d": 1440}
    TARGET_BUCKET_BP = 5.0
    Z_THRESHOLDS = [1.0]  # primary; can extend

    print(f"[round2] Cost: maker {COST_MAKER_BP_PER_SIDE}bp + taker {COST_TAKER_BP_PER_SIDE}bp = {COST_RT_BP}bp RT")
    print(f"[round2] VPVR bucket target: {TARGET_BUCKET_BP}bp")
    print(f"[round2] Profile window: 4h; Confirmation: z24 trigger z>{Z_THRESHOLDS[0]}")

    horizon_results = {}
    n_windows_per_sym = {}
    for horizon_label, horizon_bars in HORIZONS_BARS.items():
        print(f"\n=== Horizon: {horizon_label} ({horizon_bars} bars) ===")
        summary = {}
        for s in SYMS:
            ft, n_w = first_touch_run(s, lookback_days=730, horizon_bars=horizon_bars,
                                       target_bucket_bp=TARGET_BUCKET_BP,
                                       z_threshold=Z_THRESHOLDS[0])
            n_windows_per_sym[(horizon_label, s)] = n_w
            ft.to_parquet(OUT / f"round2_firsttouch_{s}_{horizon_label}.parquet", index=False)
            summary[s] = summarize_first_touch(ft, s)
            for scen in ("scenario_a_literal", "scenario_b_defensive"):
                side = "combined"
                d = summary[s][side][scen]
                if not d:
                    continue
                print(f"  [{s}] {horizon_label} {scen}: fill={d['fill_rate']:.2%}  "
                      f"tp1={d['tp1_first_rate']:.2%}  drop={d['dropout_first_rate']:.2%}  "
                      f"mean_net_mark_filled={d['mean_markout_filled_bps']:+.1f}bp")
        horizon_results[horizon_label] = summary

    # Master summary with both gross and net numbers.
    out_payload = {
        "config": {
            "target_bucket_bp": TARGET_BUCKET_BP,
            "horizon_bars": HORIZONS_BARS,
            "z_threshold": Z_THRESHOLDS[0],
            "cost_assumption_bp": {
                "maker_per_side": COST_MAKER_BP_PER_SIDE,
                "taker_per_side": COST_TAKER_BP_PER_SIDE,
                "round_trip": COST_RT_BP,
            },
            "profile_window": "4h",
            "intra_bar_distribution": "uniform over [low,high]",
            "lookback_days": 730,
            "kline_source": str(DATA),
            "comparison_vs_round1": {
                "round1_buckets": "200 across daily range (~25-50bp wide)",
                "round1_profile_window": "daily",
                "round1_confirm_trigger": "none",
                "round1_cost_assumption": "VIP0 9bp pair-RT (pre-SMA-36660)",
            },
        },
        "n_windows_per_symbol_horizon": {f"{h}_{s}": v for (h, s), v in n_windows_per_sym.items()},
        "results_by_horizon": horizon_results,
    }
    out_path = OUT / "round2_summary.json"
    with open(out_path, "w") as f:
        json.dump(out_payload, f, indent=2, default=str)
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()