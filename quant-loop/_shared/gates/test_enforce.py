"""Tests for gate enforcement.

Run: python3 _shared/gates/test_enforce.py

Uses plain asserts (no pytest assumed). Prints "N/N tests passed" at the end.
Covers: all-pass, per-gate failures, missing-field FAIL semantics
(MISSING_FIELD:<name>), and the DSR auto-compute path.
"""
import os
import sys
import tempfile

# Allow direct execution: add repo root (three levels up) to sys.path so the
# `_shared` package is importable without pytest's rootdir injection.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from _shared.gates.enforce import certify_metrics, certify_strategy, GateResult


# A metrics dict that satisfies every gate.
PASSING = {
    "sharpe_daily": 1.5,
    "annualized_return": 0.30,
    "max_drawdown_pct": -0.10,
    "profit_factor": 2.0,
    "cpcv_mean_oos_sharpe": 1.2,
    "bootstrap_ci95_lower": 0.8,
    "deflated_sharpe": 0.6,
    "n_trades": 100,
}


def test_all_pass_synthetic():
    result = certify_metrics(dict(PASSING))
    assert isinstance(result, GateResult)
    assert result.passed, f"expected PASS, got failures: {result.failed_gates}"
    assert result.failed_gates == []
    assert "PASS" in str(result)


def test_g3_max_drawdown_fail():
    m = dict(PASSING)
    m["max_drawdown_pct"] = -0.30  # worse than -0.25 threshold
    result = certify_metrics(m)
    assert not result.passed
    assert "G3" in result.failed_gates, f"G3 must be in failed list: {result.failed_gates}"


def test_g1_sharpe_fail():
    m = dict(PASSING)
    m["sharpe_daily"] = 0.5  # below 1.0
    result = certify_metrics(m)
    assert not result.passed
    assert "G1" in result.failed_gates, f"G1 must be in failed list: {result.failed_gates}"


def test_missing_fields_strict_fails():
    # Empty dict: every gate with a missing-key default falls through to a
    # failing value. strict=True must report a failure.
    result = certify_metrics({}, strict=True)
    assert not result.passed
    assert len(result.failed_gates) > 0


def test_missing_cpcv_fails_g5_both_modes():
    # All gates good except cpcv_mean_oos_sharpe omitted. Missing required
    # field must be an explicit FAIL in BOTH strict and non-strict mode —
    # "gate skip = pass" is the bug this guards against.
    m = {
        "sharpe_daily": 1.5,
        "annualized_return": 0.30,
        "max_drawdown_pct": -0.10,
        "profit_factor": 2.0,
        "bootstrap_ci95_lower": 0.8,
        "deflated_sharpe": 0.6,
        "n_trades": 100,
        # no cpcv_mean_oos_sharpe
    }
    for strict in (True, False):
        result = certify_metrics(m, strict=strict)
        assert not result.passed, f"strict={strict}: missing cpcv must FAIL"
        assert "G5" in result.failed_gates
        assert any("MISSING_FIELD:cpcv_mean_oos_sharpe" in r for r in result.reasons), \
            f"expected MISSING_FIELD reason, got: {result.reasons}"


def test_nan_cpcv_fails_g5():
    # NaN counts as missing (validation/gates.py emits NaN when no framework
    # windows were run).
    m = dict(PASSING)
    m["cpcv_mean_oos_sharpe"] = float("nan")
    result = certify_metrics(m)
    assert not result.passed
    assert "G5" in result.failed_gates
    assert any("MISSING_FIELD:cpcv_mean_oos_sharpe" in r for r in result.reasons)


def test_sharpe_only_fails_all_other_gates():
    # The original bug: a strategy with only a Sharpe value got a virtual
    # PASS. Now every missing required field is an explicit named FAIL.
    result = certify_metrics({"sharpe_daily": 31.7})
    assert not result.passed
    assert "G1" not in result.failed_gates, "sharpe is present and above bar"
    for gid in ("G2", "G3", "G4", "G5", "G6", "G7", "T1"):
        assert gid in result.failed_gates, f"{gid} must fail as missing"
    missing = [r for r in result.reasons if "MISSING_FIELD:" in r]
    assert len(missing) == 7, f"expected 7 MISSING_FIELD reasons, got: {missing}"


