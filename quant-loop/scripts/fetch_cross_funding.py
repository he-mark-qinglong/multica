#!/usr/bin/env python3
"""Fetch perpetual funding rate history from Binance USDT-M, Bybit linear, Hyperliquid.

Output: /Users/mark/multica/quant-loop/data/funding_cross/{binance,bybit,hyperliquid}/{SYMBOL}.parquet
Schema: ts (UTC datetime64), funding_rate (float), venue (str), symbol (str)

Notes:
- Binance/Bybit funding settles every 8h; Hyperliquid settles every 1h (raw rate kept as-is).
- Proxy env is set in-script before any request (user requirement).
"""
import os

os.environ.setdefault("https_proxy", "http://127.0.0.1:7890")
os.environ.setdefault("http_proxy", "http://127.0.0.1:7890")
os.environ.setdefault("all_proxy", "socks5://127.0.0.1:7890")

import time
import sys
import requests
import pandas as pd

OUT_BASE = "/Users/mark/multica/quant-loop/data/funding_cross"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT"]
HL_COINS = {s: s.replace("USDT", "") for s in SYMS}

BINANCE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BYBIT_URL = "https://api.bybit.com/v5/market/funding/history"
HL_URL = "https://api.hyperliquid.xyz/info"

NOW_MS = int(time.time() * 1000)
DAY_MS = 86400_000

FAILURES = []  # (venue, symbol, reason)


def get(url, params=None, json_body=None, tries=6, timeout=30):
    for i in range(tries):
        try:
            if json_body is not None:
                r = requests.post(url, json=json_body, timeout=timeout)
            else:
                r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            wait = 2 ** i
            if "429" in str(e):
                wait = 10 * (2 ** i)  # rate limit: back off harder
            print(f"    retry {i+1}/{tries} after error: {e} (sleep {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"request failed after {tries} tries: {url} {params or json_body}")


def save_df(rows, venue, symbol):
    """rows: list of (ts_ms, funding_rate)"""
    df = pd.DataFrame(rows, columns=["ts", "funding_rate"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["funding_rate"] = df["funding_rate"].astype(float)
    df = df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    df["venue"] = venue
    df["symbol"] = symbol
    df = df[["ts", "funding_rate", "venue", "symbol"]]
    outdir = os.path.join(OUT_BASE, venue)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{symbol}.parquet")
    df.to_parquet(path, index=False)
    return path, df


def fetch_binance(sym):
    rows = []
    start = int(pd.Timestamp("2019-01-01", tz="UTC").timestamp() * 1000)
    while start < NOW_MS:
        data = get(BINANCE_URL, params={
            "symbol": sym, "startTime": start, "limit": 1000,
        })
        if not data:
            break
        rows.extend((int(d["fundingTime"]), float(d["fundingRate"])) for d in data)
        last = int(data[-1]["fundingTime"])
        if len(data) < 1000 or last < start:
            break
        start = last + 1
        time.sleep(0.3)
    return rows


def fetch_bybit(sym):
    rows = []
    end = NOW_MS
    while True:
        data = get(BYBIT_URL, params={
            "category": "linear", "symbol": sym,
            "startTime": 0, "endTime": end, "limit": 200,
        })
        lst = (data.get("result") or {}).get("list") or []
        if not lst:
            break
        rows.extend((int(d["fundingRateTimestamp"]), float(d["fundingRate"])) for d in lst)
        min_ts = min(int(d["fundingRateTimestamp"]) for d in lst)
        if len(lst) < 200:
            break
        end = min_ts - 1
        time.sleep(0.3)
    return rows


def fetch_hyperliquid(coin):
    rows = []
    start = int(pd.Timestamp("2022-01-01", tz="UTC").timestamp() * 1000)
    while start < NOW_MS:
        data = get(HL_URL, json_body={
            "type": "fundingHistory", "coin": coin,
            "startTime": start, "endTime": NOW_MS,
        })
        if not data:
            break
        rows.extend((int(d["time"]), float(d["fundingRate"])) for d in data)
        last = max(int(d["time"]) for d in data)
        # HL caps a response at 500 entries; keep paging while the page is full.
        if len(data) < 500 or last < start:
            break
        start = last + 1
        time.sleep(0.6)
    return rows


def main():
    args = sys.argv[1:]
    venues = {"binance", "bybit", "hyperliquid"}
    only = {a for a in args if a in venues}
    sym_filter = {a for a in args if a not in venues}  # e.g. AVAX
    results = {}  # venue -> {symbol: df}

    jobs = [("binance", fetch_binance, {s: s for s in SYMS}),
            ("bybit", fetch_bybit, {s: s for s in SYMS}),
            ("hyperliquid", fetch_hyperliquid, {c: c for c in HL_COINS.values()})]
    if only:
        jobs = [j for j in jobs if j[0] in only]
    if sym_filter:
        jobs = [(v, f, {s: a for s, a in m.items() if s in sym_filter or a in sym_filter})
                for v, f, m in jobs]

    for venue, fn, mapping in jobs:
        results[venue] = {}
        for sym, api_sym in mapping.items():
            print(f"[{venue}] {sym} ...", flush=True)
            try:
                rows = fn(api_sym)
                if not rows:
                    FAILURES.append((venue, sym, "empty response / no data"))
                    print(f"  WARNING: no rows for {venue} {sym}", flush=True)
                    continue
                path, df = save_df(rows, venue, sym)
                results[venue][sym] = df
                print(f"  -> {len(df)} rows  {df['ts'].min()} .. {df['ts'].max()}  ({path})", flush=True)
            except Exception as e:
                FAILURES.append((venue, sym, str(e)))
                print(f"  FAILED: {venue} {sym}: {e}", flush=True)

    print("\n===== COVERAGE SUMMARY =====")
    header = f"{'venue':<12}{'symbol':<10}{'rows':>8}  {'first':<20}{'last':<20}"
    print(header)
    for venue in results:
        for sym, df in sorted(results[venue].items()):
            print(f"{venue:<12}{sym:<10}{len(df):>8}  {str(df['ts'].min()):<20}{str(df['ts'].max()):<20}")

    print("\n===== BTC CROSS-VENUE SANITY CHECK =====")
    try:
        def load(venue, sym):
            return pd.read_parquet(os.path.join(OUT_BASE, venue, f"{sym}.parquet")) \
                     .set_index("ts")["funding_rate"]
        b = load("binance", "BTCUSDT")
        y = load("bybit", "BTCUSDT")
        h = load("hyperliquid", "BTC")
        common = b.index.intersection(y.index)
        t = common[-1]
        # HL timestamps carry ms jitter; take the nearest hourly sample.
        i = h.index.get_indexer([t], method="nearest")[0]
        t_hl = h.index[i]
        print(f"common funding timestamp (binance/bybit): {t}")
        print(f"  binance     : {b.loc[t]:+.6f}")
        print(f"  bybit       : {y.loc[t]:+.6f}")
        print(f"  hyperliquid : {h.iloc[i]:+.6f}  @ {t_hl}  (hourly rate; ~1/8 of 8h rate is expected)")
    except Exception as e:
        print(f"sanity check failed: {e}")

    print("\n===== FAILURES =====")
    if FAILURES:
        for f in FAILURES:
            print(f"  {f[0]} {f[1]}: {f[2]}")
    else:
        print("  none")

    print("\n===== FILE LIST =====")
    for venue in ["binance", "bybit", "hyperliquid"]:
        d = os.path.join(OUT_BASE, venue)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                print(f"  {os.path.join(d, f)}")


if __name__ == "__main__":
    main()
