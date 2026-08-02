# Open Questions — Quant Research Backlog

> Ranked. Researcher picks highest-priority not-currently-advanced each session.
> Status: exploring / maturing / shelved / killed / shipped

## P0 — PRIMARY axis (tape-reading / large-order / microstructure)

### T01 — OFI on real aggTrades (revivable)
- **Status**: killed (2026-07-20) — gross signal real (+3.41bp top-bot quintile spread, corr +0.21) but cost-cap fails (17.83bp SPOT round-trip ≫ 3.41bp edge). 0/90 cells pass G1 post-cost.
- **Question**: Does Cont-Kukanov-Stoikov OFI predict next-horizon drift on BTC/ETH/SOL perp with REAL trade-level data (not kline proxy)?
- **Prior**: SMA-34997 v1 KILL — kline proxy can't capture same-ms trade bursts. v1 had no chance.
- **v2 result** (SMA-35037): 1m BTC bars 2026-04-19 → 2026-06-30 (105k bars). Mechanism confirmed (corr +0.21, top-bot spread +3.41bp/trade) but trading it as a taker loses money. CPCV mean OOS Sharpe = −34 ± 5.
- **Kill reason**: taker round-trip cost (17.83bp SPOT, 10.83bp FUTURES) exceeds per-trade edge by 3-5x. Net of cost the signal is structurally negative at 1m horizon.
- **Revival conditions**: (a) sub-taker execution (maker+queue, eff cost <1bp); (b) T04/SMA-34992 iceberg-confluence pushing per-trade edge >20bp; (c) liquidation-cascade sub-regime only; (d) fundamentally stronger signal at higher horizon.
- **Threads**: see `THREADS/T01-ofi-aggtrades.md` for full sweep + verdict.
- **Links**: T04 (iceberg), T05 (regime-conditional flow).

### T08 — VPVR-confluence (HVN entry + funding gate + LVN exit) — new prior, regime-conditional — **CAMPAIGN CLOSED 2026-07-22**
- **Status**: archived-campaign-close (2026-07-22) — SMA-34901 closed as research-complete-hold per swarm主agent Path (A) decision (comment id `fda358f8-1d82-4e3c-bbc1-2a1c77d26733`). Issue status = `done`. T08 thread file updated. `results-ledger.md` line 9 unchanged (PASS / HOLD-for-promotion / 3 follow-ups — distinguishes issue-completion from framework-ship-readiness).
- **NOT a kill** — in-sample edge (Sharpe_d 1.053, ann +26.67%, PF 1.544, n=49) is real on the only regime where the trigger fires. The campaign closes because the **funding>0.03% trigger is structurally dead in current data** (May 2024 → present, 18 months zero events), NOT because the signal's structure is wrong.
- **Question**: Does a structural-positional long-only crypto-perp signal — enter at HVN support when funding>0.03%, exit at next LVN above — produce framework-shippable alpha on a regime-conditional basis?
- **Prior content (new — not T06)**: funding-as-timing-filter (NOT funding-as-carry). T06 was long-pays-carry (negative carry cost, killed on negative Sharpe). This is long-at-max-carry-pulse (still negative carry cost but bounded by 24h exit). Different prior, different signal class.
- **2026-07-20 audit verdict (this session, quant-researcher)**: shipped in metrics.json line 9 of results-ledger.md; PASS on combined-gate eval; HOLD-for-promotion per three blockers:
  - **Gate 1 (2025 cold-regime OOS) STRUCTURALLY UNMET**: funding parquet extends to 2026-07-17 (clean) but BTC/ETH/SOL max funding in 2025 = 0.0001/0.0001/0.000259 vs threshold 0.0003. **Zero `funding>0.03%` events for 18 months straight** (May 2024 → Jul 2026). The signal cannot fire by trigger construction. This is regime shift, not data gap.
  - **Gate 2 (LVN-exit validation) STRUCTURALLY UNMET**: of 43 trades with target_lvn_price, only 5 (11.6%) actually hit `high ≥ target_lvn.price_high` within 96 bars. Plus 6 trades had no target LVN above entry. 44/49 = 89.8% time_stop exits. The exit thesis is essentially "funding>0.03% + HVN proximity + 24h drift up", not the structural LVN target the spec was written against.
  - **Gate 3 (≥3 expanding OOS windows) UNMET by Gate 1**: regime-gated signals cannot satisfy expanding-OOS-into-cold when the regime has been absent for 18 months.
- **Per-symbol Sharpe<1.0**: BTC 0.84, ETH 0.89, SOL 0.31. Combined 1.053 only via cross-symbol variance reduction. SOL is structural laggard (median pnl_pct = -0.20%). 17/49 (35%) in Mar 2024 alone; only 7 active months in 14-month window.
- **Recommendation (now terminal)**: archived-campaign-close. Future work on this signal class must satisfy cycle-46 family exhaustion rule (multica-agent-base §strategy-layer) — NOT parameter sweep on the same trigger. Successor spec requires different trigger AND likely different signal class.
- **Revival condition** (archived — any ONE):
  - (a) Funding regime persistence returns — any month with all 3 symbols registering ≥1 `funding>0.03%` event reopens Gate 3 (current absence is 18+ months and growing).
  - (b) Lower `proximity_atr` threshold so target_lvn is closer to entry and reachable in 96 bars — cheapest way to test Gate 2, but does not address Gate 1.
  - (c) Sub-threshold funding entry (≥0.02%) — risks prior-content validity drift AND changes the signal definition (a new issue / new campaign, not revival of T08).
- **Threads**: `THREADS/T08-vpvr-funding-hvn-lvn-confluence.md` (status header flipped to archived-campaign-close 2026-07-22).
- **2026-07-21 swarm-owner validation**: VPPR swarm-owner (decision comment id `136d0e51-d395-4709-8908-09dfaf8e17ae`) audited the full thread and confirmed T08's three-HOLD-gate finding. Recommended Path (A) close-as-research-complete-hold OR Path (B) open trigger-modified successor spec.
- **2026-07-22 swarm主agent Path (A) decision**: swarm主agent (decision comment id `fda358f8-1d82-4e3c-bbc1-2a1c77d26733`) acted on the swarm-owner recommendation and chose Path (A). Justification: honest negative result should be rewarded per multica culture, not left rotting in `in_review`. SMA-34901 closed; T08 archived; next VPPR campaign iteration goes on a **non-funding-carry axis**.
- **Campaign state (terminal)**: SMA-34901 was the **last open issue in d1f4d321** (16 done + 4 cancelled + 1 in_review = 21 total, all closed). VPPR Campaign (project d1f4d321) is now fully closed.
- **Links**: SMA-34901 (closed 2026-07-22, status=done), SMA-34733 (VPPR variant-sweep family root, same-project — campaign fully closed), SMA-34652 (cross-project family root), SMA-34990 (T06 ancestor funding_carry_asym V2 NOT-PROFITABLE), SMA-34787 (Sharpe daily-resampled audit), SMA-34924 (VERDICT-block convention), SMA-35167 (T09 sibling — different family, killed 2026-07-21), T06 (killed — different prior content).

