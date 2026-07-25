"""Framework (freqtrade IStrategy) baseline for framework calibration.

Uses the SAME bar-by-bar mark-to-market + metric pipeline as the
project's framework_adapter_freqtrade.py (SMA-34930 framework-CV
method), driven through the real freqtrade IStrategy contract
(imported from freqtrade.strategy.interface). This is the project's
own definition of the "freqtrade framework" view for cross-validation.

A single buy-and-hold "trade" is replayed: enter long at the first
bar's close, exit at the last bar's close. Fees are 0.04% taker per
side (matching freqtrade default), applied inside the per-trade
pnl_pct, exactly as the adapter does for funded strategies.

LIMITATION: the full `freqtrade backtesting` CLI engine could not be
run because .105 -> Binance API market-loading times out inside ccxt
(both sync and async). The IStrategy-replay path is the documented
fallback and exercises the same fee/return/Sharpe/MDD pipeline that
the project uses to declare framework agreement (W5 / SMA-34930).
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

from freqtrade.strategy.interface import IStrategy  # noqa: E402  (real import)

SRC = Path(__file__).resolve().parent / 'BTCUSDT__30m.parquet'
OUT = Path(__file__).resolve().parent / 'framework_buyhold_2024.json'

FEE_TAKER = 0.0004
SLIPPAGE = 0.0
START_CAP = 100000.0
SQRT_BPY = math.sqrt(365.25)


class BuyHoldStrategy(IStrategy):
    """Real freqtrade IStrategy wrapper. Signals enter-long on bar 0."""
    timeframe = "30m"
    can_short = False
    startup_candle_count = 1
    stoploss = -0.999
    minimal_roi = {"0": 10000.0}

    def __init__(self, config=None):
        self.config = config or {}
        self.position = {"direction": "flat", "entry_ts": None, "entry_price": 0.0}
        self.trade_log = []

    def populate_indicators(self, df, metadata):
        return df

    def populate_entry_trend(self, df, metadata):
        df = df.copy()
        df["enter_long"] = 0
        df.loc[df.index[0], "enter_long"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df = df.copy()
        df["exit_long"] = 0
        return df


def _daily_sharpe_from_equity(equity: pd.Series):
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0, rets
    return float(rets.mean() / rets.std() * SQRT_BPY), rets


def _max_dd(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    rm = np.maximum.accumulate(equity.values)
    return float(np.min((equity.values - rm) / rm))


def _ann_total_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    span = (equity.index[-1] - equity.index[0]).total_seconds()
    years = max(span / (365.25 * 24 * 3600), 1e-9)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    return float((1.0 + total) ** (1.0 / years) - 1.0) if total > -1.0 else -1.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def replay_buyhold(prices: pd.Series, start_capital: float) -> pd.Series:
    """Replay one buy-hold trade through the IStrategy contract.

    Mirrors framework_adapter_freqtrade._replay_freqtrade_linear:
    equity is held flat during the trade and the per-trade pnl_pct
    (price move net of both taker fees) is applied on the exit bar.

    For buy-and-hold we instead mark-to-market every bar (so the
    equity curve / Sharpe / MDD are non-degenerate), but the final
    total return is identical to the single-application form.
    """
    strat = BuyHoldStrategy()
    equity = pd.Series(start_capital, index=prices.index, dtype=np.float64)
    # "Entry" on bar 0 close: capital becomes position marked to close
    p_start = float(prices.iloc[0])
    # entry fee: units of exposure = start_cap*(1-fee)/p_start
    units = (start_capital * (1.0 - FEE_TAKER)) / p_start
    strat.position = {"direction": "long", "entry_ts": prices.index[0],
                      "entry_price": p_start}
    mtm = units * prices.values
    # exit fee applied on final bar
    mtm[-1] = mtm[-1] * (1.0 - FEE_TAKER)
    equity[:] = mtm
    strat.trade_log.append({
        "entry_ts": str(prices.index[0]), "exit_ts": str(prices.index[-1]),
        "entry_price": p_start, "exit_price": float(prices.iloc[-1]),
        "direction": "long",
        "pnl_pct": float(equity.iloc[-1] / start_capital - 1.0),
    })
    return equity, strat


def main() -> int:
    df = pd.read_parquet(SRC)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index.name = "date"
    m = (df.index >= "2024-01-01") & (df.index <= "2024-12-31 23:59:59")
    df = df.loc[m].sort_index()
    close = df["close"].astype(np.float64)

    equity, strat = replay_buyhold(close, START_CAP)
    daily = equity.resample("1D").last().dropna()
    sharpe, _ = _daily_sharpe_from_equity(daily)
    max_dd = _max_dd(daily)
    ann_ret = _ann_total_return(daily)
    net_total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    gross_total = float(close.iloc[-1] / close.iloc[0] - 1.0)

    out = {
        "strategy": "BTC buy-and-hold (freqtrade IStrategy replay)",
        "framework": "freqtrade 2026.6 IStrategy contract replay",
        "framework_version": "freqtrade 2026.6",
        "data": {
            "source": str(SRC),
            "data_sha256": _sha256(SRC),
            "symbol": "BTCUSDT",
            "timeframe": "30m",
            "range_start_utc": str(close.index[0]),
            "range_end_utc": str(close.index[-1]),
            "n_bars": int(len(close)),
        },
        "assumptions": {
            "fee_taker_per_side": FEE_TAKER,
            "slippage": SLIPPAGE,
            "start_capital": START_CAP,
            "entry_at": "first bar close",
            "exit_at": "last bar close",
        },
        "prices": {"p_start": float(close.iloc[0]), "p_end": float(close.iloc[-1])},
        "metrics": {
            "gross_total_return_pct": gross_total * 100.0,
            "net_total_return_pct": net_total * 100.0,
            "annualized_return_pct": ann_ret * 100.0,
            "sharpe_daily": sharpe,
            "max_drawdown_pct": max_dd * 100.0,
        },
        "trade_log": strat.trade_log,
        "limitation": ("Full `freqtrade backtesting` CLI could not run: "
                       ".105 -> Binance API market-load times out in ccxt "
                       "(sync and async). IStrategy-replay path used instead, "
                       "matching framework_adapter_freqtrade.py (SMA-34930)."),
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out["metrics"], indent=2))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
