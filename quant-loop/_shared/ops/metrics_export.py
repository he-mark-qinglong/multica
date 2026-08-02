"""Prometheus text exposition exporter (H7).

Registers gauges and counters (equity, position, fills, kill_switch_state,
...) and renders the standard Prometheus text-based exposition format, so a
scraper (or `curl localhost:PORT/metrics`) can read live runner state.

References:
- Prometheus exposition format spec:
  https://prometheus.io/docs/instrumenting/exposition_formats/#text-based-format
- Google SRE Book, ch. 6 "Monitoring Distributed Systems" — the four golden
  signals; gauges/counters map to latency/saturation/traffic/errors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

Labels = Tuple[Tuple[str, str], ...]  # sorted (key, value) pairs


def _norm_labels(labels: Optional[Mapping[str, str]] = None) -> Labels:
    return tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))


def _render_labels(labels: Labels) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


def _render_value(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value))


@dataclass(frozen=True)
class _MetricMeta:
    name: str
    kind: str   # "gauge" | "counter"
    help: str


class MetricsRegistry:
    """In-memory registry of gauges and counters keyed by (name, labels)."""

    def __init__(self) -> None:
        self._meta: Dict[str, _MetricMeta] = {}
        self._values: Dict[Tuple[str, Labels], float] = {}

    # -- registration -------------------------------------------------------
    def register_gauge(self, name: str, help: str = "") -> None:
        self._register(name, "gauge", help)

    def register_counter(self, name: str, help: str = "") -> None:
        self._register(name, "counter", help)

    def _register(self, name: str, kind: str, help: str) -> None:
        existing = self._meta.get(name)
        if existing is not None:
            if existing.kind != kind:
                raise ValueError(f"metric {name!r} already registered as {existing.kind}")
            return
        self._meta[name] = _MetricMeta(name=name, kind=kind, help=help or name)

    # -- mutation -----------------------------------------------------------
    def set_gauge(self, name: str, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        self._require_kind(name, "gauge")
        self._values[(name, _norm_labels(labels))] = float(value)

    def inc_counter(
        self,
        name: str,
        amount: float = 1.0,
        labels: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._require_kind(name, "counter")
        if amount < 0:
            raise ValueError("counters can only increase")
        key = (name, _norm_labels(labels))
        self._values[key] = self._values.get(key, 0.0) + float(amount)

    def get(self, name: str, labels: Optional[Mapping[str, str]] = None) -> float:
        return self._values.get((name, _norm_labels(labels)), 0.0)

    def _require_kind(self, name: str, kind: str) -> None:
        meta = self._meta.get(name)
        if meta is None:
            raise KeyError(f"metric {name!r} not registered")
        if meta.kind != kind:
            raise ValueError(f"metric {name!r} is a {meta.kind}, not a {kind}")

    # -- rendering ----------------------------------------------------------
    def render_prometheus(self) -> str:
        """Render the standard text exposition format. Pure w.r.t. state."""
        lines = []
        for name in sorted(self._meta):
            meta = self._meta[name]
            lines.append(f"# HELP {name} {meta.help}")
            lines.append(f"# TYPE {name} {meta.kind}")
            samples = sorted(lbl for (n, lbl) in self._values if n == name)
            for labels in samples:
                value = self._values[(name, labels)]
                lines.append(f"{name}{_render_labels(labels)} {_render_value(value)}")
        return "\n".join(lines) + ("\n" if lines else "")


def runner_metrics_registry() -> MetricsRegistry:
    """Pre-registered registry with the standard paper-runner metrics."""
    reg = MetricsRegistry()
    reg.register_gauge("quant_loop_equity", "Current equity in USD")
    reg.register_gauge("quant_loop_position", "Current net position in base units")
    reg.register_gauge("quant_loop_kill_switch_state", "1 if kill switch is latched, else 0")
    reg.register_counter("quant_loop_fills_total", "Cumulative number of fills")
    reg.register_counter("quant_loop_restarts_total", "Cumulative supervisor restarts")
    return reg


def snapshot_runner_state(
    registry: MetricsRegistry,
    state: Mapping[str, float],
    strategy: Optional[str] = None,
) -> MetricsRegistry:
    """Push a paper-runner state snapshot into the registry.

    Recognised state keys: ``equity``, ``position``, ``kill_switch`` (bool/int),
    ``fills`` (cumulative count -> set as gauge-style counter baseline is not
    supported; use inc semantics by passing the delta in ``fills_delta``).
    """
    labels = {"strategy": strategy} if strategy else None
    if "equity" in state:
        registry.set_gauge("quant_loop_equity", float(state["equity"]), labels)
    if "position" in state:
        registry.set_gauge("quant_loop_position", float(state["position"]), labels)
    if "kill_switch" in state:
        registry.set_gauge("quant_loop_kill_switch_state", float(bool(state["kill_switch"])), labels)
    if "fills_delta" in state:
        registry.inc_counter("quant_loop_fills_total", float(state["fills_delta"]), labels)
    return registry
