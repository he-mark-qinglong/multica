"""OFI zero-fee retest: does the killed T01 signal survive at 0bp?

Background
----------
T01 (Cont-Kukanov-Stoikov OFI on real aggTrades) was killed at SMA-35037:
the gross signal was real (corr +0.21, quintile spread +3.41bp/trade)
but taker round-trip costs (10.83bp futures, 17.83bp spot) dwarfed the
edge. The fee research report identifies **Lighter** as a 0bp path
(maker 0bp / taker 0bp, no thresholds) with artificial latency
(maker 200ms, taker 300ms).

This script answers: at 0bp fee, does the OFI edge survive? And does
the 300ms taker latency eat it?

Outputs
-------
- ``research/fee_paths/ofi_zero_fee_results.json`` — full sweep results
- ``research/fee_paths/ofi_zero_fee_bars.parquet`` — 1m bars (Apr–Jul)
- stdout summary table

References
----------
- ``research/ofi/02_ofi_signal.py`` — original OFI signal + backtest
- ``research/ofi/05_net_backtest.py`` — net-of-cost holding-period sweep
- ``research/fee_paths/REPORT.md`` — fee research (Lighter path)
- ``research/OPEN_QUESTIONS.md`` — T01 kill conditions + revival criteria
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

_QL = Path(__file__).resolve().parents[2]
_QL_STR = str(_QL)
if _QL_STR not in sys.path:
    sys.path.insert(0, _QL_STR)

from _shared.execution.lighter_adapter import LighterAdapter, LighterConfig

OUT = Path(__file__).resolve().parent
DATA_TRADES = _QL / "data" / "trades" / "BTCUSDT_aggtrades.parquet"
EXISTING_BARS = _QL / "research" / "ofi" / "btc_1m_3mo.parquet"

# Fee levels to sweep (round-trip bps)
FEE_GRID = [0.0, 0.5, 1.0, 2.0, 5.0, 10.83]

# Annualization for 1m bars (24/7 markets)
PERIODS_PER_YEAR = 365 * 24 * 60  # 525_600


# ============================================================
# 1. Build 1m bars from aggTrades
# ============================================================

def build_1m_bars_month(trades_path: Path) -> pd.DataFrame:
    """Build 1m OHLCV+OFI bars from one month of aggTrades.

    Columns: buy_vol, sell_vol, vwap, close, n_trades, ofi
    """
    df = pd.read_parquet(trades_path, columns=["ts", "price", "qty", "is_buyer_maker"])
    df["minute"] = df["ts"].dt.floor("1min")

    # Signed volume: taker buy (is_buyer_maker=False) is aggressive buy
    df["buy_qty"] = np.where(~df["is_buyer_maker"], df["qty"], 0.0)
    df["sell_qty"] = np.where(df["is_buyer_maker"], df["qty"], 0.0)
    df["notional"] = df["price"] * df["qty"]

    g = df.groupby("minute", sort=True)
    bars = pd.DataFrame({
        "buy_vol": g["buy_qty"].sum(),
        "sell_vol": g["sell_qty"].sum(),
        "close": g["price"].last(),
        "vwap": (g["notional"].sum() / g["qty"].sum()),
        "n_trades": g["qty"].count(),
    })
    bars.index.name = "ts"
    bars["ofi"] = bars["buy_vol"] - bars["sell_vol"]
    return bars


def load_1m_bars() -> pd.DataFrame:
    """Build ALL 1m bars from raw aggTrades with REAL close prices.

    The prior T01 research (SMA-35037) used pre-built bars from
    ``research/ofi/btc_1m_3mo.parquet`` which has vwap but NOT close.
    Returns were computed as vwap.pct_change(), which creates a
    mechanical correlation with OFI (both driven by within-bar volume
    imbalance). This function rebuilds with the actual last-trade price
    as ``close`` to eliminate that artifact.
    """
    months = ["4", "5", "6", "7"]
    all_bars = []
    for m in months:
        path = DATA_TRADES / "year=2026" / f"month={m}"
        if path.exists():
            all_bars.append(build_1m_bars_month(path))

    bars = pd.concat(all_bars)
    bars = bars[~bars.index.duplicated(keep="last")]
    bars = bars[bars["n_trades"] >= 10].copy()

    # Returns: close-to-close (the correct return for signal validation)
    bars["ret"] = bars["close"].pct_change()
    # Also keep vwap return for the artifact diagnostic
    bars["vwap_ret"] = bars["vwap"].pct_change()
    bars = bars.dropna(subset=["ret"])
    return bars


# ============================================================
# 2. OFI signal
# ============================================================

def z_ofi(bars: pd.DataFrame, lookback: int) -> pd.Series:
    """Cont-Kukanov-Stoikov OFI: rolling z-score of signed flow."""
    ofi = bars["ofi"]
    mu = ofi.rolling(lookback, min_periods=lookback // 2).mean()
    sd = ofi.rolling(lookback, min_periods=lookback // 2).std()
    return (ofi - mu) / sd.replace(0, np.nan)


def position_signal(z: pd.Series, thr: float) -> pd.Series:
    """Long if z > thr, short if z < -thr, else flat. Shifted by 1 bar."""
    pos = pd.Series(0, index=z.index, dtype=np.float64)
    pos[z > thr] = 1.0
    pos[z < -thr] = -1.0
    return pos.shift(1).fillna(0.0)


# ============================================================
# 3. Backtest engine
# ============================================================

def backtest(
    bars: pd.DataFrame,
    lookback: int,
    thr: float,
    hold_bars: int,
    fee_bps_rt: float,
    latency_bps_per_entry: float = 0.0,
) -> Dict:
    """Run OFI backtest with configurable fee and latency.

    Parameters
    ----------
    fee_bps_rt:
        Round-trip fee cost in basis points (entry + exit combined).
    latency_bps_per_entry:
        Average adverse-selection cost per entry in bps (from the Lighter
        latency model). Applied once per trade entry.
    """
    z = z_ofi(bars, lookback)
    ret = bars["ret"]

    # Simple position: signal drives next-bar position
    raw_pos = position_signal(z, thr)

    if hold_bars <= 1:
        # One-bar trades: each signal bar → one entry+exit
        pos = raw_pos.copy()
        # Entries: bars where pos is nonzero and it's a new position
        entries = (pos != 0) & ((pos.shift(1) == 0) | (pos != pos.shift(1)))
        n_entries = int(entries.sum())
        gross = pos * ret
        entries_mask = entries.fillna(False)
    else:
        # Hold for H bars after first signal (overlap rule)
        pos = pd.Series(0.0, index=z.index)
        held_until = -1
        entries_arr = np.zeros(len(z), dtype=bool)
        for i in range(len(z)):
            if i > held_until and i > 0:
                sig = raw_pos.iloc[i]
                if sig != 0:
                    pos.iloc[i] = sig
                    held_until = i + hold_bars - 1
                    entries_arr[i] = True
        n_entries = int(entries_arr.sum())
        gross = pos * ret
        entries_mask = pd.Series(entries_arr, index=z.index)

    # Cost: fee + latency per entry (round trip)
    total_cost_bps = fee_bps_rt + latency_bps_per_entry
    cost_frac = total_cost_bps / 1e4
    entries_series = entries_mask.astype(float)
    cost = entries_series * cost_frac
    net = gross - cost

    return {
        "net": net,
        "gross": gross,
        "n_entries": n_entries,
        "fee_bps_rt": fee_bps_rt,
        "latency_bps": latency_bps_per_entry,
        "total_cost_bps": total_cost_bps,
    }


# ============================================================
# 4. Metrics
# ============================================================

def sharpe(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    r = returns.dropna()
    if len(r) < 10 or r.std(ddof=1) < 1e-15:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year))


def ann_return(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    r = returns.dropna()
    if len(r) < 1:
        return 0.0
    return float(r.mean() * periods_per_year)


def mean_edge_per_trade(result: Dict) -> float:
    """Average gross edge per entry in bps."""
    n = result["n_entries"]
    if n < 1:
        return 0.0
    gross_sum = result["gross"].abs().sum()
    return float(gross_sum / n * 1e4)


def hit_rate(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 1:
        return float("nan")
    return float((r > 0).sum() / len(r))


# ============================================================
# 5. Parameter optimization (on IS half)
# ============================================================

def find_best_params(
    bars: pd.DataFrame,
    lookbacks: Tuple[int, ...] = (30, 60, 120, 240),
    thrs: Tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0),
    holds: Tuple[int, ...] = (1, 3, 5, 15),
    fee_bps_rt: float = 0.0,
) -> Tuple[int, float, int, pd.DataFrame]:
    """Grid search on IS (first half) at zero fee. Returns best (L, thr, H, grid_df)."""
    n = len(bars)
    cut = n // 2
    is_bars = bars.iloc[:cut]

    rows = []
    for L in lookbacks:
        for thr in thrs:
            for H in holds:
                r = backtest(is_bars, L, thr, H, fee_bps_rt)
                s = sharpe(r["net"])
                rows.append({
                    "lookback": L, "thr": thr, "hold": H,
                    "is_sharpe": s,
                    "is_ann": ann_return(r["net"]),
                    "n_entries": r["n_entries"],
                })
    grid = pd.DataFrame(rows)
    best = grid.sort_values("is_sharpe", ascending=False).iloc[0]
    return int(best["lookback"]), float(best["thr"]), int(best["hold"]), grid


# ============================================================
# 6. Latency analysis from trade tape
# ============================================================

def estimate_latency_cost(
    bars: pd.DataFrame,
    signal_bars: pd.DatetimeIndex,
    side: str = "taker",
    latency_ms: float = 300.0,
) -> float:
    """Estimate average adverse-selection cost (bps) of latency per entry.

    For each signal bar timestamp, finds the last trade price at/before
    the bar close and the first trade price after bar_close + latency.
    Returns the mean absolute slippage (adverse selection) in bps.

    Processes the tape month-by-month to fit in memory.
    """
    adapter = LighterAdapter(LighterConfig(
        taker_latency_ms=latency_ms,
        maker_latency_ms=max(latency_ms - 100, 0),
    ))

    # Group signals by month for tape lookup
    sig_df = pd.DataFrame({"ts": signal_bars})
    sig_df["month"] = sig_df["ts"].dt.to_period("M")
    sig_by_month = sig_df.groupby("month")

    all_slips = []
    for period, group in sig_by_month:
        year = period.year
        month = period.month
        tape_path = DATA_TRADES / f"year={year}" / f"month={month}"
        if not tape_path.exists():
            continue

        # Load tape (only ts, price columns for memory)
        tape = pd.read_parquet(tape_path, columns=["ts", "price"])
        # Ensure sorted
        tape = tape.sort_values("ts").reset_index(drop=True)

        signals = pd.DatetimeIndex(group["ts"].values)
        slips = adapter.batch_latency_slippage(signals, tape, side=side)
        all_slips.append(slips)

    if not all_slips:
        return 0.0

    combined = pd.concat(all_slips)
    # Adverse selection: absolute value (the signal direction means we're
    # buying when price is about to go up, so the delay hurts us)
    # For a long entry: latency cost = fill_price/ref_price - 1 (if price went up, we pay more)
    # For a short entry: latency cost = -(fill_price/ref_price - 1) (if price went up, we lose)
    # We take the directional cost: positive = unfavorable
    return float(combined.abs().mean())  # mean absolute slippage


# ============================================================
# 7. Break-even fee calculation
# ============================================================

def break_even_fee(
    bars: pd.DataFrame,
    lookback: int,
    thr: float,
    hold_bars: int,
) -> float:
    """The round-trip fee (bps) at which annualized Sharpe crosses 0.

    Uses the gross mean return per bar and trade frequency to solve for
    the fee that makes mean(net) = 0, then converts to Sharpe = 0
    (since std is unaffected by a flat fee drag).
    """
    r = backtest(bars, lookback, thr, hold_bars, fee_bps_rt=0.0)
    net = r["net"].dropna()
    n_entries = r["n_entries"]
    n_bars = len(net)

    if n_entries < 1:
        return 0.0

    # Mean net return per bar at 0 fee
    mean_per_bar = float(net.mean())

    if mean_per_bar <= 0:
        # Already negative at 0 fee — no positive break-even
        return 0.0

    # Each entry costs fee_bps_rt / 1e4 in fractional return.
    # Total drag per bar = (n_entries / n_bars) * (fee_bps_rt / 1e4)
    # Break-even: mean_per_bar = (n_entries / n_bars) * (fee_bps_rt / 1e4)
    entries_per_bar = n_entries / n_bars
    break_even = mean_per_bar * 1e4 / entries_per_bar
    return break_even


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()
    print("=" * 70)
    print("OFI ZERO-FEE RETEST (T01 revival at 0bp)")
    print("=" * 70)

    # --- Load data ---
    print("\n[1] Loading 1m bars from raw aggTrades (real close prices)...")
    bars = load_1m_bars()
    n = len(bars)
    cut = n // 2  # IS/OOS split
    print(f"  {n:,} bars: {bars.index.min()} → {bars.index.max()}")
    print(f"  IS (first half):  {cut:,} bars")
    print(f"  OOS (second half): {n - cut:,} bars")

    # --- Signal validation: close vs vwap return ---
    print("\n[1b] OFI signal validation (close vs vwap artifact check)...")
    z = z_ofi(bars, 60)
    z_clean = z.dropna()
    next_close = bars["ret"].shift(-1).reindex(z_clean.index)
    next_vwap = bars["vwap_ret"].shift(-1).reindex(z_clean.index)
    corr_close = z_clean.corr(next_close)
    corr_vwap = z_clean.corr(next_vwap)
    print(f"  OFI vs NEXT-bar return correlation:")
    print(f"    close-to-close: {corr_close:.4f}  ← the real signal")
    print(f"    vwap-to-vwap:   {corr_vwap:.4f}  ← prior research used this (artifact)")
    print(f"  Same-bar (contemporaneous):")
    print(f"    close: {z_clean.corr(bars['ret'].reindex(z_clean.index)):.4f}")
    print(f"    vwap:  {z_clean.corr(bars['vwap_ret'].reindex(z_clean.index)):.4f}")

    # --- Parameter optimization at 0bp ---
    print("\n[2] Grid search on IS at 0bp fee...")
    t1 = time.time()
    best_L, best_thr, best_H, grid = find_best_params(bars)
    print(f"  Best IS params (0bp): L={best_L}, thr={best_thr}, hold={best_H}")
    best_row = grid[(grid.lookback == best_L) & (grid.thr == best_thr) & (grid.hold == best_H)].iloc[0]
    print(f"  IS Sharpe={best_row['is_sharpe']:.2f}, IS Ann={best_row['is_ann']:.4f}, "
          f"IS entries={int(best_row['n_entries']):,}")
    print(f"  ({time.time()-t1:.1f}s)")

    # Show top 10 IS cells
    print("\n  Top 10 IS cells (0bp):")
    top10 = grid.sort_values("is_sharpe", ascending=False).head(10)
    pd.set_option("display.float_format", lambda x: f"{x:.2f}")
    print(top10.to_string(index=False))

    # --- Fee sweep on OOS with best params ---
    print(f"\n[3] Fee sweep on OOS with L={best_L}, thr={best_thr}, hold={best_H}")
    oos_bars = bars.iloc[cut:]

    fee_results = []
    for fee_bps in FEE_GRID:
        r = backtest(oos_bars, best_L, best_thr, best_H, fee_bps_rt=fee_bps)
        s = sharpe(r["net"])
        a = ann_return(r["net"])
        hr = hit_rate(r["net"])
        edge = mean_edge_per_trade(r)
        fee_results.append({
            "fee_bps_rt": fee_bps,
            "oos_sharpe": round(s, 3),
            "oos_ann_return": round(a, 4),
            "oos_hit_rate": round(hr, 3),
            "n_entries": r["n_entries"],
            "gross_edge_bps": round(edge, 2),
            "net_edge_bps": round(edge - fee_bps, 2),
        })

    fee_df = pd.DataFrame(fee_results)
    print("\n  Fee sweep (OOS):")
    print(fee_df.to_string(index=False))

    # --- Break-even fee ---
    print(f"\n[4] Break-even fee (full dataset)...")
    be_fee = break_even_fee(bars, best_L, best_thr, best_H)
    print(f"  Break-even RT fee: {be_fee:.2f} bp")

    # Also compute break-even per OOS only
    be_fee_oos = break_even_fee(oos_bars, best_L, best_thr, best_H)
    print(f"  Break-even RT fee (OOS only): {be_fee_oos:.2f} bp")

    # --- Latency analysis ---
    print(f"\n[5] Lighter latency analysis (300ms taker)...")
    t2 = time.time()
    # Get entry signal timestamps from the full backtest
    z = z_ofi(bars, best_L)
    pos_sig = position_signal(z, best_thr)
    # Entries: where position goes from 0 to nonzero or flips
    entries_mask = (pos_sig != 0) & ((pos_sig.shift(1) == 0) | (pos_sig != pos_sig.shift(1)))
    entry_ts = bars.index[entries_mask.fillna(False)]
    print(f"  {len(entry_ts):,} entry signals to analyze")

    # Estimate per-entry latency cost from the tape
    latency_cost_bps = estimate_latency_cost(
        bars, entry_ts, side="taker", latency_ms=300.0,
    )
    print(f"  Mean |latency slippage| per entry: {latency_cost_bps:.2f} bp")
    print(f"  ({time.time()-t2:.1f}s)")

    # Also test 200ms maker
    latency_cost_maker = estimate_latency_cost(
        bars, entry_ts, side="maker", latency_ms=200.0,
    )
    print(f"  Mean |latency slippage| (200ms maker): {latency_cost_maker:.2f} bp")

    # --- Lighter scenario: 0bp fee + latency ---
    print(f"\n[6] Lighter scenario: 0bp fee + 300ms taker latency (OOS)...")
    r_lighter = backtest(oos_bars, best_L, best_thr, best_H,
                         fee_bps_rt=0.0, latency_bps_per_entry=latency_cost_bps)
    s_lighter = sharpe(r_lighter["net"])
    a_lighter = ann_return(r_lighter["net"])
    print(f"  Sharpe: {s_lighter:.3f}")
    print(f"  Ann return: {a_lighter:.4f}")

    # Lighter maker scenario: 0bp fee + 200ms maker latency
    r_lighter_maker = backtest(oos_bars, best_L, best_thr, best_H,
                               fee_bps_rt=0.0, latency_bps_per_entry=latency_cost_maker)
    s_maker = sharpe(r_lighter_maker["net"])
    print(f"  Sharpe (maker 200ms): {s_maker:.3f}")

    # --- CPCV robustness ---
    print(f"\n[7] CPCV robustness (best params, 0bp)...")
    n_groups = 6
    group_size = n // n_groups
    fold_sharpes = []
    for i in range(n_groups):
        for j in range(i + 1, n_groups):
            test_start = i * group_size
            test_end = (j + 1) * group_size
            test_b = bars.iloc[test_start:test_end]
            if len(test_b) < 200:
                continue
            # Embargo: drop first/last 60 bars
            test_b = test_b.iloc[60:-60]
            r = backtest(test_b, best_L, best_thr, best_H, fee_bps_rt=0.0)
            fold_sharpes.append(sharpe(r["net"]))

    fold_arr = np.array(fold_sharpes)
    print(f"  CPCV folds: {len(fold_arr)}")
    print(f"  Mean: {fold_arr.mean():.3f}  Std: {fold_arr.std(ddof=1):.3f}")
    print(f"  Min: {fold_arr.min():.3f}  Max: {fold_arr.max():.3f}")
    print(f"  % positive: {100*(fold_arr > 0).mean():.0f}%")

    # --- Save bars ---
    bars_out = OUT / "ofi_zero_fee_bars.parquet"
    bars.to_parquet(bars_out)
    print(f"\n  Saved bars → {bars_out}")

    # --- Summary ---
    summary = {
        "data": {
            "n_bars": int(n),
            "range_start": str(bars.index.min()),
            "range_end": str(bars.index.max()),
            "is_bars": int(cut),
            "oos_bars": int(n - cut),
        },
        "best_params": {
            "lookback": best_L,
            "thr": best_thr,
            "hold": best_H,
            "is_sharpe": float(best_row["is_sharpe"]),
            "is_ann": float(best_row["is_ann"]),
            "n_entries_is": int(best_row["n_entries"]),
        },
        "fee_sweep_oos": fee_results,
        "break_even_fee_bps": {
            "full": round(be_fee, 2),
            "oos": round(be_fee_oos, 2),
        },
        "latency_analysis": {
            "taker_300ms_bps": round(latency_cost_bps, 3),
            "maker_200ms_bps": round(latency_cost_maker, 3),
            "lighter_taker_sharpe": round(s_lighter, 3),
            "lighter_taker_ann": round(a_lighter, 4),
            "lighter_maker_sharpe": round(s_maker, 3),
        },
        "cpcv_0bp": {
            "n_folds": len(fold_arr),
            "mean": round(float(fold_arr.mean()), 3),
            "std": round(float(fold_arr.std(ddof=1)), 3),
            "min": round(float(fold_arr.min()), 3),
            "max": round(float(fold_arr.max()), 3),
            "pct_positive": round(float((fold_arr > 0).mean()), 3),
        },
        "top10_is_cells": top10.to_dict("records"),
        "elapsed_sec": round(time.time() - t0, 1),
    }

    results_path = OUT / "ofi_zero_fee_results.json"
    with open(results_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n  Saved results → {results_path}")

    print(f"\n{'=' * 70}")
    print(f"DONE in {time.time()-t0:.1f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
