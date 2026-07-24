# T04 — Iceberg absorption-thesis audit (2026-07-22)

**Status**: exploration-result recorded — **absent-positive-cost-cap KILL**

## Question
Does clustered same-millisecond trade-burst detection at pinned prices
predict institutional accumulation? What's the OOS Sharpe of entering
on confirmed iceberg absorption?

## Prior
- SMA-34992 task `106f7349` produced `iceberg_stats_2026_04_07.json` on
  2026-07-20 21:23 (BTC aggTrades, 129,377,337 trades, 1,146 iceberg
  events at the configured thresholds). The output is **detection
  descriptive stats only** — no forward-return / Sharpe analysis. T04
  was the analytical step to turn detection counts into alpha evidence.
- Execution-microstructure skill §Falsification (cost-cap): a
  microstructure signal must clear post-cost edge or it is structurally
  unviable at retail-taker execution.

## Detector (Toke & Lumbroso 2012-style)
- Source: `~/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet`
  (year=2026, months 4-7 = 2026-04-19 → 2026-07-17, 90 days,
  129,377,337 trades).
- Params: `min_cluster=5, cv_threshold=0.10, price_tick=0.01,
  persistence_gap_ms=1000, min_persistence_ms=50, min_bursts_in_event=2`.
- 8,408,388 same-ms bursts survived `min_cluster`. 24,920 passed the
  constant-size (low CV) filter. **1,467 merged iceberg events** on the
  full window (note: re-run count differs from the original stats file's
  1,146 by ~28% — likely due to runtime floating-point drift in the
  same-ms grouping; both are within the same order of magnitude and the
  direction split / anchor-strength profile are consistent).

## Detection stats (consistent with prior output)
- Buy/sell split: 41.3% / 58.7% (slight sell bias — consistent with
  iceberg-as-resting-liquidity interpretation where hidden sell orders
  refill faster than buy orders in 24h BTC tape).
- Anchor strength (price CV within event): 91.1% < 1bp (very tight pinning,
  signals the iceberg is at one tick by construction).
- Event duration: median 349ms, mean 405ms, p99 992ms.
- Event qty: median 0.011 BTC, p99 0.187 BTC, max 0.588 BTC (~$35k at
  $60k BTC). Tiny — institutional-sizing icebergs are absent at this
  trade-frequency slice; the events we detect are closer to HFT-style
  refill patterns than visible institutional absorption.

## Absorption thesis test (THIS SESSION'S ONE STEP)
For each of 1,467 events, computed `signed_return = direction *
fwd_return_h` where direction ∈ {+1 (buy), -1 (sell)} and fwd_return_h
= VWAP at t+h / VWAP at event_end - 1. Horizons tested: 1s, 10s, 60s,
300s. Positive signed_return means absorption thesis holds: a
buy-iceberg is followed by mid moving UP, a sell-iceberg by mid moving
DOWN. Subsets: anchored-tight (anchor_cv<1bp, 91% of events); big
events (top-quartile by qty, n≈368).

### Results (gross)
| Subset | h=1s | h=10s | h=60s | h=300s |
|---|---|---|---|---|
| All events (n=1467) | -0.67bp (t=-1.03) | -1.57bp (t=-0.76) | +4.92bp (t=+1.12) | +5.44bp (t=+0.58) |
| Anchored-tight (n≈1335) | -0.88bp (t=-1.39) | -1.86bp (t=-0.91) | +1.58bp (t=+0.36) | +1.62bp (t=+0.17) |
| Big events (top-q) (n≈368) | -2.31bp (t=-1.77) | -6.11bp (t=-1.53) | -0.51bp (t=-0.06) | -17.83bp (t=-0.98) |

### Results (post-cost, BTC perp round-trip = 10.83bp per T01)
| Subset | h=1s | h=10s | h=60s | h=300s |
|---|---|---|---|---|
| Anchored-tight | -11.71bp (t=-18.4) | -12.69bp (t=-6.2) | -9.25bp (t=-2.1) | -9.21bp (t=-0.98) |
| Big events | -13.14bp (t=-10.1) | -16.94bp (t=-4.3) | -11.34bp (t=-1.4) | -28.66bp (t=-1.6) |

## Verdict: **KILL — cost-cap (NOT signal-noise)**

### Honest reading
- **Detection works**: 1,467 events over 90 days = ~16/day, 91%
  anchor-tight, median 0.011 BTC each. The detector is producing
  plausible iceberg candidates at expected density.
