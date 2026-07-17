"""Run backtest for mtf_h2_vpvr_edge_1m_15m_2h_20260718."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data_loader import load_all  # noqa: E402
from strategy import (  # noqa: E402
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)

CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _summarise_symbol(sym_result: dict, cfg: dict) -> dict:
    trades = sym_result["trades"]
    bar_return = sym_result["bar_return"]
    df_idx = pd.date_range(start=sym_result["span_start"], periods=sym_result["n_bars"], freq="1min")
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "symbol": sym_result["symbol"], "n_trades": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "total_return_pct": 0.0,
            "sharpe_daily_resampled": 0.0, "annualized_return_daily": 0.0,
            "max_drawdown_pct": 0.0,
            "span_start": sym_result["span_start"], "span_end": sym_result["span_end"],
            "n_bars": sym_result["n_bars"],
        }
    pnls = np.array([t["pnl_pct"] for t in trades])
    wins = pnls > 0
    losses = pnls <= 0
    win_rate = float(wins.mean())
    gw = float(pnls[wins].sum()) if wins.any() else 0.0
    gl = float(-pnls[losses].sum()) if losses.any() else 0.0
    pf = gw / gl if gl > 0 else float("inf")
    sr = sharpe_daily_resampled(bar_return, df_idx)
    starting = float(cfg.get("starting_capital_usd", 100000.0))
    pfdd = profit_factor_and_mdd(bar_return, starting)
    total = float(np.exp(np.log1p(np.where(bar_return > -1, bar_return, -0.999999)).sum()) - 1.0)
    return {
        "symbol": sym_result["symbol"], "n_trades": n_trades, "win_rate": win_rate,
        "profit_factor": float(pf), "total_return_pct": total,
        "sharpe_daily_resampled": sr["sharpe_daily_resampled"],
        "annualized_return_daily": sr["annualized_return_daily"],
        "max_drawdown_pct": pfdd["max_drawdown_pct"],
        "span_start": sr["span"][0] or sym_result["span_start"],
        "span_end": sr["span"][1] or sym_result["span_end"],
        "n_bars": sym_result["n_bars"],
    }


def _write_trades_csv(per_symbol: list, run_dir: Path) -> None:
    cols = [
        "symbol", "direction", "entry_ts", "entry_price",
        "exit_ts", "exit_price", "pnl_pct", "bars_held",
        "vah_at_entry", "val_at_entry", "poc_at_entry",
        "atr_at_entry", "exit_reason", "trend_2h_at_entry",
    ]
    out = run_dir / "trades_all.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for sr in per_symbol:
            for t in sr["trades"]:
                w.writerow({k: t.get(k) for k in cols})
    # per-symbol copies
    for sr in per_symbol:
        path = run_dir / ("trades_" + sr["symbol"] + ".csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for t in sr["trades"]:
                w.writerow({k: t.get(k) for k in cols})


def _write_equity_csv(per_symbol: list, portfolio: dict, run_dir: Path) -> None:
    # Build equity CSVs at DAILY granularity (matches the daily-resampled
    # Sharpe method — bar-level equity adds nothing meaningful for storage
    # or downstream analysis, and at 1m it's ~100MB per file).
    if not per_symbol:
        return
    starts = [pd.Timestamp(s["trades"][0]["entry_ts"]) for s in per_symbol if s["trades"]]
    if not starts:
        starts = [pd.Timestamp(per_symbol[0]["span_start"])]
    n_bars = portfolio["n_bars"]
    idx = pd.date_range(start=min(starts), periods=n_bars, freq="1min")

    def _daily(equity: np.ndarray, br: np.ndarray) -> tuple:
        eq_s = pd.Series(equity, index=idx)
        # last bar of each day
        daily_eq = eq_s.resample("1D").last().dropna()
        daily_br = pd.Series(br, index=idx).resample("1D").sum().reindex(daily_eq.index).fillna(0.0)
        return daily_eq, daily_br

    # portfolio equity (daily)
    eq_path = run_dir / "equity_portfolio.csv"
    d_eq, d_br = _daily(portfolio["equity"], portfolio["bar_return"])
    with open(eq_path, "w", newline="") as f:
        f.write("timestamp,equity_usd,bar_return\n")
        for ts, eq, br in zip(d_eq.index, d_eq.to_numpy(), d_br.to_numpy()):
            f.write("{},{},{}\n".format(ts.isoformat(), eq, br))

    # per-symbol equity (daily)
    for sr in per_symbol:
        path = run_dir / ("equity_" + sr["symbol"] + ".csv")
        starting = 100000.0
        eq = np.empty(sr["n_bars"])
        eq[0] = starting
        for i in range(1, sr["n_bars"]):
            eq[i] = eq[i - 1] * (1.0 + float(sr["bar_return"][i]))
        d_eq_s, d_br_s = _daily(eq, sr["bar_return"])
        with open(path, "w", newline="") as f:
            f.write("timestamp,equity_usd,bar_return\n")
            for ts, eqv, br in zip(d_eq_s.index, d_eq_s.to_numpy(), d_br_s.to_numpy()):
                f.write("{},{},{}\n".format(ts.isoformat(), eqv, br))


def main() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m data for", syms)
    data = load_all(syms)
    for s, df in data.items():
        print(" ", s, len(df), "span", df.index[0], "->", df.index[-1])
    print("Running backtest …")
    res = run_backtest(data, cfg)
    port = res["portfolio"]
    print(f"portfolio n_bars={port['n_bars']}, n_symbols={len(res['per_symbol'])}")
    for ps in res["per_symbol"]:
        print(f"  {ps['symbol']}: n_trades={len(ps['trades'])}")

    per_sym_metrics = []
    for ps in res["per_symbol"]:
        per_sym_metrics.append(_summarise_symbol(ps, cfg))

    n_total = sum(int(m["n_trades"]) for m in per_sym_metrics)
    if per_sym_metrics:
        avg_sharpe = float(np.mean([m["sharpe_daily_resampled"] for m in per_sym_metrics]))
        avg_ret = float(np.mean([m["annualized_return_daily"] for m in per_sym_metrics]))
        avg_pf = float(np.mean([m["profit_factor"] for m in per_sym_metrics
                                 if np.isfinite(m["profit_factor"])]))
        avg_mdd = float(np.mean([m["max_drawdown_pct"] for m in per_sym_metrics]))
        avg_wr = float(np.mean([m["win_rate"] for m in per_sym_metrics]))
    else:
        avg_sharpe = avg_ret = avg_pf = avg_mdd = avg_wr = 0.0

    # portfolio metrics with a real index from the data span
    port_metrics = {}
    if port["n_bars"] > 0:
        first_sym = per_sym_metrics[0]
        idx = pd.date_range(start=first_sym["span_start"], periods=port["n_bars"], freq="1min")
        starting = float(cfg.get("starting_capital_usd", 100000.0))
        sr_p = sharpe_daily_resampled(port["bar_return"], idx)
        pfdd_p = profit_factor_and_mdd(port["bar_return"], starting)
        port_metrics = {
            "n_bars": port["n_bars"],
            "sharpe_daily_resampled": sr_p["sharpe_daily_resampled"],
            "annualized_return_daily": sr_p["annualized_return_daily"],
            "max_drawdown_pct": pfdd_p["max_drawdown_pct"],
            "profit_factor": pfdd_p["profit_factor"],
            "total_return_pct": float(np.exp(np.log1p(
                np.where(port["bar_return"] > -1, port["bar_return"], -0.999999)).sum()) - 1.0),
        }

    sharpe_min = float(cfg.get("hard_gates", {}).get("oos_sharpe_min", 1.0))
    payload = {
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "date": cfg.get("date"),
        "hypothesis": cfg.get("hypothesis"),
        "campaign": cfg.get("campaign"),
        "primary_timeframe": cfg.get("primary_timeframe"),
        "filter_timeframe": cfg.get("filter_timeframe"),
        "regime_timeframe": cfg.get("regime_timeframe"),
        "timeframe": cfg.get("primary_timeframe"),
        "instruments": cfg.get("instruments"),
        "variant": "single_pair_H2",
        "n_trades_total": n_total,
        "win_rate_avg": avg_wr,
        "profit_factor_avg": avg_pf,
        "avg_pair_sharpe_daily_resampled": avg_sharpe,
        "avg_pair_annualized_return_daily": avg_ret,
        "avg_pair_max_drawdown_pct": avg_mdd,
        "portfolio": port_metrics,
        "sharpe_method": "daily_resampled",
        "sharpe_method_evidence": (
            "sharpe_daily_resampled is computed by aggregating per-bar equity into "
            "daily equity (last-bar-of-day), then pct_change and Sharpe over "
            "the daily series, annualised by sqrt(365)."
        ),
        "per_pair": {m["symbol"]: m for m in per_sym_metrics},
        "params": cfg.get("indicators", {}),
        "tag": "PROFITABLE" if avg_sharpe >= sharpe_min else "NOT-PROFITABLE",
        "evidence_gate": {
            "sharpe_threshold": sharpe_min,
            "sharpe_observed": avg_sharpe,
            "passed_full_backtest": avg_sharpe >= sharpe_min,
            "note": "full-history gate only; OOS walk-forward Sharpe is in walk_forward.json",
        },
    }

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(payload, indent=2, default=float))
    (RESULTS_DIR / "summary.json").write_text(json.dumps({
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "hypothesis": cfg.get("hypothesis"),
        "campaign": cfg.get("campaign"),
        "tag": payload["tag"],
        "sharpe_method": "daily_resampled",
        "avg_pair_sharpe_daily_resampled": avg_sharpe,
        "avg_pair_annualized_return_daily": avg_ret,
        "avg_pair_max_drawdown_pct": avg_mdd,
        "profit_factor_avg": avg_pf,
        "n_trades_total": n_total,
        "portfolio_annualized_return_daily": port_metrics.get("annualized_return_daily", 0.0),
        "portfolio_sharpe_daily_resampled": port_metrics.get("sharpe_daily_resampled", 0.0),
        "per_pair": {m["symbol"]: m for m in per_sym_metrics},
    }, indent=2, default=float))

    _write_trades_csv(res["per_symbol"], RESULTS_DIR)
    _write_equity_csv(res["per_symbol"], port, RESULTS_DIR)

    print("=== " + cfg["strategy"] + " (H2 single-pair) ===")
    print("tag                       :", "[" + payload["tag"] + "]")
    print("avg symbol sharpe(d/r)    :", f"{avg_sharpe:.3f}")
    print("avg symbol ann.ret(d)     :", f"{avg_ret:.4f}")
    print("avg symbol max DD         :", f"{avg_mdd:.4f}")
    print("avg symbol profit_f       :", f"{avg_pf:.3f}")
    print("n_trades_total            :", n_total)
    print("sharpe_method             :", payload["sharpe_method"])
    print("metrics.json              :", str(RESULTS_DIR / "metrics.json"))
    return payload


if __name__ == "__main__":
    main()
