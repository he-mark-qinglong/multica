"""Backtest ↔ paper-path parity validator (B19).

Runs the SAME strategy over the SAME bar slice through two independent
drivers and compares the resulting fill sequences:

  * **backtest path** — batch: strategy decisions are converted into a
    ``Trade`` schedule and executed by the authoritative
    ``_shared.run_backtest`` engine; fills are derived from the applied
    schedule at the engine's accounting prices (signal-bar closes).
  * **paper path** — online: an independent bar-by-bar event loop (the
    shape of the live paper runner) that maintains position state,
    records fills as transitions happen, and compounds equity
    incrementally. It mirrors run_backtest's ``"fill"`` cost convention:
    entry commission at signal bar + 1, exit commission at the exit bar
    (full round-trip cost when the round trip spans a single held bar),
    plus the force-close drag when a direction flip closes and re-opens
    on the same bar.

Parity contract: every paired fill must differ by less than
``price_tol_bp`` in price and less than ``time_tol_bars`` in timestamp,
and both paths must produce the same number of fills. The terminal
equity difference is reported (and pinned near machine precision in the
regression tests).

Strategy contract: a callable ``strategy(ts, bar, position) -> int``
returning the target position ∈ {-1, 0, +1} given the bar timestamp, the
bar row (``pd.Series`` with at least ``close``) and the current signed
position. Signals on the final two bars are flatten-only (forced to 0)
so every trade closes on a valid bar under both paths.

References
----------
  - Grinold & Kahn (2000), *Active Portfolio Management*, ch. on
    implementation shortfall — simulated-vs-live divergence as a
    measurable cost.
  - SMA-35145 / SMA-35100 (workspace-internal) — Sharpe divergence from
    engine-convention drift; this module pins the convention so drift
    fails loudly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Literal, Mapping, Sequence

import pandas as pd

from _shared.run_backtest import Trade, run_backtest

Direction = Literal["long", "short"]
StrategyFn = Callable[[pd.Timestamp, pd.Series, int], int]


@dataclass(frozen=True)
class ParityParams:
    """Tolerances and shared economics for both paths."""

    initial_capital: float = 100_000.0
    cost_bps_rt: float = 24.0
    size_fraction: float = 1.0
    freq_per_year: int = 365 * 24
    price_tol_bp: float = 1.0
    time_tol_bars: float = 1.0


@dataclass(frozen=True)
class Fill:
    """One execution record produced by either path."""

    ts: pd.Timestamp
    side: str            # "buy" | "sell"
    price: float
    reason: str          # "entry" | "exit"


@dataclass(frozen=True)
class FillMismatch:
    """One divergent (or unpaired) fill."""

    index: int
    backtest_fill: Fill | None
    paper_fill: Fill | None
    price_diff_bp: float | None
    time_diff_bars: float | None


@dataclass(frozen=True)
class PathResult:
    """Output of one driver path."""

    fills: tuple[Fill, ...]
    equity: pd.Series
    metrics: Mapping[str, float]


@dataclass(frozen=True)
class ParityReport:
    """Parity verdict + diagnostics."""

    ok: bool
    n_backtest_fills: int
    n_paper_fills: int
    n_mismatches: int
    mismatches: tuple[FillMismatch, ...]
    max_price_diff_bp: float
    max_time_diff_bars: float
    equity_final_diff_pct: float
    metrics_backtest: Mapping[str, float]
    metrics_paper: Mapping[str, float]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clamp_target(raw: Any) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(-1, min(1, v))


def _effective_target(strategy: StrategyFn, ts: pd.Timestamp,
                      bar: pd.Series, position: int, j: int, n: int) -> int:
    """Strategy target with the flatten-only contract on the last 2 bars."""
    if j >= n - 2:
        return 0
    return _clamp_target(strategy(ts, bar, position))


def _basic_metrics(equity: pd.Series, initial_capital: float) -> dict[str, float]:
    final = float(equity.iloc[-1]) if len(equity) else initial_capital
    return {
        "final_equity": final,
        "total_return_pct": final / initial_capital - 1.0,
        "n_bars": int(len(equity)),
    }


def _trades_from_targets(
    bars: pd.DataFrame,
    targets: Sequence[int],
    size_fraction: float,
) -> List[Trade]:
    """Convert per-bar target positions into a closed-Trade schedule.

    A position change closes the open trade at the transition bar and (if
    the new target is non-zero) opens the next one on the same bar.
    """
    trades: List[Trade] = []
    idx = bars.index
    pos = 0
    entry_ts: pd.Timestamp | None = None
    for j, target in enumerate(targets):
        if target == pos:
            continue
        if pos != 0 and entry_ts is not None:
            trades.append(Trade(
                entry_ts=entry_ts, exit_ts=idx[j],
                direction="long" if pos > 0 else "short",
                size_fraction=size_fraction,
            ))
        entry_ts = idx[j] if target != 0 else None
        pos = target
    return trades


def _fills_from_trades(bars: pd.DataFrame, trades: Sequence[Trade]) -> tuple[Fill, ...]:
    """Derive fill records at the engine's accounting prices (bar closes).

    Applies the same on-bar validity filter as run_backtest so unapplied
    trades never surface as fills.
    """
    idx = bars.index
    close = bars["close"]
    n = len(bars)
    loc = {ts: i for i, ts in enumerate(idx)}
    fills: List[Fill] = []
    for t in trades:
        ei = loc.get(t.entry_ts)
        xi = loc.get(t.exit_ts)
        if ei is None or xi is None or xi <= ei or ei + 1 >= n or xi >= n:
            continue
        entry_side = "buy" if t.direction == "long" else "sell"
        exit_side = "sell" if t.direction == "long" else "buy"
        fills.append(Fill(t.entry_ts, entry_side, float(close.iloc[ei]), "entry"))
        fills.append(Fill(t.exit_ts, exit_side, float(close.iloc[xi]), "exit"))
    return tuple(fills)


# ---------------------------------------------------------------------------
# Path 1 — batch backtest via the authoritative engine
# ---------------------------------------------------------------------------

def run_backtest_path(
    bars: pd.DataFrame,
    strategy: StrategyFn,
    params: ParityParams,
) -> PathResult:
    """Batch path: strategy → Trade schedule → ``run_backtest``."""
    n = len(bars)
    targets: List[int] = []
    pos = 0
    for j, (ts, bar) in enumerate(bars.iterrows()):
        target = _effective_target(strategy, ts, bar, pos, j, n)
        targets.append(target)
        pos = target

    trades = _trades_from_targets(bars, targets, params.size_fraction)
    result = run_backtest(
        bars,
        trades,
        initial_capital=params.initial_capital,
        cost_bps_rt=params.cost_bps_rt,
        freq_per_year=params.freq_per_year,
    )
    fills = _fills_from_trades(bars, trades)
    metrics = dict(result["metrics"])
    metrics["final_equity"] = float(result["equity"].iloc[-1])
    return PathResult(fills=fills, equity=result["equity"], metrics=metrics)


# ---------------------------------------------------------------------------
# Path 2 — online paper event loop (independent implementation)
# ---------------------------------------------------------------------------

def run_paper_path(
    bars: pd.DataFrame,
    strategy: StrategyFn,
    params: ParityParams,
) -> PathResult:
    """Online path: bar-by-bar event loop with incremental compounding.

    Mirrors run_backtest's ``"fill"`` convention exactly (see module
    docstring) so parity pins the engine's accounting rules. Never calls
    run_backtest.
    """
    n = len(bars)
    close = bars["close"].to_numpy(dtype=float)
    idx = bars.index
    cost_rt = params.cost_bps_rt / 10_000.0

    equity = params.initial_capital
    equity_vals: List[float] = []
    fills: List[Fill] = []

    pos = 0                       # signed current position {-1, 0, +1}
    entry_bar: int | None = None
    pending_drag: dict[int, float] = {}  # bar → additive commission drag

    for j in range(n):
        ts = idx[j]
        bar = bars.iloc[j]
        target = _effective_target(strategy, ts, bar, pos, j, n)

        drag = pending_drag.pop(j, 0.0)

        # Transitions decided at this bar's close.
        new_pos, new_entry_bar = pos, entry_bar
        if target != pos:
            if pos != 0:
                # Exit fill at this bar's close; exit commission at this bar.
                exit_side = "sell" if pos > 0 else "buy"
                fills.append(Fill(ts, exit_side, float(close[j]), "exit"))
                assert entry_bar is not None
                if j > entry_bar + 1:
                    drag += params.size_fraction * cost_rt / 2.0
                else:
                    # One-held-bar round trip: engine charges the full RT.
                    drag += params.size_fraction * cost_rt
            if target != 0:
                # Entry fill at this bar's close; commission at bar j+1.
                entry_side = "buy" if target > 0 else "sell"
                fills.append(Fill(ts, entry_side, float(close[j]), "entry"))
                if j + 1 < n:
                    pending_drag[j + 1] = (pending_drag.get(j + 1, 0.0)
                                           + params.size_fraction * cost_rt / 2.0)
                    if pos != 0:
                        # Same-bar close+open (flip): engine's force-close
                        # branch charges the PRIOR trade an extra exit half
                        # at the new entry bar + 1.
                        pending_drag[j + 1] += params.size_fraction * cost_rt / 2.0
                new_entry_bar = j
            else:
                new_entry_bar = None
            new_pos = target

        # Bar return: the position held over (j-1, j] is the OLD position.
        price_ret = close[j] / close[j - 1] - 1.0 if j > 0 else 0.0
        ret = pos * params.size_fraction * price_ret - drag
        equity *= 1.0 + ret
        equity_vals.append(equity)

        pos, entry_bar = new_pos, new_entry_bar

    equity_series = pd.Series(equity_vals, index=idx, dtype=float)
    return PathResult(
        fills=tuple(fills),
        equity=equity_series,
        metrics=_basic_metrics(equity_series, params.initial_capital),
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def infer_bar_seconds(bars: pd.DataFrame) -> float:
    """Median bar spacing in seconds."""
    diffs = bars.index.to_series().diff().dropna()
    if len(diffs) == 0:
        return 0.0
    return float(diffs.median().total_seconds())


def compare_fills(
    backtest_fills: Sequence[Fill],
    paper_fills: Sequence[Fill],
    bar_seconds: float,
    params: ParityParams,
) -> tuple[tuple[FillMismatch, ...], float, float]:
    """Pair fills by sequence position; return (mismatches, max Δbp, max Δbars)."""
    mismatches: List[FillMismatch] = []
    max_bp = 0.0
    max_bars = 0.0
    n = max(len(backtest_fills), len(paper_fills))
    for i in range(n):
        fb = backtest_fills[i] if i < len(backtest_fills) else None
        fp = paper_fills[i] if i < len(paper_fills) else None
        if fb is None or fp is None:
            mismatches.append(FillMismatch(i, fb, fp, None, None))
            continue
        mid = (fb.price + fp.price) / 2.0
        price_diff_bp = abs(fb.price - fp.price) / mid * 10_000.0 if mid > 0 else 0.0
        if bar_seconds > 0:
            time_diff_bars = abs((fb.ts - fp.ts).total_seconds()) / bar_seconds
        else:
            time_diff_bars = 0.0 if fb.ts == fp.ts else float("inf")
        max_bp = max(max_bp, price_diff_bp)
        max_bars = max(max_bars, time_diff_bars)
        if (fb.side != fp.side
                or price_diff_bp >= params.price_tol_bp
                or time_diff_bars >= params.time_tol_bars):
            mismatches.append(FillMismatch(i, fb, fp, price_diff_bp, time_diff_bars))
    return tuple(mismatches), max_bp, max_bars


def validate_parity(
    bars: pd.DataFrame,
    strategy: StrategyFn,
    params: ParityParams | None = None,
) -> ParityReport:
    """Run both paths over ``bars`` and compare the fill sequences."""
    params = params or ParityParams()
    bt = run_backtest_path(bars, strategy, params)
    pp = run_paper_path(bars, strategy, params)
    mismatches, max_bp, max_bars = compare_fills(
        bt.fills, pp.fills, infer_bar_seconds(bars), params,
    )
    eq_bt = float(bt.equity.iloc[-1]) if len(bt.equity) else params.initial_capital
    eq_pp = float(pp.equity.iloc[-1]) if len(pp.equity) else params.initial_capital
    eq_diff_pct = (
        (eq_pp - eq_bt) / eq_bt * 100.0 if eq_bt > 0 else 0.0
    )
    return ParityReport(
        ok=len(mismatches) == 0,
        n_backtest_fills=len(bt.fills),
        n_paper_fills=len(pp.fills),
        n_mismatches=len(mismatches),
        mismatches=mismatches,
        max_price_diff_bp=max_bp,
        max_time_diff_bars=max_bars,
        equity_final_diff_pct=eq_diff_pct,
        metrics_backtest=bt.metrics,
        metrics_paper=pp.metrics,
    )


__all__ = [
    "Fill",
    "FillMismatch",
    "ParityParams",
    "ParityReport",
    "PathResult",
    "StrategyFn",
    "compare_fills",
    "infer_bar_seconds",
    "run_backtest_path",
    "run_paper_path",
    "validate_parity",
]
