"""Generic strategy runner — contract v2 -> backtest -> 9-key metrics.

Phase D of PLAN_20260724_hf_strategy_optimization. One entry point for
any strategy module implementing the v2 contract::

    python -m _shared.templates.run_strategy path/to/strategy.py \
        --config config.json --bars-dir data/perp_1m --symbol BTCUSDT

Flow:

1. Load the strategy module from its file path.
2. Structural contract check (``validate_module_signature``).
3. ``trades = module.generate_signals(bars, config)``.
4. Equity walk via ``_shared.run_backtest.run_backtest(cost_mode="fill")``
   on the primary symbol's bars.
5. Metrics via ``_shared.validation.compute_metrics.compute_metrics``
   (the single 9-key schema).

The strategy itself never computes equity or metrics.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Mapping

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]  # quant-loop/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _shared.run_backtest import Trade, run_backtest  # noqa: E402
from _shared.templates.strategy_contract_v2 import (  # noqa: E402
    validate_module_signature,
    validate_trades,
)
from _shared.validation.compute_metrics import compute_metrics  # noqa: E402

#: Default annualisation factors per detected bar spacing.
_MINUTES_PER_YEAR = 365 * 24 * 60


def load_strategy_module(path: str | Path) -> ModuleType:
    """Import a strategy module from a filesystem path."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"strategy module not found: {path}")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load strategy module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(path.stem, module)
    spec.loader.exec_module(module)
    return module


