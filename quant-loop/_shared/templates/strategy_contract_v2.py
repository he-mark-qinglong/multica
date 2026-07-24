"""Strategy contract v2 — the interface every new (high-frequency) strategy must implement.

Phase D of PLAN_20260724_hf_strategy_optimization. Rules:

1. A strategy is a **signal layer only**: it exposes
   ``generate_signals(bars, config) -> list[Trade]`` and nothing else.
   Equity walking always goes through
   ``_shared.run_backtest.run_backtest(cost_mode="fill")``.
2. Indicators (VPVR / ATR / RSI / regime) MUST be imported from
   ``_shared/indicators/`` — inline re-implementations are banned.
3. No local data copies: bars come from ``data/perp_*`` + ``data/trades/``
   manifests and are handed to the strategy as a ``dict[str, pd.DataFrame]``.

Contract
--------

.. code-block:: python

    def generate_signals(
        bars: dict[str, pd.DataFrame],
        config: dict,
    ) -> list[Trade]:
        ...

- ``bars`` maps symbol -> OHLCV frame indexed by UTC ``pd.Timestamp``
  (must contain at least a ``close`` column).
- ``config`` is a plain dict of strategy parameters (symbol, timeframes,
  thresholds). A module SHOULD expose ``DEFAULT_CONFIG`` so the contract
  checker can run it on synthetic bars.
- Returns a list of ``_shared.run_backtest.Trade`` — closed trades with
  ``entry_ts``/``exit_ts`` on the primary symbol's bar index,
  ``direction`` in ``{"long", "short"}``, ``size_fraction`` in ``[0, 1]``.

Public API
----------
- :func:`validate_module_signature` — structural check (cheap, no run).
- :func:`validate_trades` — validate a trade list against a bar index.
- :func:`check_contract` — full check: signature + synthetic smoke run.
- :func:`make_synthetic_bars` — deterministic synthetic OHLCV frames.
"""
from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pandas as pd

from _shared.run_backtest import Trade

#: Error raised for every contract violation.
class ContractError(Exception):
    """Strategy module violates the v2 contract."""


# ---------------------------------------------------------------------------
# Synthetic data for smoke runs.
# ---------------------------------------------------------------------------
def make_synthetic_bars(
    symbols: Sequence[str] = ("SYNTH",),
    n_bars: int = 500,
    *,
    freq: str = "1h",
    start: str = "2026-01-01",
    seed: int = 42,
) -> Dict[str, pd.DataFrame]:
    """Deterministic random-walk OHLCV frames keyed by symbol."""
    rng = np.random.default_rng(seed)
    out: Dict[str, pd.DataFrame] = {}
    for k, sym in enumerate(symbols):
        idx = pd.date_range(start, periods=n_bars, freq=freq, tz="UTC")
        rets = rng.normal(0.0, 0.005, size=n_bars)
        close = 100.0 * np.exp(np.cumsum(rets)) * (1.0 + 0.1 * k)
        spread = np.abs(rng.normal(0.0, 0.002, size=n_bars))
        high = close * (1.0 + spread)
        low = close * (1.0 - spread)
        open_ = np.concatenate([[close[0]], close[:-1]])
        volume = np.abs(rng.normal(1000.0, 200.0, size=n_bars))
        out[sym] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close,
             "volume": volume},
            index=idx,
        )
    return out


# ---------------------------------------------------------------------------
# Structural signature check.
# ---------------------------------------------------------------------------
def validate_module_signature(module: ModuleType) -> None:
    """Check the module exposes ``generate_signals(bars, config)``.

    Raises :class:`ContractError` on any violation.
    """
    fn = getattr(module, "generate_signals", None)
    if fn is None:
        raise ContractError(
            f"{getattr(module, '__name__', module)!r} has no 'generate_signals'"
        )
    if not callable(fn):
        raise ContractError("'generate_signals' is not callable")
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as exc:  # pragma: no cover - builtins
        raise ContractError(f"cannot inspect generate_signals: {exc}") from exc
    params = list(sig.parameters.values())
    positional = [
        p for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) < 2 and not any(
        p.kind == p.VAR_POSITIONAL for p in params
    ):
        raise ContractError(
            "generate_signals must accept (bars, config) as its first two "
            f"positional parameters, got {[p.name for p in params]}"
        )
    if positional and positional[0].name != "bars":
        raise ContractError(
            f"first parameter must be named 'bars', got {positional[0].name!r}"
        )
    if len(positional) >= 2 and positional[1].name != "config":
        raise ContractError(
            f"second parameter must be named 'config', got {positional[1].name!r}"
        )


