"""Inter-strategy signal bus — in-memory pub/sub with TTL and versioning.

Strategies running in the same loop often produce information other
strategies can consume (a regime detector's state, a market maker's
toxicity estimate, a lead strategy's position intent). The signal bus is
the single channel for that: publishers post *named* signals keyed by
``(symbol, signal_type)``; subscribers pull the latest still-valid value
for a key. Pull-based (not callback) delivery keeps strategies pure and
deterministic — a subscriber's output depends only on bus state at its
own decision time, never on delivery ordering side effects.

Semantics:
  - **TTL** — every signal carries a time-to-live; once
    ``now > ts + ttl`` the signal is invisible to subscribers (it stays
    in history). ``ttl=None`` means never expires.
  - **Version stamp** — the bus assigns a strictly monotonically
    increasing ``version`` on publish, so subscribers can detect "new
    value since I last looked" without comparing payloads.
  - **History** — the last ``history_size`` signals per key are kept;
    ``history(key, n)`` returns the newest ``n`` (newest first).
  - **Optional jsonl spill** — with ``spill_path`` set, every publish is
    appended as one JSON line, so a separate process can rebuild bus
    state via ``load_spill`` (cross-process read-only sharing).

``Signal`` is a frozen dataclass — bus state changes only by appending
new immutable facts, never by mutating an existing one.

References:
- CEP literature on TTL/windowed streams: Cugola & Margara (2012)
  "Processing flows of information", ACM Computing Surveys.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

Key = Tuple[str, str]  # (symbol, signal_type)


@dataclass(frozen=True)
class Signal:
    """One published fact on the bus. Immutable by construction.

    Attributes:
        symbol: instrument the signal refers to ("" for market-wide).
        signal_type: namespaced signal name, e.g. 'regime', 'toxicity'.
        value: JSON-serialisable payload (float, str, dict, ...).
        ts: event time in epoch seconds (publisher's clock).
        ttl: seconds the signal stays visible; None = never expires.
        version: bus-assigned monotonic stamp (0 before publish).
        publisher: originating strategy/module name (traceability).
    """
    symbol: str
    signal_type: str
    value: Any
    ts: float
    ttl: Optional[float] = None
    version: int = 0
    publisher: str = ""

    @property
    def key(self) -> Key:
        return (self.symbol, self.signal_type)

    def is_valid(self, now: float) -> bool:
        """True iff the signal has not expired at ``now`` (inclusive end)."""
        return self.ttl is None or now <= self.ts + self.ttl


@dataclass(frozen=True)
class BusConfig:
    """Signal bus parameters.

    Attributes:
        history_size: signals retained per (symbol, signal_type) key.
        spill_path: optional jsonl file; every publish is appended.
    """
    history_size: int = 100
    spill_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.history_size < 1:
            raise ValueError("history_size must be >= 1")


class SignalBus:
    """In-memory pub/sub bus. Thread-unsafe by design — one loop owns it.

    The bus holds the only mutable state in this module; everything it
    stores and returns is immutable ``Signal`` instances.
    """

    def __init__(self, config: Optional[BusConfig] = None) -> None:
        self._config = config or BusConfig()
        self._store: Dict[Key, List[Signal]] = {}  # key -> oldest..newest
        self._version = 0

    # -- publisher side ------------------------------------------------------

    def publish(
        self,
        symbol: str,
        signal_type: str,
        value: Any,
        ts: float,
        ttl: Optional[float] = None,
        publisher: str = "",
    ) -> Signal:
        """Post a signal; returns the stamped (immutable) Signal.

        The assigned ``version`` is strictly increasing across the whole
        bus, so a subscriber can remember one watermark per key.
        """
        self._version += 1
        sig = Signal(
            symbol=symbol,
            signal_type=signal_type,
            value=value,
            ts=float(ts),
            ttl=ttl,
            version=self._version,
            publisher=publisher,
        )
        hist = self._store.setdefault(sig.key, [])
        hist.append(sig)
        if len(hist) > self._config.history_size:
            del hist[: len(hist) - self._config.history_size]
        if self._config.spill_path is not None:
            with open(self._config.spill_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(sig)) + "\n")
        return sig

    # -- subscriber side (pull) ----------------------------------------------

    def get(self, symbol: str, signal_type: str, now: float) -> Optional[Signal]:
        """Latest non-expired signal for the key, or None."""
        hist = self._store.get((symbol, signal_type))
        if not hist:
            return None
        for sig in reversed(hist):
            if sig.is_valid(now):
                return sig
        return None

    def get_value(self, symbol: str, signal_type: str, now: float,
                  default: Any = None) -> Any:
        """Convenience: payload of ``get`` or ``default`` when absent/expired."""
        sig = self.get(symbol, signal_type, now)
        return sig.value if sig is not None else default

    def get_since(self, symbol: str, signal_type: str, now: float,
                  min_version: int) -> Optional[Signal]:
        """Latest valid signal strictly newer than ``min_version``, else None.

        Lets a subscriber ask "anything new since my watermark?" without
        comparing payloads.
        """
        sig = self.get(symbol, signal_type, now)
        if sig is not None and sig.version > min_version:
            return sig
        return None

    def history(self, symbol: str, signal_type: str, n: int,
                now: Optional[float] = None) -> List[Signal]:
        """Newest ``n`` retained signals for the key (newest first).

        With ``now`` given, expired signals are filtered out; without it
        the raw retained window is returned (expired included) — useful
        for audit/replay.
        """
        hist = self._store.get((symbol, signal_type), [])
        out = [s for s in reversed(hist) if now is None or s.is_valid(now)]
        return out[:n]

    def keys(self) -> List[Key]:
        """All keys that currently have any retained signal."""
        return sorted(self._store)

    @property
    def current_version(self) -> int:
        """The bus-wide version watermark (0 when nothing published)."""
        return self._version


# ---------------------------------------------------------------------------
# Cross-process spill (pure functions over a jsonl file)
# ---------------------------------------------------------------------------

def load_spill(path: str, now: Optional[float] = None) -> SignalBus:
    """Rebuild a bus from a spill file written by another process.

    Args:
        path: jsonl file previously used as ``BusConfig.spill_path``.
        now: if given, expired signals are dropped during load (a reader
            that only cares about current state); if None, everything is
            retained (audit/replay).

    Returns:
        A new SignalBus whose versions match the original publish order.
    """
    bus = SignalBus()
    if not os.path.exists(path):
        return bus
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sig = Signal(**rec)
            if now is not None and not sig.is_valid(now):
                continue
            bus._version = max(bus._version, sig.version)
            hist = bus._store.setdefault(sig.key, [])
            hist.append(sig)
            if len(hist) > bus._config.history_size:
                del hist[: len(hist) - bus._config.history_size]
    return bus
