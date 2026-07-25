#!/usr/bin/env python3
"""
knowledge_graph_scanner.py

Scans /Users/mark/multica/quant-loop/strategies/ recursively for reusable
modules across active strategies and _graveyard.

Produces:
- strategy_module_inventory.json
- strategy_module_inventory.csv
- knowledge_graph_summary.md
"""
from __future__ import annotations

import ast
import csv
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

STRATEGIES_ROOT = Path("/Users/mark/multica/quant-loop/strategies")
OUTPUT_DIR = Path("/Users/mark/multica/quant-loop/research/swarm/2026-07-25/gate-ledger-fix")

SKIP_DIRS = {
    "tests",
    "__pycache__",
    ".pytest_cache",
    "data",
    "results",
    ".git",
}

# Filename -> classification hints. Lower keys checked first.
FILENAME_PATTERNS: dict[tuple[str, ...], str] = {
    ("strategy.py", "run.py", "main.py", "prototype.py"): "entry",
    (
        "data_loader.py",
        "universe.py",
        "calendar.py",
        "tod_calendar.py",
        "walk_forward.py",
        "oos_walk_forward.py",
        "backtest.py",
        "portfolio.py",
        "utils.py",
        "helpers.py",
        "run_cpcv.py",
        "run_framework_cv_multiwindow.py",
        "sensitivity_sweep.py",
        "rebuild_summary.py",
        "build_deliverables.py",
        "scripts/bootstrap.py",
        "scripts/b6_bootstrap.py",
        "paper_runner.py",
    ): "data_utils",
    (
        "indicators.py",
        "build_signals.py",
        "combine_signals.py",
        "signals.py",
        "signal.py",
        "vpvr_levels.py",
        "mtf_xs_pairs_base.py",
        "mtf_xs_runner.py",
        "iter94.py",
        "cointegration.py",
        "state_machine.py",
        "trend_filter.py",
    ): "signal_generation",
    (
        "sizing.py",
        "position_size.py",
        "risk.py",
        "sizing_sweep.py",
        "kill_criteria.py",
    ): "risk_sizing",
    (
        "execution.py",
        "cost.py",
        "cost_model.py",
        "slippage.py",
        "commission.py",
        "fees.py",
        "fill_engine.py",
    ): "execution_cost",
    (
        "metrics.py",
        "performance.py",
        "report.py",
        "performance_report.py",
        "eval.py",
        "optimize.py",
        "b6_fwer.py",
    ): "evaluation_metrics",
    (
        "framework_adapter",
        "diagnose.py",
        "inspect.py",
        "smoke_test.py",
        "smoke_backtest.py",
        "write_winner_trades.py",
    ): "anti_pattern",
}

CONTENT_HINTS: dict[tuple[str, ...], str] = {
    ("from strategy import", "import strategy", "def run_backtest", "def main", "if __name__ == '__main__'", "if __name__ == \"__main__\""): "entry",
    ("def compute_signal", "def generate_signal", "def build_signal", "class Signal", "def vpvr", "def zscore", "def rsi", "def macd", "def adx", "def atr", "def cointegration", "def trend_filter", "def state_machine"): "signal_generation",
    ("def position_size", "def sizing", "def risk", "def kelly", "def vol_target", "def max_position", "def kill_criteria"): "risk_sizing",
    ("def slippage", "def commission", "def market_impact", "def execution_cost", "def fees"): "execution_cost",
    ("def sharpe", "def sortino", "def calmar", "def max_drawdown", "def profit_factor", "def expectancy", "def win_rate", "def metrics", "def optimize"): "evaluation_metrics",
    ("def load_data", "def fetch_data", "def read_csv", "def get_universe", "def walk_forward", "def train_test_split", "def bootstrap"): "data_utils",
    ("hardcoded", "TODO", "FIXME", "copy-paste", "duplicate"): "anti_pattern",
}