### T04 — Iceberg detection efficacy — KILL (2026-07-22)
- **Status**: killed (2026-07-22) — absorption-trading hypothesis fails cost-cap at retail-taker execution on BTC aggTrades 2026-04-19 → 2026-07-17 (129M trades).
- **Question**: Does clustered same-ms trade-burst detection at pinned prices predict institutional accumulation? What's the OOS Sharpe of entering on confirmed iceberg absorption?
- **Detector (Toke & Lumbroso 2012-style) re-run** (this session, quant-researcher): 1,467 merged iceberg events on the 90-day BTC aggTrades window. Buy/sell split 41/59. 91% anchor-tight (price CV<1bp). Median event qty 0.011 BTC, p99 0.187 BTC. Density ~16 events/day.
- **Forward-return audit (signed = direction × fwd_return_h)**: at h ∈ {1s, 10s, 60s, 300s}, gross signed mean ranges -1.86bp → +5.44bp. At 1s/10s horizons the SIGNED return is NEGATIVE (-0.7 to -1.9bp, t ≈ -1) — short-horizon price mean-reverts against the iceberg direction. 60s/300s weakly positive (t<1.2) but t-stats are sub-significant.
- **Post-cost (BTC perp 10.83bp round-trip per T01)**: ALL 8 horizon×subset cells net NEGATIVE. Best case: anchored-tight at 300s horizon = -9.21bp net. Worst case: big-events at 300s = -28.66bp net. Cost-cap dominates marginal gross edge at every horizon.
- **Honest interpretation**: short-horizon *negative* signed returns are consistent with "iceberg detected AFTER the impact already happened" (consumed-liquidity pattern) rather than "iceberg predicts next move" (passive-absorption pattern). Big events (top-quartile by qty) are *more* negative post-impact, not less — opposite of absorption thesis. The detector itself works but the trading hypothesis it implies is wrong at retail-taker execution on this BTC tape.
- **Kill reason**: cost-cap. Distinct from T01 mechanism (T01 = taker-flow imbalance, T04 = resting-liquidity absorption) — separate kill bucket, both cost-cap. Per execution-microstructure skill §Falsification (cost-cap): a microstructure signal must clear post-cost edge at the tested execution venue to be ship-eligible. T04 fails.
- **Revival conditions** (any ONE):
  - (a) Sub-taker execution (maker-add with queue priority, eff cost <1bp) — would turn the marginal +1.6 to +5.4bp gross into net positive at 60-300s horizons.
  - (b) Larger qty cutoff (`total_qty_btc > 0.05`, only ~2% of current events) — re-run before full kill on this dimension.
  - (c) Cross-asset (ETHUSDT aggTrades, different iceberg patterns) — if ETH shows positive gross + post-cost, the BTC result is venue-specific not mechanism-broken.
  - (d) Liquidation-cascade sub-regime filter — cascade-context absorption is qualitatively different from resting-context.
  - (e) Longer horizon (1h-4h) — but at that horizon the signal is no longer microstructure; other signals should capture.
