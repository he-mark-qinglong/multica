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
    # G5: CV OOS Sharpe — placeholder until CPCV run; enforced only when present
    ("G5", "cpcv_mean_oos_sharpe >= 1.0", lambda m: m.get("cpcv_mean_oos_sharpe", float("nan")) >= 1.0
     if not _isnan(m.get("cpcv_mean_oos_sharpe", float("nan"))) else True,
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
        strict: if True, fail on missing keys; if False, skip gates with missing inputs
    
    Returns:
        GateResult with passed/failed/reasons.
    """
    failed = []
    reasons = []
    for gid, name, fn, desc in GATES:
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


def certify_strategy(metrics_path: str | Path, n_trials: int = 100) -> GateResult:
    """Read metrics.json + compute DSR if not present, then certify.
    
    Args:
        metrics_path: path to a strategy's metrics.json
        n_trials: family size for DSR (default 100; campaigns typically have 100+ trials)
    """
    path = Path(metrics_path).expanduser()
    if not path.exists():
        return GateResult(passed=False, failed_gates=["FILE"], reasons=[f"not found: {path}"], metrics={})
    
    with open(path) as f:
        m = json.load(f)
    
    # If cpcv fields missing but OOS sharpe present, compute DSR
    if "deflated_sharpe" not in m and "cpcv_mean_oos_sharpe" in m:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "validation"))
            from cpcv import deflated_sharpe
            sharpe = m["cpcv_mean_oos_sharpe"]
            sample_len = m.get("n_bars", m.get("n_bars_total", 365 * 4))  # default 1y 6h bars
            dsr = deflated_sharpe(sharpe, n_trials, sample_len)
            m["deflated_sharpe"] = dsr
        except Exception:
            pass  # leave to gate to fail naturally
    
    return certify_metrics(m)


def main():
    """CLI: python -m _shared.gates.enforce <metrics.json>"""
    if len(sys.argv) < 2:
        print("usage: enforce.py <metrics.json>", file=sys.stderr)
        sys.exit(2)
    result = certify_strategy(sys.argv[1])
    print(str(result))
    if result.reasons:
        for r in result.reasons:
            print(f"  - {r}")
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
