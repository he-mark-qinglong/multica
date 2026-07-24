"""Tests for validation/gates.py — G1-G7 delegated to _shared/gates/enforce.py.

G7 is the Deflated Sharpe Ratio (Bailey-LdP 2014); the Bonferroni t-test path
was removed in the 2026-07-24 Phase B unification.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.gates import evaluate_gates


def _pooled_daily(n: int = 200, mu: float = 0.002, sigma: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(42)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.Series(rng.normal(mu, sigma, n), index=idx)


def _full_metrics(sharpe=1.5, ann=0.40, dd=-0.10, pf=2.0, dd_key="max_drawdown"):
    return {"BTC": {"sharpe": sharpe, "annualized_return": ann,
                    dd_key: dd, "profit_factor": pf}}


def _windows(sharpe=1.3, ann=0.30, n=3):
    return [{"sharpe": sharpe, "annualized_return": ann} for _ in range(n)]


def _passing_kwargs():
    return dict(
        full_metrics_by_symbol=_full_metrics(),
        window_native=_windows(sharpe=1.3),
        window_backtrader=_windows(sharpe=1.2),
        window_freqtrade=_windows(sharpe=1.1),
        pooled_oos_daily_returns=_pooled_daily(),
        pooled_oos_trade_pnls=[0.01] * 40 + [-0.005] * 10,
    )


def _gate(verdict, gid):
    return next(g for g in verdict.gates if g.gate == gid)


def test_all_gates_pass():
    v = evaluate_gates("variant_x", **_passing_kwargs())
    assert v.passed, f"expected PASS: {[g.line() for g in v.gates if not g.passed]}"
    assert [g.gate for g in v.gates] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "T1"]
    # summary renders one line per gate plus a header
    assert len(v.summary_lines()) == len(v.gates) + 1


def test_g1_fails_on_low_full_sharpe():
    kw = _passing_kwargs()
    kw["full_metrics_by_symbol"] = _full_metrics(sharpe=0.5)
    v = evaluate_gates("variant_x", **kw)
    assert not v.passed
    assert not _gate(v, "G1").passed
    assert _gate(v, "G1").observed == pytest.approx(0.5)


def test_g2_uses_min_of_full_and_oos_annualized():
    kw = _passing_kwargs()
    kw["window_native"] = _windows(sharpe=1.3, ann=0.10)  # OOS ann below 15%
    v = evaluate_gates("variant_x", **kw)
    g2 = _gate(v, "G2")
    assert not g2.passed
    assert g2.observed == pytest.approx(0.10)


def test_g3_drawdown_negative_convention():
    kw = _passing_kwargs()
    kw["full_metrics_by_symbol"] = _full_metrics(dd=-0.30)  # worse than -0.25
    v = evaluate_gates("variant_x", **kw)
    g3 = _gate(v, "G3")
    assert not g3.passed
    assert g3.observed == pytest.approx(-0.30)
    assert g3.threshold == pytest.approx(-0.25)


def test_g3_normalizes_legacy_positive_drawdown():
    """Legacy metrics dicts store max_drawdown as a POSITIVE fraction; the gate
    must treat 0.30 the same as -0.30."""
    kw = _passing_kwargs()
    kw["full_metrics_by_symbol"] = _full_metrics(dd=0.30)  # positive convention
    v = evaluate_gates("variant_x", **kw)
    assert not _gate(v, "G3").passed
    assert _gate(v, "G3").observed == pytest.approx(-0.30)


def test_g3_accepts_max_drawdown_pct_key():
    kw = _passing_kwargs()
    kw["full_metrics_by_symbol"] = _full_metrics(dd=-0.10, dd_key="max_drawdown_pct")
    v = evaluate_gates("variant_x", **kw)
    assert _gate(v, "G3").passed


def test_g5_fails_when_worst_framework_below_one():
    kw = _passing_kwargs()
    kw["window_freqtrade"] = _windows(sharpe=0.4)
    v = evaluate_gates("variant_x", **kw)
    g5 = _gate(v, "G5")
    assert not g5.passed
    assert g5.observed == pytest.approx(0.4)


def test_g5_skipped_when_no_framework_windows():
    kw = _passing_kwargs()
    kw["window_backtrader"] = []
    kw["window_freqtrade"] = []
    v = evaluate_gates("variant_x", **kw)
    assert _gate(v, "G5").passed  # enforce.py skips G5 on NaN


def test_g7_is_dsr_not_bonferroni():
    """G7 must depend on the DSR of the OOS Sharpe — not on a t-test over
    pooled trade pnls. Here the trade pnls are overwhelmingly positive (a
    Bonferroni t-test would easily pass) but the OOS Sharpe is ~0, so DSR < 0
    and G7 must FAIL."""
    kw = _passing_kwargs()
    kw["window_native"] = _windows(sharpe=0.0)
    kw["pooled_oos_trade_pnls"] = [0.01] * 200  # t-test would pass easily
    v = evaluate_gates("variant_x", **kw)
    g7 = _gate(v, "G7")
    assert not g7.passed
    assert g7.threshold == 0.0
    assert "Deflated Sharpe" in g7.detail


def test_g7_dsr_hurdle_grows_with_n_trials():
    """A mediocre OOS Sharpe that clears DSR at n_trials=1 must fail once the
    multiple-testing hurdle of a large family is applied."""
    kw = _passing_kwargs()
    kw["window_native"] = _windows(sharpe=0.25)
    v_small = evaluate_gates("variant_x", n_trials=1, **kw)
    v_large = evaluate_gates("variant_x", n_trials=10_000, **kw)
    assert _gate(v_small, "G7").observed > _gate(v_large, "G7").observed
    assert not _gate(v_large, "G7").passed


def test_t1_trade_floor():
    kw = _passing_kwargs()
    kw["pooled_oos_trade_pnls"] = [0.01] * 10  # below 30-trade floor
    v = evaluate_gates("variant_x", **kw)
    t1 = _gate(v, "T1")
    assert not t1.passed
    assert t1.observed == pytest.approx(10.0)
    assert not v.passed
