"""Re-run full-history backtest for H1/H3 baseline + FIXED fee-shock (SMA-36566).

Slim variant of run_btcsol_variants_fixed.py that:
  1. Imports the FIXED fee_shock_metrics from run_btcsol_variants_fixedfee.py
     (per_trade_fraction=1.0 instead of buggy 0.005)
  2. Runs ONLY full-history backtest (no walk-forward OOS, no plots)
  3. Saves equity daily CSV + trade log CSV + fixed fee_shock JSON
  4. Writes a SUMMARY.fixedfee.md cross-table for the verdict

Targets: H1, H3 (H2/H4 included for completeness but H1/H3 are the verdict-critical ones).

Outputs (parallel to original fixed outputs, prefixed .fixedfee):
  results/equity_{H}_daily.fixedfee.csv
  results/trades_{H}.fixedfee.csv
  results/fee_shock_{H}.fixedfee.json
  results/SUMMARY.fixedfee.md
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Bootstrap the FIXED variant of the runner so we get the corrected
# fee_shock_metrics default (per_trade_fraction=1.0).
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from run_btcsol_variants_fixedfee import (  # noqa: E402
    fee_shock_metrics,         # the FIXED one (default per_trade_fraction=1.0)
    load_funding,
    load_perp_1m,
    load_config,
    align_and_clip,
    portfolio_metrics,
)
# run_backtest lives in mtf_xs_pairs_base_20260718 — pull it from the
# upstream module to avoid the buggy _backtest_pair_with_cost patch.
from mtf_xs_pairs_base_20260718 import run_backtest  # noqa: E402

RESULTS_DIR = SCRIPT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_HYPOTHESES = ("H1", "H2", "H3", "H4")  # full family re-run for verdict context


def run_one(hyp: str) -> dict:
    print(f"\n=== {hyp} full-history (FIXED fee_shock, per_trade_fraction=1.0) ===", flush=True)
    cfg = load_config(hyp)
    d1m = {"BTCUSDT": load_perp_1m("BTCUSDT"), "SOLUSDT": load_perp_1m("SOLUSDT")}
    funding = {"BTCUSDT": load_funding("BTCUSDT"), "SOLUSDT": load_funding("SOLUSDT")}
    d1m, funding = align_and_clip(d1m, funding)
    common_idx = d1m["BTCUSDT"].index
    print(f"  aligned/clipped rows: {len(common_idx)}", flush=True)

    res = run_backtest(d1m, cfg, funding)
    full_metrics, equity = portfolio_metrics(res, common_idx, cfg)
    print(f"  full_history: sharpe_daily_resampled={full_metrics['sharpe_daily_resampled']:.3f} "
          f"ann={full_metrics['annualized_return_daily_resampled']*100:.2f}% "
          f"MDD={full_metrics['max_drawdown_pct_daily_method']*100:.2f}% "
          f"trades={full_metrics['n_trades']}",
          flush=True)

    trades = [t for pp in res["per_pair"] for t in pp["trades"]]
    # Trade stats for the verdict. Some hypotheses (SE-H3) stamp `gross_pct`;
    # the base engine only stamps `pnl_pct`. When gross_pct is missing,
    # back it out as pnl_pct + 8 bps (= engine cost at 1+1 bps/side/leg).
    if trades:
        if "gross_pct" in trades[0]:
            gross_bps = np.array([float(t["gross_pct"]) for t in trades]) * 10_000
        else:
            gross_bps = (np.array([float(t["pnl_pct"]) for t in trades]) + 8e-4) * 10_000
        pnl_bps = np.array([float(t["pnl_pct"]) for t in trades]) * 10_000
        trade_stats = {
            "n_trades": int(len(trades)),
            "mean_gross_bps": float(gross_bps.mean()),
            "median_gross_bps": float(np.median(gross_bps)),
            "std_gross_bps": float(gross_bps.std(ddof=1)),
            "mean_net_at_engine_8bps_bps": float(pnl_bps.mean()),
            "win_rate": float((pnl_bps > 0).mean()),
            "profit_factor_daily_method": float(full_metrics["profit_factor_daily_method"]),
            "gross_source": "trade.gross_pct" if "gross_pct" in trades[0]
                else "pnl_pct + 8bps (back-out, gross_pct not stamped)",
        }
    else:
        trade_stats = {"n_trades": 0}

    fee_sens = {
        label: fee_shock_metrics(equity, trades, rt)  # FIXED default per_trade_fraction=1.0
        for label, rt in (
            ("inhouse_4bps_rt", 4.0),
            ("freqtrade_24bps_rt", 24.0),
            ("backtrader_60bps_rt", 60.0),
        )
    }
    print(f"  fee shock FIXED 4/24/60 bps Sharpe: "
          f"{fee_sens['inhouse_4bps_rt']['sharpe_daily_resampled']:+.3f} / "
          f"{fee_sens['freqtrade_24bps_rt']['sharpe_daily_resampled']:+.3f} / "
          f"{fee_sens['backtrader_60bps_rt']['sharpe_daily_resampled']:+.3f}",
          flush=True)

    # Persist artefacts.
    daily_eq = equity.resample("1D").last().dropna()
    daily_eq.to_frame("equity").to_csv(RESULTS_DIR / f"equity_{hyp}_daily.fixedfee.csv")
    pd.DataFrame(trades).to_csv(RESULTS_DIR / f"trades_{hyp}.fixedfee.csv", index=False)

    out_fee = {
        "label": f"{hyp}_full_history_fixedfee",
        "per_trade_fraction": 1.0,
        "trade_stats": trade_stats,
        "full_history": full_metrics,
        "fee_shock": fee_sens,
    }
    (RESULTS_DIR / f"fee_shock_{hyp}.fixedfee.json").write_text(
        json.dumps(out_fee, indent=2, default=float))

    return {"hyp": hyp, "trade_stats": trade_stats, "full_history": full_metrics,
            "fee_shock": fee_sens}


def main() -> int:
    records = {h: run_one(h) for h in TARGET_HYPOTHESES}

    # Summary table.
    md = ["# H1/H3 Baseline — FIXED Fee-Shock Replay (SMA-36566)\n",
          f"Per-trade cost basis corrected from buggy 0.005 to fixed **1.0** "
          f"(= full pair pct, matches trade log `pnl_pct` and engine "
          f"`cost = 2*2*(fee+slip)/10000`).\n",
          "## Per-trade stats (sizing-independent break-even evidence)\n",
          "| Hyp | n_trades | mean gross | std gross | win_rate | mean net @8bps |",
          "|-----|---------:|-----------:|----------:|---------:|---------------:|"]
    for h, r in records.items():
        ts = r["trade_stats"]
        md.append(f"| {h}   | {ts['n_trades']} | {ts['mean_gross_bps']:.2f} bps | "
                  f"{ts['std_gross_bps']:.2f} bps | {ts['win_rate']*100:.1f}% | "
                  f"{ts['mean_net_at_engine_8bps_bps']:.2f} bps |")

    md.append("\n## Fee-shock Sharpe (FIXED per_trade_fraction=1.0)\n")
    md.append("| Hyp | Gross Sharpe | 4 bps RT | 24 bps RT | 60 bps RT | Verdict |")
    md.append("|-----|-------------:|---------:|----------:|----------:|---------|")
    for h, r in records.items():
        gross_sr = r["full_history"]["sharpe_daily_resampled"]
        fs = r["fee_shock"]
        verdict = "DEAD @24bps" if fs["freqtrade_24bps_rt"]["sharpe_daily_resampled"] < 0 else (
            "MARGINAL @60bps" if fs["backtrader_60bps_rt"]["sharpe_daily_resampled"] < 0
            else "ROBUST")
        md.append(f"| {h}   | {gross_sr:+.3f} | "
                  f"{fs['inhouse_4bps_rt']['sharpe_daily_resampled']:+.3f} | "
                  f"{fs['freqtrade_24bps_rt']['sharpe_daily_resampled']:+.3f} | "
                  f"{fs['backtrader_60bps_rt']['sharpe_daily_resampled']:+.3f} | "
                  f"{verdict} |")

    (RESULTS_DIR / "SUMMARY.fixedfee.md").write_text("\n".join(md))
    print(f"\nWrote summary to {RESULTS_DIR / 'SUMMARY.fixedfee.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())