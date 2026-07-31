# SPEC — Liquidity Sizing (Multi-Cap Intersection, MCLS v1)

**Issue**: SMA-35536 — Risk Mgmt #68 — liquidity sizing
**Parent project**: SMA-35467 (MAP-P6 — Risk Management; 100 sizing/kill-switch/exposure-limit tasks)
**Frontier-SPEC bucket**: SMA-30199 (promotion target after V-gate acceptance)
**Author**: quant-researcher (78069161-efaa-493c-9561-d72a130c5926)
**Date**: 2026-07-26
**Status**: SPEC draft → awaiting strat-execution (L3) implementation + V-gate run

## 1. Purpose

Provide a **liquidity-aware position-sizing primitive** that bounds each
order's notional by the tightest available liquidity constraint on the
book at signal time, and **composes** with the existing
`_shared/sizing/vol_target.py` regime-adaptive layer (does NOT replace
it). The primitive is the *depth axis*; vol-targeting is the *vol
axis*; both must hold for capital deployment.

The rule answers: "given a strategy wants size `s`, how much of `s` can
the venue actually absorb at this bar without the marginal fill
breaking the cost-cap or signalling to the market?" This is the rule
the dual cost-cap kill pattern (T01 retail-taker OFI + T04 iceberg,
both KILLED 2026-07-22) was missing — neither line asked "is the size
even fillable?" before pricing the edge.

The verification (V3 Aumann-falsifier, below) confirms the sizing
layer is real alpha infrastructure, not decoration: on a representative
donor strategy, **average-cap sizing** must beat **argmax-cap sizing**
by > 0.2 OOS Sharpe AND beat **flat-sizing** by > 0.2 OOS Sharpe.
Otherwise the multi-cap intersection is decoration and the SPEC is
KILLED.

## 2. Prior content — must NOT retry killed lines

| Killed line | Mechanism | Why killed | MCLS differentiation |
|-------------|-----------|------------|----------------------|
| SMA-34955 sizing axis | uniform `risk_target_pct` parameter sweep (42 variants) | 0/42 pass G3, structurally exhausted | MCLS uses real L2 depth + VPIN as input (depth-axis); SMA-34955 was a fixed-pct axis-sweep |
| T01 OFI (cost-cap) | 1m taker-flow imbalance at retail-taker cost | round-trip 17.83bp spot / 10.83bp FUT ≫ +3.41bp/trade gross | MCLS assumes ratified cost-cap holds (V1); if it doesn't, KILL — not a parameter knob |
| T04 iceberg (cost-cap) | sub-second resting-liquidity absorption | post-cost all 8 horizon×subset cells negative | MCLS shares the cost-cap lesson; rule is designed to KEEP the cap valid, not lower it |
| T06 funding-carry-asym | single-asset funding z as carry | sub-gate, killed sub-portfolio | MCLS does not consume funding signal; uses funding for VPIN only (optional input) |
| T09 vpvr_xs_pairs_4h | VPVR pair-stat-arb 4h CPCV | 0/12 variants pass; cycle-46 exhausted | MCLS is sizing, not signal; cycle-46 dedup passes (no signal overlap) |
| SMA-35002 Bayesian Regime | HMM regime detector (15m, Gaussian) | sub-gate prior; 5/7 gates FAIL | distinct from MCLS (MCLS is sizing; T12 is regime detection — see spec for revival) |
| `vol_target.py` (existing, NOT killed) | vol-targeting 15% annualized, regime-adaptive | OPT-IN LIBRARY, kept | MCLS composes with vol_target; MCLS is depth-axis, vol_target is vol-axis |
| T10 maker-execution (pre-SPEC, parking) | maker+queue execution research | pilot vs VIP3+ vs defer pending | MCLS is venue-agnostic at primitive level; venue selection (taker/maker) is downstream |

