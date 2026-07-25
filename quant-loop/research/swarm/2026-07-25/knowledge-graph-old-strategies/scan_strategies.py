#!/usr/bin/env python3
"""Scan quant-loop/strategies and produce a reusable-module catalog.

Outputs (in the script's directory):
    strategy_catalog.json   — one record per strategy directory
    strategy_catalog.csv    — flattened tabular view
    module_catalog.md       — human-readable classification + migration notes

Design notes
------------
- Does NOT modify production code; read-only AST walk.
- Classifies every top-level function/class in each strategy directory into
  signal / risk / execution / cost / evaluation buckets.
- Flags whether a module is a clean candidate for _shared/ or a cautionary
  example (strategy-specific, untested, or duplicating shared code).
"""
from __future__ import annotations

import ast
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]  # quant-loop
STRATEGIES = REPO / "strategies"
SHARED = REPO / "_shared"
OUT = Path(__file__).resolve().parent

SIGNAL_HINTS = {
    "zscore", "z_score", "vpvr", "volume_profile", "funding", "cointegration",
    "momentum", "trend", "breakout", "reversion", "reversal", "regime",
    "slope", "confluence", "iceberg", "orderbook", "rsi", "macd", "adx",
    "donchian", "atr", "volatility", "vol_breakout", "mean_reversion",
    "crossing", "crossover", "threshold", "signal", "feature", "indicator",
    "filter", "confirm",
}
RISK_HINTS = {
    "sizing", "position", "vol_target", "risk", "stop", "drawdown", "mdd",
    "atr_sizing", "kelly", "max_hold", "holding", "limit", "exposure",
}
EXEC_HINTS = {
    "execution", "fill", "order", "simulate", "broker", "exchange",
    "paper", "runner", "backtest", "bar_loop",
}
COST_HINTS = {
    "cost", "fee", "slippage", "spread", "impact", "commission",
    "cost_model", "apply_cost",
}
EVAL_HINTS = {
    "metric", "sharpe", "calmar", "sortino", "profit_factor", "pf",
    "compute", "evaluate", "validate", "cpcv", "bootstrap", "walk_forward",
    "oos", "cv", "framework", "ledger", "equity_curve",
}

CATEGORY_HINTS = {
    "signal": SIGNAL_HINTS,
    "risk/sizing": RISK_HINTS,
    "execution": EXEC_HINTS,
    "cost": COST_HINTS,
    "evaluation": EVAL_HINTS,
}


def classify_name(name: str) -> list[str]:
    """Return the categories that a function/class name suggests."""
    low = name.lower()
    cats = []
    for cat, hints in CATEGORY_HINTS.items():
        if any(h in low for h in hints):
            cats.append(cat)
    return cats or ["other"]


def parse_file(path: Path) -> dict[str, Any]:
    """AST-parse a Python file and return imports + top-level defs."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e), "imports": [], "defs": []}

    imports: list[str] = []
    defs: list[dict[str, Any]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.append(mod)
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append({
                "name": node.name,
                "lineno": node.lineno,
                "categories": classify_name(node.name),
                "is_class": isinstance(node, ast.ClassDef),
            })
    return {"imports": imports, "defs": defs}


def detect_cost_model(parsed_files: list[dict[str, Any]]) -> str:
    for pf in parsed_files:
        for imp in pf["imports"]:
            if "_shared.execution.cost_model" in imp or imp == "cost_model":
                return "_shared"
    for pf in parsed_files:
        text = "\n".join(str(pf.get("error", "")).split())
        if "cost_model" in str(pf):
            pass
    return "hardcoded"  # default assumption; refined by heuristics below


def detect_gate_logic(parsed_files: list[dict[str, Any]]) -> str:
    for pf in parsed_files:
        for imp in pf["imports"]:
            if any(x in imp for x in ("_shared.gates", "_shared.validators", "certify_strategy")):
                return "_shared"
    # manual gate if thresholds appear
    for pf in parsed_files:
        for d in pf["defs"]:
            if any(h in d["name"].lower() for h in ("gate", "check", "threshold", "pass", "fail", "certify")):
                return "manual"
    return "none"


def detect_feature_modules(parsed_files: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map category -> list of file stems that look like a module for that category."""
    categories = list(CATEGORY_HINTS.keys()) + ["other"]
    out: dict[str, list[str]] = {c: [] for c in categories}
    seen = {c: set() for c in categories}
    for pf in parsed_files:
        stem = Path(pf["path"]).stem
        cats: set[str] = set()
        for d in pf["defs"]:
            cats.update(d["categories"])
        for cat in cats:
            if stem not in seen[cat]:
                seen[cat].add(stem)
                out[cat].append(stem)
    return out


