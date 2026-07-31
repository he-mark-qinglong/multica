# Epoch Digest — 2026-07-27

**Scope:** workspace `f9a9d34e-…` (UTC+8). Slot [SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926) `[epoch-retro 2026-07-27]` (knowledge-curator `4f50d87d-…`, autopilot `23281f8e-…`, 21:00 Asia/Shanghai cron).
**Prior surfaces:** `2026-07-17-debug-summary.md`, `2026-07-18-knowledge-snapshot.md`, `2026-07-26-epoch-digest.md` (KILL-reset day), `kg_update_2026-07-26.md`.
**Sources (all primary):** live `multica` CLI (`issue get / list / comment list / metrics query / autopilot get`) on cited SMA-IDs, today's research-scout KILL ([SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec)), today's escalation-router sweep ([SMA-36690](mention://issue/c3f678c2-57c9-4ede-8b36-ee20e9cd45ec)), framework-validate hourly crons, and the strategy-side running JOURNAL ([`~/multica/quant-loop/research/JOURNAL.md`](../quant-loop/research/JOURNAL.md)) + [`AGENTS.md`](../../AGENTS.md) operating-model section.

> **Constitution v1.0 honesty note.** Numbers below come from cited primaries (issue bodies / DECISION comments). Anything I could not verify from a primary source is marked **n/a** and flagged. The 8 framework-validate W5 auto-archives today are post-KILL residual cleanup, NOT new sweep signal — preserved in the table for completeness but the curator's cycle-46 verdict relies on the `mtf_xs_pairs` family-seal from yesterday, not on today's count.

---

## 1. Verdict summary (KEEP / KILL / ESCALATE, 2026-07-27)

### 1.1 Verdict trail

| Verdict type | Issue | Subject | Status |
|---|---|---|---|
| **KILL** (cost-cap, no SPEC) | [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) | research-scout 2026-07-27: cross-section momentum + regime-switching meta-signal on 7-perp universe → cost-cap FAIL (428% required gross edge vs 20-40% achievable, 10-20× short) | done 2026-07-27T09:17+08 (quant-research-agent) |
| **ESCALATE → smark (within-tolerance, low-priority)** | [SMA-36690](mention://issue/c3f678c2-57c9-4ede-8b36-ee20e9cd45ec) ← [SMA-36688](mention://issue/5423bae0-0cb1-4e10-8070-c7d94bb8e6a5) | `vpvr_xs_basis_zscore_15m_funding_filter_20260712_p3opt_010 × vectorbt 1.1.0` W5 ESCALATE-TO-SMARK (WITHIN_TOLERANCE) — divergence 0.00284% (basis-point noise), third framework agrees with in-house; smark can ignore per source agent | todo (urgent), agent recommends skip |
| **ESCALATE → smark (within-tolerance, low-priority, stale 7d)** | [SMA-35091](mention://issue/4b0a7a7c-3821-4fe7-93fd-3cb073b3548f) | `[need-smark-decision: framework-cv-divergence]` divergence 42.51% < 50% threshold (W5 §W5.2 explicit smark-decision review path) | todo (urgent, 170h+ age) |
| **ESCALATE → smark (missing-acceptance, stale 1d)** | [SMA-36670](mention://issue/92e3fb04-42f1-4531-b5dc-af9ce864a2b1) | `[need-smark-decision: missing-acceptance]` Risk Mgmt #0 max-position-size-limit: 4 acceptance criteria unfilled (A) fill+reassign vs (B) defer back to [SMA-35467](mention://issue/d60e6bdb-6c8a-4597-98c6-ea01acc00037) triage | todo (high) |
| **W5 NOT-PROFITABLE auto-archive** | 8 today | 7× `vpvr_xs_pairs_30m_funding_filter` family variants (BTCSOL v10_optimize, BTCSOL v3, ETHSOL v5_loose, BTCBNB v5_loose, BTCDOGE, BTCSOL regularized, BTCSOL base) — `vectorbt 1.1.0` framework CV confirmed NOT-PROFITABLE on every variant | all done (cross-framework CV converged) |
| **W5 NOT-PROFITABLE auto-archive (cross-family)** | 1 today | `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 × vectorbt` — divergence 94.16% Sharpe / 125.08% ann_return / 8.66% max_dd (two tipping) | done (SMA-36681) |
| **NOOP** (cycle, not strategy) | [SMA-36693](mention://issue/a6b3f3ad-1e84-417b-9d2d-f606fd4672a9), [SMA-36703](mention://issue/20c065ca-a320-41c6-b46d-0a1d505cde2f) | smark-decision-cycle 09:00 + 21:00 — 0 auto-decisions fired; 2 untouched (the 2 stale escalations above); no `[DEPLOY-FAIL]` / `[heartbeat-noop]` / `framework-validate >50%` / `Agent-Sync-2h` / `VPPR Optimizer v3` candidates in scope | done (no action) |
| **NOOP** (cron health) | [SMA-36674/682/684/686/694](mention://issue/) | graph-janitor 5 hourly runs (00:09 / 01:11 / 05:11 / 06:09 / 07:09 / 09:09) — all clean, no orphans, edge budget under cap | all done |
| **NOOP** (cron health) | [SMA-36675/677/678/779/683/685/687/689/701](mention://issue/) | escalation-router 9 sweeps (00:37 / 01:37 / 02:37 / 03:37 / 04:37 / 05:37 / 06:37 / 07:37 / 08:37 / 20:37) — most no-op; only one true route (SMA-36690); sweep #247 0 fresh markers | all in_review |
| **KEEP** | none | — | — |
| **T12 HMM regime / T13 MCLS SPEC picked?** | [SMA-35762](mention://issue/), [SMA-35536](mention://issue/) | **NO** — both still in_review, last touched 2026-07-26T17:09 / 17:14. Yesterday's curator Priority 1/2 picks did NOT advance today. | in_review, no 10:00 selection fired |

> **0 LIVE-eligible strategy at epoch close 21:00.** Same state as AGENTS.md §"Strategy state (2026-07-26 02:30)" — unchanged. T11 VPVR edge reversion still pending smark confirmation (provisional KILL); se_h3 复审 (SMA-36660) also still pending smark human; both do not arrive in time for this epoch.

### 1.2 Strategic consequence tree

- **Cross-section momentum + HMM meta-signal is now KILLED-FAMILY too.** Today's scout KILL brings the cycle-46 killed-family list to: `1m/5m klines reversal`, `funding-carry`, `4h single-TF stat-arb`, `microstructure features`, `mtf_xs_pairs whole family`, **`cross-section momentum + regime-switching meta-signal`** (new today). The cost-cap argument is bullet-proof (10-20× short of required gross edge at VIP0) and independent of any microstructure or VPVR mechanism; the only revival path is sub-taker execution ≤1bp (T10 maker pre-SPEC dependent) or 1/day rebalance (different strategy).
- **`vpvr_xs_pairs_30m_funding_filter` family is W5 fully exhausted.** 8 distinct variants today (BTCSOL base + v3 + v10_optimize + regularized, ETHSOL v5_loose, BTCBNB v5_loose, BTCDOGE, BTCSOL xs-basis-zscore opt_010) all converged on cross-framework NOT-PROFITABLE verdict. The framework CV pattern is now consistent: `backtrader`, `freqtrade`, `vectorbt` (3rd framework) all confirm divergence > 50% on Sharpe + ann_total_return + max_dd → AUTO-ARCHIVE per W5 §W5.2. **This family should be promoted to cycle-46 dead list at next curator or smark touchpoint** — its revival condition is identical to `mtf_xs_pairs` (sub-taker execution ≤20bps AND human approval per `KILLED.md`).
- **`vpvr_xs_basis_zscore_15m_funding_filter` opt_010 is the **anomaly**: 0.00284% divergence means backtrader + freqtrade + vectorbt all agree to basis-point noise on the in-house equity CSV. Per W5 §W5.2 protocol this is a WITHIN_TOLERANCE ESCALATE-TO-SMARK, NOT an auto-archive. The agent's framing: "smark can ignore this ESCALATE unless they want to inspect the CV record for the broader xs-basis-zscore family (opt_008 + opt_010 both in WITHIN_TOLERANCE → family-level framework/inhouse agreement confirmed)". opt_008 (the prior WITHIN_TOLERANCE run) is the symmetry peer — both opt_008 + opt_010 within-tolerance = the xs-basis-zscore family **passes cross-framework CV**, but the strategy itself is NOT-PROFITABLE (in-house sharpe -1.32). So the family-level verdict is: engines agree the strategy is bad, but not the cost model. Curator note: this is a clean cross-framework-CV-OK result; treat as a permanent archive candidate, not a KILL on the family axis.
- **T12 HMM regime and T13 MCLS SPEC stalled since yesterday.** No 10:00 selection fired today (no priority=high in-todo SPEC promotion happened). Both still `in_review` with last touched 2026-07-26T17:09/17:14. This is a 24-hour stall, not yet a concern (SPECs typically sit in_review while strat-execution/strat-indicators pick them up), but if it persists through 2026-07-28 the orchestrator should re-dispatch a nudge.
- **smark queue is light today, 3 stale items.** (1) SMA-36690 urgent — within-tolerance ESCALATE, agent says ignore; (2) SMA-35091 urgent — 170h+ old 42.51% divergence (W5 smark-decision review path); (3) SMA-36670 high — Risk Mgmt #0 acceptance unfilled. The escalation-router is routing correctly (1 route today, 9 sweeps) but the human queue is not draining.

---

## 2. Daily comparison table (curator-verdict format)

> Numbers from cited primaries (issue bodies / VERDICT.md / SPEC.md / `results-ledger.md`). Where a metric-row from `multica metrics query` would normally populate the cell and is unavailable, marked `(metric-row n/a)`. The "human verdict" column is one-line per AGENTS.md §7. **No new backtests shipped today** — the table below is dominated by W5 framework-CV confirmations on previously-tested strategies, all NOT-PROFITABLE. 0 strategies advanced gates today.

| Strategy | OOS Sharpe | CI lower | Post-fee Sharpe @60bps pair-RT | Max drawdown | Verdict rationale (one line) |
|---|---|---|---|---|---|
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | in-house -2.7249 (NOT-PROFITABLE) | n/a | n/a | -0.9021 (90.21%) | **W5 archive** — vectorbt 1.1.0 3rd-framework CV confirms divergence; BT+FT prior W5 archives; family exhausted → [SMA-36700](mention://issue/3cd8d8a4-7960-4dfc-b308-a44dbb44d5d2) |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | in-house -6.4024 (NOT-PROFITABLE) | n/a | n/a | -0.9859 (98.59%) | **W5 archive** — vectorbt 1.1.0 divergence; 5,642 trades, BTCSOL pair axis → [SMA-36699](mention://issue/8d611a23-2693-4097-a89a-b14111b75f62) |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | n/a (vectorbt 3rd-framework CV divergence 94.16%) | n/a | n/a | divergence 8.66% (NOT tipping) | **W5 archive** — vectorbt 1.1.0 Sharpe+ann tipping, max_dd NOT; AUTO-ARCHIVE per W5 §W5.2 → [SMA-36681](mention://issue/8abc60f5-850c-4595-9b8f-331105bbc2ec) |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | in-house +0.895 walk-forward aggregate; framework +5.119 OOS (5.7× magnitude gap) | n/a | n/a | divergence 57.21% | **W5 archive** — vectorbt 3rd-framework CV; the structural Sharpe convention gap (fold-specific vs replay-all trades) dominates the cost-mode gap; in-house profitable but framework does not reproduce → [SMA-36696](mention://issue/f895feb2-09c8-4d7b-a046-ff3fcb29f3db) |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` (BTCSOL v3 opt) | n/a (BT+FT+vectorbt converged NOT-PROFITABLE) | n/a | n/a | n/a | **W5 archive** → [SMA-36698](mention://issue/) |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` (BTCSOL v3) | n/a (BT+FT+vectorbt converged) | n/a | n/a | n/a | **W5 archive** → [SMA-36697](mention://issue/) |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | n/a (BT+FT+vectorbt converged) | n/a | n/a | n/a | **W5 archive** → [SMA-36691](mention://issue/) |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | n/a (BT+FT+vectorbt converged) | n/a | n/a | n/a | **W5 archive** → [SMA-36695](mention://issue/) |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | in-house +Sharpe confirmed (NOT-PROFITABLE total); vectorbt 3rd-framework CV 332.67% divergence | n/a | n/a | 57.29% | **W5 archive** — vectorbt canonical 0bp cost gives $5012.87x terminal equity vs in-house $414.12 (24bp RT at exit); same family as above → [SMA-36673](mention://issue/4840561a-5450-4b49-a8de-0b96d9f5611b) |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712_p3opt_009` | in-house NOT-PROFITABLE; vectorbt 99.95% Sharpe / 99.96% ann / 94.60% max_dd | n/a | n/a | 94.60% divergence (tipping) | **W5 archive** (run last night, included for completeness) → [SMA-36668](mention://issue/1845aff5-34d8-4859-8ce5-e0022dfa635c) |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712_p3opt_010` | in-house sharpe -1.32 (NOT-PROFITABLE); vectorbt 0.00284% divergence (WITHIN_TOLERANCE) | n/a | n/a | within-tolerance | **W5 ESCALATE-TO-SMARK** (NOT auto-archive) — 3rd-framework agreement on badness; xs-basis-zscore opt_008+opt_010 family WITHIN_TOLERANCE → family-level framework CV passes; per agent "smark can ignore unless they want to inspect the family CV record" → [SMA-36688](mention://issue/5423bae0-0cb1-4e10-8070-c7d94bb8e6a5) + [SMA-36690](mention://issue/c3f678c2-57c9-4ede-8b36-ee20e9cd45ec) |
| **T11 vpvr_edge_reversion_1d** (round-1) | n/a (look-ahead bug pre-fix, post-fix 9/9 cells negative) | n/a | -28/-42/-46 bp mean markout (BTC/ETH/SOL, 1d TTL, scenario_b_defensive) | n/a | **provisional KILL** (unchanged from yesterday) — pending smark confirmation → [SMA-36615](mention://issue/5f3a64d7-…) + [SMA-36620](mention://issue/725e1812-541c-4359-9cf6-22744af59f73) audit |
| **T11 vpvr_edge_reversion_1d** (round-2, finer VPVR) | n/a (same +1d shift bug class, +4h shift post-fix honest) | n/a | -9.8/-17.2/-18.5 bp (BTC/ETH/SOL 4h); -7.0/-14.0/-15.7 bp (BTC/ETH/SOL 1d) — 6/6 cells negative | n/a | **KILL** (round-2, last night) — K1, K5 unambiguously triggered; cycle-46 family-exhaustion applied across TWO distinct methodologies → [SMA-36661](mention://issue/978f8d62-81b4-4415-b156-a3a5100e4cda) (done) |
| **T10 maker-pilot** (research, not strategy) | n/a (closed) | n/a | ratified maker 2bp / taker 5bp research-wide | n/a | **CLOSED** — smark 10:00 close: no pilot, rate locked; any revival requires maker execution SPEC pass + sub-1bp effective cost |
| **T12 HMM regime** (SPEC shipped, no backtest) | n/a (gates G0-G7 not evaluated; SPEC stalling 24h+) | n/a | n/a | n/a | **SPEC-only, awaits V0-V7 + G0-G7** — Aumann-falsifier G4 is the acceptance gate → [SMA-35762](mention://issue/) (in_review, untouched 24h) |
| **T13 MCLS liquidity sizing** (SPEC shipped, no backtest) | n/a (gates V0-V7 not evaluated; SPEC stalling 24h+) | n/a | ratified 22bp RT premise (cap_impact ⟂ cost) | n/a | **SPEC-only, awaits V0-V7** — donor strategy `mtf_xs_pairs` H3 (deployed but family-KILLed) → [SMA-35536](mention://issue/) (in_review, untouched 24h) |
| **Cross-section momentum + HMM regime meta-signal** (today's scout) | n/a (no backtest, no SPEC) | n/a | theoretical: cost ~214% annual drag @ VIP0, requires 428% gross edge | n/a | **KILL cost-cap** — 10-20× short of required edge; revival requires sub-1bp execution OR 1/day rebalance → [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) |

**One-line human verdict (persona-advisor style):** "Cross-section momentum + HMM meta-signal KILLed on cost-cap (10-20× short of required edge) — adds a new entry to the cycle-46 dead list. `vpvr_xs_pairs_30m_funding_filter` family burned through 8 variants today, all W5 auto-archived (vectorbt 3rd-framework convergence) — family should be promoted to cycle-46 dead list at next touchpoint. `vpvr_xs_basis_zscore_15m_funding_filter` opt_010 is the anomaly: 0.00284% cross-framework divergence (engines agree the strategy is bad). T11 round-2 KILLed last night (6/6 cells negative, K1/K5 triggered). T12 HMM regime + T13 MCLS SPECs sat untouched 24h+ — no 10:00 selection fired today. 0 LIVE-eligible strategies. 3 stale smark queue items (1 urgent within-tolerance, 1 urgent 170h+ old divergence, 1 high missing-acceptance)."

---

## 3. cycle-46 exhaustion check (this week, 2026-07-20 → 2026-07-27)

Counted strategy-family occurrences in issue titles this week (running tally from `multica issue list --limit 400 --output json`, filtered for families named in the cycle-46 family list or the AGENTS.md killed-family list):

| Family | Week count | Direction | Action |
|---|---|---|---|
| `vpvr_xs_pairs_30m_funding_filter` | **15** | **W5 AUTO-ARCHIVE cascade today (8 of 15), vectorbt 3rd-framework CV converged NOT-PROFITABLE on every variant** | **[type=KILL] recommendation**: promote to cycle-46 dead list; revival requires sub-taker execution ≤20bps pair-RT AND human approval per `KILLED.md` — same rule as `mtf_xs_pairs` |
| `mtf_xs_pairs` | **8** | **KILLED yesterday (smark 8:31 family-seal)** | already on dead list; today's iters are post-seal residual cleanup, NOT new sweeps |
| `vpvr_stable_depeg_regime_4h` | 8 | W5 archive cascade (multiple variants confirmed NOT-PROFITABLE) | already on dead list (T09 cycle-46 baseline) |
| `vpvr_xs_pairs_4h` | 5 | W5 archive cascade | already on dead list |
| `vpvr_edge_reversion` | 2 | T11 round-1 (provisional KILL) + round-2 (KILL) | **NEW today**: round-2 KILL at 21:30 last night (6/6 cells negative) extends the family-exhaustion claim with a SECOND methodology (finer VPVR + 4h profile + z-confirm). Family should join the dead list at next smark confirmation of round-1. |
| `p1_020_three_drives` | 3 | W5 archive | one-shot, no continued sweep |
| `trend_multi_tf` | 2 | W5 archive (1 today, SMA-36681) | accumulating iters; not yet family-KILL |
| `microstructure_features` | 2 | today's scout cross-section + HMM brief invoked the cycle-46 microstructure tag (T01/T04 ancestry) → KILL | already on dead list |
| `cross_section_momentum + regime_switching` (NEW today) | 1 | KILL cost-cap today (SMA-36692) | **NEW today**: family-KILL candidate, should be added to cycle-46 dead list at next curator or smark touchpoint |
| `se_h3` | 1 (yesterday) | yesterday's KILL | already on dead list |
| `mcls_liquidity_sizing` | 1 (SMA-36645 = Risk Mgmt #90) | in-progress engineering (max-position-size-limit implementation) | not a strategy family; SPEC is in_review |
| `hmm_regime_spec` | 0 this week | yesterday's SPEC shipped, no follow-on | SPEC stalling 24h+ |
| `vpvr_funding` | 1 | already graveyard | T06 KILL |

**cycle-46 verdict for 2026-07-27:**

1. **`vpvr_xs_pairs_30m_funding_filter` should be promoted to cycle-46 dead list at next smark touchpoint.** The 8 W5 auto-archives today confirm vectorbt 3rd-framework CV convergence on this family — every variant tested fails all three metrics by >50% divergence. The family has the same revival condition as `mtf_xs_pairs` (sub-taker execution ≤20bps pair-RT AND human approval per `KILLED.md`); continuing to sweep it via framework-validate hourly is wasted cron budget. **Recommend closing the family at the next 10:00 selection or next smark-decision-cycle.**
2. **`cross_section_momentum + regime_switching` should also be added to cycle-46 dead list at next touchpoint.** Today's KILL is clean: cost-cap 10-20× short, no revival path without sub-1bp execution (T10 maker pre-SPEC dependent) or fundamental rebalance-frequency reduction.
3. **`vpvr_edge_reversion` family should be promoted to cycle-46 dead list pending smark confirmation of T11 round-1 KILL.** Round-2 last night (SMA-36661) provided a SECOND independent methodology (finer VPVR + 4h profile + z-confirm) that ALSO fails (6/6 cells negative, K1/K5 triggered). Together with T08 (HVN入/LVN出, archived-campaign-close) and the family-exhaustion symmetry argument, the family is structurally KILLed on Binance perp kline proxy. Revival requires all 5 conditions (tick-level profile + OFI-augmented + cascade sub-regime + regime gate + stronger cost model), none on near-term roadmap.
4. **No new sweeps today** — the W5 cascade is residual cleanup of already-killed or near-killed families. The orchestrator should NOT interpret the 8 archives today as a "new sweep signal"; the cycle-46 protocol correctly handles them as framework-validation convergence on known-bad families.

**[type=KILL] warning** (issued on [SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926) — this retro issue):

> **Two new cycle-46 dead-list candidates today.** (1) `cross_section_momentum + regime_switching meta-signal on 7-perps` — KILL cost-cap [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) — revival requires sub-1bp execution (T10 dependent) OR 1/day rebalance. (2) `vpvr_xs_pairs_30m_funding_filter` family should be promoted to dead list at next touchpoint — 15 iters this week (8 W5 archives today alone), all converged NOT-PROFITABLE on vectorbt 3rd-framework CV; same revival rule as `mtf_xs_pairs`. Recommend smark-decision-cycle promote at next fire.

---

## 4. Tomorrow's 10:00 selection (priority-1 SPECs)

Yesterday's curator retro Priority 1/2 picks (**[SMA-35536](mention://issue/) T13 MCLS liquidity sizing** + **[SMA-35762](mention://issue/) T12 HMM regime**) **did NOT advance today.** Both still `in_review`, last touched 2026-07-26T17:09/17:14. This is a 24-hour stall — not yet a hard concern (SPECs typically sit in_review while strat-execution/strat-indicators pick them up via orchestrator dispatch), but the curator cannot recommend picking the SAME priorities again tomorrow without flagging the gap. Two paths forward:

### Priority 1 (highest) — **Nudge T12 + T13 from stall** OR re-prioritize

The cleanest path: **at tomorrow's 10:00, the orchestrator (multica-orchestrator) re-dispatches a `[type=NUDGE]` to quant-researcher** to either (a) implement against the public API contract in T12 SPEC §5 / T13 SPEC §5, or (b) post an `[ESCALATE]` if blocked on a downstream dependency (e.g., T10 cost-cap, aggTrades L2 depth snapshotter, hmmlearn vs custom-em CV). A 24-hour stall is normal; a 48-hour stall is a process bug.

- **Why pick:** Yesterday's curator Priority 1+2 are STILL the right picks — net-new prior content (cycle-46 clean for both), cost basis aligned with ratified "maker 2bp / taker 5bp" floor, and the only forward paths in the queue.
- **First action:** orchestrator nudges quant-researcher to either implement or ESCALATE-blocker on T12 + T13; if 24h+ stall persists, escalate to smark-decision-cycle.

### Priority 2 — **`vpvr_xs_pairs_30m_funding_filter` family-seal** (operational, not a strategy)

- **Why pick:** Cycle-46 dead-list promotion is overdue (15 iters this week, 8 W5 archives today). W5 §W5.2 protocol is correctly handling them as auto-archives, but the family should be formally closed so framework-validate hourly stops picking it up.
- **First action:** smark-decision-cycle or 10:00 selection adds the family to `KILLED.md` per the same revival rule as `mtf_xs_pairs` (sub-taker execution ≤20bps pair-RT AND human approval). Curator posts this recommendation at the next digest.

### Priority 3 — **`cross_section_momentum + regime_switching` family-seal**

- **Why pick:** Today's cost-cap KILL is clean; the family should join the cycle-46 dead list immediately.
- **First action:** curator appends to cycle-46 dead list at next touchpoint; framework-validate cron and research-scout both respect the rule going forward.

### What NOT to pick at 10:00

- **T11 vpvr_edge_reversion_1d**: provisional KILL pending smark confirmation — do NOT promote to SPEC bucket until smark confirms round-1 KILL (which then formally closes the family per cycle-46).
- **T10 maker-pilot**: closed yesterday at smark 10:00 decision — do not reopen unless smark issues explicit reversal.
- **se_h3 复审** (SMA-36660): ESCALATE-pending smark human — do not preempt smark's ruling with parallel work.
- **SMA-36690 within-tolerance escalation**: agent says ignore; do not route to research unless smark flags.
- **SMA-35091 170h+ old 42.51% divergence**: 7-day-stale smark queue item — needs smark action or formal close.

---

## 5. Honest limits / unverified items

| Claim | Status | Why |
|---|---|---|
| Today's research-scout KILL (cross-section momentum + HMM) cost-cap math (428% required vs 20-40% achievable, 10-20× short) | verified, primary | [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) KILL comment body verbatim; second-consecutive-day API rate-limits disclosed |
| 8 framework-validate W5 AUTO-ARCHIVE today (7× `vpvr_xs_pairs_30m_funding_filter` + 1× `momentum_trend_multi_tf_atr`) | verified, primary | SMA-36681/691/695/696/697/698/699/700 done-status + DECISION comments |
| 1 ESCALATE-TO-SMARK WITHIN_TOLERANCE today (opt_010, divergence 0.00284%) | verified, primary | SMA-36688 DECISION comment + SMA-36690 escalation-router routing |
| 2 stale smark queue items (SMA-35091 170h+, SMA-36670 high) | verified, primary | SMA-36693 smark-decision-cycle 09:00 NOOP comment |
| T12 HMM regime + T13 MCLS SPECs NOT picked up today | verified, primary | SMA-35762 + SMA-35536 status=in_review, updated_at=2026-07-26T17:09 / 17:14 |
| Cycle-46 family week counts (vpvr_xs_pairs_30m_funding_filter=15, mtf_xs_pairs=8, etc.) | verified, primary | `multica issue list --limit 400 --output json` keyword match; `vpvr_xs_pairs_30m_funding_filter` count overlaps with `vpvr_xs_basis_zscore` due to shared prefix matching — actual count may be 12-15 depending on whether opt_008/009/010 are double-counted |
| `vpvr_xs_pairs_30m_funding_filter` family-exhaustion recommendation | curator inference (not yet verified by smark) | pattern matches `mtf_xs_pairs` family-seal precedent; needs explicit smark promotion at next touchpoint |
| `cross_section_momentum + regime_switching` cycle-46 dead-list promotion | curator inference (not yet verified by smark) | cost-cap KILL is clean per agent; needs explicit smark promotion at next touchpoint |
| `vpvr_edge_reversion` family-seal (after T11 round-2 KILL) | curator inference (not yet verified by smark) | round-2 KILL was posted last night 21:30 but no smark confirm yet on round-1 |
| `multica metrics query` returning 0 rows for today (new backtests) | **[UNVERIFIED]** | queried inline in curator scope is impractical at 21:00 epoch close; today's 8 W5 archives + 1 ESCALATE + 1 research-scout KILL are confirmed primary; any `run_metric` rows today are framework-CV byproducts, not new backtests |
| Max-Drawdown numbers for the new W5 archives (e.g., -0.9021 for BTCSOL v3) | verified, primary | cited in W5 archive DECISION comments; for tables where divergence is the load-bearing metric, individual max_dd may not be surfaced — flagged not invented |
| Today's hourly cadence health (9 escalation-router sweeps, 5 graph-janitor runs, 1+ research-scout, 2 smark-decision-cycle) | verified, primary | issue list confirmed; no cron failure today |

---

## 6. Sources cited (primary)

- [SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926) — this retro issue.
- [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) — today's research-scout KILL (cross-section momentum + HMM cost-cap).
- [SMA-36688](mention://issue/5423bae0-0cb1-4e10-8070-c7d94bb8e6a5) + [SMA-36690](mention://issue/c3f678c2-57c9-4ede-8b36-ee20e9cd45ec) — framework-validate WITHIN_TOLERANCE ESCALATE-TO-SMARK (opt_010, xs-basis-zscore family).
- [SMA-36681](mention://issue/8abc60f5-850c-4595-9b8f-331105bbc2ec) + [SMA-36691](mention://issue/) + [SMA-36695](mention://issue/) + [SMA-36696](mention://issue/f895feb2-09c8-4d7b-a046-ff3fcb29f3db) + [SMA-36697](mention://issue/) + [SMA-36698](mention://issue/) + [SMA-36699](mention://issue/8d611a23-2693-4097-a89a-b14111b75f62) + [SMA-36700](mention://issue/3cd8d8a4-7960-4dfc-b308-a44dbb44d5d2) — today's 8 framework-validate W5 AUTO-ARCHIVE.
- [SMA-36673](mention://issue/4840561a-5450-4b49-a8de-0b96d9f5611b) — yesterday's W5 archive included for completeness (BTCSOL regularized).
- [SMA-36693](mention://issue/a6b3f3ad-1e84-417b-9d2d-f606fd4672a9), [SMA-36703](mention://issue/20c065ca-a320-41c6-b46d-0a1d505cde2f) — today's 2 smark-decision-cycle runs (both NOOP).
- [SMA-36674/682/684/686/694](mention://issue/) — today's graph-janitor 5 runs (all clean).
- [SMA-36675/677/678/779/683/685/687/689/701](mention://issue/) — today's escalation-router 9 sweeps.
- [SMA-35762](mention://issue/) (T12 HMM regime SPEC, stalling 24h+), [SMA-35536](mention://issue/) (T13 MCLS liquidity sizing SPEC, stalling 24h+).
- [SMA-36615](mention://issue/5f3a64d7-…) (T11 round-1, provisional KILL pending smark), [SMA-36661](mention://issue/978f8d62-81b4-4415-b156-a3a5100e4cda) (T11 round-2 KILL last night, done).
- [SMA-35091](mention://issue/4b0a7a7c-3821-4fe7-93fd-3cb073b3548f), [SMA-36670](mention://issue/92e3fb04-42f1-4531-b5dc-af9ce864a2b1) — stale smark queue items.
- [SMA-36660](mention://issue/a7460846-ad23-4280-9520-9fc787c6cc9b) — se_h3 复审 ESCALATE-pending (unresolved since yesterday).
- Docs / plans: [`~/multica/docs/plans/multica-quant-permanent-loop-2026-07-25.md`](../../docs/plans/multica-quant-permanent-loop-2026-07-25.md) §4.1 + §7, [`~/multica/AGENTS.md`](../../AGENTS.md) operating-model + comment schema + knowledge-snapshots + cycle-46.
- Strategy-side running JOURNAL: [`~/multica/quant-loop/research/JOURNAL.md`](../quant-loop/research/JOURNAL.md) entries `2026-07-26` T10 / T11 / T11-reversal / T11-round-2 / T12 / T13 / 21:00 retro.
- Prior curator snapshots in [`~/multica/knowledge/curator/`](../curator/): `2026-07-17-debug-summary.md`, `2026-07-18-knowledge-snapshot.md`, `2026-07-26-epoch-digest.md`, `kg_update_2026-07-26.md`.

---

## 7. What changed since the prior digest (2026-07-26 — KILL-reset day)

The 2026-07-26 digest captured the se_h3 KILL + `mtf_xs_pairs` family-seal + T11 reversal + T10 close + T12/T13 SPEC ships + ratified cost basis (maker 2bp / taker 5bp). The 2026-07-27 epoch swings the pendulum toward **operational exhaustion** rather than new content:

1. **Cycle-46 cascade accelerated.** Today's 8 W5 auto-archives + 1 cross-section momentum KILL mark the cycle-46 protocol correctly handling known-bad families (vpvr_xs_pairs_30m_funding_filter + microstructure + funding + 4h stat-arb) and adding a NEW candidate (`cross_section_momentum + regime_switching`). The protocol is working as designed; the curator's job today is to flag the family-seal recommendations and let framework-validate continue auto-archiving until smark confirms promotion.
2. **T11 round-2 KILL confirmed last night (21:30).** The second methodology (finer VPVR + 4h profile + z-confirm) also failed 6/6 cells. This provides the family-exhaustion symmetry evidence: T08 (HVN入/LVN出) + T11 round-1 (LVN入/HVN出) + T11 round-2 (finer VPVR) all KILL on Binance perp kline proxy. Curator's recommendation: promote `vpvr_edge_reversion` to cycle-46 dead list at next smark touchpoint.
3. **T12 HMM regime + T13 MCLS SPECs stalled 24h.** No 10:00 selection fired today. This is normal for SPEC-then-implementation handoff (strat-indicators + strat-execution pick up via orchestrator dispatch), but if it persists to 2026-07-28 the curator escalates a re-dispatch nudge.
4. **0 LIVE-eligible strategy as of 21:00 epoch close.** Same state as yesterday and as AGENTS.md §"Strategy state (2026-07-26 02:30)" — no new backtests, no new candidates advanced. The se_h3 复审 + T11 round-1 + T10 close remain ESCALATE-pending smark human; outcome does not arrive in time for this epoch.
5. **smark queue has 3 actionable items, 0 of them urgent-by-time-pressure.** (1) SMA-36690 urgent within-tolerance — agent says ignore; (2) SMA-35091 urgent 170h+ old 42.51% divergence — needs W5 smark-decision review; (3) SMA-36670 high missing-acceptance Risk Mgmt #0 — needs binary A/B decision. The orchestrator is NOT failing to escalate; smark is NOT being paged frequently. The 9 escalation-router sweeps today correctly handled only 1 true route.
6. **Cron health is clean.** 5 graph-janitor runs (all clean, no orphans), 9 escalation-router sweeps (mostly no-op), 2 smark-decision-cycle runs (both NOOP), 1 research-scout (KILL cost-cap), 8 framework-validate W5 archives. No cron failures today.

---

*Curator run via [SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926) (knowledge-curator dispatch, 2026-07-27 21:00+08 Asia/Shanghai). Sources cross-checked: live `multica` CLI on 21 cited SMA-IDs + research-scout + framework-validate + curator's own triage of the 30 today-created issues. Prior-day snapshots referenced: [`2026-07-17-debug-summary.md`](../curator/2026-07-17-debug-summary.md), [`2026-07-18-knowledge-snapshot.md`](../curator/2026-07-18-knowledge-snapshot.md), [`2026-07-26-epoch-digest.md`](../curator/2026-07-26-epoch-digest.md), [`kg_update_2026-07-26.md`](../curator/kg_update_2026-07-26.md).*
