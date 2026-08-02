"""Backtest performance benchmark (B16) — target: >100K bars/s.

Times both engines on 1M-bar synthetic data and reports bars/s:

  * ``run_backtest``          — authoritative per-bar compounding engine
  * ``run_vectorized_backtest`` — fully vectorised signal engine (B2)

The authoritative engine is also profiled with
``_shared/ops_profile.py:profile_callable`` to identify hotspots.
Findings (2026-08-02, 1M bars / 2,000 trades, best-of-3):

  * ``run_backtest``            ~2.0e7 bars/s — PASS (200x target)
  * ``run_vectorized_backtest`` ~3.4e7 bars/s — PASS (340x target)
  * Cross-engine consistency at benchmark scale: max rel err 0.0.

  Hotspot analysis: ~60% of ``run_backtest`` runtime is per-TRADE
  timestamp lookup in ``_bar_index`` — pandas ``searchsorted`` plus
  Timestamp scalar boxing (``_unbox_scalar``/``_box_func``), not per-bar
  work. The per-bar numpy passes (cumprod/pct_change/cummax) are
  memory-bandwidth-bound C code. The natural optimisation (view the
  DatetimeIndex as int64 and use raw ``numpy.searchsorted``) is
  semantics-preserving but would require modifying
  ``_shared/run_backtest.py``, which this work package forbids — and at
  200x the performance target it is not needed. Both numbers are
  reported as-is.

Usage::

    python -m _shared.bench_backtest            # 1M bars, default trade load
    python -m _shared.bench_backtest 200000 500 # custom n_bars / n_trades

Pure functions + a thin CLI; no I/O beyond stdout.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

import numpy as np
import pandas as pd

from _shared.ops_profile import profile_callable
from _shared.run_backtest import Trade, run_backtest
from _shared.vectorized_backtest import (
    VectorizedBacktestConfig,
    run_vectorized_backtest,
)

__all__ = ["BenchConfig", "synthetic_bars", "benchmark", "format_report"]


@dataclass(frozen=True)
class BenchConfig:
    """Benchmark dimensions."""

    n_bars: int = 1_000_000
    n_trades: int = 2_000
    seed: int = 7
    repeat: int = 3  # best-of-N timing per engine


def synthetic_bars(n: int, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic 1h close series of length ``n``."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2000-01-01", periods=n, freq="h", tz="UTC")
    close = 50_000.0 * np.exp(np.cumsum(rng.normal(2e-6, 0.002, size=n)))
    return pd.DataFrame({"close": close}, index=idx)


def _make_trades(index: pd.DatetimeIndex, n_trades: int, seed: int) -> List[Trade]:
    """Evenly spaced non-overlapping trades (entry/exit on bars)."""
    rng = np.random.default_rng(seed + 1)
    n = len(index)
    span = max((n - 20) // max(n_trades, 1), 2)
    trades: List[Trade] = []
    ei = 5
    while len(trades) < n_trades and ei + span // 2 < n - 1:
        hold = max(span // 2, 1)
        trades.append(
            Trade(
                entry_ts=index[ei],
                exit_ts=index[ei + hold],
                direction="long" if rng.random() < 0.5 else "short",
                size_fraction=float(rng.uniform(0.3, 1.0)),
            )
        )
        ei += span
    return trades


def _random_signals(n: int, seed: int) -> np.ndarray:
    """Vectorised random -1/0/+1 signal with multi-bar runs (no loop)."""
    rng = np.random.default_rng(seed + 2)
    # Run-length encoding: geometric run lengths, random states per run.
    states = rng.choice([-1.0, 0.0, 1.0], size=n // 8 + 2, p=[0.25, 0.5, 0.25])
    lengths = rng.geometric(p=0.125, size=n // 8 + 2)  # mean run = 8 bars
    sig = np.repeat(states, lengths)[:n]
    if sig.size < n:
        sig = np.pad(sig, (0, n - sig.size))
    return sig


def _best_of(fn: Callable[[], Any], repeat: int) -> tuple[float, Any]:
    """Return (best_seconds, last_result) over ``repeat`` timed calls."""
    best = float("inf")
    result: Any = None
    for _ in range(max(repeat, 1)):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best, result


def benchmark(config: BenchConfig = BenchConfig()) -> Dict[str, Any]:
    """Time both engines; profile the authoritative one.

    Returns a dict with per-engine seconds + bars/s, the profile table
    for ``run_backtest``, and a consistency check that both engines agree
    on the same random strategy at benchmark scale.
    """
    bars = synthetic_bars(config.n_bars, config.seed)
    trades = _make_trades(bars.index, config.n_trades, config.seed)
    close = bars["close"].to_numpy(dtype=float)
    signals = _random_signals(config.n_bars, config.seed)

    main_secs, main_res = _best_of(
        lambda: run_backtest(bars, trades), config.repeat
    )
    vec_secs, vec_res = _best_of(
        lambda: run_vectorized_backtest(
            close, signals, config=VectorizedBacktestConfig()
        ),
        config.repeat,
    )

    _, profile_table = profile_callable(run_backtest, bars, trades)

    # Consistency at benchmark scale: same random strategy, both engines.
    from _shared.vectorized_backtest import signals_to_trades

    conv_trades = signals_to_trades(bars.index, signals)
    ref = run_backtest(bars, conv_trades)
    max_rel = float(
        np.max(
            np.abs(ref["equity"].to_numpy() - vec_res["equity"])
            / ref["equity"].to_numpy()
        )
    )

    n = config.n_bars
    return {
        "n_bars": n,
        "n_trades_main": len(trades),
        "main": {"seconds": main_secs, "bars_per_sec": n / main_secs},
        "vectorized": {"seconds": vec_secs, "bars_per_sec": n / vec_secs},
        "consistency_max_rel_err": max_rel,
        "profile_table": profile_table,
    }


def format_report(result: Dict[str, Any]) -> str:
    """Human-readable benchmark report."""
    lines = [
        f"=== bench_backtest (B16) — {result['n_bars']:,} bars ===",
        (
            f"run_backtest (authoritative, {result['n_trades_main']} trades): "
            f"{result['main']['seconds']:.3f}s  "
            f"-> {result['main']['bars_per_sec']:,.0f} bars/s"
        ),
        (
            f"run_vectorized_backtest (B2 engine): "
            f"{result['vectorized']['seconds']:.3f}s  "
            f"-> {result['vectorized']['bars_per_sec']:,.0f} bars/s"
        ),
        (
            "target >100,000 bars/s: "
            f"run_backtest={'PASS' if result['main']['bars_per_sec'] > 100_000 else 'MISS'} "
            f"vectorized={'PASS' if result['vectorized']['bars_per_sec'] > 100_000 else 'MISS'}"
        ),
        (
            "cross-engine consistency at benchmark scale: "
            f"max rel err = {result['consistency_max_rel_err']:.3e}"
        ),
        "",
        "run_backtest hotspots (cProfile, cumulative):",
        result["profile_table"],
    ]
    return "\n".join(lines)


def main() -> None:
    n_bars = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    n_trades = int(sys.argv[2]) if len(sys.argv) > 2 else 2_000
    print(format_report(benchmark(BenchConfig(n_bars=n_bars, n_trades=n_trades))))


if __name__ == "__main__":
    main()
