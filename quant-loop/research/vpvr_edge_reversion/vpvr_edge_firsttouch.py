"""
First-touch probability for VPVR edge-limit reversion — corrected + honest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
DATA = ROOT / "data/perp_1m"
OUT = ROOT / "research/vpvr_edge_reversion"
HORIZON_BARS = 1440


@dataclass
class Setup:
    symbol: str
    window_end: pd.Timestamp
    direction: str            # "long" | "short"
    hvn: float
    entry_price: float        # LVN edge
    tp1_price: float          # HVN center
    tp2_price: float          # opposite LVN edge
    sl_price: float           # variant A — opposite LVN + full range (runaway side)
    entry_dropout_price: float  # variant B — opposite side from entry by full_range (defensive)
    half_range_bps: float
    full_range_bps: float


def make_setups(metrics_df: pd.DataFrame) -> list[Setup]:
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
        # full_range in absolute price terms (approximated via HVN center)
        full_range_abs = full_range_bps / 1e4 * hvn

        # LONG: entry at lower LVN; TP1=HVN; TP2=upper LVN; SL=upper LVN + full_range; DROP=lo_lvn - full_range
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
        ))
        # SHORT: entry at upper LVN; TP1=HVN; TP2=lower LVN; SL=lower LVN - full_range; DROP=hi_lvn + full_range
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
        ))
    return setups


def simulate_setup(setup: Setup, future_bars: pd.DataFrame) -> dict:
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

    # Find first fill bar.
    if setup.direction == "long":
        # Fill when low <= entry (price dips into our bid)
        fill_mask = lows <= entry
    else:
        # Fill when high >= entry (price spikes into our offer)
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

    # After fill, walk forward to first-touch of TP1, TP2, SL (runaway), DROP (downside break).
    after_highs = highs[fill_idx:]
    after_lows = lows[fill_idx:]
    after_closes = closes[fill_idx:]

    if setup.direction == "long":
        # Upward targets: tp1 (HVN, closer), tp2 (upper LVN), sl (beyond upper LVN by full_range)
        # Downside break: low <= dropout  (price closes below lower LVN by full_range — level broken)
        tp1_mask = after_highs >= tp1
        tp2_mask = after_highs >= tp2
        sl_mask = after_highs >= sl
        dropout_mask = after_lows <= dropout
    else:
        # Downward targets for short: tp1 (HVN, closer), tp2 (lower LVN), sl (below lower LVN)
        # Upside break: high >= dropout
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

    # Scenario A — literal smark: only TP1/TP2/SL (runaway side) tracked; downside = MTM at horizon.
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

    # Scenario B — defensive: TP1/early-reversion vs. SL-or-dropout (level break either side).
    candidates_b = []
    if first_tp1 is not None:
        candidates_b.append((first_tp1, "tp1_first"))
    if first_sl is not None:
        candidates_b.append((first_sl, "sl_first"))
    if first_drop is not None:
        candidates_b.append((first_drop, "dropout_first"))
    if candidates_b:
        # Tie-break: if dropout == sl, prefer sl_first (loss = full range); tie = dropout
        idx_b, status_b = min(candidates_b)
        if status_b == "tp1_first":
            mark_b = setup.half_range_bps
        elif status_b == "sl_first":
            mark_b = -setup.full_range_bps
        else:  # dropout_first
            mark_b = -setup.full_range_bps
    else:
        status_b = "no_exit_in_horizon"
        last_close = closes[-1]
        if setup.direction == "long":
            mark_b = (last_close - entry) / entry * 1e4
        else:
            mark_b = (entry - last_close) / entry * 1e4

    # Mark-to-market at horizon end regardless.
    last_close = closes[-1]
    if setup.direction == "long":
        mtm = (last_close - entry) / entry * 1e4
    else:
        mtm = (entry - last_close) / entry * 1e4

    return {
        "scenario_a": status_a,
        "scenario_a_markout_bps": float(mark_a),
        "scenario_b": status_b,
        "scenario_b_markout_bps": float(mark_b),
        "mtm_at_horizon_end_bps": float(mtm),
        "bars_to_fill": fill_idx,
        "horizon_used": n - fill_idx,
        "fill_time": fill_time.isoformat(),
    }


def first_touch_run(symbol: str, lookback_days: int = 730) -> pd.DataFrame:
    print(f"[firsttouch] {symbol}: loading {lookback_days}d of 1m klines...")
    df = pd.read_parquet(DATA / f"{symbol}_1m.parquet",
                         columns=["open_time", "open", "high", "low", "close"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    cutoff = df["ts"].max() - pd.Timedelta(days=lookback_days)
    df = df[df["ts"] >= cutoff].reset_index(drop=True)

    metrics_fp = OUT / f"daily_metrics_{symbol}.parquet"
    metrics = pd.read_parquet(metrics_fp)
    metrics["window_end"] = pd.to_datetime(metrics["window_end"]).dt.tz_localize(None)
    metrics["window_end"] = metrics["window_end"].dt.tz_localize("UTC")

    setups = make_setups(metrics)
    print(f"  setups: {len(setups)}")

    df = df.set_index("ts")
    rows = []
    for setup in setups:
        signal_time = setup.window_end
        future = df.loc[(df.index > signal_time)].head(HORIZON_BARS).reset_index()
        result = simulate_setup(setup, future)
        rows.append({**asdict(setup), **result})
    out = pd.DataFrame(rows)
    return out


def summarize_first_touch(ft: pd.DataFrame, symbol: str) -> dict:
    if len(ft) == 0:
        return {"symbol": symbol, "n_setups": 0}

    def agg(sub: pd.DataFrame, scenario_status: str, scenario_markout: str) -> dict:
        n = len(sub)
        if n == 0:
            return {}
        fill_rate = float((sub[scenario_status] != "no_fill").mean())
        outcomes = sub[scenario_status].value_counts(normalize=True).to_dict()

        # Conditional on fill
        filled = sub[sub[scenario_status] != "no_fill"]
        if len(filled):
            tp1_rate = float((filled[scenario_status] == "tp1_first").mean())
            tp2_rate = float((filled[scenario_status] == "tp2_first").mean())
            sl_rate = float((filled[scenario_status] == "sl_first").mean())
            dropout_rate = float((filled[scenario_status] == "dropout_first").mean())
            no_exit_rate = float((filled[scenario_status] == "no_exit_in_horizon").mean())
            mean_mark = float(filled[scenario_markout].mean())
            med_mark = float(filled[scenario_markout].median())
            mean_mark_mtm = float(filled["mtm_at_horizon_end_bps"].mean())
        else:
            tp1_rate = tp2_rate = sl_rate = dropout_rate = no_exit_rate = 0.0
            mean_mark = med_mark = mean_mark_mtm = 0.0

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
            "mean_mtm_at_horizon_end_filled_bps": mean_mark_mtm,
            "outcomes_breakdown": outcomes,
        }

    out = {"symbol": symbol}
    for direction in ("long", "short"):
        sub = ft[ft["direction"] == direction]
        out[direction] = {
            "scenario_a_literal": agg(sub, "scenario_a", "scenario_a_markout_bps"),
            "scenario_b_defensive": agg(sub, "scenario_b", "scenario_b_markout_bps"),
        }
    out["combined"] = {
        "scenario_a_literal": agg(ft, "scenario_a", "scenario_a_markout_bps"),
        "scenario_b_defensive": agg(ft, "scenario_b", "scenario_b_markout_bps"),
    }
    return out


def main():
    SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    summary = {}
    for s in SYMS:
        ft = first_touch_run(s)
        ft.to_parquet(OUT / f"firsttouch_{s}.parquet", index=False)
        summary[s] = summarize_first_touch(ft, s)
        for scen in ("scenario_a_literal", "scenario_b_defensive"):
            print(f"\n[{s}] {scen}:")
            for side in ("long", "short", "combined"):
                d = summary[s][side][scen] if side != "combined" else summary[s][side][scen]
                if not d:
                    continue
                print(f"  {side:8s}: fill={d['fill_rate']:.2%}  tp1={d['tp1_first_rate']:.2%}  "
                      f"tp2={d['tp2_first_rate']:.2%}  sl={d['sl_first_rate']:.2%}  "
                      f"drop={d['dropout_first_rate']:.2%}  noexit={d['no_exit_in_horizon_rate']:.2%}  "
                      f"mean_mark_filled={d['mean_markout_filled_bps']:+.1f}bp  "
                      f"mtm_end={d['mean_mtm_at_horizon_end_filled_bps']:+.1f}bp")
    with open(OUT / "firsttouch_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nSaved → {OUT}/firsttouch_summary.json")


if __name__ == "__main__":
    main()