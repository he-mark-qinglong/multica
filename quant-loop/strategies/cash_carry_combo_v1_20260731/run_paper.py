#!/usr/bin/env python3
"""Paper-trade adapter — cash_carry_combo_v1 → _shared.paper.runner (round-4 LIVE).

The W5/T9 runner (``_shared/paper/runner.py``) is instrument-agnostic: it
consumes ``(cfg_path, bars_csv, trades_csv, run_dir)`` and walks the equity
curve through the authoritative engine (``_shared/run_backtest.py``). A
delta-neutral carry book has ~zero price exposure by construction, so this
adapter expresses the strategy's own mark-to-market as a *synthetic
instrument*:

  bars    — one row per funding event (8h grid);
            ``close = 1 + combo_equity_bp / 1e4``, where combo_equity_bp is
            the equal-weight mean of per-symbol
            ``strategy.symbol_equity_curve`` outputs (funding income + basis
            change, entry/exit costs already netted). The runner's
            ``cost_bps_rt`` is therefore 0 — charging again would double-count
            the cost model in ``config.json``.
  trades  — one ``long`` / ``size_fraction=1.0`` position per funding cycle
            (entry=ts[i], exit=ts[i+1]): "hold the carry book over this
            funding period", matching the strategy docstring's claim that the
            position is the funding-cycle delta-neutral book.

Kill-switch parameters are the runner defaults ported from the graveyard
harness (``paper_runner.py:63-100``, read-only): PF floor 1.0 after 100
trades, maxDD > 1.5x backtest, rolling-20d-Sharpe < 0.0.
``backtest_max_dd_pct`` is the combo backtest max DD as a *fraction*
(``compute_metrics`` convention), because the runner compares it against
fractional live drawdowns (``_build_day_row`` dd form, cf.
``run_backtest.py:118-120``).

Pure core (``combo_equity_bp`` / ``bars_from_equity`` / ``trades_from_index``
/ ``build_paper_config``) is I/O-free and unit-tested in
``tests/test_run_paper.py``; ``main()`` is the thin CLI shell.

Usage:
    python3 run_paper.py [--run-dir DIR] [--capital USD]

Exit code is the runner's: 0 = clean, 2 = kill-switch latched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

import pandas as pd

STRATEGY_DIR = Path(__file__).resolve().parent
REPO_ROOT = STRATEGY_DIR.parent.parent
for p in (str(REPO_ROOT), str(STRATEGY_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from _shared.paper.runner import run as paper_run  # noqa: E402
from strategy import (  # noqa: E402
    CarryConfig,
    compute_metrics,
    load_symbol_data,
    symbol_equity_curve,
)

STRATEGY_ID = "cash_carry_combo_v1_20260731"
FUNDING_EVENTS_PER_YEAR = 3 * 365  # 8h funding grid

# Runner-default kill criteria (graveyard paper_runner.py:63-100 port).
DEFAULT_KILL_CRITERIA = {
    "min_trades_before_kill_check": 100,
    "min_live_profit_factor": 1.0,
    "max_drawdown_multiple_vs_backtest": 1.5,
    "rolling_20d_sharpe_floor": 0.0,
}


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------

def combo_equity_bp(
    frames: Mapping[str, pd.DataFrame],
    cfg: CarryConfig,
) -> pd.Series:
    """Equal-weight combo carry equity (bp), indexed by funding-event ts.

    ``frames`` maps symbol → the ``load_symbol_data`` frame (columns
    ``ts``, ``fund_bp``, ``basis_bp``). Per-symbol curves come from
    ``strategy.symbol_equity_curve`` with the regime filter applied to
    ``cfg.filter_symbols``; symbols are aligned on an outer ts join + ffill,
    mirroring ``strategy.run_backtest`` but preserving real timestamps
    (the runner needs a DatetimeIndex).
    """
    per_sym = {}
    for sym, df in frames.items():
        eq = symbol_equity_curve(
            df["fund_bp"].reset_index(drop=True),
            df["basis_bp"].reset_index(drop=True),
            leverage=cfg.leverage,
            entry_exit_cost_bp=cfg.entry_cost_bp * 2,  # entry + exit
            use_filter=sym in cfg.filter_symbols,
            filter_window=cfg.filter_window_events,
        )
        # Normalise to whole seconds: the funding parquet carries stray
        # millisecond offsets (e.g. "2023-11-02 00:00:00.001000+00:00") whose
        # mixed string forms break the runner's pd.read_csv(parse_dates=...)
        # on the CSV round-trip. Floor + dedupe keeps one row per event.
        ts = pd.DatetimeIndex(df["ts"]).floor("s")
        s = pd.Series(eq.to_numpy(), index=ts, name=sym)
        per_sym[sym] = s[~s.index.duplicated(keep="last")]
    eq_df = pd.DataFrame(per_sym).sort_index().ffill().dropna()
    return eq_df.mean(axis=1)


def bars_from_equity(equity_bp: pd.Series) -> pd.DataFrame:
    """Synthetic bars: close = 1 + equity_bp/1e4 (equity ratio, starts ≈1)."""
    close = 1.0 + equity_bp.to_numpy(dtype=float) / 10_000.0
    return pd.DataFrame({"ts": equity_bp.index, "close": close})


def trades_from_index(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """One long position per funding cycle: entry=ts[i], exit=ts[i+1].

    Consecutive cycles tile the whole grid; with ``cost_bps_rt=0`` the
    runner's one-position-at-a-time force-close costs nothing, so the paper
    equity reproduces the synthetic close path.
    """
    idx = pd.DatetimeIndex(idx)
    return pd.DataFrame(
        {
            "entry_ts": idx[:-1],
            "exit_ts": idx[1:],
            "direction": "long",
            "size_fraction": 1.0,
        }
    )


def build_paper_config(
    backtest_max_dd_pct: float,
    *,
    starting_capital_usd: float = 100_000.0,
    cost_bps_rt: float = 0.0,
    freq_per_year: int = FUNDING_EVENTS_PER_YEAR,
    kill_criteria: Mapping[str, float] | None = None,
) -> dict:
    """Runner config dict; kill criteria default to the runner defaults.

    ``backtest_max_dd_pct`` is taken as a fraction (negative or positive);
    stored positive — the runner abs()es both sides before comparing.
    """
    return {
        "strategy_id": STRATEGY_ID,
        "timeframe": "8h",
        "starting_capital_usd": float(starting_capital_usd),
        "cost_bps_rt": float(cost_bps_rt),
        "freq_per_year": int(freq_per_year),
        "backtest_expectations": {
            "backtest_max_dd_pct": abs(float(backtest_max_dd_pct)),
        },
        "kill_criteria": dict(kill_criteria or DEFAULT_KILL_CRITERIA),
    }


# ---------------------------------------------------------------------------
# I/O shell
# ---------------------------------------------------------------------------

def load_frames(cfg: CarryConfig, root: Path = REPO_ROOT) -> dict[str, pd.DataFrame]:
    """Load per-symbol funding+spot frames via the strategy's own loader."""
    return {sym: load_symbol_data(sym, cfg, root) for sym in cfg.symbols}


