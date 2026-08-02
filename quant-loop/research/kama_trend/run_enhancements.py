"""KAMA strategy enhancement backtests.

Baseline (validated, CPCV PASS):
  KAMA(er=5, fast=2, slow=30) slope over 10 bars > 0 -> long (+1), else flat (0).
  BTCUSDT 4h, 7 bp round-trip cost.  Mean OOS Sharpe = 1.12, DSR = 1.10.

Enhancements explored:
  1. Short-side      — slope < 0 -> short (-1) instead of flat.
  2. KAMA +/- z bands — mean-reversion entry (dip-buy) within KAMA uptrend.
  3. Regime filter    — only trade when ATR/price > its rolling median.

All use the same data, cost model, and no-lookahead convention as kama_core.py.
Outputs: results_enhancements.json  +  enhancements_report.md
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")
sys.path.insert(0, "/Users/mark/multica/quant-loop/research/kama_trend")

import json

import numpy as np
import pandas as pd

from kama_core import FEE_PER_SIDE, kama, load_ohlc, strategy_returns, tstat

OUT = "/Users/mark/multica/quant-loop/research/kama_trend"
BARS_PER_YEAR = 2190  # 4h bars per year (6/day * 365)

# Validated baseline parameters
ER, FAST, SLOW, LB = 5, 2, 30, 10


# ───────────────────────── metrics ─────────────────────────────────────────


def sharpe_annual(r: pd.Series, bpy: int = BARS_PER_YEAR) -> float:
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return np.nan
    return float(r.mean() / r.std() * np.sqrt(bpy))


def max_drawdown(r: pd.Series) -> float:
    cum = (1 + r).cumprod()
    peak = cum.cummax()
    return float(((cum - peak) / peak).min())


def n_round_trips(signal: pd.Series) -> int:
    pos = signal.shift(1).fillna(0)
    return int((pos.diff().abs() > 0).sum() / 2)


def yearly_breakdown(r: pd.Series) -> dict:
    """Per-year t-stat and per-bar mean (bps)."""
    t = {}
    m = {}
    for yr, rr in r.groupby(r.index.year):
        t[int(yr)] = round(tstat(rr), 2)
        m[int(yr)] = round(float(rr.mean()) * 1e4, 1)
    r_recent = r[r.index >= "2024-01-01"]
    t["2024-2026"] = round(tstat(r_recent), 2)
    m["2024-2026"] = round(float(r_recent.mean()) * 1e4, 1)
    return {"t": t, "mean_bps": m}


def metrics(signal: pd.Series, close: pd.Series, label: str) -> dict:
    r = strategy_returns(close, signal)
    return {
        "label": label,
        "sharpe": round(sharpe_annual(r), 2),
        "t_full": round(tstat(r), 2),
        "mean_bps": round(float(r.mean()) * 1e4, 2),
        "max_dd": round(max_drawdown(r), 3),
        "n_rt": n_round_trips(signal),
        "n_bars": len(r),
        "yearly": yearly_breakdown(r),
    }


# ───────────────────────── strategies ──────────────────────────────────────


def baseline_long(close: pd.Series) -> pd.Series:
    """slope > 0 -> long, else flat."""
    k = kama(close, ER, FAST, SLOW)
    slope = k - k.shift(LB)
    sig = (slope > 0).astype(float)
    sig[slope.isna()] = 0.0
    return sig


def short_side(close: pd.Series, buffer_pct: float = 0.0) -> pd.Series:
    """slope > buffer -> long, slope < -buffer -> short, else hold previous.

    buffer_pct is a percentage of price (e.g. 0.001 = 0.1%). A non-zero
    buffer creates a hysteresis dead-zone around zero slope to reduce
    whipsaw at trend transitions. buffer_pct=0 is the plain short-side.
    """
    k = kama(close, ER, FAST, SLOW)
    slope = k - k.shift(LB)
    slope_pct = slope / close  # normalise to percentage of price
    sv = slope_pct.values
    n = len(sv)
    pos = np.zeros(n)
    cur = 0.0
    for i in range(n):
        if np.isnan(sv[i]):
            cur = 0.0
            pos[i] = 0.0
            continue
        if sv[i] > buffer_pct:
            cur = 1.0
        elif sv[i] < -buffer_pct:
            cur = -1.0
        # else: hold previous position (cur unchanged)
        pos[i] = cur
    sig = pd.Series(pos, index=close.index)
    sig[slope.isna()] = 0.0
    return sig


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    pc = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - pc).abs(), (low - pc).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window).mean()


def kama_z_bands(
    close: pd.Series, z: float, std_window: int = 20, allow_short: bool = False
) -> pd.Series:
    """Mean-reversion entry within KAMA uptrend.

    Long entry : close < KAMA - z*std(residual)  AND  KAMA slope > 0.
    Long exit  : close >= KAMA  OR  close > KAMA + z*std(residual).
    If allow_short: symmetric short in downtrend (close > upper & slope<0).

    std is computed on residuals (close - KAMA), which measures deviation
    from the adaptive centre rather than raw price volatility.
    """
    k = kama(close, ER, FAST, SLOW)
    slope = k - k.shift(LB)
    resid = close - k
    std = resid.rolling(std_window).std()
    lower = (k - z * std).values
    upper = (k + z * std).values
    kv = k.values
    cv = close.values
    sv = slope.values
    n = len(cv)
    pos = np.zeros(n)
    in_long = False
    in_short = False
    for i in range(n):
        if np.isnan(sv[i]):
            in_long = in_short = False
            continue
        c = cv[i]
        uptrend = sv[i] > 0
        downtrend = sv[i] < 0
        lo_ok = not np.isnan(lower[i])
        up_ok = not np.isnan(upper[i])

        # --- long side ---
        if uptrend:
            if not in_long:
                if lo_ok and c < lower[i]:
                    in_long = True
            else:
                if c >= kv[i] or (up_ok and c > upper[i]):
                    in_long = False
        else:
            in_long = False

        # --- optional short side ---
        if allow_short and downtrend:
            if not in_short:
                if up_ok and c > upper[i]:
                    in_short = True
            else:
                if c <= kv[i] or (lo_ok and c < lower[i]):
                    in_short = False
        else:
            in_short = False

        pos[i] = 1.0 if in_long else (-1.0 if in_short else 0.0)

    return pd.Series(pos, index=close.index)


def regime_filtered(
    close: pd.Series,
    df: pd.DataFrame,
    atr_window: int = 14,
    median_window: int = 50,
    direction: str = "high",
) -> pd.Series:
    """KAMA long-signal only in trending (high-vol) regime.

    Regime proxy: ATR(price)/price vs its rolling median.
    direction='high'  -> take signals only when vol_ratio > median.
    direction='low'   -> take signals only when vol_ratio <= median.
    """
    k = kama(close, ER, FAST, SLOW)
    slope = k - k.shift(LB)
    vol_ratio = _atr(df, atr_window) / close
    vol_median = vol_ratio.rolling(median_window).median()
    if direction == "high":
        regime = vol_ratio > vol_median
    else:
        regime = vol_ratio <= vol_median
    sig = ((slope > 0) & regime).astype(float)
    sig[slope.isna()] = 0.0
    sig[regime.isna()] = 0.0
    return sig


def combined_z_regime(
    close: pd.Series,
    df: pd.DataFrame,
    z: float,
    std_window: int = 20,
    atr_window: int = 14,
    median_window: int = 50,
) -> pd.Series:
    """KAMA ±z mean-reversion entry, only in high-vol regime."""
    k = kama(close, ER, FAST, SLOW)
    slope = k - k.shift(LB)
    resid = close - k
    std = resid.rolling(std_window).std()
    vol_ratio = _atr(df, atr_window) / close
    vol_median = vol_ratio.rolling(median_window).median()
    high_vol = (vol_ratio > vol_median).values

    lower = (k - z * std).values
    upper = (k + z * std).values
    kv = k.values
    cv = close.values
    sv = slope.values
    n = len(cv)
    pos = np.zeros(n)
    in_long = False
    for i in range(n):
        if np.isnan(sv[i]) or not high_vol[i]:
            in_long = False
            continue
        c = cv[i]
        uptrend = sv[i] > 0
        lo_ok = not np.isnan(lower[i])
        up_ok = not np.isnan(upper[i])
        if uptrend:
            if not in_long:
                if lo_ok and c < lower[i]:
                    in_long = True
            else:
                if c >= kv[i] or (up_ok and c > upper[i]):
                    in_long = False
        else:
            in_long = False
        pos[i] = 1.0 if in_long else 0.0
    return pd.Series(pos, index=close.index)


# ───────────────────────── main ────────────────────────────────────────────


def main():
    df = load_ohlc("BTC", "4h")
    close = df["close"]
    results = {}

    # ── baseline ──
    sig = baseline_long(close)
    results["baseline_long"] = metrics(sig, close, "Baseline long-only (er5,f2,s30,lb10)")

    # ── Enhancement 1: short-side ──
    for buf in [0.0, 0.001, 0.003, 0.005]:
        sig = short_side(close, buffer_pct=buf)
        lbl = f"Short-side (buf={buf*100:.1f}% of price)"
        results[f"short_buf{buf}"] = metrics(sig, close, lbl)

    # ── Enhancement 2: KAMA ±z bands (long-only dip buy) ──
    for z in [1.0, 1.5, 2.0, 2.5]:
        sig = kama_z_bands(close, z, std_window=20, allow_short=False)
        results[f"zband_{z}"] = metrics(sig, close, f"KAMA±z bands z={z} (long dip-buy)")
    # symmetric (long+short) at best-looking z candidates
    for z in [1.5, 2.0]:
        sig = kama_z_bands(close, z, std_window=20, allow_short=True)
        results[f"zband_{z}_ls"] = metrics(sig, close, f"KAMA±z bands z={z} (long+short)")

    # std-window sensitivity at z=1.5
    for sw in [10, 30, 50]:
        sig = kama_z_bands(close, 1.5, std_window=sw)
        results[f"zband_1.5_sw{sw}"] = metrics(sig, close, f"KAMA±z z=1.5 std_win={sw}")

    # ── Enhancement 3: regime filter ──
    for mw in [25, 50, 100]:
        for direction in ["high", "low"]:
            sig = regime_filtered(close, df, median_window=mw, direction=direction)
            results[f"regime_{direction}_mw{mw}"] = metrics(
                sig, close, f"Regime {direction}-vol, median_win={mw}"
            )

    # ── Combined: KAMA±z in high-vol regime ──
    for z in [1.5, 2.0]:
        sig = combined_z_regime(close, df, z=z)
        results[f"combined_z{z}_regime"] = metrics(
            sig, close, f"Combined: KAMA±z={z} dip-buy in high-vol regime"
        )

    # ── buy & hold reference ──
    sig_bh = pd.Series(1.0, index=close.index)
    results["buy_hold"] = metrics(sig_bh, close, "Buy & Hold BTC")

    # ── write JSON ──
    with open(f"{OUT}/results_enhancements.json", "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"Wrote results_enhancements.json ({len(results)} variants)")
    for key, m in results.items():
        print(
            f"  {m['label']:55s}  Sharpe={m['sharpe']:6.2f}  "
            f"t={m['t_full']:6.2f}  24-26 t={m['yearly']['t']['2024-2026']:6.2f}  "
            f"nRT={m['n_rt']:5d}  MDD={m['max_dd']:.3f}"
        )


if __name__ == "__main__":
    main()
