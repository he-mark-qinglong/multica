"""Kill-criteria evaluator for SMA-35012 paper-trading harness.

Combines the smark-directed absolute thresholds (max_dd > 5%, daily_loss > 2%)
with the issue-body thresholds (PF<1.0 after >=100 trades, maxDD > 1.5x
backtest, rolling 20d Sharpe < 0). ANY single trigger halts the engine.

Trigger priority: absolute drawdown limits are checked FIRST so that
structurally-bad sessions halt before the slower PF/Sharpe thresholds
accumulate enough samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Smark-directed absolute thresholds (SMA-35012 DECISION 2026-07-20T21:41+08)
SMARK_ABSOLUTE_MAX_DD_PCT = 5.0       # halt if max_drawdown_pct > 5.0
SMARK_ABSOLUTE_DAILY_LOSS_PCT = 2.0   # halt if daily_loss_pct > 2.0


@dataclass
class KillState:
    triggered: bool = False
    reason: str = ""
    trigger_source: str = ""   # "smark_absolute" | "issue_body" | ""
    at: str = ""              # ISO timestamp the trigger fired


@dataclass
class MetricsSnapshot:
    """All metrics the kill evaluator consumes. Fields default to safe zeros."""
    equity_usd: float = 100000.0
    equity_open_today_usd: float = 100000.0
    peak_equity_usd: float = 100000.0
    max_drawdown_pct: float = 0.0          # negative number, e.g. -3.2 = -3.2%
    daily_return_pct: float = 0.0          # today's return vs equity_open_today
    profit_factor_lifetime: float = 0.0
    rolling_20d_sharpe: float = 0.0
    n_trades: int = 0
    backtest_max_dd_pct: float = 0.0       # absolute value (positive)


def evaluate(metrics: MetricsSnapshot, cfg: dict, state: KillState) -> KillState:
    """Evaluate ALL kill criteria. ANY trigger halts the engine.

    cfg must contain:
      - kill_criteria.min_trades_before_kill_check
      - kill_criteria.min_live_profit_factor
      - kill_criteria.max_drawdown_multiple_vs_backtest
      - kill_criteria.rolling_20d_sharpe_floor
      - kill_criteria.smark_absolute_max_dd_pct (optional override)
      - kill_criteria.smark_absolute_daily_loss_pct (optional override)
    """
    if state.triggered:
        return state

    kc = cfg.get("kill_criteria", {})
    smark_max_dd = float(kc.get("smark_absolute_max_dd_pct", SMARK_ABSOLUTE_MAX_DD_PCT))
    smark_daily_loss = float(kc.get("smark_absolute_daily_loss_pct", SMARK_ABSOLUTE_DAILY_LOSS_PCT))

    # --- Smark absolute thresholds (highest priority, fastest halt) ---
    dd_abs = abs(metrics.max_drawdown_pct)
    if dd_abs > smark_max_dd:
        state.triggered = True
        state.reason = (
            f"SMARK_ABSOLUTE: maxDD={dd_abs:.4f}% > {smark_max_dd:.2f}% "
            f"(equity ${metrics.equity_usd:,.2f}, peak ${metrics.peak_equity_usd:,.2f})"
        )
        state.trigger_source = "smark_absolute"
        return state

    daily_loss = abs(metrics.daily_return_pct) if metrics.daily_return_pct < 0 else 0.0
    if daily_loss > smark_daily_loss:
        state.triggered = True
        state.reason = (
            f"SMARK_ABSOLUTE: daily_loss={daily_loss:.4f}% > {smark_daily_loss:.2f}% "
            f"(equity_open_today ${metrics.equity_open_today_usd:,.2f}, "
            f"equity_now ${metrics.equity_usd:,.2f})"
        )
        state.trigger_source = "smark_absolute"
        return state

    # --- Issue-body thresholds ---
    min_n = int(kc.get("min_trades_before_kill_check", 100))
    if metrics.n_trades >= min_n:
        if metrics.profit_factor_lifetime < float(kc.get("min_live_profit_factor", 1.0)):
            state.triggered = True
            state.reason = (
                f"PF={metrics.profit_factor_lifetime:.4f} < "
                f"{kc.get('min_live_profit_factor', 1.0)} after "
                f"{metrics.n_trades} trades (>= {min_n})"
            )
            state.trigger_source = "issue_body"
            return state

    bt_dd = abs(metrics.backtest_max_dd_pct)
    dd_mult = float(kc.get("max_drawdown_multiple_vs_backtest", 1.5))
    if bt_dd > 0 and dd_abs > dd_mult * bt_dd:
        state.triggered = True
        state.reason = (
            f"maxDD={dd_abs:.4f}% > {dd_mult}x backtest {bt_dd:.4f}%"
        )
        state.trigger_source = "issue_body"
        return state

    sharpe_floor = float(kc.get("rolling_20d_sharpe_floor", 0.0))
    if metrics.rolling_20d_sharpe < sharpe_floor:
        state.triggered = True
        state.reason = (
            f"rolling_20d_sharpe={metrics.rolling_20d_sharpe:.4f} < {sharpe_floor}"
        )
        state.trigger_source = "issue_body"
        return state

    return state