**Lesson absorbed**: SMA-34955 KILL + T01/T04 dual cost-cap kill = "sizing
rules must be depth-grounded, not fixed-pct" + "cost-cap stays ratified,
do not invent cheaper fills". MCLS satisfies both.

## 3. Architecture — Multi-Cap Intersection

MCLS returns a per-bar size multiplier `m_t ∈ [floor, cap]` where:

```
m_t = min(
    cap_adv_t,         # (a) ADV-fraction cap
    cap_depth_t,       # (b) top-of-book depth cap
    cap_part_t,        # (c) bar-volume participation cap
    cap_impact_t,      # (d) impact-vs-edge shrink
    cap_vpin_t,        # (e) VPIN-aware shrink
    cap_base * vol_w,  # (f) compose with vol_target multiplier
)
```

All caps are intersection (MIN), not union — the tightest constraint
wins. Each cap is documented in §4 with its datacontract and a
falsification sub-gate.

### 3.1 Parameters (defaults, all configurable per strategy)

| Parameter | Default | Meaning | Tighten → | Loosen → |
|-----------|--------:|---------|-----------|----------|
| `k_adv` | 0.02 | max 2% of 24h ADV per fill | smaller size, lower impact | larger size, more impact |
| `k_depth` | 0.10 | max 10% of top-5 levels combined | smaller size, less book pressure | larger size, more book pressure |
| `k_part` | 0.05 | max 5% of 1h bar volume | smaller size, lower participation | larger size, higher participation |
| `k_impact` | 0.5 | if proj_impact / expected_edge > 0.5, shrink | more conservative | more aggressive |
| `k_vpin` | 0.6 | if VPIN > 0.6 (informed-trading prob), shrink | more conservative | more aggressive |
| `floor` | 0.0 | min multiplier (0 = flat when all caps zero) | rarely changed | rarely changed |
| `cap` | 1.5 | max multiplier (150% of base size) | tighter | looser |
| `k_floor` | 0.05 | if all caps < 0.05 of base → flatten (kill-switch handoff) | harder flatten | softer flatten |
| `l2_stale_seconds` | 60 | if L2 age > 60s → cap_depth contribution = 0 (force cap_adv-only fallback) | stricter | looser |

Defaults are calibrated to the ratified SMA-34900/SMA-34913 cost-cap
(22bp RT futures) on BTC/ETH/SOL. Stress corner (§7.3) doubles each
default to test sensitivity.

### 3.2 Composes with `vol_target.py` (NOT replaces)

The composition contract: a strategy's bar loop produces a baseline
equity curve, applies `vol_target.apply_vol_target` first (regime
normalization), then multiplies by `mcl.size_multiplier` (depth
normalization). Both transforms are optional, in that order, and
each can be disabled without affecting the other. This matches
`_shared/sizing/README.md` ("opt-in library, no auto-wiring").

```python
from _shared.sizing.vol_target import apply_vol_target
from _shared.sizing.liquidity import MCLS

equity_vt = apply_vol_target(equity_baseline)        # vol-axis
mcl = MCLS(adv_24h=adv, depth=topN, vol_1h=vol1h,
          vpin=vpin_series, expected_edge=edge)
m_t = mcl.size_multiplier(timestamp=t)               # depth-axis
equity_sized = apply_per_bar_multiplier(equity_vt, m_t)
```

## 4. Caps — datacontract + sub-gate

### 4.1 `cap_adv` — ADV-fraction cap

```
cap_adv_t = k_adv * ADV_24h_t / target_dollar_notional
```

**Data contract**:
- `ADV_24h_t` (USD): 24h rolling average daily volume in USD, computed
  from real aggTrades (per `execution-microstructure` SKILL — NEVER
  kline proxy). Source: `_shared/data/aggtrades_loader.py` (real,
  BTC 129M+ rows available per OPEN_QUESTIONS T01 audit).

**Sub-gate V2.1**: cap_adv honored ≥ 99% of bars on BTC/ETH/SOL
backtest window. Compute `empirical_share_t = filled_qty_t / ADV_24h_t`
and assert `empirical_share_t < k_adv * 1.01` for ≥ 99% of bars.

