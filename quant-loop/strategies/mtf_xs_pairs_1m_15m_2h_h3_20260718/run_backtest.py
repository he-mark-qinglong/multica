"""Run backtest for mtf_xs_pairs_1m_15m_2h_h3_20260718 (H3 — funding regime).

Outputs (per SMA-34878 deliverable):
  results/summary.json
  results/metrics.json
  results/trades_*.csv        (one CSV per pair, plus a combined trades_all.csv)
  results/equity_*.csv        (one per pair, plus an equity_portfolio.csv)
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from data_loader import load_all, load_funding  # noqa: E402
from _indicators.mtf_xs_runner_20260718 import run_backtest, write_metrics  # noqa: E402


CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Stable column order for trades_*.csv
TRADE_COLUMNS = [
    "pair", "direction", "entry_ts", "exit_ts",
    "entry_price_a", "entry_price_b",
    "exit_price_a", "exit_price_b",
    "pnl_pct", "bars_held",
    "z_at_entry", "z_at_exit",
    "slope15m_at_entry", "trend2h_at_entry",
    "exit_reason",
]


def _write_trades_csvs(per_pair: list, results_dir: Path) -> list:
    """Write one trades CSV per pair, plus a combined trades_all.csv."""
    out_paths = []
    for pp in per_pair:
        pair = pp["pair"]
        path = results_dir / f"trades_{pair.replace('/', '_')}.csv"
        rows = pp.get("trades", [])
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in TRADE_COLUMNS})
        out_paths.append(path)
    # Combined
    all_rows = []
    for pp in per_pair:
        all_rows.extend(pp.get("trades", []))
    combined_path = results_dir / "trades_all.csv"
    with combined_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
        w.writeheader()
        for row in all_rows:
            w.writerow({k: row.get(k, "") for k in TRADE_COLUMNS})
    out_paths.append(combined_path)
    return out_paths


def _write_equity_csvs(per_pair: list, portfolio: dict, results_dir: Path,
                       starting_capital: float) -> list:
    """Write one equity CSV per pair and one equity_portfolio.csv.

    Equity is reconstructed from bar_return at 1m resolution; the index is
    a synthetic 1m-spaced DatetimeIndex because the run_backtest result
    doesn't carry the original timestamps (the metrics JSON uses a dummy
    index for the same reason). The values themselves are accurate.
    """
    out_paths = []
    for pp in per_pair:
        pair = pp["pair"]
        path = results_dir / f"equity_{pair.replace('/', '_')}.csv"
        n = pp["n_bars"]
        bar_return = pp["bar_return"]
        eq = np.empty(n)
        eq[0] = starting_capital
        for i in range(1, n):
            eq[i] = eq[i - 1] * (1.0 + bar_return[i])
        idx = pd.date_range("2022-01-01", periods=n, freq="1min")
        pd.DataFrame({"timestamp": idx, "equity": eq}).to_csv(path, index=False)
        out_paths.append(path)
    # Portfolio
    path = results_dir / "equity_portfolio.csv"
    n = portfolio["n_bars"]
    bar_return = portfolio["bar_return"]
    eq = np.empty(n)
    eq[0] = starting_capital
    for i in range(1, n):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    idx = pd.date_range("2022-01-01", periods=n, freq="1min")
    pd.DataFrame({"timestamp": idx, "equity": eq}).to_csv(path, index=False)
    out_paths.append(path)
    return out_paths


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    starting = float(cfg.get("starting_capital_usd", 100000.0))
    print("Loading 1m data for", syms)
    data = load_all(syms)
    funding = load_funding(syms) if cfg.get("hypothesis") == "H3" else None
    for s, df in data.items():
        print(" ", s, len(df), "span", df.index[0], "->", df.index[-1])
    print("Running backtest …")
    res = run_backtest(data, cfg, funding=funding)
    port = res["portfolio"]
    print(f"portfolio n_bars={port['n_bars']}, n_pairs={len(res['per_pair'])}")
    for pp in res["per_pair"]:
        print(f"  {pp['pair']}: n_trades={len(pp['trades'])}")
    payload = write_metrics(res, cfg, RESULTS_DIR)
    trades_paths = _write_trades_csvs(res["per_pair"], RESULTS_DIR)
    equity_paths = _write_equity_csvs(res["per_pair"], port, RESULTS_DIR, starting)
    print("=== " + cfg["strategy"] + " (" + cfg.get("hypothesis", "?") + ") ===")
    print("tag                  :", "[" + payload["tag"] + "]")
    print("avg pair sharpe(d/r) :", f"{payload['avg_pair_sharpe_daily_resampled']:.3f}")
    print("avg pair ann.ret(d) :", f"{payload['avg_pair_annualized_return_daily']:.4f}")
    print("avg pair max DD      :", f"{payload['avg_pair_max_drawdown_pct']:.4f}")
    print("avg pair profit_f    :", f"{payload['profit_factor_avg']:.3f}")
    print("n_trades_total       :", payload["n_trades_total"])
    print("sharpe_method        :", payload["sharpe_method"])
    print("metrics.json         :", str(RESULTS_DIR / "metrics.json"))
    print("trades CSVs          :", len(trades_paths))
    for p in trades_paths:
        print("  -", p.name)
    print("equity CSVs          :", len(equity_paths))
    for p in equity_paths:
        print("  -", p.name)


if __name__ == "__main__":
    main()
