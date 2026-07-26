"""Integration tests for max_position_size.py against the existing
``_shared/sizing/vol_target.py`` and ``_shared/execution/cost_model.py``.

Per SPEC SMA-35558 / SMA-36645 (Risk Mgmt #90):

  I1  max-size BLOCKS even when vol_target says OK              (seatbelt wins)
  I2  aggregate across 3 strategies on BTCUSDT                   (cross-strategy)
  I3  regime="high_vol" + DD=0.18  => DD scale still applies     (no bypass)

The tests run as a plain script (``python3 test_integration_max_position_size.py``)
AND as a pytest collection. They integrate with the established sister
modules without mutating them.
"""
from __future__ import annotations

import os
import sys

# Repo root + sibling _shared subpackages on sys.path so the established
# "bare top-level" import convention works exactly as in the existing
# test_vol_target.py / test_cost_model.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_SHARED = os.path.join(_REPO_ROOT, "_shared")
_SIZING = os.path.join(_SHARED, "sizing")
_EXECUTION = os.path.join(_SHARED, "execution")
for _p in (_HERE, _SHARED, _SIZING, _EXECUTION, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vol_target import vol_target_weights  # noqa: E402
from cost_model import apply_cost, BINANCE_FUTURES  # noqa: E402

from max_position_size import (  # noqa: E402
    MaxSizeConfig,
    PortfolioState,
    Position,
    PositionRequest,
    evaluate_max_position_size,
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
NAV = 1_000_000.0
DEFAULT_CFG = MaxSizeConfig()


def _portfolio(
    positions: list[Position] | None = None,
    drawdown_pct: float = 0.0,
    regime: str = "neutral",
    nav_usd: float = NAV,
) -> PortfolioState:
    return PortfolioState(
        nav_usd=nav_usd,
        positions=tuple(positions or []),
        drawdown_pct=drawdown_pct,
        regime=regime,
    )


results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    status = "PASS" if cond else "FAIL"
    line = f"[{status}] {name}"
    if detail and not cond:
        line += f"  // {detail}"
    print(line)


# ---------------------------------------------------------------------------
# I1 — vol_target says "OK to size up to 3x" but max_position_size BLOCKS.
# The seatbelt MUST win, regardless of what vol-targeting decided.
# ---------------------------------------------------------------------------
def test_i1_max_size_blocks_even_when_vol_target_ok() -> None:
    np.random.seed(42)
    # Realized vol artificially low → vol_target's multiplier pushes to cap.
    # Whatever vol_target says, the absolute requested notional hits the
    # 5% per-position cap here.
    sigma = 0.001
    returns = pd.Series(np.random.normal(0.0, sigma, size=200))
    weights = vol_target_weights(returns, target_vol=0.15, lookback=20, periods_per_year=365)
    post_warmup = weights.iloc[20:]
    scaling_ok = (post_warmup > 1.5).mean() > 0.5  # vol_target is leaning "scale up"

    # Apply a $60k request against a 5% cap = $50k. vol_target says fine but
    # max_position_size vetoes it.
    req = PositionRequest("s1", "BTCUSDT", "long", 60_000.0, ts="t")
    d = evaluate_max_position_size(req, _portfolio(), DEFAULT_CFG)

    check(
        "I1 vol_target says scale-up; max-size still BLOCKS the 6% request",
        scaling_ok and (not d.allow) and d.breach_kind == "position",
        detail=f"weights>=1.5 fraction={float((post_warmup > 1.5).mean()):.2f}, decision.allow={d.allow}",
    )


# ---------------------------------------------------------------------------
# I2 — aggregate across 3 strategies on BTCUSDT.
# 5% NAV each = 15% gross. The 4th request on BTCUSDT (any size) MUST be
# blocked by the symbol cap at exactly the current exposure.
# ---------------------------------------------------------------------------
def test_i2_aggregate_across_3_strategies_btcusdt() -> None:
    positions = [
        Position("strat_a", "BTCUSDT",  50_000.0, "long"),
        Position("strat_b", "BTCUSDT",  50_000.0, "long"),
        Position("strat_c", "BTCUSDT",  50_000.0, "long"),
    ]
    # Total gross BTCUSDT = 150_000 = 15% NAV == exact cap. Any new notional
    # must block by symbol.
    req_small = PositionRequest("strat_d", "BTCUSDT", "long", 1.0, ts="t")
    d_small = evaluate_max_position_size(req_small, _portfolio(positions), DEFAULT_CFG)
    check(
        "I2 3 strats each 5% BTCUSDT -> 4th request blocked by 'symbol'",
        (not d_small.allow) and d_small.breach_kind == "symbol"
        and d_small.capped_notional_usd == 0.0,
        detail=f"allow={d_small.allow}, kind={d_small.breach_kind}",
    )

    # A second confirmation: two of the three close to flat, and one new
    # request of 2% fits comfortably (gross stays <=15%).
    slim_positions = [
        Position("strat_a", "BTCUSDT", 25_000.0, "long"),
        Position("strat_b", "BTCUSDT", 25_000.0, "long"),
    ]
    req_fits = PositionRequest("strat_c", "BTCUSDT", "long", 80_000.0, ts="t")
    d_fits = evaluate_max_position_size(req_fits, _portfolio(slim_positions), DEFAULT_CFG)
    # gross after = 25+25+80 = 130K <= 150K cap -> ALLOW at the symbol axis,
    # but per-position cap = 50K, so 80K blocks by 'position' first.
    check(
        "I2 cross-strategy: 80K request blocked by 'position' (not symbol)",
        (not d_fits.allow) and d_fits.breach_kind == "position",
        detail=f"kind={d_fits.breach_kind}, capped={d_fits.capped_notional_usd}",
    )


# ---------------------------------------------------------------------------
# I3 — regime="high_vol" + DD=0.18: DD scale still applies. The regime flag
# is informational — it does NOT bypass the DD-scaled cap. With DD=0.18 we
# expect dd_mult = 1 - 0.5*(0.08/0.10) = 0.6, so a 5% request becomes
# effectively a 3% request and a 5% nominal request blocks by 'position'.
# ---------------------------------------------------------------------------
def test_i3_regime_does_not_bypass_dd_scale() -> None:
    portfolio = _portfolio(drawdown_pct=0.18, regime="high_vol")
    req = PositionRequest("s1", "BTCUSDT", "long", 50_000.0, ts="t")  # 5% nominal
    d = evaluate_max_position_size(req, portfolio, DEFAULT_CFG)

    expected_dd_mult = 0.6
    check(
        "I3 regime='high_vol' + DD=0.18 => DD scale still applied "
        "(dd_mult=0.6, 5% request blocked)",
        abs(d.dd_mult - expected_dd_mult) < 1e-9
        and (not d.allow)
        and d.breach_kind == "position"
        and d.capped_notional_usd == 0.0,
        detail=f"dd_mult={d.dd_mult}, kind={d.breach_kind}, allow={d.allow}",
    )


# ---------------------------------------------------------------------------
# Extra integration: cap-aware sizing stays inside the notional budget
# across realistic per-bar vol_target_weights. Each bar emits a proposed
# notional that vol_target says is OK at the soft cap; the hard cap then
# enforces the absolute NAV limit. This is the seatbelt's day job.
# ---------------------------------------------------------------------------
def test_seatbelt_vetoes_oversized_weighted_request() -> None:
    np.random.seed(7)
    n = 100
    sigma = 0.01
    returns = pd.Series(np.random.normal(0.0, sigma, size=n))
    weights = vol_target_weights(returns, target_vol=0.15, lookback=20, periods_per_year=365)
    # After warmup, weights can exceed 1.0 by up to 3x.

    cap_usd = 0.05 * NAV  # 5% per-position
    over_cap_count = 0
    total = 0
    for w in weights.iloc[20:]:
        total += 1
        proposed = NAV * 0.05 * float(w)  # base size scaled by vol_target weight
        req = PositionRequest("s1", "BTCUSDT", "long", proposed, ts="t")
        d = evaluate_max_position_size(req, _portfolio(drawdown_pct=0.0), DEFAULT_CFG)
        if proposed > cap_usd and d.allow:
            over_cap_count += 1
    check(
        "seatbelt: vol_target weights > 1.0 always vetoed to <= 5% NAV",
        over_cap_count == 0,
        detail=f"{over_cap_count}/{total} bars slipped past seatbelt",
    )


# ---------------------------------------------------------------------------
# Extra integration: cost-aware notional. Even after the seatbelt allows,
# the cost_model's ratified round-trip cost must remain a fraction of the
# allowed notional (no degenerate fill costs). Sanity: a $50k BINANCE_FUTURES
# order yields the 22bp round-trip baseline (matches factor_backtester's
# CostModel.sma34900_baseline()).
# ---------------------------------------------------------------------------
def test_allowed_notional_has_sane_cost() -> None:
    allowed_usd = 50_000.0
    cost_usd = apply_cost(allowed_usd, 1e12, venue=BINANCE_FUTURES, side="taker")
    rt_bps = cost_usd / allowed_usd * 10000.0
    check(
        "cost_model RT on seatbelt-allowed notional == 22bp baseline",
        abs(rt_bps - 22.0) < 1e-6,
        detail=f"rt={rt_bps:.4f}bp",
    )


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------
def main() -> list[tuple[str, bool, str]]:
    del results[:]
    test_i1_max_size_blocks_even_when_vol_target_ok()
    test_i2_aggregate_across_3_strategies_btcusdt()
    test_i3_regime_does_not_bypass_dd_scale()
    test_seatbelt_vetoes_oversized_weighted_request()
    test_allowed_notional_has_sane_cost()
    return results


def test_integration_max_position_size_checks() -> None:
    """Pytest entry — fails CI loud if I1–I3 regress."""
    res = main()
    failed = [(name, detail) for name, ok, detail in res if not ok]
    assert not failed, f"failed: {failed}\nfull: {res}"


if __name__ == "__main__":
    res = main()
    passed = sum(1 for _, ok, _ in res if ok)
    print(f"\n{passed}/{len(res)} passed")
    sys.exit(0 if passed == len(res) else 1)
