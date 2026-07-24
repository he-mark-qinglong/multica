"""Tests for the pre-registered CPCV template (Phase D — HF pipeline).

Synthetic-data verification that the template:
  - runs the pre-registered flow end-to-end through _shared/validation/cpcv,
  - emits the expected per-candidate fields (mean Sharpe, worst-fold, DSR,
    9-key aggregate from compute_metrics),
  - applies the DSR multiple-testing penalty correctly,
  - implements first-pass-in-registration-order selection (no OOS re-ranking),
  - degrades gracefully when no folds are usable.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from _shared.templates.preregistered_cpcv import (
    DEFAULT_GATES,
    decide_chosen,
    evaluate_candidate,
    run_preregistered_cpcv,
    write_results,
)

# Small harness config for fast synthetic tests (defaults are sized for 4h bars).
TEST_CPCV_CONFIG = {
    "n_groups": 6,
    "k_test": 2,
    "purge_bars": 10,
    "embargo_bars": 5,
    "periods_per_year": 365 * 24,  # hourly bars
}

AGGREGATE_KEYS = {
    "sharpe_daily",
    "annualized_return",
    "max_drawdown_pct",
    "profit_factor",
    "n_trades",
    "n_bars",
    "win_rate",
    "calmar",
    "sortino",
}


def _trending_walk(n: int = 2400, seed: int = 7) -> pd.DataFrame:
    """Synthetic trending random walk (hourly bars)."""
    rng = np.random.default_rng(seed)
    log_ret = 0.00015 + rng.normal(0, 0.004, size=n)
    price = 100.0 * np.exp(np.cumsum(log_ret))
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    return pd.DataFrame({"close": price}, index=idx)


def _momentum_signal(params: dict, data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
    """Rolling-mean momentum; window from candidate params.

    Fits nothing beyond the signal itself (template tests only need the
    refit-on-train plumbing exercised, which the CPCV harness covers).
    """
    window = int(params["window"])
    ret = data_full["close"].pct_change().fillna(0.0)
    pos = np.sign(data_full["close"].rolling(window).mean().diff())
    return (pos.shift(1).fillna(0.0) * ret).astype(float)


CANDIDATES = [
    {"label": "mom_w20", "rationale": "fast momentum", "params": {"window": 20}},
    {"label": "mom_w100", "rationale": "slow momentum", "params": {"window": 100}},
]


def test_run_preregistered_outputs_expected_fields():
    """Full pipeline: every candidate gets mean/worst-fold/DSR + 9-key aggregate."""
    data = _trending_walk()
    env = run_preregistered_cpcv(CANDIDATES, data, _momentum_signal, TEST_CPCV_CONFIG)

    assert env["n_trials"] == len(CANDIDATES)
    assert env["cpcv_config"]["n_groups"] == 6
    assert len(env["pre_registered_candidates"]) == len(CANDIDATES)

    for res in env["pre_registered_candidates"]:
        for key in (
            "label",
            "rationale",
            "params",
            "n_paths",
            "folds_complete",
            "mean_oos_sharpe",
            "std_oos_sharpe",
            "worst_oos_sharpe",
            "total_oos_trades",
            "trades_per_fold",
            "deflated_sharpe",
            "aggregate",
            "folds",
        ):
            assert key in res, f"missing key: {key}"
        # C(6,2) = 15 paths; all folds usable on 2400-bar synthetic data
        assert res["n_paths"] == 15
        assert res["folds_complete"] == 15
        assert math.isfinite(res["mean_oos_sharpe"])
        assert math.isfinite(res["worst_oos_sharpe"])
        assert math.isfinite(res["deflated_sharpe"])
        assert res["worst_oos_sharpe"] <= res["mean_oos_sharpe"]
        assert AGGREGATE_KEYS.issubset(res["aggregate"].keys())
        assert len(res["folds"]) == res["folds_complete"]
        fold0 = res["folds"][0]
        for fkey in ("train_start", "train_end", "test_start", "test_end", "oos_sharpe", "n_trades"):
            assert fkey in fold0, f"missing fold key: {fkey}"


def test_candidate_params_reach_signal_fn():
    """Each candidate's params dict is forwarded to the signal function."""
    seen = []

    def spy_signal(params, data_train, data_full):
        seen.append(dict(params))
        return _momentum_signal(params, data_train, data_full)

    data = _trending_walk()
    run_preregistered_cpcv(CANDIDATES, data, spy_signal, TEST_CPCV_CONFIG)

    # Signal fn is invoked once per fold per candidate; both param sets appear.
    windows = {p["window"] for p in seen}
    assert windows == {20, 100}