### 4.2 `cap_depth` — top-of-book depth cap

```
cap_depth_t = k_depth * depth_topN_t / target_dollar_notional
depth_topN_t = sum of bid qty + ask qty on top-5 price levels,
               median over rolling 60s L2 snapshots
```

**Data contract**:
- `depth_topN_t` (USD): top-5 level combined depth, 60s median of L2
  order-book snapshots. Source: Binance USDⓈ-M WS depth stream
  (`<symbol>@depth20@100ms`); N=5 levels per side.

**Sub-gate V2.2**: cap_depth honored ≥ 99% of bars. When L2 is stale
(> 60s), cap_depth contribution falls to 0 → MCLS falls back to
cap_adv-only. Sub-gate V6 (stale-data path) verifies this fallback.

### 4.3 `cap_part` — bar-volume participation cap

```
cap_part_t = k_part * vol_1h_t / target_dollar_notional
vol_1h_t (USD) = rolling 1h bar volume, aggTrades-aggregated
```

**Data contract**:
- `vol_1h_t` (USD): rolling 1h trade-volume from aggTrades. kline
  proxy is REJECTED a priori (per execution-microstructure skill,
  kline proxies were killed).

**Sub-gate V2.3**: cap_part honored ≥ 99% of bars. Note: this cap is
redundant with cap_adv at long horizons (cap_adv dominates), but
matters at sub-minute horizons where 1h bar volume ≪ 24h ADV.

### 4.4 `cap_impact` — impact-vs-edge shrink

```
expected_impact_t = alpha * sqrt(order_qty_t / ADV_24h_t)
                    # square-root impact model (Torre-Ferraris 1997,
                    # already wired in _shared/execution/cost_model.py)
if expected_impact_t > k_impact * expected_edge_t:
    cap_impact_t = k_impact * expected_edge_t / expected_impact_t
else:
    cap_impact_t = 1.0
```

**Data contract**:
- `expected_edge_t`: per-bar edge estimate from the strategy signal
  (in basis points). Required input — strategies without an edge
  estimate MUST set `expected_edge_t = ∞` (no shrink) or skip cap_impact.
- `alpha`: square-root impact coefficient. Default 10bp (matches
  Binance USDⓈ-M taker slippage empirically observed on BTC, see
  `_shared/execution/cost_model.py`).

**Sub-gate V2.4**: cap_impact shrinks on ≥ 50% of fills where
`expected_impact_t > 0.3 * expected_edge_t` (the
"significant-impact" subset). This proves the cap fires under the
condition it was designed for.

### 4.5 `cap_vpin` — informed-trading shrink

```
VPIN_t = |buy_vol - sell_vol| / total_vol over rolling N trades,
         bucketized over volume-clock (Easley-O'Hara 2012)
if VPIN_t > k_vpin:
    cap_vpin_t = (1 - VPIN_t) / (1 - k_vpin)
else:
    cap_vpin_t = 1.0
```

**Data contract**:
- `VPIN_t`: implemented via bulk-volume classification
  (Lee-Ready 1991 or bulk-classifier on aggTrades). Source: same
  aggTrades feed as cap_adv.

**Sub-gate V2.5**: cap_vpin shrinks on ≥ 5% of bars (VPIN is
event-driven, low base-rate by construction). Sub-gate V4 confirms
that in regimes where VPIN > k_vpin, MCLS sizes are measurably smaller
than vol_target-only sizes.

## 5. Public API contract

