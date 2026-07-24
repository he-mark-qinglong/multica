#!/usr/bin/env python3
"""Compare high-frequency strategy candidates.

Reads results-ledger.md and produces a focused comparison of 1m/5m strategies.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "results-ledger.md"
OUT = REPO / "results" / "hf_candidates_comparison.md"


def parse_ledger() -> list[dict[str, str]]:
    lines = LEDGER.read_text().splitlines()
    rows = []
    in_table = False
    headers: list[str] = []
    for line in lines:
        if line.startswith("| Strategy |"):
            in_table = True
            headers = [h.strip() for h in line.strip("|").split("|")]
            continue
        if in_table and line.startswith("|----------"):
            continue
        if in_table and line.startswith("| `"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            row = dict(zip(headers, cells))
            rows.append(row)
        elif in_table and not line.startswith("|"):
            in_table = False
    return rows


def _to_float(s: str) -> float | None:
    if s in ("—", "?", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def main() -> None:
    rows = parse_ledger()
    hf = [r for r in rows if r.get("TF") in ("1m", "5m")]

    lines = [
        "# High-Frequency Strategy Candidates Comparison",
        "",
        "> Focus: 1m / 5m timeframe strategies. Generated from results-ledger.md.",
        "",
        "## Summary",
        "",
        f"- Total HF strategies evaluated: {len(hf)}",
        f"- PASS: {sum(1 for r in hf if r['Verdict'] == 'PASS')}",
        f"- HOLD: {sum(1 for r in hf if r['Verdict'] == 'HOLD')}",
        f"- KILL: {sum(1 for r in hf if r['Verdict'] == 'KILL')}",
        f"- UNTESTED: {sum(1 for r in hf if r['Verdict'] == 'UNTESTED')}",
        "",
        "## Detailed Comparison",
        "",
        "| Strategy | TF | Status | Sharpe(in-house) | BT Sharpe | FT Sharpe | VBT Sharpe | PF | maxDD | Trades | Verdict |",
        "|----------|----|--------|------------------|-----------|-----------|------------|----|-------|--------|---------|",
    ]

    for r in hf:
        status = "ACTIVE" if "Graveyard Family" not in r else f"GRAVEYARD({r['Graveyard Family']})"
        lines.append(
            f"| `{r['Strategy']}` | {r['TF']} | {status} | "
            f"{r['Sharpe(in-house)']} | {r['BT Sharpe']} | {r['FT Sharpe']} | {r['VBT Sharpe']} | "
            f"{r['PF']} | {r['maxDD']} | {r['Trades']} | {r['Verdict']} |"
        )

    # Add key insights
    lines += [
        "",
        "## Key Insights",
        "",
        "1. **Cost-cap dominates 1m/5m klines strategies**: All graveyarded 1m/5m strategies show negative framework CV Sharpe or in-house Sharpe that doesn't cover costs.",
        "2. **mtf_xs_pairs H3 is the only positive-expectation HF candidate**: Multi-timeframe (1m entry + 15m sizing + 2h regime) is the proven template.",
        "3. **loid_iceberg_v4 is the only untested HF axis with real data**: aggTrades order flow remains unproven but has exclusive data asset.",
        "4. **bb_reversion_rsi shows classic high-Sharpe illusion**: In-house Sharpe 2.0+ but near-zero total return after costs.",
        "",
        "## Next Actions",
        "",
        "- Complete loid_iceberg_v4 90d parameter scan (Phase E).",
        "- Evaluate H3 variants (H1/H2/H4) with the unified Phase B-D pipeline.",
        "- Consider T10 sub-taker execution research to unlock microstructure edges.",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print(f"[write] {OUT}")


if __name__ == "__main__":
    main()
