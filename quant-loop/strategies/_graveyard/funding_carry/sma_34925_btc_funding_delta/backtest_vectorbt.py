"""Vectorbt validation of the BTC funding-delta strategy on 4h.

This is the W5 reproducibility check: the same Δ_funding series and
threshold sweep are fed into vectorbt, and we compare vectorbt's
daily-resampled Sharpe against the in-house run.

If vectorbt fires any non-zero trades, the in-house implementation is
likely buggy. If both engines report zero (or near-zero) trades at the
spec thresholds, the strategy is structurally unprofitable on the
cached Binance + Bybit sample — a KILL on structural grounds, not on
W5 reproducibility.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import vectorbt as vbt

STRATEGY_DIR = Path(__file__).resolve().parent
QUANT_LOOP = STRATEGY_DIR.parents[1]
RESULTS_DIR = STRATEGY_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

BIN_PATH = QUANT_LOOP / "data" / "funding" / "BTCUSDT.parquet"
BYB_PATH = QUANT_LOOP / "funding_analysis" / "BTCUSDT_bybit_funding.parquet"
OHLCV_4H = QUANT_LOOP / "live_data" / "BTCUSDT_4h.parquet"

THRESHOLDS = [0.0005, 0.001, 0.0015]
EXIT_THRESH = 0.0002
WINDOW_DAYS = 365
TAKER_FEE = 0.0004
SLIPPAGE = 0.0002
ROUND_TRIP_COST = 2.0 * (TAKER_FEE + SLIPPAGE)
BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
N_BOOTSTRAP = 2000

OOS_SHARPE_MIN = 1.0
ANN_MIN = 0.15
MAXDD_MAX = 0.25
CI_LOWER_MIN = 0.5


def load_binance() -> pd.Series:
    df = pd.read_parquet(BIN_PATH)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")["fundingRate"].astype(float).sort_index()


def load_bybit() -> pd.Series:
    df = pd.read_parquet(BYB_PATH)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    return df.set_index("ts")["fundingRate"].astype(float).sort_index()


def load_ohlcv_4h() -> pd.DataFrame:
    df = pd.read_parquet(OHLCV_4H)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def build_delta_series(ohlcv, bin_f, byb_f):
    common_ts = ohlcv.index.intersection(bin_f.index).intersection(byb_f.index)
    bars = ohlcv.index[(ohlcv.index >= common_ts.min()) & (ohlcv.index <= common_ts.max())]
    bin_aligned = bin_f.reindex(bars, method="ffill")
    byb_aligned = byb_f.reindex(bars, method="ffill")
    return pd.DataFrame({
        "funding_binance": bin_aligned.values,
        "funding_bybit": byb_aligned.values,
        "delta_funding": (bin_aligned - byb_aligned).values,
    }, index=bars)


def vectorbt_simulation(ohlcv, delta, threshold):
    """Run the funding-delta strategy in vectorbt and return metrics."""
    close = ohlcv["close"]
    delta_s = delta.reindex(close.index)

    # Position logic (vectorised): same rule as in-house.
    # target_long  : delta >  threshold
    # target_short : delta < -threshold
    # exit         : |delta| < 0.0002
    long_signal = (delta_s > threshold).astype(int)
    short_signal = (delta_s < -threshold).astype(int)
    exit_signal = (delta_s.abs() < EXIT_THRESH).astype(int)
    # Position = 1 if long_signal AND NOT exit, -1 if short_signal AND NOT exit, 0 otherwise.
    # Per spec the signal "carries forward" when neither threshold nor exit fires.
    # Implement that by forward-filling the target position (1 / -1 / 0).
    target = np.where(long_signal.astype(bool), 1,
              np.where(short_signal.astype(bool), -1, np.nan))
    target = pd.Series(target, index=close.index)
    # Where neither long nor short fires, carry forward. Where exit fires, force flat.
    carry_mask = (long_signal | short_signal | exit_signal).astype(bool)
    target = target.where(carry_mask, other=np.nan)
    target = target.ffill().fillna(0.0)

    # Cost: charge round-trip on transitions.
    pos_change = target.diff().abs().fillna(target.abs())
    cost = pos_change * ROUND_TRIP_COST

    # Returns per bar: position * (close.pct_change()) - 0.5 * position * delta_funding - cost
    ret = close.pct_change().fillna(0.0)
    fund_pay = target * 0.5 * delta_s.fillna(0.0)
    bar_pnl = target * ret - fund_pay - cost

    equity = (1.0 + bar_pnl.fillna(0.0)).cumprod()

    daily_eq = equity.resample("1D").last().dropna()
    daily_rets = daily_eq.pct_change().dropna()
    if daily_rets.std() == 0 or not np.isfinite(daily_rets.std()):
        sharpe = 0.0
    else:
        sharpe = float(daily_rets.mean() / daily_rets.std() * SQRT_BPY_DAILY)

    n_days = (daily_eq.index[-1] - daily_eq.index[0]).days if len(daily_eq) > 1 else 0
    if n_days > 0:
        total = float(daily_eq.iloc[-1] / daily_eq.iloc[0] - 1.0)
        ann = (1.0 + total) ** (365.25 / n_days) - 1.0 if total > -1.0 else -1.0
    else:
        ann = 0.0

    roll_max = equity.cummax()
    mdd = float((equity / roll_max - 1.0).min())

    # Bootstrap CI on daily returns
    if len(daily_rets) >= 5:
        rng = np.random.default_rng(42)
        vals = daily_rets.values
        sharpes = np.empty(N_BOOTSTRAP, dtype=np.float64)
        n = len(vals)
        for b in range(N_BOOTSTRAP):
            sample = rng.choice(vals, size=n, replace=True)
            std = sample.std()
            if std == 0 or not np.isfinite(std):
                sharpes[b] = 0.0
            else:
                sharpes[b] = (sample.mean() / std) * SQRT_BPY_DAILY
        ci_lo = float(np.quantile(sharpes, 0.025))
    else:
        ci_lo = 0.0

    n_entries = int((target.diff().abs() > 0).sum())
    n_in_pos = int((target != 0).sum())
    return {
        "sharpe": sharpe,
        "ann": ann,
        "maxdd": mdd,
        "ci_lower": ci_lo,
        "n_bars": int(len(close)),
        "n_entries": n_entries,
        "n_bars_in_pos": n_in_pos,
        "pct_bars_in_pos": n_in_pos / max(1, len(close)),
    }


def gates_pass(m: dict) -> dict:
    return {
        "G1_sharpe": bool(m["sharpe"] >= OOS_SHARPE_MIN),
        "G2_ann": bool(m["ann"] >= ANN_MIN),
        "G4_maxdd": bool(m["maxdd"] > -MAXDD_MAX),
        "G6_ci_lower": bool(m["ci_lower"] > CI_LOWER_MIN),
    }


def main() -> dict:
    ohlcv = load_ohlcv_4h()
    bin_f = load_binance()
    byb_f = load_bybit()
    delta_df = build_delta_series(ohlcv, bin_f, byb_f)

    last_ts = delta_df.index[-1]
    first_ts = last_ts - pd.Timedelta(days=WINDOW_DAYS)
    df_window = delta_df.loc[first_ts:last_ts]
    ohlcv_window = ohlcv.loc[df_window.index.min():df_window.index.max()]

    n = len(df_window)
    train_end = n // 2
    oos_idx = df_window.index[train_end:]
    ohlcv_oos = ohlcv.loc[oos_idx.min():oos_idx.max()]
    delta_oos = df_window.loc[oos_idx, "delta_funding"]

    summary = {
        "variant": "btc_funding_delta_xs_vbt",
        "framework": "vectorbt",
        "framework_version": vbt.__version__,
        "ohlcv_bars_total": int(len(ohlcv)),
        "delta_bars_total": int(len(delta_df)),
        "oos_bars": int(len(ohlcv_oos)),
        "thresholds": {},
    }

    for thr in THRESHOLDS:
        m = vectorbt_simulation(ohlcv_oos, delta_oos, thr)
        gates = gates_pass(m)
        summary["thresholds"][f"thr_{thr}"] = {
            "threshold": thr,
            "metrics": m,
            "gates": gates,
            "all_gates_pass": all(gates.values()),
        }
        print(f"[vbt] thr={thr}: Sharpe={m['sharpe']:.3f} ann={m['ann']*100:.2f}% "
              f"maxDD={m['maxdd']*100:.2f}% CIlo={m['ci_lower']:.3f} "
              f"entries={m['n_entries']} -> gates={gates}")

    out = RESULTS_DIR / "summary_vectorbt.json"
    out.write_text(json.dumps(summary, indent=2, default=float))
    print(f"[vbt] wrote {out}")
    return summary


if __name__ == "__main__":
    main()