"""Tests for _shared/ops/audit_trail.py (H20)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.ops.audit_trail import (
    AuditRecord,
    TransitionKind,
    append_record,
    diff_record,
    diff_summary,
    load_trail,
    query_trail,
    tail,
)


def _rec(ts, kind=TransitionKind.START, actor="auto", strategy="mm_btc", **kw):
    return AuditRecord(ts=ts, kind=kind.value, actor=actor, strategy=strategy, **kw)


def test_all_required_transition_kinds_exist():
    names = {k.name for k in TransitionKind}
    assert names == {"START", "CONFIG_CHANGE", "OPEN", "CLOSE", "KILL", "SHUTDOWN", "STATUS"}


def test_actor_validation():
    with pytest.raises(ValueError, match="actor"):
        AuditRecord(ts=1.0, kind="START", actor="robot")
    with pytest.raises(ValueError):
        AuditRecord(ts=1.0, kind="NOT_A_KIND", actor="auto")


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "run" / "audit.jsonl"
    r1 = _rec(100.0, before={"state": "stopped"}, after={"state": "running"})
    r2 = _rec(200.0, kind=TransitionKind.KILL, actor="manual",
              note="fat finger suspected")
    append_record(path, r1)
    append_record(path, r2)

    records = load_trail(path)
    assert len(records) == 2
    assert records[0].after["state"] == "running"
    assert records[1].kind == "KILL"
    assert records[1].actor == "manual"
    assert records[1].note == "fat finger suspected"


def test_load_missing_and_corrupt(tmp_path):
    assert load_trail(tmp_path / "nope.jsonl") == ()
    path = tmp_path / "audit.jsonl"
    append_record(path, _rec(1.0))
    with path.open("a") as fh:
        fh.write("{torn\n")
        fh.write('{"ts": "x"}\n')
    records = load_trail(path)
    assert len(records) == 1 and records[0].ts == 1.0


def test_diff_summary_added_removed_changed():
    diff = diff_summary(
        {"a": 1, "b": 2, "c": 3},
        {"a": 1, "b": 20, "d": 4},
    )
    assert diff == {"b": (2, 20), "c": (3, None), "d": (None, 4)}
    assert diff_summary({"x": 1}, {"x": 1}) == {}


def test_diff_record_uses_before_and_after():
    r = _rec(1.0, kind=TransitionKind.CONFIG_CHANGE,
             before={"max_pos": 10, "mode": "paper"},
             after={"max_pos": 5, "mode": "paper"})
    assert diff_record(r) == {"max_pos": (10, 5)}


def test_tail_returns_newest_oldest_first():
    records = tuple(_rec(float(i)) for i in range(20))
    t = tail(records, 3)
    assert [r.ts for r in t] == [17.0, 18.0, 19.0]
    assert tail(records, 0) == ()
    assert tail(records, 100) == records
    with pytest.raises(ValueError):
        tail(records, -1)


def test_query_trail_filters():
    records = (
        _rec(100.0, TransitionKind.START, "auto", "a"),
        _rec(200.0, TransitionKind.OPEN, "auto", "a"),
        _rec(300.0, TransitionKind.KILL, "manual", "b"),
        _rec(400.0, TransitionKind.SHUTDOWN, "auto", "a"),
    )
    kills = query_trail(records, kind="KILL")
    assert len(kills) == 1 and kills[0].actor == "manual"

    manual = query_trail(records, actor="manual")
    assert len(manual) == 1

    by_strat = query_trail(records, strategy="a")
    assert len(by_strat) == 3

    window = query_trail(records, start_ts=200.0, end_ts=300.0)
    assert [r.ts for r in window] == [200.0, 300.0]

    combined = query_trail(records, kind="OPEN", strategy="a", actor="auto")
    assert len(combined) == 1 and combined[0].ts == 200.0


def test_record_is_frozen():
    r = _rec(1.0)
    with pytest.raises(AttributeError):
        r.actor = "manual"  # type: ignore[misc]
