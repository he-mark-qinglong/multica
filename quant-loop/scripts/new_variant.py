#!/usr/bin/env python3
"""Generate a contract-v2 strategy variant skeleton.

Usage (from the quant-loop directory)::

    python3 scripts/new_variant.py <name> --timeframe 1h --symbols BTCUSDT,SOLUSDT

Writes ``strategies/<name>_<YYYYMMDD>/`` containing:

* ``config.json``           — aligned with ``validation.generic_harness`` defaults.
* ``data_loader.py``        — ``load_all(symbols, timeframe)`` reading
                              ``data/perp_<tf>/<SYM>_<tf>.parquet`` and falling
                              back to ``data/perp_1m/`` + resample when the
                              requested timeframe parquet is absent.
* ``signals.py``            — ``generate_signals(bars, config)`` returning
                              ``_shared.run_backtest.Trade`` instances. The
                              signature uses positional parameters named
                              ``bars`` and ``config`` so the contract-v2
                              structural check passes, and accepts both a
                              single-symbol ``pd.DataFrame`` (the harness call
                              shape) and a ``{symbol: DataFrame}`` dict (the
                              contract checker call shape).

After writing the files, this tool runs
``_shared.templates.strategy_contract_v2.check_contract`` against the freshly
generated ``signals.py`` so a broken scaffold never reaches the strategies
tree. On a contract failure the half-baked directory is removed and the
process exits with code 1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make ``_shared`` importable for the self-check at the bottom of this module.
_HERE = Path(__file__).resolve().parent
_QUANT_LOOP_ROOT = _HERE.parent
if str(_QUANT_LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(_QUANT_LOOP_ROOT))

from _shared.templates.strategy_contract_v2 import (  # noqa: E402  (sys.path mutation above)
    ContractError,
    check_contract,
)


# ---------------------------------------------------------------------------
# Generated file templates.
#
# Triple-quoted strings below are the literal bodies of the two generated
# Python files. ``{{...}}`` escapes are literal braces that survive the
# ``.format()`` call; ``{name}`` / ``{value}`` placeholders are filled from
# build_variant()'s keyword arguments.
# ---------------------------------------------------------------------------

DATA_LOADER_TEMPLATE = '''"""Data loader for {variant_name} (contract v2).

Exposes ``load_all(symbols, timeframe) -> {{sym: DataFrame}}``. Each frame is
indexed by tz-UTC ``pd.DatetimeIndex`` and carries OHLCV columns. When the
requested ``perp_<timeframe>/<SYM>_<timeframe>.parquet`` file is absent and
``timeframe != "1m"``, the loader falls back to ``perp_1m`` and resamples to
the requested frequency.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

QUANT_LOOP_ROOT = Path(__file__).resolve().parents[2]

# Resample aggregations: keep the OHLCV semantics of the canonical engine.
_RESAMPLE_AGG = {{
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}}


def _set_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Move ``open_time`` (unix-ms column) to a tz-UTC DatetimeIndex."""
    if "open_time" in df.columns:
        idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        out = df.drop(columns=["open_time"]).copy()
        out.index = idx
    elif isinstance(df.index, pd.DatetimeIndex):
        out = df.copy()
    else:
        raise ValueError(
            "parquet has no open_time column and index is not a DatetimeIndex"
        )
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    return out.sort_index()


def _load_one(symbol: str, timeframe: str) -> pd.DataFrame:
    primary = (
        QUANT_LOOP_ROOT
        / "data"
        / "perp_{{timeframe}}"
        / f"{{symbol}}_{{timeframe}}.parquet"
    )
    if primary.exists():
        return _set_utc_index(pd.read_parquet(primary))

    if timeframe != "1m":
        fallback = QUANT_LOOP_ROOT / "data" / "perp_1m" / f"{{symbol}}_1m.parquet"
        if fallback.exists():
            df = _set_utc_index(pd.read_parquet(fallback))
            agg = {{k: v for k, v in _RESAMPLE_AGG.items() if k in df.columns}}
            return df.resample(timeframe).agg(agg).dropna(how="any")

    raise FileNotFoundError(
        f"no data for {{symbol}} on {{timeframe}}: tried {{primary}}"
        + (f" and fallback {{fallback}}" if timeframe != "1m" else "")
    )


def load_all(symbols, timeframe):
    return {{sym: _load_one(sym, timeframe) for sym in symbols}}
'''


SIGNALS_TEMPLATE = '''"""Signal layer for {variant_dir_name} (contract v2).

Contract v2 callable::

    generate_signals(bars, config) -> list[Trade]

* ``bars`` is either a single-symbol ``pd.DataFrame`` (the call shape used by
  ``validation.generic_harness.run_generic_from_variant``) or a
  ``{{symbol: DataFrame}}`` dict (the call shape used by
  ``_shared.templates.strategy_contract_v2.check_contract``). Both shapes are
  accepted so the same module passes the harness smoke run AND the contract
  checker's synthetic smoke run.
* ``config`` is a plain dict. ``DEFAULT_CONFIG`` is exported for the contract
  checker, which uses it to build a deterministic synthetic smoke run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

try:
    from _shared.run_backtest import Trade  # type: ignore
except ImportError:  # pragma: no cover - standalone import fallback
    _QL = Path(__file__).resolve().parents[2]
    if str(_QL) not in sys.path:
        sys.path.insert(0, str(_QL))
    from _shared.run_backtest import Trade  # type: ignore


