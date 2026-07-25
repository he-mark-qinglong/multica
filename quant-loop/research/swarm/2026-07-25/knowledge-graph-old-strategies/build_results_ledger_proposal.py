#!/usr/bin/env python3
"""Proposed ledger builder with explicit CV / profitability / hold / kill states.

This is a drop-in replacement design for `scripts/build_results_ledger.py`.
Run it standalone to emit `results-ledger-proposed.md` in the same directory.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
STRATEGIES = REPO / "strategies"
OUT = Path(__file__).resolve().parent


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
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


def _get(metrics: dict | None, keys: tuple[str, ...]) -> float | None:
    if not metrics:
        return None
    for k in keys:
        if k in metrics and metrics[k] is not None:
            return float(metrics[k])
    return None


def _get_sharpe(metrics: dict | None) -> float | None:
    return _get(metrics, ("sharpe", "sharpe_daily", "agg_sharpe_in_sample"))


def _get_pf(metrics: dict | None) -> float | None:
    return _get(metrics, ("profit_factor", "agg_profit_factor"))


def _get_maxdd(metrics: dict | None) -> float | None:
    return _get(metrics, ("max_drawdown_pct", "max_drawdown", "max_dd", "agg_mdd_worst"))


def _get_n_trades(metrics: dict | None) -> int | None:
    v = _get(metrics, ("n_trades", "agg_n_trades_total"))
    return int(v) if v is not None else None


def _get_oos_sharpe(cv: dict | None) -> float | None:
    if not cv:
        return None
    for key in ("framework_oos", "oos", "walk_forward"):
        oos = cv.get(key) or {}
        if isinstance(oos, dict):
            for sub in ("oos_sharpe_mean", "mean_oos_sharpe", "sharpe"):
                if sub in oos and oos[sub] is not None:
                    return float(oos[sub])
    return None


def _get_oos_windows(cv: dict | None) -> int | None:
    if not cv:
        return None
    for key in ("framework_oos", "oos", "walk_forward"):
        oos = cv.get(key) or {}
        if isinstance(oos, dict):
            if "oos_windows" in oos and oos["oos_windows"] is not None:
                return int(oos["oos_windows"])
    return None


def _is_framework_consistent(row: dict[str, Any]) -> bool:
    """True if any framework engine reports PASS or WITHIN_TOLERANCE."""
    verdicts = [v.get("verdict", "") for v in row["frameworks"].values()]
    return any("PASS" in v or "WITHIN_TOLERANCE" in v or "W5_PASS" in v for v in verdicts)


def _verdict(row: dict[str, Any]) -> str:
    """New state machine: CV_PASS / PROFITABLE / HOLD / KILL / NO_DATA / UNTESTED."""
    if row["status"] == "GRAVEYARD":
        return "KILL"

    sharpe = row.get("sharpe_inhouse")
    ann = row.get("ann_return_inhouse")
    pf = row.get("pf_inhouse")
    maxdd = row.get("maxdd_inhouse")
    n_trades = row.get("n_trades")
    oos_sharpe = row.get("oos_sharpe")
    oos_windows = row.get("oos_windows")

    has_metrics = any(v is not None for v in (sharpe, ann, pf, maxdd, n_trades))
    if not has_metrics:
        return "UNTESTED"

    # Core gate bar
    sharpe_ok = sharpe is not None and sharpe >= 1.0
    ann_ok = ann is not None and ann >= 0.15
    maxdd_ok = maxdd is not None and abs(maxdd) < 0.25
    pf_ok = pf is not None and pf > 1.5
    trades_ok = n_trades is not None and n_trades >= 30

    # Missing a core metric => not certifiable; do not silently pass.
    core_missing = any(v is None for v in (sharpe, ann, pf, maxdd))
    if core_missing:
        return "NO_DATA"

    in_sample_pass = sharpe_ok and ann_ok and maxdd_ok and pf_ok and trades_ok

    oos_ok = oos_sharpe is not None and oos_windows is not None and oos_windows >= 3 and oos_sharpe >= 1.0

    if in_sample_pass and oos_ok:
        return "CV_PASS"
    if in_sample_pass and not oos_ok:
        return "PROFITABLE"
    if not in_sample_pass and (sharpe_ok or ann_ok):
        return "HOLD"
    return "KILL"


def scan_strategy_dir(path: Path, status: str, graveyard_family: str | None = None) -> dict[str, Any]:
    name = path.name
    config = _load_json(path / "config.json") or {}
    metrics = _load_json(path / "results" / "metrics.json")

    if name == "loid_iceberg_v4_1m_20260720":
        special = _load_json(REPO / "results" / "sma-34992" / "loid_iceberg_v4_btc_90d_metrics.json")
        if special:
            metrics = special

    row: dict[str, Any] = {
        "strategy_key": name,
        "path": str(path.relative_to(REPO)),
        "timeframe": config.get("timeframe") or _infer_timeframe(name),
        "family": _infer_family(name),
        "instruments": config.get("instruments") or config.get("symbols") or [],
        "status": status,
        "sharpe_inhouse": _get_sharpe(metrics),
        "ann_return_inhouse": _get(metrics, ("ann_return", "annualized_return", "total_return_pct")),
        "pf_inhouse": _get_pf(metrics),
        "maxdd_inhouse": _get_maxdd(metrics),
        "n_trades": _get_n_trades(metrics),
        "oos_sharpe": None,
        "oos_windows": None,
        "frameworks": {},
    }

    # Walk framework / OOS artifacts.
    for cv_path in sorted(path.glob("results/framework_cv_*.json")):
        engine = cv_path.stem.replace("framework_cv_", "")
        cv = _load_json(cv_path)
        if not cv:
            continue
        if cv.get("engine") == "cross_framework_fee_shock":
            verdict = "W5_PASS" if cv.get("W5_passed") else "W5_FAIL"
            for fw_key, col in (("freqtrade_metrics", "freqtrade"), ("backtrader_metrics", "backtrader")):
                fw = cv.get(fw_key) or {}
                sharpe = fw.get("sharpe_daily_resampled")
                if sharpe is not None and col not in row["frameworks"]:
                    row["frameworks"][col] = {"sharpe": float(sharpe), "verdict": verdict}
            continue
        row["frameworks"][engine] = {
            "sharpe": _get(cv, ("sharpe", "sharpe_daily")),
            "verdict": cv.get("w5_verdict") or cv.get("verdict") or "?",
        }

    # Also pull OOS from walk_forward.json / cpcv_metrics.json.
    wf = _load_json(path / "results" / "walk_forward.json") or _load_json(path / "results" / "cpcv_metrics.json")
    if wf:
        row["oos_sharpe"] = _get_oos_sharpe(wf)
        row["oos_windows"] = _get_oos_windows(wf)

    row["framework_consistent"] = _is_framework_consistent(row)
    row["verdict"] = _verdict(row)
    return row


def scan_all() -> list[dict[str, Any]]:
    rows = []
    for child in sorted(STRATEGIES.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if child.name in ("reports",):
            continue
        rows.append(scan_strategy_dir(child, "ACTIVE"))

    graveyard = STRATEGIES / "_graveyard"
    if graveyard.exists():
        for family_dir in sorted(graveyard.iterdir()):
            if not family_dir.is_dir():
                continue
            for child in sorted(family_dir.iterdir()):
                if not child.is_dir():
                    continue
                row = scan_strategy_dir(child, "GRAVEYARD", family_dir.name)
                rows.append(row)
    return rows


def _fmt(x: float | None, nd: int = 3) -> str:
    if x is None:
        return "—"
    return f"{x:.{nd}f}"


def write_ledger(rows: list[dict[str, Any]], out_path: Path) -> None:
    lines = [
        "# Results Ledger — quant-loop strategies (proposed state machine)",
        "",
        "> Auto-generated by `build_results_ledger_proposal.py`. Do not edit by hand.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Legend: `CV_PASS` = hard gates + OOS passed; `PROFITABLE` = in-sample gates pass but OOS not proven; "
        "`HOLD` = partial / mixed; `KILL` = hard fail or graveyard; `NO_DATA` = core metrics missing; `UNTESTED` = no results.",
        "",
        "## Active Strategies",
        "",
        "| Strategy | TF | Family | Sharpe | AnnRet | PF | maxDD | Trades | OOS Sharpe | OOS Win | Framework | Verdict |",
        "|----------|----|--------|--------|--------|----|-------|--------|------------|---------|-----------|---------|",
    ]

    for row in rows:
        if row["status"] != "ACTIVE":
            continue
        fw = row["frameworks"]
        bt = fw.get("backtrader", {}).get("sharpe")
        ft = fw.get("freqtrade", {}).get("sharpe")
        framework_flag = "Y" if row["framework_consistent"] else "N"
        lines.append(
            f"| `{row['strategy_key']}` | {row['timeframe']} | {row['family']} | "
            f"{_fmt(row['sharpe_inhouse'])} | {_fmt(row['ann_return_inhouse'])} | "
            f"{_fmt(row['pf_inhouse'], 2)} | {_fmt(row['maxdd_inhouse'])} | {_fmt(row['n_trades'], 0)} | "
            f"{_fmt(row['oos_sharpe'])} | {_fmt(row['oos_windows'], 0)} | {framework_flag} | {row['verdict']} |"
        )

    lines += [
        "",
        "## Graveyard Strategies",
        "",
        "| Strategy | Graveyard Family | TF | Sharpe | AnnRet | PF | maxDD | Trades | OOS Sharpe | OOS Win | Framework | Verdict |",
        "|----------|------------------|----|--------|--------|----|-------|--------|------------|---------|-----------|---------|",
    ]

    for row in rows:
        if row["status"] != "GRAVEYARD":
            continue
        fw = row["frameworks"]
        framework_flag = "Y" if row["framework_consistent"] else "N"
        lines.append(
            f"| `{row['strategy_key']}` | {row.get('graveyard_family', '?')} | {row['timeframe']} | "
            f"{_fmt(row['sharpe_inhouse'])} | {_fmt(row['ann_return_inhouse'])} | "
            f"{_fmt(row['pf_inhouse'], 2)} | {_fmt(row['maxdd_inhouse'])} | {_fmt(row['n_trades'], 0)} | "
            f"{_fmt(row['oos_sharpe'])} | {_fmt(row['oos_windows'], 0)} | {framework_flag} | {row['verdict']} |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[write] {out_path} ({len(rows)} strategies)")


def main() -> None:
    rows = scan_all()
    out = OUT / "results-ledger-proposed.md"
    write_ledger(rows, out)


if __name__ == "__main__":
    main()