def classify_by_filename(path: Path) -> str | None:
    """Return module_type hint based on filename, or None."""
    name = path.name
    stem = path.stem
    # Generic one-word stems that should only match exactly (not as prefix).
    exact_only_stems = {"run", "main", "data", "util", "helper", "test"}
    for keys, mtype in FILENAME_PATTERNS.items():
        if name in keys:
            return mtype
        for key in keys:
            if not key.endswith(".py"):
                continue
            key_stem = key[:-3]
            if stem == key_stem:
                return mtype
            # Allow dated variants like mtf_xs_pairs_base_20260718.py
            if key_stem not in exact_only_stems and stem.startswith(f"{key_stem}_"):
                return mtype
    # Stem keyword matching for compound names (e.g. my_data_loader.py).
    # Data/orchestration keywords are checked first because they describe the
    # file's purpose more specifically than generic signal keywords like "vpvr".
    stem_lower = stem.lower()
    if "adapter" in stem_lower:
        return "anti_pattern"
    if any(k in stem_lower for k in ("data", "loader", "universe", "calendar", "walk_forward", "backtest", "portfolio", "util", "helper", "bootstrap", "cpcv", "sweep", "runner")):
        return "data_utils"
    if any(k in stem_lower for k in ("sizing", "position", "risk", "kelly", "kill_criteria")):
        return "risk_sizing"
    if any(k in stem_lower for k in ("cost", "execution", "slippage", "fee", "commission", "fill_engine")):
        return "execution_cost"
    if any(k in stem_lower for k in ("metric", "performance", "report", "eval", "optimize", "fwer")):
        return "evaluation_metrics"
    if any(k in stem_lower for k in ("indicator", "signal", "build_signal", "cointegration", "trend_filter", "state_machine")):
        return "signal_generation"
    # Broad VPVR keyword only after more specific checks.
    if "vpvr" in stem_lower:
        return "signal_generation"
    return None


def classify_by_content(text: str) -> str | None:
    """Return module_type hint based on AST/text heuristics."""
    for keys, mtype in CONTENT_HINTS.items():
        for key in keys:
            if key in text:
                return mtype
    return None


def count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def extract_top_level_names(text: str) -> list[str]:
    names: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return names
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(f"fn:{node.name}")
        elif isinstance(node, ast.ClassDef):
            names.append(f"cls:{node.name}")
    return names


def detect_anti_pattern(path: Path, text: str, mtype: str) -> tuple[str, str]:
    """Re-evaluate anti_pattern reasons."""
    reasons: list[str] = []
    lower = text.lower()

    # Framework adapters are duplicated boilerplate.
    if "adapter" in path.stem.lower():
        reasons.append("framework adapter duplicated per strategy")
    if "vectorbt" in lower and "import" in lower:
        reasons.append("framework-specific vectorbt coupling")
    if "backtrader" in lower and "import" in lower:
        reasons.append("framework-specific backtrader coupling")
    if "freqtrade" in lower and "import" in lower:
        reasons.append("framework-specific freqtrade coupling")

    # Hardcoded absolute paths
    if re.search(r"/Users/mark/multica/quant-loop", text):
        reasons.append("hardcoded absolute project paths")

    # Copy-paste indicators of duplicated metrics boilerplate
    if text.count("def sharpe") > 0 and text.count("def max_drawdown") > 0 and mtype != "evaluation_metrics":
        reasons.append("inline metrics duplicated from _shared/evaluation")

    # Smoke/inspect/diagnose scripts are one-off debugging files
    if path.stem in ("smoke_test", "smoke_backtest", "diagnose", "inspect", "inspect_full", "inspect_trades"):
        reasons.append("one-off diagnostic/script not reusable")

    # Runners are thin wrappers; classify as data_utils if not already anti-pattern
    if path.stem in ("run_backtest", "run_cross_check", "run_g1g7_backtest") and not reasons:
        return "data_utils", "orchestration/runner script"

    if not reasons and mtype != "anti_pattern":
        return mtype, ""

    return "anti_pattern", "; ".join(reasons) if reasons else "questionable pattern"


def determine_reusable(mtype: str, reason: str, symbols: list[str], loc: int) -> tuple[str, str]:
    if mtype == "anti_pattern":
        return "no", reason or "anti-pattern"
    if mtype == "entry":
        return "maybe", "entry files are strategy-specific but can inspire templates"
    if mtype == "signal_generation":
        if loc < 30:
            return "maybe", "too small to be worth extracting unless shared by many strategies"
        return "yes", "generic indicator/signal functions should migrate to _shared/indicators"
    if mtype == "risk_sizing":
        return "yes", "position sizing primitives belong in _shared/sizing"
    if mtype == "execution_cost":
        return "yes", "cost models belong in _shared/execution"
    if mtype == "evaluation_metrics":
        return "yes", "metric implementations belong in _shared/validation or _shared/evaluation"
    if mtype == "data_utils":
        if "run_backtest" in reason or "runner" in reason:
            return "maybe", "orchestration helpers can be templated in _shared/templates"
        return "yes", "data loading/universe/calendar utilities belong in _shared/data"
    return "maybe", "needs manual review"


