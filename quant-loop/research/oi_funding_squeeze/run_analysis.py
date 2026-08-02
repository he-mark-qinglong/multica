"""Run the OI x funding squeeze event study on real data.

Data sources (audit-by-replication enumeration, /tmp/quant_loop_data_files.txt):
  - OI      : data/oi/{sym}.parquet          (hourly, 2026-07-12 -> 2026-08-02)
  - funding : data/funding/{sym}.parquet     (8h, 2021-11 -> 2026-07-25)
  - price   : data/perp_30m/{sym}_30m.parquet (2022-01 -> 2026-07-24, all 7)
    (data/spot/*_1h.parquet ends 2026-06-30 -> ZERO overlap with OI, unusable;
     data/perp_1m only covers BTC/ETH/SOL)

Variants:
  SPEC    : daily grid, 20d z-window (task spec)
  LOOSE5  : daily grid, 5d z-window (generous robustness check)
  HOURLY5 : hourly grid, 120h z-window, fwd 24h/72h/168h (max-sample check;
            overlapping windows -> t-stats optimistic, noted in report)
"""
import json
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd

from research.oi_funding_squeeze.squeeze import (
    baseline_table, event_table, forward_returns, funding_to_daily,
    oi_change_z, oi_to_daily, price_to_daily, squeeze_score, summarize,
    rolling_z,
)

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT"]
ROOT = "/Users/mark/multica/quant-loop"


def load(sym):
    oi = pd.read_parquet(f"{ROOT}/data/oi/{sym}.parquet")
    fu = pd.read_parquet(f"{ROOT}/data/funding/{sym}.parquet")
    px = pd.read_parquet(f"{ROOT}/data/perp_30m/{sym}_30m.parquet")
    return oi, fu, px


def coverage_report():
    print("=== coverage ===")
    for sym in SYMS:
        oi, fu, px = load(sym)
        oi_ts = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
        fu_ts = pd.to_datetime(fu["ts"], unit="ms", utc=True)
        px_ts = pd.to_datetime(px["open_time"], unit="ms", utc=True)
        lo, hi = max(oi_ts.min(), fu_ts.min(), px_ts.min()), min(oi_ts.max(), fu_ts.max(), px_ts.max())
        print(f"{sym}: OI {oi_ts.min()} -> {oi_ts.max()} ({len(oi)} rows, "
              f"median dt={oi_ts.diff().median()})")
        print(f"{'':10s} FU {fu_ts.min()} -> {fu_ts.max()} | PX {px_ts.min()} -> {px_ts.max()}")
        print(f"{'':10s} COMMON WINDOW: {lo} -> {hi} = {(hi-lo).days} days")


def run_daily(window, label, threshold=2.0, horizons=(1, 3, 7)):
    print(f"\n=== {label}: daily grid, z-window={window}d, |score|>{threshold} ===")
    all_ev, all_base = [], []
    per_sym = {}
    for sym in SYMS:
        oi, fu, px = load(sym)
        oi_d = oi_to_daily(oi)
        fu_d = funding_to_daily(fu)
        px_d = price_to_daily(px)
        z = oi_change_z(oi_d, window)
        score = squeeze_score(z, fu_d)
        fwd = forward_returns(px_d, list(horizons))
        n_valid = score.dropna().shape[0]
        ev = event_table(score, fwd, threshold)
        base = baseline_table(score, fwd)
        per_sym[sym] = {"n_factor_days": n_valid, "n_events": len(ev)}
        ev = ev.copy(); ev["sym"] = sym
        base = base.copy(); base["sym"] = sym
        all_ev.append(ev); all_base.append(base)
        print(f"{sym}: factor_days={n_valid} events={len(ev)}")
    ev_all = pd.concat(all_ev) if all_ev else pd.DataFrame()
    base_all = pd.concat(all_base) if all_base else pd.DataFrame()
    print("-- pooled events --")
    print(summarize(ev_all).to_string(float_format=lambda x: f"{x:.4f}"))
    print("-- pooled baseline (direction rule on all days) --")
    print(summarize(base_all).to_string(float_format=lambda x: f"{x:.4f}"))
    return per_sym, ev_all, base_all


def run_hourly(window_h=120, label="HOURLY5", threshold=2.0, horizons_h=(24, 72, 168)):
    print(f"\n=== {label}: hourly grid, z-window={window_h}h, |score|>{threshold} ===")
    all_ev, all_base = [], []
    for sym in SYMS:
        oi, fu, px = load(sym)
        ts = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
        oi_h = pd.Series(oi["open_interest_value"].to_numpy(float), index=ts).sort_index()
        oi_chg = oi_h.pct_change()
        z = rolling_z(oi_chg, window_h)
        futs = pd.to_datetime(fu["ts"], unit="ms", utc=True)
        fu_s = pd.Series(fu["fundingRate"].to_numpy(float), index=futs).sort_index()
        fu_h = fu_s.reindex(z.index, method="ffill", limit=8)  # last 8h rate known
        score = z * np.sign(fu_h)
        pts = pd.to_datetime(px["open_time"], unit="ms", utc=True)
        px_h = pd.Series(px["close"].to_numpy(float), index=pts).sort_index().reindex(
            z.index, method="ffill")
        fwd = pd.DataFrame({h: px_h.shift(-h) / px_h - 1.0 for h in horizons_h})
        ev = event_table(score, fwd, threshold)
        base = baseline_table(score, fwd)
        ev = ev.copy(); ev["sym"] = sym
        base = base.copy(); base["sym"] = sym
        all_ev.append(ev); all_base.append(base)
        print(f"{sym}: factor_hours={score.dropna().shape[0]} events={len(ev)}")
    ev_all = pd.concat(all_ev)
    base_all = pd.concat(all_base)
    print("-- pooled events (OVERLAPPING windows, t optimistic) --")
    print(summarize(ev_all).to_string(float_format=lambda x: f"{x:.4f}"))
    print("-- pooled baseline --")
    print(summarize(base_all).to_string(float_format=lambda x: f"{x:.4f}"))
    print("-- per-symbol event counts & 24h mean/t --")
    for sym, g in ev_all.groupby("sym"):
        s = summarize(g)
        r = s.loc["ret_24"] if "ret_24" in s.index else None
        if r is not None:
            print(f"  {sym}: n={int(r['n'])} mean24={r['mean']:.4%} t={r['t']:.2f} win={r['win']:.1%}")
    return ev_all, base_all


if __name__ == "__main__":
    coverage_report()
    run_daily(20, "SPEC")
    run_daily(5, "LOOSE5")
    run_hourly()
