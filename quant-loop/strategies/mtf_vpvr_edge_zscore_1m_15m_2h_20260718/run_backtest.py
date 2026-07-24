"""Run backtest for mtf_vpvr_edge_zscore_1m_15m_2h_20260718 (SMA-34991)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from data_loader import load_all, load_funding  # noqa: E402
from strategy import (  # noqa: E402
    run_backtest,
    daily_returns,
    sharpe_daily_resampled,
    profit_factor_and_mdd,
)

CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import numpy as np
import pandas as pd


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m data for", syms)
    data = load_all(syms)
    for s, df in data.items():
        print(f"  {s}: {len(df):,} bars, span {df.index[0]} -> {df.index[-1]}")
    print("Running backtest …")
    res = run_backtest(data, cfg, funding=None)
    port = res["portfolio"]
    print(f"portfolio n_bars={port['n_bars']:,}, n_symbols={len(res['per_symbol'])}")
    for ps in res["per_symbol"]:
        print(f"  {ps['symbol']}: n_trades={len(ps['trades'])}")

    # Build a usable index for metrics. For per-symbol, use that symbol's index;
    # for portfolio, use the first symbol's index (truncated to portfolio n_bars).
    starting = float(cfg.get("starting_capital_usd", 100_000.0))

    per_sym_metrics = []
    for ps in res["per_symbol"]:
        if ps["n_bars"] == 0:
            continue
        idx = pd.date_range("2022-01-01", periods=ps["n_bars"], freq="1min")
        sr = sharpe_daily_resampled(ps["bar_return"], idx)
        pfdd = profit_factor_and_mdd(ps["bar_return"], starting)
        per_sym_metrics.append({
            "symbol": ps["symbol"],
            "n_trades": len(ps["trades"]),
            "sharpe_daily_resampled": sr["sharpe_daily_resampled"],
            "annualized_return_daily": sr["annualized_return_daily"],
            "profit_factor": pfdd["profit_factor"],
            "max_drawdown_pct": pfdd["max_drawdown_pct"],
            "span_start": sr["span"][0],
            "span_end": sr["span"][1],
        })

    # Portfolio metrics.
    if port["n_bars"]:
        idx_p = pd.date_range("2022-01-01", periods=port["n_bars"], freq="1min")
        sr_p = sharpe_daily_resampled(port["bar_return"], idx_p)
        pfdd_p = profit_factor_and_mdd(port["bar_return"], starting)
    else:
        sr_p = {"sharpe_daily_resampled": 0.0, "annualized_return_daily": 0.0, "n_days": 0}
        pfdd_p = {"profit_factor": 0.0, "max_drawdown_pct": 0.0}

    avg_sharpe = float(np.mean([m["sharpe_daily_resampled"] for m in per_sym_metrics])) if per_sym_metrics else 0.0
    avg_pf = float(np.mean([m["profit_factor"] for m in per_sym_metrics if np.isfinite(m["profit_factor"])]) ) if per_sym_metrics else 0.0
    avg_mdd = float(np.mean([m["max_drawdown_pct"] for m in per_sym_metrics])) if per_sym_metrics else 0.0
    n_total = sum(int(m["n_trades"]) for m in per_sym_metrics)

    payload = {
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "campaign": cfg.get("campaign"),
        "hypothesis": cfg.get("hypothesis"),
        "primary_timeframe": cfg.get("primary_timeframe"),
        "filter_timeframe": cfg.get("filter_timeframe"),
        "regime_timeframe": cfg.get("regime_timeframe"),
        "instruments": syms,
        "n_trades_total": n_total,
        "avg_pair_sharpe_daily_resampled": avg_sharpe,
        "portfolio_sharpe_daily_resampled": sr_p["sharpe_daily_resampled"],
        "portfolio_annualized_return_daily": sr_p["annualized_return_daily"],
        "portfolio_max_drawdown_pct": pfdd_p["max_drawdown_pct"],
        "profit_factor_avg": avg_pf,
        "avg_pair_max_drawdown_pct": avg_mdd,
        "per_symbol": per_sym_metrics,
        "sharpe_method": "daily_resampled",
        "params": cfg.get("indicators", {}),
    }
    metrics_path = RESULTS_DIR / "metrics.json"
    metrics_path.write_text(json.dumps(payload, indent=2, default=float))

    print("=== " + cfg["strategy"] + " ===")
    print("avg per-symbol Sharpe (d/r) :", f"{avg_sharpe:.3f}")
    print("avg per-symbol ann.ret (d) :", f"{float(np.mean([m['annualized_return_daily'] for m in per_sym_metrics])):.4f}")
    print("avg per-symbol profit_f    :", f"{avg_pf:.3f}")
    print("avg per-symbol max DD      :", f"{avg_mdd:.4f}")
    print("portfolio Sharpe (d/r)     :", f"{sr_p['sharpe_daily_resampled']:.3f}")
    print("portfolio ann.ret (d)      :", f"{sr_p['annualized_return_daily']:.4f}")
    print("portfolio max DD           :", f"{pfdd_p['max_drawdown_pct']:.4f}")
    print("n_trades_total             :", n_total)
    print("metrics.json               :", str(metrics_path))

    # Validate metrics via shared validator (catch sentinels / NaN / out-of-range).
    try:
        from _shared.validators.metrics_validator import validate_metrics
        validate_metrics({
            "sharpe_daily": avg_sharpe,
            "annualized_return": float(np.mean([m["annualized_return_daily"] for m in per_sym_metrics])) if per_sym_metrics else 0.0,
            "max_drawdown_pct": avg_mdd,
            "profit_factor": avg_pf,
            "n_trades": n_total,
        }, strategy_name=cfg["strategy"])
        print("validate_metrics           : OK")
    except Exception as e:
        print("validate_metrics           : WARN —", e)


if __name__ == "__main__":
    main()