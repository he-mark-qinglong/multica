"""Performance profiling utilities (J18).

Thin cProfile wrapper with a ``@profile_section(name)`` decorator that
accumulates per-section statistics across calls, plus a function-level
cumulative-time table reporter. Ships with an example entry point that
profiles the market-making simulator on synthetic aggTrades::

    python -m _shared.ops_profile

References:
  - CPython ``cProfile``/``pstats`` deterministic profiling docs
"""
from __future__ import annotations

import cProfile
import functools
import io
import pstats
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Section registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionStats:
    """Aggregated profiling stats for one named section."""

    name: str
    calls: int
    total_seconds: float  # wall time of the profiled calls
    stats: pstats.Stats


_SECTIONS: dict[str, list] = {}  # name -> [calls, total_seconds, pstats.Stats]


def _merge_stats(existing: Optional[pstats.Stats], new: pstats.Stats) -> pstats.Stats:
    if existing is None:
        return new
    existing.add(new)
    return existing


def profile_section(name: str) -> Callable:
    """Decorator: profile every call under section ``name``.

    Stats accumulate across calls; inspect with :func:`report`.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result, _ = profile_callable(fn, *args, _section=name, **kwargs)
            return result

        return wrapper

    return decorator


def profile_callable(
    fn: Callable,
    *args: Any,
    _section: Optional[str] = None,
    **kwargs: Any,
) -> tuple[Any, str]:
    """Run ``fn`` under cProfile; return ``(result, table)``.

    When ``_section`` is given, the run's stats are merged into the named
    section registry as well.
    """
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        result = fn(*args, **kwargs)
    finally:
        profiler.disable()

    stats = pstats.Stats(profiler)
    table = _format_stats(stats)

    if _section is not None:
        entry = _SECTIONS.get(_section)
        if entry is None:
            _SECTIONS[_section] = [1, stats.total_tt, stats]
        else:
            entry[0] += 1
            entry[1] += stats.total_tt
            entry[2] = _merge_stats(entry[2], stats)

    return result, table


def _format_stats(stats: pstats.Stats, top_n: int = 30) -> str:
    """Function-level table sorted by cumulative time."""
    buf = io.StringIO()
    stats.stream = buf
    stats.sort_stats("cumulative").print_stats(top_n)
    stats.stream = io.StringIO()  # detach before returning
    return buf.getvalue()


def report(top_n: int = 30) -> str:
    """Function-level cumulative-time table for all registered sections."""
    if not _SECTIONS:
        return "no profiled sections"
    parts = []
    for name, (calls, total, stats) in sorted(_SECTIONS.items()):
        parts.append(f"=== section: {name} (calls={calls}, total={total:.4f}s) ===")
        parts.append(_format_stats(stats, top_n=top_n))
    return "\n".join(parts)


def reset() -> None:
    """Clear all accumulated section stats."""
    _SECTIONS.clear()


# ---------------------------------------------------------------------------
# Example: profile the maker simulator on synthetic aggTrades
# ---------------------------------------------------------------------------

def _synthetic_aggtrades(n: int = 20_000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-04-19", tz="UTC")
    offsets = np.cumsum(rng.exponential(scale=0.5, size=n))  # seconds
    ts = start + pd.to_timedelta(offsets, unit="s")
    price = 60_000.0 * np.exp(np.cumsum(rng.normal(0, 1e-4, size=n)))
    qty = rng.uniform(0.001, 0.5, size=n)
    is_buyer_maker = rng.random(n) < 0.5
    return pd.DataFrame(
        {"ts": ts, "price": price, "qty": qty, "is_buyer_maker": is_buyer_maker}
    )


def main() -> None:
    """Profile ``simulate_market_making`` on a synthetic trade tape."""
    from _shared.market_making.maker_simulator import (
        MakerSimConfig,
        simulate_market_making,
    )

    aggtrades = _synthetic_aggtrades()
    config = MakerSimConfig(
        start_ts="2026-04-19",
        end_ts="2026-04-20",
        trade_step=1,
    )

    _, table = profile_callable(simulate_market_making, aggtrades, config)
    print(table)


if __name__ == "__main__":
    main()