def prepare_inputs(run_dir: Path, cfg: CarryConfig, root: Path = REPO_ROOT) -> dict:
    """Build + write bars.csv / trades.csv / paper_config.json; return paths."""
    frames = load_frames(cfg, root)
    equity_bp = combo_equity_bp(frames, cfg)
    metrics = compute_metrics(
        equity_bp.reset_index(drop=True),
        pd.Series(equity_bp.index).reset_index(drop=True),
    )

    bars = bars_from_equity(equity_bp)
    trades = trades_from_index(equity_bp.index)
    paper_cfg = build_paper_config(metrics["max_drawdown_pct"])

    inputs_dir = Path(run_dir) / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    bars_csv = inputs_dir / "bars.csv"
    trades_csv = inputs_dir / "trades.csv"
    cfg_path = inputs_dir / "paper_config.json"
    bars.to_csv(bars_csv, index=False)
    trades.to_csv(trades_csv, index=False)
    cfg_path.write_text(json.dumps(paper_cfg, indent=2) + "\n")

    return {
        "bars_csv": bars_csv,
        "trades_csv": trades_csv,
        "cfg_path": cfg_path,
        "combo_metrics": metrics,
        "n_events": len(equity_bp),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--run-dir",
        default=str(STRATEGY_DIR / "paper_run"),
        help="runner run_dir (state.json + results-ledger/ live here)",
    )
    ap.add_argument("--capital", type=float, default=100_000.0)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    cfg = CarryConfig.from_json()
    prep = prepare_inputs(run_dir, cfg)

    m = prep["combo_metrics"]
    print(f"combo backtest: ann={m['annualized_return']:+.2%} "
          f"maxDD={m['max_drawdown_pct']:.2%} calmar={m['calmar']:.2f} "
          f"events={prep['n_events']}")
    print(f"inputs written under {run_dir / 'inputs'}")

    # starting_capital override: rebuild config if user passed --capital.
    cfg_path = prep["cfg_path"]
    if args.capital != 100_000.0:
        paper_cfg = json.loads(cfg_path.read_text())
        paper_cfg["starting_capital_usd"] = float(args.capital)
        cfg_path.write_text(json.dumps(paper_cfg, indent=2) + "\n")

    rc = paper_run(cfg_path, prep["bars_csv"], prep["trades_csv"], run_dir)
    state = json.loads((run_dir / "state.json").read_text())
    print(f"runner exit={rc} last_date={state['last_date']} "
          f"killed={state['killed']} reason={state['kill_reason']!r}")
    ledger = run_dir / "results-ledger" / "daily_metrics.csv"
    if ledger.exists():
        n_rows = len(pd.read_csv(ledger))
        print(f"ledger {ledger}: {n_rows} daily rows")
    return rc


if __name__ == "__main__":
    sys.exit(main())
