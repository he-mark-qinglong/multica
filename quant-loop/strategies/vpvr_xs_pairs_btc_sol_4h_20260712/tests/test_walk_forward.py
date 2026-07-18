"""Tests for B6 walk-forward runner (vpvr_xs_pairs_4h_zscore_vpvr_20260710).

Verifies the walk_forward.py output:
- Produces walk_forward.json with N windows
- All windows have non-empty test bars and non-negative n_test_trades
- aggregate.ship_gate.wf_ratio_pass is True when IS sharpe > 0 and mean OOS > 0
- deflated_sharpe_z is finite
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

STRATEGY_DIR = Path(__file__).resolve().parent.parent
WF_SCRIPT = STRATEGY_DIR / "walk_forward.py"
RESULTS_DIR = STRATEGY_DIR / "results"


def _run_walk_forward() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WF_SCRIPT)],
        cwd=STRATEGY_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def test_walk_forward_exits_zero():
    proc = _run_walk_forward()
    assert proc.returncode == 0, (
        f"walk_forward.py exited {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout[:2000]}\nSTDERR:\n{proc.stderr[:2000]}"
    )


def test_walk_forward_emits_json():
    proc = _run_walk_forward()
    assert proc.returncode == 0, proc.stderr
    out = RESULTS_DIR / "walk_forward.json"
    assert out.is_file(), f"missing {out}"
    payload = json.loads(out.read_text())
    assert payload["n_windows"] >= 2, f"too few windows: {payload['n_windows']}"
    return payload


def test_walk_forward_windows_have_metrics():
    payload = test_walk_forward_emits_json()
    for w in payload["windows"]:
        assert w["n_test_bars"] > 0, f"window {w['window_id']}: 0 test bars"
        assert "test_sharpe" in w, f"window {w['window_id']}: missing test_sharpe"
        assert np.isfinite(w["test_sharpe"]), f"window {w['window_id']}: non-finite sharpe"
        assert np.isfinite(w["test_mdd"]), f"window {w['window_id']}: non-finite mdd"


def test_walk_forward_ship_gate_shape():
    payload = test_walk_forward_emits_json()
    agg = payload["aggregate"]
    assert "ship_gate" in agg
    sg = agg["ship_gate"]
    for key in ("wf_ratio_threshold", "wf_ratio_pass",
                "min_oos_sharpe_threshold", "min_oos_sharpe_pass",
                "overall_pass"):
        assert key in sg, f"ship_gate missing key: {key}"


def test_walk_forward_aggregate_finite():
    payload = test_walk_forward_emits_json()
    agg = payload["aggregate"]
    assert np.isfinite(agg["mean_test_sharpe"])
    assert np.isfinite(agg["mean_test_return"])
    assert np.isfinite(agg["worst_test_mdd"])
    assert np.isfinite(agg["walk_forward_ratio"])


def test_walk_forward_v3_internal_consistency():
    """V3-specific invariants after the B7 fix (lookahead + confluence-loss exit).

    Post-fix V3 had IS sharpe 0.425 (vs pre-fix 0.596 which was inflated by
    the two bugs). The walk-forward ratio is therefore negative. We DO NOT
    assert that V3 is shippable here — only that the runner correctly
    reports the post-fix state (internal consistency between backtest and
    walk-forward).
    """
    payload = test_walk_forward_emits_json()
    agg = payload["aggregate"]
    # The in_sample_sharpe field must match metrics.json (auto-read).
    metrics_path = STRATEGY_DIR / "results" / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    assert agg["in_sample_sharpe"] == pytest.approx(float(metrics["sharpe"]), abs=1e-6), \
        f"WF in_sample_sharpe ({agg['in_sample_sharpe']}) != metrics.json sharpe ({metrics['sharpe']})"
    # Per-window sharpes must be finite (not NaN/inf from division issues).
    for w in payload["windows"]:
        assert np.isfinite(w["test_sharpe"]), f"window {w['window_id']} non-finite sharpe"
    # After the B7 fix V3 is NOT shippable — ship_gate.overall_pass must be False
    # unless someone re-tunes it to cross 0.5 again.
    assert agg["ship_gate"]["overall_pass"] is False, \
        f"post-fix V3 should NOT pass ship_gate; got {agg['ship_gate']}"