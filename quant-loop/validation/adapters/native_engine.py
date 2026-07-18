"""Native-engine adapter: run a variant's own backtest engine on a data slice.

Contract (VPVR-family convention in quant-loop):
    <variant>/data_loader.py  exposes load_all(symbols, timeframe) -> {sym: df}
    <variant>/strategy.py     exposes run_backtest(df, cfg) -> result with
                              .equity_curve (pd.Series) and .trades (list of
                              Trade with entry_date/entry_price/exit_date/
                              exit_price/direction/pnl_pct)

A variant may instead ship <variant>/harness_adapter.py exposing
    run(df, cfg, symbol) -> (equity: pd.Series, trades: list[dict])
which takes precedence (escape hatch for other engine families).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class FrameworkRun:
    framework: str
    symbol: str
    equity: pd.Series
    trade_pnls: list[float]  # per-trade pnl as fraction of stake
    trades: list[dict]       # normalized trade dicts (symbol/direction/entry_date/
                             # entry_price/exit_date/exit_price/pnl_pct)


class UnsupportedVariantError(RuntimeError):
    """Variant does not expose a runnable engine contract."""


def _load_module(name: str, path: Path, variant_dir: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise UnsupportedVariantError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    # register before exec: dataclasses resolve cls.__module__ via sys.modules
    sys.modules[name] = mod
    # variant modules import each other as siblings (e.g. `from data_loader import ...`)
    sys.path.insert(0, str(variant_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(variant_dir))
    return mod


class NativeEngineAdapter:
    def __init__(self, variant_dir: Path):
        self.variant_dir = Path(variant_dir)
        self.config = json.loads((self.variant_dir / "config.json").read_text())
        self._harness_adapter = None
        if (self.variant_dir / "harness_adapter.py").exists():
            self._harness_adapter = _load_module(
                f"harness_adapter_{self.variant_dir.name}",
                self.variant_dir / "harness_adapter.py",
                self.variant_dir,
            )
        elif not (self.variant_dir / "strategy.py").exists():
            raise UnsupportedVariantError(
                f"{self.variant_dir.name}: no harness_adapter.py and no strategy.py"
            )

    @property
    def symbols(self) -> list[str]:
        return list(self.config["instruments"])

    @property
    def timeframe(self) -> str:
        return self.config["timeframe"]

    def load_data(self) -> dict[str, pd.DataFrame]:
        """Full-span data per symbol, via the variant's own data_loader."""
        data_loader = _load_module(
            f"data_loader_{self.variant_dir.name}",
            self.variant_dir / "data_loader.py",
            self.variant_dir,
        )
        return data_loader.load_all(self.symbols, self.timeframe)

    def run(self, df: pd.DataFrame, symbol: str) -> FrameworkRun:
        """Run the native engine on the given (already sliced) dataframe."""
        cfg = dict(self.config)
        cfg["_symbol"] = symbol
        if self._harness_adapter is not None:
            equity, trades = self._harness_adapter.run(df, cfg, symbol)
            equity = pd.Series(equity)
            trade_dicts = [dict(t) for t in trades]
        else:
            strategy = _load_module(
                f"strategy_{self.variant_dir.name}",
                self.variant_dir / "strategy.py",
                self.variant_dir,
            )
            result = strategy.run_backtest(df.copy(), cfg)
            equity = pd.Series(result.equity_curve)
            trade_dicts = [
                {
                    "symbol": symbol,
                    "direction": t.direction,
                    "entry_date": pd.Timestamp(t.entry_date),
                    "entry_price": float(t.entry_price),
                    "exit_date": pd.Timestamp(t.exit_date),
                    "exit_price": float(t.exit_price),
                    "pnl_pct": float(t.pnl_pct),
                }
                for t in result.trades
            ]
        return FrameworkRun(
            framework="native",
            symbol=symbol,
            equity=equity,
            trade_pnls=[t["pnl_pct"] for t in trade_dicts],
            trades=trade_dicts,
        )
