"""Tests for hedged_grid_v1 strategy (synthetic data)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from strategies.hedged_grid_v1_20260802.strategy import (
    GridState,
    SymbolConfig,
    compute_metrics,
    efficiency_ratio,
    equity,
    grid_target_slots,
    hedged_grid_step,
    net_delta,
    run_symbol,
)

CAPITAL = 10_000.0


def _bars(close, start="2024-01-01", spread=0.5):
    """Synthetic 1h bars frame matching load_symbol_data's output shape."""
    close = np.asarray(close, dtype=float)
    idx = pd.date_range(start, periods=len(close), freq="1h", tz="UTC")
    return pd.DataFrame({
        "high": close + spread,
        "low": close - spread,
        "close": close,
    }, index=idx)


def _no_funding():
    return pd.DataFrame({"ts": pd.to_datetime([], utc=True),
                         "fundingRate": []})


def _fast_cfg(**kw):
    """Small-window config so synthetic tests don't need 30d of warmup."""
    base = dict(symbol="SYNTH", er_threshold=2.0, er_period=4, n_levels=10,
                range_days=1, hedge_band=0.10, spot_fee_bp=10.0,
                perp_fee_bp=5.0)
    base.update(kw)
    return SymbolConfig(**base)


# ---------------------------------------------------------------------------
# Indicator / target unit tests
# ---------------------------------------------------------------------------

def test_efficiency_ratio_trend_vs_chop():
    trend = pd.Series(np.linspace(100, 200, 50))
    er_trend = efficiency_ratio(trend, 10).iloc[-1]
    assert er_trend == pytest.approx(1.0)

    chop = pd.Series([100.0, 101.0] * 25)  # net zero over even window
    er_chop = efficiency_ratio(chop, 10).iloc[-1]
    assert er_chop == pytest.approx(0.0)


def test_grid_target_slots_boundaries():
    assert grid_target_slots(90, 100, 200, 10) == 10   # below range → full
    assert grid_target_slots(100, 100, 200, 10) == 10  # at bottom → full
    assert grid_target_slots(200, 100, 200, 10) == 0   # at top → flat
    assert grid_target_slots(210, 100, 200, 10) == 0   # above range → flat
    assert grid_target_slots(150, 100, 200, 10) == 5   # mid → half
    assert grid_target_slots(150, 100, 100, 10) == 0   # degenerate range


# ---------------------------------------------------------------------------
# Step-function unit tests (fees, funding, hedge band)
# ---------------------------------------------------------------------------

def test_step_entry_exit_fees_exact():
    cfg = dict(price=100.0, grid_active=True, hedge_tol_qty=0.10,
               spot_fee_bp=10.0, perp_fee_bp=5.0)

    # Enter 1 unit of spot: spot fee + hedge fee (drift 1.0 > tol 0.10).
    s = hedged_grid_step(GridState(), spot_target_qty=1.0, **cfg)
    assert s.spot_qty == pytest.approx(1.0)
    assert s.spot_basis == pytest.approx(100.0)
    assert s.hedge_qty == pytest.approx(-1.0)
    expected_fees = 1.0 * 100 * 10 / 1e4 + 1.0 * 100 * 5 / 1e4  # 0.10 + 0.05
    assert s.fees_paid == pytest.approx(expected_fees)
    assert equity(s, 100.0, CAPITAL) == pytest.approx(CAPITAL - expected_fees)

    # Exit back to flat: another round of fees on both legs.
    s2 = hedged_grid_step(s, spot_target_qty=0.0, **cfg)
    assert s2.spot_qty == 0.0 and s2.hedge_qty == 0.0
    assert s2.fees_paid == pytest.approx(2 * expected_fees)


def test_step_funding_sign_for_short_hedge():
    s = GridState(spot_qty=1.0, spot_basis=100.0,
                  hedge_qty=-1.0, hedge_basis=-100.0)
    # Positive funding: short receives.
    s_pos = hedged_grid_step(s, price=100.0, spot_target_qty=1.0,
                             grid_active=False, hedge_tol_qty=0.10,
                             spot_fee_bp=10.0, perp_fee_bp=5.0,
                             funding_rate=0.0001)
    assert s_pos.funding_received == pytest.approx(1.0 * 100 * 0.0001)
    # Negative funding: short pays.
    s_neg = hedged_grid_step(s, price=100.0, spot_target_qty=1.0,
                             grid_active=False, hedge_tol_qty=0.10,
                             spot_fee_bp=10.0, perp_fee_bp=5.0,
                             funding_rate=-0.0002)
    assert s_neg.funding_received == pytest.approx(-1.0 * 100 * 0.0002)


