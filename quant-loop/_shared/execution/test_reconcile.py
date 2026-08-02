"""Tests for _shared/execution/reconcile.py (E7)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.execution.reconcile import (
    PositionDiff,
    ReconcileResult,
    Reconciler,
    compute_diff,
)


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------

def test_compute_diff_matching_positions():
    """Identical ledgers → all 'ok'."""
    local = {"BTC": 1.0, "ETH": 10.0}
    exchange = {"BTC": 1.0, "ETH": 10.0}
    diffs = compute_diff(local, exchange)
    assert len(diffs) == 2
    assert all(d.severity == "ok" for d in diffs)


def test_compute_diff_missing_symbol_in_local():
    """Symbol in exchange but not local → diff = -exchange_qty."""
    local = {"BTC": 1.0}
    exchange = {"BTC": 1.0, "ETH": 5.0}
    diffs = compute_diff(local, exchange)
    eth_diff = next(d for d in diffs if d.symbol == "ETH")
    assert eth_diff.local_qty == 0.0
    assert eth_diff.exchange_qty == 5.0
    assert eth_diff.diff == pytest.approx(-5.0)
    assert eth_diff.severity == "critical"


def test_compute_diff_missing_symbol_in_exchange():
    """Symbol in local but not exchange → diff = +local_qty."""
    local = {"BTC": 1.0, "SOL": 100.0}
    exchange = {"BTC": 1.0}
    diffs = compute_diff(local, exchange)
    sol_diff = next(d for d in diffs if d.symbol == "SOL")
    assert sol_diff.local_qty == 100.0
    assert sol_diff.exchange_qty == 0.0
    assert sol_diff.diff == pytest.approx(100.0)
    assert sol_diff.severity == "critical"


def test_compute_diff_severity_warn():
    """Diff between warn_threshold and critical_threshold → 'warn'."""
    local = {"BTC": 1.05}
    exchange = {"BTC": 1.0}
    diffs = compute_diff(local, exchange, warn_threshold=0.01, critical_threshold=0.1)
    assert diffs[0].severity == "warn"


def test_compute_diff_severity_critical():
    """Diff above critical_threshold → 'critical'."""
    local = {"BTC": 2.0}
    exchange = {"BTC": 1.0}
    diffs = compute_diff(local, exchange, warn_threshold=0.01, critical_threshold=0.1)
    assert diffs[0].severity == "critical"


def test_compute_diff_floating_point_dust():
    """Tiny diff below warn_threshold → 'ok'."""
    local = {"BTC": 1.000001}
    exchange = {"BTC": 1.0}
    diffs = compute_diff(local, exchange, warn_threshold=0.01)
    assert diffs[0].severity == "ok"


def test_compute_diff_sorted_by_symbol():
    """Diffs should be sorted alphabetically by symbol."""
    local = {"ZEC": 1.0, "ADA": 1.0, "BTC": 1.0}
    exchange = {"ZEC": 1.0, "ADA": 1.0, "BTC": 1.0}
    diffs = compute_diff(local, exchange)
    symbols = [d.symbol for d in diffs]
    assert symbols == ["ADA", "BTC", "ZEC"]


def test_compute_diff_empty():
    """Both empty dicts → empty tuple."""
    assert compute_diff({}, {}) == ()


def test_compute_diff_returns_positiondiff_objects():
    """Each diff should be a PositionDiff frozen dataclass."""
    diffs = compute_diff({"BTC": 1.0}, {"BTC": 1.0})
    assert all(isinstance(d, PositionDiff) for d in diffs)


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------

def test_reconciler_immediate_convergence():
    """Matching positions → converged on first attempt."""
    r = Reconciler(max_retries=3)
    result = r.reconcile({"BTC": 1.0}, {"BTC": 1.0})
    assert result.converged
    assert result.attempts == 1
    assert result.alerts == ()


def test_reconciler_persistent_diff():
    """Diff never resolves → not converged after max_retries."""
    r = Reconciler(max_retries=3, retry_delay_sec=0.0)
    result = r.reconcile({"BTC": 2.0}, {"BTC": 1.0})
    assert not result.converged
    assert result.attempts == 3
    assert len(result.alerts) > 0
    assert "BTC" in result.alerts[0]


def test_reconciler_converges_after_retry():
    """fetch_fn eventually returns matching data → converges."""
    call_count = [0]
    exchange_data = [{"BTC": 1.0}, {"BTC": 2.0}]  # first wrong, second right

    def fetch_fn():
        idx = min(call_count[0], len(exchange_data) - 1)
        call_count[0] += 1
        return exchange_data[idx]

    r = Reconciler(max_retries=3, retry_delay_sec=0.0)
    # Start with a diff, but fetch_fn will return the correct data
    result = r.reconcile({"BTC": 2.0}, {"BTC": 1.0}, fetch_fn=fetch_fn)
    assert result.converged
    assert result.attempts <= 3


def test_reconciler_no_fetch_fn():
    """Without fetch_fn, exchange data never changes → no convergence."""
    r = Reconciler(max_retries=2, retry_delay_sec=0.0)
    result = r.reconcile({"BTC": 1.5}, {"BTC": 1.0})
    assert not result.converged
    assert result.attempts == 2


def test_reconciler_custom_thresholds():
    """Custom thresholds should affect severity classification."""
    r = Reconciler(
        max_retries=1,
        warn_threshold=0.5,
        critical_threshold=2.0,
    )
    # diff = 0.3, warn_threshold = 0.5 → 0.3 < 0.5 → "ok"
    result = r.reconcile({"BTC": 1.3}, {"BTC": 1.0})
    btc_diff = next(d for d in result.diffs if d.symbol == "BTC")
    assert btc_diff.severity == "ok"

    # diff = 0.8, between 0.5 and 2.0 → "warn"
    result2 = r.reconcile({"BTC": 1.8}, {"BTC": 1.0})
    btc_diff2 = next(d for d in result2.diffs if d.symbol == "BTC")
    assert btc_diff2.severity == "warn"


def test_reconciler_fetch_fn_exception_handled():
    """If fetch_fn raises, reconciler should handle gracefully."""
    call_count = [0]

    def flaky_fetch():
        call_count[0] += 1
        raise ConnectionError("exchange API down")

    r = Reconciler(max_retries=3, retry_delay_sec=0.0)
    result = r.reconcile({"BTC": 2.0}, {"BTC": 1.0}, fetch_fn=flaky_fetch)
    assert not result.converged
    assert len(result.alerts) > 0


def test_reconcile_result_is_frozen():
    """ReconcileResult should be immutable."""
    diffs = (PositionDiff(symbol="BTC", local_qty=1.0, exchange_qty=1.0,
                          diff=0.0, severity="ok"),)
    result = ReconcileResult(diffs=diffs, converged=True, attempts=1, alerts=())
    with pytest.raises(Exception):
        result.converged = False


def test_positiondiff_is_frozen():
    """PositionDiff should be immutable."""
    d = PositionDiff("BTC", 1.0, 1.0, 0.0, "ok")
    with pytest.raises(Exception):
        d.diff = 5.0
