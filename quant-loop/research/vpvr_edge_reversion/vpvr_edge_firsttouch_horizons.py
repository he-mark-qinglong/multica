"""
Multi-horizon first-touch — tests edge at maker-relevant timescales (1h, 4h, 1d).
Same setup logic as vpvr_edge_firsttouch.py; varies horizon_bars.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from vpvr_edge_firsttouch import make_setups, simulate_setup

ROOT = Path("/Users/mark/multica/quant-loop")
DATA = ROOT / "data/perp_1m"
OUT = ROOT / "research/vpvr_edge_reversion"

HORIZONS = {
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def run_for_horizon(symbol: str, horizon_bars: int, lookback_days: int = 730) -> pd.DataFrame:
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
    df = df.set_index("ts")
    rows = []
    for setup in setups:
        future = df.loc[(df.index > setup.window_end)].head(horizon_bars).reset_index()
        result = simulate_setup(setup, future)
        rows.append({**asdict(setup), **result})
    return pd.DataFrame(rows)


def summarize(sub: pd.DataFrame, scenario_status: str, scenario_markout: str) -> dict:
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
        drop_rate = float((filled[scenario_status] == "dropout_first").mean())
        noexit_rate = float((filled[scenario_status] == "no_exit_in_horizon").mean())
        mean_mark = float(filled[scenario_markout].mean())
    else:
        tp1_rate = tp2_rate = sl_rate = drop_rate = noexit_rate = 0.0
        mean_mark = 0.0
    # Conditional expected value at horizon (no_fill = 0 contribution).
    unconditional_mean = float(sub[scenario_markout].mean())
    return {
        "n": int(n),
        "fill_rate": fill_rate,
        "tp1_rate": tp1_rate,
        "tp2_rate": tp2_rate,
        "sl_rate": sl_rate,
        "dropout_rate": drop_rate,
        "noexit_rate": noexit_rate,
        "mean_markout_filled_bps": mean_mark,
        "unconditional_mean_markout_bps": unconditional_mean,
        "outcomes_breakdown": outcomes,
    }


def main():
    SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    out = {"horizons": list(HORIZONS.keys()), "symbols": SYMS, "data": {}}
    for sym in SYMS:
        out["data"][sym] = {}
        for label, horizon_bars in HORIZONS.items():
            print(f"[horizons] {sym} {label} ({horizon_bars} bars)...", flush=True)
            ft = run_for_horizon(sym, horizon_bars)
            ft.to_parquet(OUT / f"firsttouch_{sym}_{label}.parquet", index=False)
            d = {"scenario_a_literal": {}, "scenario_b_defensive": {}}
            for direction in ("long", "short"):
                sub = ft[ft["direction"] == direction]
                d["scenario_a_literal"][direction] = summarize(sub, "scenario_a", "scenario_a_markout_bps")
                d["scenario_b_defensive"][direction] = summarize(sub, "scenario_b", "scenario_b_markout_bps")
            # combined
            d["scenario_a_literal"]["combined"] = summarize(ft, "scenario_a", "scenario_a_markout_bps")
            d["scenario_b_defensive"]["combined"] = summarize(ft, "scenario_b", "scenario_b_markout_bps")
            out["data"][sym][label] = d
    with open(OUT / "firsttouch_horizons.json", "w") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n=== Summary (scenario_a_literal, combined) ===")
    print(f"{'symbol':8s} {'horizon':6s} {'fill':>5s} {'tp1':>5s} {'tp2':>5s} {'sl':>5s} {'noexit':>6s} {'mean_mark_bps':>14s} {'uncond_bps':>11s}")
    for sym in SYMS:
        for label in HORIZONS:
            d = out["data"][sym][label]["scenario_a_literal"]["combined"]
            print(f"{sym:8s} {label:6s} {d['fill_rate']:5.2%} {d['tp1_rate']:5.2%} {d['tp2_rate']:5.2%} "
                  f"{d['sl_rate']:5.2%} {d['noexit_rate']:6.2%} {d['mean_markout_filled_bps']:+14.1f} "
                  f"{d['unconditional_mean_markout_bps']:+11.1f}")

    print("\n=== Summary (scenario_b_defensive, combined) ===")
    print(f"{'symbol':8s} {'horizon':6s} {'fill':>5s} {'tp1':>5s} {'drop':>5s} {'sl':>5s} {'noexit':>6s} {'mean_mark_bps':>14s} {'uncond_bps':>11s}")
    for sym in SYMS:
        for label in HORIZONS:
            d = out["data"][sym][label]["scenario_b_defensive"]["combined"]
            print(f"{sym:8s} {label:6s} {d['fill_rate']:5.2%} {d['tp1_rate']:5.2%} {d['dropout_rate']:5.2%} "
                  f"{d['sl_rate']:5.2%} {d['noexit_rate']:6.2%} {d['mean_markout_filled_bps']:+14.1f} "
                  f"{d['unconditional_mean_markout_bps']:+11.1f}")


if __name__ == "__main__":
    main()