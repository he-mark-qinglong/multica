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

### T04 — Iceberg detection efficacy
- **Status**: exploring (SMA-34992 task 106f7349 produced output 05:34)
- **Question**: Does clustered same-ms trade-burst detection at pinned prices predict institutional accumulation? What's the OOS Sharpe of entering on confirmed iceberg absorption?
- **Prior**: task 106f7349 just completed — result pending analysis.
- **Next**: read the task output, extract the detection statistics, design the entry signal.
- **Links**: T01 (OFI), execution-microstructure skill.

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

## Killed (do not retry without new info)
- Bayesian Regime Posterior (SMA-35002): 5/7 pre-SPEC gates FAIL, Aumann-falsifier FAIL. Prior content sub-gate.
- funding-carry-asym V1/V2 (SMA-34990): NOT-PROFITABLE on canonical window.
- sizing axis (SMA-34955): 0/42 variants pass G3, structurally exhausted.


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
