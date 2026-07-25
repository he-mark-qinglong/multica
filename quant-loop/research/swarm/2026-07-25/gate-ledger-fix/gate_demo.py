#!/usr/bin/env python3
"""Demonstrate current vs proposed gate behavior for gate-ledger-fix.

Run with: python3 gate_demo.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Metrics:
    sharpe: Optional[float] = None
    ann_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    profit_factor: Optional[float] = None
    oos_sharpe: Optional[float] = None
    oos_windows: Optional[int] = None


@dataclass
class RuleResult:
    rule: str
    op: str
    threshold: float
    actual: Optional[float] = None
    pass_: bool = field(default=False, metadata={"name": "pass"})
    note: str = ""

    def as_dict(self):
        return {
            "rule": self.rule,
            "op": self.op,
            "threshold": self.threshold,
            "actual": self.actual,
            "pass": self.pass_,
            "note": self.note or None,
        }


def compare(op: str, actual: float, threshold: float) -> bool:
    if op == ">=":
        return actual >= threshold
    if op == ">":
        return actual > threshold
    if op == "<":
        return actual < threshold
    return False


RULES = [
    ("sharpe", ">=", 1.0, lambda m: m.sharpe),
    ("ann_return", ">=", 0.15, lambda m: m.ann_return),
    ("max_drawdown", "<", 0.25, lambda m: abs(m.max_drawdown) if m.max_drawdown is not None else None),
    ("profit_factor", ">", 1.5, lambda m: m.profit_factor),
    ("oos_windows", ">=", 3, lambda m: float(m.oos_windows) if m.oos_windows is not None else None),
    ("oos_sharpe", ">=", 1.0, lambda m: m.oos_sharpe),
]


def evaluate_current(m: Metrics):
    """Mirror of current gate.Evaluate (skip missing metrics)."""
    detail = []
    failed = False
    for name, op, threshold, extractor in RULES:
        actual = extractor(m)
        if actual is None:
            detail.append(RuleResult(name, op, threshold, None, True, "skipped: no data"))
        else:
            passed = compare(op, actual, threshold)
            detail.append(RuleResult(name, op, threshold, actual, passed, ""))
            if not passed:
                failed = True
    if failed:
        status = "fail"
    elif m.sharpe is not None:
        status = "pass"
    else:
        status = "no-data"
    return status, detail


def evaluate_proposed(m: Metrics):
    """Mirror of proposed strict gate.Evaluate."""
    detail = []
    failed = False
    sharpe_missing = False
    for name, op, threshold, extractor in RULES:
        actual = extractor(m)
        if actual is None:
            if name == "sharpe":
                sharpe_missing = True
                detail.append(RuleResult(name, op, threshold, None, False, "missing required metric"))
                failed = True
            else:
                detail.append(RuleResult(name, op, threshold, None, False, "missing required metric"))
                failed = True
        else:
            passed = compare(op, actual, threshold)
            detail.append(RuleResult(name, op, threshold, actual, passed, ""))
            if not passed:
                failed = True
    if failed and sharpe_missing:
        status = "no-data"
    elif failed:
        status = "fail"
    elif sharpe_missing:
        status = "no-data"
    else:
        status = "pass"
    return status, detail


def main():
    cases = [
        ("vpvr_stable_depeg_p3opt_091 (sharpe only)", Metrics(sharpe=31.7)),
        ("H3 baseline as-uploaded (no PF)", Metrics(sharpe=1.35, ann_return=0.25, max_drawdown=-0.137, oos_sharpe=2.77, oos_windows=7)),
        ("H3 baseline with PF", Metrics(sharpe=1.35, ann_return=0.25, max_drawdown=-0.137, profit_factor=1.22, oos_sharpe=2.77, oos_windows=7)),
        ("strong candidate", Metrics(sharpe=1.8, ann_return=0.42, max_drawdown=-0.12, profit_factor=1.9, oos_sharpe=1.3, oos_windows=4)),
        ("overfit", Metrics(sharpe=5.72, ann_return=2.40, max_drawdown=0.05, profit_factor=4.1, oos_sharpe=0.61, oos_windows=2)),
        ("empty", Metrics()),
    ]

    out = []
    for label, m in cases:
        cur_status, cur_detail = evaluate_current(m)
        prop_status, prop_detail = evaluate_proposed(m)
        out.append({
            "case": label,
            "current_status": cur_status,
            "proposed_status": prop_status,
            "current_detail": [d.as_dict() for d in cur_detail],
            "proposed_detail": [d.as_dict() for d in prop_detail],
        })
        print(f"\n=== {label} ===")
        print(f"current: {cur_status:10} -> proposed: {prop_status}")
        for c, p in zip(cur_detail, prop_detail):
            print(f"  {c.rule:15} current {'PASS' if c.pass_ else 'FAIL':4} | proposed {'PASS' if p.pass_ else 'FAIL':4}  {p.note}")

    path = "/Users/mark/multica/quant-loop/research/swarm/2026-07-25/gate-ledger-fix/gate_demo_results.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[wrote] {path}")


if __name__ == "__main__":
    main()
