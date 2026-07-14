"""Catalog entry point for `pairs_cointegration_1d_20260709`.

This module is the canonical CLI surface invoked by:

    cd /home/smark/multica/quant-loop/strategies/pairs_cointegration_1d_20260709
    python3 -m backtest

It is deliberately thin: all real work lives in `run_backtest.py` and the
strategy / portfolio modules. This module's job is:

  1. Load the strategy `config.json`.
  2. Print a one-line summary so a cron / log scraper can verify the run
     finished without parsing CSV outputs.
  3. Hand off to `run_backtest.run_multi_pair_backtest` (the real driver).
  4. Print the resulting portfolio summary in a human-readable form and
     raise a `SystemExit` with the exit code documented in `run_backtest.main`.

Why this exists separately from `run_backtest.py`:

  - Sister strategies in the catalog (`vpvr_reversion_1d_20260621`,
    `donchian_breakout_atr_1d_20260709`, etc.) all expose a `backtest.py`
    module so the catalog import surface (`from backtest import main`) is
    uniform. We keep the convention so B3 / B5 don't have to special-case
    this strategy.
  - `run_backtest.py` is the workhorse driver. It can be invoked directly
    (`python3 -m run_backtest`) for richer debugging output.

Security baseline:
  - Refuses to run with `LIVE_TRADING=1` (paper trade only).
  - Does not write to disk outside `results/`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

# We import the heavy driver lazily so `from backtest import load_config`
# (used by some notebook flows) doesn't pull pandas/statsmodels.
def load_config() -> dict:
    """Load the strategy config from disk."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _print_summary(cfg: dict, result) -> None:
    """Print a human-readable summary of the multi-pair run."""
    print(f"[backtest] {cfg['strategy']}  timeframe={cfg['timeframe']}")
    print(f"[backtest] universe: {', '.join(result.universe)}")
    print(f"[backtest] selected pairs: {result.n_active_pairs} / {len(result.pair_selection)} candidates")
    for k, r in result.pair_results.items():
        print(
            f"[backtest]   {k:>22s}  trades={r.n_trades:>3d}  "
            f"win_rate={100*r.win_rate:.1f}%  pnl_pct={100*r.total_pnl_pct:+.3f}%"
        )
    print(
        f"[backtest] portfolio pnl: {result.portfolio_total_pnl_usd:+.2f} USD  "
        f"({100*result.portfolio_total_pnl_pct:+.3f}%)  "
        f"trades={result.n_total_trades}"
    )
    if result.n_pair_pauses or result.n_portfolio_pauses:
        print(
            f"[backtest] state machine: pair_pauses={result.n_pair_pauses}  "
            f"portfolio_pauses={result.n_portfolio_pauses}"
        )


def main() -> int:
    """Catalog entry point: load config, run, print, return exit code."""
    cfg = load_config()
    # Import lazily so the module-level surface stays cheap.
    import run_backtest as rb
    result = rb.run_multi_pair_backtest(cfg)
    rb.persist_results(result)
    _print_summary(cfg, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())