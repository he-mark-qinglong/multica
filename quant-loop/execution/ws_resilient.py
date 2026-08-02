"""ws_resilient — generic resilient WebSocket connection wrapper (E17).

Venue-agnostic WebSocket resilience layer, extracted from the
reconnect logic that the perp adapter
(:mod:`execution.venue_adapter_binance_perp_p7exec_003`) embeds in
its WSS consumer, and generalised for reuse by any venue stream.

The wrapper owns:

* **connection state machine** — ``CONNECTING`` / ``OPEN`` /
  ``RECONNECTING`` / ``DEAD`` (:class:`WsState`).  All transitions
  are driven explicitly (``connect`` / ``on_disconnect`` /
  ``tick``) so the wrapper is fully testable offline against a
  fake in-memory connection; no threads, no real sockets.
* **exponential backoff with jitter** — reconnect delay is
  ``min(max_backoff_s, base_backoff_s * 2**(attempt-1))`` scaled
  by ``1 + jitter_fraction * (2*u - 1)`` where ``u`` comes from an
  injectable ``rng`` (defaults to :func:`random.random`; tests
  pin it for determinism).
* **heartbeat ping/pong timeout** — when ``OPEN``, ``tick`` sends
  a ping after ``ping_interval_s`` of silence and declares the
  connection lost when no pong (or any inbound frame) arrives
  within ``pong_timeout_s`` of the last ping.
* **sequence-gap detection + replay request** — an injectable
  ``sequence_extractor`` pulls a monotonically increasing sequence
  number out of each inbound frame; a jump of more than 1 emits a
  ``GAP_DETECTED`` event plus a ``REPLAY_REQUESTED`` event whose
  payload is the (from_seq, to_seq) range the caller should
  re-request (venue-specific replay mechanics stay with the
  caller).
* **disconnect buffering with cap** — outbound messages sent
  while not ``OPEN`` are buffered up to
  ``buffer_max_messages``; beyond the cap the *oldest* buffered
  message is dropped (drop-oldest keeps the most recent state,
  the right policy for market-data resends) and counted in
  ``n_buffer_dropped``.  The buffer flushes in FIFO order on
  reconnect.

Design constraints
------------------
* No I/O of its own: the underlying connection is an injected
  factory returning a transport object with ``send(str)`` and
  ``close()`` (a real ``websocket-client`` app in production, a
  fake in tests).
* Time is explicit: every method takes ``now_s`` (monotonic
  seconds) so tests script the clock; no wall-clock reads inside.
* All outward effects are returned as :class:`WsEvent` frozen
  dataclasses — the caller decides what to journal.

References
----------
- Binance USD-M user-data stream reconnect semantics
  (``listenKeyExpired``) — see the perp sibling's WSS consumer.
- RFC 6455 (ping/pong control frames).
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Deque, List, Optional, Tuple


# ---------------------------------------------------------------------------
# State machine + events
# ---------------------------------------------------------------------------


class WsState(str, Enum):
    """Connection lifecycle state."""

    CONNECTING = "CONNECTING"
    OPEN = "OPEN"
    RECONNECTING = "RECONNECTING"
    DEAD = "DEAD"


# Event kinds emitted by the wrapper.
E_CONNECTED = "CONNECTED"
E_DISCONNECTED = "DISCONNECTED"
E_RECONNECT_SCHEDULED = "RECONNECT_SCHEDULED"
E_RECONNECTING = "RECONNECTING"
E_DEAD = "DEAD"
E_MESSAGE = "MESSAGE"
E_GAP_DETECTED = "GAP_DETECTED"
E_REPLAY_REQUESTED = "REPLAY_REQUESTED"
E_PING_SENT = "PING_SENT"
E_HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
E_BUFFER_DROPPED = "BUFFER_DROPPED"
E_BUFFER_FLUSHED = "BUFFER_FLUSHED"


@dataclass(frozen=True)
class WsEvent:
    """One outward effect of the wrapper.  ``payload`` is a small
    dict whose keys depend on ``kind`` (documented per method)."""

    kind: str
    ts_s: float
    payload: Tuple[Tuple[str, Any], ...]

    @staticmethod
    def make(kind: str, ts_s: float, **payload: Any) -> "WsEvent":
        return WsEvent(
            kind=kind,
            ts_s=float(ts_s),
            payload=tuple(sorted(payload.items())),
        )

    def get(self, key: str, default: Any = None) -> Any:
        return dict(self.payload).get(key, default)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResilientWsPolicy:
    """Declarative configuration for :class:`ResilientWs`.

    ``base_backoff_s``       first reconnect delay (s).
    ``max_backoff_s``        reconnect-delay ceiling (s).
    ``jitter_fraction``      symmetric jitter applied to each delay,
                             e.g. 0.2 → delay * (1 ± 0.2*u).
    ``max_reconnect_attempts`` cap on consecutive failed reconnects
                             before ``DEAD``; 0 = unlimited.
    ``ping_interval_s``      silence after which a ping is sent.
    ``pong_timeout_s``       time after a ping with no inbound frame
                             before the connection is declared lost.
    ``buffer_max_messages``  outbound buffer cap while disconnected;
                             0 disables buffering (sends fail fast).
    """

    base_backoff_s: float = 1.0
    max_backoff_s: float = 60.0
    jitter_fraction: float = 0.2
    max_reconnect_attempts: int = 10
    ping_interval_s: float = 30.0
    pong_timeout_s: float = 10.0
    buffer_max_messages: int = 1000

    def __post_init__(self) -> None:
        if self.base_backoff_s <= 0:
            raise ValueError("base_backoff_s must be positive")
        if self.max_backoff_s < self.base_backoff_s:
            raise ValueError("max_backoff_s must be >= base_backoff_s")
        if not (0.0 <= self.jitter_fraction < 1.0):
            raise ValueError("jitter_fraction must be in [0, 1)")
        if self.max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts must be >= 0")
        if self.ping_interval_s <= 0 or self.pong_timeout_s <= 0:
            raise ValueError("heartbeat intervals must be positive")
        if self.buffer_max_messages < 0:
            raise ValueError("buffer_max_messages must be >= 0")


DEFAULT_RESILIENT_WS_POLICY = ResilientWsPolicy()

#: Connection factory type: called with no args on every (re)connect
#: attempt; must return an object with ``send(str)`` and
#: ``close()``.  May raise — the wrapper treats a raise as a failed
#: attempt and schedules the next backoff.
ConnectionFactory = Callable[[], Any]

#: Sequence extractor: pulls the venue sequence number out of one
#: inbound frame (already JSON-decoded by the caller or raw — the
#: extractor's contract).  Return ``None`` for frames without a
#: sequence (heartbeats, subscription acks).
SequenceExtractor = Callable[[Any], Optional[int]]


def backoff_delay_s(
    attempt: int,
    policy: ResilientWsPolicy = DEFAULT_RESILIENT_WS_POLICY,
    *,
    u: float = 0.5,
) -> float:
    """Pure backoff calculator: exponential, capped, jittered.

    ``attempt`` is 1-based.  ``u`` is one rng draw in [0, 1);
    ``u=0.5`` yields the unjittered delay.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt!r}")
    base = min(
        policy.max_backoff_s,
        policy.base_backoff_s * (2.0 ** (attempt - 1)),
    )
    jitter = 1.0 + policy.jitter_fraction * (2.0 * float(u) - 1.0)
    return base * jitter


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------


