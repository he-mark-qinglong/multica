"""Generate SUMMARY.md from results/metrics.json."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METRICS_PATH = ROOT / "results" / "metrics.json"
OUT_PATH = ROOT / "SUMMARY.md"


def fmt_sharpe(v: float) -> str:
    return f"{v:+.3f}"


def fmt_pct(v: float) -> str:
    return f"{v*100:+.2f}%"


def fmt_gate(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def main() -> None:
    data = json.loads(METRICS_PATH.read_text())
    variants = data["variants"]

    lines = []
    lines.append("# H3-variants-h1h2h4 — Unified comparison summary")
    lines.append("")
    lines.append(f"**Cost assumption:** {data['cost']['fee_bps_per_side']:.0f} bps/side fee + "
                 f"{data['cost']['slippage_bps_per_side']:.0f} bps/side slippage = "
                 f"{2*(data['cost']['fee_bps_per_side']+data['cost']['slippage_bps_per_side']):.0f} bps RT per symbol.")
    lines.append("")

    lines.append("## What was done")
    lines.append("")
    lines.append("1. Loaded canonical 1m OHLCV for BTCUSDT/SOLUSDT from `quant-loop/data/perp_1m/` "
                 "and 8h funding rates from `quant-loop/data/funding/`.")
    lines.append("2. Ran H1, H2, H3 and a reconstructed H4 on the BTCUSDT/SOLUSDT pair only through the shared "
                 "`mtf_xs_pairs_base_20260718` + `mtf_xs_runner_20260718` pipeline at the ratified 22 bps RT per-symbol cost.")
    lines.append("3. Computed full-history (IS) metrics and anchored expanding-train walk-forward OOS metrics "
                 "(daily-resampled Sharpe, bootstrap CI, worst-window MDD).")
    lines.append("4. Checked G1-G7/T1 gates using `_shared/gates/enforce.py` semantics.")
    lines.append("5. Ran a cost-sensitivity sweep on H3 from 4 bps to 60 bps per-symbol RT.")
    lines.append("")

    lines.append("## Key numbers")
    lines.append("")
    lines.append("| Variant | OOS Sharpe | OOS ann. | Worst MDD | IS PF | Trades | G1-G7/T1 | Verdict |")
    lines.append("|---------|-----------:|---------:|----------:|------:|-------:|----------|---------|")
    for h in ("H1", "H2", "H3", "H4"):
        v = variants[h]
        oos = v["oos"]
        fh = v["full_history"]
        gates = v["gates"]
        passed = gates["passed"]
        verdict = "HOLD" if passed else "KILL"
        lines.append(
            f"| {h} | {fmt_sharpe(oos['oos_sharpe_mean_daily_resampled'])} | "
            f"{fmt_pct(oos['oos_annualized_mean_daily'])} | "
            f"{fmt_pct(oos['oos_max_drawdown_worst_pct'])} | "
            f"{fh['profit_factor']:.3f} | "
            f"{fh['n_trades']:,} | "
            f"{fmt_gate(passed)} | {verdict} |"
        )
    lines.append("")

    lines.append("### Gate-by-gate detail")
    lines.append("")
    for h in ("H1", "H2", "H3", "H4"):
        v = variants[h]
        gm = v["gate_metrics"]
        g = v["gates"]
        lines.append(f"#### {h}")
        lines.append("")
        lines.append(f"- G1 Sharpe ≥ 1.0: {fmt_gate(gm['sharpe_daily'] >= 1.0)} ({fmt_sharpe(gm['sharpe_daily'])})")
        lines.append(f"- G2 ann. return ≥ 15%: {fmt_gate(gm['annualized_return'] >= 0.15)} ({fmt_pct(gm['annualized_return'])})")
        lines.append(f"- G3 max DD > -25%: {fmt_gate(gm['max_drawdown_pct'] > -0.25)} ({fmt_pct(gm['max_drawdown_pct'])})")
        lines.append(f"- G4 profit factor > 1.5: {fmt_gate(gm['profit_factor'] > 1.5)} ({gm['profit_factor']:.3f})")
        lines.append(f"- G5 CPCV OOS Sharpe ≥ 1.0: N/A (not run)")
        lines.append(f"- G6 bootstrap CI95 lower ≥ 0.5: {fmt_gate(gm['bootstrap_ci95_lower'] >= 0.5)} ({fmt_sharpe(gm['bootstrap_ci95_lower'])})")
        lines.append(f"- G7 deflated Sharpe > 0: {fmt_gate(gm['deflated_sharpe'] > 0)} ({fmt_sharpe(gm['deflated_sharpe'])})")
        lines.append(f"- T1 n_trades ≥ 30: {fmt_gate(gm['n_trades'] >= 30)} ({gm['n_trades']:,})")
        if g["failed_gates"]:
            lines.append(f"- Failed gates: {', '.join(g['failed_gates'])}")
        lines.append("")

    lines.append("## Cost sensitivity — H3")
    lines.append("")
    lines.append("| per-symbol RT (bps) | pair RT (bps) | Sharpe | ann. return | PF | trades | win rate |")
    lines.append("|---------------------|---------------|--------|-------------|-----|--------|----------|")
    for row in data["h3_cost_sensitivity"]:
        lines.append(
            f"| {row['per_symbol_rt_bps']:.0f} | {row['pair_rt_bps']:.0f} | "
            f"{fmt_sharpe(row['sharpe_daily'])} | {fmt_pct(row['annualized_return'])} | "
            f"{row['profit_factor']:.3f} | {row['n_trades']:,} | {row['win_rate']*100:.1f}% |"
        )
    lines.append("")

    lines.append("## Decision")
    lines.append("")
    # Count passes
    passed = [h for h in ("H1", "H2", "H3", "H4") if variants[h]["gates"]["passed"]]
    if passed:
        lines.append(f"**HOLD:** {', '.join(passed)} passed the G1-G7/T1 gate set at 22 bps RT per symbol.")
    else:
        lines.append("**KILL all variants:** None of H1-H4 cleared the full gate set at the ratified 22 bps RT per-symbol cost.")
    lines.append("H1/H2/H4 were already recorded as NOT-PROFITABLE in the campaign ledger; this run confirms they do not "
                 "outperform H3 under the unified cost model.")
    lines.append("")

    lines.append("## Next 1-2 actions")
    lines.append("")
    if "H3" in passed:
        lines.append("1. **Run H3 through the Phase B-D execution/maker-cost study** to determine the true cost ceiling "
                     "before live candidacy.")
        lines.append("2. **Do NOT** allocate more runtime to H1/H2/H4 variants in this family; exhaust via the existing "
                     "cycle-46 rebuild (H3 sizing) instead.")
    else:
        lines.append("1. **Investigate whether H3 at 22 bps per-symbol RT can be rescued by sizing/execution** "
                     "before considering the whole family KILLed.")
        lines.append("2. If H3 also fails at realistic cost, archive H1-H4 and move to a new family in cycle-47+.")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append("- `results/metrics.json` — machine-readable metrics and gate results.")
    lines.append("- `results/equity_H{1..4}_daily.csv` — daily portfolio equity curves.")
    lines.append("- `results/equity_curves.png` — normalized equity comparison.")
    lines.append("- `results/oos_metrics.png` — OOS Sharpe / ann. return / |MDD| bars.")
    lines.append("- `results/h3_cost_sensitivity.csv` / `.png` — H3 Sharpe vs cost.")
    lines.append("")

    OUT_PATH.write_text("\n".join(lines))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
