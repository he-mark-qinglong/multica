"""Contract-v2 signal layer for ``pairs_cointegration_1d_20260709``.

Wraps the legacy ``strategy.build_signals`` (rolling OLS hedge + z-score, see
``strategy.py:115``) into the harness-compatible ``generate_signals(bars,
config) -> list[Trade]`` shape. Equity walk is owned by the shared
``_shared.run_backtest`` engine via the generic harness; this module emits the
trade schedule only.

For each pair (A, B) the Z-score grid is built on their aligned close series
and entries/exits are walked under leg A:

    z >  +entry_threshold -> short_spread -> short A (Trade.direction == "short")
    z <  -entry_threshold -> long_spread  -> long  A (Trade.direction == "long")
    |z| < exit_threshold  -> close any open position
    |Δspread| > stop_sigma * spread_std  -> close (coint break)

Only the A leg is modelled as a Trade — the engine is single-symbol, and the
B leg's contribution would require the price-return book to model the spread
directly. The partner leg is documented as out-of-scope in W1-T12 (signal
layer only); see ``tests/test_signals.py::test_date_equivalence_with_legacy``
for the trade-plan overlap check.

Two call shapes are supported:

1. ``generate_signals(df, cfg)`` where ``df`` is a single-symbol
   :class:`pandas.DataFrame` — the generic-harness route
   (``validation/oos_harness.py`` → ``run_generic_from_variant`` calls
   ``signals_mod.generate_signals(dfs, config)`` per (window, symbol)).
   The active symbol is recovered by matching the df's close series against
   the cached 1d parquet for BTC/ETH/SOL.

2. ``generate_signals({sym: df}, cfg)`` — the contract checker
   (``_shared/templates/strategy_contract_v2.py::check_contract``) synthesizes
   a dict of frames; the primary symbol is taken from
   ``config.get("primary_symbol")`` or the dict's first key.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

import numpy as np
import pandas as pd

from _shared.run_backtest import Trade
from strategy import build_signals  # sibling; OK at import time (variant dir added to sys.path)

DATA_DIR = Path(__file__).resolve().parent / "data"
PAIRS: tuple[tuple[str, str], ...] = (
    ("BTCUSDT", "ETHUSDT"),
    ("BTCUSDT", "SOLUSDT"),
    ("ETHUSDT", "SOLUSDT"),
)

# Defaults used both by the contract checker's synthetic smoke run and as a
# fallback when the harness passes an empty config dict. Production values
# are pulled from ``config.json`` at invocation time.
DEFAULT_CONFIG: Dict[str, Any] = {
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "primary_symbol": "BTCUSDT",
    "cointegration": {"hedge_window_days": 90, "adf_maxlag": 1},
    "signal": {
        "zscore_window_days": 30,
        "entry_threshold": 2.0,
        "exit_threshold": 0.5,
        "stop_sigma_threshold": 4.0,
    },
    "position_sizing": {"leg_pct_per_pair": 0.05},
}


def _load_cached(symbol: str) -> pd.DataFrame:
    return pd.read_parquet(DATA_DIR / f"fapi_{symbol}__1d.parquet")


def _identify_symbol(df: pd.DataFrame) -> Optional[str]:
    """Return the universe symbol that ``df`` is a window of, or None.

    We match by index containment (the harness's ``_slice`` produces a contiguous
    sub-range of the full bar index) and by ``np.allclose`` on the close series.
    O(n_unique_symbols * n_common_bars); the universe has 3 members and the bar
    series is short (≤ 800 rows), so this is negligible.
    """
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        try:
            ref = _load_cached(sym)
        except FileNotFoundError:
            continue
        common = ref.index.intersection(df.index)
        # Every df bar must appear in the cached index (harness slices are
        # contiguous subsets); df and ref must agree on close at those bars.
        if (
            len(common) == len(df.index)
            and len(common) > 0
            and np.allclose(
                ref.loc[common, "close"].to_numpy(),
                df["close"].to_numpy(),
            )
        ):
            return sym
    return None


def _pair_trades(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    cfg: Mapping[str, Any],
    size: float,
) -> List[Trade]:
    """Walk a single (A, B) pair into Trade objects under leg A."""
    sig = build_signals(df_a, df_b, dict(cfg))
    out: List[Trade] = []
    in_pos = False
    side: Optional[str] = None
    entry_ts: Optional[pd.Timestamp] = None
    for dt, row in sig.iterrows():
        z = row["zscore"]
        if not np.isfinite(z):
            continue
        if in_pos:
            close_now = bool(row["coint_break"]) or bool(row["exit_signal"])
            if close_now and dt > entry_ts:
                out.append(
                    Trade(
                        entry_ts=entry_ts,  # type: ignore[arg-type]
                        exit_ts=dt,
                        direction=side,  # type: ignore[arg-type]
                        size_fraction=size,
                    )
                )
                in_pos = False
                side = None
                entry_ts = None
        else:
            if bool(row["entry_short_spread"]):
                in_pos, side, entry_ts = True, "short", dt
            elif bool(row["entry_long_spread"]):
                in_pos, side, entry_ts = True, "long", dt
    return out


def generate_signals(
    bars: Union[pd.DataFrame, Mapping[str, pd.DataFrame]],
    config: Optional[Mapping[str, Any]] = None,
) -> List[Trade]:
    """Contract-v2 entry point — see module docstring."""
    cfg: Dict[str, Any] = {**DEFAULT_CONFIG, **(dict(config) if config else {})}
    # Merge the deep-nested dicts so a partial ``{"signal": {"entry_threshold": ...}}``
    # override preserves the rest of the signal schema.
    cfg["cointegration"] = {**DEFAULT_CONFIG["cointegration"], **cfg.get("cointegration", {})}
    cfg["signal"] = {**DEFAULT_CONFIG["signal"], **cfg.get("signal", {})}
    cfg["position_sizing"] = {
        **DEFAULT_CONFIG["position_sizing"],
        **cfg.get("position_sizing", {}),
    }
    size = float(cfg["position_sizing"]["leg_pct_per_pair"])

    if isinstance(bars, pd.DataFrame):
        # Generic-harness mode: a single primary symbol frame, no symbol name.
        sym = _identify_symbol(bars)
        if sym is None:
            return []
        # Partner frames are reindexed onto the primary's bar index so every
        # generated Trade has its entry_ts/exit_ts on a real bar of the
        # primary series (otherwise _shared.run_backtest silently skips them
        # — see ``generic_harness.py:144-145``).
        try:
            primary_idx = bars.index
            frames = {
                s: _load_cached(s).reindex(primary_idx).dropna(subset=["close"])
                for s in cfg["symbols"]
            }
        except FileNotFoundError:
            return []
        primary = sym
        primary_df = bars
    else:
        # Dict / synthetic mode (contract checker).
        frames = {s: df.dropna(subset=["close"]) for s, df in bars.items()}
        primary = str(
            cfg.get("primary_symbol")
            or (next(iter(bars)) if bars else "BTCUSDT")
        )
        if primary not in frames:
            return []
        primary_df = frames[primary]

    min_bars = (
        cfg["cointegration"]["hedge_window_days"]
        + cfg["signal"]["zscore_window_days"]
        + 5  # warmup slack
    )

    out: List[Trade] = []
    for a, b in PAIRS:
        # The pair (A, B) is only emitted under A — so the primary frame must
        # be A (and A's full close series must be present).
        if a != primary or a not in frames or b not in frames:
            continue
        fa = frames[a]
        fb = frames[b]
        if len(fa) < min_bars or len(fb) < min_bars:
            continue
        # Reindex both legs on the primary bar index to guarantee Trade
        # timestamps are in ``bars.index`` (the generic harness's off-bar
        # skip would otherwise drop them silently).
        fa = fa.reindex(primary_df.index).dropna(subset=["close"])
        fb = fb.reindex(primary_df.index).dropna(subset=["close"])
        if len(fa) < min_bars or len(fb) < min_bars:
            continue
        out.extend(_pair_trades(fa, fb, cfg, size))

    # Sort by entry_ts so downstream equity walk is deterministic regardless
    # of pair-iteration order.
    out.sort(key=lambda t: t.entry_ts)
    return out


__all__ = ["generate_signals", "DEFAULT_CONFIG", "PAIRS", "DATA_DIR"]
