"""Targeted 2024-only verification of H3 signal enhancements.

Reuses the signal builder and backtest loop from run_experiments.py but
runs only one calendar year, so it finishes quickly and gives directional
numbers without rerunning the full-history sweep.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_experiments import (  # noqa: E402
    backtest_variant,
    build_portfolio,
    enhance_signals,
    load_config,
    metrics_from_result,
)
import data_loader_patch as dlp  # noqa: E402


def main():
    cfg = load_config()
    d1m_raw = dlp.load_all()
    funding_raw = dlp.load_funding()
    d1m, funding = dlp.slice_by_date(d1m_raw, funding_raw, start="2024-01-01", end="2024-12-31")
    print("2024 bars BTC:", len(d1m["BTCUSDT"]), "SOL:", len(d1m["SOLUSDT"]))

    signals = enhance_signals(d1m, cfg, funding)

    variants = [
        {"name": "baseline", "params": {}},
        {"name": "slope_fav_4", "params": {"slope_filter": {"lookback": 4, "sign": "favorable"}}},
        {"name": "slope_adv_4", "params": {"slope_filter": {"lookback": 4, "sign": "adverse"}}},
        {"name": "adverse_stop_0_7", "params": {"adverse_stop_z": 0.7, "regime_break": 9.0}},
        {"name": "slope_fav_4_stop_0_7", "params": {"slope_filter": {"lookback": 4, "sign": "favorable"}, "adverse_stop_z": 0.7, "regime_break": 9.0}},
        {"name": "candle_confirm", "params": {"candle_confirm": True}},
        {"name": "funding_diff", "params": {"funding_diff_filter": True}},
    ]

    rows = []
    for v in variants:
        res = backtest_variant(signals, cfg, v["params"])
        port = build_portfolio([res], starting_capital=float(cfg.get("starting_capital_usd", 100_000.0)))
        port_idx = res["index"][:port["n_bars"]]
        port_res = {
            "pair": res["pair"],
            "trades": res["trades"],
            "bar_return": port["bar_return"],
            "n_bars": port["n_bars"],
            "equity": port["equity"],
            "index": port_idx,
        }
        m = metrics_from_result(port_res, cfg)
        rows.append({
            "variant": v["name"],
            "n_trades": int(m["n_trades"]),
            "mean_net_bps": round(float(m["mean_net_pct"]) * 1e4, 3),
            "mean_gross_bps": round(float(m["mean_gross_pct"]) * 1e4, 3),
            "win_rate": round(float(m["win_rate"]), 4),
            "sharpe_daily_resampled": round(float(m["sharpe_daily_resampled"]), 4),
            "annualized_return_daily": round(float(m["annualized_return_daily"]), 4),
            "max_drawdown_pct": round(float(m["max_drawdown_pct"]), 4),
            "profit_factor": round(float(m["profit_factor"]), 4),
        })
        print(v["name"], rows[-1])

    out_path = HERE / "quick_verify_2024.json"
    out_path.write_text(json.dumps(rows, indent=2, default=float))
    print("Saved", out_path)


if __name__ == "__main__":
    main()