DEFAULT_CONFIG = {{
    "symbols": {symbols_json},
    "primary_symbol": {primary_symbol_json},
    "fast": 10,
    "slow": 30,
    "hold_bars": 12,
    "size_fraction": 0.01,
}}


def generate_signals(bars, config):
    """Toy dual moving-average crossover: long on fast-over-slow, hold ``hold_bars`` bars."""
    if isinstance(bars, dict):
        primary = (config or {{}}).get("primary_symbol") or next(iter(bars))
        df = bars[primary]
    else:
        df = bars
    cfg = {{**DEFAULT_CONFIG, **(config or {{}})}}

    if df is None or len(df) < max(cfg["slow"], cfg["hold_bars"]) + 2:
        return []

    close = df["close"].astype(float)
    fast = close.rolling(cfg["fast"]).mean()
    slow = close.rolling(cfg["slow"]).mean()
    idx = df.index

    trades = []
    in_trade = False
    exit_i = -1
    for i in range(1, len(idx)):
        if in_trade:
            if i >= exit_i:
                in_trade = False
            continue
        if i <= cfg["slow"]:
            continue
        prev_fast = fast.iloc[i - 1]
        prev_slow = slow.iloc[i - 1]
        cur_fast = fast.iloc[i]
        cur_slow = slow.iloc[i]
        if (
            pd.notna(prev_fast) and pd.notna(prev_slow)
            and pd.notna(cur_fast) and pd.notna(cur_slow)
            and prev_fast <= prev_slow and cur_fast > cur_slow
        ):
            entry = idx[i]
            xi = min(i + cfg["hold_bars"], len(idx) - 1)
            trades.append(Trade(
                entry_ts=entry,
                exit_ts=idx[xi],
                direction="long",
                size_fraction=float(cfg["size_fraction"]),
            ))
            in_trade = True
            exit_i = xi
    return trades
'''


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def build_variant(
    name: str,
    timeframe: str,
    symbols: list,
    strategies_root: str | Path | None = None,
) -> Path:
    """Create ``strategies/<name>_<YYYYMMDD>/`` and return the directory path.

    Raises ``FileExistsError`` if a variant with the same name was created on
    the same UTC date. Raises ``ValueError`` for empty / non-string inputs.
    """
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string")
    if not timeframe or not isinstance(timeframe, str):
        raise ValueError("timeframe must be a non-empty string")
    if not symbols:
        raise ValueError("symbols must list at least one symbol")

    if strategies_root is None:
        strategies_root = _QUANT_LOOP_ROOT / "strategies"
    strategies_root = Path(strategies_root).resolve()

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    variant_dir = strategies_root / f"{name}_{date_str}"
    if variant_dir.exists():
        raise FileExistsError(f"variant already exists: {variant_dir}")
    variant_dir.mkdir(parents=True, exist_ok=False)

    config = {
        "timeframe": timeframe,
        "instruments": list(symbols),
        "fees_bps_per_side": 1.0,
        "slippage_bps_per_side": 1.0,
        "starting_capital_usd": 100000.0,
        "sizing": {"per_signal_weight_pct": 0.01, "max_gross_exposure_pct": 0.05},
    }
    (variant_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    (variant_dir / "data_loader.py").write_text(
        DATA_LOADER_TEMPLATE.format(variant_name=variant_dir.name)
    )
    (variant_dir / "signals.py").write_text(
        SIGNALS_TEMPLATE.format(
            variant_dir_name=variant_dir.name,
            symbols_json=json.dumps(list(symbols)),
            primary_symbol_json=json.dumps(symbols[0]),
        )
    )
    return variant_dir


def _check_scaffold(variant_dir: Path) -> dict:
    """Import the freshly written ``signals.py`` and run check_contract on it.

    Returns the contract report on success, raises ``ContractError`` on any
    structural or behavioural violation.
    """
    module_name = f"signals_{variant_dir.name}"
    spec = importlib.util.spec_from_file_location(
        module_name, variant_dir / "signals.py"
    )
    if spec is None or spec.loader is None:
        raise ContractError(
            f"cannot load signals module from {variant_dir / 'signals.py'}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return check_contract(module)


def main(argv=None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        description="Generate a contract-v2 strategy variant skeleton.",
    )
    parser.add_argument("name", help="Short strategy name (e.g. 'momentum_fast_15m')")
    parser.add_argument(
        "--timeframe", default="1h",
        help="Bar timeframe (default: 1h). Falls back to perp_1m + resample when missing.",
    )
    parser.add_argument(
        "--symbols", required=True,
        help="Comma-separated list of instruments, e.g. BTCUSDT,SOLUSDT.",
    )
    parser.add_argument(
        "--strategies-root", default=None,
        help="Override the strategies/ directory (used by tests).",
    )
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        parser.error("--symbols must list at least one symbol")

    try:
        variant_dir = build_variant(
            name=args.name,
            timeframe=args.timeframe,
            symbols=symbols,
            strategies_root=args.strategies_root,
        )
    except FileExistsError as exc:
        print(f"[new_variant] ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"[new_variant] generated scaffold at {variant_dir}")
    try:
        report = _check_scaffold(variant_dir)
    except ContractError as exc:
        print(f"[new_variant] contract check FAILED: {exc}", file=sys.stderr)
        shutil.rmtree(variant_dir)
        return 1
    print(f"[new_variant] contract OK: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())