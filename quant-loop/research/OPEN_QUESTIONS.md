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
