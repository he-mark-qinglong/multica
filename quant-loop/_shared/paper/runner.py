"""Paper-trading runner skeleton — config-driven, idempotent, kill-aware.

W5/T9 (round-2 sprint). Spec source: ``docs/plans/infra-sprint-2026-07-25/
round2/w5-s3-paper-harness.md``.

Purpose
-------
Offline batch runner that replaces the live `paper_runner.py` shadow loop in
the graveyard. Reads bars + trades CSVs, walks the equity curve through the
authoritative in-house engine (`_shared.run_backtest`), and writes one row per
UTC date into ``run_dir/results-ledger/daily_metrics.csv`` via the W5/T8
`ledger_writer` (atomic + day-level dedup).

Contract
--------
- **Idempotent** — `state.json` records `last_date`; resumed runs skip
  already-processed dates. Re-running a finished run does NOT duplicate rows.
- **Kill-aware** — three hard rules from the graveyard harness
  (`paper_runner.py:63-100`) are ported in-line (no import — graveyard is
  read-only). Once triggered, kill latches and the runner returns exit code 2.
- **Path-disciplined** — `QUANT_LOOP_ROOT` is derived from `__file__` only.
  No hardcoded absolute paths anywhere (the graveyard `paper_runner.py:25`
  hardcoding ``/home/smark/multica/quant-loop`` is the anti-example).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

# Path discipline: locate the quant-loop repo via __file__, not hardcoded.
QUANT_LOOP_ROOT = Path(__file__).resolve().parents[2]
if str(QUANT_LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(QUANT_LOOP_ROOT))

from _shared.run_backtest import Trade, run_backtest  # noqa: E402
from _shared.paper.ledger_writer import DAILY_FIELDS, append_daily_row  # noqa: E402


# --- Required config keys (dotted notation) ---------------------------------
# Kept in one place so both load_config and the JSON schema agree.
REQUIRED_CONFIG_KEYS: List[str] = [
    "strategy_id",
    "timeframe",
    "starting_capital_usd",
    "cost_bps_rt",
    "freq_per_year",
    "backtest_expectations.backtest_max_dd_pct",
    "kill_criteria.min_trades_before_kill_check",
    "kill_criteria.min_live_profit_factor",
    "kill_criteria.max_drawdown_multiple_vs_backtest",
    "kill_criteria.rolling_20d_sharpe_floor",
]


class ConfigError(KeyError):
    """Raised when a required config key is missing or out of range.

    Subclasses KeyError so existing test assertions that catch ``KeyError``
    still work — but gives a recognisable type for new code.
    """


# --- Config ------------------------------------------------------------------
def load_config(path: Path) -> dict:
    """Read JSON config; raise ConfigError with the missing key name(s).

    The error message includes the dotted key path so tests / operators can
    see exactly which config field is wrong without grep.
    """
    cfg = json.loads(Path(path).read_text())
    if not isinstance(cfg, dict):
        raise ConfigError(f"config root must be an object, got {type(cfg).__name__}")

    missing: List[str] = []
    for dotted in REQUIRED_CONFIG_KEYS:
        section, _, key = dotted.partition(".")
        if not key:
            # Top-level key (no dotted section).
            if section not in cfg:
                missing.append(dotted)
            continue
        if section not in cfg:
            missing.append(dotted)
            continue
        section_val = cfg[section]
        if not isinstance(section_val, dict):
            # Wrong shape entirely — surface as missing rather than TypeError.
            missing.append(dotted)
            continue
        if key not in section_val:
            missing.append(dotted)
    if missing:
        # KeyError-compatible for test_missing_config_key_raises; message
        # includes the missing key name.
        raise ConfigError(
            f"missing required config key(s): {', '.join(missing)}"
        )

    # Cheap type / range checks (schema validates too; this is the runtime
    # guard so a misconfigured run fails loudly before any I/O happens).
    sc = cfg["starting_capital_usd"]
    if not isinstance(sc, (int, float)) or sc <= 0:
        raise ConfigError(f"starting_capital_usd must be a positive number, got {sc!r}")
    cb = cfg["cost_bps_rt"]
    if not isinstance(cb, (int, float)) or cb < 0:
        raise ConfigError(f"cost_bps_rt must be >= 0, got {cb!r}")
    fp = cfg["freq_per_year"]
    if not isinstance(fp, int) or fp <= 0:
        raise ConfigError(f"freq_per_year must be a positive int, got {fp!r}")

    return cfg


# --- State (idempotent resume) -----------------------------------------------
def load_state(run_dir: Path) -> Dict[str, Any]:
    """Read ``state.json``; return defaults if absent.

    Default state: nothing processed yet, kill not triggered.
    """
    state_path = Path(run_dir) / "state.json"
    if not state_path.exists():
        return {"last_date": None, "killed": False, "kill_reason": ""}
    state = json.loads(state_path.read_text())
    # Defensive defaults for older / partial state files.
    state.setdefault("last_date", None)
    state.setdefault("killed", False)
    state.setdefault("kill_reason", "")
    return state


def save_state(run_dir: Path, state: Dict[str, Any]) -> None:
    """Atomic write of state.json via tmp-file + os.replace.

    Same crash-safety discipline as ``ledger_writer.append_daily_row``.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    fd, tmp = tempfile.mkstemp(prefix="state.", suffix=".json", dir=str(run_dir))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, state_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


