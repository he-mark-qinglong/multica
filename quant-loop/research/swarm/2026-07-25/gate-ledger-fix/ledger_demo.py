#!/usr/bin/env python3
"""Demonstrate proposed ledger verdicts on current strategy data.

Run with: python3 ledger_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # .../quant-loop
STRATEGIES = REPO / "strategies"


def _load_json(path: Path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _get(metrics, keys):
    for k in keys:
        if metrics and k in metrics and metrics[k] is not None:
            return metrics[k]
    return None


def _infer_timeframe(name: str) -> str:
    for tf in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "1d"):
        if f"_{tf}_" in name or name.endswith(f"_{tf}"):
            return tf
    return "?"


def _infer_family(name: str) -> str:
    parts = name.split("_")
    if parts[0] == "vpvr":
        return "_".join(parts[:3]) if len(parts) >= 3 else name
    if parts[0] in ("funding", "momentum", "trend", "vol", "bb", "pairs", "mtf", "loid"):
        return "_".join(parts[:2]) if len(parts) >= 2 else name
    return parts[0]


def _evaluate_gate(row: dict) -> str:
    s = row.get("sharpe_inhouse")
    if s is None:
        return "no-data"
    ann = row.get("ann_return_inhouse")
    mdd = row.get("maxdd_inhouse")
    pf = row.get("pf_inhouse")
    oos = row.get("oos_sharpe")
    oos_win = row.get("oos_windows")
    missing = [k for k in ("ann_return_inhouse", "maxdd_inhouse", "pf_inhouse", "oos_sharpe", "oos_windows") if row.get(k) is None]
    if missing:
        return "fail"
    if not (s >= 1.0 and ann >= 0.15 and abs(mdd) < 0.25 and pf > 1.5 and oos >= 1.0 and oos_win >= 3):
        return "fail"
    return "pass"


def _status(row: dict) -> str:
    if row["status"] == "GRAVEYARD":
        return "KILL"
    has_metrics = any(row.get(k) is not None for k in ("sharpe_inhouse", "pf_inhouse", "maxdd_inhouse", "n_trades"))
    has_frameworks = bool(row.get("frameworks"))
    if not has_metrics and not has_frameworks:
        return "UNTESTED"
    verdicts = [v.get("verdict", "") for v in row.get("frameworks", {}).values()]
    w5_pass = any("PASS" in v or "WITHIN_TOLERANCE" in v for v in verdicts)
    w5_kill = any("AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v for v in verdicts)
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


def scan_strategy_dir(path: Path) -> dict:
    name = path.name
    config = _load_json(path / "config.json") or {}
    metrics = _load_json(path / "results" / "metrics.json")

    if name == "loid_iceberg_v4_1m_20260720":
        special = _load_json(REPO / "results" / "sma-34992" / "loid_iceberg_v4_btc_90d_metrics.json")
        if special:
            metrics = special

    row = {
        "strategy_key": name,
        "path": str(path.relative_to(REPO)),
        "timeframe": config.get("timeframe") or _infer_timeframe(name),
        "family": _infer_family(name),
        "status": "ACTIVE",
        "sharpe_inhouse": _get(metrics, ("sharpe", "sharpe_daily", "agg_sharpe_in_sample")),
        "ann_return_inhouse": _get(metrics, ("ann_return", "annualized_return", "annualized_return_daily", "avg_pair_annualized_return_daily")),
        "pf_inhouse": _get(metrics, ("profit_factor", "agg_profit_factor", "portfolio", "profit_factor")),
        "maxdd_inhouse": _get(metrics, ("max_drawdown_pct", "max_drawdown", "max_dd", "agg_mdd_worst")),
        "n_trades": _get(metrics, ("n_trades", "agg_n_trades_total")),
        "oos_sharpe": _get(metrics, ("oos_sharpe",)),
        "oos_windows": _get(metrics, ("oos_windows",)),
        "frameworks": {},
    }
    # nested profit_factor fallback for mtf_xs_pairs portfolio object
    if row["pf_inhouse"] is None and isinstance(metrics, dict) and isinstance(metrics.get("portfolio"), dict):
        row["pf_inhouse"] = metrics["portfolio"].get("profit_factor")
    if row["maxdd_inhouse"] is None and isinstance(metrics, dict) and isinstance(metrics.get("portfolio"), dict):
        row["maxdd_inhouse"] = metrics["portfolio"].get("max_drawdown_pct")
    if row["ann_return_inhouse"] is None and isinstance(metrics, dict) and isinstance(metrics.get("portfolio"), dict):
        row["ann_return_inhouse"] = metrics["portfolio"].get("annualized_return_daily")

    for cv_path in sorted(path.glob("results/framework_cv_*.json")):
        engine = cv_path.stem.replace("framework_cv_", "")
        cv = _load_json(cv_path)
        if cv and cv.get("engine") == "cross_framework_fee_shock":
            verdict = "W5_PASS" if cv.get("W5_passed") else "W5_FAIL"
            for fw_key, col in (("freqtrade_metrics", "freqtrade"), ("backtrader_metrics", "backtrader")):
                fw = cv.get(fw_key) or {}
                sharpe = fw.get("sharpe_daily_resampled")
                if sharpe is not None and col not in row["frameworks"]:
                    row["frameworks"][col] = {"sharpe": float(sharpe), "verdict": verdict}
            continue
        row["frameworks"][engine] = {
            "sharpe": _get(cv, ("sharpe", "oos_sharpe_mean")),
            "verdict": cv.get("w5_verdict") or cv.get("verdict") or "?",
        }
    return row


def scan_all() -> list[dict]:
    rows = []
    for child in sorted(STRATEGIES.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if child.name in ("reports",):
            continue
        rows.append(scan_strategy_dir(child))

    graveyard = STRATEGIES / "_graveyard"
    if graveyard.exists():
        for family_dir in sorted(graveyard.iterdir()):
            if not family_dir.is_dir():
                continue
            for child in sorted(family_dir.iterdir()):
                if not child.is_dir():
                    continue
                row = scan_strategy_dir(child)
                row["status"] = "GRAVEYARD"
                row["graveyard_family"] = family_dir.name
                rows.append(row)
    return rows


def _fmt(x, nd=3):
    if x is None:
        return "—"
    return f"{x:.{nd}f}"


def main():
    rows = scan_all()
    counts = {"CV_PASS": 0, "PROFITABLE": 0, "HOLD": 0, "KILL": 0, "UNTESTED": 0}
    print("| Strategy | Gate | Ledger Verdict | Old Verdict | Notes |")
    print("|----------|------|----------------|-------------|-------|")
    old_verdicts = {}
    # rough mapping of old verdict for comparison
    for row in rows:
        if row["status"] == "GRAVEYARD":
            old_verdicts[row["strategy_key"]] = "KILL"
        elif not row["frameworks"]:
            old_verdicts[row["strategy_key"]] = "UNTESTED"
        else:
            verdicts = [v["verdict"] for v in row["frameworks"].values()]
            if any("PASS" in v or "WITHIN_TOLERANCE" in v for v in verdicts):
                old_verdicts[row["strategy_key"]] = "PASS"
            elif any("AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v for v in verdicts):
                old_verdicts[row["strategy_key"]] = "KILL"
            else:
                old_verdicts[row["strategy_key"]] = "HOLD"

    for row in rows:
        verdict = _status(row)
        counts[verdict] = counts.get(verdict, 0) + 1
        gate = _evaluate_gate(row)
        old = old_verdicts.get(row["strategy_key"], "?")
        notes = []
        if verdict == "CV_PASS" and old == "PASS":
            notes.append("old PASS split: framework OK but gate not met")
        elif verdict == "PROFITABLE" and old == "PASS":
            notes.append("old PASS confirmed as profitable")
        elif verdict == "KILL" and old == "PASS":
            notes.append("old PASS demoted to KILL (gate fail or W5 kill)")
        print(f"| `{row['strategy_key']}` | {gate} | {verdict} | {old} | {', '.join(notes)} |")

    print("\nVerdict counts:", counts)
    out_path = Path(__file__).parent / "ledger_demo_counts.json"
    out_path.write_text(json.dumps(counts, indent=2) + "\n")
    print(f"[wrote] {out_path}")


if __name__ == "__main__":
    main()