class ResilientWs:
    """Generic resilient WebSocket wrapper.

    Drive it explicitly:

    * :meth:`connect` — open the initial connection (or raise into
      ``RECONNECTING`` when the factory fails);
    * :meth:`on_message` — feed one inbound frame;
    * :meth:`on_disconnect` — report a transport-level drop;
    * :meth:`send` — outbound send (buffers when not OPEN);
    * :meth:`tick` — periodic heartbeat + reconnect driver.

    Every call returns a list of :class:`WsEvent` describing what
    happened; the wrapper itself performs no logging or journaling.
    """

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory,
        policy: ResilientWsPolicy = DEFAULT_RESILIENT_WS_POLICY,
        sequence_extractor: Optional[SequenceExtractor] = None,
        rng: Callable[[], float] = random.random,
        name: str = "ws",
    ) -> None:
        self._factory = connection_factory
        self.policy = policy
        self._extract_seq = sequence_extractor
        self._rng = rng
        self.name = name

        self._state = WsState.CONNECTING
        self._conn: Any = None
        self._attempt = 0
        self._next_attempt_at_s: Optional[float] = None
        self._last_inbound_s: Optional[float] = None
        self._last_ping_s: Optional[float] = None
        self._last_seq: Optional[int] = None
        self._buffer: Deque[str] = deque()
        # Counters (observability; snapshot() exposes them).
        self.n_connects = 0
        self.n_disconnects = 0
        self.n_reconnect_attempts = 0
        self.n_gaps = 0
        self.n_replay_requests = 0
        self.n_pings = 0
        self.n_heartbeat_timeouts = 0
        self.n_buffer_dropped = 0
        self.n_buffer_flushed = 0
        self.n_messages = 0

    # -- reads -------------------------------------------------------------

    @property
    def state(self) -> WsState:
        return self._state

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    @property
    def last_seq(self) -> Optional[int]:
        return self._last_seq

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "attempt": self._attempt,
            "last_seq": self._last_seq,
            "buffered": len(self._buffer),
            "n_connects": self.n_connects,
            "n_disconnects": self.n_disconnects,
            "n_reconnect_attempts": self.n_reconnect_attempts,
            "n_gaps": self.n_gaps,
            "n_replay_requests": self.n_replay_requests,
            "n_pings": self.n_pings,
            "n_heartbeat_timeouts": self.n_heartbeat_timeouts,
            "n_buffer_dropped": self.n_buffer_dropped,
            "n_buffer_flushed": self.n_buffer_flushed,
            "n_messages": self.n_messages,
        }

    # -- connection lifecycle ----------------------------------------------

    def connect(self, *, now_s: float) -> List[WsEvent]:
        """Open the connection via the factory.  On success the
        state becomes OPEN, the backoff counter resets, and any
        buffered outbound messages flush FIFO.  On factory failure
        the wrapper enters RECONNECTING with a scheduled backoff
        (or DEAD when the attempt cap is hit)."""
        events: List[WsEvent] = []
        self._state = WsState.CONNECTING
        try:
            conn = self._factory()
        except Exception as exc:  # factory refused — treat as drop
            return events + self._schedule_reconnect(
                now_s=now_s, reason=f"connect_failed:{exc!r}",
            )
        self._conn = conn
        self._attempt = 0
        self._next_attempt_at_s = None
        self._last_inbound_s = float(now_s)
        self._last_ping_s = None
        self._state = WsState.OPEN
        self.n_connects += 1
        events.append(WsEvent.make(E_CONNECTED, now_s, name=self.name))
        events.extend(self._flush_buffer(now_s=now_s))
        return events

    def on_disconnect(
        self,
        *,
        now_s: float,
        reason: str = "transport_lost",
    ) -> List[WsEvent]:
        """Report a transport-level disconnect.  Always safe to
        call (idempotent when already RECONNECTING / DEAD)."""
        self.n_disconnects += 1
        self._close_conn_quietly()
        if self._state == WsState.DEAD:
            return []
        events = [WsEvent.make(
            E_DISCONNECTED, now_s, name=self.name, reason=reason,
        )]
        events.extend(self._schedule_reconnect(now_s=now_s,
                                               reason=reason))
        return events

    def _schedule_reconnect(
        self,
        *,
        now_s: float,
        reason: str,
    ) -> List[WsEvent]:
        self._attempt += 1
        cap = self.policy.max_reconnect_attempts
        if cap > 0 and self._attempt > cap:
            self._state = WsState.DEAD
            self._next_attempt_at_s = None
            return [WsEvent.make(
                E_DEAD, now_s, name=self.name, attempt=self._attempt,
                reason=reason,
            )]
        delay = backoff_delay_s(
            self._attempt, self.policy, u=self._rng(),
        )
        self._state = WsState.RECONNECTING
        self._next_attempt_at_s = float(now_s) + delay
        return [WsEvent.make(
            E_RECONNECT_SCHEDULED, now_s, name=self.name,
            attempt=self._attempt, delay_s=delay, reason=reason,
        )]

    # -- heartbeat + reconnect driver ---------------------------------------

    def tick(self, *, now_s: float) -> List[WsEvent]:
        """Periodic driver.  Call at a regular cadence.

        * RECONNECTING: attempt the reconnect once
          ``next_attempt_at`` is reached.
        * OPEN: send a ping after ``ping_interval_s`` of inbound
          silence; declare the connection lost when no inbound
          frame arrives within ``pong_timeout_s`` of the ping.
        """
        events: List[WsEvent] = []
        now_s = float(now_s)
        if self._state == WsState.RECONNECTING:
            if (self._next_attempt_at_s is not None
                    and now_s >= self._next_attempt_at_s):
                self.n_reconnect_attempts += 1
                events.append(WsEvent.make(
                    E_RECONNECTING, now_s, name=self.name,
                    attempt=self._attempt,
                ))
                events.extend(self.connect(now_s=now_s))
            return events

        if self._state != WsState.OPEN:
            return events

        last_in = (
            self._last_inbound_s if self._last_inbound_s is not None
            else now_s
        )
        if self._last_ping_s is not None:
            # Awaiting a pong: any inbound frame clears _last_ping_s
            # (see on_message); otherwise time out.
            if now_s - self._last_ping_s >= self.policy.pong_timeout_s:
                self.n_heartbeat_timeouts += 1
                events.append(WsEvent.make(
                    E_HEARTBEAT_TIMEOUT, now_s, name=self.name,
                    since_ping_s=now_s - self._last_ping_s,
                ))
                events.extend(self.on_disconnect(
                    now_s=now_s, reason="heartbeat_timeout",
                ))
            return events
        if now_s - last_in >= self.policy.ping_interval_s:
            self._send_raw("ping")
            self._last_ping_s = now_s
            self.n_pings += 1
            events.append(WsEvent.make(E_PING_SENT, now_s,
                                       name=self.name))
        return events

    # -- inbound -------------------------------------------------------------

    def on_message(
        self,
        frame: Any,
        *,
        now_s: float,
    ) -> List[WsEvent]:
        """Feed one inbound frame.  Any inbound traffic counts as a
        pong (clears the heartbeat timer) and resets the silence
        clock.  When a ``sequence_extractor`` is configured, a
        sequence jump > 1 emits GAP_DETECTED + REPLAY_REQUESTED
        (payload ``from_seq`` / ``to_seq`` — the missed range)."""
        events: List[WsEvent] = []
        now_s = float(now_s)
        self._last_inbound_s = now_s
        self._last_ping_s = None  # any inbound frame is a liveness proof
        self.n_messages += 1

        seq = self._extract_seq(frame) if self._extract_seq else None
        if seq is not None:
            if self._last_seq is not None and seq > self._last_seq + 1:
                self.n_gaps += 1
                events.append(WsEvent.make(
                    E_GAP_DETECTED, now_s, name=self.name,
                    last_seq=self._last_seq, got_seq=seq,
                ))
                self.n_replay_requests += 1
                events.append(WsEvent.make(
                    E_REPLAY_REQUESTED, now_s, name=self.name,
                    from_seq=self._last_seq + 1, to_seq=seq - 1,
                ))
            if self._last_seq is None or seq > self._last_seq:
                self._last_seq = seq
        events.append(WsEvent.make(E_MESSAGE, now_s, name=self.name,
                                   seq=seq))
        return events

    # -- outbound ------------------------------------------------------------

    def send(self, message: str, *, now_s: float) -> List[WsEvent]:
        """Send one outbound message.  When OPEN, writes straight to
        the transport.  Otherwise buffers up to
        ``buffer_max_messages``; beyond the cap the *oldest*
        buffered message is dropped (drop-oldest) and counted.
        With buffering disabled (cap 0) the message is dropped and
        counted.  Returns the events emitted."""
        events: List[WsEvent] = []
        if self._state == WsState.OPEN and self._conn is not None:
            self._send_raw(message)
            return events
        if self.policy.buffer_max_messages == 0:
            self.n_buffer_dropped += 1
            events.append(WsEvent.make(
                E_BUFFER_DROPPED, now_s, name=self.name,
                reason="buffering_disabled",
            ))
            return events
        while len(self._buffer) >= self.policy.buffer_max_messages:
            self._buffer.popleft()
            self.n_buffer_dropped += 1
            events.append(WsEvent.make(
                E_BUFFER_DROPPED, now_s, name=self.name,
                reason="buffer_full_drop_oldest",
            ))
        self._buffer.append(str(message))
        return events

    # -- internals -----------------------------------------------------------

    def _send_raw(self, message: str) -> None:
        if self._conn is not None:
            self._conn.send(message)

    def _flush_buffer(self, *, now_s: float) -> List[WsEvent]:
        events: List[WsEvent] = []
        n = len(self._buffer)
        while self._buffer:
            self._send_raw(self._buffer.popleft())
        if n:
            self.n_buffer_flushed += n
            events.append(WsEvent.make(
                E_BUFFER_FLUSHED, now_s, name=self.name, n=n,
            ))
        return events

    def _close_conn_quietly(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover — defensive
                pass


__all__ = [
    "ConnectionFactory",
    "DEFAULT_RESILIENT_WS_POLICY",
    "E_BUFFER_DROPPED",
    "E_BUFFER_FLUSHED",
    "E_CONNECTED",
    "E_DEAD",
    "E_DISCONNECTED",
    "E_GAP_DETECTED",
    "E_HEARTBEAT_TIMEOUT",
    "E_MESSAGE",
    "E_PING_SENT",
    "E_RECONNECTING",
    "E_RECONNECT_SCHEDULED",
    "E_REPLAY_REQUESTED",
    "ResilientWs",
    "ResilientWsPolicy",
    "SequenceExtractor",
    "WsEvent",
    "WsState",
    "backoff_delay_s",
]
