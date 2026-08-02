"""Gate enforcement — refuses to certify a strategy as SHIP-eligible if
metrics fail G1-G7 + Wave 2 additions (CPCV + DSR).

Usage:
    from _shared.gates.enforce import certify_strategy
    result = certify_strategy(metrics_path="path/to/metrics.json", n_trials=120)
    if not result.passed:
        print(result.reasons)
        sys.exit(1)
"""
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


# G1-G7 per SPEC + Wave 2 additions
GATES = [
    # (id, name, criterion_fn, description)
    ("G1", "sharpe_daily >= 1.0", lambda m: m.get("sharpe_daily", float("-inf")) >= 1.0,
     "Daily Sharpe ≥ 1.0"),
    ("G2", "annualized_return >= 0.15", lambda m: m.get("annualized_return", float("-inf")) >= 0.15,
     "Annualized return ≥ 15%"),
    ("G3", "max_drawdown_pct > -0.25", lambda m: m.get("max_drawdown_pct", -1.0) > -0.25,
     "Max drawdown > -25%"),
    ("G4", "profit_factor > 1.5", lambda m: m.get("profit_factor", 0.0) > 1.5,
     "Profit factor > 1.5"),
    # G5: CV OOS Sharpe — missing/NaN is an explicit FAIL (see REQUIRED_FIELDS)
    ("G5", "cpcv_mean_oos_sharpe >= 1.0", lambda m: m.get("cpcv_mean_oos_sharpe", float("nan")) >= 1.0,
     "CPCV mean OOS Sharpe ≥ 1.0"),
    # G6: bootstrap CI95 lower bound
    ("G6", "bootstrap_ci95_lower >= 0.5", lambda m: m.get("bootstrap_ci95_lower", 0.0) >= 0.5,
     "Bootstrap CI95 lower ≥ 0.5"),
    # G7 (corrected): Deflated Sharpe Ratio > 0 — replaces bogus Bonferroni
    ("G7", "deflated_sharpe > 0.0", lambda m: m.get("deflated_sharpe", float("-inf")) > 0.0,
     "Deflated Sharpe Ratio > 0 (Bailey-LdP 2014)"),
    # Trades floor
    ("T1", "n_trades >= 30", lambda m: m.get("n_trades", 0) >= 30,
     "At least 30 trades"),
]


def _isnan(x):
    try:
        return x != x
    except Exception:
        return False


# Required input field per gate. A missing (absent / None / NaN) required
# field is an explicit FAIL with reason "MISSING_FIELD:<name>" — it is NEVER
# a silent skip. This is the single place the required-field list lives.
REQUIRED_FIELDS = {
    "G1": "sharpe_daily",
    "G2": "annualized_return",
    "G3": "max_drawdown_pct",
    "G4": "profit_factor",
    "G5": "cpcv_mean_oos_sharpe",
    "G6": "bootstrap_ci95_lower",
    "G7": "deflated_sharpe",
    "T1": "n_trades",
}


def _is_missing(value) -> bool:
    return value is None or _isnan(value)


@dataclass
class GateResult:
    passed: bool
    failed_gates: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def __str__(self) -> str:
        if self.passed:
            return "PASS: all gates satisfied"
        return f"FAIL: {len(self.failed_gates)} gates failed: {', '.join(self.failed_gates)}"


def certify_metrics(metrics: dict, strict: bool = True) -> GateResult:
    """Check a metrics dict against all gates.
    
    Args:
        metrics: dict from metrics.json
        strict: if True, fail on evaluation exceptions; if False, skip gates
            that raise. Missing required fields (REQUIRED_FIELDS) ALWAYS fail
            with reason "MISSING_FIELD:<name>", regardless of strict.
    
    Returns:
        GateResult with passed/failed/reasons.
    """
    failed = []
    reasons = []
    for gid, name, fn, desc in GATES:
        key = REQUIRED_FIELDS.get(gid)
        if key is not None and (key not in metrics or _is_missing(metrics.get(key))):
            failed.append(gid)
            reasons.append(f"{gid} {name}: MISSING_FIELD:{key}")
            continue
        try:
            ok = bool(fn(metrics))
        except Exception as e:
            if strict:
                failed.append(gid)
                reasons.append(f"{gid} {name}: exception {e}")
            continue
        if not ok:
            failed.append(gid)
            value = metrics.get(name.split()[0].split("_")[-1], "?")
            reasons.append(f"{gid} {name}: got {value!r}, expected {desc}")
    return GateResult(passed=len(failed) == 0, failed_gates=failed, reasons=reasons, metrics=metrics)


