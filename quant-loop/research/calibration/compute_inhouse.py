"""In-house buy-and-hold baseline for framework calibration.

Methodology:
  - Hold 1 unit of BTC exposure from 2024-01-01 00:00 UTC open of 2024
    through 2024-12-31 23:30 UTC close.
  - Entry: buy at the first bar's close with taker fee 0.04% on notional.
  - Exit:  sell at the last bar's close with taker fee 0.04% on notional.
  - Equity curve marked bar-by-bar on close prices (no intrabar fees).
  - Sharpe: daily-resampled log returns, annualised by sqrt(365.25).
  - Max drawdown: peak-to-trough on the equity curve.
Fee/slippage assumptions match freqtrade default: 0.04% taker per side,
0% slippage.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent / 'BTCUSDT__30m.parquet'
OUT = Path(__file__).resolve().parent / 'inhouse_buyhold_2024.json'

FEE_TAKER = 0.0004   # 0.04% per side
SLIPPAGE = 0.0       # default
START_CAP = 100000.0

def main() -> int:
    df = pd.read_parquet(SRC)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index.name = "date"
    m = (df.index >= "2024-01-01") & (df.index <= "2024-12-31 23:59:59")
    df = df.loc[m].sort_index()
    close = df["close"].astype(np.float64)

    p_start = float(close.iloc[0])
    p_end   = float(close.iloc[-1])

    # Buy-and-hold with explicit fee accounting:
    #   entry notional buys (1-fee) units of price exposure per $ invested
    #   exit applies (1-fee) on the gross proceeds
    gross_ret = p_end / p_start - 1.0
    net_mult  = (1.0 - FEE_TAKER) / (1.0 + FEE_TAKER)   # exit fee / entry fee
    net_ret   = (1.0 + gross_ret) * net_mult - 1.0

    # Bar-by-bar equity curve (start_cap normalised, entry fee on bar 0,
    # mark-to-market on close, exit fee on final bar)
    eq = pd.Series(START_CAP, index=close.index, dtype=np.float64)
    units = (START_CAP * (1.0 - FEE_TAKER)) / p_start   # BTC units after entry fee
    equity = units * close.values                       # mark-to-market each bar
    # apply exit fee only at the final bar (does not change intraperiod curve shape)
    equity[-1] = equity[-1] * (1.0 - FEE_TAKER)
    eq = pd.Series(equity, index=close.index)

    # Daily resample for Sharpe (last value per day)
    daily = eq.resample("1D").last().dropna()
    daily_ret = daily.pct_change().dropna()
    if daily_ret.std() == 0 or not np.isfinite(daily_ret.std()):
        sharpe = 0.0
    else:
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(365.25))

    # Max drawdown on daily equity
    rm = np.maximum.accumulate(daily.values)
    dd = (daily.values - rm) / rm
    max_dd = float(np.min(dd))

    span_years = (close.index[-1] - close.index[0]).total_seconds() / (365.25 * 86400)
    ann_ret = (1.0 + net_ret) ** (1.0 / span_years) - 1.0 if net_ret > -1.0 else -1.0

    out = {
        "strategy": "BTC buy-and-hold (calibration baseline)",
        "data": {
            "source": str(SRC),
            "symbol": "BTCUSDT",
            "timeframe": "30m",
            "range_start_utc": str(close.index[0]),
            "range_end_utc":   str(close.index[-1]),
            "n_bars": int(len(close)),
        },
        "assumptions": {
            "fee_taker_per_side": FEE_TAKER,
            "slippage": SLIPPAGE,
            "start_capital": START_CAP,
            "entry_at": "first bar close",
            "exit_at":  "last bar close",
        },
        "prices": {"p_start": p_start, "p_end": p_end},
        "metrics": {
            "gross_total_return_pct": gross_ret * 100.0,
            "net_total_return_pct":   net_ret   * 100.0,
            "annualized_return_pct":  ann_ret   * 100.0,
            "sharpe_daily": sharpe,
            "max_drawdown_pct": max_dd * 100.0,
        },
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out["metrics"], indent=2))
    print("wrote", OUT)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
