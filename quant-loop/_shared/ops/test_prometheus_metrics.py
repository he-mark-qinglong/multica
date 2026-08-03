"""Tests for Prometheus metrics exporter."""
import pytest
import time
import urllib.request

from _shared.ops.prometheus_metrics import MetricsExporter, TradingMetrics


class TestGauges:
    def test_set_and_export(self):
        m = MetricsExporter(port=0)
        m.set_gauge("temperature", 42.5)
        text = m.export_text()
        assert "temperature 42.5" in text
        assert "# TYPE temperature gauge" in text

    def test_with_labels(self):
        m = MetricsExporter(port=0)
        m.set_gauge("price", 50000, {"symbol": "BTC"})
        text = m.export_text()
        assert 'price{symbol="BTC"}' in text

    def test_overwrite(self):
        m = MetricsExporter(port=0)
        m.set_gauge("count", 1)
        m.set_gauge("count", 5)
        text = m.export_text()
        assert "count 5" in text


class TestCounters:
    def test_increment(self):
        m = MetricsExporter(port=0)
        m.inc_counter("trades", 1)
        m.inc_counter("trades", 2)
        text = m.export_text()
        assert "trades 3" in text
        assert "# TYPE trades counter" in text

    def test_with_labels(self):
        m = MetricsExporter(port=0)
        m.inc_counter("volume", 100, {"side": "buy"})
        m.inc_counter("volume", 50, {"side": "sell"})
        text = m.export_text()
        assert 'volume{side="buy"}' in text
        assert 'volume{side="sell"}' in text


class TestHistograms:
    def test_observe(self):
        m = MetricsExporter(port=0)
        for v in [0.01, 0.05, 0.3, 1.5, 10.0]:
            m.observe("latency", v)
        text = m.export_text()
        assert "# TYPE latency histogram" in text
        assert "latency_count 5" in text
        assert "latency_sum" in text

    def test_buckets_cumulative(self):
        m = MetricsExporter(port=0)
        m.observe("req", 0.01)
        m.observe("0.5", 0.5)  # label is name
        text = m.export_text()
        # le="0.01" should have count 1
        assert 'latency_bucket' not in text  # we used "req" not "latency"
        assert 'req_bucket{le="0.01"}' in text


class TestHttpServer:
    def test_serves_metrics(self):
        m = MetricsExporter(port=19199, host="127.0.0.1")
        m.set_gauge("test_metric", 123)
        m.start()
        try:
            time.sleep(0.3)
            resp = urllib.request.urlopen("http://127.0.0.1:19199/metrics")
            text = resp.read().decode()
            assert "test_metric 123" in text
            assert resp.status == 200
        finally:
            m.stop()

    def test_health_endpoint(self):
        m = MetricsExporter(port=19198, host="127.0.0.1")
        m.start()
        try:
            time.sleep(0.3)
            resp = urllib.request.urlopen("http://127.0.0.1:19198/health")
            assert resp.read() == b"ok"
        finally:
            m.stop()


class TestTradingMetrics:
    def test_record_position(self):
        exporter = MetricsExporter(port=0)
        tm = TradingMetrics(exporter, symbol="BTCUSDT")
        tm.record_position(size=0.5, entry_price=50000, unrealized_pnl=100)
        text = exporter.export_text()
        assert "position_size" in text
        assert "position_unrealized_pnl" in text

    def test_record_fill(self):
        exporter = MetricsExporter(port=0)
        tm = TradingMetrics(exporter)
        tm.record_fill(side="buy", price=50000, qty=0.1, fee=1.0, latency_ms=12.5)
        text = exporter.export_text()
        assert "trades_total" in text
        assert "fill_latency_ms" in text

    def test_record_equity_and_risk(self):
        exporter = MetricsExporter(port=0)
        tm = TradingMetrics(exporter)
        tm.record_equity(equity=10500, drawdown=-0.02, sharpe=1.5)
        tm.record_risk(var_95=0.03, cvar_95=0.05, max_drawdown=-0.15)
        text = exporter.export_text()
        assert "equity_usd" in text
        assert "var_95" in text
        assert "cvar_95" in text
