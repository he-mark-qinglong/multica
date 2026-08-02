"""Backtest latency model (B7).

A bar-only backtest implicitly assumes zero latency: the strategy sees
the bar the instant it closes and its order executes at that same
instant.  Live trading has two distinct delay legs:

* **feed latency** — the market-data feed delivers a bar/tick
  ``feed_latency`` after it actually happened, so a decision stamped
  ``t`` was made on information that is ``feed_latency`` stale;
* **order latency** — an order submitted at ``t`` only becomes
  executable at the venue at ``t + order_latency`` (and a cancel only
  takes effect at ``t + cancel_latency``).

This module provides three samplers for those legs — fixed, normal
(clipped at zero), and empirical replay of measured latencies — plus a
stateful :class:`LatencySimulator` a backtest loop can drive:

    sim = LatencySimulator(NormalLatency(feed_mean_ns=2e6, ...), seed=7)
    live_ts = sim.submit("ord-1", ts_ns=bar.ts_ns)   # order goes live
    ...
    if sim.fillable("ord-1", bar.ts_ns): ...          # may it fill now?
    result = sim.cancel("ord-1", ts_ns=now)           # in-flight cancel

Cancel interception: a cancel sent at ``t`` wins against any fill that
would happen at ``>= t + cancel_latency``; a fill before that timestamp
beats the cancel (:attr:`CancelResult.ALREADY_FILLED`).  A cancel
against an unknown or already-cancelled order is a no-op
(:attr:`CancelResult.NOT_FOUND` / ``ALREADY_CANCELLED``).

References
----------
- Cartea, Jaimungal & Penalva (2015), "Algorithmic and High-Frequency
  Trading", Ch. 2 — latency as a first-class execution risk.
- Hasbrouck & Saar (2013), "Low-Latency Trading" — the
  feed-vs-order latency decomposition.
- Moallemi & Sağlam (2013), "The Cost of Latency in High-Frequency
  Trading" — stale-feed decision cost.

Pure functions + frozen dataclasses for the samplers; the simulator is
the only stateful object and keeps no I/O.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "CancelResult",
    "CancelStatus",
    "EmpiricalLatency",
    "FixedLatency",
    "LatencySample",
    "LatencySimulator",
    "NormalLatency",
    "PendingOrder",
    "sample_latency",
]


@dataclass(frozen=True)
class LatencySample:
    """One latency observation, all in nanoseconds."""

    feed_ns: int
    order_ns: int
    cancel_ns: int = 0

    def __post_init__(self) -> None:
        for name in ("feed_ns", "order_ns", "cancel_ns"):
            if getattr(self, name) < 0:
                raise ValueError(f"LatencySample: {name} must be >= 0")


@dataclass(frozen=True)
class FixedLatency:
    """Deterministic latency: every sample is identical."""

    feed_ns: int = 0
    order_ns: int = 0
    cancel_ns: int = 0

    def __post_init__(self) -> None:
        for name in ("feed_ns", "order_ns", "cancel_ns"):
            if getattr(self, name) < 0:
                raise ValueError(f"FixedLatency: {name} must be >= 0")

    def sample(self, rng: random.Random) -> LatencySample:
        return LatencySample(
            feed_ns=self.feed_ns,
            order_ns=self.order_ns,
            cancel_ns=self.cancel_ns,
        )


@dataclass(frozen=True)
class NormalLatency:
    """Gaussian latency per leg, clipped at zero (latencies are
    non-negative; a heavy left tail is truncated rather than allowed
    to make an order executable in the past)."""

    feed_mean_ns: float = 0.0
    feed_std_ns: float = 0.0
    order_mean_ns: float = 0.0
    order_std_ns: float = 0.0
    cancel_mean_ns: float = 0.0
    cancel_std_ns: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "feed_mean_ns", "feed_std_ns",
            "order_mean_ns", "order_std_ns",
            "cancel_mean_ns", "cancel_std_ns",
        ):
            if getattr(self, name) < 0.0:
                raise ValueError(f"NormalLatency: {name} must be >= 0")

    def sample(self, rng: random.Random) -> LatencySample:
        def leg(mean: float, std: float) -> int:
            return max(0, int(round(rng.gauss(mean, std))))

        return LatencySample(
            feed_ns=leg(self.feed_mean_ns, self.feed_std_ns),
            order_ns=leg(self.order_mean_ns, self.order_std_ns),
            cancel_ns=leg(self.cancel_mean_ns, self.cancel_std_ns),
        )


@dataclass(frozen=True)
class EmpiricalLatency:
    """Replay measured latencies.

    ``mode="cycle"``  — deterministic round-robin over ``samples``;
    each :meth:`sample` call advances the cursor (the cursor index is
    derived from the rng's state is NOT used; see
    :class:`LatencySimulator`, which tracks the cursor).

    ``mode="sample"`` — uniform random draw per call via ``rng``.
    """

    samples: Tuple[LatencySample, ...] = ()
    mode: str = "cycle"

    def __post_init__(self) -> None:
        if not self.samples:
            raise ValueError("EmpiricalLatency: samples must be non-empty")
        if self.mode not in ("cycle", "sample"):
            raise ValueError(
                f"EmpiricalLatency: mode must be 'cycle' or 'sample', "
                f"got {self.mode!r}"
            )
        object.__setattr__(self, "samples", tuple(self.samples))

    def sample(self, rng: random.Random, cursor: int = 0) -> Tuple[LatencySample, int]:
        """Return ``(sample, next_cursor)``.

        Pure: the caller owns the cursor, so the frozen dataclass stays
        immutable.  In ``sample`` mode the cursor is ignored.
        """
        if self.mode == "sample":
            return rng.choice(self.samples), cursor
        idx = cursor % len(self.samples)
        return self.samples[idx], idx + 1


def sample_latency(
    model: object,
    rng: random.Random,
    cursor: int = 0,
) -> Tuple[LatencySample, int]:
    """Uniform entry point over the three model types."""
    if isinstance(model, (FixedLatency, NormalLatency)):
        return model.sample(rng), cursor
    if isinstance(model, EmpiricalLatency):
        return model.sample(rng, cursor)
    raise TypeError(f"unsupported latency model: {type(model)!r}")


class CancelStatus(str, Enum):
    CANCELLED = "CANCELLED"              # cancel accepted, effective at ts + cancel_latency
    ALREADY_FILLED = "ALREADY_FILLED"    # the fill beat the cancel
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class CancelResult:
    order_id: str
    status: CancelStatus
    effective_ts_ns: Optional[int]   # when the cancel takes effect (if accepted)


@dataclass(frozen=True)
class PendingOrder:
    """Internal per-order latency state."""

    order_id: str
    submit_ts_ns: int
    live_ts_ns: int          # first timestamp at which the order may fill
    decision_ts_ns: int      # submit_ts_ns - feed_latency (info as-of)
    cancel_effective_ts_ns: Optional[int] = None
    filled: bool = False
    cancelled: bool = False


class LatencySimulator:
    """Stateful latency tracker for a backtest loop.

    Thread-unsafe by design (backtests are single-threaded).  Seeded
    ``random.Random`` keeps runs reproducible.
    """

    def __init__(
        self,
        model: object = FixedLatency(),
        *,
        seed: Optional[int] = None,
    ) -> None:
        self._model = model
        self._rng = random.Random(seed)
        self._cursor = 0
        self._orders: Dict[str, PendingOrder] = {}
        self._last_sample: Optional[LatencySample] = None

    # -- introspection -----------------------------------------------------

    @property
    def last_sample(self) -> Optional[LatencySample]:
        """The latencies drawn for the most recent submit/cancel."""
        return self._last_sample

    def pending(self, order_id: str) -> Optional[PendingOrder]:
        return self._orders.get(order_id)

    # -- lifecycle ----------------------------------------------------------

    def submit(self, order_id: str, ts_ns: int) -> int:
        """Register an order submitted at ``ts_ns``.

        Returns the timestamp at which the order becomes executable at
        the venue (``ts_ns + order_latency``).  The decision that
        produced the order is recorded as of
        ``ts_ns - feed_latency`` (stale-feed accounting).
        """
        sample, self._cursor = sample_latency(self._model, self._rng, self._cursor)
        self._last_sample = sample
        self._orders[order_id] = PendingOrder(
            order_id=order_id,
            submit_ts_ns=int(ts_ns),
            live_ts_ns=int(ts_ns) + sample.order_ns,
            decision_ts_ns=int(ts_ns) - sample.feed_ns,
        )
        return int(ts_ns) + sample.order_ns

    def fillable(self, order_id: str, ts_ns: int) -> bool:
        """May this order execute at ``ts_ns``?

        False when the order is unknown, already filled, already
        cancelled, not yet live at the venue, or under an in-flight
        cancel whose effective time has passed.
        """
        po = self._orders.get(order_id)
        if po is None or po.filled:
            return False
        if int(ts_ns) < po.live_ts_ns:
            return False
        if po.cancel_effective_ts_ns is not None:
            # Cancel requested: it intercepts any fill at or after its
            # effective time; before that the fill still wins the race.
            if int(ts_ns) >= po.cancel_effective_ts_ns:
                return False
        elif po.cancelled:
            return False
        return True

    def mark_filled(self, order_id: str, ts_ns: int) -> None:
        """Record that the order filled at ``ts_ns``.

        Raises if the order was not fillable at that timestamp — a
        backtest that fills an order before it was live has a bug, and
        silently accepting it would corrupt every downstream metric.
        """
        if not self.fillable(order_id, ts_ns):
            raise ValueError(
                f"mark_filled: order {order_id!r} is not fillable at "
                f"{ts_ns} (check submit/cancel/live timestamps)"
            )
        po = self._orders[order_id]
        self._orders[order_id] = PendingOrder(
            order_id=po.order_id,
            submit_ts_ns=po.submit_ts_ns,
            live_ts_ns=po.live_ts_ns,
            decision_ts_ns=po.decision_ts_ns,
            cancel_effective_ts_ns=po.cancel_effective_ts_ns,
            filled=True,
            cancelled=False,
        )

    def cancel(self, order_id: str, ts_ns: int) -> CancelResult:
        """Attempt to cancel an in-flight order.

        The cancel is drawn a fresh latency sample (the cancel leg).
        It is *accepted* when the order has not filled yet; it takes
        effect at ``ts_ns + cancel_latency`` — between ``ts_ns`` and
        that effective time the order may still fill (the race is
        resolved by :meth:`fillable` / :meth:`mark_filled` honouring
        the effective timestamp).
        """
        sample, self._cursor = sample_latency(self._model, self._rng, self._cursor)
        self._last_sample = sample
        effective = int(ts_ns) + sample.cancel_ns
        po = self._orders.get(order_id)
        if po is None:
            return CancelResult(order_id, CancelStatus.NOT_FOUND, None)
        if po.filled:
            return CancelResult(order_id, CancelStatus.ALREADY_FILLED, None)
        if po.cancelled:
            return CancelResult(order_id, CancelStatus.ALREADY_CANCELLED, None)
        self._orders[order_id] = PendingOrder(
            order_id=po.order_id,
            submit_ts_ns=po.submit_ts_ns,
            live_ts_ns=po.live_ts_ns,
            decision_ts_ns=po.decision_ts_ns,
            cancel_effective_ts_ns=effective,
            filled=False,
            cancelled=True,
        )
        return CancelResult(order_id, CancelStatus.CANCELLED, effective)

    def newly_live(self, from_ts_ns: int, to_ts_ns: int) -> List[str]:
        """Order ids whose ``live_ts_ns`` falls inside
        ``[from_ts_ns, to_ts_ns)`` — the orders a bar covering that
        interval is the first to be able to fill."""
        return sorted(
            po.order_id
            for po in self._orders.values()
            if from_ts_ns <= po.live_ts_ns < to_ts_ns
        )
