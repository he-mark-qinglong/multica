"""Vectorbt cross-validation adapter.

Re-executes the native engine's trade decisions inside vectorbt's
signal-based portfolio simulator. Entry/exit *timestamps and direction* come
from the native trades; *fills are re-priced by vectorbt* — native fill
prices are not trusted. Mirrors the backtrader/freqtrade replay contract so
all three framework legs are interchangeable in the harness.

vectorbt is an optional dependency (heavy: numba/llvmlite). The import is
deferred to call time and a missing install raises VectorbtReplayError,
which the generic harness records as a framework skip instead of crashing.
"""
from __future__ import annotations

import pandas as pd

from .native_engine import FrameworkRun


class VectorbtReplayError(RuntimeError):
    pass


def _import_vectorbt():
    try:
        import vectorbt as vbt  # noqa: N813
    except ImportError as e:
        raise VectorbtReplayError(
            "vectorbt not installed (pip install vectorbt) — framework leg unavailable"
        ) from e
    return vbt


def _naive(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def run_vectorbt_replay(
    df: pd.DataFrame,
    native_trades: list[dict],
    *,
    symbol: str,
    starting_cash: float = 100_000.0,
    fees: float = 0.0002,
    size: float = 0.01,
) -> FrameworkRun:
    """Replay native trade decisions on `df` (already window-sliced).

    ``fees`` is the per-fill commission fraction (half the round-trip cost),
    matching the backtrader adapter's ``commission`` semantics. ``size`` is
    the fraction of current cash deployed per signal (``size_type="percent"``),
    matching the backtrader adapter's fixed-fraction notional sizer.
    """
    vbt = _import_vectorbt()

    df_v = df.copy()
    if df_v.index.tz is not None:
        df_v.index = df_v.index.tz_convert("UTC").tz_localize(None)

    entries = pd.Series(False, index=df_v.index)
    exits = pd.Series(False, index=df_v.index)
    short_entries = pd.Series(False, index=df_v.index)
    short_exits = pd.Series(False, index=df_v.index)
    for t in native_trades:
        e, x = _naive(t["entry_date"]), _naive(t["exit_date"])
        is_long = t["direction"] == "long"
        if e in entries.index:
            (entries if is_long else short_entries)[e] = True
        if x in exits.index:
            (exits if is_long else short_exits)[x] = True

    try:
        pf = vbt.Portfolio.from_signals(
            close=df_v["close"],
            entries=entries,
            exits=exits,
            short_entries=short_entries,
            short_exits=short_exits,
            init_cash=starting_cash,
            fees=fees,
            size=size,
            size_type="percent",
            freq=_infer_freq(df_v.index),
        )
    except Exception as e:  # vectorbt raises a zoo of internal errors
        raise VectorbtReplayError(f"vectorbt from_signals failed for {symbol}: {e}") from e

    equity = pd.Series(pf.value(), index=df_v.index, dtype=float)

    trade_pnls: list[float] = []
    try:
        rec = pf.trades.records_readable
        if rec is not None and len(rec):
            trade_pnls = [float(r) for r in rec["Return"]]
    except (AttributeError, KeyError, ValueError):
        trade_pnls = []

    return FrameworkRun(
        framework="vectorbt",
        symbol=symbol,
        equity=equity,
        trade_pnls=trade_pnls,
        trades=[],
    )


def _infer_freq(idx: pd.DatetimeIndex) -> str | None:
    freq = pd.infer_freq(idx)
    return freq if freq else None
