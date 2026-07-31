"""Cross-framework backtest adapters.

Each submodule wraps an external framework (backtrader, freqtrade, fastquant,
vectorbt) and exposes a uniform ``run_<engine>_backtest`` entry point so the
authoritative in-house engine in ``_shared/run_backtest.py`` can be
cross-validated against an independent implementation.

The adapter surface is intentionally tiny:

    equity, metrics = run_<engine>_backtest(bars, trades, **kwargs)

where ``metrics`` is a dict with the keys the
``_shared/validators/framework_cv_validator.py`` expects under
``framework_cv["framework"]`` (``sharpe``, ``total_return``, ``max_dd``,
plus a few informational fields).

Adapters MUST:

  * Be pure functions of their inputs (no global state, no I/O).
  * Honour the per-bar compounding convention (per-bar equity update) so
    divergence vs the in-house engine is attributable to cost model /
    timing / signal-handling differences, not compounding order.
  * Fall back to a deterministic shim when the optional dependency is
    not installed — this lets unit tests run on bare CI without forcing
    every framework on the path.

The two adapters shipped in this package are:

  * ``backtrader_adapter`` — SMA-35409 / MAP-P5 #042, event-driven
    broker (percent commission per fill, next-bar-open execution).
  * ``fastquant_adapter`` — SMA-35404 / MAP-P5 #037, backtesting.py
    derived broker (same percent-commission model, vectorised portfolio).
"""
from __future__ import annotations

from .backtrader_adapter import (
    BACKTRADER_AVAILABLE,
    BACKTRADER_DEFAULT_COMMISSION,
    BACKTRADER_DEFAULT_SMA_FAST,
    BACKTRADER_DEFAULT_SMA_SLOW,
    BACKTRADER_DEFAULT_STRATEGY,
    BACKTRADER_SUPPORTED_STRATEGIES,
    BacktraderMetrics,
    is_available as backtrader_is_available,
    import_error as backtrader_import_error,
    run_backtrader_backtest,
    to_framework_cv as to_backtrader_framework_cv,
)
from .fastquant_adapter import (
    FASTQUANT_DEFAULT_COMMISSION,
    FASTQUANT_DEFAULT_FAST_PERIOD,
    FASTQUANT_DEFAULT_SLOW_PERIOD,
    FASTQUANT_DEFAULT_STRATEGY,
    FASTQUANT_SUPPORTED_STRATEGIES,
    FastquantMetrics,
    run_fastquant_backtest,
)

__all__ = [
    "BACKTRADER_AVAILABLE",
    "BACKTRADER_DEFAULT_COMMISSION",
    "BACKTRADER_DEFAULT_SMA_FAST",
    "BACKTRADER_DEFAULT_SMA_SLOW",
    "BACKTRADER_DEFAULT_STRATEGY",
    "BACKTRADER_SUPPORTED_STRATEGIES",
    "BacktraderMetrics",
    "FASTQUANT_DEFAULT_COMMISSION",
    "FASTQUANT_DEFAULT_FAST_PERIOD",
    "FASTQUANT_DEFAULT_SLOW_PERIOD",
    "FASTQUANT_DEFAULT_STRATEGY",
    "FASTQUANT_SUPPORTED_STRATEGIES",
    "FastquantMetrics",
    "backtrader_is_available",
    "backtrader_import_error",
    "run_backtrader_backtest",
    "run_fastquant_backtest",
    "to_backtrader_framework_cv",
]