def test_step_hedge_band_avoids_churn():
    s = GridState(spot_qty=1.0, spot_basis=100.0,
                  hedge_qty=-1.0, hedge_basis=-100.0)
    # Grid trims 5% of inventory — inside the band: no hedge trade.
    s2 = hedged_grid_step(s, price=100.0, spot_target_qty=0.95,
                          grid_active=True, hedge_tol_qty=0.10,
                          spot_fee_bp=10.0, perp_fee_bp=5.0)
    assert s2.spot_qty == pytest.approx(0.95)
    assert s2.hedge_qty == pytest.approx(-1.0)  # unchanged
    assert net_delta(s2) == pytest.approx(-0.05)
    # A 20% trim breaches the band → rebalance to new target.
    s3 = hedged_grid_step(s, price=100.0, spot_target_qty=0.80,
                          grid_active=True, hedge_tol_qty=0.10,
                          spot_fee_bp=10.0, perp_fee_bp=5.0)
    assert s3.hedge_qty == pytest.approx(-0.80)
    assert net_delta(s3) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Full-loop synthetic scenarios
# ---------------------------------------------------------------------------

def test_oscillating_market_grid_profit():
    """Sine-wave chop with the gate always open: grid harvest > fees.

    The range window must span at least one full oscillation, otherwise the
    trailing range chases price and inverts the grid — mirror of the real
    config where 30d ranges bracket multi-day swings.
    """
    n = 3000
    close = 100 + 5 * np.sin(2 * np.pi * np.arange(n) / 96)
    res = run_symbol(_bars(close), _no_funding(), _fast_cfg(range_days=6),
                     CAPITAL)
    m = res["metrics"]
    assert m["n_grid_trades"] > 20
    assert m["total_return"] > 0.0
    assert res["equity"].iloc[-1] > CAPITAL


def test_trending_market_er_gate_halts_grid():
    """Monotonic trend: ER pinned near 1 > threshold → no GRID trades.

    The hedge still rebalances (it is intentionally NOT gated — protection
    must stay on in trends), so equity stays ≈ CAPITAL: the 50% initial
    inventory's price PnL is offset by the short perp.
    """
    n = 2000
    close = 100 * 1.001 ** np.arange(n)
    res = run_symbol(_bars(close), _no_funding(),
                     _fast_cfg(er_threshold=0.3), CAPITAL)
    m = res["metrics"]
    assert m["n_grid_trades"] == 0
    # Hedge established → exposure neutralized → equity ≈ flat despite +7x trend.
    assert res["equity"].iloc[-1] == pytest.approx(CAPITAL, rel=0.05)


def test_hedge_keeps_directional_exposure_near_zero():
    """Net delta stays within the band of FULL grid capacity, and the
    hedged book carries far less directional risk than the raw inventory."""
    n = 3000
    close = 100 + 5 * np.sin(2 * np.pi * np.arange(n) / 96)
    cfg = _fast_cfg(range_days=6)
    bars = _bars(close)
    res = run_symbol(bars, _no_funding(), cfg, CAPITAL)
    tr = res["trace"]

    # Reconstruct the absolute band the way run_symbol does:
    # hedge_band × average inventory (CAPITAL × 0.5 / price, base units).
    warm_price = close[cfg.range_bars]
    avg_inv = CAPITAL * 0.5 / warm_price
    tol = cfg.hedge_band * avg_inv
    # Band containment applies once the hedge has first engaged — the
    # warm-up bars before the first rebalance legitimately carry the raw
    # initial inventory.
    engaged = tr[tr["hedge_qty"].abs() > 0]
    assert len(engaged) > 0
    assert (engaged["net_delta"].abs() <= tol * (1 + 1e-6)).all()

    # The hedge does real work: average residual delta is a small fraction
    # of the average gross inventory (unhedged would be 1.0).
    active = tr["spot_qty"].abs() > 0
    assert active.any()
    ratio = (tr["net_delta"].abs()[active].mean()
             / tr["spot_qty"].abs()[active].mean())
    assert ratio <= 0.3


def test_metrics_sanity_flat_curve():
    idx = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    flat = pd.Series(CAPITAL, index=idx)
    m = compute_metrics(flat, CAPITAL)
    assert m["total_return"] == 0.0
    assert m["max_drawdown_pct"] == 0.0
    assert m["calmar"] == float("inf")
