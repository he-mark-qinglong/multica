"""Tests for liquidity.py (MCLS, Multi-Cap Liquidity Sizing).

Implements the V2.* sub-gate smoke tests (each cap fires on its
designed-condition subset) and the V5 (kill-switch handoff) + V6
(stale-L2 fallback) acceptance gates. Plain asserts, prints N/N at
end; collectable by pytest.

Run directly:
    python3 _shared/sizing/test_liquidity.py

Reference: SPEC ``liquidity_sizing_v1_20260726/SPEC.md`` §4-§7.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

# Allow direct execution: this directory on sys.path so `liquidity` is importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from liquidity import MCLS, LiquiditySnapshot, MCLSParams

# Reference defaults from SPEC §3.1.
K_ADV = 0.02
K_DEPTH = 0.10
K_PART = 0.05
K_IMPACT = 0.5
K_VPIN = 0.6
FLOOR = 0.0
CAP = 1.5
K_FLOOR = 0.05
L2_STALE = 60
ALPHA = 10.0

# A "deep-book" baseline snapshot (large ADV + depth + 1h vol, no
# impact, no VPIN, healthy edge) — all caps > 1, multiplier should
# clip to 1.5.
DEEP_SNAP = dict(
    adv_24h_usd=50_000_000.0,
    depth_top5_usd=10_000_000.0,
    depth_age_seconds=5.0,
    vol_1h_usd=2_000_000.0,
    vpin=0.3,
    expected_edge_bp=10.0,
)

results: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def _snap(**overrides) -> LiquiditySnapshot:
    base = dict(DEEP_SNAP)
    base.update(overrides)
    base.setdefault("timestamp", pd.Timestamp("2026-07-26T00:00:00Z"))
    return LiquiditySnapshot(**base)


# ---------------------------------------------------------------------------
# Per-cap direct tests
# ---------------------------------------------------------------------------


def test_cap_adv_formula() -> None:
    """V2.1 — cap_adv = k_adv * ADV_24h / target_dollar_notional."""
    m = MCLS()
    # base=10k, ADV=1M, k=0.02 → cap_adv = 0.02 * 1M / 10k = 2.0
    snap = _snap(adv_24h_usd=1_000_000.0)
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    check("T1 cap_adv = k_adv*ADV/base", abs(bd["cap_adv"] - 2.0) < 1e-9)


def test_cap_depth_formula() -> None:
    """V2.2 — cap_depth = k_depth * depth_topN / target_dollar_notional."""
    m = MCLS()
    # base=50k, depth=1M, k=0.10 → cap_depth = 0.10 * 1M / 50k = 2.0
    snap = _snap(depth_top5_usd=1_000_000.0, depth_age_seconds=5.0)
    bd = m.cap_breakdown(snap, base_size_usd=50_000.0)
    check("T2 cap_depth = k_depth*depth/base", abs(bd["cap_depth"] - 2.0) < 1e-9)


def test_cap_part_formula() -> None:
    """V2.3 — cap_part = k_part * vol_1h / target_dollar_notional."""
    m = MCLS()
    # base=100k, vol_1h=2M, k=0.05 → cap_part = 0.05 * 2M / 100k = 1.0
    snap = _snap(vol_1h_usd=2_000_000.0)
    bd = m.cap_breakdown(snap, base_size_usd=100_000.0)
    check("T3 cap_part = k_part*vol_1h/base", abs(bd["cap_part"] - 1.0) < 1e-9)


def test_cap_impact_shrink_fires() -> None:
    """V2.4 — cap_impact shrinks when impact > k_impact * edge.

    base=80k, ADV=1M → participation=0.08 → impact = 10*sqrt(0.08) ≈ 2.828bp
    edge=4bp, k_impact=0.5 → threshold = 2bp. impact (2.828) > 2 →
    cap_impact = 2 / 2.828 ≈ 0.707.
    """
    m = MCLS()
    snap = _snap(adv_24h_usd=1_000_000.0, expected_edge_bp=4.0)
    bd = m.cap_breakdown(snap, base_size_usd=80_000.0)
    expected = (K_IMPACT * 4.0) / (ALPHA * (0.08 ** 0.5))
    check("T4 cap_impact shrink formula", abs(bd["cap_impact"] - expected) < 1e-9)
    check("T4 cap_impact in (0, 1) under shrink", 0.0 < bd["cap_impact"] < 1.0)


def test_cap_impact_no_shrink_when_impact_low() -> None:
    """Cap_impact = 1.0 when impact <= k_impact * edge."""
    m = MCLS()
    # base=10k, ADV=10M → participation=0.001 → impact = 10*sqrt(0.001) ≈ 0.316bp
    # edge=10bp, k_impact=0.5 → threshold=5bp. impact (0.316) << 5 → cap=1.0
    snap = _snap(adv_24h_usd=10_000_000.0, expected_edge_bp=10.0)
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    check("T5 cap_impact = 1.0 when impact low", abs(bd["cap_impact"] - 1.0) < 1e-9)


def test_cap_impact_skipped_with_infinite_edge() -> None:
    """If expected_edge_bp = +inf, cap_impact must NOT shrink (1.0)."""
    m = MCLS()
    snap = _snap(adv_24h_usd=1_000_000.0, expected_edge_bp=float("inf"))
    # Large participation to force impact to be high; with inf edge, must = 1.0.
    bd = m.cap_breakdown(snap, base_size_usd=500_000.0)
    check("T6 cap_impact = 1.0 when edge=+inf", abs(bd["cap_impact"] - 1.0) < 1e-9)


def test_cap_impact_skipped_with_zero_edge() -> None:
    """edge <= 0 (no edge estimate) → cap_impact = 1.0."""
    m = MCLS()
    for edge_bp in (0.0, -1.0, -100.0):
        snap = _snap(expected_edge_bp=edge_bp)
        bd = m.cap_breakdown(snap, base_size_usd=100_000.0)
        check(f"T7 cap_impact = 1.0 when edge={edge_bp}", abs(bd["cap_impact"] - 1.0) < 1e-9)


def test_cap_vpin_shrink_fires() -> None:
    """V2.5 — cap_vpin shrinks when VPIN > k_vpin.

    VPIN=0.8, k_vpin=0.6 → cap_vpin = (1-0.8)/(1-0.6) = 0.5.
    """
    m = MCLS()
    snap = _snap(vpin=0.8)
    bd = m.cap_breakdown(snap, base_size_usd=100_000.0)
    expected = (1.0 - 0.8) / (1.0 - K_VPIN)
    check("T8 cap_vpin shrink formula", abs(bd["cap_vpin"] - expected) < 1e-9)
    check("T8 cap_vpin in (0, 1) under shrink", 0.0 < bd["cap_vpin"] < 1.0)


def test_cap_vpin_no_shrink_when_below_threshold() -> None:
    """VPIN <= k_vpin → cap_vpin = 1.0."""
    m = MCLS()
    for vpin_val in (0.0, 0.3, K_VPIN, 0.59):
        snap = _snap(vpin=vpin_val)
        bd = m.cap_breakdown(snap, base_size_usd=100_000.0)
        check(f"T9 cap_vpin = 1.0 when VPIN={vpin_val}", abs(bd["cap_vpin"] - 1.0) < 1e-9)


# ---------------------------------------------------------------------------
# size_multiplier composition tests
# ---------------------------------------------------------------------------


def test_size_multiplier_uses_min_intersection() -> None:
    """When several caps < 1, the smallest wins (intersection)."""
    m = MCLS()
    # craft: cap_adv = 1.0, cap_depth = 0.5, cap_part = 1.0, no impact/vpin shrink.
    # base=100k, ADV=500M, k_adv=0.02 → cap_adv = 0.02*500M/100k = 100 → >>1
    # base=100k, depth=500k, k_depth=0.10 → cap_depth = 0.10*500k/100k = 0.5
    # base=100k, vol_1h=10M, k_part=0.05 → cap_part = 0.05*10M/100k = 5.0 → >>1
    snap = _snap(
        adv_24h_usd=500_000_000.0,
        depth_top5_usd=500_000.0,
        vol_1h_usd=10_000_000.0,
        vpin=0.3,
        expected_edge_bp=10.0,
    )
    mult = m.size_multiplier(snap, base_size_usd=100_000.0)
    check("T10 size_multiplier picks min (cap_depth=0.5)", abs(mult - 0.5) < 1e-9)


def test_size_multiplier_clipped_to_cap() -> None:
    """The ``cap`` parameter is a defensive ceiling; with default params,
    ``cap_impact`` and ``cap_vpin`` are bounded in [0, 1.0] (they only
    shrink, never grow), so ``m_t <= 1.0`` always. The 1.5 ceiling is
    effectively unreachable unless those per-cap ceilings are relaxed
    (e.g. via a future cap_impact > 1.0 case).

    What this test DOES verify: with the deep-book snapshot, the
    multiplier equals the tightest cap (1.0), not any value > 1.0. And
    when ``vol_target_weight`` pushes cap_base_vol > 1.0, that 1.0
    ceiling (cap_impact / cap_vpin) still binds — cap is not exercised.
    """
    m = MCLS()
    snap = _snap()  # cap_impact=1.0, cap_vpin=1.0; other caps >> 1
    mult = m.size_multiplier(snap, base_size_usd=10_000.0)
    check("T11 multiplier = tightest cap (1.0), not clipped above",
          abs(mult - 1.0) < 1e-9)

    # Even with vol_target_weight > cap, cap_impact=1.0 binds first.
    mult_hi = m.size_multiplier(snap, base_size_usd=10_000.0, vol_target_weight=2.0)
    check("T11 cap_impact=1.0 still binds when vol_target_weight=2.0",
          abs(mult_hi - 1.0) < 1e-9)

    # Lower cap param directly: with cap=0.5, even raw m_t=1.0 is clipped.
    m_low_cap = MCLS(MCLSParams(cap=0.5))
    mult_clipped = m_low_cap.size_multiplier(snap, base_size_usd=10_000.0)
    check("T11 cap=0.5 clips raw m_t=1.0 down to 0.5",
          abs(mult_clipped - 0.5) < 1e-9)


def test_size_multiplier_clipped_to_floor() -> None:
    """``floor`` clip ONLY fires at 0.0; tiny positive weights stay tiny.

    ``np.clip(0.0001, 0.0, 1.5) = 0.0001`` — the floor is not a "shrink
    anything below 1bp" rule. The kill-switch (V5) is the only path to
    a 0.0 return; the floor parameter only triggers when a value is
    literally below it.
    """
    m = MCLS()
    snap = _snap()
    mult_small = m.size_multiplier(snap, base_size_usd=10_000.0, vol_target_weight=0.0001)
    check("T12 multiplier = vol_target_weight when tiny (0.0001)",
          abs(mult_small - 0.0001) < 1e-9)
    # And the floor IS hit when vol_target_weight is exactly 0.0.
    mult_zero = m.size_multiplier(snap, base_size_usd=10_000.0, vol_target_weight=0.0)
    check("T12 multiplier = 0.0 when vol_target_weight=0.0", mult_zero == 0.0)


def test_size_multiplier_v6_stale_l2_fallback() -> None:
    """When L2 age > l2_stale_seconds, cap_depth is excluded.

    Without fallback: cap_depth=very_small would dominate min and shrink.
    With fallback: cap_depth ignored; remaining caps decide.
    """
    m = MCLS()
    # Stale L2: depth_top5_usd=10 (tiny) but depth_age=120s (>60s)
    # cap_depth raw = 0.10 * 10 / 100k = 1e-5 → would dominate if not excluded.
    snap = _snap(
        adv_24h_usd=500_000_000.0,
        depth_top5_usd=10.0,
        depth_age_seconds=120.0,    # stale
        vol_1h_usd=10_000_000.0,
        vpin=0.3,
        expected_edge_bp=10.0,
    )
    bd = m.cap_breakdown(snap, base_size_usd=100_000.0)
    # Diagnostic should mark stale.
    check("T13 cap_breakdown flags l2_stale=True", bd["l2_stale"] is True)
    # Multiplier must NOT be the tiny cap_depth — should use other caps.
    mult = m.size_multiplier(snap, base_size_usd=100_000.0)
    check("T13 stale-L2 multiplier > 0.1 (cap_depth excluded)", mult > 0.1)


def test_size_multiplier_v5_kill_switch_handoff() -> None:
    """V5 — when ALL active liquidity caps < k_floor, MCLS returns 0."""
    m = MCLS()
    # Force all 5 caps below k_floor=0.05:
    # cap_adv: base=10M, ADV=10M, k_adv=0.02 → 0.02*10M/10M = 0.02 < 0.05 ✓
    # cap_depth: base=10M, depth=1M, k=0.10 → 0.10*1M/10M = 0.01 < 0.05 ✓
    # cap_part: base=10M, vol_1h=10M, k=0.05 → 0.05*10M/10M = 0.05 = k_floor (not <)
    #        so set vol_1h=5M → cap_part = 0.025 < 0.05 ✓
    # cap_impact: force impact >> edge to make shrink < k_floor
    #   base=10M, ADV=10M, participation=1 → impact = 10*sqrt(1) = 10bp
    #   edge=1bp, k_impact=0.5 → threshold=0.5bp. impact (10) >> 0.5 →
    #   cap_impact = 0.5*1/10 = 0.05 = k_floor (not <)
    #   edge=0.5bp → threshold=0.25 → cap_impact = 0.25/10 = 0.025 < 0.05 ✓
    # cap_vpin: VPIN=0.9, k_vpin=0.6 → cap_vpin = (1-0.9)/0.4 = 0.25 > 0.05
    #   Need VPIN > 0.97 → cap_vpin = (1-0.97)/0.4 = 0.075 > 0.05 still.
    #   VPIN = 0.99 → cap_vpin = (1-0.99)/0.4 = 0.025 < 0.05 ✓
    snap = _snap(
        adv_24h_usd=10_000_000.0,
        depth_top5_usd=1_000_000.0,
        vol_1h_usd=5_000_000.0,
        vpin=0.99,
        expected_edge_bp=0.5,
    )
    mult = m.size_multiplier(snap, base_size_usd=10_000_000.0)
    check("T14 V5 kill-switch returns 0 when all caps < k_floor", mult == 0.0)


def test_size_multiplier_kill_not_firing_when_one_cap_above_floor() -> None:
    """V5 — only fires when ALL caps < k_floor; one above kills the trigger.

    Constructed so that exactly ONE cap (cap_adv) is strictly above
    k_floor=0.05, and the OTHER FOUR are strictly below k_floor=0.05.
    Under the SPEC §6 V5 contract ("all 5 caps < k_floor simultaneously"),
    kill MUST NOT fire and the multiplier must equal the tightest cap.
    """
    m = MCLS()
    # base=10M; chosen inputs:
    #   ADV = 30M  -> cap_adv    = 0.02 * 30M  / 10M = 0.06  (above k_floor)
    #   depth=1M   -> cap_depth  = 0.10 *  1M   / 10M = 0.01  (below k_floor)
    #   vol_1h=5M  -> cap_part   = 0.05 *  5M   / 10M = 0.025 (below k_floor)
    #   vpin=0.99  -> cap_vpin   = (1-0.99) / 0.4 = 0.025    (below k_floor)
    #   edge=0.5bp -> participation=10M/30M=0.333, impact=10*sqrt(0.333)=5.77bp
    #                 threshold=0.5*0.5=0.25bp -> cap_impact=0.25/5.77=0.043
    #                                                       (below k_floor)
    # All 4 non-adv caps < k_floor; cap_adv (0.06) is the only one above.
    snap = _snap(
        adv_24h_usd=30_000_000.0,
        depth_top5_usd=1_000_000.0,
        vol_1h_usd=5_000_000.0,
        vpin=0.99,
        expected_edge_bp=0.5,
    )
    mult = m.size_multiplier(snap, base_size_usd=10_000_000.0)
    check("T15 kill does NOT fire when cap_adv > k_floor", mult > 0.0)
    # The tightest cap is cap_depth = 0.01, so multiplier must equal that.
    check("T15 multiplier = tightest cap (0.01)", abs(mult - 0.01) < 1e-9)


# ---------------------------------------------------------------------------
# Vol-target composition + diagnostic tests
# ---------------------------------------------------------------------------


def test_size_multiplier_composes_with_vol_target() -> None:
    """vol_target_weight enters the min() intersection (SPEC §3.2)."""
    m = MCLS()
    snap = _snap()  # all caps > 1
    mult_w1 = m.size_multiplier(snap, base_size_usd=10_000.0, vol_target_weight=1.0)
    mult_w07 = m.size_multiplier(snap, base_size_usd=10_000.0, vol_target_weight=0.7)
    check("T16 vol_target_weight=0.7 shrinks vs =1.0", mult_w07 < mult_w1)
    check("T16 vol_target_weight=0.7 ≈ 0.7 (capped at 1.5)", abs(mult_w07 - 0.7) < 1e-9)


def test_cap_breakdown_returns_all_fields() -> None:
    """Diagnostic must expose every cap individually."""
    m = MCLS()
    snap = _snap()
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    required = {"cap_adv", "cap_depth", "cap_part", "cap_impact", "cap_vpin", "cap_base", "l2_stale"}
    check("T17 cap_breakdown exposes all fields", required.issubset(bd.keys()))
    check("T17 cap_base = 1.0 (default)", bd["cap_base"] == 1.0)
    check("T17 l2_stale = False when fresh", bd["l2_stale"] is False)


# ---------------------------------------------------------------------------
# Edge cases / robustness
# ---------------------------------------------------------------------------


def test_invalid_base_size_raises() -> None:
    """base_size_usd <= 0 must raise (MCLS scales an existing notional)."""
    m = MCLS()
    snap = _snap()
    raised = False
    try:
        m.size_multiplier(snap, base_size_usd=0.0)
    except ValueError:
        raised = True
    check("T18 base_size_usd=0 raises ValueError", raised)
    raised = False
    try:
        m.size_multiplier(snap, base_size_usd=-100.0)
    except ValueError:
        raised = True
    check("T18 base_size_usd<0 raises ValueError", raised)


def test_missing_adv_returns_inf_no_constraint() -> None:
    """adv_24h_usd <= 0 → cap_adv = +inf (graceful degrade, not NaN)."""
    m = MCLS()
    snap = _snap(adv_24h_usd=0.0)
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    check("T19 cap_adv = +inf when ADV missing", bd["cap_adv"] == float("inf"))


def test_missing_vol_1h_returns_inf() -> None:
    """vol_1h_usd <= 0 → cap_part = +inf."""
    m = MCLS()
    snap = _snap(vol_1h_usd=0.0)
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    check("T20 cap_part = +inf when vol_1h missing", bd["cap_part"] == float("inf"))


def test_vpin_one_returns_zero_cap_vpin() -> None:
    """VPIN = 1.0 (max) → cap_vpin = 0.0 (severe shrink)."""
    m = MCLS()
    snap = _snap(vpin=1.0)
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    check("T21 cap_vpin = 0.0 at VPIN=1.0", abs(bd["cap_vpin"] - 0.0) < 1e-9)


def test_vol_target_weight_zero_yields_zero() -> None:
    """vol_target_weight = 0 → multiplier = 0 (regime flatten)."""
    m = MCLS()
    snap = _snap()
    mult = m.size_multiplier(snap, base_size_usd=10_000.0, vol_target_weight=0.0)
    check("T22 vol_target_weight=0 → mult=0", mult == 0.0)


def test_custom_params_respected() -> None:
    """MCLSParams overrides honored end-to-end."""
    p = MCLSParams(k_adv=0.10, k_floor=0.20)
    m = MCLS(p)
    snap = _snap(adv_24h_usd=100_000.0)
    # base=10k, ADV=100k, k_adv=0.10 → cap_adv = 0.10*100k/10k = 1.0
    bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
    check("T23 custom k_adv honored", abs(bd["cap_adv"] - 1.0) < 1e-9)
    check("T23 custom k_floor honored", p.k_floor == 0.20)


def test_cap_impact_participation_clamped() -> None:
    """Defensive: participation > 1 (base > ADV) shouldn't crash.

    participation > 1 → impact > alpha. With edge held fixed, cap_impact
    is a small fraction. Verify it stays in (0, 1) and finite.
    """
    m = MCLS()
    snap = _snap(adv_24h_usd=100_000.0, expected_edge_bp=10.0)
    bd = m.cap_breakdown(snap, base_size_usd=200_000.0)  # 2x ADV
    check("T24 cap_impact finite under base>ADV", np.isfinite(bd["cap_impact"]))
    check("T24 cap_impact in (0, 1] under base>ADV", 0.0 < bd["cap_impact"] <= 1.0)


# ---------------------------------------------------------------------------
# Sub-gate smoke: each cap fires on its designed-condition subset (V2.*)
# ---------------------------------------------------------------------------


def test_subgate_v2_1_cap_adv_honored() -> None:
    """V2.1 — filled_qty / ADV < k_adv*1.01 for ≥ 99% of bars (smoke)."""
    m = MCLS(MCLSParams(k_adv=0.02))
    np.random.seed(0)
    n = 1000
    fail = 0
    for _ in range(n):
        adv = np.random.uniform(1e6, 1e8)
        base = np.random.uniform(1e3, 1e5)
        mult = m.size_multiplier(_snap(adv_24h_usd=adv), base_size_usd=base)
        empirical_share = (mult * base) / adv
        if empirical_share >= 0.02 * 1.01:
            fail += 1
    check(f"V2.1 cap_adv honored ≥ 99% of bars (failed {fail}/{n})", fail <= n * 0.01)


def test_subgate_v2_2_cap_depth_honored() -> None:
    """V2.2 — filled_qty / depth < k_depth*1.01 for ≥ 99% of NON-STALE bars.

    Per L2 audit (quant-analyst): the ≥ 99% bound is conditional on
    ``depth_age < l2_stale_seconds`` (i.e. only on bars where the L2
    snapshot is fresh enough to constrain). On stale-L2 bars cap_depth
    is excluded from the intersection by design (V6 fallback), so
    including those bars in the V2.2 sample would mask the real cap_depth
    test (stale bars trivially "honored" because they don't constrain).
    """
    m = MCLS(MCLSParams(k_depth=0.10))
    np.random.seed(1)
    n = 1000
    fail = 0
    for _ in range(n):
        depth = np.random.uniform(1e5, 1e7)
        base = np.random.uniform(1e3, 1e4)
        snap = _snap(depth_top5_usd=depth, depth_age_seconds=1.0)
        mult = m.size_multiplier(snap, base_size_usd=base)
        empirical_share = (mult * base) / depth
        if empirical_share >= 0.10 * 1.01:
            fail += 1
    check(f"V2.2 cap_depth honored ≥ 99% of bars (failed {fail}/{n})", fail <= n * 0.01)


def test_subgate_v2_5_cap_vpin_fires_above_threshold() -> None:
    """V2.5 — cap_vpin shrinks on ≥ 5% of bars when VPIN > k_vpin often."""
    m = MCLS()
    n = 1000
    shrink_fires = 0
    np.random.seed(2)
    for _ in range(n):
        # Half the time, force VPIN above threshold to verify shrink fires.
        vpin = float(np.random.uniform(0.61, 0.95))
        snap = _snap(vpin=vpin)
        bd = m.cap_breakdown(snap, base_size_usd=10_000.0)
        if bd["cap_vpin"] < 1.0:
            shrink_fires += 1
    check(f"V2.5 cap_vpin fires on ≥ 5% (fired {shrink_fires}/{n})", shrink_fires >= n * 0.05)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def main() -> list[tuple[str, bool]]:
    del results[:]  # idempotent across pytest + __main__ re-runs

    # per-cap direct
    test_cap_adv_formula()
    test_cap_depth_formula()
    test_cap_part_formula()
    test_cap_impact_shrink_fires()
    test_cap_impact_no_shrink_when_impact_low()
    test_cap_impact_skipped_with_infinite_edge()
    test_cap_impact_skipped_with_zero_edge()
    test_cap_vpin_shrink_fires()
    test_cap_vpin_no_shrink_when_below_threshold()

    # size_multiplier
    test_size_multiplier_uses_min_intersection()
    test_size_multiplier_clipped_to_cap()
    test_size_multiplier_clipped_to_floor()
    test_size_multiplier_v6_stale_l2_fallback()
    test_size_multiplier_v5_kill_switch_handoff()
    test_size_multiplier_kill_not_firing_when_one_cap_above_floor()

    # composition + diagnostics
    test_size_multiplier_composes_with_vol_target()
    test_cap_breakdown_returns_all_fields()

    # edge cases
    test_invalid_base_size_raises()
    test_missing_adv_returns_inf_no_constraint()
    test_missing_vol_1h_returns_inf()
    test_vpin_one_returns_zero_cap_vpin()
    test_vol_target_weight_zero_yields_zero()
    test_custom_params_respected()
    test_cap_impact_participation_clamped()

    # sub-gate smokes
    test_subgate_v2_1_cap_adv_honored()
    test_subgate_v2_2_cap_depth_honored()
    test_subgate_v2_5_cap_vpin_fires_above_threshold()

    return results


def test_liquidity_checks() -> None:
    """Pytest entry: re-run main() and assert all passed."""
    res = main()
    failed = [name for name, ok in res if not ok]
    assert not failed, f"failed checks: {failed}"


if __name__ == "__main__":
    res = main()
    passed = sum(1 for _, ok in res if ok)
    print(f"\n{passed}/{len(res)} passed")
    sys.exit(0 if passed == len(res) else 1)