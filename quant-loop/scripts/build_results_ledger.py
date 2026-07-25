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
    for key in ("max_drawdown_pct", "max_drawdown", "max_dd", "agg_mdd_worst"):
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

    # Special case: loid_iceberg_v4 results live in results/sma-34992/
    if name == "loid_iceberg_v4_1m_20260720":
        special = _load_json(REPO / "results" / "sma-34992" / "loid_iceberg_v4_btc_90d_metrics.json")
        if special:
            metrics = special

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
        if cv and cv.get("engine") == "cross_framework_fee_shock":
            # Fee-shock CV schema (e.g. mtf_xs_pairs H3): in-house equity
            # replayed under each framework's round-trip fee model, not a
            # real engine replay. Map per-framework sharpe onto the standard
            # columns so the ledger shows the numbers with a W5 verdict.
            verdict = "W5_PASS" if cv.get("W5_passed") else "W5_FAIL"
            for fw_key, col in (("freqtrade_metrics", "freqtrade"), ("backtrader_metrics", "backtrader")):
                fw = cv.get(fw_key) or {}
                sharpe = fw.get("sharpe_daily_resampled")
                # Multiple sizing variants (atr_mult_1_00 / 1_25) map onto the
                # same column — keep the first (1_00, the shipped winner).
                if sharpe is not None and col not in row["frameworks"]:
                    row["frameworks"][col] = {"sharpe": float(sharpe), "verdict": verdict}
            continue
        row["frameworks"][engine] = {
            "sharpe": _get_framework_sharpe(cv, engine),
            "verdict": _get_verdict(cv),
        }

    # Independent verdict components: framework agreement vs in-house
    # profitability. A LIVE candidate (ledger verdict PASS) requires BOTH.
    row["framework_consistent"] = _framework_consistent(row)
    row["profitable"] = _profitable(row)

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


def _fmt_bool(b: bool) -> str:
    return "yes" if b else "no"


def _framework_consistent(row: dict[str, Any]) -> bool:
    """True when any framework cross-validation verdict agrees with the
    in-house result (W5 PASS / within tolerance). Independent of whether the
    in-house metrics themselves are profitable."""
    verdicts = [v["verdict"] for v in row["frameworks"].values()]
    return any("PASS" in v or "WITHIN_TOLERANCE" in v for v in verdicts)


def _framework_killed(row: dict[str, Any]) -> bool:
    verdicts = [v["verdict"] for v in row["frameworks"].values()]
    return any("AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v for v in verdicts)


# In-house profitability bar (mirrors G1/G3/G4/T1 in _shared/gates/enforce.py).
# A missing required field means NOT profitable — missing data is never a pass.
_PROFITABILITY_REQUIRED = ("sharpe_inhouse", "pf_inhouse", "maxdd_inhouse", "n_trades")


def _profitable(row: dict[str, Any]) -> bool:
    """True only when all required in-house metrics are present AND pass the
    profitability bar: sharpe >= 1.0, PF > 1.5, |maxDD| < 0.25, trades >= 30."""
    if any(row.get(k) is None for k in _PROFITABILITY_REQUIRED):
        return False
    return (
        row["sharpe_inhouse"] >= 1.0
        and row["pf_inhouse"] > 1.5
        and abs(row["maxdd_inhouse"]) < 0.25
        and row["n_trades"] >= 30
    )


def _status(row: dict[str, Any]) -> str:
    """Ledger verdict. Framework consistency and profitability are recorded
    independently (framework_consistent / profitable); a strategy is a LIVE
    candidate (PASS) only when BOTH hold."""
    if row["status"] == "GRAVEYARD":
        return "KILL"
    has_metrics = any(row.get(k) is not None for k in _PROFITABILITY_REQUIRED)
    if not has_metrics and not row["frameworks"]:
        return "UNTESTED"
    consistent = _framework_consistent(row)
    if _framework_killed(row) and not consistent:
        return "KILL"
    if consistent:
        return "PASS" if _profitable(row) else "CV_PASS"
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
        "| Strategy | TF | Family | Sharpe(in-house) | PF | maxDD | Trades | BT Sharpe | FT Sharpe | VBT Sharpe | FW-Consistent | Profitable | Verdict |",
        "|----------|----|--------|------------------|----|-------|--------|-----------|-----------|-----------|---------------|------------|---------|",
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
            f"{_fmt(bt)} | {_fmt(ft)} | {_fmt(vbt)} | "
            f"{_fmt_bool(row['framework_consistent'])} | {_fmt_bool(row['profitable'])} | {verdict} |"
        )

    lines += [
        "",
        "## Graveyard Strategies",
        "",
        "| Strategy | Graveyard Family | TF | Sharpe(in-house) | PF | maxDD | Trades | BT Sharpe | FT Sharpe | VBT Sharpe | FW-Consistent | Profitable | Verdict |",
        "|----------|------------------|----|------------------|----|-------|--------|-----------|-----------|-----------|---------------|------------|---------|",
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
            f"{_fmt(bt)} | {_fmt(ft)} | {_fmt(vbt)} | "
            f"{_fmt_bool(row['framework_consistent'])} | {_fmt_bool(row['profitable'])} | {verdict} |"
        )

    out_path.write_text("\n".join(lines) + "\n")
    print(f"[write] {out_path} ({len(rows)} strategies)")


def _kill_fields(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """(kill_reason, kill_evidence) for rows whose ledger verdict is KILL.

    Mirrors the two KILL branches of _status(): graveyard archival and
    framework-driven kill (AUTO-ARCHIVE / NOT-PROFITABLE without any
    PASS/WITHIN_TOLERANCE). Returns (None, None) for non-KILL rows.
    """
    if _status(row) != "KILL":
        return None, None
    if row["status"] == "GRAVEYARD":
        family = row.get("graveyard_family", "?")
        return f"archived to strategies/_graveyard/{family}", row["path"]
    for engine, fw in row["frameworks"].items():
        v = fw.get("verdict") or ""
        if "AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v:
            return (f"framework verdict {v} ({engine})",
                    f"{row['path']}/results/framework_cv_{engine}.json")
    return "ledger verdict KILL", row["path"]


def write_ledger_json(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Machine-readable sidecar for publishers merging verdict fields into
    metrics blobs (see quant-loop/docs/metrics-blob-convention.md)."""
    import datetime
    payload = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "strategies": [
            {
                "strategy_key": row["strategy_key"],
                "verdict": _status(row),
                "kill_reason": _kill_fields(row)[0],
                "kill_evidence": _kill_fields(row)[1],
            }
            for row in rows
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[write] {out_path} ({len(rows)} strategies)")


def main() -> None:
    rows = scan_all()
    out = REPO / "results-ledger.md"
    write_ledger(rows, out)
    write_ledger_json(rows, REPO / "results-ledger.json")


if __name__ == "__main__":
    main()