def short_description_from_symbols(path: Path, symbols: list[str], mtype: str) -> str:
    if mtype == "entry":
        return "strategy entry/run script"
    if mtype == "anti_pattern":
        return "strategy-specific or duplicated code"

    func_names = [s.split(":", 1)[1] for s in symbols if s.startswith("fn:")]
    cls_names = [s.split(":", 1)[1] for s in symbols if s.startswith("cls:")]

    desc_parts: list[str] = []
    if cls_names:
        desc_parts.append(f"classes: {', '.join(cls_names[:3])}")
    if func_names:
        desc_parts.append(f"functions: {', '.join(func_names[:5])}")
    if not desc_parts:
        desc_parts.append("module-level code")

    return "; ".join(desc_parts)


def is_strategy_directory(d: Path) -> bool:
    return d.is_dir() and any((d / name).exists() for name in ("strategy.py", "run.py", "main.py"))


def scan() -> list[dict]:
    records: list[dict] = []

    strategy_dirs = sorted([d for d in STRATEGIES_ROOT.iterdir() if is_strategy_directory(d)])
    # Also include _graveyard children and _indicators / _oos_rank as special dirs
    special_dirs = []
    for special in ("_graveyard", "_indicators", "_oos_rank_20260718"):
        p = STRATEGIES_ROOT / special
        if p.exists():
            if special == "_graveyard":
                special_dirs.extend([d for d in p.iterdir() if d.is_dir()])
            else:
                special_dirs.append(p)

    all_dirs = sorted(set(strategy_dirs + special_dirs), key=lambda x: x.name)

    for strategy_dir in all_dirs:
        py_files = sorted(
            [f for f in strategy_dir.rglob("*.py")
             if f.name not in ("__init__.py", "conftest.py")
             and not any(part in SKIP_DIRS for part in f.relative_to(strategy_dir).parts)],
            key=lambda x: str(x),
        )

        for py_file in py_files:
            rel = py_file.relative_to(STRATEGIES_ROOT)
            strategy_name = strategy_dir.name
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            loc = count_lines(py_file)
            symbols = extract_top_level_names(text)

            mtype = classify_by_filename(py_file)
            if not mtype:
                mtype = classify_by_content(text) or "unknown"

            mtype, reason = detect_anti_pattern(py_file, text, mtype)
            if mtype == "anti_pattern" and not reason:
                reason = "classified as anti-pattern by filename/content"

            reusable, reusable_reason = determine_reusable(mtype, reason, symbols, loc)

            description = short_description_from_symbols(py_file, symbols, mtype)

            records.append({
                "strategy_dir": strategy_name,
                "file_path": str(rel),
                "module_type": mtype,
                "short_description": description,
                "reusable_in_shared": reusable,
                "reason": reusable_reason,
                "lines_of_code": loc,
            })

    return records


