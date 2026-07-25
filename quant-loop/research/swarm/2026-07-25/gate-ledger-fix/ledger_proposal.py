#!/usr/bin/env python3
"""Proposal for build_results_ledger.py verdict redesign.

This file contains the proposed replacement for the _status() function and
ledger table schema in /Users/mark/multica/quant-loop/scripts/build_results_ledger.py.

Goal: disentangle "cross-validation passed" (framework agreement) from
"profitable" (in-house metrics meet the gate) and from "kill".
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Proposed new table headers (replace the two existing markdown tables)
# ---------------------------------------------------------------------------
ACTIVE_HEADER = (
    "| Strategy | TF | Family | Gate | Sharpe(in-house) | PF | maxDD | Trades | "
    "AnnReturn | OOS Sharpe | OOS Win | CV Verdict | Ledger Verdict |"
)
ACTIVE_SEP = (
    "|----------|----|--------|------|------------------|----|-------|--------|"
    "-----------|------------|---------|------------|----------------|"
)

GRAVEYARD_HEADER = (
    "| Strategy | Graveyard Family | TF | Gate | Sharpe(in-house) | PF | maxDD | Trades | "
    "AnnReturn | OOS Sharpe | OOS Win | CV Verdict | Ledger Verdict |"
)
GRAVEYARD_SEP = (
    "|----------|------------------|----|------|------------------|----|-------|--------|"
    "-----------|------------|---------|------------|----------------|"
)


# ---------------------------------------------------------------------------
# Proposed gate evaluation (local mirror of server/internal/gate strict rules)
# ---------------------------------------------------------------------------
def _evaluate_gate(row: dict) -> str:
    """Return pass / fail / no-data using the strict gate-ledger-fix rules."""
    s = row.get("sharpe_inhouse")
    ann = row.get("sharpe_inhouse")  # placeholder; ledger schema would store ann_return
    mdd = row.get("maxdd_inhouse")
    pf = row.get("pf_inhouse")
    oos = None  # placeholder; ledger schema would store oos_sharpe
    oos_win = None  # placeholder; ledger schema would store oos_windows

    # In a real patch, build_results_ledger.py would parse ann_return,
    # oos_sharpe and oos_windows from metrics.json exactly like it parses
    # sharpe / pf / maxdd today.
    if s is None:
        return "no-data"
    if ann is None or mdd is None or pf is None or oos is None or oos_win is None:
        return "fail"
    if not (s >= 1.0 and ann >= 0.15 and abs(mdd) < 0.25 and pf > 1.5 and oos >= 1.0 and oos_win >= 3):
        return "fail"
    return "pass"


# ---------------------------------------------------------------------------
# Proposed replacement for build_results_ledger._status()
# ---------------------------------------------------------------------------
def _status(row: dict) -> str:
    """Map a scanned strategy row to a ledger verdict.

    Verdicts:
      KILL        - graveyard, or explicit AUTO-ARCHIVE / NOT-PROFITABLE, or
                    gate hard-fail with no redeeming framework signal.
      CV_PASS     - cross-framework agreement (W5 passed / within tolerance)
                    but in-house returns do NOT yet meet the profitability gate.
      PROFITABLE  - cross-framework agreement AND in-house metrics pass the
                    strict gate (sharpe>=1, ann>=0.15, |mdd|<0.25, PF>1.5,
                    oos_sharpe>=1, oos_windows>=3).
      HOLD        - data exists but neither clean pass nor clear kill; needs
                    more evidence.
      UNTESTED    - no metrics and no framework output at all.
    """
    if row["status"] == "GRAVEYARD":
        return "KILL"

    has_metrics = any(
        row.get(k) is not None
        for k in ("sharpe_inhouse", "pf_inhouse", "maxdd_inhouse", "n_trades")
    )
    has_frameworks = bool(row.get("frameworks"))

    if not has_metrics and not has_frameworks:
        return "UNTESTED"

    verdicts = [v.get("verdict", "") for v in row.get("frameworks", {}).values()]
    w5_pass = any("PASS" in v or "WITHIN_TOLERANCE" in v for v in verdicts)
    w5_kill = any("AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v for v in verdicts)

    # Gate evaluation using in-house metrics only.
    gate = _evaluate_gate(row)

    if w5_kill and not w5_pass:
        return "KILL"

    if w5_pass and gate == "pass":
        return "PROFITABLE"

    if w5_pass and gate != "pass":
        return "CV_PASS"

    if gate == "fail" and not w5_pass:
        return "KILL"

    return "HOLD"


# ---------------------------------------------------------------------------
# Summary of required build_results_ledger.py changes
# ---------------------------------------------------------------------------
REQUIRED_CHANGES = """
1. Parsing:
   - Extend _load_json / helper getters to also read ann_return, oos_sharpe,
     oos_windows from metrics.json (same pattern as existing sharpe/pf/maxdd).

2. Row schema:
   - Add ann_return, oos_sharpe, oos_windows to each row.

3. Status function:
   - Replace the existing _status() with the one above.

4. Output tables:
   - Replace Active/Graveyard headers with ACTIVE_HEADER / GRAVEYARD_HEADER.
   - Add a "Gate" column populated from _evaluate_gate(row).
   - Rename the final column from "Verdict" to "Ledger Verdict".

5. No changes to scanned file locations or graveyard traversal.
"""

if __name__ == "__main__":
    print("Proposed ledger verdict state machine:")
    print(REQUIRED_CHANGES)