def test_dsr_no_penalty_when_single_trial():
    """n_trials=1 → DSR equals mean OOS Sharpe (no multiple-testing hurdle)."""
    data = _trending_walk()
    res = evaluate_candidate(CANDIDATES[0], data, _momentum_signal, TEST_CPCV_CONFIG, n_trials=1)
    assert res["deflated_sharpe"] == pytest.approx(res["mean_oos_sharpe"])


def test_dsr_penalizes_multiple_trials():
    """Same candidate, larger family → strictly lower DSR."""
    data = _trending_walk()
    r1 = evaluate_candidate(CANDIDATES[0], data, _momentum_signal, TEST_CPCV_CONFIG, n_trials=1)
    r6 = evaluate_candidate(CANDIDATES[0], data, _momentum_signal, TEST_CPCV_CONFIG, n_trials=6)
    assert r6["deflated_sharpe"] < r1["deflated_sharpe"]


def test_decide_chosen_first_pass_in_registration_order():
    """Selection is first-pass-in-order, never OOS re-ranking."""
    gates = {"min_mean_oos_sharpe": 0.5, "min_worst_fold_sharpe": 0.0, "min_deflated_sharpe": 0.0, "min_total_trades": 0}
    results = [
        # First candidate FAILS worst-fold gate.
        {"label": "a", "mean_oos_sharpe": 0.9, "worst_oos_sharpe": -0.1, "deflated_sharpe": 0.8, "total_oos_trades": 100},
        # Second passes all gates → chosen even though third scores higher.
        {"label": "b", "mean_oos_sharpe": 0.6, "worst_oos_sharpe": 0.1, "deflated_sharpe": 0.4, "total_oos_trades": 100},
        {"label": "c", "mean_oos_sharpe": 2.0, "worst_oos_sharpe": 1.0, "deflated_sharpe": 1.5, "total_oos_trades": 100},
    ]
    chosen, verdict = decide_chosen(results, gates)
    assert verdict == "PASS-OPTIMIZED"
    assert chosen["label"] == "b"


def test_decide_chosen_skips_non_finite_and_kills():
    """NaN candidates are skipped; if none pass, verdict is KILL."""
    results = [
        {"label": "nan", "mean_oos_sharpe": float("nan"), "worst_oos_sharpe": float("nan"), "deflated_sharpe": float("nan"), "total_oos_trades": 0},
        {"label": "weak", "mean_oos_sharpe": 0.1, "worst_oos_sharpe": -0.5, "deflated_sharpe": -0.2, "total_oos_trades": 10},
    ]
    chosen, verdict = decide_chosen(results, DEFAULT_GATES)
    assert chosen is None
    assert verdict == "KILL"


def test_empty_folds_degrade_to_nan_and_kill():
    """Data too short for any usable fold → NaN fields, KILL verdict."""
    data = _trending_walk(n=120)  # below the 100-train / 30-test minima per fold
    env = run_preregistered_cpcv(CANDIDATES[:1], data, _momentum_signal, TEST_CPCV_CONFIG, gates=DEFAULT_GATES)
    res = env["pre_registered_candidates"][0]
    assert res["folds_complete"] == 0
    assert math.isnan(res["mean_oos_sharpe"])
    assert math.isnan(res["worst_oos_sharpe"])
    assert math.isnan(res["deflated_sharpe"])
    assert res["total_oos_trades"] == 0
    assert env["chosen_label"] is None
    assert env["verdict"] == "KILL"


def test_run_without_gates_skips_selection():
    """gates=None → pure evaluation, no verdict."""
    data = _trending_walk()
    env = run_preregistered_cpcv(CANDIDATES, data, _momentum_signal, TEST_CPCV_CONFIG)
    assert env["chosen_label"] is None
    assert env["verdict"] is None
    assert env["acceptance_gates"] is None


def test_write_results(tmp_path):
    """write_results emits parseable cpcv_metrics.json + cpcv_summary.txt."""
    data = _trending_walk()
    env = run_preregistered_cpcv(CANDIDATES, data, _momentum_signal, TEST_CPCV_CONFIG, gates=DEFAULT_GATES)
    paths = write_results(env, tmp_path)
    assert len(paths) == 2
    metrics, summary = paths
    loaded = json.loads(metrics.read_text())
    assert loaded["n_trials"] == 2
    assert len(loaded["pre_registered_candidates"]) == 2
    text = summary.read_text()
    assert "VERDICT:" in text
    for c in CANDIDATES:
        assert c["label"] in text
