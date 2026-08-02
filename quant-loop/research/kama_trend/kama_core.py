"""KAMA slope long-strategy parameter grid search — core logic.

Data : data/perp_15m/{BTC,ETH,SOL}USDT_15m.parquet aggregated to 1h/4h/1d.
Signal: KAMA(er_window, fast, slow) slope over `slope_lookback` bars.
        slope > 0 -> long, slope <= 0 -> flat. Signal at bar close, position
        effective next bar (no lookahead).
Fees  : 7 bp round-trip -> 3.5 bp charged per unit |position change|.
t-stat: plain per-bar t = mean(r_net)/std(r_net)*sqrt(N).
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd

ROOT = "/Users/mark/multica/quant-loop"
FEE_PER_SIDE = 0.00035  # 7 bp round-trip = 3.5 bp per side

ER_WINDOWS = [5, 10, 15, 20]
FASTS = [2, 3]
SLOWS = [20, 30, 50]
LOOKBACKS = [1, 3, 5, 10]
TIMEFRAMES = ["1h", "4h", "1d"]
SYMBOLS = ["BTC", "ETH", "SOL"]

TF_RULE = {"1h": "1h", "4h": "4h", "1d": "1D"}
BARS_PER_2Y = {"1h": 2 * 365 * 24, "4h": 2 * 365 * 6, "1d": 2 * 365}


def load_ohlc(symbol: str, tf: str) -> pd.DataFrame:
    df = pd.read_parquet(f"{ROOT}/data/perp_15m/{symbol}USDT_15m.parquet")
    ts = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index(ts).sort_index()
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    out = df.resample(TF_RULE[tf]).agg(agg).dropna()
    return out


def kama(close: pd.Series, er_window: int, fast: int, slow: int) -> pd.Series:
    """Kaufman Adaptive Moving Average (vectorised ER, loop recursion)."""
    vals = close.values.astype(float)
    n = len(vals)
    out = np.full(n, np.nan)
    if n <= er_window + 1:
        return pd.Series(out, index=close.index)
    change = np.abs(vals[er_window:] - vals[:-er_window])
    volatility = pd.Series(np.abs(np.diff(vals))).rolling(er_window).sum().values
    er = np.zeros(n)
    er[er_window:] = change / np.maximum(volatility[er_window - 1 :], 1e-12)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    sc[~np.isfinite(sc)] = slow_sc**2
    seed = er_window
    out[seed] = vals[seed]
    for i in range(seed + 1, n):
        out[i] = out[i - 1] + sc[i] * (vals[i] - out[i - 1])
    return pd.Series(out, index=close.index)


def strategy_returns(close: pd.Series, signal: pd.Series) -> pd.Series:
    """signal: 1/0 target at bar close; position next bar; net of fees."""
    ret = close.pct_change()
    pos = signal.shift(1).fillna(0.0)
    cost = FEE_PER_SIDE * pos.diff().abs().fillna(0.0)
    return (pos * ret - cost).dropna()


def tstat(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return np.nan
    return float(r.mean() / r.std() * np.sqrt(len(r)))


def kama_signal(close: pd.Series, er_w: int, fast: int, slow: int, lb: int) -> pd.Series:
    k = kama(close, er_w, fast, slow)
    slope = k - k.shift(lb)
    sig = (slope > 0).astype(float)
    sig[slope.isna()] = 0.0
    return sig


def ma_signal(close: pd.Series, window: int = 20) -> pd.Series:
    ma = close.rolling(window).mean()
    sig = (close > ma).astype(float)
    sig[ma.isna()] = 0.0
    return sig
