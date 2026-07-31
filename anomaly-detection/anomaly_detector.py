"""Anomaly detection library — SMA-35770-088 (Monitor #88).

A small, dependency-free anomaly detector for time-series metrics in the
multica workspace. Consumes a stream of `MetricPoint`s (one per JSONL line),
runs a configurable bank of detectors per metric, and emits `Anomaly` records
when a point is flagged.

Detector families implemented:
    - ZScoreDetector           : rolling-window mean / std z-score
    - RobustZScoreDetector     : rolling median / MAD z-score (outlier-resistant)
    - EWMAZScoreDetector       : exponentially-weighted-mean / std z-score
    - IQRDetector              : Tukey fences [Q1-k*IQR, Q3+k*IQR]
    - RateOfChangeDetector     : spike on |Δvalue| / rolling σ(Δ)
    - BoundsDetector           : static [low, high] band

Each detector exposes the same `update(point, history)` interface so the
top-level `AnomalyDetector` can run any subset side by side.

Design notes:
    - Per-metric state: each (metric_name, detector) keeps its own state, so
      that the same detector applied to two metrics does not contaminate the
      baselines.
    - Pure stdlib (Python 3.8+). No third-party deps.
    - Severity: INFO (|score| in WARN_LO), WARNING (in CRIT_LO), CRITICAL
      (above CRIT_LO). The bands are calibrated for z-score style detectors;
      the IQR / RoC / bounds detectors normalize their score before mapping.
    - Cooldown: an anomaly on (metric, detector) within `cooldown_sec` of the
      previous one is suppressed, so dashboards don't get spammed. Default 60s.
    - Training phase: no anomalies are emitted until each (metric, detector)
      has seen at least `min_samples` points. Default 10.
    - All numeric accumulators use float; NaN / inf values are skipped.

Public surface (used by run.py and tests):
    MetricPoint                : dataclass for one observation
    Anomaly                    : dataclass for a flagged observation
    AnomalyReport              : aggregate counts + list
    DETECTORS                  : registry mapping detector name -> class
    SEVERITY_BANDS             : (info_lo, warn_lo) tuple, default (3.0, 5.0)
    AnomalyDetector            : top-level multi-detector runner
    stream_metrics(paths)      : iterate MetricPoints from JSONL files
    detect_stream(paths, ...)  : one-shot detection on a stream
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter, deque, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Severity bands applied to the *normalized* |score| reported by a detector.
# For z-style detectors, |score| is |z|. For IQR / RoC / Bounds the score is
# pre-normalized to a comparable scale (see _score_for_severity in the
# detectors). The mapping is intentionally simple: 3.0 -> INFO, 5.0 -> WARN,
# 8.0 -> CRIT.
SEVERITY_BANDS = {
    "INFO":     3.0,
    "WARNING":  5.0,
    "CRITICAL": 8.0,
}


@dataclass(frozen=True)
class MetricPoint:
    ts: float                 # unix epoch seconds (UTC)
    name: str                 # metric name, e.g. "cpu_pct", "api_latency_ms"
    value: float              # observed value
    labels: dict = field(default_factory=dict)

    def key(self) -> tuple:
        """Stable key that includes labels so two metrics with the same
        name but different labels do not share detector state."""
        # Sort labels so order does not matter for equality.
        return (self.name, tuple(sorted((self.labels or {}).items())))


@dataclass(frozen=True)
class Anomaly:
    ts: float
    name: str
    detector: str
    severity: str             # INFO / WARNING / CRITICAL
    score: float              # detector score (signed; magnitude used for severity)
    value: float
    baseline: float           # expected value (mean, median, prev value, etc.)
    spread: float             # expected spread (std, MAD, IQR, etc.)
    labels: dict = field(default_factory=dict)
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "ts": self.ts,
            "name": self.name,
            "detector": self.detector,
            "severity": self.severity,
            "score": round(self.score, 4),
            "value": self.value,
            "baseline": round(self.baseline, 4),
            "spread": round(self.spread, 4),
            "labels": dict(self.labels),
            "message": self.message,
        }


@dataclass
class AnomalyReport:
    total_points: int = 0
    anomalies: list = field(default_factory=list)        # list[Anomaly]
    points_seen: Counter = field(default_factory=Counter) # name -> count
    suppressed: Counter = field(default_factory=Counter)  # name -> suppressed count

    def by_severity(self) -> dict:
        c = Counter()
        for a in self.anomalies:
            c[a.severity] += 1
        return dict(c)

    def by_detector(self) -> dict:
        c = Counter()
        for a in self.anomalies:
            c[a.detector] += 1
        return dict(c)

    def by_metric(self) -> dict:
        c = Counter()
        for a in self.anomalies:
            c[a.name] += 1
        return dict(c)

    def as_dict(self) -> dict:
        return {
            "total_points": self.total_points,
            "anomaly_count": len(self.anomalies),
            "suppressed": dict(self.suppressed),
            "by_severity": self.by_severity(),
            "by_detector": self.by_detector(),
            "by_metric": self.by_metric(),
            "points_seen": dict(self.points_seen),
            "anomalies": [a.as_dict() for a in self.anomalies],
        }


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

def severity_for(score: float) -> str:
    """Map a normalized |score| to a severity label.

    |score| < SEVERITY_BANDS['INFO']     -> 'INFO'     (no anomaly flagged)
    |score| < SEVERITY_BANDS['WARNING']  -> 'WARNING'
    |score| < SEVERITY_BANDS['CRITICAL'] -> 'WARNING' (still anomaly)
    |score| >= SEVERITY_BANDS['CRITICAL']-> 'CRITICAL'

    Note: any anomaly is flagged only when |score| >= INFO threshold. Below
    that, the detector returns None (no anomaly).
    """
    m = abs(score)
    if m >= SEVERITY_BANDS["CRITICAL"]:
        return "CRITICAL"
    if m >= SEVERITY_BANDS["WARNING"]:
        return "WARNING"
    if m >= SEVERITY_BANDS["INFO"]:
        return "WARNING"
    return "INFO"


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

class Detector:
    """Abstract base — each detector holds per-key state and returns a
    list of Anomalies (usually 0 or 1) when given a new MetricPoint."""

    name: str = "base"

    def update(self, point: MetricPoint, history: deque) -> list:
        """Process one point. `history` is a deque of recent values for this
        metric (oldest first, point excluded). Returns [] or [Anomaly]."""
        raise NotImplementedError

    def reset(self) -> None:
        """Reset per-detector state. Called per (metric, key)."""
        pass


class ZScoreDetector(Detector):
    """Rolling-window z-score. Anomaly when |(x - mean) / std| > z_threshold."""

    name = "zscore"

    def __init__(self, window: int = 50, z_threshold: float = 3.0,
                 severity_override: Callable[[float], str] | None = None) -> None:
        self.window = max(2, int(window))
        self.z_threshold = float(z_threshold)
        self._severity = severity_override or severity_for

    def update(self, point: MetricPoint, history: deque) -> list:
        if len(history) < 2:
            return []
        n = len(history)
        mean = sum(history) / n
        # Sample std (ddof=1); falls back to 0 if all values identical.
        var = sum((v - mean) ** 2 for v in history) / (n - 1)
        std = math.sqrt(var)
        if std == 0 or not math.isfinite(std):
            # Degenerate window — nothing meaningful to compare against.
            return []
        z = (point.value - mean) / std
        if abs(z) < self.z_threshold:
            return []
        return [Anomaly(
            ts=point.ts,
            name=point.name,
            detector=self.name,
            severity=self._severity(z),
            score=z,
            value=point.value,
            baseline=mean,
            spread=std,
            labels=dict(point.labels),
            message=f"z={z:.2f} > {self.z_threshold} over window={self.window}",
        )]


class RobustZScoreDetector(Detector):
    """Median / MAD-based z-score. Robust to outliers already in the window.

    modified_z = 0.6745 * (x - median) / MAD
    where MAD = median(|x_i - median|).
    """

    name = "robust_zscore"

    def __init__(self, window: int = 50, z_threshold: float = 3.5,
                 min_mad: float = 1e-9) -> None:
        self.window = max(3, int(window))
        self.z_threshold = float(z_threshold)
        self.min_mad = float(min_mad)

    def update(self, point: MetricPoint, history: deque) -> list:
        if len(history) < 3:
            return []
        sorted_h = sorted(history)
        mid = len(sorted_h) // 2
        if len(sorted_h) % 2 == 0:
            median = 0.5 * (sorted_h[mid - 1] + sorted_h[mid])
        else:
            median = sorted_h[mid]
        deviations = sorted(abs(v - median) for v in history)
        mid_d = len(deviations) // 2
        if len(deviations) % 2 == 0:
            mad = 0.5 * (deviations[mid_d - 1] + deviations[mid_d])
        else:
            mad = deviations[mid_d]
        if mad < self.min_mad:
            # Avoid divide-by-zero on flat signals; the point itself is not
            # informative, skip.
            return []
        z = 0.6745 * (point.value - median) / mad
        if abs(z) < self.z_threshold:
            return []
        return [Anomaly(
            ts=point.ts,
            name=point.name,
            detector=self.name,
            severity=severity_for(z),
            score=z,
            value=point.value,
            baseline=median,
            spread=mad,
            labels=dict(point.labels),
            message=f"robust_z={z:.2f} > {self.z_threshold} over window={self.window}",
        )]


class EWMAZScoreDetector(Detector):
    """Exponentially-weighted moving average / std z-score.

    Uses λ = 1 - exp(-ln2 / halflife) so the half-life is in *samples*, not
    seconds. Tracks mean and variance incrementally.
    """

    name = "ewma_zscore"

    def __init__(self, halflife: float = 20.0, z_threshold: float = 3.0) -> None:
        self.halflife = max(1.0, float(halflife))
        self.z_threshold = float(z_threshold)
        self.alpha = 1.0 - math.exp(-math.log(2.0) / self.halflife)
        # Per-key state, populated lazily in update via history inspection.
        self._cache: dict = {}

    def _state_for(self, key: tuple) -> dict:
        st = self._cache.get(key)
        if st is None:
            st = {"mean": None, "var": None, "n": 0}
            self._cache[key] = st
        return st

    def update(self, point: MetricPoint, history: deque) -> list:
        key = point.key()
        st = self._state_for(key)
        # Bootstrap from history on first sample.
        if st["mean"] is None:
            if len(history) < 2:
                return []
            n0 = len(history)
            mean0 = sum(history) / n0
            var0 = sum((v - mean0) ** 2 for v in history) / max(1, n0 - 1)
            st["mean"] = mean0
            st["var"] = var0
            st["n"] = n0
        # Update with current point.
        m = st["mean"]
        v = st["var"]
        diff = point.value - m
        new_mean = m + self.alpha * diff
        new_var = (1 - self.alpha) * (v + self.alpha * diff * diff)
        st["mean"] = new_mean
        st["var"] = new_var
        st["n"] += 1
        std = math.sqrt(max(new_var, 0.0))
        if std == 0 or not math.isfinite(std):
            return []
        z = (point.value - new_mean) / std
        if abs(z) < self.z_threshold:
            return []
        return [Anomaly(
            ts=point.ts,
            name=point.name,
            detector=self.name,
            severity=severity_for(z),
            score=z,
            value=point.value,
            baseline=new_mean,
            spread=std,
            labels=dict(point.labels),
            message=f"ewma_z={z:.2f} > {self.z_threshold} halflife={self.halflife}",
        )]

    def reset(self) -> None:
        self._cache.clear()


class IQRDetector(Detector):
    """Tukey fences. Anomaly when value < Q1 - k*IQR or value > Q3 + k*IQR.

    Score is reported as distance from the nearer fence, in IQR units, with
    sign indicating which side it fell on. |score| >= 1 is the boundary.
    """

    name = "iqr"

    def __init__(self, window: int = 50, k: float = 1.5,
                 severity_threshold: float = 3.0) -> None:
        self.window = max(4, int(window))
        self.k = float(k)
        self.severity_threshold = float(severity_threshold)

    def update(self, point: MetricPoint, history: deque) -> list:
        if len(history) < 4:
            return []
        s = sorted(history)
        n = len(s)
        # Linear-interpolation quartiles (method=7 in numpy / R type=7).
        def _pct(p: float) -> float:
            idx = p * (n - 1)
            lo = int(math.floor(idx))
            hi = int(math.ceil(idx))
            if lo == hi:
                return s[lo]
            frac = idx - lo
            return s[lo] * (1 - frac) + s[hi] * frac
        q1 = _pct(0.25)
        q3 = _pct(0.75)
        iqr = q3 - q1
        if iqr <= 0:
            return []
        lower = q1 - self.k * iqr
        upper = q3 + self.k * iqr
        if lower <= point.value <= upper:
            return []
        # Normalize the score: distance from nearer fence / IQR.
        if point.value < lower:
            score = (lower - point.value) / iqr
        else:
            score = (point.value - upper) / iqr
        # score is positive magnitude; sign indicates side.
        if score < self.severity_threshold:
            return []
        return [Anomaly(
            ts=point.ts,
            name=point.name,
            detector=self.name,
            severity=severity_for(score),
            score=score,
            value=point.value,
            baseline=0.5 * (q1 + q3),
            spread=iqr,
            labels=dict(point.labels),
            message=f"iqr score={score:.2f} (k={self.k}, window={self.window})",
        )]


class RateOfChangeDetector(Detector):
    """Anomaly on sharp deltas. z = (Δvalue) / σ(Δ) over a rolling window."""

    name = "rate_of_change"

    def __init__(self, window: int = 50, z_threshold: float = 3.0) -> None:
        self.window = max(2, int(window))
        self.z_threshold = float(z_threshold)
        # Per-key state: previous value.
        self._prev: dict = {}

    def update(self, point: MetricPoint, history: deque) -> list:
        key = point.key()
        prev = self._prev.get(key)
        self._prev[key] = point.value
        if prev is None:
            return []
        # Build a delta series from history.
        if len(history) < 2:
            return []
        deltas = [history[i + 1] - history[i] for i in range(len(history) - 1)]
        if len(deltas) < 2:
            return []
        mean = sum(deltas) / len(deltas)
        var = sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)
        std = math.sqrt(var)
        if std == 0 or not math.isfinite(std):
            return []
        delta = point.value - prev
        z = (delta - mean) / std
        if abs(z) < self.z_threshold:
            return []
        return [Anomaly(
            ts=point.ts,
            name=point.name,
            detector=self.name,
            severity=severity_for(z),
            score=z,
            value=point.value,
            baseline=prev,
            spread=std,
            labels=dict(point.labels),
            message=f"delta_z={z:.2f} Δ={delta:.4f} > {self.z_threshold}",
        )]

    def reset(self) -> None:
        self._prev.clear()


class BoundsDetector(Detector):
    """Static band detector. Anomaly when value < low or > high.

    The score reported is the IQR-equivalent: how many "spreads" away from the
    nearer bound. We approximate the spread as 1/6 of (high - low) so a band
    fully outside the bounds immediately yields |score| >= 3.
    """

    name = "bounds"

    def __init__(self, low: float | None = None, high: float | None = None,
                 severity_threshold: float = 3.0) -> None:
        if low is None and high is None:
            raise ValueError("BoundsDetector requires at least one of low/high")
        self.low = low
        self.high = high
        self.severity_threshold = float(severity_threshold)
        # Approximate spread = range / 6 (so |score|>=3 means outside the band).
        if low is not None and high is not None and high > low:
            self._approx_spread = (high - low) / 6.0
        else:
            self._approx_spread = 1.0  # unknown range, score is a delta.

    def update(self, point: MetricPoint, history: deque) -> list:  # noqa: ARG002
        v = point.value
        triggered = False
        score = 0.0
        if self.low is not None and v < self.low:
            triggered = True
            score = max(score, (self.low - v) / self._approx_spread)
        if self.high is not None and v > self.high:
            triggered = True
            score = max(score, (v - self.high) / self._approx_spread)
        if not triggered or score < self.severity_threshold:
            return []
        return [Anomaly(
            ts=point.ts,
            name=point.name,
            detector=self.name,
            severity=severity_for(score),
            score=score,
            value=point.value,
            baseline=(self.low if self.low is not None else float("nan")),
            spread=self._approx_spread,
            labels=dict(point.labels),
            message=(f"value {v} outside [{self.low}, {self.high}], "
                     f"score={score:.2f}"),
        )]


# Registry — name -> class. Built-in detectors are listed first.
DETECTORS: dict[str, type] = {
    "zscore":          ZScoreDetector,
    "robust_zscore":   RobustZScoreDetector,
    "ewma_zscore":     EWMAZScoreDetector,
    "iqr":             IQRDetector,
    "rate_of_change":  RateOfChangeDetector,
    "bounds":          BoundsDetector,
}


def make_detector(name: str, **kwargs) -> Detector:
    """Construct a detector by registry name."""
    if name not in DETECTORS:
        raise KeyError(f"unknown detector {name!r}; available: {sorted(DETECTORS)}")
    return DETECTORS[name](**kwargs)


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """Top-level detector. Holds per-key detector instances and a per-key
    history deque. Calls each detector on every point and aggregates."""

    def __init__(self, detectors: list[Detector] | None = None,
                 cooldown_sec: float = 60.0,
                 min_samples: int = 10,
                 history_size: int | None = None) -> None:
        self.detectors: list[Detector] = list(detectors) if detectors else [
            ZScoreDetector(),
            RobustZScoreDetector(),
            EWMAZScoreDetector(),
            IQRDetector(),
            RateOfChangeDetector(),
        ]
        self.cooldown_sec = float(cooldown_sec)
        self.min_samples = max(1, int(min_samples))
        # Per-key history deque of recent values (oldest first).
        self._history: dict[tuple, deque] = {}
        # Per-key history cap. Detectors know their own preferred window;
        # we keep enough to satisfy the largest.
        if history_size is None:
            sizes = []
            for d in self.detectors:
                if hasattr(d, "window"):
                    sizes.append(int(getattr(d, "window")))
                elif hasattr(d, "halflife"):
                    sizes.append(int(getattr(d, "halflife") * 4))
            history_size = max(sizes) if sizes else 200
        self.history_size = max(10, int(history_size))
        # Per-(key, detector) last-fire timestamp (epoch sec) for cooldown.
        self._last_fire: dict[tuple, float] = {}
        # Stats counters.
        self.report = AnomalyReport()

    # Per-key state --------------------------------------------------
    def _history_for(self, key: tuple) -> deque:
        h = self._history.get(key)
        if h is None:
            h = deque(maxlen=self.history_size)
            self._history[key] = h
        return h

    def update(self, point: MetricPoint) -> list:
        """Process one point; returns the list of Anomalies emitted."""
        self.report.total_points += 1
        self.report.points_seen[point.name] += 1
        key = point.key()
        history = self._history_for(key)

        # Skip non-finite values entirely.
        if not math.isfinite(point.value):
            return []

        emitted: list[Anomaly] = []
        # Snapshot history length before appending.
        pre_len = len(history)
        if pre_len < self.min_samples:
            # Training phase: accumulate history silently. Detectors with
            # their own bootstrap (EWMA) read `history` here without firing.
            history.append(point.value)
            return emitted
        for det in self.detectors:
            try:
                det_anoms = det.update(point, history)
            except Exception:
                # Detector bug must not poison the run.
                det_anoms = []
            for a in det_anoms:
                fire_key = (key, det.name)
                last = self._last_fire.get(fire_key, -math.inf)
                if point.ts - last < self.cooldown_sec:
                    self.report.suppressed[point.name] += 1
                    continue
                self._last_fire[fire_key] = point.ts
                self.report.anomalies.append(a)
                emitted.append(a)
        history.append(point.value)
        return emitted

    def consume(self, points: Iterable[MetricPoint]) -> AnomalyReport:
        """Update on every point and return the accumulated report."""
        for p in points:
            self.update(p)
        return self.report


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------

def _coerce_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        # Don't treat bool as numeric.
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _coerce_ts(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        # Heuristic: epoch millis vs seconds.
        return v / 1000.0 if v > 1e12 else v
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            v = float(s)
            return v / 1000.0 if v > 1e12 else v
        except ValueError:
            pass
        # ISO-8601 fallback (limited — good enough for our sample data).
        m = re.match(
            r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]\d{2}:?\d{2})?$",
            s,
        )
        if m:
            from datetime import datetime, timezone
            base = f"{m.group(1)}T{m.group(2)}"
            tz = m.group(3)
            try:
                if tz and tz != "Z":
                    dt = datetime.fromisoformat(s)
                elif tz == "Z":
                    dt = datetime.fromisoformat(base + "+00:00")
                else:
                    dt = datetime.fromisoformat(base)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                return None
    return None


def parse_metric_point(line: str) -> MetricPoint | None:
    """Parse one JSONL line into a MetricPoint. Returns None on parse failure."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("metric")
    if not isinstance(name, str) or not name:
        return None
    raw_value = obj.get("value", obj.get("v"))
    value = _coerce_value(raw_value)
    if value is None:
        return None
    ts = _coerce_ts(obj.get("ts") or obj.get("timestamp") or obj.get("t"))
    if ts is None:
        ts = time.time()
    labels = obj.get("labels") or obj.get("tags") or {}
    if not isinstance(labels, dict):
        labels = {}
    # Coerce label values to strings to keep the key tuple hashable.
    labels = {str(k): str(v) for k, v in labels.items()}
    return MetricPoint(ts=ts, name=name, value=value, labels=labels)


