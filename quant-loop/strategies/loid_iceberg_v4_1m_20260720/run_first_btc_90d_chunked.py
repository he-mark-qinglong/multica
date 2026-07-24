"""Memory-efficient BTCUSDT 90d backtest for SMA-34992 / LOID-V4.

Processes aggTrades month-by-month to avoid OOM on the 5.5GB hive.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

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

REQUIRED_COLS = ["ts", "price", "qty", "is_buyer_maker", "first_id", "last_id"]


def load_month(year: int, month: int) -> pd.DataFrame:
    """Load one month of BTCUSDT aggTrades."""
    path = REPO / "data" / "trades" / "BTCUSDT_aggtrades.parquet" / f"year={year}" / f"month={month}"
    print(f"[load] {path} ...")
    table = ds.dataset(str(path), format="parquet", partitioning="hive").to_table(columns=REQUIRED_COLS)
    df = table.to_pandas()
    # Convert ts back to integer milliseconds (detector expects int)
    if pd.api.types.is_datetime64_any_dtype(df["ts"]):
        # datetime64[ms] -> int64 is already milliseconds
        df["ts"] = df["ts"].astype("int64")
    print(f"[load] rows={len(df):,}")
    return df


def build_1m_ohlcv_from_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Build 1m OHLCV from aggTrades."""
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
    return ohlcv


def main() -> None:
    cfg_det = DetectorConfig(lookback=1000, large_z=3.0, whale_z=5.0)
    cfg_bt = BacktestConfig(
        threshold=5.0,
        max_hold_minutes=240,
        notional_usd=100_000.0,
        adv_usd=5_000_000_000.0,
        impact_factor=0.05,
    )

    all_composites = []
    all_ohlcv = []
    total_trades = 0

    # Process recent 90d: 2026-04 .. 2026-07
    for year, month in [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]:
        trades = load_month(year, month)
        total_trades += len(trades)

        # Detector on this month
        out = detect_iceberg(trades, cfg_det)
        composite_min = out["composite_by_minute"].rename(columns={"minute_ts_utc": "ts"})
        composite_min = composite_min.set_index("ts")
        all_composites.append(composite_min)
        print(f"[detect] {year}-{month:02d}: stats={out['stats']}")

        # OHLCV for this month
        ohlcv = build_1m_ohlcv_from_trades(trades)
        all_ohlcv.append(ohlcv)
        print(f"[ohlcv] {year}-{month:02d}: n_bars={len(ohlcv):,}")

        # Free memory
        del trades, out, composite_min, ohlcv

    # Concatenate
    composite = pd.concat(all_composites).sort_index()
    ohlcv = pd.concat(all_ohlcv).sort_index()
    composite = composite[~composite.index.duplicated(keep="first")]
    ohlcv = ohlcv[~ohlcv.index.duplicated(keep="first")]

    # Ensure both indexes are UTC-aware datetime64[ns, UTC]
    if composite.index.tz is None:
        composite.index = composite.index.tz_localize("UTC")
    if ohlcv.index.tz is None:
        ohlcv.index = ohlcv.index.tz_localize("UTC")

    print(f"[total] trades={total_trades:,} composite_bars={len(composite):,} ohlcv_bars={len(ohlcv):,}")

    # Run backtest
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

    metrics = compute_metrics(equity, n_trades=len(trades_log), freq_per_year=365 * 24 * 60)
    print(f"[metrics] {metrics}")

    ok, msg = safe_validate(metrics, "loid_iceberg_v4_btc_90d")
    print(f"[validate] ok={ok} msg={msg}")

    out_json = OUT_DIR / "loid_iceberg_v4_btc_90d_metrics.json"
    with open(out_json, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[write] {out_json}")

    trades_path = OUT_DIR / "loid_iceberg_v4_btc_90d_trades.json"
    with open(trades_path, "w") as f:
        json.dump(trades_log, f, indent=2, default=str)
    print(f"[write] {trades_path} ({len(trades_log)} trades)")


if __name__ == "__main__":
    main()
