#!/usr/bin/env python3
"""Scan quant-loop/strategies and build a structured inventory of reusable modules.

Outputs:
  - strategy_inventory.json
  - strategy_inventory.csv
  - strategy_inventory.md
  - module_catalog.md (categorized: signal / risk / execution / cost / evaluation)
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

REPO = Path("/Users/mark/multica/quant-loop")
STRATEGIES = REPO / "strategies"
SHARED = REPO / "_shared"
OUT = Path(__file__).resolve().parent

SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules", ".tox", ".venv"}

ENTRY_HINTS = (
    "run_backtest.py",
    "strategy.py",
    "main.py",
    "smoke_backtest.py",
    "smoke_test.py",
    "backtest.py",
)

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "signal": (
        "signal",
        "feature",
        "indicator",
        "build_signals",
        "combine_signals",
        "vpvr",
        "funding",
        "zscore",
        "regime",
        "candle",
        "volume",
        "orderbook",
        "iceberg",
        "cointegration",
        "momentum",
        "trend",
        "breakout",
        "reversion",
        "mean_reversion",
        "vpvr_levels",
    ),
    "risk": (
        "risk",
        "sizing",
        "position",
        "stop",
        "drawdown",
        "portfolio",
        "volatility",
        "atr",
    ),
    "execution": (
        "execution",
        "fill",
        "order",
        "routing",
        "twap",
        "paper_trading",
        "fill_engine",
    ),
    "cost": (
        "cost",
        "fee",
        "slippage",
        "commission",
        "maker",
        "taker",
    ),
    "evaluation": (
        "metric",
        "compute_metrics",
        "validation",
        "validators",
        "walk_forward",
        "backtest",
        "evaluate",
        "certify",
        "gates",
        "enforce",
    ),
}


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def infer_family(name: str) -> str:
    parts = name.split("_")
    if parts[0] == "vpvr":
        return "_".join(parts[:3]) if len(parts) >= 3 else name
    if parts[0] in (
        "funding",
        "momentum",
        "trend",
        "vol",
        "bb",
        "pairs",
        "mtf",
        "loid",
        "xs",
        "donchian",
        "impl",
        "large",
    ):
        return "_".join(parts[:2]) if len(parts) >= 2 else name
    return parts[0]


def infer_timeframe(name: str, config: dict | None) -> str:
    if config:
        for key in ("timeframe", "timeframe_entry", "tf"):
            if key in config and config[key]:
                return str(config[key])
    for tf in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "1d"):
        if f"_{tf}_" in name or name.endswith(f"_{tf}"):
            return tf
    return "?"


def categorize_module(rel_path: str, text: str) -> list[str]:
    base = Path(rel_path).name.lower()
    cats: set[str] = set()
    for cat, hints in CATEGORY_HINTS.items():
        for hint in hints:
            if hint in base:
                cats.add(cat)
    # content-based overrides
    if re.search(r"\bdef\s+sharpe|profit_factor|max_drawdown|compute_metrics", text):
        cats.add("evaluation")
    if re.search(r"\bdef\s+build_signals|generate_signals|compute_zscore|vpvr|funding", text):
        cats.add("signal")
    if re.search(r"\bdef\s+(position_size|sizing|atr_stop|risk|volatility_target)", text):
        cats.add("risk")
    if re.search(r"\bdef\s+(execute|fill|slippage|fee_model|commission)", text):
        cats.add("execution")
    if re.search(r"\bfee[s]?_bps|slippage_bps|cost_per_trade|round_trip", text, re.I):
        if "execution" not in cats:
            cats.add("cost")
    return sorted(cats) or ["other"]


def movability_score(rel_path: str, text: str, imports: list[str]) -> tuple[str, list[str]]:
    """Return (verdict, reasons) for whether a module can move to _shared/."""
    reasons: list[str] = []
    base = Path(rel_path).name.lower()
    rel_lower = rel_path.lower()

    # Already shared
    if rel_path.startswith("_shared/"):
        return "already_shared", ["lives in _shared/"]

    # Graveyard / adapter / runner files are usually too specific
    if "framework_adapter" in base or "run_backtest" in base or "run_first" in base:
        return "cautionary", ["entry/runner/framework glue; strategy-specific wiring"]
    if "data_loader" in base:
        return "cautionary", ["typically hardcodes symbols/paths for the strategy"]
    if "config" in base:
        return "cautionary", ["configuration is per-strategy"]
    if base in ("strategy.py", "backtest.py", "run_backtest.py", "smoke_backtest.py",
                "smoke_test.py", "optimize.py", "run_cross_check.py",
                "run_g1g7_backtest.py", "run_threshold_scan.py"):
        return "cautionary", ["entry/runner/strategy wiring; not a reusable library module"]

    # Heuristic: hardcoded symbols / paths
    if re.search(r"[A-Z]{2,}USDT|BTC|ETH|SOL|DOGE|BNB", text):
        reasons.append("contains hardcoded ticker symbols")
    if re.search(r"strategies/[a-z0-9_]+|_graveyard|results/", text):
        reasons.append("references strategy-local paths")

    # Heuristic: depends on sibling strategy modules
    own_dir = str(Path(rel_path).parent).lower()
    for imp in imports:
        imp_lower = imp.lower().replace(".", "/")
        if own_dir and own_dir != "." and imp_lower.startswith(own_dir):
            reasons.append(f"imports sibling module '{imp}'")

    if reasons:
        return "cautionary", reasons

    return "portable", ["generic helper; no hardcoded symbols/strategy paths"]


def extract_imports(text: str) -> list[str]:
    imports: list[str] = []
    for line in text.splitlines()[:80]:
        line = line.strip()
        if line.startswith("import "):
            mod = line.split()[1].split(".")[0]
            if mod not in ("__future__",):
                imports.append(mod)
        elif line.startswith("from "):
            parts = line.split()
            if len(parts) >= 2 and parts[1] not in ("__future__",):
                imports.append(parts[1])
    return imports


def scan_strategy_dir(path: Path) -> dict[str, Any]:
    name = path.name
    config = load_json(path / "config.json") or {}
    metrics = load_json(path / "results" / "metrics.json")

    row: dict[str, Any] = {
        "strategy_key": name,
        "path": str(path.relative_to(REPO)),
        "timeframe": infer_timeframe(name, config),
        "family": infer_family(name),
        "status": "ACTIVE",
        "config": config,
        "metrics": metrics,
        "modules": [],
        "entry_points": [],
    }

    # scan files
    for fp in sorted(path.rglob("*")):
        if not fp.is_file():
            continue
        if any(part in SKIP_DIRS for part in fp.parts):
            continue
        rel = str(fp.relative_to(REPO))
        if fp.suffix == ".py":
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            imports = extract_imports(text)
            cats = categorize_module(rel, text)
            verdict, reasons = movability_score(rel, text, imports)
            mod = {
                "path": rel,
                "categories": cats,
                "imports": imports,
                "movability": verdict,
                "movability_reasons": reasons,
            }
            row["modules"].append(mod)
            if fp.name in ENTRY_HINTS:
                row["entry_points"].append(rel)
        elif fp.name == "config.json":
            pass
        elif fp.suffix == ".json" and "framework_cv" in fp.name:
            cv = load_json(fp)
            if cv:
                row.setdefault("framework_cv", {})[fp.name] = {
                    "verdict": cv.get("verdict") or cv.get("w5_verdict"),
                    "W5_passed": cv.get("W5_passed"),
                }

    return row


def scan_all() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def build_module_catalog(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    catalog: dict[str, list[dict[str, str]]] = {
        "signal_generation": [],
        "risk_sizing": [],
        "execution": [],
        "cost": [],
        "evaluation": [],
        "other": [],
    }
    label_map = {
        "signal": "signal_generation",
        "risk": "risk_sizing",
        "execution": "execution",
        "cost": "cost",
        "evaluation": "evaluation",
        "other": "other",
    }
    for row in rows:
        for mod in row["modules"]:
            for cat in mod["categories"]:
                bucket = label_map.get(cat, "other")
                catalog[bucket].append(
                    {
                        "strategy": row["strategy_key"],
                        "module": mod["path"],
                        "movability": mod["movability"],
                        "reason": "; ".join(mod["movability_reasons"])[:200],
                    }
                )
    return catalog


def write_json(rows: list[dict[str, Any]], catalog: dict[str, list[dict[str, str]]]) -> None:
    (OUT / "strategy_inventory.json").write_text(
        json.dumps(
            {"strategies": rows, "module_catalog": catalog},
            indent=2,
            default=str,
        )
    )


def write_csv(rows: list[dict[str, Any]]) -> None:
    with open(OUT / "strategy_inventory.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "strategy",
                "status",
                "family",
                "timeframe",
                "entry_points",
                "modules",
                "portable_modules",
                "cautionary_modules",
            ]
        )
        for row in rows:
            mods = [Path(m["path"]).name for m in row["modules"]]
            portable = sum(1 for m in row["modules"] if m["movability"] == "portable")
            cautionary = sum(1 for m in row["modules"] if m["movability"] == "cautionary")
            writer.writerow(
                [
                    row["strategy_key"],
                    row["status"],
                    row["family"],
                    row["timeframe"],
                    "; ".join(Path(e).name for e in row["entry_points"]),
                    "; ".join(mods),
                    portable,
                    cautionary,
                ]
            )


def write_markdown(rows: list[dict[str, Any]], catalog: dict[str, list[dict[str, str]]]) -> None:
    lines = [
        "# Old-strategy module inventory",
        "",
        f"Generated from `{STRATEGIES.relative_to(REPO)}` (active + graveyard).",
        "",
        "## Strategy overview",
        "",
        "| Strategy | Status | Family | TF | Entry points | Modules | Portable | Cautionary |",
        "|----------|--------|--------|----|--------------|---------|----------|------------|",
    ]
    for row in rows:
        entry = "; ".join(Path(e).name for e in row["entry_points"]) or "—"
        mods = "; ".join(Path(m["path"]).name for m in row["modules"]) or "—"
        portable = sum(1 for m in row["modules"] if m["movability"] == "portable")
        cautionary = sum(1 for m in row["modules"] if m["movability"] == "cautionary")
        lines.append(
            f"| `{row['strategy_key']}` | {row['status']} | {row['family']} | {row['timeframe']} | "
            f"{entry} | {mods} | {portable} | {cautionary} |"
        )

    lines += ["", "## Reusable module catalog", ""]
    for bucket in [
        "signal_generation",
        "risk_sizing",
        "execution",
        "cost",
        "evaluation",
    ]:
        nice = bucket.replace("_", " ").title()
        lines += [f"### {nice}", "", "| Strategy | Module | Movability | Reason |", "|----------|--------|------------|--------|"]
        for item in catalog.get(bucket, []):
            lines.append(
                f"| `{item['strategy']}` | `{item['module']}` | {item['movability']} | {item['reason']} |"
            )
        lines.append("")

    (OUT / "strategy_inventory.md").write_text("\n".join(lines))


def main() -> None:
    rows = scan_all()
    catalog = build_module_catalog(rows)
    write_json(rows, catalog)
    write_csv(rows)
    write_markdown(rows, catalog)
    print(f"[scan] {len(rows)} strategies, wrote inventory to {OUT}")


if __name__ == "__main__":
    main()