def certify_strategy(
    metrics_path: str | Path,
    n_trials: int | None = None,
    ledger_path: str | Path | None = None,
) -> GateResult:
    """Read metrics.json + compute DSR if not present, then certify.

    Args:
        metrics_path: path to a strategy's metrics.json
        n_trials: REAL family size for DSR (number of candidates actually
            tried). There is no default: when DSR must be computed and no
            trial count can be resolved, certification FAILS explicitly
            (reason MISSING_N_TRIALS) rather than inventing a number.
        ledger_path: optional results-ledger.json override (tests). Defaults
            to <repo>/results-ledger.json.

    n_trials resolution order when DSR must be computed:
        1. explicit ``n_trials`` argument
        2. ``n_trials`` field inside metrics.json
        3. derived from the results-ledger family: the strategy_key is
           located in the ledger, its family inferred (same rule as
           scripts/build_results_ledger.py), and n_trials = number of ledger
           strategies in that family.
    """
    path = Path(metrics_path).expanduser()
    if not path.exists():
        return GateResult(passed=False, failed_gates=["FILE"], reasons=[f"not found: {path}"], metrics={})

    with open(path) as f:
        m = json.load(f)

    # If cpcv fields missing but OOS sharpe present, compute DSR
    if "deflated_sharpe" not in m and "cpcv_mean_oos_sharpe" in m:
        resolved = _resolve_n_trials(n_trials, m, path, ledger_path)
        if resolved is None:
            return GateResult(
                passed=False,
                failed_gates=["G7"],
                reasons=[
                    "G7 deflated_sharpe > 0.0: MISSING_N_TRIALS — DSR must be "
                    "computed from the real trial count; pass n_trials=, set "
                    "'n_trials' in metrics.json, or make the strategy "
                    "resolvable in results-ledger.json (refusing the old "
                    "hard-coded default of 100)"
                ],
                metrics=m,
            )
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "validation"))
            from cpcv import deflated_sharpe
            sharpe = m["cpcv_mean_oos_sharpe"]
            sample_len = m.get("n_bars", m.get("n_bars_total", 365 * 4))  # default 1y 6h bars
            dsr = deflated_sharpe(
                sharpe, resolved, sample_len,
                trial_sharpe_var=m.get("trial_sharpe_var"),
            )
            m["deflated_sharpe"] = dsr
            m["n_trials"] = resolved
        except Exception:
            pass  # leave to gate to fail naturally

    return certify_metrics(m)


def _resolve_n_trials(
    n_trials: int | None,
    metrics: dict,
    metrics_path: Path,
    ledger_path: str | Path | None,
) -> int | None:
    """Resolve the real DSR trial count; None when it cannot be known."""
    if n_trials is not None:
        return int(n_trials)
    from_metrics = metrics.get("n_trials")
    if isinstance(from_metrics, (int, float)) and not isinstance(from_metrics, bool) and from_metrics >= 1:
        return int(from_metrics)
    return _n_trials_from_ledger(metrics_path, ledger_path)


def _infer_family(name: str) -> str:
    """Family inference — keep in sync with scripts/build_results_ledger.py."""
    parts = name.split("_")
    if parts[0] == "vpvr":
        return "_".join(parts[:3]) if len(parts) >= 3 else name
    if parts[0] in ("funding", "momentum", "trend", "vol", "bb", "pairs", "mtf", "loid"):
        return "_".join(parts[:2]) if len(parts) >= 2 else name
    return parts[0]


def _n_trials_from_ledger(metrics_path: Path, ledger_path: str | Path | None) -> int | None:
    """Derive family size from results-ledger.json.

    Finds this strategy's key in the ledger (by matching a path component of
    metrics_path against ledger strategy_keys), then counts ledger strategies
    in the same inferred family. Returns None when unresolvable.
    """
    ledger = Path(ledger_path) if ledger_path else (
        Path(__file__).resolve().parents[2] / "results-ledger.json"
    )
    try:
        with open(ledger) as f:
            entries = json.load(f).get("strategies", [])
    except Exception:
        return None
    keys = {e.get("strategy_key") for e in entries if e.get("strategy_key")}
    if not keys:
        return None
    resolved_parts = metrics_path.resolve().parts
    hit = next((p for p in resolved_parts if p in keys), None)
    if hit is None:
        return None
    family = _infer_family(hit)
    return sum(1 for k in keys if _infer_family(k) == family)


def main():
    """CLI: python -m _shared.gates.enforce <metrics.json> [n_trials]"""
    if len(sys.argv) < 2:
        print("usage: enforce.py <metrics.json> [n_trials]", file=sys.stderr)
        sys.exit(2)
    n_trials = int(sys.argv[2]) if len(sys.argv) > 2 else None
    result = certify_strategy(sys.argv[1], n_trials=n_trials)
    print(str(result))
    if result.reasons:
        for r in result.reasons:
            print(f"  - {r}")
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
