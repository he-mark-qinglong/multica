"""Fast threshold scan using cached composite.

Reuses the saved detector composite to test multiple thresholds and composite rules.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from _shared.validation.compute_metrics import compute_metrics  # noqa: E402
from strategies.loid_iceberg_v4_1m_20260720.backtest import (  # noqa: E402
    BacktestConfig,
    run as run_backtest,
)

OUT_DIR = REPO / "results" / "sma-34992" / "param_scan"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REQUIRED_COLS = ["ts", "price", "qty", "is_buyer_maker", "first_id", "last_id"]


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


def run_one(ohlcv: pd.DataFrame, composite: pd.DataFrame, threshold: float) -> dict:
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
    # Load composite
    composite_path = REPO / "results" / "sma-34992" / "loid_iceberg_v4_composite_lb1000_z3.0_w5.0.parquet"
    print(f"[load] composite from {composite_path}")
    composite = pd.read_parquet(composite_path)
    print(f"[load] composite bars={len(composite):,}")

    # Build OHLCV
    all_ohlcv = []
    for year, month in [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]:
        trades = load_month(year, month)
        all_ohlcv.append(build_1m_ohlcv(trades))
        print(f"[ohlcv] {year}-{month:02d} bars={len(all_ohlcv[-1]):,}")
        del trades
    ohlcv = pd.concat(all_ohlcv).sort_index()
    ohlcv = ohlcv[~ohlcv.index.duplicated(keep="first")]
    if ohlcv.index.tz is None:
        ohlcv.index = ohlcv.index.tz_localize("UTC")
    print(f"[total] ohlcv bars={len(ohlcv):,}")

    # Scan thresholds and composite rules
    rules = ["1min", "5min", "15min"]
    thresholds = [2.0, 3.0, 5.0, 8.0, 13.0]

    results = []
    for rule, threshold in itertools.product(rules, thresholds):
        label = f"lb1000_z3.0_w5.0_{rule}_th{threshold}"
        print(f"[scan] {label}")
        try:
            comp = resample_composite(composite, rule)
            r = run_one(ohlcv, comp, threshold)
            r.update({
                "label": label,
                "lookback": 1000,
                "large_z": 3.0,
                "whale_z": 5.0,
                "composite_rule": rule,
                "threshold": threshold,
            })
            results.append(r)
            print(f"  -> trades={r['n_trades']} sharpe={r['sharpe']:.3f} pf={r['pf']:.3f}")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append({"label": label, "error": str(e)})

    out_path = OUT_DIR / "threshold_scan_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[write] {out_path}")

    df = pd.DataFrame([r for r in results if "error" not in r])
    if len(df):
        df = df.sort_values("sharpe", ascending=False)
        print("\nTop 10 by Sharpe:")
        print(df.head(10)[["label", "n_trades", "sharpe", "ann_return", "max_dd", "pf"]].to_string(index=False))


if __name__ == "__main__":
    main()
