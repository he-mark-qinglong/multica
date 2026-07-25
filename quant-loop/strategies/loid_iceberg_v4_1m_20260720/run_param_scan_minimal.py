"""Minimal parameter scan for loid_iceberg_v4 (SMA-34992).

Only 4 pre-registered combinations to keep runtime tractable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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

# Pre-registered minimal grid (4 combos)
COMBOS = [
    {"lookback": 1000, "large_z": 3.0, "whale_z": 5.0, "composite_rule": "1min", "threshold": 5.0},
    {"lookback": 1000, "large_z": 3.0, "whale_z": 5.0, "composite_rule": "5min", "threshold": 5.0},
    {"lookback": 1000, "large_z": 2.0, "whale_z": 5.0, "composite_rule": "1min", "threshold": 5.0},
    {"lookback": 2000, "large_z": 3.0, "whale_z": 5.0, "composite_rule": "1min", "threshold": 5.0},
]


def load_month(year: int, month: int) -> pd.DataFrame:
    path = REPO / "data" / "trades" / "BTCUSDT_aggtrades.parquet" / f"year={year}" / f"month={month}"
    table = ds.dataset(str(path), format="parquet", partitioning="hive").to_table(columns=REQUIRED_COLS)
    df = table.to_pandas()
    if pd.api.types.is_datetime64_any_dtype(df["ts"]):
        df["ts"] = df["ts"].astype("int64")
    return df


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
    return composite.resample(rule).agg({"composite": "sum", "n_large": "sum", "n_whale": "sum", "n_iceberg": "sum"})


def run_one(trades: pd.DataFrame, ohlcv: pd.DataFrame, combo: dict) -> dict:
    cfg_det = DetectorConfig(lookback=combo["lookback"], large_z=combo["large_z"], whale_z=combo["whale_z"])
    out = detect_iceberg(trades, cfg_det)
    composite = out["composite_by_minute"].rename(columns={"minute_ts_utc": "ts"}).set_index("ts")
    composite = resample_composite(composite, combo["composite_rule"])

    cfg_bt = BacktestConfig(threshold=combo["threshold"], max_hold_minutes=240, notional_usd=100_000.0, adv_usd=5_000_000_000.0, impact_factor=0.05)
    bt = run_backtest(ohlcv, composite, cfg_bt)
    metrics = compute_metrics(bt["equity"], n_trades=len(bt["trades"]), freq_per_year=365 * 24 * 60)

    return {
        **combo,
        "n_trades": len(bt["trades"]),
        "sharpe": metrics["sharpe_daily"],
        "ann_return": metrics["annualized_return"],
        "max_dd": metrics["max_drawdown_pct"],
        "pf": metrics["profit_factor"],
        "n_large": out["stats"]["n_large"],
        "n_whale": out["stats"]["n_whale"],
        "n_iceberg": out["stats"]["cluster_count"],
    }


def main() -> None:
    # Load data once
    all_trades = []
    all_ohlcv = []
    for year, month in [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]:
        print(f"[load] {year}-{month:02d} ...")
        trades = load_month(year, month)
        all_trades.append(trades)
        all_ohlcv.append(build_1m_ohlcv(trades))
        print(f"[load] rows={len(trades):,}")
    trades = pd.concat(all_trades, ignore_index=True)
    ohlcv = pd.concat(all_ohlcv).sort_index()
    ohlcv = ohlcv[~ohlcv.index.duplicated(keep="first")]
    print(f"[total] trades={len(trades):,} ohlcv_bars={len(ohlcv):,}")

    results = []
    for i, combo in enumerate(COMBOS, 1):
        label = f"lb{combo['lookback']}_z{combo['large_z']}_w{combo['whale_z']}_{combo['composite_rule']}_th{combo['threshold']}"
        print(f"[{i}/{len(COMBOS)}] {label}")
        try:
            r = run_one(trades, ohlcv, combo)
            r["label"] = label
            results.append(r)
            print(f"  -> trades={r['n_trades']} sharpe={r['sharpe']:.3f} pf={r['pf']:.3f}")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append({"label": label, "error": str(e)})

    out_path = OUT_DIR / "param_scan_minimal_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[write] {out_path}")


if __name__ == "__main__":
    main()
