"""Tests for ``_shared/validation/fee_shock.py``."""
import sys
from pathlib import Path

import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fee_shock import fee_shock_sweep  # noqa: E402


def _equity(daily_rets, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(daily_rets) + 1, freq="D")
    eq = pd.Series(1.0, index=idx).copy()
    val = 1.0
    for i, r in enumerate(daily_rets):
        val *= 1.0 + r
        eq.iloc[i + 1] = val
    return eq


def test_contract_keys_and_shape():
    eq = _equity([0.01] * 10)
    trades = [{"exit_ts": "2026-01-03"}]
    out = fee_shock_sweep(eq, trades, (4.0, 24.0))
    assert set(out) == {"4.0", "24.0"}
    for k in ("extra_round_trip_bps", "sharpe_daily_resampled", "annualized_return",
              "total_return", "max_drawdown_pct", "n_trades", "mean_daily_drag_pct"):
        assert k in out["4.0"], k
    assert out["4.0"]["extra_round_trip_bps"] == 4.0
    assert out["4.0"]["n_trades"] == 1


def test_full_notional_drag_no_fraction_scaling():
    """SMA-36566 regression: drag per trade must be bps/1e4 of NAV, NOT ×0.005."""
    eq = _equity([0.0] * 10)  # flat equity: drag is the only mover
    trades = [{"exit_ts": "2026-01-05"}]
    out = fee_shock_sweep(eq, trades, (60.0,))
    # one 60bps trade on a flat curve → total_return ≈ -0.006, not -0.006*0.005
    assert out["60.0"]["total_return"] == pytest.approx(-0.006, rel=1e-6)


def test_more_cost_never_helps():
    eq = _equity([0.01] * 30)
    trades = [{"exit_ts": f"2026-01-{d:02d}"} for d in range(3, 28, 3)]
    out = fee_shock_sweep(eq, trades, (0.0, 24.0, 60.0))
    # total_return is strictly monotone in cost tier; sharpe mostly is but
    # can wiggle with std on tiny samples — assert the robust invariant.
    assert (out["0.0"]["total_return"] > out["24.0"]["total_return"]
            > out["60.0"]["total_return"])
    assert out["0.0"]["sharpe_daily_resampled"] >= out["60.0"]["sharpe_daily_resampled"]


def test_zero_trades_is_identity():
    eq = _equity([0.01, -0.005, 0.002] * 5)
    out = fee_shock_sweep(eq, [], (60.0,))
    assert out["60.0"]["sharpe_daily_resampled"] != 0.0
    assert out["60.0"]["mean_daily_drag_pct"] == 0.0
    assert out["60.0"]["n_trades"] == 0


def test_razor_margin_strategy_dies_at_realistic_cost():
    """mean gross 17.8bps/trade must go negative-Sharpe under 24bps+ tiers."""
    # daily gross +8bps with 2 trades/day → 17.6bps/day ≈ 8.8bps/trade gross
    eq = _equity([0.0008] * 60)
    trades = ([{"exit_ts": f"2026-01-{d:02d}T08:00"} for d in range(2, 31)]
              + [{"exit_ts": f"2026-01-{d:02d}T20:00"} for d in range(2, 31)])
    out = fee_shock_sweep(eq, trades, (4.0, 24.0, 60.0))
    assert out["4.0"]["sharpe_daily_resampled"] > 0
    assert out["24.0"]["sharpe_daily_resampled"] < 0
    assert out["60.0"]["sharpe_daily_resampled"] < 0
