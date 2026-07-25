"""Tests for ``_shared.validation.validate_metrics`` + CLI behavior.

Positive example is synthesised on the fly via ``compute_metrics()`` because
every pre-existing ``results/metrics.json`` in the repo uses the legacy key
set (``sharpe``/``ann_return``/``max_drawdown``) and cannot serve as a
schema-passing reference. The h3 historical file is preserved as a *drift*
regression test — see ``test_real_h3_file_is_drift_example``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # quant-loop/

from _shared.validation.compute_metrics import compute_metrics  # noqa: E402
from _shared.templates.strategy_contract_v2 import make_synthetic_bars  # noqa: E402
from _shared.validation.validate_metrics import (  # noqa: E402
    REQUIRED_KEYS,
    PROVENANCE_KEYS,
    check_provenance,
    validate_metrics,
)


def _provenance() -> dict[str, object]:
    return {
        "strategy": "synthetic_test",
        "cost_bps_rt": 22.0,
        "data_window": "2026-01-01..2026-01-21",
        "generated_at": "2026-07-25T04:00:00Z",
    }


def _good_payload() -> dict[str, object]:
    """Synthesize a passing 9-key + provenance dict from synthetic bars."""
    bars = make_synthetic_bars(["SYNTH"], n_bars=500)
    close = bars["SYNTH"]["close"].astype(float)
    equity = close / close.iloc[0] * 100_000.0
    payload = compute_metrics(
        equity=equity,
        n_trades=3,
        trade_pnls=[0.01, -0.005, 0.02],
    )
    payload.update(_provenance())
    return payload


# ---------------------------------------------------------------------------
# Positive: synthesised payload passes both schema + provenance.
# ---------------------------------------------------------------------------
def test_valid_payload_passes():
    payload = _good_payload()
    # Direct: every required key is present (compute_metrics() may have
    # substituted 0.0 for empty inputs; we always get all 9 keys).
    assert set(payload.keys()) >= set(REQUIRED_KEYS)
    assert validate_metrics(payload) == []
    assert check_provenance(payload) == []


def test_constants_match_compute_metrics_source():
    """The validator's REQUIRED_KEYS must match the upstream dict literal."""
    # Smoke: build from compute_metrics and check the keys align 1:1.
    payload = compute_metrics(
        equity=__import__("pandas").Series([100.0, 101.0, 99.0, 102.0]),
        n_trades=2,
    )
    assert set(payload) == set(REQUIRED_KEYS)
    assert set(PROVENANCE_KEYS) == {
        "strategy", "cost_bps_rt", "data_window", "generated_at",
    }


# ---------------------------------------------------------------------------
# Schema violations.
# ---------------------------------------------------------------------------
def test_missing_key_fails():
    payload = _good_payload()
    del payload["calmar"]
    violations = validate_metrics(payload)
    assert any(v == "missing key: calmar" for v in violations), violations


def test_non_dict_payload_fails():
    violations = validate_metrics("not a dict")
    assert violations and "must be a dict" in violations[0]


def test_wrong_type_n_trades_float_fails():
    payload = _good_payload()
    payload["n_trades"] = 3.5  # type error — must be int
    violations = validate_metrics(payload)
    assert any("n_trades" in v and "int" in v for v in violations), violations


def test_wrong_type_sharpe_string_fails():
    payload = _good_payload()
    payload["sharpe_daily"] = "high"  # type error — must be finite number
    violations = validate_metrics(payload)
    assert any("sharpe_daily" in v for v in violations), violations


def test_bool_rejected_for_int_key():
    payload = _good_payload()
    payload["n_trades"] = True  # bool is an int subclass in Python — explicit reject
    violations = validate_metrics(payload)
    assert any("n_trades" in v for v in violations), violations


def test_nan_and_inf_rejected():
    payload = _good_payload()
    payload["sharpe_daily"] = float("nan")
    assert any("sharpe_daily" in v for v in validate_metrics(payload))
    payload2 = _good_payload()
    payload2["annualized_return"] = float("inf")
    assert any(
        "annualized_return" in v for v in validate_metrics(payload2)
    )


# ---------------------------------------------------------------------------
# Domain constraints.
# ---------------------------------------------------------------------------
def test_win_rate_above_one_fails():
    payload = _good_payload()
    payload["win_rate"] = 1.7
    assert any("win_rate" in v for v in validate_metrics(payload))


def test_max_drawdown_positive_fails():
    payload = _good_payload()
    payload["max_drawdown_pct"] = 0.05  # positive — illegal
    assert any("max_drawdown_pct" in v for v in validate_metrics(payload))


def test_negative_n_trades_fails():
    payload = _good_payload()
    payload["n_trades"] = -1
    assert any("n_trades" in v for v in validate_metrics(payload))


# ---------------------------------------------------------------------------
# Provenance: warn-only by default.
# ---------------------------------------------------------------------------
def test_provenance_warn_only():
    payload = _good_payload()
    for k in PROVENANCE_KEYS:
        payload.pop(k, None)
    # No schema violations: the 9-key block is intact.
    assert validate_metrics(payload) == []
    # Four provenance warnings, one per key.
    warnings = check_provenance(payload)
    assert len(warnings) == 4
    assert all("missing provenance" in w for w in warnings)


# ---------------------------------------------------------------------------
# Drift regression: real h3 historical file fails as expected.
# ---------------------------------------------------------------------------
_H3_DRIFT_FILE = (
    Path(__file__).resolve().parents[2]
    / "strategies"
    / "mtf_xs_pairs_1m_15m_2h_h3_20260718"
    / "results"
    / "metrics.json"
)


@pytest.mark.skipif(
    not _H3_DRIFT_FILE.exists(),
    reason="h3 historical metrics.json not present (slim checkout)",
)
def test_real_h3_file_is_drift_example():
    payload = json.loads(_H3_DRIFT_FILE.read_text(encoding="utf-8"))
    violations = validate_metrics(payload)
    assert violations, "h3 file should fail schema (legacy keys)"
    # Sharpest single-anchor check for the task's acceptance line.
    assert any(
        v == "missing key: sharpe_daily" for v in violations
    ), violations


@pytest.mark.skipif(
    not _H3_DRIFT_FILE.exists(),
    reason="h3 historical metrics.json not present (slim checkout)",
)
def test_cli_report_on_real_file():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "_shared.validation.validate_metrics",
            str(_H3_DRIFT_FILE),
            "--report",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    combined = proc.stdout + proc.stderr
    assert "FAIL" in combined
    assert "missing key: sharpe_daily" in combined


def test_cli_missing_file_returns_code_2():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "_shared.validation.validate_metrics",
            "/nonexistent/path/to/metrics.json",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 2


def test_cli_strict_provenance_promotes_warn_to_fail(tmp_path):
    # Build a payload file with provenance missing.
    payload = _good_payload()
    for k in PROVENANCE_KEYS:
        payload.pop(k, None)
    out = tmp_path / "m.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    # default: exit 0 (warnings only)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "_shared.validation.validate_metrics",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 0, proc.stderr
    # strict: exit 1
    proc2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "_shared.validation.validate_metrics",
            str(out),
            "--strict-provenance",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc2.returncode == 1, proc2.stderr