```python
# File: _shared/sizing/liquidity.py
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MCLSParams:
    k_adv: float = 0.02
    k_depth: float = 0.10
    k_part: float = 0.05
    k_impact: float = 0.5
    k_vpin: float = 0.6
    floor: float = 0.0
    cap: float = 1.5
    k_floor: float = 0.05
    l2_stale_seconds: int = 60
    impact_alpha_bp: float = 10.0   # square-root impact coefficient


@dataclass
class LiquiditySnapshot:
    """Per-bar liquidity inputs."""
    timestamp: pd.Timestamp
    adv_24h_usd: float           # §4.1
    depth_top5_usd: float        # §4.2 (0 if L2 stale)
    depth_age_seconds: float     # §4.2 (> l2_stale_seconds → fallback)
    vol_1h_usd: float            # §4.3
    vpin: float                  # §4.5
    expected_edge_bp: float      # §4.4 (set to inf to skip)


class MCLS:
    """Multi-Cap Liquidity Sizing (intersection of 5 caps + compose
    with vol_target)."""

    def __init__(self, params: MCLSParams = MCLSParams()):
        self.p = params

    def size_multiplier(self, snap: LiquiditySnapshot,
                        base_size_usd: float,
                        vol_target_weight: float = 1.0) -> float:
        """Returns per-bar multiplier in [floor, cap] (or 0 if all
        caps fall below k_floor, handoff to risk kill-switch).

        Args:
            snap: liquidity snapshot at this bar (see dataclass).
            base_size_usd: notional the strategy wants this bar.
            vol_target_weight: output of vol_target.py for this bar
                              (default 1.0 = no vol targeting).

        Returns:
            float multiplier m_t ∈ [floor, cap]; 0 if flatten trigger.
        """

    def cap_breakdown(self, snap: LiquiditySnapshot,
                      base_size_usd: float) -> dict:
        """Diagnostic — return each cap value individually for logging
        / kill-switch trigger logic. Does NOT affect size_multiplier."""
```

## 6. Pre-registered verification gates (V0-V7)

Set BEFORE any backtest result. Different from G1-G7 (those are
strategy alpha gates); V-gates are sizing-infrastructure gates.

