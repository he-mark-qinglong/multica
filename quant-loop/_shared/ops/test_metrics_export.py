"""Tests for _shared/ops/metrics_export.py (H7)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.ops.metrics_export import (
    MetricsRegistry,
    runner_metrics_registry,
    snapshot_runner_state,
)


def test_gauge_renders_help_type_and_sample():
    reg = MetricsRegistry()
    reg.register_gauge("quant_loop_equity", "Current equity in USD")
    reg.set_gauge("quant_loop_equity", 10123.5)
    out = reg.render_prometheus()
    assert "# HELP quant_loop_equity Current equity in USD" in out
    assert "# TYPE quant_loop_equity gauge" in out
    assert "quant_loop_equity 10123.5" in out
    assert out.endswith("\n")


def test_counter_increments_and_rejects_decrement():
    reg = MetricsRegistry()
    reg.register_counter("quant_loop_fills_total", "fills")
    reg.inc_counter("quant_loop_fills_total")
    reg.inc_counter("quant_loop_fills_total", 4)
    assert reg.get("quant_loop_fills_total") == 5.0
    with pytest.raises(ValueError):
        reg.inc_counter("quant_loop_fills_total", -1)


def test_labels_render_sorted():
    reg = MetricsRegistry()
    reg.register_gauge("quant_loop_position", "pos")
    reg.set_gauge("quant_loop_position", -0.5, {"symbol": "BTCUSDT", "strategy": "mm"})
    out = reg.render_prometheus()
    assert 'quant_loop_position{strategy="mm",symbol="BTCUSDT"} -0.5' in out


def test_unregistered_metric_raises():
    reg = MetricsRegistry()
    with pytest.raises(KeyError):
        reg.set_gauge("nope", 1.0)


def test_kind_mismatch_raises():
    reg = MetricsRegistry()
    reg.register_gauge("m", "m")
    with pytest.raises(ValueError):
        reg.inc_counter("m")
    with pytest.raises(ValueError):
        reg.register_counter("m", "m")


def test_runner_registry_snapshot_covers_h7_metrics():
    reg = runner_metrics_registry()
    snapshot_runner_state(
        reg,
        {"equity": 9999.0, "position": 0.25, "kill_switch": True, "fills_delta": 3},
        strategy="mm_btc",
    )
    out = reg.render_prometheus()
    assert 'quant_loop_equity{strategy="mm_btc"} 9999.0' in out
    assert 'quant_loop_position{strategy="mm_btc"} 0.25' in out
    assert 'quant_loop_kill_switch_state{strategy="mm_btc"} 1.0' in out
    assert 'quant_loop_fills_total{strategy="mm_btc"} 3.0' in out
    assert "# TYPE quant_loop_fills_total counter" in out