- **Detector itself remains useful** for non-trading purposes (regime classification, microstructure vol monitoring, post-trade forensics). Kill is on absorption-trading hypothesis, NOT on the detector as a research instrument.
- **Threads**: `THREADS/T04-iceberg-absorption-audit.md` (full audit + per-horizon table + revival conditions).
- **Audit artifacts**: `/tmp/iceberg_audit/t04_audit.json` (this session's audit JSON), `/tmp/iceberg_audit/absorb_audit.py` (audit script). Source: `~/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet` (129M trades, 2026-04-19 → 2026-07-17). Detector: `iceberg_detector.py` from task 106f7349 workdir.
- **Links**: SMA-34992 (parent strategy-pivot issue, in_review), SMA-35037 (T01 OFI, sibling cost-cap kill), execution-microstructure skill §Falsification, multica-agent-base §strategy-layer cycle-46 family exhaustion rule.

### T09 — vpvr_xs_pairs_4h_zscore_vpvr_20260710 CPCV param search — KILL (2026-07-21)
- **Status**: killed (2026-07-21) — 0/12 pre-registered CPCV variants cleared acceptance gates; structural negative-fold pattern across the entire parameter space.
- **Question**: Can a Cartesian parameter sweep over (zscore_entry, vpvr_poc_attractor_strength, vpvr_hvn_threshold, exit_zscore, cost_bps_total) under strict CPCV walk-forward lift the 4h `xs_pair_zscore_with_vpvr_confluence` strategy above the CPCV acceptance gate (mean OOS Sharpe ≥ 0.5, worst-fold ≥ 0.0, DSR > 0)?
- **Pre-registered candidate set**: 12 variants (two waves, all chosen a priori on economic reasoning before any results inspected). Spans the most-impactful axes (tight entry, structural filter stacking, cost stress corners).
- **Result**: best real-cost variant (#10, cost40 stress) mean=+0.5001, worst-fold=-1.084 — brushes mean≥0.5 but worst-fold is 1.08 below the ≥0.0 floor. Forbidden-optimistic cost20 variant (#4): mean=+0.466, worst=-1.035. Per-variant mean Sharpe range [-0.58, +0.50]. DSR is positive on 9/12 (edge exists, just insufficient).
- **Kill reason**: (a) **structural negative-fold pattern** — every one of 12 variants has at least one OOS fold with Sharpe in [-2.84, -1.03]; (b) consistent with prior 4h family kills SMA-33997 V12/V13/V14 (PF 0.31 / 0.08 / 0.086); (c) cycle-46 family exhaustion rule (multica-agent-base §strategy-layer) applies — "one rebuild per closed family requires asymmetric execution / multi-TF confirmation, not just parameter sweep" — this attempt satisfies neither; (d) only viable sibling is `mtf_xs_pairs_funding_regime` H3 (BTC/SOL Sharpe 2.77, SMA-34875), which is multi-TF + funding-regime, NOT this 4h z-score+VPVR axis.
- **Methodology correction picked up**: `run_backtest.py:50` Sharpe now uses correct `mu/sigma*ann` (post-SMA-34922 fix). Baseline metrics.json rewritten: Sharpe=**0.334** (avg of BTC/ETH=0.282, BTC/SOL=0.568, ETH/SOL=0.151 — BTC/SOL stands out, consistent with mtf H3 finding). Both pre-fix 0.23 and post-fix 0.33 FAIL Sharpe≥0.5.
- **Task spec endorsement**: "If acceptance gate can't be met after exploring the full space, post [type=KILL]" — KILL is the intended result here.
- **Revival conditions**: (a) multi-TF confirmation (combine 4h z-score entry with 1m/15m micro-confirmation); (b) asymmetric execution (maker-add at entry / taker-flat at exit); (c) regime gate (restrict to high-vol-of-vol windows to skip the bad-folds); (d) fundamentally different signal class — 4h single-TF pair-stat-arb appears structurally thin in this 2.18y window.
- **Threads**: `THREADS/T09-vpvr-xs-pairs-4h-cpcv-optimization.md` (full audit + per-variant OOS + anti-overfit notes + revival conditions).
- **Links**: SMA-35167 (this task) + SMA-33997 (V12/V13/V14 4h family kills) + SMA-34875 (mtf H3 viable sibling) + SMA-34922/SMA-34980 (methodology fixes) + `_shared/validation/cpcv.py` (CPCV harness) + multica-agent-base §strategy-layer (cycle-46 exhaustion rule).

## P1 — frontier research threads

### T02 — MFG crowding capacity (SMA-35005 DEFER)
- **Status**: maturing (pre-SPEC analytical DEFER — needs deeper prior content)
- **Question**: Can Lasry-Lions mean-field-game model estimate strategy crowding/capacity from observable order-flow concentration?
- **Prior**: SMA-35005 review concluded the framing is sound but prior content (VPVR+funding) is sub-gate, same failure mode as Bayesian 35002.
- **Next**: derive what observable would indicate crowding WITHOUT relying on VPVR/funding prior. Maybe OI concentration + taker absorption ratio.
- **Links**: T04 (iceberg), portfolio-risk skill.

### T03 — Transfer Entropy between venues (SMA-35001)
- **Status**: exploring (in todo, strategy-worker-1)
- **Question**: Is there directed information flow BTC perp → ETH perp / alts that predicts cross-asset drift?
- **Prior**: fresh thread, Schreiber 2000 methodology.
- **Next**: compute transfer-entropy matrix on 1m returns, check if any direction is significant after multiple-testing correction.
- **Links**: T01 (OFI — info flow IS order flow).

## P2 — structural / portfolio

### T06 — Why did funding-carry-asym prior content fail?
- **Status**: killed (5/6 gate FAIL, Bayesian wrapper also failed; re-confirmed KILL post max_dd fix 2026-07-18)
- **Question**: Is there a TRANSFORM of funding+VPVR that recovers alpha, or is the content fundamentally sub-gate on crypto perp?
- **Prior**: SMA-34990 V2 NOT-PROFITABLE, SMA-35002 Bayesian 5/7 gates FAIL.
- **2026-07-18 max_dd fix verification**: SMA-34922 (multica-code) shipped daily-resampled portfolio-NAV path; framework max_dd no longer emits the `-4.0e-06` sizing artefact. [SMA-34927](https://multica/issue/e511d7c9-2258-479b-b9a3-22b8f4583595) re-judged iter#82 (`vpvr_funding_aware_v1`) under corrected max_dd. smark-proxy verdict at 2026-07-18T17:09:08+08: **KILL** (Sharpe 0.74 daily-resamp < G1; maxDD -43.07% > G3; ann passes). iter#82 ledger row stays KILLED; U2 cleared.
- **Kill reason**: prior content sub-gate; Bayesian framing can't compensate. The methodology artefact (max_dd near-zero) was hiding real G1/G3 gate failures under single-metric W5 archive — corrected methodology confirms the prior-content kill, does not invalidate it.
- **Revival condition**: only if a NEW prior source is identified (not funding/VPVR). Methodology fix (SMA-34922) does NOT count as new prior content.
- **Links**: T02 (MFG needs different prior too), execution-microstructure skill.

### T07 — Are our 5 strategy lines actually diversified?
- **Status**: exploring
- **Question**: 34991/34992/34997/35001/35012 — do their OOS return series have correlation < 0.7, or are they 1 bet in 5 disguises?
- **Prior**: portfolio-risk skill defines the test. No portfolio-level analysis done yet.
- **Next**: collect each strategy's OOS PnL series, compute correlation matrix, identify common factors.
- **Links**: portfolio-risk skill, all other threads.

### V01 — CPCV harness correctness (validation infra, not a strategy thread)
- **Status**: FIXED 2026-08-02 (SMA-36935, depth-review P0-1) — per-segment purge + AFML-correct embargo shipped on branch `agent/quant-researcher/sma-36661`; leak-oracle regression proves purge_bars≥h leaves zero fake edge (16/16 test_cpcv green).
- **Open question (standing)**: which historical near-gate OOS verdicts flip under the fixed harness? The old bugs biased OOS Sharpe UP, so all KILLs only get stronger — but any PASS / WITHIN_TOLERANCE within ~±0.3 Sharpe of a gate inherited unknown positive bias and must be re-run before being used as ship evidence.
- **Rule going forward**: no strategy sign-off on CPCV numbers produced before this fix; near-gate candidates re-run CPCV under the fixed harness first.
- **Links**: SMA-36935 + JOURNAL 2026-08-02 entry + T09 (SMA-35167, largest CPCV-sweep consumer) + `_shared/validation/cpcv.py`.

## Strategic Decision 2026-07-24 (quant-loop cleanup + HF pivot, main session)

- **Workspace restructured per PLAN_20260724_hf_strategy_optimization.md**. 123 strategy directories audited; 52 dead variants moved to `strategies/_graveyard/` with KILL_SUMMARY.md per family; 34 ghost directories deleted; paper_trading archived. Active strategies reduced to ~15.
- **Infrastructure unified**: single engine (`_shared/run_backtest.py`, vectorized 7.1x), single metrics (`compute_metrics.py`), single gates (DSR replaces Bonferroni), single cost (`factor_backtester.CostModel` ratified 22bps RT). Tests 178 passed.
- **High-frequency verdict confirmed**: 1m/5m klines price-reversal strategies are structurally dead on cost-cap (T01+T04). Only unproven HF axis is real aggTrades order flow (loid_iceberg_v4). Proven template is multi-TF + regime gate (mtf_xs_pairs H3).
- **New pipeline shipped**: strategy contract v2 (`_shared/templates/`), pre-registered CPCV template, generic framework CV (`validation/generic_harness.py`), funding/maker-taker cost extension.
- **results-ledger.md established** as top-level verdict tracker (99 strategies).
- **Next**: complete loid_iceberg_v4 90d parameter scan; evaluate H3 variants with unified pipeline; consider T10 sub-taker execution research.

## Killed (do not retry without new info)
- Bayesian Regime Posterior (SMA-35002): 5/7 pre-SPEC gates FAIL, Aumann-falsifier FAIL. Prior content sub-gate.
- funding-carry-asym V1/V2 (SMA-34990): NOT-PROFITABLE on canonical window.
- sizing axis (SMA-34955): 0/42 variants pass G3, structurally exhausted.
- vpvr_xs_pairs_4h_zscore_vpvr_20260710 CPCV (T09, SMA-35167): 0/12 variants pass acceptance gates; structural negative-fold pattern. Cycle-46 family exhaustion closed the `vpvr_xs_pairs` axis for parameter-sweep rebuilds. Revival requires asymmetric execution / multi-TF confirmation.
- VPPR-confluence HVN entry → LVN exit (T08, SMA-34901 archived-campaign-close 2026-07-22): funding>0.03% trigger structurally dead 18mo; geometry kill on next-day mean-reversion (symmetric to T11).
- VPVR edge-limit reversion (T11, SMA-36615 KILLED 2026-07-26, +1d shift correction): 9/9 cells (sym × horizon) negative on Binance perp kline proxy. Cycle-46 closed the `vpvr_edge_reversion` family on vanilla kline proxy. Revival requires tick-level profile + OFI-augmented signal + cascade sub-regime + regime gate + stronger cost model.


## Strategic Decision 2026-07-19
- VPVR 单资产回归族 KILLED（14/14 fail, avg Sharpe -2.04）
- 主力：跨品种配对 walk-forward OOS（EPIC SMA-35036）
- 探索：OFI on aggTrades（SMA-35037）→ **KILLED 2026-07-20 (cost-cap, not signal-noise)**
- defer：MFG / Schelling / Bandit / Causal Gate


## Strategic Decision 2026-07-19
- VPVR reversion KILLED (14/14 fail, avg Sharpe -2.04)
- PRIMARY: pairs walk-forward OOS (EPIC SMA-35036)
- EXPLORE: OFI on aggTrades (SMA-35037)
- DEFER: MFG / Schelling / Bandit / Causal

## Strategic Decision 2026-07-20 (research audit, this session)
- T01 OFI v2 KILLED (cost-cap) — budget closed on that prior content
- T08 VPVR-confluence: NEW prior (funding-as-timing-filter, not funding-as-carry) PROFITABLE in-sample but **MATURING-with-restrictions** — 3 HOLD gates structurally unmet, NOT framework-ship-eligible. Distinct thread from T06 (which stays killed).
- PRIMARY continues: pairs walk-forward OOS (H3 BTC/SOL shipped, ETH/SOL pending G5 cross-framework CV)
- T04 iceberg output analysis remains next-pick for next research session
- T07 portfolio-correlation now possible with mtf_xs_pairs H3 + T08 regime-conditional confluence as 2-line candidate matrix
- T06 NOT reopened — the new T08 prior is structurally different from T06 funding-as-carry

## Strategic Decision 2026-07-21 (vpvr_xs_pairs 4h optimization, quant-researcher)
- **T09 KILLED** — vpvr_xs_pairs_4h_zscore_vpvr_20260710 CPCV param search (SMA-35167). 0/12 pre-registered variants cleared acceptance gates. Structural negative-fold pattern across the entire parameter space. Consistent with cycle-46 family exhaustion: V12/V13/V14 from SMA-33997 + this 4h attempt = 4 kills under cycle-46. The `vpvr_xs_pairs` family is now exhausted for parameter-sweep rebuilds; any future attempt must satisfy "asymmetric execution / multi-TF confirmation, not just parameter sweep".
- The only viable sibling (`mtf_xs_pairs_funding_regime` H3, BTC/SOL Sharpe 2.77 shipped) validates the multi-TF requirement, NOT this 4h z-score+VPVR axis — confirms the kill reasoning.
- PRIMARY continues: (a) ETH/SOL leg G5 cross-framework CV for H3 LIVE candidacy, (b) T04 iceberg output analysis as next-pick research thread, (c) T07 portfolio-correlation matrix using mtf H3 + T08 confluence as 2-line candidates (T09 explicitly EXCLUDED — known-failing axis would dilute matrix).
- BOOTSTRAP queue updated: T04 (P0 tape-reading) advances as next research target; T09 freed research bandwidth by confirming 4h single-TF pair-stat-arb is a dead axis.
- No changes to T01/T06 KILL verdicts (different kill buckets: cost-cap, funding-carry-asym).

## Strategic Decision 2026-07-22 (VPVR Campaign closure, quant-researcher)
- **VPVR Campaign (project d1f4d321) CLOSED** — SMA-34901 flipped to `done` per swarm主agent Path (A) decision (2026-07-22T00:07+08:00, comment id `fda358f8-1d82-4e3c-bbc1-2a1c77d26733`). Project state now: 17 done + 4 cancelled = 21 total, all closed. T08 → archived-campaign-close.
- **T08 archival rationale** (NOT a kill): in-sample edge real on the only regime where the trigger fires; campaign closes because funding>0.03% trigger is structurally dead in current data (May-2024 → Jul-2026, 18 months zero events). Future revival requires trigger definition change, not parameter sweep — would be a new campaign, not revival of T08.
- **T06 remains killed** — different prior content (funding-as-carry vs funding-as-timing-filter); T08 was the "new prior source" that satisfied T06's revival bar but T08 itself is now archived-campaign-close, distinct from T06's KILL.
- **PRIMARY continues unchanged**:
  1. mtf_xs_pairs H3 BTC/SOL live (PR#6 commit `26440acd`); ETH/SOL leg G5 cross-framework CV (SMA-34966) pending
  2. T04 iceberg output analysis — primary P0 next-pick
  3. T07 portfolio-correlation matrix — now uses mtf H3 + [archived T08] as 2-line candidates (T08 explicitly archived, not live)
- **Next VPVR campaign direction** (per swarm-owner 2026-07-21 + swarm主agent 2026-07-22): non-funding-carry axis. The funding>0.03% trigger is provably dead in current data; any successor must use a different trigger AND a different signal class (per cycle-46 family exhaustion rule, multica-agent-base §strategy-layer). Concrete SPEC candidates: (a) liquidation-cascade microstructure (T01/T04 angle), (b) regime-conditional OBI on BTC 5m (cycle-46 lesson), (c) cross-pair basis in non-funding regime. Filing of next-campaign planning issue is owned by the swarm主agent / dispatcher — not by quant-researcher.
- **Killed (do-not-retry-without-new-info) preserved**: T01 (cost-cap, taker-flow imbalance), T04 (cost-cap, resting-liquidity absorption — added 2026-07-22), T06 (funding-as-carry sub-gate), T09 (4h single-TF pair-stat-arb). T08 archived-campaign-close is a distinct state — kept out of the kill bucket to preserve the "trigger-dead, not signal-wrong" distinction. T01 + T04 dual cost-cap kill suggests microstructure at retail-taker execution is structurally thin on this BTC tape — research bandwidth should pivot toward portfolio-level (T07) and higher-horizon alpha (mtf_xs_pairs, donor signal).
- **Open T10 thread**: any future T01+T04 joint revival under sub-taker execution is a single T10 thread if smark wants to fund the maker-side execution research. Filing of T10 SPEC is owned by swarm主agent / dispatcher, not by quant-researcher.

## Strategic Decision 2026-07-22 (T04 iceberg audit, quant-researcher)
- **T04 KILLED** — absorption-trading hypothesis on BTC iceberg detection fails cost-cap at retail-taker execution. Reassignment context: SMA-35021 stalled 37h+, watchdog reassigned back to quant-researcher (78069161) at 2026-07-22T04:02:13 (was reassigned 2026-07-20T23:02:16 to quant-research-agent which declined per skill-fit gate). This session committed to the prior session's "next: T04 iceberg output analysis as primary P0 pick" and delivered the absorption-thesis audit.
- **Audit verdict**: 1,467 iceberg events on 90-day BTC aggTrades (2026-04-19 → 2026-07-17, 129M trades); gross signed returns -0.7bp to +5.4bp at 1s/10s/60s/300s horizons (none significant); post-cost (10.83bp BTC perp round-trip) ALL cells negative. Short-horizon *negative* signed returns suggest "iceberg detected AFTER impact already happened" — opposite of absorption thesis. Big events (top-quartile qty) are *more* negative, not less.
- **Distinct from T01 mechanism**: T01 (OFI, killed) was taker-flow imbalance at 1m; T04 is resting-liquidity absorption at sub-second to 5-min horizons. Same cost-cap conclusion, different kill buckets. The dual kill pattern (T01 + T04 both cost-cap) is meaningful: **microstructure at retail-taker execution is structurally thin on this BTC tape**. Alpha at retail microstructure is small; durable alpha is in (a) portfolio-level diversification across non-correlated lines, or (b) higher-horizon signals where per-trade edge compounds (mtf_xs_pairs H3).
- **PRIMARY continues unchanged**:
  1. mtf_xs_pairs H3 BTC/SOL live; ETH/SOL leg G5 cross-framework CV (SMA-34966) pending
  2. T07 portfolio-correlation matrix — now the **highest-priority research pick** (only remaining P0/P1 thread that has not been audited to kill)
  3. T02 (MFG crowding) / T03 (transfer entropy) — P1 frontier threads, deferred until T07 settles
- **Open question for smark**: does the dual T01/T04 cost-cap pattern warrant opening a T10 sub-taker-execution research thread? Filing of T10 SPEC is owned by swarm主agent / dispatcher, not by quant-researcher. The quant-researcher role here is to surface the dual-kill pattern as evidence; whether to fund T10 is a strategic call.
- **No mention chain**: per `## Mentions` rules, no `@mention` link in the result comment — re-mentioning watchdog / swarm主agent / previous commenters would re-trigger them and start loops on this anchor.
| T04 audit artifacts: `THREADS/T04-iceberg-absorption-audit.md` + `/tmp/iceberg_audit/t04_audit.json` + `/tmp/iceberg_audit/absorb_audit.py` |

## Strategic Decision 2026-07-26 (T11 VPVR edge reversion REVERSED → KILL, quant-researcher)
- **SMA-36615 verdict flipped (provisional, awaiting smark final)** — T11 (VPVR edge-limit reversion) advanced from SPEC candidate → **KILL** at 17:10+08 after orchestrator EVIDENCE audit (comment 712650a0-…) caught a +1d shift look-ahead bug in `vpvr_edge_firsttouch.py`. Originally claimed Viable at 1d / Marginal at 4h / Dead at 1h; corrected reality is **9/9 (sym × horizon) cells NEGATIVE**.
- **Root cause of the reversal (this session)**: my code used `signal_time = setup.window_end` which in my parquet-floor convention was D 00:00 (start of profile-building day), not D+1 00:00 (start of next day). The "future horizon" then walked through the same day's bars that built the profile — pure look-ahead. Pre-correction results (~88% TP1-first, +73~+127bp mean markout) were intra-day reflexive patterns, not next-day mean-reversion.
- **Corrected measurement (orchestrator + my verification)**: BTC fill 63.2% / TP1-first 48.1% / dropout 37.1% / mean_markout_filled **−27.6bp**; ETH 63.2% / 45.9% / 39.1% / **−41.7bp**; SOL 63.7% / 48.0% / 37.0% / **−45.7bp**. 1d horizon all negative; 4h (-37/-58/-71bp) and 1h (-52/-88/-95bp) also all negative.
- **K conditions triggered** (pre-registered a priori): K1 median Sharpe < 0.5 ✓; K4 TP1 hit rate < 50% ✓; K5 cycle-46 negative-fold (9/9 cells) ✓.
- **Honest interpretation**: mechanism kill, NOT cost-cap kill. 1d scale LVN edges are NOT mean-reversion attractors — prices BREAK entry level 37-39% of the time, only revert to HVN center 45-48% (coin flip).
- **Cycle-46 family-exhaustion**: now applied to the VPVR-edge-reversion family on vanilla kline proxy. T11 and T08 (HVN-entry / LVN-exit) BOTH KILL on "next-day mean-reversion" — symmetric geometries, shared failure mode.
- **Stale artifacts (NOT for promotion)**: SPEC draft at `trading/strategies/vpvr_edge_reversion_1d_20260726/SPEC.md` (branch `agent/quant-researcher/379f0585` commit `01dea43`), 3 figures reference pre-correction numbers. Mark as `superseded` if smark confirms KILL.
- **Discipline lesson for future T* threads**: pre-registered K conditions only meaningful if checked against pre-registered measurement. Run sentinel like "do I ever observe future < setup_time?" to catch look-ahead.
- **Revival conditions** (all must hold for any new attempt): (1) tick-level profile data (Bookmap / TradingView PaVP); (2) OFI-augmented signal (avoid Albers 2025 dilemma); (3) liquidation-cascade sub-regime only; (4) regime gate (high vol-of-vol); (5) stronger cost model vs T10 floor.
- **KEEP/KILL final verdict pending smark decision** per orchestrator flow. smark chooses: (a) confirm KILL — close out VPVR-edge family entirely, shift bandwidth to T07 portfolio-correlation + mtf H3; (b) authorize new methodology (one of the 5 revival conditions) — fresh SPEC candidate in NEW strategy dir; (c) defer — shelve T11 with explicit re-evaluation date.
- **PRIMARY continues unchanged**: mtf_xs_pairs H3 LIVE + ETH/SOL G5 framework CV; T07 portfolio-correlation with H3; T10 cost-cap pilot decision (T11 verdict independent of T10 outcome).

### T11 — VPVR edge-limit reversion (KILLED 2026-07-26, round-1 + round-2 both KILL)
- **Status**: **KILLED** (round-2 ALSO KILLED 2026-07-26 21:30+08, this session, SMA-36661). Round-2 was smark's directive methodology-change attempt (finer VPVR + 4h window + z-confirm + lower cost); both smark acceptance gates (TP1-first > 65% AND markout > +30bp) FAIL on every cell. NOT archived-campaign-close — it is a mechanism kill with revival conditions, now closed across **two distinct methodologies** on the same family.
- **Question**: Does the inverse-geometry VPVR reversion (LVN-edge limit entry, HVN-center TP1, opposite LVN TP2) produce post-cost-positive alpha on BTC/ETH/SOL perp at 1d timescale?
- **Round-1 kill reason** (17:10+08, +1d shift correction): 9/9 (sym × horizon) cells negative on Binance perp kline proxy under honest D+1 signal timing. Mechanism kill, not cost-cap. Geometry assumption (LVN-edge reverts to HVN center in next 1d) is NOT supported — prices BREAK the entry level (37-39% dropout) more often than they revert to center (45-48% TP1-first, coin flip).
- **Round-2 kill reason** (21:30+08, +4h shift discipline, finer VPVR 5bp + 4h window + z24-confirm + lower cost): 6/6 cells negative post-cost. Mean markouts -7 to -19bp (improved ~30bp vs round-1); TP1-first 47-63% (improved ~15pp); dropout 27-39% (UNCHANGED). Methodology change DOES shift the mean upward but the absolute level is still net negative. Geometry assumption is NOT recoverable on Binance perp 1m kline proxy under honest signal timing — regardless of profile resolution, window length, confirmation trigger, or cost assumption.
- **Pre-registered K conditions triggered** (round-2): K1 (median Sharpe < 0.5) ✓; K4 (TP1 < 50% at 4h, partial at 1d) ✓; K5 (cycle-46 negative-fold: 6/6 cells) ✓.
- **Distinct prior content from killed VPVR families**: structurally distinct from T08 (HVN-entry / LVN-exit with funding>0.03% trigger, archived-campaign-close) — T08 funding trigger was dead 18mo, T11 has no funding trigger; T11 was inverse-geometry vs T08, maker-execution vs T01/T04 retail-taker. **But all four (T01/T04/T08/T11) share the same failure-mode pattern**: on vanilla Binance perp kline data without orderbook depth / regime filter / OFI augmentation, microstructure-mean-reversion signals fail at retail-taker execution AND maker-execution scales. This is a substrate limitation, not an execution-quality issue.
- **Cycle-46 family-exhaustion: closed across two methodologies**. Round-1 (vanilla 200-bucket daily no-trigger) → 9/9 negative. Round-2 (5bp buckets 4h z24-confirm) → 6/6 negative. Both kills converge on the same substrate-level verdict. Family is closed for vanilla + refined kline proxy attempts.
- **Discipline lesson (codified, second time)**: pre-registered K conditions are valuable only if checked against pre-registered measurement, not post-hoc discovery. Both rounds caught a SHIFT bug only after honest measurement diverged from in-sample. Future T* threads MUST run sentinel like "do I ever observe future < setup_time?" BEFORE trusting first-touch numbers.
- **Revival conditions (all must hold)**: (1) tick-level profile data, not kline-derived; (2) OFI-augmented signal to avoid Albers 2025 dilemma; (3) liquidation-cascade sub-regime only; (4) regime gate (high vol-of-vol); (5) stronger cost model vs T10 9bp VIP0 floor.
- **Threads**: `THREADS/T11-vpvr-edge-reversion-spec.md` (updated to round-2 KILL with both measurement sections).
- **Artifacts**: `vpvr_edge_round2.py` + `round2_summary.json` + `round2_comparison_table.md` + 6 parquet per-tick summaries. Branch: `agent/quant-researcher/sma-36661`.
- **Stale artifacts**: `trading/strategies/vpvr_edge_reversion_1d_20260726/SPEC.md` (commit `01dea43`) + 3 figures with pre-correction numbers in titles. Should be marked `superseded` if smark confirms KILL.
- **Links**: SMA-36661 (this session's round-2 KILL, in_review) + SMA-36615 (round-1 parent KILL) + SMA-36598 (T10 cost-cap, ratified research-wide as maker 2bp/taker 5bp at 21:00+08) + SMA-36660 (issue contract's earlier cost assumption: maker 0.8bp/taker 2bp — contradicted by later T10 close) + SMA-34901 (T08 archived, distinct geometry shared failure mode) + SMA-35167 (T09 cycle-46 baseline) + SMA-34990 (T06 funding KILL) + SMA-35037 (T01 cost-cap) + SMA-35021 (T04 cost-cap) + multica-agent-base §strategy-layer (cycle-46 family-exhaustion, applied across 2 methodologies now) + execution-microstructure skill + paper-replication skill + research-journal skill (kill-with-reason + revival-condition) + Albers et al. 2025 [arXiv 2502.18625].

### T12 — HMM regime detector (SPEC candidate, revival of SMA-35002 axis, new thread 2026-07-26)
- **Status**: maturing → frontier-SPEC candidate. SPEC + thread file shipped. Awaiting strat-indicators implementation against the public API contract + strat-validation walk-forward OOS run + quant-analyst cross-framework CV.
- **Question**: Does a Markov-switching regime detector on a 4h feature vector (realized vol-of-vol + cross-asset funding-basis spread + 4h return z-score), with Student-t emission and BTC+ETH+SOL joint estimation, deliver a posterior `p_t,k` whose **averaged-regime sizing** of a representative donor strategy produces **OOS Sharpe > argmax-regime sizing OOS Sharpe by > 0.3** AND > flat-sizing by > 0.3? (Aumann-falsifier, gate G4.) If yes, regime layer is real. If no, regime layer is decoration, KILL the spec.
- **Revival rationale vs SMA-35002 (Bayesian Regime Posterior, KILLED 2026-07-19)**: 35002 was killed because its prior content (VPVR-distance + funding z-score) was the same sub-gate content on `funding_carry_asym` lineage (5/6 gates FAIL), Bayesian averaging cannot compensate. Recorded resurrection criterion (comment `29d855a0-...`): "only reopen if paired with a *new* prior content — one whose raw signal clears cost on the canonical pipeline *before* being folded into a Kim-filter posterior." T12 satisfies by replacing the killed prior with 3 NEW components (rvov, fbasis, retz) that have NO content overlap with any killed line (VPVR, microstructure taker flow, single-asset funding z, pair signal).
- **Structural deltas vs 35002**: 4h refit (not 15m curve-fitting); BTC+ETH+SOL joint (not BTC single-asset); Student-t emission with EM-estimated ν (not Gaussian — directly addresses 35002 gate E "uninformative"); Aumann-falsifier promoted to PRIMARY acceptance gate; min-frequency ≥ 1% bars per regime (anti-pattern guard); autocorr(p_t,k, 4h) < 0.5 refit-cadence justification gate.
- **Pre-registered acceptance gates** (G0-G7): G0 raw-mechanism screen for each feature component; G1 BIC selects K ∈ {3,4}; G2 min-frequency ≥ 1%; G3 autocorr gate; G4 Aumann-falsifier; G5 walk-forward DSR > 0; G6 no negative-fold; G7 cross-framework CV (hmmlearn vs custom-em).
- **Walk-forward OOS protocol**: 7 expanding windows anchored 2024-01, 3-month test slices, 24h embargo around regime switches. Donor strategy: BTC/ETH/SOL 4h 30-bar EMA cross (non-regime-gated baseline). Cost: VIP0 9bp + 15bp stress corner.
- **Cycle-46 dedup**: no VPVR feature (T06/T08/T09 killed); no 1m microstructure (T01/T04 killed); no single-asset funding z (T06 killed); no pair signal (T09 killed). Different layer from T11 (state detector vs directional signal).
- **Promote target**: SMA-30199 frontier-SPEC bucket, contingent on G0-G7 passing. Not promoted yet (gates unevaluated).
- **Threads**: `research/THREADS/T12-hmm-regime-detector-spec.md`. SPEC: `research/specs/hmm_regime_detector_4h_20260726/SPEC.md`.
- **Revival condition for KILL (shelving rather than revival)**: if G4 Aumann-falsifier fails OOS or G0 raw-mechanism test eliminates all 3 feature components, the "no suitable prior content exists for an HMM at 4h on this data" verdict is the terminal answer — analogue of 35002's KILL. Don't try a 4th feature component without new external evidence.
- **Links**: SMA-35762 (this thread's parent issue) + SMA-35669 (parent project Research queue #92) + SMA-35002 (Bayesian Regime Posterior KILL — load-bearing revival criterion) + SMA-34990 (T06 funding-carry-asym KILL — distinct prior) + SMA-34875 (mtf_xs_pairs H3 — multi-TF confirmation template) + SMA-30199 (frontier-SPEC bucket, promotion target) + SMA-36598 (T10 VIP0 9bp floor reference) + `_shared/regime/btc_gate.py` (existing hard-assignment, NOT replaced) + multica-agent-base §strategy-layer (cycle-46 family-exhaustion) + regime-macro SKILL (4h refit + Aumann-falsifier + min-frequency ≥ 1%) + paper-replication SKILL (cross-framework CV) + Hamilton 1989 + Andersen-Bollerslev-Diebold 2007 (rvov mechanism) + Kim 1994 (Kim filter).

## Strategic Decision 2026-07-26 (T12 HMM regime detector SPEC, quant-researcher)
- **T12 SPEC shipped** — HMM regime detector revival of SMA-35002 axis with NEW prior content, 4h refit, BTC+ETH+SOL joint, Student-t emission. Resolves the SMA-35762 blocked-for-7-days issue (regime-macro L2 SPEC request).
- **Load-bearing change vs 35002**: prior content replaced. The killed prior was VPVR-distance + funding z-score (sub-gate on `funding_carry_asym` lineage); the new prior is 4h realized vol-of-vol + cross-asset funding-basis spread (relative, not single-asset) + 4h return z-score. Each component's raw-mechanism is pre-screened by gate G0 BEFORE Kim-filter posterior; if all 3 fail, the spec KILLs (no prior content exists at 4h on this data).
- **Architecture choices justified per regime-macro SKILL**: 4h-only refit (15m = curve-fitting); K ∈ {3,4} with BIC and min-frequency ≥ 1% bars per regime (anti-pattern guard against <1% "crisis" collapse); Student-t emission with EM-estimated ν (35002's Gaussian-emission failure mode is structurally closed); Aumann-falsifier is the PRIMARY acceptance gate (G4), not a sub-criterion.
- **Cycle-46 dedup successful**: T12 feature vector contains zero content overlap with VPVR (T06/T08/T09 killed lines), microstructure taker-flow (T01/T04 killed lines), single-asset funding z (T06 killed line), or pair signals (T09 killed line). T12 is a state detector; T11 is a directional signal. No family-exhaustion violation.
- **NO implementation work this session** — quant-researcher role is SPEC + acceptance gates per multica-agent-base §strategy-layer contract. Downstream chain: strat-indicators (L3 impl) → strat-validation (L3 OOS) → quant-analyst (L4 cross-framework CV). EVIDENCE-comment will report G0-G7 with concrete numbers after OOS run.
- **PRIMARY continues unchanged**: mtf_xs_pairs H3 LIVE + ETH/SOL G5; T07 portfolio-correlation with H3 + T08 archived; T11 awaiting T10 + analyst blocker clear; T10 (cost-cap pilot decision) parking.
- **Verdict (this session, quant-researcher)**: SPEC shipped; pre-registered gates cannot be evaluated until implementation + walk-forward OOS run. Promote-to-SMA-30199 contingent on G4 passing. SMA-35762 status: blocked → in_progress → in_review once this comment posts.

### T13 — Liquidity Sizing (MCLS — Multi-Cap Intersection, SPEC candidate, new thread 2026-07-26)
- **Status**: maturing → frontier-SPEC candidate. SPEC shipped at `research/specs/liquidity_sizing_v1_20260726/SPEC.md`. Not yet promoted to SMA-30199 — V-gates V0-V7 must pass first (strat-execution L3 impl + strat-data L3 sources + strat-validation L3 OOS + quant-analyst L4 audit).
- **Question**: Does a multi-cap intersection sizing primitive (5 liquidity caps intersected via min, composed with `_shared/sizing/vol_target.py`) produce post-cost-positive sizing infrastructure, where the prior single-axis sizing sweep (SMA-34955, uniform `risk_target_pct` × 42 variants) failed structurally?
- **Revival rationale vs SMA-34955 (KILLED 2026-07-19, sizing axis)**: SMA-34955 was a uniform `risk_target_pct` parameter sweep (0/42 variants passed G3). The axis was wrong — fixed-pct cannot respond to depth / regime / impact / informed-flow differences across bars. MCLS opens the depth-axis that SMA-34955 ignored: real L2 depth, bar-volume participation, projected impact, VPIN — all intersected per bar. SMA-34955 KILL does not invalidate MCLS; MCLS is on a structurally different axis.
- **Revival rationale vs T01 + T04 dual cost-cap kill (2026-07-22)**: T01 (OFI) and T04 (iceberg) both ignored depth-axis data they had available. Both signals were small-positive gross edges that became structurally negative post cost because sizing did not constrain to what the venue could absorb. MCLS adds cap_impact (shrink when projected_impact > k_impact × expected_edge) directly closing this loop.
- **Structural deltas vs SMA-34955**: 5 intersection caps (vs 1 fixed-pct axis); depth-axis (vs pct-axis); composed with vol_target (vs replacing); real-data sources mandatory (V0 structural pre-condition); Aumann-falsifier sizing-axis (V3) is the PRIMARY acceptance gate (not a sub-criterion).
- **Pre-registered acceptance gates** (V0-V7): V0 real-data only (no kline proxies); V1 cost-cap stays ratified (22bp RT for ≥ 95% of fills); V2 each sub-cap honored (V2.1-V2.5); V3 Aumann-falsifier (> 0.2 OOS Sharpe lift vs argmax-cap AND flat-sizing); V4 VPIN-shrink fires with t ≥ 2; V5 kill-switch handoff at all-caps < k_floor; V6 stale-L2 fallback to cap_adv-only; V7 cross-strategy MCS lift > 0.05 vs vol_target-only.
- **Walk-forward OOS protocol**: 7 expanding windows anchored 2024-01, 3-month test slices, 24h embargo. Donor strategy: mtf_xs_pairs H3 BTC/SOL (SMA-34875 shipped). Stress corners: 22bp baseline (ratified), 35bp stress, 9bp optimistic VIP0.
- **Cycle-46 dedup**: NO content overlap with any killed line (VPVR / microstructure taker / funding / pair / regime). SMA-34955 was fixed-pct axis (DIFFERENT axis), T01/T04 cost-cap is shared lesson (DIFFERENT content). MCLS composes WITH T11 (VPVR edge reversion SPEC) and T12 (HMM regime SPEC) as donor strategies for V7 cross-strategy MCS matrix.
- **Promotion target**: SMA-30199 frontier-SPEC bucket, contingent on V0-V7 passing.
- **Threads**: `research/THREADS/T13-liquidity-sizing-spec.md`. SPEC: `research/specs/liquidity_sizing_v1_20260726/SPEC.md`.
- **Revival condition for KILL**: if V1 cost-cap fails OR V3 Aumann-falsifier fails OR any V2.1-V2.5 sub-cap fails OR V5 kill-switch handoff fails OR V6 stale-data fallback fails, the spec is KILLED. WHY + revival condition recorded in THREAD. Revival requires (a) new data source not currently used (cross-venue depth), (b) fundamentally new datacontract (order-by-order L3 reconstruction), or (c) 3+ month time-decay on a new regime.
- **Links**: SMA-35536 (this issue) + SMA-35467 (parent project Risk Management) + SMA-30199 (frontier-SPEC bucket, promotion target) + SMA-34955 (KILLED sizing axis — load-bearing prior content, uniform risk_target_pct sweep) + SMA-35037 (T01 OFI cost-cap KILL) + SMA-35021 (T04 iceberg cost-cap KILL) + SMA-34900/SMA-34913 (ratified 22bp RT cost-cap baseline) + SMA-34875 (mtf_xs_pairs H3 — donor strategy for V3/V4/V7) + SMA-36598 (T10 cost-cap decomposition, 9bp VIP0 premise) + SMA-35762 (T12 HMM regime SPEC — shares Aumann-falsifier methodology) + SMA-36615 (T11 VPVR edge reversion SPEC candidate — donor for V7 when shipped) + `_shared/sizing/vol_target.py` (composed-with, NOT replaced) + `_shared/sizing/README.md` (opt-in library convention) + `_shared/execution/cost_model.py` (ratified cost constants, sqrt-impact model for cap_impact) + multica-agent-base §strategy-layer (cycle-46 family-exhaustion, opt-in library, G1-G7 strategy gates distinct from V-gates sizing gates) + multica-agent-base §Result Wire (EVIDENCE comment with concrete numbers, not vibes) + execution-microstructure SKILL (real aggTrades requirement, NOT kline proxy) + regime-macro SKILL (Aumann-falsifier methodology applied to sizing layer) + portfolio-risk SKILL (MCS ≥ 0.05 falsification for V7) + paper-replication SKILL (walk-forward OOS protocol template) + Torre & Ferraris 1997 (sqrt impact model) + Easley-O'Hara 2012 (VPIN) + Lee-Ready 1991 (bulk-volume classifier) + Almgren-Chriss 2000 (optimal execution framing) + Kyle 1985 (informed-trading probability) + Albers et al. 2025 [arXiv:2502.18625] (maker dilemma, informs cap_depth) + research-journal SKILL (kill-with-reason + revival condition).

## Strategic Decision 2026-07-26 (T13 Liquidity Sizing SPEC, quant-researcher)
- **T13 SPEC shipped** — Multi-Cap Liquidity Sizing (MCLS) addresses Risk Mgmt #68 (SMA-35536) from parent project SMA-35467. Resolves the blocked-for-1-day issue (multica-strategy L3 correctly escalated out-of-scope at 2026-07-26 13:40).
- **Load-bearing change vs SMA-34955 (killed sizing axis)**: replaced uniform `risk_target_pct` axis-sweep with 5 intersection caps on depth-axis (adv / depth / participation / impact / vpin). Each cap closes a distinct failure mode that prior lines ignored (T01/T04 cost-cap). Aumann-falsifier (V3) is the primary acceptance gate — sizing must beat argmax-cap AND flat-sizing by > 0.2 OOS Sharpe on a donor strategy.
- **Architecture choices justified per execution-microstructure + portfolio-risk SKILLs**: real aggTrades + L2 sources (V0 structural); 22bp RT cost-cap held (V1); composed with `_shared/sizing/vol_target.py` (depth-axis × vol-axis, opt-in library); kill-switch handoff at all-caps < k_floor (V5); stale-L2 fallback to cap_adv-only (V6); cross-strategy MCS > 0.05 (V7).
- **Cycle-46 dedup successful**: zero content overlap with VPVR (T06/T08/T09 killed), microstructure taker (T01/T04 killed), funding (T06 killed), pair (T09 killed), regime (SMA-35002 killed). SMA-34955 was fixed-pct axis — MCLS is depth-axis, structurally different. MCLS composes with T11 (VPVR edge reversion) and T12 (HMM regime) as donor strategies for V7 matrix.
- **NO implementation work this session** — quant-researcher role is SPEC + acceptance gates per multica-agent-base §strategy-layer contract. Downstream chain: strat-execution (L3 impl) → strat-data (L3 sources) → strat-validation (L3 OOS) → quant-analyst (L4 sub-cap audit + V3 Aumann re-impl) → smark-signoff-proxy (L4 V7 sign-off).
- **PRIMARY continues unchanged**: mtf_xs_pairs H3 LIVE + ETH/SOL G5; T07 portfolio-correlation; T11 awaiting T10 + analyst blocker clear; T12 awaiting strat-indicators impl.
- **Verdict (this session, quant-researcher)**: SPEC shipped; pre-registered V-gates cannot be evaluated until implementation + walk-forward OOS run. Promote-to-SMA-30199 contingent on V0-V7 passing. SMA-35536 status: blocked → in_progress → in_review once this comment posts.

### T14 — Performance attribution (shipped 2026-07-28, research infrastructure)
- **Status**: shipped — tool + SPEC + donor EVIDENCE. Gates A0-A5 PASS, A6 FAIL-with-finding (recorded, not redefined). Not a strategy thread; no SMA-30199 promotion.
- **Question (answered)**: Can "cost kill vs mechanism kill" be made mechanical from a closed-trade ledger? YES — `_shared/attribution/decompose.py` (gross recomputed from prices, cost swept by CostSpec, analytic break-even, kill classification, sentinels per T11 lesson).
- **Key donor numbers**: H3 (44,845 trades) gross +0.5bp/trade, break-even 0.126 bps/side → COST_CAP_KILL at every realistic cost (net −33.62 @ 8bp config, −195.06 @ 44bp ratified pair). trend_multi BTC MECHANISM_KILL at zero cost (gross −0.865).
- **A6 finding**: H3 summary.json "PROFITABLE"/Sharpe 2.32 came from cost-FREE per-bar equity path (`mtf_xs_pairs_base_20260718.py:560`); cost existed only in the trade log (line 576). SMA-36566 fee-shock bug class, surfaced mechanically.
- **Cuts finding**: z_mean_revert exits carry all gross edge (+52.6); regime_break exits bleed (−46.5 gross). Edge lives in 25–240-bar holds.
- **NEW open question (T14.1, quant-analyst audit)**: (a) do other strategy summaries share the cost-free `pnl_per_bar` pattern (sweep `_indicators/*base*.py`)? (b) unify trade-log vs equity-path notional/compounding conventions so per-trade and equity-path attribution agree by construction (tool break-even 0.126 bps/side vs curator 20bps RT divergence).
- **Threads**: `THREADS/T14-performance-attribution.md`. SPEC: `research/specs/performance_attribution_v1_20260728/SPEC.md`. Evidence: `analysis/attribution/`.
- **Links**: SMA-35757 + SMA-35669 + SMA-34875 (donor) + SMA-36566 (bug class) + SMA-35037/SMA-35021 (cost-cap kill class) + SMA-36615/SMA-36661 (mechanism kill class + sentinel lesson) + T07 (alpha_beta consumer).

## Strategic Decision 2026-07-28 (T14 performance attribution, quant-researcher)
- **T14 shipped** — Research #87 (SMA-35757) executed end-to-end in research-mainline scope (SPEC + implementation + tests + donor EVIDENCE), instead of SPEC-only hand-off, because the deliverable was a self-contained validation tool and the T12/T13 SPEC-only hand-offs stalled 24h+ downstream.
- **Method chosen**: cost attribution (gross recomputed from prices; ledger pnl_pct never trusted) + conditional cuts + kill classification (MECHANISM_KILL / COST_CAP_KILL / VIABLE_AT_COST) + analytic break-even. Brinson rejected (no sectors/benchmark in crypto perp).
- **Gate discipline**: A6 pre-registered gate FAILED on false premise — recorded as finding (H3 PROFITABLE tag was a gross-path artifact), NOT redefined post-hoc. Same discipline as T11 round-1/2 sentinel lesson.
- **Convergent validation**: 2026-07-26 mtf_xs_pairs family seal reproduced mechanically (COST_CAP_KILL at all realistic costs; zero cost headroom).
- **Downstream (not this thread)**: results-ledger batch annotation (strategy-worker); 口径 audit + cost-free pnl_per_bar sweep (quant-analyst); T07 consumes per-strategy net daily series + alpha_beta().
- **PRIMARY continues unchanged**: T07 portfolio-correlation is the top remaining P2 research pick; T12/T13 SPEC stalls flagged to orchestrator (curator retro 2026-07-27 already recommended nudge).
