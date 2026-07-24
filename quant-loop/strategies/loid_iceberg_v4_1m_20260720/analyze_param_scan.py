#!/usr/bin/env python3
"""Analyze loid_iceberg_v4 parameter scan results."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "results" / "sma-34992" / "param_scan" / "param_scan_results.json"
OUT = REPO / "results" / "sma-34992" / "param_scan_analysis.md"


def main() -> None:
    with open(RESULTS) as f:
        results = json.load(f)

    df = pd.DataFrame([r for r in results if "error" not in r])
    if len(df) == 0:
        print("No successful results")
        return

    # Filter to reasonable trade count
    df = df[df["n_trades"] >= 30]

    lines = [
        "# loid_iceberg_v4 Parameter Scan Analysis",
        "",
        f"Total combinations: {len(results)}",
        f"Successful: {len(df)}",
        f"With ≥30 trades: {len(df)}",
        "",
        "## Top 10 by Sharpe",
        "",
        "| Label | Trades | Sharpe | Ann Return | Max DD | PF | Large | Whale | Iceberg |",
        "|-------|--------|--------|------------|--------|----|-------|-------|---------|",
    ]

    top = df.nlargest(10, "sharpe")
    for _, r in top.iterrows():
        lines.append(
            f"| {r['label']} | {r['n_trades']} | {r['sharpe']:.3f} | {r['ann_return']:.3f} | "
            f"{r['max_dd']:.3f} | {r['pf']:.3f} | {r['n_large']} | {r['n_whale']} | {r['n_iceberg']} |"
        )

    lines += [
        "",
        "## Top 10 by Profit Factor",
        "",
        "| Label | Trades | Sharpe | Ann Return | Max DD | PF |",
        "|-------|--------|--------|------------|--------|----|",
    ]

    top_pf = df.nlargest(10, "pf")
    for _, r in top_pf.iterrows():
        lines.append(
            f"| {r['label']} | {r['n_trades']} | {r['sharpe']:.3f} | {r['ann_return']:.3f} | "
            f"{r['max_dd']:.3f} | {r['pf']:.3f} |"
        )

    # Best by composite rule
    lines += [
        "",
        "## Best per Composite Rule",
        "",
        "| Rule | Label | Trades | Sharpe | PF |",
        "|------|-------|--------|--------|----|",
    ]
    for rule in ["1min", "5min", "15min"]:
        sub = df[df["composite_rule"] == rule]
        if len(sub):
            best = sub.loc[sub["sharpe"].idxmax()]
            lines.append(f"| {rule} | {best['label']} | {best['n_trades']} | {best['sharpe']:.3f} | {best['pf']:.3f} |")

    # Verdict
    best_overall = df.loc[df["sharpe"].idxmax()]
    lines += [
        "",
        "## Verdict",
        "",
        f"- Best overall: `{best_overall['label']}` — Sharpe {best_overall['sharpe']:.3f}, "
        f"ann {best_overall['ann_return']:.3f}, maxDD {best_overall['max_dd']:.3f}, PF {best_overall['pf']:.3f}",
        f"- Trades: {best_overall['n_trades']}",
        "",
        "**Gate check**: mean OOS Sharpe ≥ 0.5? worst-fold ≥ 0.0? DSR > 0?",
        "- These results are full-period in-sample. CPCV walk-forward is required for ship eligibility.",
    ]

    OUT.write_text("\n".join(lines) + "\n")
    print(f"[write] {OUT}")


if __name__ == "__main__":
    main()
