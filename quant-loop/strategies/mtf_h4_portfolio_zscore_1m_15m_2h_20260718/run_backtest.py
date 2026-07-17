"""Run backtest for mtf_xs_pairs_1m_15m_2h_h4_20260718 (H4 — multi-pair portfolio).

Writes results/metrics.json and results/summary.json via the shared
mtf_xs_runner.write_metrics. Also writes per-pair trade logs
(trades_<pair>.csv) and the portfolio equity curve (equity_portfolio.csv)
plus per-pair equity curves (equity_<pair>.csv).
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
from _indicators.mtf_xs_pairs_base_20260718 import (  # noqa: E402
    aggregate_ohlcv,
    build_h4_signals,
    build_h4_portfolio,
    daily_returns,
)
from _indicators.mtf_xs_runner_20260718 import run_backtest, write_metrics  # noqa: E402


CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _write_trade_csv(trades: list, out_path: Path) -> None:
    if not trades:
        out_path.write_text("pair,direction,entry_ts,exit_ts,z_at_entry,z_at_exit,"
                            "slope15m_at_entry,trend2h_at_entry,pnl_pct,bars_held,"
                            "exit_reason,entry_price_a,entry_price_b,exit_price_a,"
                            "exit_price_b\n")
        return
    cols = ["pair", "direction", "entry_ts", "exit_ts", "z_at_entry",
            "z_at_exit", "slope15m_at_entry", "trend2h_at_entry", "pnl_pct",
            "bars_held", "exit_reason", "entry_price_a", "entry_price_b",
            "exit_price_a", "exit_price_b"]
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in trades:
            row = {c: t.get(c) for c in cols}
            w.writerow(row)


def _write_equity_csv(bar_return: np.ndarray, out_path: Path,
                      index: pd.DatetimeIndex) -> None:
    eq = np.empty(len(bar_return))
    eq[0] = 1.0
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    idx = index[: len(bar_return)]
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["openTime", "bar_return", "equity_indexed"])
        for i, ts in enumerate(idx):
            w.writerow([ts.isoformat(), float(bar_return[i]), float(eq[i])])


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m data for", syms)
    data = load_all(syms)
    for s, df in data.items():
        print(" ", s, len(df), "span", df.index[0], "->", df.index[-1])

    print("Building H4 signals …")
    # Strip tz to match base module expectations
    d_norm = {}
    for s, df in data.items():
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_convert(None)
        d_norm[s] = df
    signals = build_h4_signals(d_norm, cfg)
    print(" pairs:", list(signals.keys()))

    print("Running backtest …")
    res = run_backtest(d_norm, cfg, funding=None)
    port = res["portfolio"]
    print(f"portfolio n_bars={port['n_bars']}, n_pairs={len(res['per_pair'])}")
    for pp in res["per_pair"]:
        print(f"  {pp['pair']}: n_trades={len(pp['trades'])}, "
              f"span={pp['span_start']} -> {pp['span_end']}")

    # Per-pair trade logs and equity curves
    first_a_index = next(iter(signals.values()))["a"].index
    for pp in res["per_pair"]:
        slug = pp["pair"].replace("/", "_")
        _write_trade_csv(pp["trades"], RESULTS_DIR / f"trades_{slug}.csv")
        _write_equity_csv(pp["bar_return"], RESULTS_DIR / f"equity_{slug}.csv",
                          first_a_index)
    # Portfolio equity
    if port["n_bars"] > 0:
        _write_equity_csv(port["bar_return"], RESULTS_DIR / "equity_portfolio.csv",
                          first_a_index[: port["n_bars"]])
    # Save portfolio sizing diagnostics
    if port.get("sizing"):
        (RESULTS_DIR / "portfolio_sizing.json").write_text(
            json.dumps(port["sizing"], indent=2)
        )
        print("Portfolio sizing:", port["sizing"])

    payload = write_metrics(res, cfg, RESULTS_DIR)
    print("=== " + cfg["strategy"] + " (" + cfg.get("hypothesis", "?") + ") ===")
    print("tag                  :", "[" + payload["tag"] + "]")
    print("avg pair sharpe(d/r) :", f"{payload['avg_pair_sharpe_daily_resampled']:.3f}")
    print("avg pair ann.ret(d)  :", f"{payload['avg_pair_annualized_return_daily']:.4f}")
    print("avg pair max DD      :", f"{payload['avg_pair_max_drawdown_pct']:.4f}")
    print("avg pair profit_f    :", f"{payload['profit_factor_avg']:.3f}")
    print("n_trades_total       :", payload["n_trades_total"])
    print("portf sharpe(d/r)    :",
          f"{payload['portfolio']['sharpe_daily_resampled']:.3f}")
    print("portf ann.ret(d)     :",
          f"{payload['portfolio']['annualized_return_daily']:.4f}")
    print("sharpe_method        :", payload["sharpe_method"])
    print("metrics.json         :", str(RESULTS_DIR / "metrics.json"))


if __name__ == "__main__":
    main()