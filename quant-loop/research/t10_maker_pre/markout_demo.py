"""
T10 maker pre-SPEC — aggTrades markout data-feasibility demo.

GOAL
----
Empirically estimate, on BTC aggTrades, the two Quant-RT components that decide
whether maker execution breaks the se_h3 break-even (20bps pair-RT):

  (A) "fill probability per trade" proxy at top of book (L2 unavailable;
      we use the taker-side tape as a stand-in: each print is a fill event).
  (B) post-fill markout (a.k.a. adverse-selection cost proxy) at multiple
      horizons: 1s / 5s / 30s / 300s.

We do NOT compute cost per se (no fees in the parquet); we report the realized
mid drift conditional on a taker print. The tradable maker will post LIMIT
orders whose fills correspond to moments when the tape prints THROUGH the
posted price — we measure the post-fill drift those makers would have ridden.

NOT STRATEGY CODE — see task spec, no signal/sweep. Pure measurement.
"""
import time
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.dataset as ds

DATA = "/Users/mark/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet"
OUT  = Path("/tmp/t10_maker_pre")
OUT.mkdir(parents=True, exist_ok=True)

# pick a 3-day window: 2026-04-19 → 2026-04-22 (post-restart snapshot)
START_MS = int(pd.Timestamp("2026-04-19 00:00:00", tz="UTC").timestamp() * 1000)
END_MS   = int(pd.Timestamp("2026-04-22 00:00:00", tz="UTC").timestamp() * 1000)
TICK     = 0.01   # BTCUSDT perp tick (Binance spec)
HORIZONS_MS = {"1s": 1_000, "5s": 5_000, "30s": 30_000, "300s": 300_000}

def load_window() -> pd.DataFrame:
    t0 = time.time()
    d = ds.dataset(DATA, format="parquet", partitioning="hive")
    start_ts = pd.Timestamp(START_MS, unit="ms", tz="UTC")
    end_ts   = pd.Timestamp(END_MS,   unit="ms", tz="UTC")
    flt = (ds.field("ts") >= start_ts) & (ds.field("ts") < end_ts)
    tbl = d.to_table(filter=flt, columns=["ts", "price", "qty", "is_buyer_maker"])
    df = tbl.to_pandas()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)
    print(f"[load] rows={len(df):,} elapsed={time.time()-t0:.1f}s "
          f"range={df['ts'].min()}→{df['ts'].max()}", file=sys.stderr)
    return df

def classify_sweeps(df: pd.DataFrame) -> pd.DataFrame:
    """
    is_buyer_maker=True  → taker SOLD  → fill at bid (taker hits bid)
    is_buyer_maker=False → taker BOUGHT → fill at ask
    A 'sweep' is a sequence of >=2 same-side fills at consecutive distinct
    prices (intra-100ms window) — proxy for >1-tick walk-the-book.
    """
    df = df.copy()
    df["side"] = np.where(df["is_buyer_maker"], "bid", "ask")
    same_side = df["side"].eq(df["side"].shift(1))
    dt = (df["ts"] - df["ts"].shift(1)).dt.total_seconds() * 1000.0
    same = same_side & (dt <= 100.0)
    grp = (df["side"] != df["side"].shift(1)).cumsum()
    df["swgrp"] = grp
    grp_sizes = df.groupby("swgrp")["side"].transform("size")
    df["is_sweep"] = grp_sizes >= 2
    return df

def add_markouts(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized markouts via asof merge onto future mid."""
    df = df.sort_values("ts").reset_index(drop=True)
    # pyarrow returns ms-precision UTC; normalize to ns for merge_asof
    df["ts"] = df["ts"].dt.as_unit("ns")
    df["ref_price"] = df["price"].astype("float64")
    left = df[["ts", "side", "price", "is_sweep"]].rename(columns={"price": "fill_price"})
    for name, ms in HORIZONS_MS.items():
        future = df[["ts", "price"]].rename(columns={"price": f"fwd_{name}_price"})
        future["ts"] = future["ts"] + pd.Timedelta(ms, unit="ms")
        merged = pd.merge_asof(
            left, future,
            on="ts", direction="backward", tolerance=pd.Timedelta("2s"))
        sgn = np.where(merged["side"] == "bid", -1.0, +1.0)
        merged[f"markout_{name}"] = (
            sgn * (merged[f"fwd_{name}_price"] - merged["fill_price"]) /
            merged["fill_price"] * 10_000.0)
        df[f"markout_{name}"] = merged[f"markout_{name}"].values
    return df

def report(df: pd.DataFrame) -> dict:
    """Summary statistics of markout distribution by side × sweep."""
    rows = []
    for side in ("bid", "ask"):
        for sweep_flag in (False, True):
            sub = df[(df["side"] == side) & (df["is_sweep"] == sweep_flag)]
            row = {"side": side, "sweep": bool(sweep_flag), "n": int(len(sub))}
            for h in HORIZONS_MS:
                col = f"markout_{h}"
                v = sub[col].dropna()
                if v.empty:
                    row[f"{h}_mean_bp"] = None
                    row[f"{h}_median_bp"] = None
                    row[f"{h}_p25"] = None
                    row[f"{h}_p75"] = None
                    continue
                row[f"{h}_mean_bp"]   = float(v.mean())
                row[f"{h}_median_bp"] = float(v.median())
                row[f"{h}_p25"]       = float(v.quantile(0.25))
                row[f"{h}_p75"]       = float(v.quantile(0.75))
            rows.append(row)
    return {"rows": rows, "n_total": int(len(df)),
            "n_sweep": int(df["is_sweep"].sum()),
            "sweep_share": float(df["is_sweep"].mean())}

def main():
    df = load_window()
    df = classify_sweeps(df)
    df = add_markouts(df)
    summary = report(df)
    out_json = OUT / "markout_summary.json"
    out_json.write_text(__import__("json").dumps(summary, indent=2))
    print(f"[wrote] {out_json}", file=sys.stderr)
    print(f"[window] {df['ts'].min()} → {df['ts'].max()}  "
          f"rows={summary['n_total']:,}  sweep_share={summary['sweep_share']:.4f}",
          file=sys.stderr)
    for r in summary["rows"]:
        print("  side={side} sweep={sweep} n={n:,}  " .format(**r)
              + "  ".join(f"{h}={r[f'{h}_mean_bp']:+.2f}bp" for h in HORIZONS_MS), file=sys.stderr)

if __name__ == "__main__":
    main()
