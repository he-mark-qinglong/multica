"""Save composite from loid_iceberg_v4 detector for reuse.

Runs the detector month-by-month and saves the per-minute composite to parquet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from strategies.loid_iceberg_v4_1m_20260720.iceberg_detector import (  # noqa: E402
    DetectorConfig,
    detect as detect_iceberg,
)

OUT_DIR = REPO / "results" / "sma-34992"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REQUIRED_COLS = ["ts", "price", "qty", "is_buyer_maker", "first_id", "last_id"]


def load_month(year: int, month: int) -> pd.DataFrame:
    path = REPO / "data" / "trades" / "BTCUSDT_aggtrades.parquet" / f"year={year}" / f"month={month}"
    table = ds.dataset(str(path), format="parquet", partitioning="hive").to_table(columns=REQUIRED_COLS)
    df = table.to_pandas()
    if pd.api.types.is_datetime64_any_dtype(df["ts"]):
        df["ts"] = df["ts"].astype("int64")
    return df


def main() -> None:
    cfg = DetectorConfig(lookback=1000, large_z=3.0, whale_z=5.0)
    all_composites = []

    for year, month in [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]:
        print(f"[detect] {year}-{month:02d} ...")
        trades = load_month(year, month)
        out = detect_iceberg(trades, cfg)
        composite = out["composite_by_minute"].rename(columns={"minute_ts_utc": "ts"}).set_index("ts")
        all_composites.append(composite)
        print(f"[detect] stats={out['stats']}")
        del trades, out, composite

    composite = pd.concat(all_composites).sort_index()
    composite = composite[~composite.index.duplicated(keep="first")]
    if composite.index.tz is None:
        composite.index = composite.index.tz_localize("UTC")

    out_path = OUT_DIR / "loid_iceberg_v4_composite_lb1000_z3.0_w5.0.parquet"
    composite.to_parquet(out_path)
    print(f"[write] {out_path} ({len(composite)} bars)")


if __name__ == "__main__":
    main()
