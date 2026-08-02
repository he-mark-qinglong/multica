"""Tests for _shared/ops/drift_monitor.py (H19)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.ops.alerting import AlertLevel
from _shared.ops.drift_monitor import (
    DriftThresholds,
    Fill,
    compute_drift,
    cumulative_pnl_deviation_bp,
    drift_alert,
    fill_price_deviation_bp,
    fill_rate_deviation,
)


def _fills(prices, qty=1.0, pnl=0.0):
    return [Fill(price=p, qty=qty, pnl=pnl) for p in prices]


# --- pure metrics ---------------------------------------------------------------
def test_price_deviation_bp_zero_when_matched():
    exp = _fills([100.0, 101.0, 99.0])
    assert fill_price_deviation_bp(exp, exp) == 0.0


def test_price_deviation_bp_sign_and_size():
    exp = _fills([100.0])
    live = _fills([100.05])  # +5bp
    assert fill_price_deviation_bp(live, exp) == pytest.approx(5.0)
    live = _fills([99.95])   # -5bp
    assert fill_price_deviation_bp(live, exp) == pytest.approx(-5.0)


def test_price_deviation_qty_weighted():
    exp = [Fill(100.0, qty=1.0), Fill(100.0, qty=3.0)]
    live = [Fill(100.10, qty=1.0), Fill(100.0, qty=3.0)]
    # (1*+10bp + 3*0bp) / 4 = +2.5bp
    assert fill_price_deviation_bp(live, exp) == pytest.approx(2.5)


def test_fill_rate_deviation():
    assert fill_rate_deviation(100, 100) == 0.0
    assert fill_rate_deviation(80, 100) == pytest.approx(-0.2)
    assert fill_rate_deviation(120, 100) == pytest.approx(0.2)
    assert fill_rate_deviation(0, 0) == 0.0


def test_pnl_deviation_bp():
    exp = [Fill(100.0, qty=1.0, pnl=1.0)]   # notional 100
    live = [Fill(100.0, qty=1.0, pnl=0.5)]
    assert cumulative_pnl_deviation_bp(live, exp) == pytest.approx(-50.0)


# --- report + thresholds --------------------------------------------------------
def test_report_ok_within_thresholds():
    exp = _fills([100.0, 100.0, 100.0], pnl=0.1)
    live = _fills([100.01, 100.0, 99.99], pnl=0.1)
    report = compute_drift(live, exp)
    assert report.ok
    assert report.breaches == ()


def test_report_breach_on_price_dev():
    exp = _fills([100.0] * 10)
    live = _fills([100.10] * 10)  # +10bp vs 5bp limit
    report = compute_drift(live, exp)
    assert not report.ok
    assert any("price_dev_bp" in b for b in report.breaches)


def test_report_breach_on_fill_rate():
    exp = _fills([100.0] * 10)
    live = _fills([100.0] * 7)   # -30% vs 20% limit
    report = compute_drift(live, exp)
    assert not report.ok
    assert any("fill_rate_dev" in b for b in report.breaches)


def test_report_breach_on_pnl_dev():
    exp = [Fill(100.0, qty=1.0, pnl=1.0)] * 4    # notional 400
    live = [Fill(100.0, qty=1.0, pnl=-1.0)] * 4  # dev = -8/400 = -200bp
    report = compute_drift(live, exp)
    assert not report.ok
    assert any("pnl_dev_bp" in b for b in report.breaches)


def test_min_fills_gate_skips_evaluation():
    exp = _fills([100.0])
    live = _fills([200.0])  # absurd dev, but below min_fills
    report = compute_drift(live, exp, DriftThresholds(min_fills=5))
    assert report.ok  # not enough data to judge


def test_drift_alert_critical_on_breach():
    exp = _fills([100.0] * 10)
    live = _fills([100.20] * 10)
    report = compute_drift(live, exp)
    alert = drift_alert(report, strategy="mm_btc", now=123.0)
    assert alert is not None
    assert alert.level == AlertLevel.CRITICAL.value
    assert alert.rule == "live_backtest_drift"
    assert "mm_btc" in alert.message
    assert alert.ts == 123.0


def test_drift_alert_none_when_ok():
    exp = _fills([100.0] * 3, pnl=0.1)
    assert drift_alert(compute_drift(exp, exp)) is None
