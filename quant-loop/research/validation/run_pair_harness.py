#!/usr/bin/env python3
"""Cross-engine verification harness for pair strategies.

The source strategy directory is treated as read-only. Each run copies it to an
isolated /tmp work directory, forces the W5 24bp pair round-trip cost in that
copy, then runs run_backtest.py followed by framework_adapter_freqtrade.py.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PAIR_ROUND_TRIP_COST = 0.0024
FEE_BPS_PER_SIDE_PER_LEG = 4.0
SLIPPAGE_BPS_PER_SIDE_PER_LEG = 2.0
PASS_RELATIVE_TOLERANCE = 0.01
REQUIRED_FILES = (
    "config.json",
    "run_backtest.py",
    "framework_adapter_freqtrade.py",
)
ADAPTER_COST_CONSTANTS = {
    "INHOUSE_FEE_BPS_PER_SIDE": FEE_BPS_PER_SIDE_PER_LEG,
    "INHOUSE_SLIP_BPS_PER_SIDE": SLIPPAGE_BPS_PER_SIDE_PER_LEG,
    "FREQTRADE_FEE_BPS_PER_SIDE": FEE_BPS_PER_SIDE_PER_LEG,
    "FREQTRADE_SLIP_BPS_PER_SIDE": SLIPPAGE_BPS_PER_SIDE_PER_LEG,
}


class HarnessError(RuntimeError):
    """A preflight, engine execution, or output-contract failure."""


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_fingerprints(strategy_dir: Path) -> dict[str, str]:
    """Fingerprint source artifacts that this harness must never modify."""
    protected = [strategy_dir / "run_backtest.py", strategy_dir / "results" / "metrics.json"]
    protected.extend(sorted((strategy_dir / "data").glob("*.parquet")))
    return {
        str(path.relative_to(strategy_dir)): _sha256(path)
        for path in protected
        if path.is_file()
    }


def _validate_strategy_dir(strategy_dir: Path) -> None:
    if not strategy_dir.is_dir():
        raise HarnessError(f"strategy directory does not exist: {strategy_dir}")
    missing = [name for name in REQUIRED_FILES if not (strategy_dir / name).is_file()]
    if missing:
        raise HarnessError(f"missing required files in {strategy_dir}: {', '.join(missing)}")
    parquets = sorted((strategy_dir / "data").glob("*.parquet"))
    if not parquets:
        raise HarnessError(f"no strategy-local parquet data found in {strategy_dir / 'data'}")


def _copy_strategy(strategy_dir: Path) -> tuple[Path, Path]:
    work_root = Path(tempfile.mkdtemp(prefix=f"pair-harness-{strategy_dir.name}-"))
    work_strategy = work_root / strategy_dir.name
    shutil.copytree(
        strategy_dir,
        work_strategy,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    return work_root, work_strategy


def _force_24bp_cost(work_strategy: Path) -> None:
    """Set both copied engines to 4bp fee + 2bp slip per side per leg."""
    config_path = work_strategy / "config.json"
    config = json.loads(config_path.read_text())
    config["fees_bps_per_side"] = FEE_BPS_PER_SIDE_PER_LEG
    config["slippage_bps_per_side"] = SLIPPAGE_BPS_PER_SIDE_PER_LEG
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    configured_cost = (
        2.0
        * 2.0
        * (float(config["fees_bps_per_side"]) + float(config["slippage_bps_per_side"]))
        / 10_000.0
    )
    if not math.isclose(configured_cost, PAIR_ROUND_TRIP_COST, abs_tol=1e-12):
        raise HarnessError(
            f"in-house cost preflight failed: expected {PAIR_ROUND_TRIP_COST}, got {configured_cost}"
        )

    adapter_path = work_strategy / "framework_adapter_freqtrade.py"
    adapter_text = adapter_path.read_text()
    for name, value in ADAPTER_COST_CONSTANTS.items():
        pattern = rf"(?m)^{re.escape(name)}\s*=\s*[^\n]+$"
        adapter_text, count = re.subn(pattern, f"{name} = {value:.1f}", adapter_text)
        if count != 1:
            raise HarnessError(
                f"adapter cost preflight failed: expected one assignment for {name}, found {count}"
            )
    adapter_path.write_text(adapter_text)


def _run_engine(label: str, script: Path, cwd: Path, timeout: int) -> None:
    _log(f"[{label}] {sys.executable} {script.name} (cwd={cwd})")
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"{label} timed out after {timeout}s") from exc

    if completed.stdout:
        for line in completed.stdout.rstrip().splitlines():
            _log(f"[{label}:stdout] {line}")
    if completed.stderr:
        for line in completed.stderr.rstrip().splitlines():
            _log(f"[{label}:stderr] {line}")
    if completed.returncode != 0:
        raise HarnessError(f"{label} exited with status {completed.returncode}")


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _annualized_return_from_equity(equity_path: Path) -> float:
    with equity_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) < 2:
        raise HarnessError(f"equity output has fewer than two rows: {equity_path}")

    start_ts = _parse_timestamp(rows[0]["ts"])
    end_ts = _parse_timestamp(rows[-1]["ts"])
    start_equity = float(rows[0]["equity"])
    end_equity = float(rows[-1]["equity"])
    span_years = (end_ts - start_ts).total_seconds() / (365.25 * 24.0 * 3600.0)
    if span_years <= 0.0 or start_equity <= 0.0 or end_equity <= 0.0:
        raise HarnessError(
            f"cannot annualize equity: span_years={span_years}, start={start_equity}, end={end_equity}"
        )
    return float((end_equity / start_equity) ** (1.0 / span_years) - 1.0)


def _relative_gap(inhouse: float, framework: float) -> float:
    return abs(inhouse - framework) / max(abs(framework), 0.01)


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HarnessError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise HarnessError(f"{label} is not finite: {result}")
    return result


def run_harness(strategy_dir: Path, timeout: int) -> dict[str, Any]:
    strategy_dir = strategy_dir.expanduser().resolve()
    _validate_strategy_dir(strategy_dir)
    source_fingerprints = _protected_fingerprints(strategy_dir)
    work_root, work_strategy = _copy_strategy(strategy_dir)
    _log(f"[harness] isolated work directory: {work_root}")
    _force_24bp_cost(work_strategy)

    _run_engine("in-house", work_strategy / "run_backtest.py", work_strategy, timeout)
    _run_engine(
        "freqtrade",
        work_strategy / "framework_adapter_freqtrade.py",
        work_strategy,
        timeout,
    )

    metrics_path = work_strategy / "results" / "metrics.json"
    cv_path = work_strategy / "results" / "framework_cv_freqtrade.json"
    if not metrics_path.is_file() or not cv_path.is_file():
        raise HarnessError("engine output missing metrics.json or framework_cv_freqtrade.json")

    equity_candidates = sorted(
        (work_strategy / "results").glob("equity_*.csv"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not equity_candidates:
        raise HarnessError("in-house engine emitted no results/equity_*.csv")

    inhouse_payload = json.loads(metrics_path.read_text())
    framework_payload = json.loads(cv_path.read_text())
    framework_metrics = framework_payload.get("framework")
    if not isinstance(framework_metrics, dict):
        raise HarnessError("framework CV output has no object at key 'framework'")

    inhouse_sharpe = _finite_float(inhouse_payload.get("sharpe"), "in-house sharpe")
    framework_sharpe = _finite_float(framework_metrics.get("sharpe"), "framework sharpe")
    inhouse_ann_return = _annualized_return_from_equity(equity_candidates[0])
    framework_ann_return = _finite_float(
        framework_metrics.get("ann_total_return"), "framework annualized return"
    )

    sharpe_gap = _relative_gap(inhouse_sharpe, framework_sharpe)
    ann_return_gap = _relative_gap(inhouse_ann_return, framework_ann_return)
    max_gap = max(sharpe_gap, ann_return_gap)
    verdict = "PASS" if max_gap <= PASS_RELATIVE_TOLERANCE else "FAIL"

    current_fingerprints = _protected_fingerprints(strategy_dir)
    if current_fingerprints != source_fingerprints:
        raise HarnessError("source strategy artifacts changed during isolated harness run")

    _log(
        "[compare] sharpe: "
        f"in-house={inhouse_sharpe:.12g} framework={framework_sharpe:.12g} "
        f"delta={inhouse_sharpe - framework_sharpe:+.12g} gap={sharpe_gap * 100.0:.6f}%"
    )
    _log(
        "[compare] ann_return: "
        f"in-house={inhouse_ann_return:.12g} framework={framework_ann_return:.12g} "
        f"delta={inhouse_ann_return - framework_ann_return:+.12g} "
        f"gap={ann_return_gap * 100.0:.6f}%"
    )
    _log(
        f"[verdict] {verdict}: max_gap={max_gap * 100.0:.6f}% "
        f"(limit={PASS_RELATIVE_TOLERANCE * 100.0:.2f}%)"
    )

    return {
        "strategy": strategy_dir.name,
        "cost_rt_bps": round(PAIR_ROUND_TRIP_COST * 10_000.0, 10),
        "inhouse_sharpe": inhouse_sharpe,
        "framework_sharpe": framework_sharpe,
        "sharpe_gap_pct": sharpe_gap * 100.0,
        "inhouse_ann_return": inhouse_ann_return,
        "framework_ann_return": framework_ann_return,
        "ann_return_gap_pct": ann_return_gap * 100.0,
        "gap_pct": max_gap * 100.0,
        "verdict": verdict,
        "source_integrity_verified": True,
        "work_dir": str(work_root),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated in-house vs freqtrade verification at 24bp pair round-trip cost."
    )
    parser.add_argument("strategy_dir", type=Path, help="xs_pairs strategy directory")
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="per-engine timeout in seconds (default: 900)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        summary = run_harness(args.strategy_dir, args.timeout)
    except Exception as exc:
        error_summary = {
            "strategy": str(args.strategy_dir),
            "verdict": "ERROR",
            "error": str(exc),
        }
        _log(f"[error] {exc}")
        print(json.dumps(error_summary, sort_keys=True))
        return 2

    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