def scan_strategy_dir(path: Path, status: str, graveyard_family: str | None = None) -> dict[str, Any]:
    py_files = sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
    parsed = []
    for p in py_files:
        pf = parse_file(p)
        pf["path"] = str(p.relative_to(REPO))
        parsed.append(pf)

    entry_files = []
    for p in py_files:
        name = p.name
        if name in ("strategy.py", "run_backtest.py"):
            entry_files.append(str(p.relative_to(REPO)))
        elif name.startswith("run_") and name.endswith(".py"):
            entry_files.append(str(p.relative_to(REPO)))
        elif "_backtest.py" in name:
            entry_files.append(str(p.relative_to(REPO)))

    imports = sorted(set(imp for pf in parsed for imp in pf["imports"]))
    uses_shared = any("_shared" in imp for imp in imports)

    # Cost model source
    cost_model = "unknown"
    if any("_shared.execution.cost_model" in imp for imp in imports):
        cost_model = "_shared"
    elif any(h in " ".join(imports) for h in ("cost_model", "CostModel")) or any(
        "fee" in str(p).lower() or "slippage" in str(p).lower() for p in py_files
    ):
        cost_model = "hardcoded_or_local"
    else:
        cost_model = "none_visible"

    gate_logic = detect_gate_logic(parsed)
    feature_modules = detect_feature_modules(parsed)

    # Reusable utility defs
    reusable = []
    for pf in parsed:
        stem = Path(pf["path"]).stem
        for d in pf["defs"]:
            cats = d["categories"]
            # Pure-utility heuristic: in an indicators/execution/sizing/validation file
            # or a generic helper file, and not obviously strategy-specific.
            path_parts = [x.lower() for x in Path(pf["path"]).parts]
            in_util_dir = any(x in path_parts for x in ("indicators", "execution", "sizing", "validation", "utils", "features"))
            generic = not any(x in d["name"].lower() for x in ("_h3", "_h2", "_h1", "_h4", "_v72", "_u5", "_u6", "_p3opt"))
            moveable = (in_util_dir or "utils" in stem or "helper" in stem) and generic
            reusable.append({
                "file": pf["path"],
                "name": d["name"],
                "is_class": d["is_class"],
                "categories": cats,
                "move_to_shared_candidate": moveable,
                "already_shared": "_shared" in pf["path"],
            })

    config_exists = (path / "config.json").exists()
    results_exists = (path / "results").exists()

    return {
        "strategy_key": path.name,
        "path": str(path.relative_to(REPO)),
        "status": status,
        "graveyard_family": graveyard_family,
        "entry_files": entry_files,
        "py_files": [str(p.relative_to(REPO)) for p in py_files],
        "config_exists": config_exists,
        "results_exists": results_exists,
        "imports": imports,
        "uses_shared": uses_shared,
        "cost_model": cost_model,
        "gate_logic": gate_logic,
        "feature_modules": feature_modules,
        "reusable_functions": reusable,
        "n_defs": len(reusable),
    }


