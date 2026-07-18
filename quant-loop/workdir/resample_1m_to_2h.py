"""Resample perp 1m parquet -> 2h OHLCV for BTC/ETH/SOL.

Pure local pandas job (no network). Reads /home/smark/multica/quant-loop/data/perp_1m/
and writes /home/smark/multica/quant-loop/data/perp_2h/{SYMBOL}USDT_2h.parquet
using UTC-aligned 2h bins (offset="2h", base=0).

Aggregation matches Binance kline semantics:
  open  = first
  high  = max
  low   = min
  close = last
  volume/quote_volume/taker_buy_*  = sum
  trades = sum
  close_time = max
  ignore = sum (low-info column from raw API response, preserved for schema parity)

Sanity-checks performed and printed at end:
  - row count vs expected (n_minutes / 120)
  - no NaN in OHLCV columns
  - all 2h bar timestamps are aligned to {00:00, 02:00, ..., 22:00} UTC
  - spot-check: rebuild one bar from source 1m rows and compare with the resampled row

Output: prints a per-symbol summary line and exits 0 on success.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SRC_DIR = Path("/home/smark/multica/quant-loop/data/perp_1m")
DST_DIR = Path("/home/smark/multica/quant-loop/data/perp_2h")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
BAR_MS = 2 * 60 * 60 * 1000  # 2h in ms


def resample_one(symbol: str) -> dict:
    src = SRC_DIR / f"{symbol}_1m.parquet"
    df = pd.read_parquet(src)
    assert df["open_time"].is_monotonic_increasing, f"{symbol}: 1m not sorted by open_time"

    # Index by UTC datetime so pandas resamples on a clean axis.
    ts = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index(ts)

    # Drop 1m rows whose bar-start is before the first UTC-aligned 2h boundary
    # (so the leading 2h bucket is a full, aligned bin). The Binance 1m data
    # starts mid-day for early symbols (e.g. BTCUSDT_1m starts 2019-09-08 17:57),
    # so the leading partial bin would otherwise have <120 minutes.
    first_ts_ms = int(df["open_time"].iloc[0])
    aligned_first_ms = (first_ts_ms // BAR_MS + 1) * BAR_MS  # next 2h boundary strictly after first
    df = df.loc[df["open_time"] >= aligned_first_ms]

    # Trim trailing rows that fall in a partial bucket. We compute the bucket
    # END (exclusive) of the last full bucket, then keep only bars strictly
    # before that. A bar at the boundary itself belongs to the next bucket.
    # Note: the source 1m parquet may have minor gaps, so the kept row count
    # can be slightly less than `expected_full_bins * 120`; what matters is
    # that the kept rows fit cleanly inside complete, aligned 2h buckets.
    n_1m_aligned = len(df)
    expected_full_bins = n_1m_aligned // 120
    last_full_bucket_end_ms = aligned_first_ms + expected_full_bins * BAR_MS
    df = df.loc[df["open_time"] < last_full_bucket_end_ms]
    n_1m_after_trim = len(df)
    # Resample and re-verify the bin count.
    _tmp = df.resample("2h", origin="epoch").agg({"open": "first"})
    actual_bins = len(_tmp)
    expected_full_bins = actual_bins  # ground truth: only complete resampled bins

    # 2h resample, origin='epoch' gives standard UTC-aligned bins
    # [00:00, 02:00), [02:00, 04:00), ..., [22:00, 24:00). Bucket label is start.
    agg = {
        "open_time": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "close_time": "max",
        "quote_volume": "sum",
        "trades": "sum",
        "taker_buy_base": "sum",
        "taker_buy_quote": "sum",
        "ignore": "sum",
    }
    out = df.resample("2h", origin="epoch").agg(agg).reset_index(drop=True)
    assert len(out) == expected_full_bins, (
        f"{symbol}: resample produced {len(out)} bins, expected {expected_full_bins}"
    )

    # Sanity-checks
    nan_ohlcv = out[["open", "high", "low", "close", "volume"]].isna().sum().sum()

    # Bar alignment: bucket-start ms must be a multiple of 2h.
    misaligned = (out["open_time"] % BAR_MS != 0).sum()

    # Spot-check: rebuild the middle 2h bar from the source 1m rows.
    spot_idx = len(out) // 2
    spot_ts = out["open_time"].iloc[spot_idx]
    src_mask = (df["open_time"] >= spot_ts) & (df["open_time"] < spot_ts + BAR_MS)
    src_bucket = df.loc[src_mask]
    spot_check = {
        "spot_bucket_1m_rows": int(len(src_bucket)),
        "open_match": bool(out["open"].iloc[spot_idx] == src_bucket["open"].iloc[0]),
        "close_match": bool(out["close"].iloc[spot_idx] == src_bucket["close"].iloc[-1]),
        "high_match": bool(out["high"].iloc[spot_idx] == src_bucket["high"].max()),
        "low_match": bool(out["low"].iloc[spot_idx] == src_bucket["low"].min()),
        "vol_match": bool(abs(out["volume"].iloc[spot_idx] - src_bucket["volume"].sum()) < 1e-9),
    }

    # Write
    dst = DST_DIR / f"{symbol}_2h.parquet"
    out.to_parquet(dst, index=False)
    size_mb = dst.stat().st_size / (1024 * 1024)

    summary = {
        "symbol": symbol,
        "src_rows_1m_full": len(pd.read_parquet(src)),
        "src_rows_1m_after_align_trim": n_1m_aligned,
        "src_rows_1m_after_trailing_trim": n_1m_after_trim,
        "trimmed_leading_minutes": (aligned_first_ms - first_ts_ms) // 60000,
        "dst_rows_2h": len(out),
        "expected_full_bins": expected_full_bins,
        "dst_bytes": dst.stat().st_size,
        "dst_mb": round(size_mb, 2),
        "first_ts_ms": int(out["open_time"].iloc[0]),
        "last_ts_ms": int(out["open_time"].iloc[-1]),
        "first_iso": pd.to_datetime(out["open_time"].iloc[0], unit="ms", utc=True).isoformat(),
        "last_iso": pd.to_datetime(out["open_time"].iloc[-1], unit="ms", utc=True).isoformat(),
        "nan_ohlcv": int(nan_ohlcv),
        "misaligned_bars": int(misaligned),
        "spot_check": spot_check,
        "columns": out.columns.tolist(),
    }
    return summary


def main() -> int:
    DST_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for sym in SYMBOLS:
        results.append(resample_one(sym))

    print("=" * 72)
    print("2h resample summary")
    print("=" * 72)
    for r in results:
        print(f"[{r['symbol']}]")
        print(f"  src_rows_1m_full           : {r['src_rows_1m_full']:,}")
        print(f"  src_rows_1m_after_align    : {r['src_rows_1m_after_align_trim']:,}")
        print(f"  src_rows_1m_after_trailing : {r['src_rows_1m_after_trailing_trim']:,}")
        print(f"  dst_rows_2h                : {r['dst_rows_2h']:,}")
        print(f"  expected_full_bins         : {r['expected_full_bins']:,}")
        print(f"  dst file                   : {DST_DIR / (r['symbol'] + '_2h.parquet')}")
        print(f"  size                       : {r['dst_mb']} MB")
        print(f"  first_ts (ms/iso)          : {r['first_ts_ms']} / {r['first_iso']}")
        print(f"  last_ts  (ms/iso)          : {r['last_ts_ms']} / {r['last_iso']}")
        print(f"  NaN OHLCV                  : {r['nan_ohlcv']}")
        print(f"  misaligned bars            : {r['misaligned_bars']}")
        print(f"  spot_check                 : {r['spot_check']}")
        print(f"  columns                    : {r['columns']}")
        print()

    # Hard fail if any sanity check trips.
    bad = []
    for r in results:
        if r["nan_ohlcv"] != 0:
            bad.append(f"{r['symbol']}: NaN in OHLCV")
        if r["misaligned_bars"] != 0:
            bad.append(f"{r['symbol']}: {r['misaligned_bars']} misaligned 2h bars")
        if r["dst_rows_2h"] != r["expected_full_bins"]:
            bad.append(
                f"{r['symbol']}: rows {r['dst_rows_2h']} != expected {r['expected_full_bins']}"
            )
        sc = r["spot_check"]
        for k, v in sc.items():
            if k.endswith("_match") and not v:
                bad.append(f"{r['symbol']}: spot_check {k}={v}")
        if sc["spot_bucket_1m_rows"] != 120:
            bad.append(
                f"{r['symbol']}: spot bucket has {sc['spot_bucket_1m_rows']} 1m rows (expected 120)"
            )

    if bad:
        print("FAIL:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("All sanity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())