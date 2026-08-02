"""Tests for portfolio/capital_pool.py (I11)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.portfolio.capital_pool import (
    CapitalPool,
    PoolConfig,
    Transfer,
    compute_transfers,
)


# ---------------------------------------------------------- pure core
def test_compute_transfers_restores_targets_exactly():
    eq = {"A": 70.0, "B": 20.0, "C": 10.0}
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    transfers = compute_transfers(eq, w)
    after = dict(eq)
    for t in transfers:
        after[t.src] -= t.amount
        after[t.dst] += t.amount
    total = sum(eq.values())
    for name in eq:
        assert after[name] == pytest.approx(w[name] * total)


def test_compute_transfers_minimal_count_and_direction():
    eq = {"A": 100.0, "B": 0.0, "C": 0.0}
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    transfers = compute_transfers(eq, w)
    # One debtor, two creditors → exactly 2 transfers, all out of A.
    assert len(transfers) == 2
    assert all(t.src == "A" for t in transfers)
    by_dst = {t.dst: t.amount for t in transfers}
    assert by_dst["B"] == pytest.approx(30.0)
    assert by_dst["C"] == pytest.approx(20.0)


def test_compute_transfers_at_target_is_empty():
    eq = {"A": 50.0, "B": 50.0}
    assert compute_transfers(eq, {"A": 0.5, "B": 0.5}) == ()


def test_compute_transfers_validates_inputs():
    with pytest.raises(ValueError):
        compute_transfers({"A": 1.0}, {"A": 0.5})          # weights ≠ 1
    with pytest.raises(ValueError):
        compute_transfers({"A": 1.0}, {"A": 1.0, "B": 0.0})  # key mismatch
    with pytest.raises(ValueError):
        compute_transfers({"A": 1.0, "B": 0.0}, {"A": 1.5, "B": -0.5})


# ------------------------------------------------------------- pool state
def _pool(**kw):
    kw.setdefault("config", PoolConfig(drift_threshold=0.05))
    return CapitalPool(
        target_weights={"A": 0.6, "B": 0.4},
        equities={"A": 600.0, "B": 400.0},
        **kw,
    )


def test_pool_starts_at_target_no_rebalance():
    pool = _pool()
    assert pool.max_abs_drift() == pytest.approx(0.0)
    assert pool.rebalance() == ()
    assert pool.ledger == ()


def test_small_drift_below_threshold_no_transfer():
    pool = _pool()
    pool.apply_pnl("A", 30.0)   # A: 630/1030 → drift ≈ 0.0116 < 0.05
    assert pool.rebalance() == ()


def test_large_drift_triggers_rebalance_with_ledger():
    pool = _pool()
    pool.apply_pnl("A", 200.0)  # A: 800/1200 = 0.667 vs 0.6 → drift 0.067
    records = pool.rebalance()
    assert len(records) == 1
    rec = records[0]
    assert rec.src == "A" and rec.dst == "B"
    assert rec.amount == pytest.approx(80.0)
    assert rec.reason == "rebalance"
    assert rec.pool_equity_after == pytest.approx(1200.0)
    # Post-rebalance every strategy sits at target.
    assert pool.equities["A"] == pytest.approx(720.0)
    assert pool.equities["B"] == pytest.approx(480.0)
    assert pool.ledger == records
    # Second call: no drift left, nothing new.
    assert pool.rebalance() == ()


def test_force_rebalance_ignores_threshold():
    pool = _pool()
    pool.apply_pnl("A", 10.0)
    records = pool.rebalance(force=True)
    assert len(records) == 1


def test_apply_pnl_guards():
    pool = _pool()
    with pytest.raises(KeyError):
        pool.apply_pnl("NOPE", 1.0)
    with pytest.raises(ValueError):
        pool.apply_pnl("A", -601.0)   # would go negative


def test_add_strategy_rescales_and_rebalances():
    pool = _pool()
    # 100 ≠ 20% of the post-join pool, so a rebalance to new targets fires.
    records = pool.add_strategy("C", target_weight=0.2, initial_equity=100.0)
    # Weights: A 0.48, B 0.32, C 0.2; total 1100.
    assert pool.target_weights == pytest.approx({"A": 0.48, "B": 0.32, "C": 0.2})
    reasons = [r.reason for r in records]
    assert reasons[0] == "join"
    join = records[0]
    assert join.src == "EXTERNAL" and join.dst == "C" and join.amount == 100.0
    # Rebalance to new targets fired (A and B above their new targets).
    assert any(r.reason == "rebalance" for r in records)
    for name, w in pool.target_weights.items():
        assert pool.equities[name] == pytest.approx(w * pool.total_equity)


def test_add_strategy_validation():
    pool = _pool()
    with pytest.raises(ValueError):
        pool.add_strategy("A", 0.1)          # duplicate
    with pytest.raises(ValueError):
        pool.add_strategy("C", 1.5)          # weight out of range
    with pytest.raises(ValueError):
        pool.add_strategy("C", 0.2, initial_equity=-1.0)


def test_remove_strategy_redistributes_and_renormalises():
    pool = _pool()
    records = pool.remove_strategy("B")
    assert set(pool.equities) == {"A"}
    assert pool.target_weights == {"A": 1.0}
    leave = records[0]
    assert leave.reason == "leave"
    assert leave.src == "B" and leave.dst == "EXTERNAL"
    assert leave.amount == pytest.approx(400.0)
    assert leave.pool_equity_after == pytest.approx(600.0)
    # All of A's equity is at target (only member) → no rebalance records.
    assert all(r.reason == "leave" for r in records)


def test_remove_unknown_strategy_raises():
    pool = _pool()
    with pytest.raises(KeyError):
        pool.remove_strategy("NOPE")


def test_full_lifecycle_join_trade_leave():
    pool = CapitalPool(
        target_weights={"A": 0.5, "B": 0.5},
        equities={"A": 1000.0, "B": 1000.0},
        config=PoolConfig(drift_threshold=0.02),
    )
    pool.apply_pnl("A", 300.0)
    pool.apply_pnl("B", -100.0)
    pool.rebalance()
    pool.add_strategy("C", 0.25, initial_equity=500.0)
    pool.remove_strategy("B")
    # Invariants: weights sum to 1, equities match targets, ledger ordered.
    assert sum(pool.target_weights.values()) == pytest.approx(1.0)
    for name, w in pool.target_weights.items():
        assert pool.equities[name] == pytest.approx(w * pool.total_equity)
    assert len(pool.ledger) >= 3
    assert all(isinstance(r.amount, float) and r.amount > 0 for r in pool.ledger)


def test_config_and_records_frozen():
    cfg = PoolConfig()
    with pytest.raises(Exception):
        cfg.drift_threshold = 0.9
    pool = _pool()
    pool.apply_pnl("A", 200.0)
    (rec,) = pool.rebalance()
    with pytest.raises(Exception):
        rec.amount = 0.0
    assert isinstance(rec, object)


def test_transfer_dataclass_fields():
    t = Transfer(src="A", dst="B", amount=1.5)
    assert (t.src, t.dst, t.amount) == ("A", "B", 1.5)
