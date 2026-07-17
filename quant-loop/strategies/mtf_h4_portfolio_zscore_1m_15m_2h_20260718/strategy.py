"""H4 strategy — multi-pair portfolio with 1m/15m/2h filters + portfolio caps.

Re-exports the shared base's H4 signal builder and the H4 portfolio builder
(correlation-aware sizing + gross/net exposure caps) so that other modules
in this strategy directory can ``from strategy import ...``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from _indicators.mtf_xs_pairs_base_20260718 import (  # noqa: E402
    VARIANT_KEY,
    aggregate_ohlcv,
    build_h4_signals,
    build_h4_portfolio,
    build_portfolio,
    daily_returns,
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)

__all__ = [
    "VARIANT_KEY",
    "aggregate_ohlcv",
    "build_h4_signals",
    "build_h4_portfolio",
    "build_portfolio",
    "daily_returns",
    "profit_factor_and_mdd",
    "run_backtest",
    "sharpe_daily_resampled",
]