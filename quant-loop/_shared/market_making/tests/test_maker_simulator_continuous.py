"""Tests for the continuous market-making mode of maker_simulator.py.

Pins the core acceptance criterion: on the continuous quoting path, when
inventory is non-zero, the Avellaneda-Stoikov reservation price used for
quoting shifts by exactly ``q·γ·σ²·(T-t)`` away from fair value.
"""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.maker_simulator import (
    MakerSimConfig,
    _apply_signed_fill,
    simulate_market_making,
)
from _shared.market_making.inventory import empty_inventory


def _make_oscillating_aggtrades(n=2000, base_price=50000.0, seed=7):
    """Oscillating tape that generates frequent two-sided fills."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-04-19 00:00:00", periods=n, freq="1s", tz="UTC")
    prices = (base_price + 60 * np.sin(np.arange(n) * 0.05)
              + rng.standard_normal(n) * 3)
    return pd.DataFrame({
        "ts": ts,
        "price": prices,
        "qty": [0.5] * n,
        "is_buyer_maker": [i % 3 == 0 for i in range(n)],
    })


def _continuous_config(**overrides):
    base = dict(
        mode="continuous",
        record_quotes=True,
        start_ts="2026-04-19",
        end_ts="2026-04-20",
        size_usd=500.0,
        max_inventory_usd=3000.0,
        base_spread_bp=1.0,
        max_hold_seconds=600.0,
    )
    base.update(overrides)
    return MakerSimConfig(**base)


# ---------------------------------------------------------------------------
# Pinned: reservation price shifts correctly with non-zero inventory
# ---------------------------------------------------------------------------

def test_continuous_reservation_price_exact_shift():
    """Every quote generated with non-zero inventory must satisfy

        rp = fv - q·γ·σ²·(T-t)

    exactly (the A-S formula), and the shift direction must oppose the
    inventory sign (long → rp < fv; short → rp > fv).

    Uses a large ``gamma`` so the theoretical shift is resolvable in
    float64 at BTC price scale (default γ=0.1 yields sub-ULP shifts).
    """
    df = _make_oscillating_aggtrades()
    cfg = _continuous_config(gamma=1e4, max_inventory_usd=20000.0)
    _, metrics = simulate_market_making(df, cfg)

    quotes = metrics["quotes"]
    assert len(quotes) > 0

    # Only quotes with non-zero inventory AND measurable sigma can shift.
    shifted = [q for q in quotes
               if abs(q["inventory_qty"]) > 0 and q["sigma"] > 0]
    assert len(shifted) > 50, "test tape must produce many shifted quotes"

    resolvable = 0
    for q in shifted:
        expected = (q["fair_value"]
                    - q["inventory_qty"] * cfg.gamma
                    * q["sigma"] ** 2 * cfg.horizon_seconds)
        assert q["reservation_price"] == pytest.approx(expected, rel=1e-12)
        # Shift opposes the inventory sign — but only assert when the
        # theoretical shift is resolvable in float64 at fv scale (~1e-11).
        theo_shift = (q["inventory_qty"] * cfg.gamma
                      * q["sigma"] ** 2 * cfg.horizon_seconds)
        if abs(theo_shift) > q["fair_value"] * 1e-12:
            resolvable += 1
            shift = q["reservation_price"] - q["fair_value"]
            assert np.sign(shift) == -np.sign(q["inventory_qty"])
    assert resolvable > 50, "γ=1e4 must make most shifts sign-resolvable"


def test_continuous_flat_inventory_means_no_shift():
    """Quotes generated while flat must sit exactly on fair value."""
    df = _make_oscillating_aggtrades()
    cfg = _continuous_config()
    _, metrics = simulate_market_making(df, cfg)

    flat = [q for q in metrics["quotes"] if abs(q["inventory_qty"]) == 0]
    assert len(flat) > 0
    for q in flat:
        assert q["reservation_price"] == pytest.approx(q["fair_value"],
                                                       rel=1e-12)


def test_single_position_mode_still_flat_reservation():
    """Legacy mode keeps inventory_qty=0 on the reservation price."""
    df = _make_oscillating_aggtrades()
    cfg = _continuous_config(mode="single_position")
    _, metrics = simulate_market_making(df, cfg)

    for q in metrics["quotes"]:
        assert q["inventory_qty"] == 0.0
        assert q["reservation_price"] == pytest.approx(q["fair_value"],
                                                       rel=1e-12)


# ---------------------------------------------------------------------------
# Continuous-mode behaviour
# ---------------------------------------------------------------------------

def test_continuous_keeps_quoting_after_fill():
    """Fills must not stop quoting: more fills than round-trips and
    multiple inventory-reducing (spread_capture) events."""
    df = _make_oscillating_aggtrades()
    cfg = _continuous_config()
    _, metrics = simulate_market_making(df, cfg)

    assert metrics["quotes_filled"] > metrics["n_trades"]
    assert metrics["exit_reasons"].get("spread_capture", 0) > 10


def test_continuous_inventory_cap_enforced():
    """|net_qty| never exceeds the USD→qty cap; breaches trigger flatten."""
    df = _make_oscillating_aggtrades()
    cfg = _continuous_config()
    _, metrics = simulate_market_making(df, cfg)

    cap_qty = cfg.max_inventory_usd / 50000.0
    # cap varies with price; allow 5% price drift headroom
    assert metrics["max_abs_inventory_qty"] <= cap_qty * 1.05 + 1e-9
    assert metrics["flatten_count"] >= 0
    assert metrics["mode"] == "continuous"
    assert "realized_pnl_usd" in metrics


def test_apply_signed_fill_flip_basis():
    """A fill that flips the position must reset the VWAP cost basis of
    the remainder to the fill price (not a blended notional)."""
    inv = empty_inventory(max_inventory=1.0)
    ts = pd.Timestamp("2026-04-19", tz="UTC")
    inv = _apply_signed_fill(inv, +0.02, 100.0, ts)   # long 0.02 @100
    assert inv.avg_price == pytest.approx(100.0)

    inv = _apply_signed_fill(inv, -0.03, 110.0, ts)   # flip to short 0.01
    assert inv.net_qty == pytest.approx(-0.01)
    assert inv.avg_price == pytest.approx(110.0)      # basis = flip price


def test_apply_signed_fill_partial_reduce_keeps_basis():
    """A partial reduce must leave the remainder at the prior cost basis
    (average-cost accounting); update_inventory's blend would not."""
    inv = empty_inventory(max_inventory=1.0)
    ts = pd.Timestamp("2026-04-19", tz="UTC")
    inv = _apply_signed_fill(inv, +0.02, 100.0, ts)
    inv = _apply_signed_fill(inv, -0.01, 110.0, ts)   # partial reduce
    assert inv.net_qty == pytest.approx(0.01)
    assert inv.avg_price == pytest.approx(100.0)      # basis preserved
    inv = _apply_signed_fill(inv, -0.01, 105.0, ts)   # exact close
    assert inv.is_flat


def test_continuous_optimal_spread_mode_runs():
    """spread_mode='optimal' wires optimal_spread.py into the quote path."""
    df = _make_oscillating_aggtrades(500)
    cfg = _continuous_config(spread_mode="optimal")
    trades, metrics = simulate_market_making(df, cfg)
    assert metrics["spread_mode"] == "optimal"
    assert metrics["quotes_generated"] > 0
    assert isinstance(trades, list)


def test_default_mode_is_legacy():
    """Default config must remain the legacy single-position behaviour."""
    assert MakerSimConfig().mode == "single_position"
    assert MakerSimConfig().spread_mode == "heuristic"