def scan_all() -> list[dict[str, Any]]:
    rows = []
    for child in sorted(STRATEGIES.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
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
                rows.append(scan_strategy_dir(child, "GRAVEYARD", family_dir.name))
    return rows


def write_json_csv(rows: list[dict[str, Any]]) -> None:
    json_path = OUT / "strategy_catalog.json"
    json_path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")

    flat = []
    for r in rows:
        for cat, stems in r["feature_modules"].items():
            flat.append({
                "strategy_key": r["strategy_key"],
                "status": r["status"],
                "graveyard_family": r.get("graveyard_family") or "",
                "path": r["path"],
                "entry_files": ";".join(r["entry_files"]),
                "uses_shared": r["uses_shared"],
                "cost_model": r["cost_model"],
                "gate_logic": r["gate_logic"],
                "feature_category": cat,
                "feature_stems": ";".join(stems),
                "n_defs": r["n_defs"],
            })
    csv_path = OUT / "strategy_catalog.csv"
    if flat:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=flat[0].keys())
            writer.writeheader()
            writer.writerows(flat)


def write_module_catalog(rows: list[dict[str, Any]]) -> None:
    md = OUT / "module_catalog.md"
    lines = [
        "# Old-strategy reusable-module catalog",
        "",
        f"Generated from `{STRATEGIES.relative_to(REPO)}` ({len(rows)} strategy directories).",
        "",
        "## 1. Per-strategy summary",
        "",
        "| Strategy | Status | Entry files | Uses _shared | Cost model | Gate logic | Signal modules | Risk modules | Exec modules | Cost modules | Eval modules |",
        "|----------|--------|-------------|--------------|------------|------------|----------------|--------------|--------------|--------------|--------------|",
    ]
    for r in rows:
        fm = r["feature_modules"]
        def fmt(cat):
            return ", ".join(fm.get(cat, [])) or "—"
        lines.append(
            f"| `{r['strategy_key']}` | {r['status']} | {len(r['entry_files'])} | {r['uses_shared']} | "
            f"{r['cost_model']} | {r['gate_logic']} | {fmt('signal')} | {fmt('risk/sizing')} | "
            f"{fmt('execution')} | {fmt('cost')} | {fmt('evaluation')} |"
        )

    lines += ["", "## 2. Reusable-module candidates", "",
                "Functions / classes that look generic enough to move into `_shared/`.", ""]
    lines.append("| Strategy | File | Symbol | Category | Move to _shared? | Note |")
    lines.append("|----------|------|--------|----------|------------------|------|")
    caution_examples = []
    for r in rows:
        for fn in r["reusable_functions"]:
            if fn["already_shared"]:
                continue
            note = ""
            if fn["move_to_shared_candidate"]:
                note = "candidate"
            elif not r["uses_shared"]:
                note = "duplicates shared or ad-hoc"
                caution_examples.append((r["strategy_key"], fn["file"], fn["name"]))
            else:
                note = "strategy-specific"
            if fn["move_to_shared_candidate"]:
                lines.append(
                    f"| `{r['strategy_key']}` | `{fn['file']}` | `{fn['name']}` | "
                    f"{', '.join(fn['categories'])} | {fn['move_to_shared_candidate']} | {note} |"
                )

    lines += ["", "## 3. Cautionary / strategy-specific modules (do NOT move)", ""]
    if caution_examples:
        lines.append("| Strategy | File | Symbol | Why |")
        lines.append("|----------|------|--------|-----|")
        for strat, file, name in caution_examples[:60]:
            lines.append(f"| `{strat}` | `{file}` | `{name}` | hardcoded params / one-off |")
    else:
        lines.append("_No cautionary examples flagged._")

    md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if not STRATEGIES.exists():
        print(f"ERROR: {STRATEGIES} not found", file=sys.stderr)
        sys.exit(1)
    rows = scan_all()
    write_json_csv(rows)
    write_module_catalog(rows)
    print(f"[scan] {len(rows)} strategies -> strategy_catalog.json, strategy_catalog.csv, module_catalog.md")


if __name__ == "__main__":
    main()