def write_json(records: list[dict]) -> None:
    path = OUTPUT_DIR / "strategy_module_inventory.json"
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def write_csv(records: list[dict]) -> None:
    path = OUTPUT_DIR / "strategy_module_inventory.csv"
    if not records:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def write_summary(records: list[dict]) -> None:
    path = OUTPUT_DIR / "knowledge_graph_summary.md"

    total = len(records)
    strategy_dirs = sorted({r["strategy_dir"] for r in records})
    by_type: dict[str, list[dict]] = {}
    for r in records:
        by_type.setdefault(r["module_type"], []).append(r)

    lines: list[str] = [
        "# Quant Strategy Module Inventory",
        "",
        f"**Generated:** 2026-07-25  ",
        f"**Strategies scanned:** {len(strategy_dirs)}  ",
        f"**Total Python modules:** {total}  ",
        f"**Root:** `{STRATEGIES_ROOT}`  ",
        "",
        "## Overview",
        "",
        "This inventory is the result of a recursive scan of `quant-loop/strategies/`,",
        "including active strategies and the `_graveyard`. Each non-test Python file was",
        "classified by filename and content heuristics into one of the following buckets:",
        "",
        "| Category | Count | Description |",
        "|----------|-------|-------------|",
    ]
    for mtype in ["entry", "signal_generation", "risk_sizing", "execution_cost",
                  "evaluation_metrics", "data_utils", "anti_pattern", "unknown"]:
        count = len(by_type.get(mtype, []))
        if count == 0:
            continue
        desc = {
            "entry": "Strategy entry/run scripts",
            "signal_generation": "Indicators, signals, transforms",
            "risk_sizing": "Position sizing and risk controls",
            "execution_cost": "Cost and execution models",
            "evaluation_metrics": "Performance and evaluation metrics",
            "data_utils": "Data loading, universe, calendar, orchestration",
            "anti_pattern": "Code that should NOT be copied",
            "unknown": "Files that could not be classified",
        }[mtype]
        lines.append(f"| {mtype} | {count} | {desc} |")

    lines.extend([
        "",
        "## Reusable Modules by Category",
        "",
        "The following modules are candidates for migration into `quant-loop/_shared/`.",
        "Migration priority is roughly: `risk_sizing` / `execution_cost` / `evaluation_metrics`",
        "(high), `signal_generation` and `data_utils` (medium), `entry` (low/templates only).",
        "",
    ])

    for mtype in ["signal_generation", "risk_sizing", "execution_cost",
                  "evaluation_metrics", "data_utils", "entry"]:
        items = by_type.get(mtype, [])
        if not items:
            continue
        lines.append(f"### {mtype}")
        lines.append("")
        lines.append("| Strategy | File | LOC | Reusable? | Description |")
        lines.append("|----------|------|-----|-----------|-------------|")
        for item in sorted(items, key=lambda x: (x["strategy_dir"], x["file_path"])):
            lines.append(
                f"| {item['strategy_dir']} | `{item['file_path']}` | {item['lines_of_code']} | "
                f"{item['reusable_in_shared']} | {item['short_description']} |"
            )
        lines.append("")

    lines.extend([
        "## Anti-Patterns",
        "",
        "These files contain duplicated framework adapters, hardcoded paths, one-off",
        "diagnostic scripts, or other patterns that should NOT be copied into `_shared/`.",
        "",
        "| Strategy | File | LOC | Reason |",
        "|----------|------|-----|--------|",
    ])
    for item in sorted(by_type.get("anti_pattern", []), key=lambda x: (x["strategy_dir"], x["file_path"])):
        lines.append(
            f"| {item['strategy_dir']} | `{item['file_path']}` | {item['lines_of_code']} | {item['reason']} |"
        )

    lines.extend([
        "",
        "## Recommended Migration to `_shared/`",
        "",
        "1. **Consolidate indicators/signals** into `_shared/indicators/` — the existing",
        "   `_shared/indicators/` already contains some base modules; strategy-specific",
        "   `build_signals.py`, `indicators.py`, and `vpvr_levels.py` files should be",
        "   refactored into generic primitives there.",
        "",
        "2. **Unify sizing/risk** into `_shared/sizing/` — most strategies implement",
        "   vol-target or fixed-fraction sizing inline. Extract common helpers (e.g.",
        "   `volatility_target_size`, `kelly_fraction`) into `_shared/sizing/`.",
        "",
        "3. **Centralize cost models** into `_shared/execution/` — any strategy with",
        "   `execution.py`, `cost.py`, or inline fee logic should use shared cost models.",
        "",
        "4. **Move metrics to `_shared/validation/`** — many strategies redefine Sharpe,",
        "   max drawdown, etc. `_shared/validation/` already exists and should become the",
        "   single source of truth.",
        "",
        "5. **Data/orchestration helpers** — `data_loader.py`, `universe.py`, calendar",
        "   modules, and `run_backtest.py` runners should migrate to `_shared/data/` or",
        "   `_shared/templates/` after stripping strategy-specific columns/symbols.",
        "",
        "6. **Delete/copy-blocker list** — the anti-pattern files (framework adapters,",
        "   smoke/diagnose scripts) should be left in-place as historical artifacts but",
        "   never copied into shared infrastructure.",
        "",
        "## Methodology",
        "",
        "The scan used a rule-based classifier with the following priority:",
        "1. Filename keyword matching (`strategy.py` -> entry, `indicators.py` -> signal, etc.)",
        "2. Content keyword matching on top-level function/class names",
        "3. Anti-pattern detection (hardcoded paths, framework adapters, diagnostic scripts)",
        "4. Manual review fallback marked as `unknown` or `maybe`",
        "",
        "The scanner script itself is preserved at",
        "`knowledge_graph_scanner.py` for reproducibility.",
        "",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = scan()
    write_json(records)
    write_csv(records)
    write_summary(records)
    print(f"Scanned {len({r['strategy_dir'] for r in records})} strategy directories.")
    print(f"Found {len(records)} modules.")
    print(f"Outputs written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
