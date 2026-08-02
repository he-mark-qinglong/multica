"""Tests for portfolio/lifecycle.py (I16)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json

import pytest

from _shared.portfolio.lifecycle import (
    LifecycleManager, LifecycleState, StrategyMetrics, TransitionRecord,
    TransitionRule,
)

S = LifecycleState
GOOD = StrategyMetrics(sharpe=1.5, max_drawdown=-0.10)
BAD_SHARPE = StrategyMetrics(sharpe=0.5, max_drawdown=-0.10)
BAD_DD = StrategyMetrics(sharpe=1.5, max_drawdown=-0.30)
MISSING = StrategyMetrics()


def _walk_to_paper(mgr, sid="s1"):
    mgr.register(sid)
    assert mgr.transition(sid, S.BACKTESTING).accepted
    assert mgr.transition(sid, S.PAPER).accepted


def test_happy_path_full_funnel():
    mgr = LifecycleManager()
    _walk_to_paper(mgr)
    assert mgr.transition("s1", S.LIVE, GOOD).accepted
    assert mgr.state("s1") == S.LIVE
    assert mgr.transition("s1", S.DEGRADED).accepted
    assert mgr.transition("s1", S.LIVE).accepted      # recovery
    assert mgr.transition("s1", S.RETIRED).accepted
    assert mgr.state("s1") == S.RETIRED


def test_illegal_transition_rejected():
    mgr = LifecycleManager()
    mgr.register("s1")
    rec = mgr.transition("s1", S.LIVE, GOOD)  # REGISTERED -> LIVE
    assert not rec.accepted
    assert "illegal transition" in rec.reason
    assert mgr.state("s1") == S.REGISTERED


def test_retired_is_terminal():
    mgr = LifecycleManager()
    mgr.register("s1")
    mgr.transition("s1", S.RETIRED)
    rec = mgr.transition("s1", S.BACKTESTING)
    assert not rec.accepted and "illegal" in rec.reason


def test_paper_to_live_default_conditions():
    mgr = LifecycleManager()
    _walk_to_paper(mgr)
    assert not mgr.transition("s1", S.LIVE, BAD_SHARPE).accepted
    assert not mgr.transition("s1", S.LIVE, BAD_DD).accepted
    assert not mgr.transition("s1", S.LIVE, MISSING).accepted
    assert mgr.state("s1") == S.PAPER            # unchanged after rejections
    assert mgr.transition("s1", S.LIVE, GOOD).accepted


def test_condition_failure_reason_recorded():
    mgr = LifecycleManager()
    _walk_to_paper(mgr)
    rec = mgr.transition("s1", S.LIVE, BAD_SHARPE)
    assert "condition failed" in rec.reason
    assert "Sharpe" in rec.reason


def test_pluggable_condition_override():
    """A desk can tighten the promotion gate without touching the SM."""
    strict = TransitionRule(
        to_state=S.LIVE,
        condition=lambda m: m.sharpe is not None and m.sharpe > 2.0,
        description="desk rule: Sharpe > 2",
    )
    mgr = LifecycleManager(rules={(S.PAPER, S.LIVE): strict})
    _walk_to_paper(mgr)
    assert not mgr.transition("s1", S.LIVE, GOOD).accepted   # 1.5 < 2.0
    assert mgr.transition(
        "s1", S.LIVE, StrategyMetrics(sharpe=2.5, max_drawdown=-0.5)
    ).accepted  # custom rule ignores drawdown


def test_audit_log_records_accepted_and_rejected(tmp_path):
    path = tmp_path / "audit.jsonl"
    mgr = LifecycleManager(audit_path=path)
    mgr.register("s1")
    mgr.transition("s1", S.LIVE, GOOD)          # rejected (illegal)
    mgr.transition("s1", S.BACKTESTING)         # accepted
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["accepted"] is False
    assert lines[0]["from_state"] == "registered"
    assert lines[0]["to_state"] == "live"
    assert lines[1]["accepted"] is True
    assert lines[1]["reason"] == ""
    # In-memory records match the file.
    assert all(isinstance(r, TransitionRecord) for r in mgr.records)
    assert len(mgr.records) == 2


def test_unknown_strategy_raises():
    mgr = LifecycleManager()
    with pytest.raises(KeyError):
        mgr.transition("ghost", S.BACKTESTING)
    with pytest.raises(KeyError):
        mgr.state("ghost")


def test_double_register_raises():
    mgr = LifecycleManager()
    mgr.register("s1")
    with pytest.raises(ValueError):
        mgr.register("s1")
