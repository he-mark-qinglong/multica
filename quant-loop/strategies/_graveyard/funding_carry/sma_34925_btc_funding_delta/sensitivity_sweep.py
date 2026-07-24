"""Sensitivity sweep: at lower thresholds (where the signal DOES fire on
Binance+Bybit BTC data), does the strategy have an edge? This is
context-only — the spec thresholds (0.0005/0.001/0.0015) are what
counts for the verdict. We document what would happen if a future
caller relaxed the threshold to a level that the data actually
supports.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_inhouse import (
    load_binance, load_bybit, load_ohlcv_4h, build_delta_series,
    state_machine, evaluate,
)

STRATEGY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STRATEGY_DIR / "results"

# Sensitivity at thresholds that actually fire on the Binance+Bybit sample.
SENS_THRESHOLDS = [0.0001, 0.0002, 0.0003, 0.0005, 0.0007, 0.001]
WINDOW_DAYS = 365
BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)


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

    summary = {"variant": "btc_funding_delta_sensitivity", "thresholds": {}}
    for thr in SENS_THRESHOLDS:
        delta_oos = df_window.loc[oos_idx, "delta_funding"]
        bt_oos = state_machine(ohlcv_oos, delta_oos, thr)
        m = evaluate(bt_oos)
        n_changes = int((bt_oos["position"].diff().abs() > 0).sum())
        summary["thresholds"][f"thr_{thr}"] = {
            "threshold": thr,
            "metrics": m,
            "n_pos_changes": n_changes,
        }
        print(f"[sens] thr={thr}: OOS Sharpe={m['sharpe']:.3f} ann={m['ann']*100:.2f}% "
              f"maxDD={m['maxdd']*100:.2f}% CIlo={m['ci_lower']:.3f} "
              f"entries={n_changes} bars_in_pos={m['n_bars_in_pos']}/{m['n_bars']}")

    # Also check full-overlap max |delta| for reference
    abs_delta_full = (bin_f.reindex(delta_df.index, method="ffill") -
                      byb_f.reindex(delta_df.index, method="ffill")).abs()
    abs_delta_full = abs_delta_full.dropna()
    summary["max_abs_delta_full_overlap"] = float(abs_delta_full.max())
    summary["max_abs_delta_oos_window"] = float(
        delta_df.loc[oos_idx, "delta_funding"].abs().max()
    )

    out = RESULTS_DIR / "summary_sensitivity.json"
    out.write_text(json.dumps(summary, indent=2, default=float))
    print(f"[sens] wrote {out}")
    return summary


if __name__ == "__main__":
    main()