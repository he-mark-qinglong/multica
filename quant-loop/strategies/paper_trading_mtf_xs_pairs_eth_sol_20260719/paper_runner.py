"""Paper-trading harness for the mtf_xs_pairs ETH/SOL leg.

Phase 1: forward shadow execution. Reads live Binance USD-M market data,
computes strategy signals using the target strategy's run_backtest core,
applies cost from `_shared.execution.cost_model`, and logs metrics to the
results-ledger. NO real orders are sent. NO real capital is at risk.

Spec anchor: SPEC_live_paper_connector_binance_usdm.md (smark directive
2026-07-18: testnet only, real-money connector out of scope).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

QUANT_LOOP_ROOT = Path("/home/smark/multica/quant-loop")
sys.path.insert(0, str(QUANT_LOOP_ROOT))

from _shared.execution.cost_model import BINANCE_FUTURES, apply_cost  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"
DEFAULT_LEDGER = Path(__file__).resolve().parent / "results-ledger"


@dataclass
class KillState:
    triggered: bool = False
    reason: str = ""


def _load_config(path: Path) -> dict:
    return json.loads(path.read_text())


def _apply_cost(notional_usd: float, adv_usd: float, side: str = "taker") -> float:
    """Apply the cost-model round-trip cost. NO hardcoded fees."""
    return apply_cost(notional_usd=notional_usd, adv_usd=adv_usd,
                      venue=BINANCE_FUTURES, side=side, impact_factor=0.05)


def _load_live_bars(symbol: str, tf: str, perp_dir: Path) -> pd.DataFrame:
    """Load the canonical perp_30m parquet for the symbol."""
    p = perp_dir / f"{symbol}_30m.parquet"
    if not p.exists():
        raise FileNotFoundError(f"missing canonical bars: {p}")
    df = pd.read_parquet(p)
    if "open_time" in df.columns:
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values("ts").reset_index(drop=True)


def _evaluate_kill_criteria(metrics: dict, cfg: dict, state: KillState) -> KillState:
    """Apply hard kill rules from the issue guardrails.

    Triggers ANY of:
      - live PF < 1.0 after >= min_trades_before_kill_check trades
      - maxDD exceeds 1.5x backtest maxDD
      - rolling 20d Sharpe < 0
    """
    if state.triggered:
        return state

    kc = cfg["kill_criteria"]
    min_n = kc["min_trades_before_kill_check"]
    n = metrics.get("n_trades", 0)
    pf = metrics.get("profit_factor_lifetime", 0.0)
    dd_pct = abs(metrics.get("max_drawdown_pct", 0.0))
    bt_dd_pct = abs(cfg["backtest_expectations"].get("backtest_max_dd_pct", dd_pct))
    rolling_sharpe = metrics.get("rolling_20d_sharpe", 0.0)

    if n >= min_n and pf < kc["min_live_profit_factor"]:
        state.triggered = True
        state.reason = (f"PF={pf:.4f} < {kc['min_live_profit_factor']} "
                        f"after {n} trades (>= {min_n})")
        return state

    if bt_dd_pct > 0 and dd_pct > kc["max_drawdown_multiple_vs_backtest"] * bt_dd_pct:
        state.triggered = True
        state.reason = (f"maxDD={dd_pct:.4f} > "
                        f"{kc['max_drawdown_multiple_vs_backtest']}x backtest {bt_dd_pct:.4f}")
        return state

    if rolling_sharpe < kc["rolling_20d_sharpe_floor"]:
        state.triggered = True
        state.reason = (f"rolling_20d_sharpe={rolling_sharpe:.4f} < "
                        f"{kc['rolling_20d_sharpe_floor']}")
        return state

    return state


def _append_daily_metrics(ledger_dir: Path, row: dict) -> None:
    p = ledger_dir / "daily_metrics.csv"
    write_header = not p.exists() or p.stat().st_size == 0
    with p.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)


def _init_ledger_headers(ledger_dir: Path) -> None:
    """Ensure ledger CSV files have headers even before first append."""
    daily = ledger_dir / "daily_metrics.csv"
    if not daily.exists() or daily.stat().st_size == 0:
        daily.write_text(
            "date,total_trades,winning_trades,losing_trades,win_rate,"
            "gross_pnl_usd,net_pnl_usd,fees_usd,slippage_usd,equity_usd,"
            "daily_return_pct,rolling_20d_sharpe,rolling_20d_pf,"
            "max_drawdown_pct,max_drawdown_pct_vs_backtest,"
            "profit_factor_lifetime,bootstrap_ci_lo,action,"
            "kill_triggered,kill_reason,notes\n"
        )
    eq = ledger_dir / "equity_curve.csv"
    if not eq.exists() or eq.stat().st_size == 0:
        eq.write_text(
            "ts,equity_usd,equity_return_pct,position_state,signal_state,notes\n"
        )


def _print_scaffold(cfg: dict) -> None:
    """Print a scaffold summary so the operator can see what is wired up."""
    bt = cfg["backtest_expectations"]
    kc = cfg["kill_criteria"]
    print("=" * 72)
    print("PAPER-TRADING SCAFFOLD — mtf_xs_pairs ETH/SOL leg (phase 1)")
    print(f"  issue           : {cfg['issue_identifier']} ({cfg['issue_id']})")
    print(f"  signoff         : {cfg['signoff_issue']}")
    print(f"  strategy        : {cfg['strategy_target']} (iter#{cfg['strategy_iter']})")
    print(f"  pair / tf       : {cfg['pair']} @ {cfg['timeframe']} ({cfg['exchange']})")
    print(f"  venue           : {cfg['venue']}  (live: {cfg['live_endpoint']})")
    print(f"  cost model      : {cfg['cost_model']['module']}")
    print(f"  capital (paper) : ${cfg['starting_capital_usd']:,.2f}")
    print(f"  real capital    : {cfg['real_capital']}")
    print("-" * 72)
    print("BACKTEST EXPECTATIONS (anchor for live comparison):")
    print(f"  OOS Sharpe      : {bt['oos_sharpe']}")
    print(f"  ann return      : {bt['ann_total_return_pct']}%")
    print(f"  profit factor   : {bt['profit_factor']}")
    print(f"  bootstrap CI lo : {bt['bootstrap_ci_lo']}")
    print(f"  trades (OOS)    : {bt['n_trades_oos']}")
    print(f"  G5 CV passed    : {bt['g5_cv_passed']}")
    print("-" * 72)
    print("KILL CRITERIA (auto-halt on ANY):")
    print(f"  live PF < {kc['min_live_profit_factor']} after >={kc['min_trades_before_kill_check']} trades")
    print(f"  maxDD > {kc['max_drawdown_multiple_vs_backtest']}x backtest maxDD")
    print(f"  rolling 20d Sharpe < {kc['rolling_20d_sharpe_floor']}")
    print(f"  known weak point: {kc['known_weak_point']}")
    print("-" * 72)
    print("REVIEW CADENCE:")
    rc = cfg["review_cadence"]
    print(f"  daily metrics   : {rc['daily_metrics_log']}")
    print(f"  weekly review   : comment on issue with {rc['weekly_review_fields']}")
    pt = cfg["phase_transitions"]["phase_2_minimal_real_capital"]
    print(f"  phase 2 unlock  : >={pt['min_paper_weeks']} weeks paper AND smark approval")
    print("=" * 72)


def cmd_init(args) -> int:
    cfg = _load_config(args.config)
    _init_ledger_headers(DEFAULT_LEDGER)
    _print_scaffold(cfg)
    print("[init] ledger headers seeded at results-ledger/")
    print("[init] next: `python3 paper_runner.py scaffold` to verify shape")
    return 0


def cmd_scaffold(args) -> int:
    cfg = _load_config(args.config)
    _init_ledger_headers(DEFAULT_LEDGER)
    print(json.dumps(cfg, indent=2))
    return 0


def cmd_kill_check(args) -> int:
    """Re-run the kill-criteria evaluator on the current daily_metrics ledger."""
    cfg = _load_config(args.config)
    daily = DEFAULT_LEDGER / "daily_metrics.csv"
    if not daily.exists():
        print("[kill-check] no daily_metrics.csv yet; nothing to evaluate.")
        return 0
    df = pd.read_csv(daily)
    if df.empty:
        print("[kill-check] daily_metrics.csv is empty; nothing to evaluate.")
        return 0
    last = df.iloc[-1].to_dict()
    state = KillState()
    state = _evaluate_kill_criteria(last, cfg, state)
    if state.triggered:
        print(f"[kill-check] TRIGGERED: {state.reason}")
        return 2
    print("[kill-check] all green; no kill condition met.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="initialise ledger + print scaffold")
    sub.add_parser("scaffold", help="print the loaded scaffold config")
    sub.add_parser("kill-check", help="evaluate kill criteria on current ledger")
    args = parser.parse_args()
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "kill-check":
        return cmd_kill_check(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())