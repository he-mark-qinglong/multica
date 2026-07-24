"""Backtest harness for SMA-34992 / LOID-V4.

Implements the 4 load-bearing invariants verified by tests:
1. Round-trip cost amortized across position lifetime (one cost per entry+exit, NOT per-bar)
2. Position flips on opposite signal (close + open opposite in same bar)
3. Time-stop force-closes after max_hold_minutes
4. Equity curve is flat between trades (realized P&L), step-jumps on exit

Cost model: BINANCE_FUTURES (4bp taker), impact_factor=0.05 for large-cap perp.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# Reuse canonical cost model from _shared (per issue mandatory infrastructure)
from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES


@dataclass(frozen=True)
class BacktestConfig:
    threshold: float = 5.0            # min |composite| to take a position
    max_hold_minutes: int = 240       # 4h time-stop
    notional_usd: float = 100_000.0
    adv_usd: float = 5_000_000_000.0  # BTCUSDT perp ADV ~$5B
    impact_factor: float = 0.05       # large-cap futures (per cost_model docstring)
    fee_bps: float = 4.0              # BINANCE_FUTURES taker


def _trade_pnl(direction: str, entry_price: float, exit_price: float, cfg: BacktestConfig) -> float:
    """Realized P&L in USD for one round-trip, net of one cost."""
    if direction == "long":
        gross = (exit_price - entry_price) / entry_price * cfg.notional_usd
    elif direction == "short":
        gross = (entry_price - exit_price) / entry_price * cfg.notional_usd
    else:
        raise ValueError(f"unknown direction: {direction}")
    cost = apply_cost(
        cfg.notional_usd,
        cfg.adv_usd,
        venue=BINANCE_FUTURES,
        side="taker",
        impact_factor=cfg.impact_factor,
    )
    return gross - cost


def run(
    ohlcv: pd.DataFrame,
    composite: pd.DataFrame,
    cfg: BacktestConfig | None = None,
) -> dict[str, Any]:
    """Run the backtest.

    Args:
        ohlcv: 1m OHLCV frame with DatetimeIndex (UTC). Must have open/close.
        composite: per-minute composite frame with DatetimeIndex. Must have 'composite' column.
                   Index should overlap ohlcv.index; non-overlapping rows are forward-filled then zero-filled.

    Returns:
        {
            "trades": list[dict] with keys
                      [direction, entry_bar, entry_ts, entry_price, exit_bar, exit_ts, exit_price,
                       pnl_usd, exit_reason, held_minutes]
            "equity": pd.Series indexed by ohlcv.index, starting at cfg.notional_usd,
                      step-jumps at exit bars.
        }
    """
    if cfg is None:
        cfg = BacktestConfig()
    if len(ohlcv) == 0:
        return {"trades": [], "equity": pd.Series(dtype=float)}

    n = len(ohlcv)
    # Align composite to ohlcv index (ffill, then 0)
    sig = composite["composite"].reindex(ohlcv.index, method="ffill").fillna(0.0).values

    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    for t in range(n):
        s = float(sig[t])
        # Determine target direction based on signal
        if s > cfg.threshold:
            target = "long"
        elif s < -cfg.threshold:
            target = "short"
        else:
            target = "flat"

        if position is None:
            if target != "flat":
                position = {
                    "entry_bar": t,
                    "entry_ts": ohlcv.index[t],
                    "entry_price": float(ohlcv["open"].iloc[t]),
                    "direction": target,
                }
            continue

        held = t - position["entry_bar"]
        # 1) Time-stop takes precedence
        if held >= cfg.max_hold_minutes:
            exit_price = float(ohlcv["open"].iloc[t])
            pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price, cfg)
            trades.append({
                "direction": position["direction"],
                "entry_bar": position["entry_bar"],
                "entry_ts": position["entry_ts"],
                "entry_price": position["entry_price"],
                "exit_bar": t,
                "exit_ts": ohlcv.index[t],
                "exit_price": exit_price,
                "pnl_usd": pnl,
                "exit_reason": "time_stop",
                "held_minutes": held,
            })
            position = None
            if target != "flat":
                position = {
                    "entry_bar": t,
                    "entry_ts": ohlcv.index[t],
                    "entry_price": exit_price,
                    "direction": target,
                }
            continue

        # 2) Signal-based close: opposite direction OR signal dropped to flat
        is_opposite = (
            (position["direction"] == "long" and target == "short")
            or (position["direction"] == "short" and target == "long")
        )
        is_flat_exit = (target == "flat")
        if is_opposite or is_flat_exit:
            exit_price = float(ohlcv["open"].iloc[t])
            pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price, cfg)
            reason = "signal_flip" if is_opposite else "signal_flat"
            trades.append({
                "direction": position["direction"],
                "entry_bar": position["entry_bar"],
                "entry_ts": position["entry_ts"],
                "entry_price": position["entry_price"],
                "exit_bar": t,
                "exit_ts": ohlcv.index[t],
                "exit_price": exit_price,
                "pnl_usd": pnl,
                "exit_reason": reason,
                "held_minutes": held,
            })
            position = None
            if target != "flat":
                # Open opposite in same bar (close+flip)
                position = {
                    "entry_bar": t,
                    "entry_ts": ohlcv.index[t],
                    "entry_price": exit_price,
                    "direction": target,
                }
        # else: same direction, hold

    # End of data: close any open position at last close
    if position is not None:
        exit_price = float(ohlcv["close"].iloc[-1])
        pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price, cfg)
        trades.append({
            "direction": position["direction"],
            "entry_bar": position["entry_bar"],
            "entry_ts": position["entry_ts"],
            "entry_price": position["entry_price"],
            "exit_bar": n - 1,
            "exit_ts": ohlcv.index[-1],
            "exit_price": exit_price,
            "pnl_usd": pnl,
            "exit_reason": "end_of_data",
            "held_minutes": n - 1 - position["entry_bar"],
        })

    # Build equity curve (flat between trades, step-jump at exit bars)
    equity = np.full(n, cfg.notional_usd, dtype=np.float64)
    cumulative = cfg.notional_usd
    next_trade_idx = 0
    sorted_trades = sorted(trades, key=lambda tr: tr["exit_bar"])
    for t in range(n):
        while next_trade_idx < len(sorted_trades) and sorted_trades[next_trade_idx]["exit_bar"] == t:
            cumulative += sorted_trades[next_trade_idx]["pnl_usd"]
            next_trade_idx += 1
        equity[t] = cumulative

    equity_series = pd.Series(equity, index=ohlcv.index, dtype=float)
    return {"trades": trades, "equity": equity_series}


__all__ = ["BacktestConfig", "run"]