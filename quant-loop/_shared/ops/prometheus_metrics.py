"""Prometheus-compatible metrics exporter for live trading operations.

Provides a lightweight HTTP metrics endpoint that exposes trading system
metrics in Prometheus text exposition format. No external dependencies —
uses stdlib http.server only.

Usage:
    exporter = MetricsExporter(port=9090)
    exporter.set_gauge("position_btc", 0.5)
    exporter.set_gauge("pnl_usd", 1234.56)
    exporter.inc_counter("trades_total", labels={"side": "buy"})
    exporter.start()  # non-blocking HTTP server on :9090

    # Scrape at http://localhost:9090/metrics
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


@dataclass
class _MetricStore:
    """Thread-safe metric storage."""
    gauges: dict = field(default_factory=dict)      # name → (value, labels_dict)
    counters: dict = field(default_factory=dict)     # (name, labels_key) → value
    histograms: dict = field(default_factory=dict)   # name → {"buckets": [...], "counts": [...], "sum": float, "count": int}
    histograms_config: dict = field(default_factory=dict)  # name → bucket boundaries
    _lock: threading.Lock = field(default_factory=threading.Lock)


def _labels_key(labels: dict | None) -> str:
    """Serialize labels dict to a stable key."""
    if not labels:
        return ""
    return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


class MetricsExporter:
    """Prometheus-format metrics exporter.

    Exposes /metrics endpoint with standard Prometheus text format.
    Supports gauges, counters, and histograms.
    """

    def __init__(
        self,
        port: int = 9090,
        host: str = "127.0.0.1",
        default_buckets: list | None = None,
    ):
        self.port = port
        self.host = host
        self.store = _MetricStore()
        if default_buckets is None:
            self.store.histograms_config["__default__"] = [
                0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
            ]
        else:
            self.store.histograms_config["__default__"] = default_buckets
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- Gauge --
    def set_gauge(self, name: str, value: float, labels: dict | None = None):
        """Set a gauge metric."""
        with self.store._lock:
            key = (name, _labels_key(labels))
            self.store.gauges[key] = (value, labels or {})

    # -- Counter --
    def inc_counter(self, name: str, value: float = 1, labels: dict | None = None):
        """Increment a counter metric."""
        with self.store._lock:
            key = (name, _labels_key(labels))
            self.store.counters[key] = self.store.counters.get(key, 0.0) + value

    # -- Histogram --
    def observe(self, name: str, value: float, labels: dict | None = None):
        """Record a histogram observation."""
        with self.store._lock:
            if name not in self.store.histograms:
                buckets = self.store.histograms_config.get(name,
                            self.store.histograms_config["__default__"])
                self.store.histograms[name] = {
                    "buckets": sorted(buckets),
                    "counts": [0] * (len(buckets) + 1),
                    "sum": 0.0,
                    "count": 0,
                }
            h = self.store.histograms[name]
            h["sum"] += value
            h["count"] += 1
            for i, bound in enumerate(h["buckets"]):
                if value <= bound:
                    h["counts"][i] += 1
                    return
            h["counts"][-1] += 1  # +Inf bucket

    # -- Export --
    def export_text(self) -> str:
        """Export all metrics in Prometheus text exposition format."""
        with self.store._lock:
            lines = []

            # Gauges
            seen_gauge_names = set()
            for (name, labels_str), (value, labels) in sorted(self.store.gauges.items()):
                if name not in seen_gauge_names:
                    lines.append(f"# TYPE {name} gauge")
                    seen_gauge_names.add(name)
                label_part = "{" + labels_str + "}" if labels_str else ""
                lines.append(f"{name}{label_part} {value}")

            # Counters
            seen_counter_names = set()
            for (name, labels_str), value in sorted(self.store.counters.items()):
                if name not in seen_counter_names:
                    lines.append(f"# TYPE {name} counter")
                    seen_counter_names.add(name)
                label_part = "{" + labels_str + "}" if labels_str else ""
                lines.append(f"{name}{label_part} {value}")

            # Histograms
            for name, h in sorted(self.store.histograms.items()):
                lines.append(f"# TYPE {name} histogram")
                cumulative = 0
                for i, bound in enumerate(h["buckets"]):
                    cumulative += h["counts"][i]
                    lines.append(f'{name}_bucket{{le="{bound}"}} {cumulative}')
                cumulative += h["counts"][-1]
                lines.append(f'{name}_bucket{{le="+Inf"}} {cumulative}')
                lines.append(f'{name}_sum {h["sum"]}')
                lines.append(f'{name}_count {h["count"]}')

            return "\n".join(lines) + "\n"

    # -- HTTP server --
    def start(self):
        """Start the HTTP metrics server in a background thread."""
        if self._thread and self._thread.is_alive():
            return  # already running

        store = self.store

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/metrics":
                    text = store.gauges and MetricsExporter_export(store)
                    # Use the exporter's export_text via closure
                    body = exporter_ref.export_text().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/health":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass  # suppress access logs

        exporter_ref = self
        self._server = HTTPServer((self.host, self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None


def MetricsExporter_export(store: _MetricStore) -> str:
    """Standalone export function (for testing without HTTP)."""
    exporter = MetricsExporter.__new__(MetricsExporter)
    exporter.store = store
    return exporter.export_text()


# ---------------------------------------------------------------------------
# Trading-specific metric helpers
# ---------------------------------------------------------------------------

class TradingMetrics:
    """Pre-configured trading metrics for common use cases.

    Wraps MetricsExporter with domain-specific helpers for position,
    PnL, order, and risk metrics.
    """

    def __init__(self, exporter: MetricsExporter | None = None, symbol: str = "BTCUSDT"):
        self.exporter = exporter or MetricsExporter()
        self.symbol = symbol

    def record_position(self, size: float, entry_price: float, unrealized_pnl: float):
        """Record current position state."""
        self.exporter.set_gauge("position_size", size, {"symbol": self.symbol})
        self.exporter.set_gauge("position_entry_price", entry_price, {"symbol": self.symbol})
        self.exporter.set_gauge("position_unrealized_pnl", unrealized_pnl, {"symbol": self.symbol})

    def record_fill(self, side: str, price: float, qty: float, fee: float, latency_ms: float):
        """Record an order fill."""
        self.exporter.inc_counter("trades_total", 1, {"symbol": self.symbol, "side": side})
        self.exporter.inc_counter("volume_total", qty, {"symbol": self.symbol, "side": side})
        self.exporter.inc_counter("fees_total", fee, {"symbol": self.symbol})
        self.exporter.observe("fill_latency_ms", latency_ms, {"symbol": self.symbol})

    def record_equity(self, equity: float, drawdown: float, sharpe: float):
        """Record portfolio equity and risk metrics."""
        self.exporter.set_gauge("equity_usd", equity)
        self.exporter.set_gauge("drawdown_pct", drawdown)
        self.exporter.set_gauge("sharpe", sharpe)

    def record_risk(self, var_95: float, cvar_95: float, max_drawdown: float):
        """Record risk metrics."""
        self.exporter.set_gauge("var_95", var_95, {"symbol": self.symbol})
        self.exporter.set_gauge("cvar_95", cvar_95, {"symbol": self.symbol})
        self.exporter.set_gauge("max_drawdown", max_drawdown, {"symbol": self.symbol})