| Gate | Definition | Failure → |
|------|------------|-----------|
| **V0** | All 5 caps' data contracts have real-data sources wired (no kline proxies anywhere) | KILL SPEC (proxy data, see T01 lesson) |
| **V1 (cost-cap)** | Realized pair-RT ≤ 22bp (ratified SMA-34900/SMA-34913 baseline) for ≥ 95% of fills under MCLS, on BTC/ETH/SOL 90d window | KILL SPEC (cost-cap violation, see T01/T04 lesson) |
| **V2 (cap honored)** | All 5 sub-gates V2.1-V2.5 pass (each cap fires on its designed-condition subset) | tighten defaults; if still fails → KILL |
| **V3 (Aumann-falsifier)** | Avg-cap sizing OOS Sharpe > argmax-cap sizing by > 0.2 AND > flat-sizing by > 0.2 on a donor strategy (e.g. mtf_xs_pairs H3 BTC/SOL OOS series) | KILL SPEC (sizing layer is decoration per regime-macro SKILL §Falsification) |
| **V4 (shrink verification)** | In regimes where VPIN > k_vpin, MCLS sizes are ≤ 70% of vol_target-only sizes (t ≥ 2 statistical significance) | KILL SPEC (cap_vpin doesn't fire) |
| **V5 (kill-switch handoff)** | When all 5 caps < k_floor simultaneously, MCLS returns 0; integration test with risk kill-switch (SMA-30199 sibling: `kill_switches/flatten_all`) passes | KILL SPEC (orphan sizing, no safety integration) |
| **V6 (stale-data path)** | When L2 age > l2_stale_seconds, cap_depth = 0 and MCLS falls back to cap_adv-only; smoke test demonstrates identical cost-cap behavior under degraded data | KILL SPEC (operationally fragile) |
| **V7 (cross-strategy MCS)** | Composite MCLS+vol_target sizing on mtf_xs_pairs H3 + T11 (when shipped) gives portfolio-level MCS lift > 0.05 vs vol_target-only, per `portfolio-risk` SKILL §Falsification | KILL SPEC (sizing doesn't help portfolio, even if standalone V3 passes) |

**V0 is a structural pre-condition, not a backtest result.** V0 fails
immediately if any data input is a kline proxy.

## 7. Walk-forward OOS protocol

### 7.1 Donor strategy
The single donor for V3 + V4 is **mtf_xs_pairs H3** (BTC/SOL, SMA-34875,
shipped 2026-07-24). Reasons:
- OOS series available (PR#6 commit `26440acd`)
- Shipped = not in a kill bucket
- Already a multi-TF signal (cycle-46 family exemption template)

### 7.2 Walk-forward windows
7 expanding windows, anchored at 2024-01 (same template as T12 spec):

| Window | Train | Test | Embargo |
|--------|-------|------|---------|
| WF1 | 2024-01 → 2024-09 | 2024-10 → 2024-12 | 24h |
| WF2 | 2024-01 → 2025-03 | 2025-01 → 2025-03 | 24h |
| WF3 | 2024-01 → 2025-06 | 2025-04 → 2025-06 | 24h |
| WF4 | 2024-01 → 2025-09 | 2025-07 → 2025-09 | 24h |
| WF5 | 2024-01 → 2025-12 | 2025-10 → 2025-12 | 24h |
| WF6 | 2024-01 → 2026-03 | 2026-01 → 2026-03 | 24h |
| WF7 | 2024-01 → 2026-06 | 2026-04 → 2026-06 | 24h |

### 7.3 Stress corners (cost-cap defense)
- **Baseline**: BINANCE_FUTURES 22bp RT (SMA-34900 ratified).
- **Stress 1**: 35bp RT (50% cost increase, validates cap_impact fires).
- **Stress 2**: VIP0 9bp floor (T10 premise, validates cap_adv still
  honored under optimistic cost).

### 7.4 Cross-strategy MCS matrix (V7)
For each pair from {mtf_xs_pairs H3, T11 VPVR edge reversion (when shipped),
T12 HMM regime (when shipped)}, compute:
- Sharpe(strategy alone)
- Sharpe(strategy + vol_target)
- Sharpe(strategy + vol_target + MCLS)
- MCS lift = last − first.

Pass if MCS lift > 0.05 (per `portfolio-risk` SKILL falsification rule).
If only 1 strategy is shipped at run-time, V7 is documented as
"deferred until 2nd sibling ships" rather than failed — single-line
V7 doesn't test diversification.

## 8. Implementation ownership

- **strat-execution** (L3): `_shared/sizing/liquidity.py` + unit tests
  + README per §5. Inherits `_shared/sizing/README.md` opt-in pattern
  from `vol_target.py`.
- **strat-data** (L3): aggTrades loader + L2 depth snapshotter for
  V0 + V6 (real-data sources, NO kline proxy).
- **strat-validation** (L3): runs walk-forward OOS protocol §7;
  reports V0-V7 with concrete numbers in the EVIDENCE comment.
- **quant-analyst** (L4): independent audit of cap sub-gates V2.1-V2.5
  (do caps actually fire where designed?) + V3 Aumann-falsifier
  re-implementation.
- **smark-signoff-proxy** (L4): evidence-chain sign-off before V7
  portfolio-level claim can ship to multica-code integration.

## 9. Cycle-46 dedup (explicit)

| Killed / archived | Mechanism | MCLS overlap |
|-------------------|-----------|--------------|
| T01 OFI | 1m taker-flow imbalance | NONE — MCLS is sizing, not signal |
| T04 iceberg | sub-second absorption | NONE — MCLS is sizing; shares cost-cap lesson |
| T06 funding-carry-asym | single-asset funding z as carry | NONE — MCLS does not consume funding signal |
| T08 VPVR-confluence | VPVR + funding>0.03% trigger | NONE — MCLS is sizing; trigger-dead status unrelated |
| T09 vpvr_xs_pairs_4h | VPVR pair-stat-arb 4h | NONE — MCLS is sizing, not pair signal |
| SMA-34955 sizing axis | uniform risk_target_pct sweep | DISTINCT — MCLS is depth-axis, SMA-34955 was fixed-pct axis |
| SMA-35002 Bayesian Regime | HMM regime detector (15m, Gaussian) | DISTINCT — T12 revival is regime layer; MCLS is sizing layer |
| T11 VPVR edge reversion (SPEC candidate) | LVN/HVN + 1d TTL directional signal | NONE — MCLS composes WITH T11 as one of the donor strategies for V7 |
| T12 HMM regime detector (SPEC candidate) | 4h regime state detection | NONE — MCLS composes WITH T12 as one of the donor strategies for V7 |

## 10. Risk + reversibility

- **Capital at risk during validation**: zero (backtest + paper only).
  Real-money wiring is downstream of V7 sign-off.
- **Reversibility**: full — sizing is opt-in, no auto-wiring per
  `_shared/sizing/README.md`. A strategy that adopts MCLS can revert
  by deleting the `size_multiplier` call.
- **Operational fragility** (V6): L2 stream outage triggers fallback.
  Outage monitoring is the responsibility of the connector, not MCLS.

## 11. Out of scope (v1)

- **Maker-add queue priority** (T10 territory) — MCLS assumes taker
  execution cost; maker economics is the T10 pre-SPEC's job.
- **Cross-venue smart routing** — single venue per bar, per existing
  `_shared/execution/cost_model.py` pattern.
- **Adaptive parameter learning** — k_adv / k_depth / k_part / k_vpin
  are fixed at strategy level. Adaptive sizing is a separate research
  thread (would require its own cycle-46 family dedup table).
- **Real-money integration** — connector handles that (per
  `SPEC_live_paper_connector_binance_usdm` v1 contract).

## 12. References

- **Torre & Ferraris 1997** — square-root market impact (used in cap_impact)
- **Easley, Kiefer, O'Hara 1997** / **Easley, López de Prado, O'Hara 2012** —
  VPIN measurement (used in cap_vpin)
- **Lee-Ready 1991** — bulk-volume classifier (used in VPIN computation)
- **Almgren-Chriss 2000** — optimal execution framing (background; MCLS is
  not a VWAP/twap schedule, but the impact model uses the same sqrt-impact
  family)
- **Bessembinder 2003** — quote-size as depth proxy
- **Kyle 1985** — informed-trading probability background
- **`execution-microstructure` SKILL** — real aggTrades requirement, NOT
  kline proxies; cost-cap falsification rule
- **`regime-macro` SKILL** — Aumann-falsifier, regime-conditional sizing
- **`portfolio-risk` SKILL** — MCS ≥ 0.05 falsification rule for V7
- **`paper-replication` SKILL** — walk-forward OOS protocol template
- **`_shared/sizing/vol_target.py`** — composed-with, NOT replaced
- **`_shared/sizing/README.md`** — opt-in library convention
- **`_shared/execution/cost_model.py`** — ratified 22bp RT BINANCE_FUTURES
- **SMA-34900 / SMA-34913** — cost-cap ratification
- **SMA-34955** — killed sizing axis (uniform risk_target_pct sweep)
  — load-bearing prior content (DON'T retry)
- **SMA-34875** — mtf_xs_pairs H3 (donor strategy for V3/V4)
- **SMA-36598** — T10 maker-execution (VIP0 9bp premise, NOT this SPEC)
- **SMA-30199** — frontier-SPEC bucket (promotion target after V-gates)
- **SMA-35467** — parent project Risk Management (one of 100 sizing/
  kill-switch/exposure-limit tasks)
- **multica-agent-base §strategy-layer** — G1-G7 strategy-alpha gates,
  cycle-46 family-exhaustion rule, opt-in library convention
- **multica-agent-base §Result Wire** — V0-V7 reported as EVIDENCE comment
  with concrete numbers, not vibes