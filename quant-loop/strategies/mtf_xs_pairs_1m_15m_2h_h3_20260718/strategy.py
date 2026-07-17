"""H3 strategy — funding regime + 1m/15m BTC/SOL pair."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from _indicators.mtf_xs_pairs_base_20260718 import (  # noqa: E402
    VARIANT_KEY,
    aggregate_ohlcv,
    build_h3_signals,
    build_portfolio,
    daily_returns,
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)

__all__ = [
    "VARIANT_KEY",
    "aggregate_ohlcv",
    "build_h3_signals",
    "build_portfolio",
    "daily_returns",
    "profit_factor_and_mdd",
    "run_backtest",
    "sharpe_daily_resampled",
]