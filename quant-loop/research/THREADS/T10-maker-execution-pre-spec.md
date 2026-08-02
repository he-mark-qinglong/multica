# T10 — Maker execution pre-SPEC (cost-≤20bps feasibility, se_h3 successor)

**Status**: exploring → pre-SPEC advisory (this session, 2026-07-26)
**Discipline**: research-journal (pre-SPEC) + execution-microstructure (data + mechanism)
**Skill context**: execution-microstructure + paper-replication + research-journal

## Question (the binding constraint)
After se_h3 KILL (SMA-36570), the SMA stat-arb family's structural cost-of-living is **20 bps pair-RT**. ANY new thin-edge stat-arb SPEC must clear this ceiling to have ANY net edge band. The current best cost floor (in-house engine 4bps pair-RT) is "marginal at +5.98 Sharpe" (se_h3 verdict), not ship-ready. T10 — originally 2026-11 in the ten-year vision — was pulled forward to 2026-07-26 to investigate whether maker execution can credibly bring pair-RT cost ≤ 20 bps on Binance USDⓈ-M perp at our current capital scale.

## Prior content (this session's measurement)
- **Fee schedule**: Binance USDⓈ-M post-March 2026, VIP0 maker +2.0bp / VIP9 maker 0bp. **No negative maker rebate until VIP9 ($4B 30d notional)**.
- **Literature**: Albers et al. 2025 (Oxford, [arXiv 2502.18625](https://arxiv.org/html/2502.18625v2)) — signal-bias-free live experiment (232,897 maker orders, 1-week Binance BTC perp, Feb 2024) — confirms:
  - Top-of-book maker fill probability front/back-of-queue ratio ~9:1
  - **Negative correlation** between fill probability and post-fill return (the "dilemma")
  - Best strategy is *contrarian* to OBI (post on shorter queue)
  - Maker P&L is bounded by fee+queue loss unless there's a private signal
- **Our own data demo**: BTC aggTrades 2026-04-19 → 2026-04-22 (3 days, 5,065,766 trades). Sweep-share 88.3%. Non-sweep markouts mildly positive (+0.05..+0.32 bp). Sweep markouts negative (-0.94..-2.33 bp) at all horizons 1s/5s/30s/5min. Volume-weighted post-fill markout proxy ≈ **−1.74 bp per fill**.

## Cost decomposition (per §4 of pre-SPEC)
| VIP | fee pair-RT | + AS proxy | + queue | = pair-RT economic cost (lower bound) |
|---|---|---|---|---|
| VIP0 | 4.0 bp | +3.5 bp | +1.5 bp | ~9.0 bp |
| VIP1 | 3.6 bp | +3.5 bp | +1.5 bp | ~8.6 bp |
| VIP3 | 2.8 bp | +3.5 bp | +1.5 bp | ~7.8 bp |
| VIP5 | 2.0 bp | +3.5 bp | +1.0 bp | ~6.5 bp |
| VIP9 | 0.0 bp | +3.5 bp | +1.0 bp | ~4.5 bp |

**In theory**, even VIP0 leaves ~9 bp pair-RT cost, well under the 20 bps break-even. So the *measurement* says "feasible". But the *execution* requires:
- A. Account scaling to VIP3+ to reduce fee component meaningfully
- B. A maker-side execution algorithm with: regime-stratified posting depth, queue priority logic, post-fill inventory unwind
- C. A regime-gate (avoid sweep-flooding sub-regimes; e.g. liquidation cascades)

## Verdict (this session)
**Recommendation to smark-decision-maker**: gate full T10 SPEC on a **1-quarter maker-pilot** (BTC-only, $100k notional, regime-stratified markout book) before committing research bandwidth. The pilot produces the proprietary measurement that NO paper provides. Full T10 SPEC is premature at this scale; defer-to-2026-11 is also wrong because the task already pulled T10 forward.

## Files
- Pre-SPEC doc: `~/multica/quant-loop/research/t10_maker_pre/pre-spec-maker-execution-2026-07-26.md`
- Data demo: `~/multica/quant-loop/research/t10_maker_pre/markout_demo.py`
- Demo output: `~/multica/quant-loop/research/t10_maker_pre/markout_summary.json` (and `/tmp/t10_maker_pre/` mirror)

## Links / references
- SMA-36598 (this issue's parent)
- SMA-36570 (se_h3 verdict) — KILL with break-even 20 bps pair-RT
- SMA-30199 (frontier-SPEC parent — T10 will register a child issue here if/when promoted)
- T01 [THREADS/T01-ofi-aggtrades.md](T01-ofi-aggtrades.md) — same cost-cap kill bucket (OFI taker-side), different mechanism
- T04 [THREADS/T04-iceberg-absorption-audit.md](T04-iceberg-absorption-audit.md) — same cost-cap kill bucket (iceberg absorption maker-side)
- T08 [THREADS/T08-vpvr-funding-hvn-lvn-confluence.md](T08-vpvr-funding-hvn-lvn-confluence.md) — different (funding-trigger dead)
- T09 [THREADS/T09-vpvr-xs-pairs-4h-cpcv-optimization.md](T09-vpvr-xs-pairs-4h-cpcv-optimization.md) — different (vpvr_xs_pairs family exhausted)

## Revival conditions (revisit if any ONE becomes true)
- (a) Pilot produces **regime-stratified markout distribution** showing ≤1.0bp adverse-selection in ≥80% of fills → open full T10 SPEC
- (b) Capital scales into VIP3+ credibly → open pilot
- (c) Binance (or alternative venue like OKX/Bybit) rolls out a **negative maker rebate** at sub-VIP9 tier → revisit fee economics
- (d) A new statistical-arb family with gross edge ≥50bp/leg emerges that can absorb higher cost → mark T10 lower priority
- (e) Defer-to-2026-11 path: if smark decides research bandwidth should remain on T07 portfolio correlation + `mtf_xs_pairs` H3 LIVE → shelve T10 with explicit revival-date

## Anti-pattern guard
- Did NOT extrapolate the demo result beyond 3 days without explicit caveat (§6 of pre-SPEC)
- Did NOT tune any param to get favorable numbers — measurement is unbiased
- Did NOT recommend opening T10 SPEC without confirming regime-stratified measurement first
- Did NOT claim "T10 done" — this is pre-SPEC, NOT a verdict
## Addendum 2026-08-02 (SMA-36939 — infra follow-on, thread remains closed)
- `maker_simulator` upgraded to true continuous market making (`mode="continuous"`): persistent inventory feeds A-S `reservation_price`, `flatten_required` drives exits; optional A-S closed-form optimal spread. 197 tests green; branch `agent/quant-researcher/sma-36939` pushed to fork. Report: `reports/maker_sim_continuous_vs_single_2026-08-02.md`.
- **New finding affecting revival condition (a)**: default γ=0.1 makes the A-S inventory shift sub-tick (and sub-ULP) at BTC scale — inventory skew is mathematically wired but physically inert; positions build to cap and exit via taker flatten (24/40 exits in 2h BTC run). Any future pilot must first solve the γ-calibration question (filed as OPEN_QUESTIONS T10.1).
- Thread status unchanged (closed 2026-07-26, no pilot). This addendum is infra-readiness, not a revival.
