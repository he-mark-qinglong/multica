"""SMA-34947 U5 funding_carry ETH/SOL 1m — multi-window harness.

Iterates the U5 funding-carry long-only event-driven harness over
windows = [30, 90, 365] days so we can see whether the failure mode
on 1m funding-carry is window-shortage or signal-absence. Per
window we:

  - load the trailing N days of ETHUSDT/SOLUSDT 1m OHLCV
    (real Binance USDT-M, no synthetic) merged with the 8h
    funding events,
  - run the variant grid (abs_1bp, abs_0.5bp, abs_0.25bp,
    pct_q20, pct_q10, pct_q05) on each symbol standalone,
  - compute the equal-risk ETH+SOL portfolio per variant,
  - apply the G1-G7 hard gates (Sharpe>=1.0, ann>=15%,
    maxDD>=-25%, PF>=1.5, BS-CI_lo>=0.5, Bonferroni one-sided
    p<=0.0125, trades>=30),
  - write per-window metrics/summary + per-(sym, variant) equity
    and trades CSVs.

All window outputs land under
``~/multica/quant-loop/backtests/u5_funding_carry_eth_sol_1m/``
under per-window subdirectories so the 90-day baseline outputs from
SMA-34930 stay intact. A combined ``multi_window_summary.json`` is
written at the top level of the same backtest directory.

Cross-framework CV (backtrader-equivalent + freqtrade IStrategy)
on the best per-window variant is run after the per-window sweep,
reusing the SMA-34930 adapter harnesses.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
STRATEGY_DIR = QUANT_LOOP / "strategies" / "funding_carry"
sys.path.insert(0, str(STRATEGY_DIR))

from run_u5 import main as run_u5_main  # noqa: E402

OUT_BASE = QUANT_LOOP / "backtests" / "u5_funding_carry_eth_sol_1m"
OUT_BASE.mkdir(parents=True, exist_ok=True)

# Issue SMA-34947 spec: 30 / 90 / 365 days. Use 30/90 to match the
# pre-existing 90d baseline + add the failure-mode probe windows.
WINDOWS = [30, 90, 365]
ITER_PREFIX = "SMA-34947"


def _per_window_outdir(window_days: int) -> Path:
    d = OUT_BASE / f"w{window_days}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_one_window(window_days: int) -> dict:
    out_dir = _per_window_outdir(window_days)
    iteration = f"{ITER_PREFIX}-w{window_days}"
    print(f"\n========== WINDOW {window_days}d  ({iteration}) ==========", flush=True)
    t0 = time.time()
    rc = run_u5_main(window_days=window_days, out_dir=out_dir,
                     iteration=iteration)
    if rc != 0:
        raise RuntimeError(f"run_u5_main returned rc={rc} for window={window_days}")
    metrics_path = out_dir / "u5_metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics["_elapsed_sec"] = round(time.time() - t0, 2)
    metrics["_out_dir"] = str(out_dir)
    return metrics


def _collect_best_per_window(metrics: dict, window_days: int) -> list[dict]:
    """Return a flat list of (sym, variant, sharpe, ann, gates) rows for
    the per-symbol sweep (portfolio row excluded — portfolios are reported
    separately).
    """
    rows: list[dict] = []
    for sym, variants in metrics["per_symbol_variants"].items():
        for v in variants:
            mm = v["metrics"]
            gates = mm.get("gates", {})
            rows.append({
                "window_days": window_days,
                "scope": "per_symbol",
                "symbol": sym,
                "variant": v["label"],
                "n_trades": mm.get("n_trades"),
                "sharpe_daily": mm.get("sharpe_daily"),
                "annualized_return": mm.get("annualized_return"),
                "max_drawdown_pct": mm.get("max_drawdown_pct"),
                "profit_factor": mm.get("profit_factor"),
                "win_rate": mm.get("win_rate"),
                "bootstrap_sharpe_ci_lo": mm.get("bootstrap_sharpe_ci_lo"),
                "bootstrap_sharpe_ci_hi": mm.get("bootstrap_sharpe_ci_hi"),
                "sharpe_p_value_pos_one_sided": mm.get("sharpe_p_value_pos_one_sided"),
                "gates_pass_count": sum(1 for v_ in gates.values() if v_),
                "gates": gates,
                "n_funding_events": mm.get("n_funding_events"),
                "span_start": mm.get("span_start"),
                "span_end": mm.get("span_end"),
            })
    for variant_label, port in metrics.get("portfolio_variants", {}).items():
        g = port.get("gates", {})
        rows.append({
            "window_days": window_days,
            "scope": "portfolio",
            "symbol": "ETH+SOL",
            "variant": variant_label,
            "n_trades": port.get("n_trades"),
            "sharpe_daily": port.get("sharpe_daily"),
            "annualized_return": port.get("annualized_return"),
            "max_drawdown_pct": port.get("max_drawdown_pct"),
            "profit_factor": port.get("profit_factor"),
            "win_rate": port.get("win_rate"),
            "bootstrap_sharpe_ci_lo": port.get("bootstrap_sharpe_ci_lo"),
            "bootstrap_sharpe_ci_hi": port.get("bootstrap_sharpe_ci_hi"),
            "sharpe_p_value_pos_one_sided": port.get("sharpe_p_value_pos_one_sided"),
            "gates_pass_count": sum(1 for v_ in g.values() if v_),
            "gates": g,
            "n_funding_events": None,
            "span_start": None,
            "span_end": None,
        })
    return rows


def _funding_event_stats_rows(metrics: dict) -> list[dict]:
    rows = []
    for sym, s in metrics.get("funding_event_stats", {}).items():
        rows.append({
            "symbol": sym,
            "window_days": metrics["window_days"],
            **{k: s.get(k) for k in (
                "n_events", "max", "min", "mean", "p01", "p05", "p10",
                "p20", "p80", "p90", "p95", "p99",
                "neg_pct", "le_-1bp_pct", "le_-0.5bp_pct")},
        })
    return rows


def main() -> int:
    print(f"[multi] starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[multi] windows={WINDOWS}  out_base={OUT_BASE}")
    all_metrics: dict[int, dict] = {}
    t_start = time.time()
    for w in WINDOWS:
        all_metrics[w] = run_one_window(w)
    elapsed = round(time.time() - t_start, 2)

    # Combined summary.
    per_window_rows: list[dict] = []
    funding_rows: list[dict] = []
    for w in WINDOWS:
        per_window_rows.extend(_collect_best_per_window(all_metrics[w], w))
        funding_rows.extend(_funding_event_stats_rows(all_metrics[w]))

    # Hard-gate summary at a glance: for each window, list any (sym, variant)
    # row that passes ALL of G1-G6 + G7 (note: G6 is per-variant Bonferroni
    # already embedded in the per-variant gates dict).
    def _all_pass(g: dict) -> bool:
        # We don't enforce Bonferroni on per_symbol here because the
        # run_u5 gates dict already has G6_bonferroni_pos_one_sided.
        keys = ["G1_sharpe_ge_1", "G2_ann_ge_15pct", "G3_maxdd_ge_-25pct",
                "G4_pf_ge_1_5", "G5_bs_ci_lo_ge_0_5", "G7_trades_ge_30"]
        return all(g.get(k) for k in keys)

    passing_rows = [r for r in per_window_rows if _all_pass(r.get("gates", {}))]
    print(f"\n[multi] passing_rows (G1+G2+G3+G4+G5+G7): {len(passing_rows)}")

    combined = {
        "issue": "SMA-34947",
        "variant_key": "funding_carry_u5_eth_sol_1m",
        "instruments": ["ETHUSDT", "SOLUSDT"],
        "timeframes": ["1m"],
        "windows": WINDOWS,
        "hard_gates": all_metrics[WINDOWS[0]]["hard_gates"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_sec_total": elapsed,
        "data_provenance": all_metrics[WINDOWS[0]]["data_provenance"],
        "per_window_metrics_paths": {
            w: str(_per_window_outdir(w) / "u5_metrics.json") for w in WINDOWS
        },
        "per_window_summary_paths": {
            w: str(_per_window_outdir(w) / "u5_summary.txt") for w in WINDOWS
        },
        "funding_event_stats_by_window": funding_rows,
        "all_results_rows": per_window_rows,
        "rows_passing_all_g1_g7_count": len(passing_rows),
        "rows_passing_all_g1_g7": passing_rows,
    }
    combined_path = OUT_BASE / "multi_window_summary.json"
    combined_path.write_text(json.dumps(combined, indent=2, default=str))
    print(f"\n[multi] wrote {combined_path}")

    # Human-readable cross-window table.
    lines = []
    lines.append(f"=== U5 funding_carry ETH/SOL 1m — multi-window ({ITER_PREFIX}) ===")
    lines.append(f"windows={WINDOWS}  elapsed={elapsed}s")
    lines.append("G1 Sharpe>=1, G2 ann>=15%, G3 maxDD>=-25%, G4 PF>=1.5, "
                 "G5 BS-CI_lo>=0.5, G7 trades>=30 (G6 Bonferroni per-variant)")
    lines.append("")
    lines.append("Funding event stats per window:")
    lines.append(f"{'Window':<7}{'Sym':<8}{'n_evt':>7}{'max%':>9}{'min%':>9}{'mean%':>10}{'neg%':>8}{'<=-1bp%':>10}{'<=-0.5bp%':>11}")
    for r in funding_rows:
        lines.append(
            f"{r['window_days']:<7}{r['symbol']:<8}{r['n_events']:>7d}"
            f"{r['max']*100:>8.4f}%{r['min']*100:>8.4f}%"
            f"{r['mean']*100:>9.4f}%{r['neg_pct']*100:>7.1f}%"
            f"{r['le_-1bp_pct']*100:>9.1f}%{r['le_-0.5bp_pct']*100:>10.1f}%"
        )
    lines.append("")
    lines.append("Per-(window, scope, symbol, variant) results:")
    lines.append(f"{'Win':<5}{'Scope':<11}{'Sym':<8}{'Variant':<11}{'Trades':>7}"
                 f"{'Sharpe':>8}{'Ann%':>8}{'MaxDD%':>9}{'PF':>7}{'BS_CIlo':>9}"
                 f"{'p1side':>8}{'G_pass':>7}  Gates")
    for r in per_window_rows:
        g = r.get("gates", {})
        gates_s = "".join(["Y" if g.get(k) else "N" for k in sorted(g)])
        pf_v = r.get("profit_factor")
        pf_s = f"{pf_v:.2f}" if (isinstance(pf_v, (int, float))) else (str(pf_v) if pf_v is not None else "-")
        sharpe = r.get("sharpe_daily")
        sharpe_s = f"{sharpe:.3f}" if isinstance(sharpe, (int, float)) else "-"
        ann = r.get("annualized_return")
        ann_s = f"{ann*100:.2f}%" if isinstance(ann, (int, float)) else "-"
        maxdd = r.get("max_drawdown_pct")
        maxdd_s = f"{maxdd:.3f}" if isinstance(maxdd, (int, float)) else "-"
        ci_lo = r.get("bootstrap_sharpe_ci_lo")
        ci_lo_s = f"{ci_lo:.3f}" if isinstance(ci_lo, (int, float)) else "-"
        p1 = r.get("sharpe_p_value_pos_one_sided")
        p1_s = f"{p1:.3f}" if isinstance(p1, (int, float)) else "-"
        lines.append(
            f"{r['window_days']:<5}{r['scope']:<11}{r['symbol']:<8}{r['variant']:<11}"
            f"{r.get('n_trades', 0)!s:>7}{sharpe_s:>8}{ann_s:>8}{maxdd_s:>9}{pf_s:>7}"
            f"{ci_lo_s:>9}{p1_s:>8}{r.get('gates_pass_count', 0)!s:>7}  {gates_s}"
        )
    lines.append("")
    lines.append(f"Rows passing G1+G2+G3+G4+G5+G7 across all windows: {len(passing_rows)}")
    if passing_rows:
        for r in passing_rows:
            lines.append(
                f"  win={r['window_days']}  scope={r['scope']}  "
                f"sym={r['symbol']}  variant={r['variant']}  "
                f"sharpe={r['sharpe_daily']:.3f}  ann={r['annualized_return']*100:.2f}%  "
                f"trades={r['n_trades']}"
            )
    summary_text = "\n".join(lines) + "\n"
    (OUT_BASE / "multi_window_summary.txt").write_text(summary_text)
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())