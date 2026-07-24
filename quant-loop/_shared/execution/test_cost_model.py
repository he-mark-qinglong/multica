"""Tests for cost_model. Plain asserts, runs as `python test_cost_model.py`.

No pytest dependency. Each test exits non-zero on failure.
"""
import os
import sys

_HERE = os.path.dirname(__file__)
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cost_model import (
    BINANCE_SPOT,
    BINANCE_FUTURES,
    apply_cost,
    cost_as_pct,
    slippage_bps,
)
from backtest.factor_backtester import (
    CostModel,
    SMA34900_FEE_BPS_PER_SIDE,
    SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE,
)


def bp(frac: float) -> float:
    """fraction -> basis points."""
    return frac * 10000.0


def test_btc_liquid_round_trip():
    # $1000 on BTCUSDT, ADV $10B — highly liquid.
    rt_pct = cost_as_pct(1000.0, 10_000_000_000.0, venue=BINANCE_SPOT, side="taker")
    rt_bp = bp(rt_pct)
    # Binance spot taker w/ BNB discount = 7.5bp/leg -> 15bp round-trip fee floor,
    # slippage negligible. Allow 10-25bp window to document realistic range.
    assert 10.0 <= rt_bp <= 25.0, f"BTC round-trip out of range: {rt_bp:.2f}bp"


def test_small_altcoin_high_cost():
    # $1000 on a $1M ADV altcoin — should produce >=30bp round-trip from impact.
    rt_pct = cost_as_pct(1000.0, 1_000_000.0, venue=BINANCE_SPOT, side="taker")
    rt_bp = bp(rt_pct)
    assert rt_bp >= 30.0, f"small altcoin round-trip too low: {rt_bp:.2f}bp"


def test_zero_notional_returns_zero():
    cost = apply_cost(0.0, 10_000_000_000.0, venue=BINANCE_SPOT)
    assert cost == 0.0, f"zero notional should be zero cost, got {cost}"


def test_slippage_caps_at_100bps():
    # participation = 1e9 / 1e6 = 1000 -> uncapped slip would be ~31600bps.
    slip = slippage_bps(1_000_000_000.0, 1_000_000.0, impact_factor=0.1)
    assert slip == 100.0, f"slippage should cap at 100bps, got {slip}"


def test_unknown_liquidity_pessimistic():
    # adv <= 0 should assume pessimistic 50bp slippage.
    slip = slippage_bps(1000.0, 0.0)
    assert slip == 50.0, f"unknown liquidity should be 50bp, got {slip}"


def test_futures_matches_ratified_baseline():
    # Futures taker round trip must equal CostModel.sma34900_baseline()
    # (4 fee + 7 pure slippage = 11 bps/side = 22 bps RT), independent of
    # notional, ADV, and impact_factor.
    baseline = CostModel.sma34900_baseline()
    assert baseline.per_side_bps == 11.0
    for notional, adv in [
        (100.0, 1_000.0),
        (1_000.0, 10_000_000_000.0),
        (1_000_000.0, 1_000_000.0),
    ]:
        rt_pct = cost_as_pct(notional, adv, venue=BINANCE_FUTURES, side="taker")
        assert abs(rt_pct - baseline.round_trip_frac) < 1e-12, (
            f"futures RT {bp(rt_pct):.4f}bp != ratified {baseline.round_trip_bps}bp "
            f"(notional={notional}, adv={adv})"
        )
    # impact_factor must not leak into the unified futures path.
    a = apply_cost(1_000.0, 1e9, venue=BINANCE_FUTURES, impact_factor=0.1)
    b = apply_cost(1_000.0, 1e9, venue=BINANCE_FUTURES, impact_factor=0.05)
    assert a == b, f"impact_factor leaked into futures path: {a} vs {b}"


def test_futures_constants_sourced_from_factor_backtester():
    # The venue must be wired to the ratified constants, not local copies.
    assert BINANCE_FUTURES.taker_fee_bps == SMA34900_FEE_BPS_PER_SIDE == 4.0
    assert (
        BINANCE_FUTURES.fixed_pure_slippage_bps
        == SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE
        == 7.0
    )


def test_futures_cheaper_than_spot_at_size():
    # At institutional size the fixed 22bp futures RT beats the spot
    # sqrt-impact path. (At tiny notionals the spot path understates cost —
    # see module docstring — so this check is size-dependent by design.)
    spot = cost_as_pct(1_000_000.0, 1_000_000_000.0, venue=BINANCE_SPOT)
    fut = cost_as_pct(1_000_000.0, 1_000_000_000.0, venue=BINANCE_FUTURES)
    assert fut < spot, f"futures ({fut:.4f}) should be cheaper than spot ({spot:.4f})"


if __name__ == "__main__":
    tests = [
        test_btc_liquid_round_trip,
        test_small_altcoin_high_cost,
        test_zero_notional_returns_zero,
        test_slippage_caps_at_100bps,
        test_unknown_liquidity_pessimistic,
        test_futures_matches_ratified_baseline,
        test_futures_constants_sourced_from_factor_backtester,
        test_futures_cheaper_than_spot_at_size,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
