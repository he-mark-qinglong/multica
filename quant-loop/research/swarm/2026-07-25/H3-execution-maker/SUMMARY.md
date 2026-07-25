# H3-execution-maker — Research Summary

Generated: 2026-07-25T01:19:51.561523+00:00

Output directory: `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-execution-maker`

## What was done
1. Reconstructed the gross H3 daily equity by reversing the 4 bps in-house baseline cost drag.
2. Swept uniform total pair round-trip cost from 0 to 60 bps and recorded Sharpe, ann_return, maxDD and profit factor.
3. Ran a vectorized post-only maker simulation on 2026 BTC+SOL aggTrades, checking price-touch and a queue-depth proxy for each leg.
4. Designed two concrete execution improvements: skip trades with unfilled entry legs, and layered limit orders at 0/+1 bps offsets.

## Key numbers
- H3 baseline in-house cost: **4 bps RT per pair trade**
- Baseline metrics: Sharpe=1.351, ann=25.0%, PF=1.216, maxDD=-13.7%
- Cost ceiling for G1 (Sharpe ≥ 1.0): **18.11 bps RT**
- Cost ceiling for G2 (ann ≥ 15%): **23.01 bps RT**
- Break-even cost (Sharpe = 0): **58.54 bps RT**
- Cost at which maxDD hits -25%: **N/A bps RT**
- Cost required for G4 PF > 1.5: **-60.34 bps RT** — negative / impossible, so execution alone cannot fix PF.

### Representative maker scenarios (offset 0 bps, 1-min patience, queue multiplier 1x)

| Maker fee (bps/side) | Effective pair RT (bps) | Leg fill rate | Interp. Sharpe |
|---------------------:|------------------------:|--------------:|---------------:|
| 0.0 | 0.78 | 65.9% | 1.432 |
| 0.5 | 2.10 | 65.9% | 1.399 |
| 1.0 | 3.42 | 65.9% | 1.366 |
| 2.0 | 6.05 | 65.9% | 1.300 |

- Best simulated maker case: Sharpe ≈ 1.434 @ 0.68 bps RT
- 'Skip unfilled entry' scheme (offset 1 bps, 2-min patience): skipped 574 of 4518 2026 trades (12.7%), effective RT ≈ 7.85 bps, trade-level PF ≈ 0.465, win rate ≈ 28.7%
- 'Layered 0+1 bps' scheme (60/40 split, maker fee 1 bps/side): effective RT ≈ 6.15 bps, mapped Sharpe ≈ 1.298

## Cost-ceiling / break-even summary
- **Comfort zone (G1+G2 both hold):** total RT cost ≤ ~18.11 bps.
- **Break-even zone (Sharpe ≈ 0):** total RT cost around **58.54 bps**.
- **Current baseline:** 4 bps RT, Sharpe ≈ 1.351 — already inside the G1/G2 comfort zone but far from G4.

## G1-G7 assessment
- Baseline G1-G4/T1 certification: **FAIL**
  - Failed gates: G4, G6, G7
- G5 (CPCV OOS) and G7 (deflated Sharpe) were not evaluated in this execution-cost study.
- G6 (bootstrap CI95 lower) was not recomputed; the reported winner value is 1.914.

## Actionable maker / queue-priority execution improvements
### Scheme A — Post-only at the touch with 1-min patience (best simple maker scheme)
Place post-only limits exactly at the signal close (offset 0 bps) and hold for 1 minute. In the 2026 sample this fills ~66% of legs; with a 1 bps/side maker fee the effective pair RT cost drops to ~3.4 bps and interpolated Sharpe rises from 1.35 to ~1.37. This is the most actionable execution upgrade because it needs no signal change and stays inside the G1/G2 cost ceiling.

### Scheme B — Skip trades where the post-only entry does not fill
Use a 1 bps offset and 2-min patience; if either entry leg fails to fill, cancel and skip the trade. In the 2026 sample this skips 574 / 4,518 trades (12.7%) and removes some adverse-selection entries, but the remaining trades still have PF ≈ 0.47, so it does not rescue G4. It is worth testing only after the signal itself is improved.

### Scheme C — Layered queue-priority limit orders (tested, not recommended)
Splitting a leg 60% at the touch and 40% at +1 bps behind was simulated; the +1 bps slice has a much lower fill rate (~51%) and its taker-fallback cost dominates, producing an effective RT cost of ~6.2 bps and Sharpe ~1.30 — worse than simply posting at the touch. Based on this data, do not use a layered offset for H3.

## Verdict: continue or KILL?
**Execution-cost improvements alone cannot make H3 SHIP-eligible.** Even with near-zero maker costs, the profit factor stays below the G4 threshold of 1.5 because the gross signal edge is only barely positive (per-trade gross PF ≈ 1.01). Maker execution raises Sharpe and annual return, but it cannot repair the weak win/loss asymmetry.

**Recommendation: KILL the H3-execution-maker track unless `signal-enhance-h3` lifts gross PF above ~1.3.** Maker execution is worthwhile but cannot close the G4 gap. If signal enhancement succeeds, the live cost ceiling (~18 bps RT for G1, ~24 bps RT for G2) is easily achievable with a simple post-only-at-touch execution.

## Next 1-2 concrete actions
1. **Hand off to signal-enhance-h3.** Target at least a 30% improvement in gross profit factor (from ~1.01 to > 1.3) through entry filtering, exit timing, or adverse-selection guards. Do not commit capital based on execution-cost savings alone.
2. **If signal enhancement succeeds, implement Scheme A** (post-only limit at the signal close, 1-min patience, ~1 bps/side maker fee) in the H3 backtest engine and rerun the full walk-forward + CPCV harness with realistic Binance maker/taker fees to certify the new cost-aware metrics.

## Files produced
- `cost_sweep.csv` / `cost_sweep.png` / `cost_sweep_maxdd.png`
- `maker_simulation.csv` / `maker_sweep.png`
- `SUMMARY.md`