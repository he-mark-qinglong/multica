# T13 — Liquidity Sizing (MCLS — Multi-Cap Intersection)

## Status
**2026-07-26**: maturing → frontier-SPEC candidate. SPEC shipped at
`research/specs/liquidity_sizing_v1_20260726/SPEC.md`. Awaiting
strat-execution (L3) implementation against §5 public API contract,
strat-data (L3) real-data wiring (V0), strat-validation (L3) walk-forward
OOS §7 + V1-V7 evaluation, quant-analyst (L4) sub-cap audit + V3 Aumann
re-impl. **Not yet promoted** to SMA-30199 frontier-SPEC bucket — V-gates
must pass before promotion.

## Question
Does a **multi-cap intersection sizing primitive** (5 liquidity caps
intersected via min, composed with the existing vol-targeting layer)
produce post-cost-positive sizing infrastructure on the dual cost-cap
kill lesson (T01 + T04), where the prior single-axis sizing sweep
(SMA-34955, uniform `risk_target_pct` × 42 variants) failed
structurally?

## Prior content — decisive prior lines

### SMA-34955 sizing axis — KILLED
- **Mechanism**: uniform `risk_target_pct` parameter sweep across
  42 variants (typical sweep: 0.25% / 0.5% / 1.0% / 1.5% / 2.0% ×
  multiple strategies).
- **Result**: 0/42 pass G3 (profit factor > 1.5) on canonical window.
- **Kill reason**: structurally exhausted. The uniform-axis sweep is
  blind to depth / regime / impact / informed-flow differences across
  bars. MCLS opens the depth-axis that SMA-34955 ignored.

### T01 + T04 dual cost-cap kill (2026-07-22)
- T01 (OFI 1m retail-taker) + T04 (iceberg sub-second absorption) both
  KILLED on cost-cap at retail-taker execution on BTC aggTrades. Both
  signals had depth-axis data they ignored — neither asked "is the
  size fillable?" before pricing the edge.
- **MCLS answer**: `cap_depth` (top-of-book depth, §4.2) + `cap_part`
  (bar-volume participation, §4.3) + `cap_impact` (impact-vs-edge
  shrink, §4.4) directly answer that question at signal time.

### T06 funding-carry-asym KILL
- Out-of-content for sizing (no funding signal in MCLS), but the
  structural lesson — "small gross edge × large cost = negative net"
  — is absorbed via cap_impact (shrinkage when projected_impact >
  k_impact × expected_edge).

### T11 / T12 (SPEC candidates, in-flight)
- **T11 VPVR edge reversion**: directional signal (LVN→HVN). MCLS
  composes WITH T11 as one of the donor strategies for V7
  cross-strategy MCS matrix. NO content overlap.
- **T12 HMM regime detector**: state-detection layer. MCLS composes
  WITH T12 as one of the donor strategies for V7. Shares the
  regime-macro SKILL Aumann-falsifier methodology (applied to sizing
  decoration test in V3, regime decoration test in T12 G4). NO
  content overlap.

## Why MCLS is axis-different from everything above

5 intersection caps, each closing a distinct failure mode:

| Cap | Failure mode closed | Reference |
|-----|---------------------|-----------|
| `cap_adv` | "sized more than 24h volume justifies" | SMA-34955 implicit lesson |
| `cap_depth` | "order bigger than displayed book" (Albers dilemma) | Albers et al. 2025 |
| `cap_part` | "participation rate signals" | Kyle 1985 |
| `cap_impact` | "projected impact eats edge" | T01/T04 cost-cap |
| `cap_vpin` | "informed flow is asymmetric on this bar" | Easley-O'Hara 2012 |

Composition pattern: depth-axis (MCLS) × vol-axis (`vol_target.py`) =
both transforms optional, in order. This matches the
`_shared/sizing/README.md` opt-in library convention.

## Cycle-46 dedup

See SPEC §9 table — explicit ZERO content overlap with any killed /
archived line. SMA-34955 was the only line whose name (sizing) and
mechanism (axis sweep) overlaps this SPEC's topic; MCLS is depth-axis,
SMA-34955 was fixed-pct axis.

## 2026-07-26 measurement (this session, design-only)

This session is SPEC authoring. No backtest this session (per L2 charter:
quant-researcher owns SPEC + acceptance gates, NOT implementation). The
V-gate numbers will be produced by strat-validation in a downstream run.

## Pre-registered V-gates (V0-V7)

Different axis from G1-G7 (those are alpha gates). V-gates are sizing-
infrastructure gates.

- **V0 (structural)**: NO kline proxies anywhere. Real aggTrades + L2
  for all 5 caps. STRUCTURAL pre-condition, fails immediately on
  proxy use.
- **V1 (cost-cap)**: realized pair-RT ≤ 22bp (ratified SMA-34900 /
  SMA-34913) for ≥ 95% of fills under MCLS on BTC/ETH/SOL 90d window.
  Load-bearing gate (honors T01/T04 lesson).
- **V2 (cap honored)**: each of V2.1-V2.5 sub-gates passes.
- **V3 (Aumann-falsifier, sizing axis)**: avg-cap sizing OOS Sharpe >
  argmax-cap sizing by > 0.2 AND > flat-sizing by > 0.2 on mtf H3
  donor strategy. KILL SPEC if fails (decoration).
- **V4 (VPIN-shrink verification)**: in regimes where VPIN > k_vpin,
  MCLS sizes are ≤ 70% of vol_target-only sizes (t ≥ 2 stat sig).
- **V5 (kill-switch handoff)**: when all 5 caps < k_floor, MCLS
  returns 0 + integration test with `kill_switches/flatten_all`
  passes. KILL SPEC if fails (orphan sizing, no safety integration).
- **V6 (stale-data fallback)**: when L2 age > 60s, cap_depth = 0 and
  MCLS falls back to cap_adv-only.
- **V7 (cross-strategy MCS)**: composite MCLS+vol_target on mtf H3 +
  T11 (when shipped) gives portfolio-level MCS lift > 0.05 vs
  vol_target-only.

## Revival condition for KILL
If V1 cost-cap fails OR V3 Aumann-falsifier fails OR V2.1-V2.5 any
fails, the spec is KILLED. Reasons for KILL are recorded with
WHY in plain language. Revival requires (a) new data source not
currently used (e.g. cross-venue depth), (b) fundamentally new
datacontract (e.g. order-by-order reconstruction from full L3), or
(c) 3+ month time-decay data on a new regime (post-VIP3+ scaling,
post-new funding regime).

## Artifacts
- SPEC: `~/multica/quant-loop/research/specs/liquidity_sizing_v1_20260726/SPEC.md`
- Pre-registry metadata: SMA-35536 (this issue)
- Parent project: SMA-35467 (Risk Management)
- Promotion target (after V-gates): SMA-30199 (frontier-SPEC bucket)
- Composed-with: `_shared/sizing/vol_target.py`
- New module (after impl): `_shared/sizing/liquidity.py`
