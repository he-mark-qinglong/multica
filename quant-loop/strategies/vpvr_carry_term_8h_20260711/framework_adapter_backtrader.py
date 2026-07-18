"""Backtrader OOS walk-forward cross-validation for V8 carry-term strategy.

The canonical signal builder is shared so this adapter isolates execution-engine
semantics. Orders, fills, position lifecycle, commissions, and exits run through
backtrader 1.9.78.123. W5 compares like-for-like OOS trade metrics only.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import backtrader as bt
import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import build_signal, run_backtest, wilder_atr

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
RESULT_PATH = ROOT / "results" / "framework_cv_backtrader.json"
W5_THRESHOLD_PCT = 50.0
EPSILON = 1e-9


def make_oos_folds(n_bars: int, n_folds: int = 4) -> list[tuple[int, int]]:
    """Return contiguous folds covering the second half of a series."""
    if n_folds < 1:
        raise ValueError("n_folds must be positive")
    if n_bars < n_folds * 2:
        raise ValueError("not enough bars for train/OOS folds")

    oos_start = n_bars // 2
    oos_size = n_bars - oos_start
    boundaries = [
        int(math.floor(oos_start + (i * oos_size / n_folds) + 0.5))
        for i in range(n_folds + 1)
    ]
    boundaries[0] = oos_start
    boundaries[-1] = n_bars
    return list(zip(boundaries, boundaries[1:]))


def compute_trade_metrics(returns: Sequence[float], span_days: float) -> dict[str, float | int]:
    """Compute deterministic trade-return metrics for one OOS fold."""
    values = np.asarray(tuple(returns), dtype=np.float64)
    if values.size == 0:
        return {"n_trades": 0, "sharpe": 0.0, "total_return": 0.0, "max_dd": 0.0}

    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    peaks = np.maximum.accumulate(equity)
    max_dd = float(np.min((equity / peaks) - 1.0))
    total_return = float(equity[-1] - 1.0)
    years = max(float(span_days) / 365.25, EPSILON)
    trades_per_year = values.size / years
    sigma = float(np.std(values, ddof=0))
    sharpe = float(np.mean(values) / sigma * math.sqrt(trades_per_year)) if sigma > 0 else 0.0
    return {
        "n_trades": int(values.size),
        "sharpe": sharpe,
        "total_return": total_return,
        "max_dd": max_dd,
    }


def relative_divergence_pct(framework_value: float, inhouse_value: float,
                            epsilon: float = EPSILON) -> float:
    """Return the W5 absolute relative divergence percentage."""
    return abs(float(framework_value) - float(inhouse_value)) / max(abs(float(inhouse_value)), epsilon) * 100.0


class ValidationData(bt.feeds.PandasData):
    """Pandas feed carrying the canonical signal and strategy-specific fields."""

    lines = ("strategy_signal", "funding_rate", "funding_spread", "atr_value", "row_number")
    params = (
        ("strategy_signal", "strategy_signal"),
        ("funding_rate", "fundingRate_binance"),
        ("funding_spread", "funding_spread_bps"),
        ("atr_value", "atr_value"),
        ("row_number", "row_number"),
        ("openinterest", -1),
    )


class CarryTermBacktraderStrategy(bt.Strategy):
    """Backtrader-native order and position lifecycle for the carry-term rules."""

    params = (("config", None), ("timestamps", ()), ("symbol", "?"))

    def __init__(self) -> None:
        self.config = self.p.config
        self.timestamps = self.p.timestamps
        self.symbol = self.p.symbol
        self.pending_order = None
        self.previous_signal = 0
        self.entry_price = 0.0
        self.entry_side = 0
        self.entry_bar = -1
        self.entry_ts = None
        self.entry_spread = 0.0
        self.trail_high = 0.0
        self.trail_low = math.inf
        self.funding_carry = 0.0
        self.trade_records: list[dict[str, object]] = []

    def _submit_entry(self, side: int, bar_number: int, timestamp: pd.Timestamp) -> None:
        close = float(self.data.close[0])
        size = max((float(self.broker.getvalue()) / close) * 0.95, 0.0)
        order = self.buy(size=size) if side > 0 else self.sell(size=size)
        order.addinfo(
            action="entry",
            side=side,
            bar_number=bar_number,
            timestamp=timestamp,
            spread=float(self.data.funding_spread[0]),
        )
        self.pending_order = order

    def _submit_exit(self, reason: str, timestamp: pd.Timestamp) -> None:
        order = self.close()
        order.addinfo(action="exit", reason=reason, timestamp=timestamp)
        self.pending_order = order

    def next(self) -> None:
        bar_number = int(self.data.row_number[0])
        timestamp = self.timestamps[bar_number]
        signal = int(self.data.strategy_signal[0])
        prior_signal = self.previous_signal
        self.previous_signal = signal

        if self.pending_order is not None:
            return

        if self.entry_side == 0:
            if signal != 0 and prior_signal == 0:
                self._submit_entry(signal, bar_number, timestamp)
            return

        close = float(self.data.close[0])
        atr = float(self.data.atr_value[0])
        spread = float(self.data.funding_spread[0])
        funding = float(self.data.funding_rate[0])
        held = bar_number - self.entry_bar + 1

        self.funding_carry += -funding * self.entry_side
        self.trail_high = max(self.trail_high, close)
        self.trail_low = min(self.trail_low, close)

        exit_config = self.config["exit"]
        move = (close - self.entry_price) * self.entry_side
        reason = None
        if np.isfinite(atr) and move <= -float(exit_config["hard_stop_atr_k"]) * atr:
            reason = "hard_stop"
        elif self.entry_side > 0 and np.isfinite(atr) and close <= self.trail_high - float(exit_config["atr_trail_k"]) * atr:
            reason = "atr_trail"
        elif self.entry_side < 0 and np.isfinite(atr) and close >= self.trail_low + float(exit_config["atr_trail_k"]) * atr:
            reason = "atr_trail"
        elif (
            bool(exit_config["spread_decay_exit"])
            and held >= int(exit_config["min_holding_bars"])
            and np.isfinite(spread)
            and abs(spread) <= float(exit_config["spread_decay_threshold_bps"])
        ):
            reason = "spread_decay"
        elif held >= int(exit_config["max_holding_bars"]):
            reason = "max_holding"

        if reason is not None:
            self._submit_exit(reason, timestamp)

    def notify_order(self, order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return

        if order.status == order.Completed and order.info.action == "entry":
            self.entry_price = float(order.executed.price)
            self.entry_side = int(order.info.side)
            self.entry_bar = int(order.info.bar_number)
            self.entry_ts = pd.Timestamp(order.info.timestamp)
            self.entry_spread = float(order.info.spread)
            self.trail_high = self.entry_price
            self.trail_low = self.entry_price
            self.funding_carry = 0.0
        elif order.status == order.Completed and order.info.action == "exit":
            exit_price = float(order.executed.price)
            gross = (exit_price / self.entry_price - 1.0) * self.entry_side
            round_trip_cost = 2.0 * (
                float(self.config["fees_bps_per_side"])
                + float(self.config["slippage_bps_per_side"])
            ) / 10000.0
            net_return = gross - round_trip_cost + self.funding_carry
            self.trade_records.append({
                "symbol": self.symbol,
                "entry_ts": self.entry_ts.isoformat(),
                "exit_ts": pd.Timestamp(order.info.timestamp).isoformat(),
                "direction": "long" if self.entry_side > 0 else "short",
                "entry_price": self.entry_price,
                "exit_price": exit_price,
                "net_return": float(net_return),
                "funding_carry": float(self.funding_carry),
                "entry_spread_bps": self.entry_spread,
                "exit_reason": str(order.info.reason),
            })
            self.entry_price = 0.0
            self.entry_side = 0
            self.entry_bar = -1
            self.entry_ts = None
            self.entry_spread = 0.0
            self.trail_high = 0.0
            self.trail_low = math.inf
            self.funding_carry = 0.0

        self.pending_order = None


def run_backtrader_replay(frame: pd.DataFrame, config: dict, symbol: str) -> list[dict[str, object]]:
    """Run one symbol through backtrader and return completed trade records."""
    prepared = frame.copy()
    prepared["strategy_signal"] = build_signal(prepared, config).astype(np.float64)
    prepared["atr_value"] = wilder_atr(prepared, int(config["sizing"]["atr_period"]))
    prepared["row_number"] = np.arange(len(prepared), dtype=np.float64)

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(float(config["starting_capital_usd"]))
    commission = (
        float(config["fees_bps_per_side"])
        + float(config["slippage_bps_per_side"])
    ) / 10000.0
    cerebro.broker.setcommission(commission=commission)
    cerebro.broker.set_coc(True)
    cerebro.adddata(ValidationData(dataname=prepared))
    cerebro.addstrategy(
        CarryTermBacktraderStrategy,
        config=config,
        timestamps=tuple(prepared.index),
        symbol=symbol,
    )
    strategies = cerebro.run(runonce=False, preload=True)
    return list(strategies[0].trade_records)


def _entry_in_fold(entry_ts: str, index: pd.DatetimeIndex, start: int, end: int) -> bool:
    timestamp = pd.Timestamp(entry_ts)
    lower = index[start]
    if end < len(index):
        return lower <= timestamp < index[end]
    return lower <= timestamp <= index[-1]


def _aggregate_metric(records: Iterable[dict], metric: str) -> float:
    values = [float(record[metric]) for record in records]
    return float(np.mean(values)) if values else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_validation() -> dict[str, object]:
    """Execute four-fold OOS validation and return the serializable report."""
    config = json.loads(CONFIG_PATH.read_text())
    all_data = load_all()
    fold_results: list[dict[str, object]] = []

    for symbol, frame in all_data.items():
        for fold_number, (start, end) in enumerate(make_oos_folds(len(frame)), start=1):
            replay_frame = frame.iloc[:end].copy()
            framework_trades = run_backtrader_replay(replay_frame, config, symbol)
            inhouse_config = {**config, "_symbol": symbol}
            inhouse_result = run_backtest(replay_frame, inhouse_config)

            framework_returns = [
                float(trade["net_return"])
                for trade in framework_trades
                if _entry_in_fold(str(trade["entry_ts"]), frame.index, start, end)
            ]
            inhouse_returns = [
                float(trade.pnl_pct)
                for trade in inhouse_result["trades"]
                if _entry_in_fold(trade.entry_ts, frame.index, start, end)
            ]
            span_days = max((end - start) * 8.0 / 24.0, 1.0)
            fold_results.append({
                "symbol": symbol,
                "fold": fold_number,
                "oos_start": frame.index[start].isoformat(),
                "oos_end": frame.index[end - 1].isoformat(),
                "framework": compute_trade_metrics(framework_returns, span_days),
                "inhouse": compute_trade_metrics(inhouse_returns, span_days),
            })

    framework_oos = {
        metric: _aggregate_metric((row["framework"] for row in fold_results), metric)
        for metric in ("sharpe", "total_return", "max_dd")
    }
    inhouse_oos = {
        metric: _aggregate_metric((row["inhouse"] for row in fold_results), metric)
        for metric in ("sharpe", "total_return", "max_dd")
    }
    divergence = {
        metric: relative_divergence_pct(framework_oos[metric], inhouse_oos[metric])
        for metric in ("sharpe", "total_return", "max_dd")
    }
    tipping_metrics = [
        metric for metric, value in divergence.items() if value > W5_THRESHOLD_PCT
    ]
    data_manifest = {
        path.name: _sha256(path)
        for path in sorted((ROOT / "data").glob("*__8h.parquet"))
    }

    return {
        "strategy": config["strategy"],
        "strategy_issue": "SMA-34745",
        "framework": "backtrader",
        "framework_version": bt.__version__,
        "validation_type": "OOS walk-forward",
        "fold_count": 4,
        "oos_fraction": 0.5,
        "signal_source": "canonical build_signal; independent backtrader order/fill/position lifecycle",
        "fee_slippage_bps_per_side": (
            float(config["fees_bps_per_side"])
            + float(config["slippage_bps_per_side"])
        ),
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "framework_oos_mean": framework_oos,
        "inhouse_oos_mean": inhouse_oos,
        "absolute_relative_divergence_pct": divergence,
        "max_absolute_relative_divergence_pct": max(divergence.values()),
        "w5_threshold_pct": W5_THRESHOLD_PCT,
        "w5_auto_archive": bool(tipping_metrics),
        "w5_tipping_metrics": tipping_metrics,
        "w5_verdict": (
            "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)"
            if tipping_metrics else "WITHIN_TOLERANCE (ESCALATE per W5)"
        ),
        "folds": fold_results,
        "data_sha256": data_manifest,
        "inhouse_metrics_path": str(ROOT / "results" / "metrics.json"),
        "inhouse_metrics_tag_untouched": True,
    }


def main() -> int:
    report = run_validation()
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "result_path": str(RESULT_PATH),
        "framework_oos_mean": report["framework_oos_mean"],
        "inhouse_oos_mean": report["inhouse_oos_mean"],
        "absolute_relative_divergence_pct": report["absolute_relative_divergence_pct"],
        "w5_auto_archive": report["w5_auto_archive"],
        "w5_tipping_metrics": report["w5_tipping_metrics"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
