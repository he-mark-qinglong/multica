#!/usr/bin/env python3
"""VPVR + KAMA(VWMA) + z-score deviation strategy prototype (optimized).

Indicators (VWMA, KAMA, POC, z) are parameter-independent of the swept
knobs (z_in / max_hold / sl_bp) → compute once per symbol, sweep cheap.
"""
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/mark/multica/quant-loop")
from _shared.indicators.vpvr import compute_vpvr_levels

ROOT = "/Users/mark/multica/quant-loop"


def vwma(close, volume, window):
    pv = close * volume
    return pv.rolling(window).sum() / volume.rolling(window).sum()


def kama(series, er_window=10, fast=2, slow=30):
    n = len(series)
    out = np.full(n, np.nan)
    vals = series.values.astype(float)
    # find first valid index (input may have leading NaN, e.g. VWMA warm-up)
    valid = np.isfinite(vals)
    if valid.sum() <= er_window:
        return pd.Series(out, index=series.index)
    first = int(np.argmax(valid))  # first non-NaN
    seed = first + er_window       # need er_window history for ER
    if seed >= n:
        return pd.Series(out, index=series.index)
    change = np.abs(vals[er_window:] - vals[:-er_window])
    volatility = pd.Series(np.abs(np.diff(vals))).rolling(er_window).sum().values
    er = np.zeros(n)
    er[er_window:] = change / np.maximum(volatility[er_window - 1:], 1e-12)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    sc[~np.isfinite(sc)] = slow_sc ** 2
    out[seed] = np.nanmean(vals[first:seed + 1])
    for i in range(seed + 1, n):
        prev = out[i - 1]
        if not np.isfinite(prev):
            out[i] = vals[i]
            continue
        out[i] = prev + sc[i] * (vals[i] - prev) if np.isfinite(vals[i]) else prev
    return pd.Series(out, index=series.index)


def rolling_poc(high, low, volume, window, num_bins=60, recompute_every=24):
    n = len(high)
    poc = np.full(n, np.nan)
    last_poc = np.nan
    for i in range(window, n):
        if (i - window) % recompute_every == 0 or np.isnan(last_poc):
            h = high.iloc[i - window:i]
            l = low.iloc[i - window:i]
            v = volume.iloc[i - window:i]
            if v.sum() <= 0:
                continue
            try:
                vp = compute_vpvr_levels(h, l, v, num_bins=num_bins)
                last_poc = vp.poc_price
            except Exception:
                pass
        poc[i] = last_poc
    return pd.Series(poc, index=high.index)


def prepare(df, vwma_window=20, kama_er=10, kama_fast=2, kama_slow=30,
            z_window=100, vpvr_window=480, vpvr_bins=60, vpvr_every=24):
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    v = vwma(close, volume, vwma_window)
    kv = kama(v, kama_er, kama_fast, kama_slow)
    resid = close - kv
    sd = resid.rolling(z_window).std()
    z = (resid / sd.replace(0, np.nan)).values
    poc = rolling_poc(high, low, volume, vpvr_window, vpvr_bins, vpvr_every).values
    sd_v = sd.values
    c = close.values
    start = max(vwma_window, vpvr_window, z_window) + kama_er + 1
    return c, z, poc, sd_v, start


def backtest(c, z, poc, sd_v, start, z_in, z_out, tp_bp, sl_bp, max_hold, fee_bp):
    n = len(c)
    trades = []
    pos = None
    i = start
    while i < n - 1:
        zi, pc, px = z[i], poc[i], c[i]
        if pos is not None:
            d, ep, ei = pos
            pnl_bp = ((px - ep) / ep * 10000) if d == "long" else ((ep - px) / ep * 10000)
            held = i - ei
            exit_now = False
            if d == "long" and (zi >= -z_out or pnl_bp >= tp_bp): exit_now = True
            if d == "short" and (zi <= z_out or pnl_bp >= tp_bp): exit_now = True
            if pnl_bp <= -sl_bp: exit_now = True
            if held >= max_hold: exit_now = True
            if d == "long" and not np.isnan(pc) and px >= pc and ep < pc: exit_now = True
            if d == "short" and not np.isnan(pc) and px <= pc and ep > pc: exit_now = True
            if exit_now:
                trades.append(pnl_bp - fee_bp)
                pos = None
        if pos is None and not np.isnan(zi) and not np.isnan(pc) and sd_v[i] > 0:
            if zi <= -z_in and px < pc:
                pos = ("long", px, i)
            elif zi >= z_in and px > pc:
                pos = ("short", px, i)
        i += 1
    if pos is not None:
        d, ep, ei = pos
        px = c[-1]
        pnl_bp = ((px - ep) / ep * 10000) if d == "long" else ((ep - px) / ep * 10000)
        trades.append(pnl_bp - fee_bp)
    return trades


def stats(arr):
    if len(arr) < 30:
        return None
    avg = arr.mean()
    win = (arr > 0).mean()
    g = arr[arr > 0].sum(); l = abs(arr[arr < 0].sum())
    pf = g / l if l > 0 else 999
    cum = np.cumsum(arr); mdd = (cum - np.maximum.accumulate(cum)).min()
    return avg, win, pf, mdd


if __name__ == "__main__":
    FEE = 7
    symbols = {
        "BTCUSDT": f"{ROOT}/data/perp_15m/BTCUSDT_15m.parquet",
        "ETHUSDT": f"{ROOT}/data/perp_15m/ETHUSDT_15m.parquet",
        "SOLUSDT": f"{ROOT}/data/perp_15m/SOLUSDT_15m.parquet",
    }
    print("=" * 90)
    print("VPVR + KAMA(VWMA) + z-deviation — 15m perp, fee=7bp RT")
    print("=" * 90)

    for sym, path in symbols.items():
        df = pd.read_parquet(path)
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        print(f"\n### {sym} ({len(df):,} bars, {df['ts'].min().date()} → {df['ts'].max().date()})")
        t0 = time.time()
        c, z, poc, sd_v, start = prepare(df)
        print(f"  indicators ready ({time.time()-t0:.0f}s)")

        best = None
        for z_in in [1.5, 2.0, 2.5]:
            for max_hold in [24, 48, 96]:
                for sl_bp in [150, 300]:
                    trades = backtest(c, z, poc, sd_v, start, z_in, 0.5, 10**9, sl_bp, max_hold, FEE)
                    arr = np.array(trades)
                    s = stats(arr)
                    if s is None or s[0] <= 0:
                        continue
                    avg, win, pf, mdd = s
                    print(f"  z={z_in} hold={max_hold} sl={sl_bp}: n={len(arr):,} avg={avg:+.2f}bp win={win:.1%} PF={pf:.2f} maxDD={mdd:.0f}bp")
                    if best is None or avg > best[0]:
                        best = (avg, f"z={z_in} hold={max_hold} sl={sl_bp}", len(arr), pf, mdd)
        if best:
            print(f"  >>> BEST: {best[1]} avg={best[0]:+.2f}bp n={best[2]} PF={best[3]:.2f} maxDD={best[4]:.0f}bp")
        else:
            print(f"  >>> no positive combination")
        print(f"  ({time.time()-t0:.0f}s total)")
