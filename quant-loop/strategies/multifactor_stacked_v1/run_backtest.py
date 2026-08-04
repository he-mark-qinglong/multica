#!/usr/bin/env python3
"""Run multifactor_stacked_v1: portfolio backtest + CPCV validation + report.

Pipeline:
  1. Load BTC/ETH/SOL 15m klines, resample to 4h/1d.
  2. For each variant (kama_only, kama_imb, kama_session, stacked4):
     per-symbol net returns -> equal-weight portfolio via
     ``_shared/portfolio/backtest_engine.py``.
  3. CPCV (n_groups=6, k_test=2, purge=50, embargo=20) on each variant's
     portfolio return stream + Deflated Sharpe (n_trials = #variants).
  4. Write REPORT.md + validation_report.json next to this script.

Usage:
    python3 strategies/multifactor_stacked_v1/run_backtest.py [--skip-cpcv]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
QL_ROOT = HERE.parents[1]
for p in (str(HERE), str(QL_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from _shared.portfolio.backtest_engine import PortfolioBacktestEngine  # noqa: E402
from _shared.validation.cpcv import cpcv, deflated_sharpe  # noqa: E402
from strategy import (  # noqa: E402
    PERIODS_PER_YEAR,
    VARIANTS,
    backtest_returns,
    load_symbol,
    metrics_from_returns,
)

CPCV_KW = dict(n_groups=6, k_test=2, purge_bars=50, embargo_bars=20,
               periods_per_year=PERIODS_PER_YEAR)


def load_all(cfg: dict) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    return {sym: load_symbol(QL_ROOT, sym) for sym in cfg["symbols"]}


def portfolio_returns(data: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
                      cfg: dict, variant: str) -> pd.Series:
    """Equal-weight portfolio of per-symbol net returns (common 4h index)."""
    per_sym = {}
    for sym, (df_4h, df_1d) in data.items():
        per_sym[sym] = backtest_returns(df_4h, df_1d, cfg, variant)
    df = pd.DataFrame(per_sym).dropna()
    engine = PortfolioBacktestEngine(
        strategies={c: df[c] for c in df.columns},
        optimizer="equal", rebalance_mode="none",
        target_vol=None, max_drawdown=None,
        periods_per_year=PERIODS_PER_YEAR, warmup=0,
    )
    result = engine.run()
    return result.returns


def run_cpcv(port_rets: pd.Series, label: str):
    """CPCV on a fixed-parameter portfolio return stream (temporal stability).

    strategy_fn ignores data_train by construction — parameters come from
    config, so the harness flags this as temporal-stability, which is what
    it honestly is (same convention as the kama_mtf baseline)."""
    frame = port_rets.to_frame("port")
    res = cpcv(frame, lambda _train, d: d["port"], **CPCV_KW)
    folds = [{"test_start": str(f.test_start), "test_end": str(f.test_end),
              "oos_sharpe": round(f.oos_sharpe, 4), "n_trades": f.n_trades}
             for f in res.folds]
    print(f"  [{label}] CPCV mean OOS Sharpe = {res.mean_oos_sharpe:.3f} "
          f"(worst {min(f.oos_sharpe for f in res.folds):.3f}, "
          f"{len(res.folds)} folds)", flush=True)
    return res, folds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-cpcv", action="store_true")
    args = ap.parse_args()

    cfg = json.loads((HERE / "config.json").read_text())
    data = load_all(cfg)

    report: dict = {"strategy": cfg["strategy_name"], "variants": {},
                    "cpcv_params": {k: v for k, v in CPCV_KW.items()
                                    if k != "periods_per_year"},
                    "validation_type": "temporal-stability (fixed params)"}

    print(f"=== multifactor_stacked_v1 — {len(VARIANTS)} variants ===", flush=True)
    for variant in VARIANTS:
        port = portfolio_returns(data, cfg, variant)
        m = metrics_from_returns(port)
        entry = {"full_sample": {k: round(v, 4) for k, v in m.items()},
                 "n_bars": len(port),
                 "span": f"{port.index[0]} → {port.index[-1]}"}
        if not args.skip_cpcv:
            res, folds = run_cpcv(port, variant)
            entry["cpcv"] = {
                "mean_oos_sharpe": round(res.mean_oos_sharpe, 4),
                "std_oos_sharpe": round(res.std_oos_sharpe, 4),
                "worst_fold": round(min(f.oos_sharpe for f in res.folds), 4),
                "best_fold": round(max(f.oos_sharpe for f in res.folds), 4),
                "n_folds": len(res.folds),
                "all_folds_positive": all(f.oos_sharpe > 0 for f in res.folds),
                "folds": folds,
            }
        report["variants"][variant] = entry
        print(f"  [{variant}] Sharpe {m['sharpe']:.3f}  MaxDD {m['max_drawdown']:.2%}  "
              f"Calmar {m['calmar']:.2f}  TotRet {m['total_return']:.1%}", flush=True)

    # Deflated Sharpe for the best variant, corrected for the 4-variant search.
    best = max(report["variants"].items(),
               key=lambda kv: kv[1]["full_sample"]["sharpe"])
    dsr = deflated_sharpe(observed_sharpe=best[1]["full_sample"]["sharpe"],
                          n_trials=len(VARIANTS), sample_len=best[1]["n_bars"])
    report["deflated_sharpe"] = {"best_variant": best[0], "value": round(dsr, 4),
                                 "n_trials": len(VARIANTS),
                                 "edge_survives_deflation": dsr > 0}
    print(f"  DSR (best={best[0]}, n_trials=4): {dsr:.3f} "
          f"→ {'PASS' if dsr > 0 else 'FAIL'}", flush=True)

    (HERE / "validation_report.json").write_text(json.dumps(report, indent=2))
    _write_markdown(report)
    print(f"  wrote {HERE / 'validation_report.json'} and REPORT.md", flush=True)
    return 0


def _write_markdown(report: dict) -> None:
    lines = [
        "# multifactor_stacked_v1 — Validation Report",
        "",
        f"- Validation: {report['validation_type']}, CPCV "
        f"{report['cpcv_params']}",
        f"- DSR: {report['deflated_sharpe']['value']:.3f} on best variant "
        f"**{report['deflated_sharpe']['best_variant']}** "
        f"(n_trials={report['deflated_sharpe']['n_trials']}) → "
        f"{'PASS' if report['deflated_sharpe']['edge_survives_deflation'] else 'FAIL'}",
        "",
        "| Variant | Sharpe | MaxDD | Calmar | Total Ret | CPCV mean OOS Sharpe "
        "| CPCV worst fold | all folds > 0 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, v in report["variants"].items():
        fs = v["full_sample"]
        if "cpcv" in v:
            c = v["cpcv"]
            cpcv_cells = (f"{c['mean_oos_sharpe']:.3f} | {c['worst_fold']:.3f} | "
                          f"{'yes' if c['all_folds_positive'] else 'NO'}")
        else:
            cpcv_cells = "— | — | —"
        lines.append(
            f"| {name} | {fs['sharpe']:.3f} | {fs['max_drawdown']:.2%} | "
            f"{fs['calmar']:.2f} | {fs['total_return']:.1%} | {cpcv_cells} |")
    lines += [
        "",
        "## Interpretation",
        "",
        "- `kama_only` is the validated baseline (kama_mtf_btc_4h_1d params).",
        "- Aux factors are AND-gates on top of the KAMA veto: they can only "
        "*reduce* exposure. The study asks whether that reduction is "
        "compensated by higher risk-adjusted returns (Sharpe/Calmar/MaxDD).",
        "- CPCV here is temporal-stability (fixed parameters), not true OOS "
        "refit — the strategy_fn intentionally ignores data_train.",
        "- Factor 2 (book imbalance) uses the taker-flow kline proxy; real "
        "books5 history from scripts/collect_okx_book_ws.py is still "
        "accumulating.",
    ]
    (Path(__file__).resolve().parent / "REPORT.md").write_text("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