# --- Kill evaluation ---------------------------------------------------------
def evaluate_kill(day_row: Dict[str, Any], cfg: dict, state: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the three hard kill rules. Latches: already-killed state is sticky.

    Rules ported from ``paper_runner.py:63-100`` (graveyard). Inline port —
    the graveyard module is read-only / archived and we don't import from it.

    Side-effects on ``day_row``: on the rule that triggers, sets
    ``kill_triggered=True``, ``kill_reason=<reason>``, ``action="HALT"``.

    Returns a (possibly updated) state dict — never mutates ``state`` in place.
    """
    if state.get("killed", False):
        return state

    kc = cfg["kill_criteria"]
    n = int(day_row.get("total_trades", 0) or 0)
    pf = float(day_row.get("profit_factor_lifetime", 0.0) or 0.0)
    dd_pct = abs(float(day_row.get("max_drawdown_pct", 0.0) or 0.0))
    bt_dd_pct = abs(float(
        cfg["backtest_expectations"].get("backtest_max_dd_pct", dd_pct) or 0.0
    ))
    rolling_sharpe = float(day_row.get("rolling_20d_sharpe", 0.0) or 0.0)

    new_state = dict(state)

    # Rule 1: PF floor (only meaningful after a warm-up of N trades).
    if n >= kc["min_trades_before_kill_check"] and pf < kc["min_live_profit_factor"]:
        new_state["killed"] = True
        new_state["kill_reason"] = (
            f"PF={pf:.4f} < {kc['min_live_profit_factor']} "
            f"after {n} trades (>= {kc['min_trades_before_kill_check']})"
        )
        day_row["kill_triggered"] = True
        day_row["kill_reason"] = new_state["kill_reason"]
        day_row["action"] = "HALT"
        return new_state

    # Rule 2: maxDD exceeded vs backtest anchor.
    if bt_dd_pct > 0 and dd_pct > kc["max_drawdown_multiple_vs_backtest"] * bt_dd_pct:
        new_state["killed"] = True
        new_state["kill_reason"] = (
            f"maxDD={dd_pct:.4f} > "
            f"{kc['max_drawdown_multiple_vs_backtest']}x backtest {bt_dd_pct:.4f}"
        )
        day_row["kill_triggered"] = True
        day_row["kill_reason"] = new_state["kill_reason"]
        day_row["action"] = "HALT"
        return new_state

    # Rule 3: rolling 20d Sharpe floor.
    if rolling_sharpe < kc["rolling_20d_sharpe_floor"]:
        new_state["killed"] = True
        new_state["kill_reason"] = (
            f"rolling_20d_sharpe={rolling_sharpe:.4f} < "
            f"{kc['rolling_20d_sharpe_floor']}"
        )
        day_row["kill_triggered"] = True
        day_row["kill_reason"] = new_state["kill_reason"]
        day_row["action"] = "HALT"
        return new_state

    return new_state


# --- Per-trade pnl attribution ----------------------------------------------
def _per_trade_pnl(equity: pd.Series, trades_df: pd.DataFrame) -> List[float]:
    """Per-trade equity change (exit equity - entry equity).

    For non-overlapping schedules this is exact; with the run_backtest
    one-position-at-a-time force-close, overlapping trade windows still see
    the cumulated equity delta. The runner uses this only for win/loss
    classification (sign), not for sizing — so the coarse proxy is sufficient.
    """
    pnls: List[float] = []
    idx = equity.index
    for _, row in trades_df.iterrows():
        try:
            entry_ts = pd.Timestamp(row["entry_ts"])
            exit_ts = pd.Timestamp(row["exit_ts"])
            entry_eq = float(equity.loc[entry_ts])
            exit_eq = float(equity.loc[exit_ts])
            pnls.append(exit_eq - entry_eq)
        except KeyError:
            pnls.append(0.0)
    return pnls


# --- Main run ----------------------------------------------------------------
def _build_day_row(
    date_str: str,
    day_group: pd.DataFrame,
    equity: pd.Series,
    cum_max_running: pd.Series,
    initial_capital: float,
    trades_df: pd.DataFrame,
    per_trade_pnls: List[float],
) -> Dict[str, Any]:
    """Compose one daily_metrics row aligned to DAILY_FIELDS."""
    day_close_equity = float(day_group["equity"].iloc[-1])
    day_open_equity = float(day_group["equity"].iloc[0])
    daily_return_pct = (
        (day_close_equity / day_open_equity - 1.0) * 100.0
        if day_open_equity > 0 else 0.0
    )
    net_pnl_usd = day_close_equity - day_open_equity
    # DD up to and including end of this date — same form as run_backtest.py:118-120
    dd_today = float(((day_group["equity"] - cum_max_running.loc[day_group.index])
                      / cum_max_running.loc[day_group.index]).min())

    # Trades exiting today:
    exit_dates = pd.to_datetime(trades_df["exit_ts"]).dt.date
    day_idx = pd.Timestamp(date_str).date()
    day_mask = exit_dates == day_idx
    n_today = int(day_mask.sum())
    day_pnls = [p for p, m in zip(per_trade_pnls, day_mask) if m]
    wins = sum(1 for p in day_pnls if p > 0)
    losses = sum(1 for p in day_pnls if p < 0)
    win_rate = (wins / n_today) if n_today > 0 else 0.0
    gross_pnl_usd = sum(p for p in day_pnls if p > 0)

    row: Dict[str, Any] = {f: 0 for f in DAILY_FIELDS}
    row["date"] = date_str
    row["total_trades"] = n_today
    row["winning_trades"] = wins
    row["losing_trades"] = losses
    row["win_rate"] = win_rate
    row["gross_pnl_usd"] = gross_pnl_usd
    row["net_pnl_usd"] = net_pnl_usd
    row["fees_usd"] = 0.0
    row["slippage_usd"] = 0.0
    row["equity_usd"] = day_close_equity
    row["daily_return_pct"] = daily_return_pct
    row["rolling_20d_sharpe"] = 0.0
    row["rolling_20d_pf"] = 0.0
    row["max_drawdown_pct"] = dd_today
    row["max_drawdown_pct_vs_backtest"] = 0.0
    row["profit_factor_lifetime"] = 0.0  # filled by run() over lifetime
    row["bootstrap_ci_lo"] = 0.0
    row["action"] = "RUN"
    row["kill_triggered"] = False
    row["kill_reason"] = ""
    row["notes"] = ""
    _ = initial_capital  # noqa: F841 — reserved for future net_pnl convention
    return row


def run(cfg_path: Path, bars_csv: Path, trades_csv: Path, run_dir: Path) -> int:
    """Drive one offline paper run. Returns 0 (clean) or 2 (kill triggered).

    Algorithm
    ---------
    1. Load config + state.
    2. Read bars (ts-indexed) + trades (entry_ts/exit_ts/direction/size_fraction).
    3. Call run_backtest once to get the full equity curve.
    4. Group equity by UTC date; for each date not yet in state:
       - build daily row, evaluate kill rules, append via T8 writer,
         advance state.
    5. Return 2 if a kill fires this run; else 0.
    """
    cfg = load_config(cfg_path)
    state = load_state(run_dir)
    run_dir = Path(run_dir)
    ledger_dir = run_dir / "results-ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)

    if state.get("killed", False):
        # Resumed onto an already-killed run: nothing to do, return 2.
        return 2

    # ---- Read inputs ----
    bars = pd.read_csv(Path(bars_csv), parse_dates=["ts"])
    if "ts" not in bars.columns:
        raise ConfigError(f"bars_csv missing 'ts' column: {bars_csv}")
    bars = bars.set_index("ts").sort_index()
    if "close" not in bars.columns:
        raise ConfigError(f"bars_csv missing 'close' column: {bars_csv}")

    trades_df = pd.read_csv(Path(trades_csv))
    for col in ("entry_ts", "exit_ts", "direction", "size_fraction"):
        if col not in trades_df.columns:
            raise ConfigError(f"trades_csv missing '{col}' column: {trades_csv}")

    trades: List[Trade] = [
        Trade(
            entry_ts=pd.Timestamp(r["entry_ts"]),
            exit_ts=pd.Timestamp(r["exit_ts"]),
            direction=str(r["direction"]),
            size_fraction=float(r.get("size_fraction", 1.0)),
        )
        for _, r in trades_df.iterrows()
    ]

    # ---- Equity walk (one-shot) ----
    result = run_backtest(
        bars,
        trades,
        initial_capital=cfg["starting_capital_usd"],
        cost_bps_rt=cfg["cost_bps_rt"],
        freq_per_year=cfg["freq_per_year"],
    )
    equity: pd.Series = result["equity"]

    if len(equity) == 0:
        save_state(run_dir, state)
        return 0

    # Lifetime PF (gross winning / |gross losing|) — constant per run.
    per_trade_pnls = _per_trade_pnl(equity, trades_df)
    lifetime_gross_win = sum(p for p in per_trade_pnls if p > 0)
    lifetime_gross_loss = abs(sum(p for p in per_trade_pnls if p < 0))
    lifetime_pf = (
        lifetime_gross_win / lifetime_gross_loss
        if lifetime_gross_loss > 1e-12 else 0.0
    )

    cum_max_running = equity.cummax()
    equity_by_date = equity.groupby(equity.index.date)

    last_date = state.get("last_date")
    exit_code = 0
    for date_val, day_series in equity_by_date:
        date_str = str(date_val)
        if last_date is not None and date_str <= last_date:
            continue  # idempotent skip — already processed
        day_df = pd.DataFrame({"equity": day_series})
        day_row = _build_day_row(
            date_str, day_df, equity, cum_max_running,
            cfg["starting_capital_usd"], trades_df, per_trade_pnls,
        )
        day_row["profit_factor_lifetime"] = lifetime_pf
        new_state = evaluate_kill(day_row, cfg, state)
        append_daily_row(ledger_dir, day_row)
        state = {
            "last_date": date_str,
            "killed": new_state.get("killed", False),
            "kill_reason": new_state.get("kill_reason", ""),
        }
        save_state(run_dir, state)
        if state["killed"]:
            exit_code = 2
            break  # halt immediately — no further dates processed

    return exit_code


__all__ = [
    "ConfigError",
    "DAILY_FIELDS",
    "QUANT_LOOP_ROOT",
    "REQUIRED_CONFIG_KEYS",
    "evaluate_kill",
    "load_config",
    "load_state",
    "run",
    "save_state",
]