def test_dsr_auto_compute_path():
    # cpcv_mean_oos_sharpe present, deflated_sharpe missing → certify_strategy
    # must compute DSR from cpcv.py and inject it into the metrics.
    m = {
        "sharpe_daily": 1.5,
        "annualized_return": 0.30,
        "max_drawdown_pct": -0.10,
        "profit_factor": 2.0,
        "cpcv_mean_oos_sharpe": 1.5,
        "bootstrap_ci95_lower": 0.8,
        "n_trades": 100,
        # deflated_sharpe intentionally omitted
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        import json
        json.dump(m, f)
        tmp_path = f.name
    try:
        result = certify_strategy(tmp_path, n_trials=120)
        assert "deflated_sharpe" in result.metrics, "DSR must be auto-computed when cpcv sharpe present"
        dsr = result.metrics["deflated_sharpe"]
        assert isinstance(dsr, float), f"DSR must be a float, got {type(dsr)}"
        # cpcv sharpe 1.5 over 120 trials / ~1460 bars should still clear the
        # multiple-testing hurdle comfortably.
        assert dsr > 0.0, f"DSR should be > 0 for sharpe 1.5, got {dsr}"
    finally:
        os.unlink(tmp_path)


def test_missing_file_returns_file_failure():
    result = certify_strategy("/nonexistent/path/metrics.json")
    assert not result.passed
    assert "FILE" in result.failed_gates


def _write_metrics(tmp_dir, m, strategy_dir=None):
    """Write metrics dict to a temp metrics.json, return its path."""
    import json
    if strategy_dir:
        d = os.path.join(tmp_dir, "strategies", strategy_dir, "results")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "metrics.json")
    else:
        path = os.path.join(tmp_dir, "metrics.json")
    with open(path, "w") as f:
        json.dump(m, f)
    return path


_METRICS_NO_DSR = {
    "sharpe_daily": 1.5,
    "annualized_return": 0.30,
    "max_drawdown_pct": -0.10,
    "profit_factor": 2.0,
    "cpcv_mean_oos_sharpe": 1.5,
    "bootstrap_ci95_lower": 0.8,
    "n_trades": 100,
    # deflated_sharpe intentionally omitted
}


def test_missing_n_trials_explicit_fail():
    # No explicit n_trials, none in metrics, no ledger match → explicit FAIL,
    # never the old hard-coded default of 100.
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_metrics(tmp, dict(_METRICS_NO_DSR))
        result = certify_strategy(path, ledger_path=os.path.join(tmp, "no_ledger.json"))
    assert not result.passed, "missing n_trials must FAIL, not default to 100"
    assert "G7" in result.failed_gates
    assert any("MISSING_N_TRIALS" in r for r in result.reasons), result.reasons
    assert "deflated_sharpe" not in result.metrics


def test_n_trials_from_metrics_field():
    # metrics.json carries its own real trial count → used directly.
    from _shared.validation.cpcv import deflated_sharpe
    m = dict(_METRICS_NO_DSR, n_trials=7, n_bars=1460)
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_metrics(tmp, m)
        result = certify_strategy(path, ledger_path=os.path.join(tmp, "no_ledger.json"))
    expected = deflated_sharpe(1.5, 7, 1460)
    assert abs(result.metrics["deflated_sharpe"] - expected) < 1e-12, (
        f"{result.metrics['deflated_sharpe']} vs {expected}"
    )
    assert result.metrics["n_trials"] == 7


def test_n_trials_derived_from_results_ledger():
    # No explicit n_trials → derived from the results-ledger family:
    # target strategy's family (vpvr_a_b_*) has 2 entries in the ledger.
    import json
    from _shared.validation.cpcv import deflated_sharpe
    ledger = {
        "strategies": [
            {"strategy_key": "vpvr_a_b_1d_20260801"},
            {"strategy_key": "vpvr_a_b_4h_20260801"},
            {"strategy_key": "vpvr_a_c_1d_20260801"},
            {"strategy_key": "momentum_trend_1h_20260801"},
        ]
    }
    m = dict(_METRICS_NO_DSR, n_bars=1460)
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_metrics(tmp, m, strategy_dir="vpvr_a_b_1d_20260801")
        ledger_path = os.path.join(tmp, "results-ledger.json")
        with open(ledger_path, "w") as f:
            json.dump(ledger, f)
        result = certify_strategy(path, ledger_path=ledger_path)
    expected = deflated_sharpe(1.5, 2, 1460)
    assert abs(result.metrics["deflated_sharpe"] - expected) < 1e-12, (
        f"family-derived n_trials=2: {result.metrics['deflated_sharpe']} vs {expected}"
    )
    assert result.metrics["n_trials"] == 2


def test_explicit_n_trials_beats_metrics_and_ledger():
    from _shared.validation.cpcv import deflated_sharpe
    m = dict(_METRICS_NO_DSR, n_trials=7, n_bars=1460)
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_metrics(tmp, m)
        result = certify_strategy(path, n_trials=120)
    expected = deflated_sharpe(1.5, 120, 1460)
    assert abs(result.metrics["deflated_sharpe"] - expected) < 1e-12


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(f"\n{passed}/{total} tests passed")
    return failed == 0


if __name__ == "__main__":
    ok = _run_all()
    sys.exit(0 if ok else 1)
