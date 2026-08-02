#!/usr/bin/env python3
"""Download Binance SPOT klines (monthly zips) for all perp symbols.

Source: https://data.binance.vision/data/spot/monthly/klines/{SYM}/{INT}/{SYM}-{INT}-{YYYY-MM}.zip
Output: data/spot/{SYMBOL}_{INTERVAL}.parquet  (columns match perp_1m schema)
"""
import io
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT"]
INTERVALS = ["1h", "1m"]
START = "2021-11"
END = "2026-07"

OUT_DIR = Path("/Users/mark/multica/quant-loop/data/spot")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://data.binance.vision/data/spot/monthly/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"]


def months_between(start: str, end: str):
    y0, m0 = int(start[:4]), int(start[5:])
    y1, m1 = int(end[:4]), int(end[5:])
    cur = (y0, m0)
    while cur <= (y1, m1):
        yield f"{cur[0]:04d}-{cur[1]:02d}"
        cur = (cur[0] + 1, 1) if cur[1] == 12 else (cur[0], cur[1] + 1)


def download_symbol_interval(sym: str, interval: str) -> pd.DataFrame:
    frames = []
    for ym in months_between(START, END):
        url = f"{BASE}/{sym}/{interval}/{sym}-{interval}-{ym}.zip"
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                continue  # month not available (future or pre-listing)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                name = zf.namelist()[0]
                with zf.open(name) as f:
                    df = pd.read_csv(f, header=None, names=COLS, low_memory=False)
            frames.append(df)
        except Exception as e:
            print(f"  [WARN] {sym} {interval} {ym}: {e}", file=sys.stderr, flush=True)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # Binance ms/us epoch quirk: early files use microseconds
    out["open_time"] = pd.to_numeric(out["open_time"], errors="coerce")
    if out["open_time"].max() > 1e16:
        out.loc[out["open_time"] > 1e16, "open_time"] //= 1000
    out["close_time"] = pd.to_numeric(out["close_time"], errors="coerce")
    if out["close_time"].max() > 1e16:
        out.loc[out["close_time"] > 1e16, "close_time"] //= 1000
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.drop_duplicates(subset=["open_time"]).sort_values("open_time")
    return out.reset_index(drop=True)


def main():
    t0 = time.time()
    for interval in INTERVALS:
        for sym in SYMBOLS:
            out_path = OUT_DIR / f"{sym}_{interval}.parquet"
            if out_path.exists():
                print(f"[skip] {sym} {interval} exists", flush=True)
                continue
            print(f"[fetch] {sym} {interval} ...", flush=True)
            df = download_symbol_interval(sym, interval)
            if df.empty:
                print(f"  [WARN] no data for {sym} {interval}", flush=True)
                continue
            df.to_parquet(out_path, index=False)
            print(f"  [done] {sym} {interval}: {len(df):,} rows "
                  f"({df['open_time'].min()} → {df['open_time'].max()})", flush=True)
    print(f"ALL DONE in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
