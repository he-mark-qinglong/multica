"""First BTCUSDT 90d backtest run for SMA-34992 / LOID-V4.

Loads BTCUSDT aggTrades from <data_root>/trades/, runs
the detector, builds 1m composite, runs the backtest, computes 9-key metrics,
writes results/sma-34992/loid_iceberg_v4_btc_90d_metrics.json + summary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from _shared.validation.compute_metrics import compute_metrics  # noqa: E402
from _shared.validators.metrics_validator import safe_validate  # noqa: E402
from strategies.loid_iceberg_v4_1m_20260720.iceberg_detector import (  # noqa: E402
    DetectorConfig,
    detect as detect_iceberg,
)
from strategies.loid_iceberg_v4_1m_20260720.backtest import (  # noqa: E402
    BacktestConfig,
    run as run_backtest,
)

OUT_DIR = REPO / "results" / "sma-34992"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_btc_aggtrades() -> pd.DataFrame:
    """Load BTCUSDT aggTrades parquet hive (90d recent window only)."""
    p = REPO / "data" / "trades" / "BTCUSDT_aggtrades.parquet"
    print(f"[load] reading {p} (hive) ...")
    table = ds.dataset(str(p), format="parquet", partitioning="hive").to_table()
    df = table.to_pandas()
    print(f"[load] rows={len(df):,} columns={list(df.columns)}")
    return df


def build_1m_composite(trades: pd.DataFrame, cfg: DetectorConfig) -> pd.DataFrame:
    """Run detector + resample to 1m composite on a fixed 1m UTC grid."""
    print(f"[detect] n_trades={len(trades):,} lookback={cfg.lookback} ...")
    out = detect_iceberg(trades, cfg)
    print(f"[detect] stats: {out['stats']}")
    composite_min = out["composite_by_minute"]
    # Rename to remove detector-internal names; caller expects 'composite'
    composite_min = composite_min.rename(columns={"minute_ts_utc": "ts"})
    composite_min = composite_min.set_index("ts")
    return composite_min


def load_btc_1m_ohlcv(trades: pd.DataFrame) -> pd.DataFrame:
    """Build 1m OHLCV from aggTrades (we don't need the separate perp_1m parquet)."""
    print("[ohlcv] building 1m OHLCV from aggTrades ...")
    df = trades.copy()
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    ohlcv = (
        df.resample("1min")
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("qty", "sum"),
        )
        .dropna(subset=["close"])
    )
    print(f"[ohlcv] n_bars={len(ohlcv):,} range={ohlcv.index[0]} → {ohlcv.index[-1]}")
    return ohlcv


def main():
    cfg_det = DetectorConfig(lookback=1000, large_z=3.0, whale_z=5.0)
    cfg_bt = BacktestConfig(
        threshold=5.0,
        max_hold_minutes=240,
        notional_usd=100_000.0,
        adv_usd=5_000_000_000.0,
        impact_factor=0.05,
    )

    trades = load_btc_aggtrades()
    ohlcv = load_btc_1m_ohlcv(trades)
    composite = build_1m_composite(trades, cfg_det)

    print(f"[backtest] running with threshold={cfg_bt.threshold} max_hold={cfg_bt.max_hold_minutes}min ...")
    bt_out = run_backtest(ohlcv, composite, cfg_bt)
    trades_log = bt_out["trades"]
    equity = bt_out["equity"]

    print(f"[backtest] trades={len(trades_log)}")
    if trades_log:
        long_n = sum(1 for t in trades_log if t["direction"] == "long")
        short_n = sum(1 for t in trades_log if t["direction"] == "short")
        reasons = {}
        for t in trades_log:
            reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
        total_pnl = sum(t["pnl_usd"] for t in trades_log)
        avg_pnl = total_pnl / len(trades_log) if trades_log else 0.0
        print(f"[backtest] long={long_n} short={short_n} total_pnl=${total_pnl:.2f} avg/trade=${avg_pnl:.2f}")
        print(f"[backtest] exit_reasons={reasons}")

    # 9-key metrics via shared helper. freq_per_year = 365*24*60 = 525600 for 1m bars.
    metrics = compute_metrics(equity, n_trades=len(trades_log), freq_per_year=365 * 24 * 60)
    print(f"[metrics] {metrics}")

    ok, msg = safe_validate(metrics, "loid_iceberg_v4_btc_90d")
    print(f"[validate] ok={ok} msg={msg}")

    out_json = OUT_DIR / "loid_iceberg_v4_btc_90d_metrics.json"
    with open(out_json, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[write] {out_json}")

    # Trade-level log
    trades_path = OUT_DIR / "loid_iceberg_v4_btc_90d_trades.json"
    with open(trades_path, "w") as f:
        json.dump(trades_log, f, indent=2, default=str)
    print(f"[write] {trades_path} ({len(trades_log)} trades)")

    return metrics, trades_log, ok, msg


if __name__ == "__main__":
    main()