"""KAMA trend strategy — BTC 4h.

Kaufman Adaptive Moving Average (KAMA) slope-based long-only trend follower.
Signal: KAMA(er, fast, slow) slope over `slope_lookback` bars.
  slope > 0 → long, slope ≤ 0 → flat.
Position effective next bar (no lookahead). 7bp round-trip cost.

Validated via CPCV (n_groups=6, k_test=2, purge=50, embargo=20):
  mean OOS Sharpe = 1.12, DSR = 1.10 (n_trials=864), worst fold = +0.27.

See `research/kama_trend/REPORT.md` for the full 864-cell grid search and
`research/kama_trend/cpcv_validation.json` for the formal CPCV result.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]  # quant-loop/
CONFIG_PATH = REPO_ROOT / "config.json"

FEE_PER_SIDE = 0.00035  # 7bp round-trip = 3.5bp per side


@dataclass
class BacktestResult:
    equity: np.ndarray
    returns: pd.Series
    signal: pd.Series
    n_trades: int
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    calmar: float


def kama(close: pd.Series, er_window: int = 5, fast: int = 2, slow: int = 30) -> pd.Series:
    """Kaufman Adaptive Moving Average.

    Args:
        close: close prices indexed by timestamp.
        er_window: efficiency ratio lookback.
        fast: fast SC constant period.
        slow: slow SC constant period.

    Returns:
        KAMA line (same index as close).
    """
    vals = close.values.astype(float)
    n = len(vals)
    out = np.full(n, np.nan)
    if n <= er_window + 1:
        return pd.Series(out, index=close.index)

    change = np.abs(vals[er_window:] - vals[:-er_window])
    volatility = pd.Series(np.abs(np.diff(vals))).rolling(er_window).sum().values
    er = np.zeros(n)
    er[er_window:] = change / np.maximum(volatility[er_window - 1:], 1e-12)

    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    sc[~np.isfinite(sc)] = slow_sc ** 2

    seed = er_window
    out[seed] = vals[seed]
    for i in range(seed + 1, n):
        out[i] = out[i - 1] + sc[i] * (vals[i] - out[i - 1])
    return pd.Series(out, index=close.index)


def generate_signal(close: pd.Series, cfg: dict) -> pd.Series:
    """Generate long/flat signal from KAMA slope.

    Args:
        close: close prices.
        cfg: config dict with er_window, fast, slow, slope_lookback.

    Returns:
        Float series: 1.0 (long) or 0.0 (flat).
    """
    p = cfg["params"]
    kama_line = kama(close, p["er_window"], p["fast"], p["slow"])
    slope = (kama_line - kama_line.shift(p["slope_lookback"])) / kama_line.shift(p["slope_lookback"])
    return (slope > 0).astype(float)


def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    """Run KAMA trend backtest on OHLCV data.

    Args:
        df: OHLCV DataFrame with 'close' column, timestamp index.
        cfg: config dict (see config.json).

    Returns:
        BacktestResult with equity curve and metrics.
    """
    close = df["close"]
    signal = generate_signal(close, cfg)

    ret = close.pct_change()
    pos = signal.shift(1).fillna(0.0)
    cost = FEE_PER_SIDE * pos.diff().abs().fillna(0.0)
    net_rets = (pos * ret - cost).dropna()

    equity = np.cumprod(1 + net_rets.values)
    n_bars = len(net_rets)
    n_years = n_bars / 2190.0  # 4h bars per year

    total_return = float(equity[-1] - 1.0) if len(equity) > 0 else 0.0
    annualized = float(equity[-1] ** (1.0 / max(n_years, 1e-9)) - 1.0) if len(equity) > 0 else 0.0

    # Max drawdown
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(np.min(dd)) if len(dd) > 0 else 0.0

    # Sharpe (annualized, 4h bars)
    periods_per_year = 2190
    if len(net_rets) > 1 and net_rets.std() > 1e-12:
        sharpe = float(net_rets.mean() / net_rets.std() * np.sqrt(periods_per_year))
    else:
        sharpe = 0.0

    calmar = abs(annualized / max_dd) if abs(max_dd) > 1e-9 else 0.0

    n_trades = int((pos.diff().abs() > 0).sum())

    return BacktestResult(
        equity=equity,
        returns=net_rets,
        signal=signal,
        n_trades=n_trades,
        total_return=total_return,
        annualized_return=annualized,
        max_drawdown=max_dd,
        sharpe=sharpe,
        calmar=calmar,
    )


def load_data(symbol: str = "BTCUSDT", timeframe: str = "4h") -> pd.DataFrame:
    """Load OHLCV data from parquet, aggregated to target timeframe."""
    root = QUANT_LOOP
    # symbol may be "BTC" (short) or "BTCUSDT" (full) — normalize
    sym = symbol.replace("USDT", "") if symbol.endswith("USDT") else symbol
    df = pd.read_parquet(f"{root}/data/perp_15m/{sym}USDT_15m.parquet")
    ts = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index(ts).sort_index()

    tf_rule = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(tf_rule[timeframe]).agg(agg).dropna()


if __name__ == "__main__":
    cfg = json.loads(CONFIG_PATH.read_text())
    df = load_data(cfg["symbol"], cfg["timeframe"])
    result = run_backtest(df, cfg)
    print(f"KAMA Trend BTC 4h — Backtest Results")
    print(f"  Data: {df.index[0]} → {df.index[-1]} ({len(df)} bars)")
    print(f"  Trades: {result.n_trades}")
    print(f"  Total return: {result.total_return:.2%}")
    print(f"  Annualized: {result.annualized_return:.2%}")
    print(f"  Max DD: {result.max_drawdown:.2%}")
    print(f"  Sharpe: {result.sharpe:.3f}")
    print(f"  Calmar: {result.calmar:.3f}")
