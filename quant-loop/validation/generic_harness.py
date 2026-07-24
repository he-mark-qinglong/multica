"""Generic three-framework CV harness (Phase D, 2026-07-24).

New HF strategies only emit a trade schedule — no per-strategy
``framework_adapter_*.py``, no inline equity walk, no inline metrics.

Strategy contract v2
--------------------
A strategy is a single callable::

    generate_signals(df: pd.DataFrame, cfg: dict) -> list[Trade | dict]

Each trade is either a ``_shared.run_backtest.Trade`` or a dict with keys
``entry_ts`` / ``exit_ts`` (timestamps on bars of ``df.index``), ``direction``
("long" | "short") and optional ``size_fraction`` (defaults to the config's
``sizing.per_signal_weight_pct``).

Pipeline
--------
- **native leg**: equity is walked by ``_shared.run_backtest.run_backtest``
  (cost_mode="fill") — the single authoritative engine. The strategy never
  computes its own equity or metrics.
- **framework legs**: the same schedule is replayed through
  ``validation/adapters/`` (backtrader / freqtrade / vectorbt), which re-price
  every fill independently. A framework whose engine is not installed is
  recorded in ``report["framework_skips"]`` and skipped (gate G5 then evaluates
  on whichever framework legs did run) instead of crashing the harness.
- **gates**: identical G1-G7(+T1) evaluation as the legacy variant harness via
  ``validation.gates.evaluate_gates``.

Two entry points
----------------
- :func:`run_generic_validation` — programmatic: pass the callable + config +
  data dict directly.
- :func:`run_generic_from_variant` — variant-directory contract: a directory
  with ``config.json``, ``data_loader.py`` (``load_all(symbols, timeframe)``)
  and ``signals.py`` (``generate_signals(df, cfg)``). Used by the
  ``validation.oos_harness`` CLI when a variant ships ``signals.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Iterable, Union

import pandas as pd

from _shared.run_backtest import Trade, run_backtest

from . import metrics as M
from .adapters.native_engine import FrameworkRun, _load_module
from .gates import evaluate_gates
from .windows import compute_oos_windows

QUANT_LOOP_ROOT = Path(__file__).resolve().parent.parent

TradeLike = Union[Trade, dict]
SignalFn = Callable[[pd.DataFrame, dict], Iterable[TradeLike]]

# timeframe -> bars per year (crypto trades 24/7)
_FREQ_PER_YEAR = {
    "1m": 365 * 24 * 60,
    "5m": 365 * 24 * 12,
    "15m": 365 * 24 * 4,
    "30m": 365 * 24 * 2,
    "1h": 365 * 24,
    "2h": 365 * 12,
    "4h": 365 * 6,
    "8h": 365 * 3,
    "1d": 365,
}

FRAMEWORKS = ("native", "backtrader", "freqtrade", "vectorbt")


def freq_per_year(timeframe: str) -> int:
    try:
        return _FREQ_PER_YEAR[timeframe]
    except KeyError:
        raise ValueError(
            f"unsupported timeframe {timeframe!r}; known: {sorted(_FREQ_PER_YEAR)}"
        ) from None


def normalize_trades(trades: Iterable[TradeLike], default_size: float) -> list[Trade]:
    """Coerce generate_signals output into run_backtest.Trade objects."""
    out: list[Trade] = []
    for t in trades:
        if isinstance(t, Trade):
            out.append(t)
            continue
        d = dict(t)
        direction = d["direction"]
        if direction not in ("long", "short"):
            raise ValueError(f"trade direction must be 'long'|'short', got {direction!r}")
        out.append(Trade(
            entry_ts=pd.Timestamp(d["entry_ts"]),
            exit_ts=pd.Timestamp(d["exit_ts"]),
            direction=direction,
            size_fraction=float(d.get("size_fraction", default_size)),
        ))
    return out


def trade_dicts(trades: Iterable[Trade]) -> list[dict]:
    """Convert Trades to the normalized dict shape replay adapters consume."""
    return [
        {
            "direction": t.direction,
            "entry_date": t.entry_ts,
            "exit_date": t.exit_ts,
        }
        for t in trades
    ]


def _slice(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df.loc[(df.index >= start) & (df.index <= end)]


def _native_run(
    df: pd.DataFrame,
    trades: list[Trade],
    symbol: str,
    *,
    starting_capital: float,
    cost_bps_rt: float,
    fpy: int,
) -> FrameworkRun:
    """Native leg: authoritative shared engine + close-to-close per-trade pnl."""
    result = run_backtest(
        df, trades,
        initial_capital=starting_capital,
        cost_bps_rt=cost_bps_rt,
        cost_mode="fill",
        freq_per_year=fpy,
    )
    idx = df.index
    close = df["close"]
    cost_rt = cost_bps_rt / 1e4
    pnls: list[float] = []
    dicts: list[dict] = []
    for t in trades:
        if t.entry_ts not in idx or t.exit_ts not in idx or t.exit_ts <= t.entry_ts:
            continue  # mirrors the engine's off-bar skip
        d = 1.0 if t.direction == "long" else -1.0
        gross = (float(close[t.exit_ts]) / float(close[t.entry_ts]) - 1.0) * d
        pnl = t.size_fraction * gross - t.size_fraction * cost_rt
        pnls.append(pnl)
        dicts.append({
            "symbol": symbol,
            "direction": t.direction,
            "entry_date": t.entry_ts,
            "entry_price": float(close[t.entry_ts]),
            "exit_date": t.exit_ts,
            "exit_price": float(close[t.exit_ts]),
            "pnl_pct": pnl,
        })
    return FrameworkRun(
        framework="native",
        symbol=symbol,
        equity=result["equity"],
        trade_pnls=pnls,
        trades=dicts,
    )


def _run_framework_leg(
    name: str,
    dfs: pd.DataFrame,
    native: FrameworkRun,
    *,
    symbol: str,
    timeframe: str,
    starting_capital: float,
    commission: float,
    weight: float,
    max_open: int,
    keep_ft_dir: bool,
) -> FrameworkRun:
    """Dispatch one replay leg. Imports are lazy: a missing engine raises the
    adapter's own *ReplayError / ImportError, which the caller records as a
    framework skip."""
    if name == "backtrader":
        from .adapters.backtrader_replay import run_backtrader_replay
        return run_backtrader_replay(
            dfs, native.trades, symbol=symbol,
            starting_cash=starting_capital, commission=commission, weight=weight)
    if name == "freqtrade":
        from .adapters.freqtrade_replay import run_freqtrade_replay
        return run_freqtrade_replay(
            dfs, native.trades, symbol=symbol, timeframe=timeframe,
            starting_wallet=starting_capital,
            stake_per_trade=starting_capital * weight,
            max_open_trades=max_open, fee=commission, keep_dir=keep_ft_dir)
    if name == "vectorbt":
        from .adapters.vectorbt_replay import run_vectorbt_replay
        return run_vectorbt_replay(
            dfs, native.trades, symbol=symbol,
            starting_cash=starting_capital, fees=commission, size=weight)
    raise ValueError(f"unknown framework leg: {name!r}")


def run_generic_validation(
    generate_signals: SignalFn,
    config: dict,
    data: dict[str, pd.DataFrame],
    *,
    n_windows: int = 3,
    frameworks: Iterable[str] = ("native", "backtrader", "freqtrade"),
    output_dir: Path | None = None,
    variant_name: str = "generic",
    keep_ft_dir: bool = False,
) -> tuple[bool, dict]:
    """Run native + framework CV for a signal-layer strategy.

    Parameters
    ----------
    generate_signals : callable(df, cfg) -> iterable of Trade | dict
        Signal layer. Called once per (window, symbol) on the sliced frame
        and once per symbol on the full-span frame.
    config : dict
        Strategy config. Recognised keys: ``timeframe`` (default "1h"),
        ``fees_bps_per_side`` (1.0), ``slippage_bps_per_side`` (1.0),
        ``starting_capital_usd`` (100_000), ``sizing.per_signal_weight_pct``
        (0.01), ``sizing.max_gross_exposure_pct`` (0.05).
    data : {symbol: ohlcv DataFrame}
        Full-span bars per symbol; windowing is done here.
    frameworks : subset of {"native", "backtrader", "freqtrade", "vectorbt"}
    output_dir : where verdict.json / verdict.md are written
        (default ``results/validation_generic/`` under the quant-loop root).

    Returns (passed, report) — same shape as the legacy harness report, plus
    ``pipeline: "generic"`` and ``framework_skips``.
    """
    frameworks = [f for f in frameworks]
    unknown = set(frameworks) - set(FRAMEWORKS)
    if unknown:
        raise ValueError(f"unknown frameworks: {sorted(unknown)} (known: {list(FRAMEWORKS)})")
    if not data:
        raise ValueError("data must contain at least one symbol")

    timeframe = str(config.get("timeframe", "1h"))
    fpy = freq_per_year(timeframe)
    fees_bps = float(config.get("fees_bps_per_side", 1.0))
    slippage_bps = float(config.get("slippage_bps_per_side", 1.0))
    commission = (fees_bps + slippage_bps) / 1e4
    cost_bps_rt = 2.0 * (fees_bps + slippage_bps)
    starting_capital = float(config.get("starting_capital_usd", 100_000.0))
    sizing = config.get("sizing", {})
    weight = float(sizing.get("per_signal_weight_pct", 0.01))
    max_gross = float(sizing.get("max_gross_exposure_pct", 0.05))
    max_open = max(1, round(max_gross / max(weight, 1e-9)))

    print(f"[generic-harness] variant={variant_name} tf={timeframe} "
          f"symbols={sorted(data)} windows={n_windows} frameworks={frameworks}")

    span_start = min(df.index[0] for df in data.values())
    span_end = max(df.index[-1] for df in data.values())
    windows = compute_oos_windows(span_start, span_end, n_windows)
    print(f"[generic-harness] data span {span_start} .. {span_end}")
    for w in windows:
        print(f"[generic-harness]   {w.label}")

    report: dict = {
        "variant": variant_name,
        "pipeline": "generic",
        "timeframe": timeframe,
        "windows": [w.label for w in windows],
        "symbols": {},
        "framework_skips": {},
    }

    def signals_for(df: pd.DataFrame) -> list[Trade]:
        return normalize_trades(generate_signals(df, dict(config)), weight)

    # ---- full-period native runs (G1/G2/G3/G4) -----------------------------
    full_metrics_by_symbol: dict[str, dict] = {}
    if "native" in frameworks:
        for sym, df in data.items():
            run = _native_run(df, signals_for(df), sym,
                              starting_capital=starting_capital,
                              cost_bps_rt=cost_bps_rt, fpy=fpy)
            m = M.metrics_from_run(run.equity, run.trade_pnls)
            full_metrics_by_symbol[sym] = m
            print(f"[generic-harness] full native {sym}: sharpe={m['sharpe']:.3f} "
                  f"ann={m['annualized_return']:.3f} mdd={m['max_drawdown']:.3f} "
                  f"pf={m['profit_factor']:.3f} trades={m['n_trades']}")

    # ---- per-window runs (native + framework CV) ----------------------------
    window_native: list[dict] = []
    window_by_framework: dict[str, list[dict]] = {
        f: [] for f in frameworks if f != "native"}
    pooled_oos_daily: dict[str, list[pd.Series]] = {s: [] for s in data}
    pooled_oos_pnls: list[float] = []

    for w in windows:
        for sym, df in data.items():
            dfs = _slice(df, w.start, w.end)
            if dfs.empty:
                print(f"[generic-harness] {w.label} {sym}: no data in window, skipped")
                continue
            native = _native_run(dfs, signals_for(dfs), sym,
                                 starting_capital=starting_capital,
                                 cost_bps_rt=cost_bps_rt, fpy=fpy)
            m_nat = M.metrics_from_run(native.equity, native.trade_pnls)
            window_native.append(m_nat)
            pooled_oos_daily[sym].append(m_nat["daily_returns"])
            pooled_oos_pnls.extend(native.trade_pnls)
            print(f"[generic-harness] {w.label} {sym} native: sharpe={m_nat['sharpe']:.3f} "
                  f"trades={m_nat['n_trades']}")

            window_entry = {"native": M.public_metrics(m_nat)}
            for fw in window_by_framework:
                try:
                    fw_run = _run_framework_leg(
                        fw, dfs, native, symbol=sym, timeframe=timeframe,
                        starting_capital=starting_capital, commission=commission,
                        weight=weight, max_open=max_open, keep_ft_dir=keep_ft_dir)
                except Exception as e:
                    reason = f"{type(e).__name__}: {e}"
                    report["framework_skips"].setdefault(fw, reason)
                    print(f"[generic-harness] {w.label} {sym} {fw}: SKIPPED ({reason})")
                    continue
                m_fw = M.metrics_from_run(fw_run.equity, fw_run.trade_pnls)
                window_by_framework[fw].append(m_fw)
                window_entry[fw] = M.public_metrics(m_fw)
                print(f"[generic-harness] {w.label} {sym} {fw}: sharpe={m_fw['sharpe']:.3f} "
                      f"trades={m_fw['n_trades']}")

            report["symbols"].setdefault(sym, {})[w.label] = window_entry

    # ---- pooled OOS series for G6/G7 ----------------------------------------
    per_symbol_daily = [
        pd.concat(series_list).sort_index()
        for series_list in pooled_oos_daily.values() if series_list
    ]
    if per_symbol_daily:
        pooled_daily = pd.concat(per_symbol_daily, axis=1).mean(axis=1).dropna()
    else:
        pooled_daily = pd.Series(dtype=float)

    if not full_metrics_by_symbol:
        full_metrics_by_symbol = {
            sym: M.metrics_from_run(pd.Series(dtype=float), []) for sym in data}
    verdict = evaluate_gates(
        variant_name,
        full_metrics_by_symbol=full_metrics_by_symbol,
        window_native=window_native,
        window_backtrader=window_by_framework.get("backtrader", []),
        window_freqtrade=window_by_framework.get("freqtrade", []),
        pooled_oos_daily_returns=pooled_daily,
        pooled_oos_trade_pnls=pooled_oos_pnls,
    )

    report["full_native"] = {s: M.public_metrics(m) for s, m in full_metrics_by_symbol.items()}
    report["gates"] = [vars(g) for g in verdict.gates]
    report["verdict"] = "PASS" if verdict.passed else "FAIL"

    out = Path(output_dir) if output_dir else (
        QUANT_LOOP_ROOT / "results" / "validation_generic")
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdict.json").write_text(json.dumps(report, indent=2, default=float))
    md = "\n".join(verdict.summary_lines()) + "\n"
    (out / "verdict.md").write_text(md)
    print(f"[generic-harness] verdict written to {out}")
    print(md)
    return verdict.passed, report


def is_generic_variant(variant_dir: Path) -> bool:
    """A variant dir runs the generic pipeline iff it ships signals.py."""
    return (Path(variant_dir) / "signals.py").exists()


def run_generic_from_variant(
    variant_dir: Path,
    n_windows: int,
    frameworks: list[str],
    output_dir: Path | None = None,
    keep_ft_dir: bool = False,
) -> tuple[bool, dict]:
    """Variant-directory contract for the generic pipeline.

    Requires ``config.json``, ``data_loader.py`` exposing
    ``load_all(symbols, timeframe) -> {sym: df}`` and ``signals.py`` exposing
    ``generate_signals(df, cfg) -> iterable of Trade | dict``.
    """
    variant_dir = Path(variant_dir)
    config = json.loads((variant_dir / "config.json").read_text())
    for required in ("data_loader.py", "signals.py"):
        if not (variant_dir / required).exists():
            raise ValueError(f"{variant_dir.name}: generic variant requires {required}")
    data_loader = _load_module(
        f"data_loader_{variant_dir.name}", variant_dir / "data_loader.py", variant_dir)
    signals_mod = _load_module(
        f"signals_{variant_dir.name}", variant_dir / "signals.py", variant_dir)
    symbols = list(config.get("instruments", []))
    if not symbols:
        raise ValueError(f"{variant_dir.name}: config.json must list instruments")
    timeframe = str(config["timeframe"])
    data = data_loader.load_all(symbols, timeframe)
    return run_generic_validation(
        signals_mod.generate_signals, config, data,
        n_windows=n_windows, frameworks=frameworks,
        output_dir=output_dir or (variant_dir / "results" / "validation"),
        variant_name=variant_dir.name, keep_ft_dir=keep_ft_dir)


__all__ = [
    "FRAMEWORKS",
    "SignalFn",
    "TradeLike",
    "freq_per_year",
    "is_generic_variant",
    "normalize_trades",
    "run_generic_from_variant",
    "run_generic_validation",
    "trade_dicts",
]