def load_bars_file(path: str | Path) -> pd.DataFrame:
    """Load one OHLCV frame from CSV or parquet; index = UTC timestamps."""
    path = Path(path)
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        for col in ("timestamp", "open_time", "time", "date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True)
                df = df.set_index(col)
                break
        else:
            df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def load_bars_dir(directory: str | Path, symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """Load ``{SYMBOL}.csv`` / ``{SYMBOL}.parquet`` for each symbol."""
    directory = Path(directory)
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        for ext in (".parquet", ".csv"):
            cand = directory / f"{sym}{ext}"
            if cand.is_file():
                out[sym] = load_bars_file(cand)
                break
        else:
            raise FileNotFoundError(
                f"no bars file for {sym!r} in {directory} "
                f"(tried {sym}.parquet / {sym}.csv)"
            )
    return out


def infer_freq_per_year(index: pd.Index) -> int:
    """Annualisation factor from the median bar spacing (365-day year)."""
    if len(index) < 3:
        return 365 * 24
    idx = pd.DatetimeIndex(index)
    deltas = (idx[1:] - idx[:-1]).total_seconds()
    med_minutes = float(pd.Series(deltas).median()) / 60.0
    if med_minutes <= 0:
        return 365 * 24
    return max(1, int(round(_MINUTES_PER_YEAR / med_minutes)))


def estimate_trade_pnls(
    trades: List[Trade],
    bars: pd.DataFrame,
    cost_bps_rt: float,
) -> List[float]:
    """Approximate per-trade net pnl fractions (for per-trade win_rate).

    Uses close-to-close price move over (entry, exit] minus the
    round-trip cost — matches the engine's fill-cost convention.
    """
    close = bars["close"]
    cost_rt = cost_bps_rt / 10_000.0
    pnls: List[float] = []
    for t in trades:
        try:
            entry_px = float(close.loc[:t.entry_ts].iloc[-1])
            exit_px = float(close.loc[:t.exit_ts].iloc[-1])
        except IndexError:
            continue
        d = 1.0 if t.direction == "long" else -1.0
        pnls.append(float(t.size_fraction) * ((exit_px / entry_px - 1.0) * d - cost_rt))
    return pnls


def run_strategy(
    strategy_path: str | Path,
    config: Mapping[str, Any] | None = None,
    *,
    bars: Dict[str, pd.DataFrame] | None = None,
    bars_dir: str | Path | None = None,
    initial_capital: float = 100_000.0,
    cost_bps_rt: float = 24.0,
    cost_mode: str = "fill",
    freq_per_year: int | None = None,
) -> Dict[str, Any]:
    """Run a contract-v2 strategy end-to-end.

    Parameters
    ----------
    strategy_path
        Filesystem path to the strategy module (must implement
        ``generate_signals(bars, config) -> list[Trade]``).
    config
        Strategy config dict (merged over the module's DEFAULT_CONFIG by
        the strategy itself). ``symbol``/``symbols`` select which frames
        are loaded; ``primary_symbol`` picks the equity-walk frame.
    bars
        Pre-loaded ``{symbol: frame}``. When omitted, frames are loaded
        from ``bars_dir`` for the configured symbols.
    bars_dir
        Directory with ``{SYMBOL}.parquet`` / ``{SYMBOL}.csv`` files.
    initial_capital, cost_bps_rt, cost_mode, freq_per_year
        Passed to ``_shared.run_backtest.run_backtest``; freq defaults to
        the inferred bar spacing of the primary symbol.

    Returns
    -------
    dict with ``equity`` (pd.Series), ``metrics`` (9-key schema from
    compute_metrics), ``n_trades``, ``n_skipped``, ``trades``.
    """
    module = load_strategy_module(strategy_path)
    validate_module_signature(module)

    cfg: Dict[str, Any] = dict(getattr(module, "DEFAULT_CONFIG", {}) or {})
    cfg.update(config or {})

    if bars is None:
        symbols = cfg.get("symbols") or [cfg.get("symbol", "SYNTH")]
        if bars_dir is None:
            raise ValueError("either bars or bars_dir must be provided")
        bars = load_bars_dir(bars_dir, [str(s) for s in symbols])

    trades = module.generate_signals(bars, dict(cfg))

    primary = str(cfg.get("primary_symbol") or cfg.get("symbol")
                  or next(iter(bars)))
    if primary not in bars:
        raise KeyError(f"primary symbol {primary!r} not in bars ({sorted(bars)})")
    primary_bars = bars[primary]
    validate_trades(trades, primary_bars.index)

    result = run_backtest(
        primary_bars,
        trades,
        initial_capital=initial_capital,
        cost_bps_rt=cost_bps_rt,
        cost_mode=cost_mode,  # type: ignore[arg-type]
        freq_per_year=freq_per_year or infer_freq_per_year(primary_bars.index),
    )
    fpy = freq_per_year or infer_freq_per_year(primary_bars.index)
    metrics = compute_metrics(
        result["equity"],
        n_trades=result["n_trades"],
        freq_per_year=fpy,
        trade_pnls=estimate_trade_pnls(trades, primary_bars, cost_bps_rt),
    )
    return {
        "equity": result["equity"],
        "metrics": metrics,
        "n_trades": result["n_trades"],
        "n_skipped": result["n_skipped"],
        "trades": trades,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("strategy", help="path to the strategy module (.py)")
    parser.add_argument("--config", help="path to a JSON config file")
    parser.add_argument("--bars-dir", help="directory with {SYMBOL}.csv/.parquet bars")
    parser.add_argument("--symbol", action="append", dest="symbols",
                        help="symbol to load from --bars-dir (repeatable)")
    parser.add_argument("--primary-symbol", help="symbol used for the equity walk")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--cost-bps-rt", type=float, default=24.0)
    parser.add_argument("--cost-mode", choices=["fill", "amortise"], default="fill")
    parser.add_argument("--freq-per-year", type=int, default=None)
    args = parser.parse_args(argv)

    config: Dict[str, Any] = {}
    if args.config:
        config.update(json.loads(Path(args.config).read_text()))
    if args.symbols:
        config["symbols"] = args.symbols
    if args.primary_symbol:
        config["primary_symbol"] = args.primary_symbol

    out = run_strategy(
        args.strategy,
        config,
        bars_dir=args.bars_dir,
        initial_capital=args.initial_capital,
        cost_bps_rt=args.cost_bps_rt,
        cost_mode=args.cost_mode,
        freq_per_year=args.freq_per_year,
    )
    print(json.dumps({
        "metrics": out["metrics"],
        "n_trades": out["n_trades"],
        "n_skipped": out["n_skipped"],
    }, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "load_strategy_module",
    "load_bars_file",
    "load_bars_dir",
    "infer_freq_per_year",
    "estimate_trade_pnls",
    "run_strategy",
    "main",
]