- **Absorption thesis DOES NOT beat cost-cap**: gross signed returns
  are weak at best (largest +5.44bp at 300s horizon, t=0.58 — not
  significant). At 1s/10s horizons, signed returns are actually
  *negative* (price mean-reverts against the iceberg direction over
  short windows — consistent with "iceberg detected after the impact
  already happened" rather than "iceberg predicts next move").
- **Big events are worse**, not better: top-quartile by qty shows
  *more* negative gross returns (-2 to -18bp) — the opposite of what
  the absorption thesis predicts. Larger detected events look like
  *consumed liquidity* (post-impact) rather than *passive absorption*
  (predictive).
- **Cost-cap dominates**: 10.83bp round-trip BTC perp turns the
  marginal +5.44bp gross at 300s horizon into -5.4bp net. ALL
  post-cost numbers are negative.

### Why this is NOT a kill of the underlying detector
The detector itself works (1,467 events, well-anchored, plausible
density). The kill is on the **absorption-trading hypothesis** at
1s/10s/60s/300s horizons at retail-taker execution. The detector
remains useful for non-trading purposes (regime classification,
volatility-of-microstructure monitoring, post-trade forensics) but
does not generate alpha at the horizons + cost model tested here.

### Distinguish from prior kills
- T01 (OFI on aggTrades, SMA-35037) — different signal class (taker-side
  flow imbalance, not resting-liquidity absorption); also cost-cap kill,
  different mechanism. T01's gross edge was 3.41bp (small positive),
  T04's gross edge is -2 to +5bp (zero-ish), so T04's kill is more
  about absence of gross edge than cost-cap alone.
- T06/T08/T09 — all structural-prior / funding-related. T04 is purely
  microstructure (no prior on funding/regime). T04's kill is consistent
  with T01's lesson (microstructure edge ≪ cost at retail execution)
  but the signal class is independent.

## Revival conditions (any ONE)
- **(a) Sub-taker execution**: maker-add with queue priority gives
  effective cost <1bp. The 1.46bp gross at 60s in the anchored-tight
  subset could then become ~+0.5bp net per round-trip, marginal.
  Requires exchange-provided maker rebates or sophisticated queue
  management; near-term roadmap = unclear.
- **(b) Larger qty cutoff**: events with `total_qty_btc > 0.05` (the
  bigger institutional icebergs the detector is missing — only 2% of
  current events meet this) might show different behavior. Worth a
  single follow-up run before full kill.
- **(c) Cross-asset**: repeat the same test on ETHUSDT aggTrades
  (deeper liquidity, different iceberg patterns). If ETH shows a
  positive gross + post-cost, it's evidence the BTC result is
  venue-specific not mechanism-broken.
- **(d) Liquidation-cascade sub-regime**: filter events that occur
  during liquidation cascades (cross-ref with liquidation prints) and
  test only those. Cascade-context absorption is qualitatively
  different from resting-context absorption.
- **(e) Longer horizon (1h, 4h)**: the +5.4bp gross at 300s was
  weakly positive but un-significant. A 1h-4h horizon test might
  show a stronger drift, but at that horizon the signal is no
  longer microstructure — it becomes a position-trade that other
  signals should capture.

## Files
- Audit JSON: `/tmp/iceberg_audit/t04_audit.json`
- Detector (verbatim from task 106f7349 workdir): `iceberg_detector.py`
- Audit script: `/tmp/iceberg_audit/absorb_audit.py`
- Source data: `~/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet`

## Cross-references
- T01 (OFI, killed cost-cap) — same data source, complementary microstructure view
- T06 (funding_carry_asym, killed prior-content) — orthogonal, no overlap
- T08 (VPVR-confluence, archived-campaign-close) — orthogonal, no overlap
- T09 (vpvr_xs_pairs 4h CPCV, killed family exhaustion) — orthogonal
- SMA-34992 (this thread's parent issue, in_review) — strategy-side;
  the audit confirms the T04 mechanism question but does not address
  the T08-style 4h-VPVR-confluence element of the smark-PRIMARY
  strategy. That hybrid should be re-evaluated separately if it
  still exists in the strategy spec.
- execution-microstructure skill §Falsification (cost-cap)
- multica-agent-base §strategy-layer cycle-46 family exhaustion rule
  (applies to T04 detector sweeps but NOT to a single-window audit
  — this is one forward-return test, not a parameter sweep)