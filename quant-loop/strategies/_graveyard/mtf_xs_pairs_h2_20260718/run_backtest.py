"""Run backtest for mtf_xs_pairs_1m_15m_2h_h2_20260718 (H2 — VPVR edge)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from data_loader import load_all, load_funding  # noqa: E402
from _indicators.mtf_xs_runner_20260718 import run_backtest, write_metrics  # noqa: E402


CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
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
    print("=== " + cfg["strategy"] + " (" + cfg.get("hypothesis", "?") + ") ===")
    print("tag                  :", "[" + payload["tag"] + "]")
    print("avg pair sharpe(d/r) :", f"{payload['avg_pair_sharpe_daily_resampled']:.3f}")
    print("avg pair ann.ret(d) :", f"{payload['avg_pair_annualized_return_daily']:.4f}")
    print("avg pair max DD      :", f"{payload['avg_pair_max_drawdown_pct']:.4f}")
    print("avg pair profit_f    :", f"{payload['profit_factor_avg']:.3f}")
    print("n_trades_total       :", payload["n_trades_total"])
    print("sharpe_method        :", payload["sharpe_method"])
    print("metrics.json         :", str(RESULTS_DIR / "metrics.json"))


if __name__ == "__main__":
    main()