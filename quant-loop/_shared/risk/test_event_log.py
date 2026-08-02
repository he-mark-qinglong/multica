"""Tests for _shared/risk/event_log.py (D20)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.risk.event_log import (
    RiskEvent,
    RiskEventType,
    append_event,
    filter_events,
    load_events,
    query_events,
)


def _ev(ts, etype=RiskEventType.LIMIT_BREACH, strategy="mm_btc", **kw):
    return RiskEvent(ts=ts, event_type=etype.value, strategy=strategy, **kw)


def test_all_required_event_types_exist():
    names = {e.name for e in RiskEventType}
    assert names == {
        "LIMIT_BREACH", "KILL_SWITCH", "MARGIN_CALL",
        "LIQUIDATION", "DRIFT", "HEARTBEAT",
    }


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "risk" / "events.jsonl"
    e1 = _ev(1000.0, message="dd limit crossed", context={"dd_pct": 12.3})
    e2 = _ev(1010.0, etype=RiskEventType.KILL_SWITCH, strategy="trend_eth")
    append_event(path, e1)
    append_event(path, e2)

    events = load_events(path)
    assert len(events) == 2
    assert events[0].ts == 1000.0
    assert events[0].event_type == RiskEventType.LIMIT_BREACH.value
    assert events[0].context["dd_pct"] == 12.3
    assert events[1].strategy == "trend_eth"


def test_load_missing_file_returns_empty(tmp_path):
    assert load_events(tmp_path / "nope.jsonl") == ()


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    append_event(path, _ev(1.0))
    with path.open("a") as fh:
        fh.write("{torn write\n")
        fh.write('{"ts": "x", "event_type": 42}\n')
    events = load_events(path)
    assert len(events) == 1
    assert events[0].ts == 1.0


def test_invalid_event_type_fails_at_construction():
    with pytest.raises(ValueError):
        RiskEvent(ts=1.0, event_type="NOT_A_REAL_TYPE")


def test_filter_by_type_strategy_and_window():
    events = (
        _ev(100.0, RiskEventType.LIMIT_BREACH, "a"),
        _ev(200.0, RiskEventType.KILL_SWITCH, "a"),
        _ev(300.0, RiskEventType.LIMIT_BREACH, "b"),
        _ev(400.0, RiskEventType.DRIFT, "a"),
    )
    by_type = filter_events(events, event_type="LIMIT_BREACH")
    assert {e.strategy for e in by_type} == {"a", "b"}
    assert all(e.event_type == "LIMIT_BREACH" for e in by_type)

    by_strat = filter_events(events, strategy="a")
    assert len(by_strat) == 3

    window = filter_events(events, start_ts=200.0, end_ts=300.0)
    assert [e.ts for e in window] == [200.0, 300.0]

    combined = filter_events(
        events, event_type=RiskEventType.LIMIT_BREACH, strategy="a"
    )
    assert len(combined) == 1 and combined[0].ts == 100.0


def test_query_events_loads_and_filters(tmp_path):
    path = tmp_path / "events.jsonl"
    append_event(path, _ev(1.0, RiskEventType.HEARTBEAT, "mm_btc"))
    append_event(path, _ev(2.0, RiskEventType.HEARTBEAT, "trend"))
    append_event(path, _ev(3.0, RiskEventType.MARGIN_CALL, "mm_btc"))

    hits = query_events(path, event_type="HEARTBEAT", strategy="mm_btc")
    assert len(hits) == 1 and hits[0].ts == 1.0


def test_event_is_frozen():
    e = _ev(1.0)
    with pytest.raises(AttributeError):
        e.message = "rewrite history"  # type: ignore[misc]
