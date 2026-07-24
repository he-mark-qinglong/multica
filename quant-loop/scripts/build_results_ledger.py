#!/usr/bin/env python3
"""Build the top-level results-ledger.md from strategy results.

Scans strategies/ and strategies/_graveyard/ for metrics.json and
framework_cv_*.json files, then emits a unified ledger.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
STRATEGIES = REPO / "strategies"


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


def _get_sharpe(metrics: dict | None) -> float | None:
    if not metrics:
        return None
    for key in ("sharpe", "sharpe_daily", "agg_sharpe_in_sample"):
        if key in metrics and metrics[key] is not None:
            return float(metrics[key])
    return None


def _get_pf(metrics: dict | None) -> float | None:
    if not metrics:
        return None
    for key in ("profit_factor", "agg_profit_factor"):
        if key in metrics and metrics[key] is not None:
            return float(metrics[key])
    return None


def _get_maxdd(metrics: dict | None) -> float | None:
    if not metrics:
        return None
    for key in ("max_drawdown_pct", "max_dd", "agg_mdd_worst"):
        if key in metrics and metrics[key] is not None:
            return float(metrics[key])
    return None


def _get_n_trades(metrics: dict | None) -> int | None:
    if not metrics:
        return None
    for key in ("n_trades", "agg_n_trades_total"):
        if key in metrics and metrics[key] is not None:
            return int(metrics[key])
    return None


def _get_framework_sharpe(cv: dict | None, engine: str) -> float | None:
    if not cv:
        return None
    fw = cv.get("framework") or cv.get(engine) or {}
    if not isinstance(fw, dict):
        return None
    sharpe = fw.get("sharpe")
    if sharpe is not None:
        return float(sharpe)
    oos = cv.get("framework_oos") or {}
    if not isinstance(oos, dict):
        return None
    sharpe = oos.get("oos_sharpe_mean")
    if sharpe is not None:
        return float(sharpe)
    return None


def _get_verdict(cv: dict | None) -> str:
    if not cv:
        return "?"
    return cv.get("w5_verdict") or cv.get("verdict") or "?"


def scan_strategy_dir(path: Path) -> dict[str, Any]:
    name = path.name
    config = _load_json(path / "config.json") or {}
    metrics = _load_json(path / "results" / "metrics.json")

    row = {
        "strategy_key": name,
        "path": str(path.relative_to(REPO)),
        "timeframe": config.get("timeframe") or _infer_timeframe(name),
        "family": _infer_family(name),
        "instruments": config.get("instruments") or config.get("symbols") or [],
        "status": "ACTIVE",
        "sharpe_inhouse": _get_sharpe(metrics),
        "pf_inhouse": _get_pf(metrics),
        "maxdd_inhouse": _get_maxdd(metrics),
        "n_trades": _get_n_trades(metrics),
        "frameworks": {},
    }

    for cv_path in sorted(path.glob("results/framework_cv_*.json")):
        engine = cv_path.stem.replace("framework_cv_", "")
        cv = _load_json(cv_path)
        row["frameworks"][engine] = {
            "sharpe": _get_framework_sharpe(cv, engine),
            "verdict": _get_verdict(cv),
        }

    return row


def scan_all() -> list[dict[str, Any]]:
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


def _fmt(x: float | None, nd: int = 3) -> str:
    if x is None:
        return "—"
    return f"{x:.{nd}f}"


def _status(row: dict[str, Any]) -> str:
    if row["status"] == "GRAVEYARD":
        return "KILL"
    if not row["frameworks"]:
        return "UNTESTED"
    verdicts = [v["verdict"] for v in row["frameworks"].values()]
    if any("PASS" in v or "WITHIN_TOLERANCE" in v for v in verdicts):
        return "PASS"
    if any("AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v for v in verdicts):
        return "KILL"
    return "HOLD"


def write_ledger(rows: list[dict[str, Any]], out_path: Path) -> None:
    lines = [
        "# Results Ledger — quant-loop strategies",
        "",
        "> Auto-generated by `scripts/build_results_ledger.py`. Do not edit by hand.",
        "",
        f"Generated: {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}",
        "",
        "## Active Strategies",
        "",
        "| Strategy | TF | Family | Sharpe(in-house) | PF | maxDD | Trades | BT Sharpe | FT Sharpe | VBT Sharpe | Verdict |",
        "|----------|----|--------|------------------|----|-------|--------|-----------|-----------|-----------|---------|",
    ]

    for row in rows:
        if row["status"] != "ACTIVE":
            continue
        bt = row["frameworks"].get("backtrader", {}).get("sharpe")
        ft = row["frameworks"].get("freqtrade", {}).get("sharpe")
        vbt = row["frameworks"].get("vectorbt", {}).get("sharpe")
        verdict = _status(row)
        lines.append(
            f"| `{row['strategy_key']}` | {row['timeframe']} | {row['family']} | "
            f"{_fmt(row['sharpe_inhouse'])} | {_fmt(row['pf_inhouse'], 2)} | "
            f"{_fmt(row['maxdd_inhouse'])} | {_fmt(row['n_trades'], 0)} | "
            f"{_fmt(bt)} | {_fmt(ft)} | {_fmt(vbt)} | {verdict} |"
        )

    lines += [
        "",
        "## Graveyard Strategies",
        "",
        "| Strategy | Graveyard Family | TF | Sharpe(in-house) | PF | maxDD | Trades | BT Sharpe | FT Sharpe | VBT Sharpe | Verdict |",
        "|----------|------------------|----|------------------|----|-------|--------|-----------|-----------|-----------|---------|",
    ]

    for row in rows:
        if row["status"] != "GRAVEYARD":
            continue
        bt = row["frameworks"].get("backtrader", {}).get("sharpe")
        ft = row["frameworks"].get("freqtrade", {}).get("sharpe")
        vbt = row["frameworks"].get("vectorbt", {}).get("sharpe")
        verdict = _status(row)
        lines.append(
            f"| `{row['strategy_key']}` | {row.get('graveyard_family', '?')} | {row['timeframe']} | "
            f"{_fmt(row['sharpe_inhouse'])} | {_fmt(row['pf_inhouse'], 2)} | "
            f"{_fmt(row['maxdd_inhouse'])} | {_fmt(row['n_trades'], 0)} | "
            f"{_fmt(bt)} | {_fmt(ft)} | {_fmt(vbt)} | {verdict} |"
        )

    out_path.write_text("\n".join(lines) + "\n")
    print(f"[write] {out_path} ({len(rows)} strategies)")


def main() -> None:
    rows = scan_all()
    out = REPO / "results-ledger.md"
    write_ledger(rows, out)


if __name__ == "__main__":
    main()
