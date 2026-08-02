"""Tests for execution/ws_resilient (E17) — fully offline.

Drives the wrapper against a fake in-memory WS transport (no
sockets): connect → open, disconnect → backoff-scheduled reconnect
→ reopen, sequence-gap detection with replay request, heartbeat
ping/pong timeout, and disconnect-buffering with the drop-oldest
cap.  The rng is pinned for deterministic jitter.

Run:
    python3 -m pytest execution/test_ws_resilient.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest  # noqa: E402

from execution.ws_resilient import (  # noqa: E402
    E_BUFFER_DROPPED,
    E_BUFFER_FLUSHED,
    E_CONNECTED,
    E_DEAD,
    E_DISCONNECTED,
    E_GAP_DETECTED,
    E_HEARTBEAT_TIMEOUT,
    E_MESSAGE,
    E_PING_SENT,
    E_RECONNECT_SCHEDULED,
    E_REPLAY_REQUESTED,
    ResilientWs,
    ResilientWsPolicy,
    WsState,
    backoff_delay_s,
)


class FakeWs:
    """In-memory fake transport: records sends, scriptable close."""

    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, msg):
        self.sent.append(msg)

    def close(self):
        self.closed = True


def _seq_extractor(frame):
    return frame.get("seq") if isinstance(frame, dict) else None


def _make(fail_first=0, **policy_kw):
    """Build a wrapper + factory; the factory fails ``fail_first``
    times before yielding fresh FakeWs connections."""
    conns = []
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise ConnectionRefusedError("refused")
        conn = FakeWs()
        conns.append(conn)
        return conn

    policy = ResilientWsPolicy(**policy_kw)
    ws = ResilientWs(
        connection_factory=factory,
        policy=policy,
        sequence_extractor=_seq_extractor,
        rng=lambda: 0.5,  # pinned: unjittered backoff
        name="test",
    )
    return ws, conns, calls


# -- pure backoff --------------------------------------------------------------


def test_backoff_exponential_capped():
    p = ResilientWsPolicy(base_backoff_s=1.0, max_backoff_s=10.0,
                          jitter_fraction=0.0)
    assert backoff_delay_s(1, p, u=0.5) == 1.0
    assert backoff_delay_s(2, p, u=0.5) == 2.0
    assert backoff_delay_s(3, p, u=0.5) == 4.0
    assert backoff_delay_s(10, p, u=0.5) == 10.0  # capped
    with pytest.raises(ValueError):
        backoff_delay_s(0, p)


def test_backoff_jitter_bounds():
    p = ResilientWsPolicy(base_backoff_s=10.0, max_backoff_s=100.0,
                          jitter_fraction=0.2)
    assert backoff_delay_s(1, p, u=0.0) == pytest.approx(8.0)
    assert backoff_delay_s(1, p, u=0.999) == pytest.approx(11.996)
    assert backoff_delay_s(1, p, u=0.5) == pytest.approx(10.0)


# -- connect / disconnect / reconnect -------------------------------------------


def test_connect_open():
    ws, conns, _ = _make()
    events = ws.connect(now_s=0.0)
    assert ws.state == WsState.OPEN
    assert events[0].kind == E_CONNECTED
    assert len(conns) == 1


def test_disconnect_reconnect_cycle():
    ws, conns, _ = _make(base_backoff_s=1.0, max_backoff_s=60.0)
    ws.connect(now_s=0.0)
    events = ws.on_disconnect(now_s=5.0, reason="tcp_reset")
    kinds = [e.kind for e in events]
    assert kinds == [E_DISCONNECTED, E_RECONNECT_SCHEDULED]
    assert ws.state == WsState.RECONNECTING
    # pinned rng → delay == base * 2^0 == 1.0 → next attempt at 6.0
    assert ws._next_attempt_at_s == pytest.approx(6.0)

    # tick before the deadline: nothing happens
    assert ws.tick(now_s=5.5) == []
    assert ws.state == WsState.RECONNECTING

    # tick at the deadline: reconnect
    events = ws.tick(now_s=6.0)
    kinds = [e.kind for e in events]
    assert E_CONNECTED in kinds
    assert ws.state == WsState.OPEN
    assert len(conns) == 2
    assert conns[0].closed  # old transport was closed


def test_backoff_progression_across_drops():
    # A successful reconnect resets the attempt counter, so the
    # progression restarts from base after each recovery.
    ws, _, _ = _make(base_backoff_s=1.0, max_backoff_s=60.0)
    ws.connect(now_s=0.0)
    ws.on_disconnect(now_s=1.0)
    assert ws._next_attempt_at_s == pytest.approx(2.0)   # 1s
    ws.tick(now_s=2.0)
    ws.on_disconnect(now_s=3.0)
    assert ws._next_attempt_at_s == pytest.approx(4.0)   # 1s (reset)
    ws.tick(now_s=4.0)
    ws.on_disconnect(now_s=6.0)
    assert ws._next_attempt_at_s == pytest.approx(7.0)   # 1s (reset)


def test_backoff_progression_on_failed_reconnects():
    # Consecutive FAILED reconnect attempts grow the delay
    # exponentially (attempt counter only resets on success).
    ws, _, _ = _make(fail_first=99, base_backoff_s=1.0,
                     max_backoff_s=60.0, max_reconnect_attempts=0)
    ws.connect(now_s=0.0)                    # attempt 1 fails
    assert ws._next_attempt_at_s == pytest.approx(1.0)   # 1s
    ws.tick(now_s=1.0)                       # attempt 2 fails
    assert ws._next_attempt_at_s == pytest.approx(3.0)   # 2s
    ws.tick(now_s=3.0)                       # attempt 3 fails
    assert ws._next_attempt_at_s == pytest.approx(7.0)   # 4s


def test_dead_after_attempt_cap():
    ws, _, _ = _make(fail_first=99, base_backoff_s=1.0,
                     max_reconnect_attempts=2)
    events = ws.connect(now_s=0.0)  # attempt 1 fails
    assert ws.state == WsState.RECONNECTING
    events = ws.tick(now_s=1.0)     # attempt 2 fails
    assert ws.state == WsState.RECONNECTING
    events = ws.tick(now_s=3.0)     # attempt 3 exceeds cap
    kinds = [e.kind for e in events]
    assert E_DEAD in kinds
    assert ws.state == WsState.DEAD
    # on_disconnect on a dead socket is a no-op
    assert ws.on_disconnect(now_s=4.0) == []


def test_successful_reconnect_resets_attempts():
    ws, _, _ = _make(base_backoff_s=1.0)
    ws.connect(now_s=0.0)
    ws.on_disconnect(now_s=1.0)
    ws.tick(now_s=2.0)
    assert ws.state == WsState.OPEN
    ws.on_disconnect(now_s=10.0)
    # backoff restarted from base
    assert ws._next_attempt_at_s == pytest.approx(11.0)


# -- gap detection + replay ------------------------------------------------------


def test_gap_detection_and_replay_request():
    ws, _, _ = _make()
    ws.connect(now_s=0.0)
    ws.on_message({"seq": 1}, now_s=1.0)
    ws.on_message({"seq": 2}, now_s=2.0)
    events = ws.on_message({"seq": 5}, now_s=3.0)
    kinds = [e.kind for e in events]
    assert E_GAP_DETECTED in kinds
    assert E_REPLAY_REQUESTED in kinds
    replay = next(e for e in events if e.kind == E_REPLAY_REQUESTED)
    assert replay.get("from_seq") == 3
    assert replay.get("to_seq") == 4
    assert ws.last_seq == 5
    snap = ws.snapshot()
    assert snap["n_gaps"] == 1 and snap["n_replay_requests"] == 1


def test_in_order_messages_no_gap():
    ws, _, _ = _make()
    ws.connect(now_s=0.0)
    for i, seq in enumerate((1, 2, 3)):
        events = ws.on_message({"seq": seq}, now_s=float(i))
        assert [e.kind for e in events] == [E_MESSAGE]
    # duplicate / out-of-order old seq: no gap, last_seq unchanged
    events = ws.on_message({"seq": 2}, now_s=4.0)
    assert E_GAP_DETECTED not in [e.kind for e in events]
    assert ws.last_seq == 3


def test_frames_without_seq_are_safe():
    ws, _, _ = _make()
    ws.connect(now_s=0.0)
    events = ws.on_message({"pong": True}, now_s=1.0)
    assert events[0].get("seq") is None
    assert ws.last_seq is None


# -- heartbeat --------------------------------------------------------------------


def test_heartbeat_ping_then_pong():
    ws, conns, _ = _make(ping_interval_s=10.0, pong_timeout_s=5.0)
    ws.connect(now_s=0.0)
    # silence until t=10 → ping
    events = ws.tick(now_s=10.0)
    assert [e.kind for e in events] == [E_PING_SENT]
    assert conns[0].sent == ["ping"]
    # pong (any inbound frame) clears the pending ping
    ws.on_message({"pong": True}, now_s=12.0)
    assert ws.tick(now_s=20.0) != [E_HEARTBEAT_TIMEOUT]
    assert ws.state == WsState.OPEN


def test_heartbeat_timeout_triggers_reconnect():
    ws, _, _ = _make(ping_interval_s=10.0, pong_timeout_s=5.0,
                     base_backoff_s=1.0)
    ws.connect(now_s=0.0)
    ws.tick(now_s=10.0)   # ping sent
    events = ws.tick(now_s=15.0)  # no pong within 5s
    kinds = [e.kind for e in events]
    assert E_HEARTBEAT_TIMEOUT in kinds
    assert E_DISCONNECTED in kinds
    assert E_RECONNECT_SCHEDULED in kinds
    assert ws.state == WsState.RECONNECTING
    assert ws.snapshot()["n_heartbeat_timeouts"] == 1


# -- disconnect buffering ----------------------------------------------------------


def test_buffering_and_flush_on_reconnect():
    ws, conns, _ = _make(base_backoff_s=1.0)
    ws.connect(now_s=0.0)
    ws.send("live-msg", now_s=0.5)
    assert conns[0].sent == ["live-msg"]

    ws.on_disconnect(now_s=1.0)
    ws.send("buffered-1", now_s=1.1)
    ws.send("buffered-2", now_s=1.2)
    assert ws.buffered == 2

    events = ws.tick(now_s=2.0)  # reconnect
    flushed = next(e for e in events if e.kind == E_BUFFER_FLUSHED)
    assert flushed.get("n") == 2
    assert ws.buffered == 0
    # FIFO order on the new connection
    assert conns[1].sent == ["buffered-1", "buffered-2"]


def test_buffer_cap_drops_oldest():
    ws, conns, _ = _make(base_backoff_s=1.0, buffer_max_messages=3)
    ws.connect(now_s=0.0)
    ws.on_disconnect(now_s=1.0)
    for i in range(5):
        events = ws.send(f"m{i}", now_s=1.0 + i * 0.1)
    assert ws.buffered == 3
    assert ws.snapshot()["n_buffer_dropped"] == 2
    dropped = [e for e in events if e.kind == E_BUFFER_DROPPED]
    assert dropped and dropped[0].get("reason") == (
        "buffer_full_drop_oldest")
    ws.tick(now_s=2.0)
    # oldest two (m0, m1) dropped; newest three flushed
    assert conns[1].sent == ["m2", "m3", "m4"]


def test_buffering_disabled_drops_immediately():
    ws, _, _ = _make(buffer_max_messages=0)
    ws.connect(now_s=0.0)
    ws.on_disconnect(now_s=1.0)
    events = ws.send("m", now_s=1.1)
    assert events[0].kind == E_BUFFER_DROPPED
    assert events[0].get("reason") == "buffering_disabled"
    assert ws.buffered == 0


def test_send_on_dead_is_buffered_not_sent():
    ws, _, _ = _make(fail_first=99, max_reconnect_attempts=1,
                     base_backoff_s=1.0)
    ws.connect(now_s=0.0)
    ws.tick(now_s=1.0)
    ws.tick(now_s=2.0)
    assert ws.state == WsState.DEAD
    ws.send("m", now_s=3.0)
    assert ws.buffered == 1  # buffered, no transport write


# -- policy validation ---------------------------------------------------------------


def test_policy_validation():
    with pytest.raises(ValueError):
        ResilientWsPolicy(base_backoff_s=0.0)
    with pytest.raises(ValueError):
        ResilientWsPolicy(base_backoff_s=10.0, max_backoff_s=1.0)
    with pytest.raises(ValueError):
        ResilientWsPolicy(jitter_fraction=1.5)
    with pytest.raises(ValueError):
        ResilientWsPolicy(buffer_max_messages=-1)
