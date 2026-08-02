"""Fully vectorised signal-driven backtest engine (B2).

Takes a per-bar signal array (``-1``/``0``/``+1``) and a close-price array
and produces the equity curve with **zero Python-level loops over bars** —
every step (held-position masking, entry/exit commission debits, equity
compounding) is a NumPy whole-array operation.

Cost / fill conventions are aligned bit-for-bit with the authoritative
in-house engine ``_shared/run_backtest.py`` (``cost_mode="fill"``):

  * A signal decided at bar ``t`` becomes effective at bar ``t+1``'s open
    (backtrader next-bar execution). The held window of a position entered
    at signal bar ``a`` and exited at signal bar ``b`` (signal changes away
    at ``b``) is ``(a, b]`` — the position earns the close-to-close return
    of bars ``a+1 .. b``.
  * Entry commission (half the round-trip rate, sized by position
    notional fraction) is debited at bar ``a+1``; exit commission at the
    signal-change bar ``b``. A position still open on the last bar is
    force-closed there with the same exit debit.
  * One-bar round-trips (``b == a+1``) replicate the reference engine's
    quirk of charging the entry half **plus** a full round-trip on that
    single bar — kept deliberately so the two engines agree exactly.
  * Per-bar compounding: ``equity[t] = equity[t-1] * (1 + ret[t])``.

Equivalence: converting any signal array into the corresponding
non-overlapping ``Trade`` schedule and running both engines yields equity
curves that agree to ~1e-12 (spec gate: <1%). See
``test_vectorized_backtest.py::test_matches_run_backtest_random_signals``.

References:
  - López de Prado, M. (2018), *Advances in Financial Machine Learning*,
    Ch. 13-14 (vectorised backtesting vs event-driven; the look-ahead
    hazards of vectorised fills — avoided here by the strict next-bar
    execution convention).
  - ``_shared/run_backtest.py`` module docstring (SMA-35145/SMA-35100
    convention pins).

Pure functions, frozen config dataclass, no I/O, no global state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from _shared.run_backtest import Trade

__all__ = [
    "VectorizedBacktestConfig",
    "run_vectorized_backtest",
    "signals_to_trades",
]


@dataclass(frozen=True)
class VectorizedBacktestConfig:
    """Configuration for :func:`run_vectorized_backtest`."""

    initial_capital: float = 100_000.0
    cost_bps_rt: float = 24.0          # round-trip cost in basis points
    freq_per_year: int = 365 * 24      # annualisation (default: 1h bars)


def signals_to_trades(
    index: pd.DatetimeIndex,
    signals: np.ndarray,
    size_fraction: float | np.ndarray = 1.0,
) -> List[Trade]:
    """Convert a signal array into the equivalent non-overlapping Trade schedule.

    A maximal run of identical non-zero signal from bar ``a`` until the
    signal changes at bar ``b`` becomes ``Trade(entry_ts=index[a],
    exit_ts=index[b], ...)`` — the exact schedule whose ``run_backtest``
    equity curve matches the vectorised engine. A signal held into the
    final bar exits at ``index[-1]``.

    This helper contains a Python loop over *segments* (number of position
    changes), never over bars — it is O(n_trades), not O(n_bars).
    """
    sig = np.asarray(signals, dtype=float)
    n = sig.shape[0]
    sizes = _as_size_array(size_fraction, n)
    trades: List[Trade] = []
    change = np.flatnonzero(sig[1:] != sig[:-1]) + 1  # bars where sig[j] != sig[j-1]
    starts = np.concatenate(([0], change))
    for k, a in enumerate(starts):
        s = sig[a]
        if s == 0.0 or a + 1 >= n:
            continue  # flat segment, or no held bar available after entry
        b = int(starts[k + 1]) if k + 1 < len(starts) else n - 1
        trades.append(
            Trade(
                entry_ts=index[a],
                exit_ts=index[b],
                direction="long" if s > 0 else "short",
                size_fraction=float(sizes[a]),
            )
        )
    return trades


def run_vectorized_backtest(
    close: np.ndarray,
    signals: np.ndarray,
    size_fraction: float | np.ndarray = 1.0,
    *,
    index: Optional[pd.DatetimeIndex] = None,
    config: VectorizedBacktestConfig = VectorizedBacktestConfig(),
) -> Dict[str, Any]:
    """Vectorised equity walk for a -1/0/+1 signal array.

    Parameters
    ----------
    close : np.ndarray
        Close prices, length ``n``. Must be positive.
    signals : np.ndarray
        Desired position per bar: ``+1`` long, ``-1`` short, ``0`` flat.
        The signal at bar ``t`` is executed at bar ``t+1``'s open.
    size_fraction : float or np.ndarray
        Notional fraction ∈ [0, 1]. Scalar applies to every entry; an
        array gives the fraction sampled at each entry's signal bar.
    index : pd.DatetimeIndex, optional
        Bar timestamps. When given, ``equity`` is a ``pd.Series`` indexed
        by it; otherwise a plain ``np.ndarray`` is returned.
    config : VectorizedBacktestConfig

    Returns
    -------
    dict with:
      - ``equity`` : pd.Series (if ``index`` given) else np.ndarray, length n
      - ``metrics`` : sharpe / annualised_pct / total_return_pct /
        max_drawdown_pct / n_bars (same keys as ``run_backtest``)
      - ``n_entries`` : number of position entries executed
      - ``bar_returns`` : np.ndarray, the per-bar simple returns applied
    """
    if config.initial_capital <= 0:
        raise ValueError(f"initial_capital must be > 0, got {config.initial_capital}")
    close = np.asarray(close, dtype=float)
    sig = np.asarray(signals, dtype=float)
    if close.shape != sig.shape:
        raise ValueError(
            f"close and signals must have the same shape, got {close.shape} vs {sig.shape}"
        )
    if not np.all(np.isin(sig, (-1.0, 0.0, 1.0))):
        raise ValueError("signals must contain only -1, 0, +1")
    n = close.shape[0]
    if n < 2:
        equity = np.full(max(n, 1), config.initial_capital, dtype=float)[:n]
        return _result(equity, index, config, np.zeros(n), n_entries=0)

    sizes = _as_size_array(size_fraction, n)
    cost_rt = config.cost_bps_rt / 10_000.0

    # Close-to-close per-bar returns (price_ret[0] unused, zero).
    price_ret = np.zeros(n, dtype=float)
    price_ret[1:] = close[1:] / close[:-1] - 1.0

    # Held position at bar j is the signal decided at bar j-1.
    prev_sig = np.zeros(n, dtype=float)
    prev_sig[1:] = sig[:-1]

    # Commission debits, aligned with run_backtest cost_mode="fill".
    # Bar 0 counts as a change when the signal opens non-zero.
    changed = np.zeros(n, dtype=bool)
    changed[0] = sig[0] != 0.0
    changed[1:] = sig[1:] != sig[:-1]

    # Held size at bar j: the size_fraction sampled at the signal bar where
    # the currently-held position was ENTERED (fixed for the whole trade,
    # matching run_backtest's per-Trade size_fraction). Computed without a
    # loop via a running max of the last change index.
    arange = np.arange(n)
    last_change = np.maximum.accumulate(np.where(changed, arange, 0))
    held_size = np.zeros(n, dtype=float)
    held_size[1:] = sizes[last_change[:-1]]
    prev_size = held_size

    # Gross per-bar returns (no Python loop).
    bar_ret = held_size * price_ret * prev_sig

    # Entry half-RT at the bar AFTER the entry signal bar.
    entry_sig_bars = np.flatnonzero(changed & (sig != 0.0))
    entry_bars = entry_sig_bars + 1
    entry_bars = entry_bars[entry_bars < n]
    bar_ret[entry_bars] -= sizes[entry_bars - 1] * cost_rt / 2.0

    # Exit half-RT at the signal-change bar that closes a position.
    exit_bars = np.flatnonzero(changed & (prev_sig != 0.0))
    exit_debit = prev_size[exit_bars] * cost_rt / 2.0
    # One-bar round-trip (entered at j-1, exited at j): the reference
    # engine charges a FULL round-trip on top of the entry half. Replicate.
    one_bar = np.zeros(n, dtype=bool)
    one_bar[1:] = changed[:-1]
    one_bar_rt = one_bar[exit_bars]
    exit_debit = np.where(one_bar_rt, prev_size[exit_bars] * cost_rt, exit_debit)
    bar_ret[exit_bars] -= exit_debit

    # Direct flips (non-zero -> non-zero at bar j): the reference engine's
    # one-position-at-a-time rule force-closes the prior trade at the new
    # entry bar (j+1) and charges an EXTRA exit half there, on top of the
    # normal exit half already debited at bar j. Replicate exactly.
    flip_sig_bars = np.flatnonzero(changed & (prev_sig != 0.0) & (sig != 0.0))
    flip_bars = flip_sig_bars + 1
    flip_bars = flip_bars[flip_bars < n]
    bar_ret[flip_bars] -= prev_size[flip_bars - 1] * cost_rt / 2.0

    # Position still open on the last bar: force-close with the exit half.
    if prev_sig[-1] != 0.0:
        bar_ret[-1] -= prev_size[-1] * cost_rt / 2.0

    equity = config.initial_capital * np.cumprod(1.0 + bar_ret)
    return _result(equity, index, config, bar_ret, n_entries=int(entry_bars.size))


def _as_size_array(size_fraction: float | np.ndarray, n: int) -> np.ndarray:
    """Broadcast scalar size to an array; validate range [0, 1]."""
    arr = np.asarray(size_fraction, dtype=float)
    if arr.ndim == 0:
        arr = np.full(n, float(arr))
    if arr.shape != (n,):
        raise ValueError(f"size_fraction must be scalar or length {n}, got {arr.shape}")
    if np.any(arr < 0.0) or np.any(arr > 1.0):
        raise ValueError("size_fraction values must be in [0, 1]")
    return arr


def _metrics_np(equity: np.ndarray, freq_per_year: int) -> Dict[str, float]:
    """Sharpe / total_return / max_dd — numpy mirror of run_backtest._metrics."""
    n = len(equity)
    if n < 2:
        return {
            "sharpe": 0.0,
            "annualised_pct": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "n_bars": int(n),
        }
    rets = equity[1:] / equity[:-1] - 1.0
    start, end = float(equity[0]), float(equity[-1])
    total_return = end / start - 1.0
    n_bars = n - 1
    if n_bars > 0 and start > 0 and (1.0 + total_return) > 0:
        ann = (1.0 + total_return) ** (freq_per_year / n_bars) - 1.0
    else:
        ann = -1.0
    sd = float(rets.std(ddof=1)) if len(rets) >= 2 else 0.0
    sharpe = float(rets.mean() / sd * math.sqrt(freq_per_year)) if sd > 1e-12 else 0.0
    peak = np.maximum.accumulate(equity)
    max_dd = float(((equity - peak) / peak).min())
    return {
        "sharpe": sharpe,
        "annualised_pct": float(ann),
        "total_return_pct": float(total_return),
        "max_drawdown_pct": max_dd,
        "n_bars": int(n),
    }


def _result(
    equity: np.ndarray,
    index: Optional[pd.DatetimeIndex],
    config: VectorizedBacktestConfig,
    bar_ret: np.ndarray,
    n_entries: int,
) -> Dict[str, Any]:
    metrics = _metrics_np(equity, config.freq_per_year)
    out_equity: Any = (
        pd.Series(equity, index=index, dtype=float) if index is not None else equity
    )
    return {
        "equity": out_equity,
        "metrics": metrics,
        "n_entries": n_entries,
        "bar_returns": bar_ret,
    }