# ---------------------------------------------------------------------------
# Trade-list validation.
# ---------------------------------------------------------------------------
def validate_trades(
    trades: Any,
    bar_index: pd.Index | None = None,
) -> List[Trade]:
    """Validate a ``generate_signals`` return value.

    Checks: is a list of :class:`Trade`; direction ∈ {long, short};
    ``size_fraction`` ∈ [0, 1]; ``entry_ts < exit_ts``; and (when
    ``bar_index`` is given) both timestamps are on bars.

    Returns the validated list. Raises :class:`ContractError` otherwise.
    """
    if not isinstance(trades, (list, tuple)):
        raise ContractError(
            f"generate_signals must return a list of Trade, got {type(trades).__name__}"
        )
    ts_index = pd.DatetimeIndex(bar_index) if bar_index is not None else None
    for i, t in enumerate(trades):
        if not isinstance(t, Trade):
            raise ContractError(
                f"trade[{i}] is not a _shared.run_backtest.Trade: {type(t).__name__}"
            )
        if t.direction not in ("long", "short"):
            raise ContractError(
                f"trade[{i}].direction must be 'long' or 'short', got {t.direction!r}"
            )
        if not (0.0 <= float(t.size_fraction) <= 1.0):
            raise ContractError(
                f"trade[{i}].size_fraction must be in [0, 1], got {t.size_fraction}"
            )
        if not (isinstance(t.entry_ts, pd.Timestamp)
                and isinstance(t.exit_ts, pd.Timestamp)):
            raise ContractError(
                f"trade[{i}] entry_ts/exit_ts must be pd.Timestamp"
            )
        if not t.exit_ts > t.entry_ts:
            raise ContractError(
                f"trade[{i}] exit_ts ({t.exit_ts}) must be after entry_ts ({t.entry_ts})"
            )
        if ts_index is not None:
            for field, ts in (("entry_ts", t.entry_ts), ("exit_ts", t.exit_ts)):
                loc = ts_index.searchsorted(ts)
                if loc >= len(ts_index) or ts_index[loc] != ts:
                    raise ContractError(
                        f"trade[{i}].{field} ({ts}) is not on a bar in the "
                        "primary symbol's bar index"
                    )
    return list(trades)


# ---------------------------------------------------------------------------
# Full contract check (signature + synthetic smoke run).
# ---------------------------------------------------------------------------
def check_contract(
    module: ModuleType,
    *,
    run_synthetic: bool = True,
    n_bars: int = 500,
    seed: int = 42,
) -> Dict[str, Any]:
    """Validate a strategy module against the v2 contract.

    With ``run_synthetic=True`` (default) the module's
    ``generate_signals`` is executed on deterministic synthetic bars
    built from ``DEFAULT_CONFIG`` (falling back to a single ``SYNTH``
    symbol) and the returned trades are validated against the primary
    symbol's bar index.

    Returns a small report dict ``{"ok": True, "n_trades": int}``.
    Raises :class:`ContractError` on violation.
    """
    validate_module_signature(module)
    if not run_synthetic:
        return {"ok": True, "n_trades": None}

    config: Mapping[str, Any] = dict(getattr(module, "DEFAULT_CONFIG", {}) or {})
    symbols = config.get("symbols")
    if symbols is None:
        symbols = [config.get("symbol", "SYNTH")]
    symbols = [str(s) for s in symbols]
    bars = make_synthetic_bars(symbols, n_bars=n_bars, seed=seed)

    trades = module.generate_signals(bars, dict(config))
    primary = str(config.get("primary_symbol") or config.get("symbol") or symbols[0])
    bar_index = bars[primary].index if primary in bars else None
    validated = validate_trades(trades, bar_index)
    return {"ok": True, "n_trades": len(validated)}


__all__ = [
    "ContractError",
    "Trade",
    "make_synthetic_bars",
    "validate_module_signature",
    "validate_trades",
    "check_contract",
]