def iter_metric_files(paths: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield (label, file_path) for every readable JSONL file under paths.

    Mirrors the convention used by `log_aggregator.iter_log_files` so the
    multica monitor family reads consistently.
    """
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = [x for x in p.rglob("*") if x.is_file() and x.suffix == ".jsonl"]
        else:
            # Glob
            parent = p.parent if str(p.parent) else "."
            try:
                files = [Path(x) for x in sorted(Path(parent).glob(p.name))
                         if x.suffix == ".jsonl"]
            except OSError:
                files = []
        for f in files:
            real = str(f.resolve())
            if real in seen:
                continue
            seen.add(real)
            label = f.stem
            yield label, real


def stream_metrics(paths: Iterable[str], recursive: bool = True) -> Iterator[MetricPoint]:
    """Yield MetricPoints read from JSONL files under `paths`."""
    for label, path in iter_metric_files(paths):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    pt = parse_metric_point(line)
                    if pt is not None:
                        yield pt
        except OSError:
            continue


def detect_stream(paths: Iterable[str], detectors: list[Detector] | None = None,
                  cooldown_sec: float = 60.0, min_samples: int = 10,
                  recursive: bool = True) -> AnomalyReport:
    """Convenience: build an AnomalyDetector, run it over a JSONL stream."""
    runner = AnomalyDetector(
        detectors=detectors,
        cooldown_sec=cooldown_sec,
        min_samples=min_samples,
    )
    return runner.consume(stream_metrics(paths, recursive=recursive))


# ---------------------------------------------------------------------------
# Smoke helpers (used by run.py and run_metrics.py)
# ---------------------------------------------------------------------------

def default_detector_bank() -> list[Detector]:
    """The default detector bank — one of each family except bounds."""
    return [
        ZScoreDetector(window=50, z_threshold=3.0),
        RobustZScoreDetector(window=50, z_threshold=3.5),
        EWMAZScoreDetector(halflife=20, z_threshold=3.0),
        IQRDetector(window=50, k=1.5),
        RateOfChangeDetector(window=50, z_threshold=3.0),
    ]