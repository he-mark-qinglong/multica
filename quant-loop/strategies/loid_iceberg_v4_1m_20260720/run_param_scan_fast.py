"""Efficient parameter scan for loid_iceberg_v4 (SMA-34992).

Key optimization: run detector once per (lookback, large_z, whale_z), then
resample composite and run backtest for each (composite_rule, threshold).
Reduces detector runs from 81 to 9.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from _shared.validation.compute_metrics import compute_metrics  # noqa: E402
from strategies.loid_iceberg_v4_1m_20260720.iceberg_detector import (  # noqa: E402
    DetectorConfig,
    detect as detect_iceberg,
)
from strategies.loid_iceberg_v4_1m_20260720.backtest import (  # noqa: E402
    BacktestConfig,
    run as run_backtest,
)

OUT_DIR = REPO / "results" / "sma-34992" / "param_scan"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REQUIRED_COLS = ["ts", "price", "qty", "is_buyer_maker", "first_id", "last_id"]


def load_all_trades() -> pd.DataFrame:
    """Load all months of BTCUSDT aggTrades."""
    all_trades = []
    for year, month in [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]:
        path = REPO / "data" / "trades" / "BTCUSDT_aggtrades.parquet" / f"year={year}" / f"month={month}"
        print(f"[load] {year}-{month:02d} ...")
        table = ds.dataset(str(path), format="parquet", partitioning="hive").to_table(columns=REQUIRED_COLS)
        df = table.to_pandas()
        if pd.api.types.is_datetime64_any_dtype(df["ts"]):
            df["ts"] = df["ts"].astype("int64")
        all_trades.append(df)
        print(f"[load] rows={len(df):,}")
    trades = pd.concat(all_trades, ignore_index=True)
    print(f"[load] total={len(trades):,}")
    return trades


def build_1m_ohlcv(trades: pd.DataFrame) -> pd.DataFrame:
    df = trades.copy()
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return (
        df.resample("1min")
        .agg(open=("price", "first"), high=("price", "max"), low=("price", "min"), close=("price", "last"), volume=("qty", "sum"))
        .dropna(subset=["close"])
    )


def resample_composite(composite: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "1min":
        return composite
    return composite.resample(rule).agg({
        "composite": "sum",
        "n_large": "sum",
        "n_whale": "sum",
        "n_iceberg": "sum",
    })


def run_backtest_one(ohlcv: pd.DataFrame, composite: pd.DataFrame, threshold: float) -> dict:
    cfg = BacktestConfig(threshold=threshold, max_hold_minutes=240, notional_usd=100_000.0, adv_usd=5_000_000_000.0, impact_factor=0.05)
    bt = run_backtest(ohlcv, composite, cfg)
    metrics = compute_metrics(bt["equity"], n_trades=len(bt["trades"]), freq_per_year=365 * 24 * 60)
    return {
        "n_trades": len(bt["trades"]),
        "sharpe": metrics["sharpe_daily"],
        "ann_return": metrics["annualized_return"],
        "max_dd": metrics["max_drawdown_pct"],
        "pf": metrics["profit_factor"],
    }


def main() -> None:
    # Pre-registered grid
    lookbacks = [500, 1000, 2000]
    large_zs = [2.0, 3.0, 4.0]
    whale_zs = [5.0]
    composite_rules = ["1min", "5min", "15min"]
    thresholds = [3.0, 5.0, 8.0]

    # Load data once
    trades = load_all_trades()
    ohlcv = build_1m_ohlcv(trades)
    print(f"[ohlcv] bars={len(ohlcv):,}")

    results = []

    # Step 1: run detector once per unique config
    det_configs = list(itertools.product(lookbacks, large_zs, whale_zs))
    det_outputs = {}
    for lookback, large_z, whale_z in det_configs:
        key = (lookback, large_z, whale_z)
        print(f"[detect] lookback={lookback} large_z={large_z} whale_z={whale_z} ...")
        cfg = DetectorConfig(lookback=lookback, large_z=large_z, whale_z=whale_z)
        out = detect_iceberg(trades, cfg)
        composite = out["composite_by_minute"].rename(columns={"minute_ts_utc": "ts"}).set_index("ts")
        det_outputs[key] = {
            "composite": composite,
            "stats": out["stats"],
        }
        print(f"[detect] stats={out['stats']}")

    # Step 2: for each detector output, resample and backtest
    total = len(det_configs) * len(composite_rules) * len(thresholds)
    i = 0
    for (lookback, large_z, whale_z), det in det_outputs.items():
        for rule, threshold in itertools.product(composite_rules, thresholds):
            i += 1
            label = f"lb{lookback}_z{large_z}_w{whale_z}_{rule}_th{threshold}"
            print(f"[{i}/{total}] {label}")
            try:
                composite = resample_composite(det["composite"], rule)
                r = run_backtest_one(ohlcv, composite, threshold)
                r.update({
                    "label": label,
                    "lookback": lookback,
                    "large_z": large_z,
                    "whale_z": whale_z,
                    "composite_rule": rule,
                    "threshold": threshold,
                    "n_large": det["stats"]["n_large"],
                    "n_whale": det["stats"]["n_whale"],
                    "n_iceberg": det["stats"]["cluster_count"],
                })
                results.append(r)
                print(f"  -> trades={r['n_trades']} sharpe={r['sharpe']:.3f} pf={r['pf']:.3f}")
            except Exception as e:
                print(f"  -> ERROR: {e}")
                results.append({"label": label, "error": str(e)})

    out_path = OUT_DIR / "param_scan_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[write] {out_path}")

    # Summary
    df = pd.DataFrame([r for r in results if "error" not in r])
    if len(df):
        df = df.sort_values("sharpe", ascending=False)
        print("\nTop 10 by Sharpe:")
        print(df.head(10)[["label", "n_trades", "sharpe", "ann_return", "max_dd", "pf"]].to_string(index=False))


if __name__ == "__main__":
    main()
