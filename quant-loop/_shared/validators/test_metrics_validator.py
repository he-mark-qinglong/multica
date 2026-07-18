"""Tests for metrics_validator. Run: pytest _shared/validators/test_metrics_validator.py"""
import math

import pytest

from _shared.validators.metrics_validator import safe_validate, validate_metrics


VALID = {
    "sharpe_daily": 1.5,
    "annualized_return": 0.22,
    "max_drawdown_pct": -0.18,
    "profit_factor": 1.8,
    "n_trades": 420,
    "n_bars": 525_600,
    "win_rate": 0.54,
    "calmar": 1.2,
    "sortino": 1.9,
}


def test_valid_metrics_pass():
    # should not raise
    validate_metrics(VALID, strategy_name="happy_path")


def test_max_dd_sentinel_minus_4e_minus_6_raises():
    # SMA-34922 sentinel
    m = {**VALID, "max_drawdown_pct": -4e-6}
    with pytest.raises(AssertionError, match="sentinel"):
        validate_metrics(m, strategy_name="sma-34922-regression")


def test_sharpe_nan_raises():
    m = {**VALID, "sharpe_daily": float("nan")}
    with pytest.raises(AssertionError, match="NaN"):
        validate_metrics(m, strategy_name="nan_case")


def test_sharpe_out_of_range_raises():
    m = {**VALID, "sharpe_daily": 100.0}
    with pytest.raises(AssertionError, match="outside expected range"):
        validate_metrics(m, strategy_name="oor_case")


def test_safe_validate_returns_false_tuple_on_bad_input():
    m = {**VALID, "max_drawdown_pct": -4e-6}
    ok, msg = safe_validate(m, strategy_name="safe_path")
    assert ok is False
    assert isinstance(msg, str) and "sentinel" in msg


def test_safe_validate_returns_true_on_good_input():
    ok, msg = safe_validate(VALID, strategy_name="safe_ok")
    assert ok is True
    assert msg == "ok"


def test_missing_keys_are_fine():
    # empty dict / partial dict must NOT raise
    validate_metrics({}, strategy_name="empty")
    validate_metrics({"sharpe_daily": 0.7}, strategy_name="partial")
