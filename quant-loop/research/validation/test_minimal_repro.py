#!/usr/bin/env python3
"""Tiny pair-strategy diagnostic using 50 BTC + 50 SOL bars.

This script deliberately keeps the dataset small enough to inspect by hand. It
runs the real in-house strategy, then feeds its trades to the existing
framework_adapter_freqtrade.py replay functions. It also evaluates focused
invariants for the five suspect areas covered by the pair-strategy audit.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True

DEFAULT_STRATEGY_DIR = Path(
    "/home/smark/multica/quant-loop/strategies/"
    "vpvr_xs_pairs_30m_funding_filter_20260712"
)
PAIR_ROUND_TRIP_COST = 0.0024
RELATIVE_TOLERANCE = 0.01


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _ohlcv(close: np.ndarray, index: pd.DatetimeIndex, x: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1_000.0 + 10.0 * np.cos(x),
        },
        index=index,
    )


def _relative_gap(left: float, right: float) -> float:
    return abs(left - right) / max(abs(right), 0.01)


def _max_relative_gap(left: np.ndarray, right: np.ndarray) -> float:
    denominator = np.maximum(np.abs(right), 0.01)
    return float(np.max(np.abs(left - right) / denominator))


def _manual_gross_equity(
    prices: pd.DataFrame,
    trades: pd.DataFrame,
    starting_equity: float,
) -> np.ndarray:
    """Independent 50/50-leg oracle: pos * (BTC return - SOL return) / 2."""
    timestamps = pd.DatetimeIndex(prices["ts"])
    close_a = prices["close_a"].to_numpy(dtype=float)
    close_b = prices["close_b"].to_numpy(dtype=float)
    held = np.zeros(len(prices), dtype=float)
    for trade in trades.itertuples(index=False):
        entry_idx = int(timestamps.get_loc(trade.entry_ts))
        exit_idx = int(timestamps.get_loc(trade.exit_ts))
        direction = 1.0 if trade.direction == "long_a_short_b" else -1.0
        held[entry_idx + 1 : exit_idx + 1] = direction

    equity = np.empty(len(prices), dtype=float)
    equity[0] = starting_equity
    for i in range(1, len(prices)):
        bar_return = 0.0
        if held[i] != 0.0:
            a_return = close_a[i] / close_a[i - 1] - 1.0
            b_return = close_b[i] / close_b[i - 1] - 1.0
            bar_return = held[i] * (a_return - b_return) / 2.0
        equity[i] = equity[i - 1] * (1.0 + bar_return)
    return equity


def run_diagnostic(strategy_dir: Path) -> dict[str, Any]:
    strategy_dir = strategy_dir.expanduser().resolve()
    strategy_path = strategy_dir / "strategy.py"
    adapter_path = strategy_dir / "framework_adapter_freqtrade.py"
    if not strategy_path.is_file() or not adapter_path.is_file():
        raise RuntimeError(f"strategy.py or framework_adapter_freqtrade.py missing in {strategy_dir}")

    strategy = _load_module("sma35067_minimal_strategy", strategy_path)
    adapter = _load_module("sma35067_minimal_adapter", adapter_path)

    # Exactly 50 bars per leg. SOL timestamps are deliberately five minutes late.
    index = pd.date_range("2025-01-01", periods=50, freq="30min")
    x = np.arange(50, dtype=float)
    common_market = 0.001 * x
    spread = 0.035 * np.sin(2.0 * np.pi * x / 10.0)
    spread[40:] = 0.0  # force the last trade flat before the dataset ends
    btc_close = 30_000.0 * np.exp(common_market + spread / 2.0)
    sol_close = 100.0 * np.exp(common_market - spread / 2.0)

    btc = _ohlcv(btc_close, index, x)
    sol_offset = _ohlcv(sol_close, index + pd.Timedelta("5min"), x)
    raw_overlap = len(btc.index.intersection(sol_offset.index))
    sol = strategy.resample_ohlcv(sol_offset, rule="30min")
    aligned_index = btc.index.intersection(sol.index)
    alignment_pass = raw_overlap == 0 and len(aligned_index) == 50
    btc = btc.loc[aligned_index]
    sol = sol.loc[aligned_index]

    # Funding is benign, then a known blow-off is injected at bar 20.
    funding = pd.Series(
        [0.0, 0.01, 0.0],
        index=[index[0], index[20], index[32]],
        name="funding_rate",
    )
    config = {
        "indicators": {
            "zscore_lookback_bars": 8,
            "vpvr_window_bars": 8,
            "vpvr_n_bins": 4,
            "atr_period": 3,
            "vpvr_proximity_atr_k": 1.0,
            "zscore_entry_threshold": 1.0,
            "funding_8h_ema_window": 2,
            "funding_filter_threshold": 0.001,
        },
        "entry": {
            "require_vpvr_confluence": False,
            "require_funding_filter": True,
        },
        "exit": {
            "zscore_exit_threshold": 0.25,
            "regime_switch_zscore_threshold": 4.0,
            "max_holding_bars": 6,
        },
        "fees_bps_per_side": 4.0,
        "slippage_bps_per_side": 2.0,
        "starting_capital_usd": 100_000.0,
    }

    inhouse_result = strategy.run_pair_backtest(
        btc,
        sol,
        config,
        pair_label="BTCUSDT/SOLUSDT",
        funding_a=funding,
        funding_b=funding,
    )
    trades = pd.DataFrame(inhouse_result["trades"])
    if trades.empty:
        raise RuntimeError("synthetic strategy emitted no closed trades")
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True).dt.tz_convert(None)
    trades["exit_ts"] = pd.to_datetime(trades["exit_ts"], utc=True).dt.tz_convert(None)

    prices = pd.DataFrame(
        {
            "ts": aligned_index,
            "close_a": btc["close"].to_numpy(dtype=float),
            "close_b": sol["close"].to_numpy(dtype=float),
        }
    )
    replay_inhouse, _, replay_skipped = adapter.replay_inhouse_bar_mtm(
        prices,
        trades,
        config["starting_capital_usd"],
        cost_rt=PAIR_ROUND_TRIP_COST,
    )
    replay_framework, _, framework_skipped, framework_out_of_window = (
        adapter.replay_freqtrade_bar_mtm(
            prices,
            trades,
            config["starting_capital_usd"],
            cost_rt=PAIR_ROUND_TRIP_COST,
        )
    )
    replay_gross, _, _ = adapter.replay_inhouse_bar_mtm(
        prices,
        trades,
        config["starting_capital_usd"],
        cost_rt=0.0,
    )

    strategy_equity = np.asarray(inhouse_result["equity"], dtype=float)
    inhouse_replay_gap = _max_relative_gap(strategy_equity, replay_inhouse.to_numpy())
    engine_equity_gap = _max_relative_gap(
        replay_inhouse.to_numpy(), replay_framework.to_numpy()
    )
    engine_match_pass = (
        replay_skipped == 0
        and framework_skipped == 0
        and framework_out_of_window == 0
        and inhouse_replay_gap <= RELATIVE_TOLERANCE
        and engine_equity_gap <= RELATIVE_TOLERANCE
    )

    inhouse_metrics = adapter.compute_metrics(replay_inhouse)
    framework_metrics = adapter.compute_metrics(replay_framework)
    sharpe_gap = _relative_gap(
        float(inhouse_metrics["sharpe"]), float(framework_metrics["sharpe"])
    )
    ann_return_gap = _relative_gap(
        float(inhouse_metrics["ann_total_return"]),
        float(framework_metrics["ann_total_return"]),
    )

    manual_gross = _manual_gross_equity(
        prices, trades, config["starting_capital_usd"]
    )
    sizing_gap = _max_relative_gap(replay_gross.to_numpy(), manual_gross)
    sizing_pass = sizing_gap <= 1e-12

    exit_cost_errors: list[float] = []
    gross_equity = replay_gross.to_numpy(dtype=float)
    net_equity = replay_inhouse.to_numpy(dtype=float)
    for trade in trades.itertuples(index=False):
        exit_idx = int(aligned_index.get_loc(trade.exit_ts))
        gross_exit_return = gross_equity[exit_idx] / gross_equity[exit_idx - 1] - 1.0
        net_exit_return = net_equity[exit_idx] / net_equity[exit_idx - 1] - 1.0
        exit_cost_errors.append(
            abs((gross_exit_return - net_exit_return) - PAIR_ROUND_TRIP_COST)
        )
    max_exit_cost_error = max(exit_cost_errors, default=math.inf)
    cost_pass = max_exit_cost_error <= 1e-12

    funding_allow, funding_ema = strategy.funding_ema_filter(
        aligned_index,
        funding,
        config["indicators"]["funding_8h_ema_window"],
        config["indicators"]["funding_filter_threshold"],
    )
    funding_without_injection = funding.iloc[:1]
    allow_without_future, _ = strategy.funding_ema_filter(
        aligned_index,
        funding_without_injection,
        config["indicators"]["funding_8h_ema_window"],
        config["indicators"]["funding_filter_threshold"],
    )
    funding_future_invariance_pass = bool(
        (funding_allow.iloc[:20].to_numpy() == allow_without_future.iloc[:20].to_numpy()).all()
    )
    blocked_entries = [
        trade.entry_ts.isoformat()
        for trade in trades.itertuples(index=False)
        if not bool(funding_allow.loc[trade.entry_ts])
    ]
    funding_gate_pass = not blocked_entries
    funding_same_bar_visible = not bool(funding_allow.loc[index[20]])

    lookback = int(config["indicators"]["zscore_lookback_bars"])
    zscore = strategy.pair_zscore(btc["close"], sol["close"], lookback)
    finite_positions = np.flatnonzero(np.isfinite(zscore.to_numpy(dtype=float)))
    first_finite_position = int(finite_positions[0]) if len(finite_positions) else -1
    mutated_sol = sol["close"].copy()
    mutated_sol.iloc[31:] *= 1.5
    mutated_zscore = strategy.pair_zscore(btc["close"], mutated_sol, lookback)
    future_invariant = np.allclose(
        zscore.iloc[:31].to_numpy(dtype=float),
        mutated_zscore.iloc[:31].to_numpy(dtype=float),
        equal_nan=True,
    )
    rolling_pass = first_finite_position == lookback - 1 and bool(future_invariant)

    checks = {
        "funding_filter": funding_future_invariance_pass and funding_gate_pass,
        "cross_symbol_alignment": alignment_pass,
        "pair_trade_sizing": sizing_pass,
        "two_leg_fee_slippage": cost_pass,
        "rolling_window_boundary": rolling_pass,
        "dual_engine_match": engine_match_pass
        and sharpe_gap <= RELATIVE_TOLERANCE
        and ann_return_gap <= RELATIVE_TOLERANCE,
    }
    checks = {name: bool(passed) for name, passed in checks.items()}
    verdict = "PASS" if all(checks.values()) else "FAIL"

    return {
        "strategy": strategy_dir.name,
        "bars": {"BTCUSDT": len(btc), "SOLUSDT": len(sol_offset)},
        "synthetic_trades": int(len(trades)),
        "cost_rt_bps": round(PAIR_ROUND_TRIP_COST * 10_000.0, 10),
        "checks": checks,
        "diagnostics": {
            "raw_offset_overlap_bars": raw_overlap,
            "aligned_overlap_bars": len(aligned_index),
            "funding_same_bar_visible": funding_same_bar_visible,
            "funding_ema_at_injection": float(funding_ema.loc[index[20]]),
            "blocked_entry_timestamps": blocked_entries,
            "inhouse_replay_gap_pct": inhouse_replay_gap * 100.0,
            "engine_equity_gap_pct": engine_equity_gap * 100.0,
            "sharpe_gap_pct": sharpe_gap * 100.0,
            "ann_return_gap_pct": ann_return_gap * 100.0,
            "sizing_gap_pct": sizing_gap * 100.0,
            "max_exit_cost_error_bps": max_exit_cost_error * 10_000.0,
            "zscore_first_finite_position": first_finite_position,
            "zscore_expected_first_finite_position": lookback - 1,
            "zscore_future_invariant": bool(future_invariant),
        },
        "verdict": verdict,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy-dir",
        type=Path,
        default=DEFAULT_STRATEGY_DIR,
        help=f"strategy implementation to probe (default: {DEFAULT_STRATEGY_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = run_diagnostic(args.strategy_dir)
    except Exception as exc:
        print(json.dumps({"verdict": "ERROR", "error": str(exc)}, sort_keys=True))
        return 2

    for name, passed in result["checks"].items():
        print(f"[{name}] {'PASS' if passed else 'FAIL'}", file=sys.stderr)
    blocked = result["diagnostics"]["blocked_entry_timestamps"]
    if blocked:
        print(
            "[funding_filter] entries were opened while the synthetic funding gate was False: "
            + ", ".join(blocked),
            file=sys.stderr,
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
