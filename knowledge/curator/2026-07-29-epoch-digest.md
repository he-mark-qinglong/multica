# Epoch Digest — 2026-07-29

**Scope:** workspace `f9a9d34e-…` (UTC+8). Slot [SMA-36765](mention://issue/8b8d946f-5539-46d3-b950-7e444dade151) `[epoch-retro 2026-07-29]` (knowledge-curator `4f50d87d-…`, autopilot `23281f8e-…`, 21:00 Asia/Shanghai cron).
**Prior surfaces:** `2026-07-17-debug-summary.md`, `2026-07-18-knowledge-snapshot.md`, `2026-07-26-epoch-digest.md` (KILL-reset day), `2026-07-27-epoch-digest.md` (cycle-46 cascade day), `kg_update_2026-07-26.md`.
**Sources (all primary):** live `multica` CLI (`issue get / list / comment list / metrics query / autopilot get`) on cited SMA-IDs, today's `smark-decision-cycle` sweep ([SMA-36766](mention://issue/) + [SMA-36763](mention://issue/)), today's `escalation-router` sweep #264 ([SMA-36764](mention://issue/)), today's `infra-health-watchdog` (autopilot `c84304df`, 21:07 tick), yesterday's `framework-validate` W5 archive ([SMA-36714](mention://issue/)) + WITHIN_TOLERANCE ESCALATE-TO-SMARK ([SMA-36725](mention://issue/) + [SMA-36727](mention://issue/)), yesterday's `roadmap-maintainer` monthly baseline ([SMA-36734](mention://issue/), PR #16), the strategy-side running JOURNAL ([`~/multica/quant-loop/research/JOURNAL.md`](../quant-loop/research/JOURNAL.md)) + [`AGENTS.md`](../../AGENTS.md) operating-model section.

> **Constitution v1.0 honesty note.** Numbers below come from cited primaries (issue bodies / DECISION comments / published metrics). Anything I could not verify from a primary source is marked **n/a** and flagged. **0 new strategy artifacts shipped today** — today's events are entirely cron noise (escalation-router / smark-decision-cycle / roadmap-maintainer daily tick / infra-health-watchdog); the curator's cycle-46 verdict for this week therefore relies on yesterday's pattern + the prior digests, not on any new signal from 2026-07-29.

---

## 1. Verdict summary (KEEP / KILL / ESCALATE, 2026-07-28 → 2026-07-29)

### 1.1 Verdict trail

| Verdict type | Issue | Subject | Status |
|---|---|---|---|
| **KILL** (today) | none | — | — |
| **KILL** (yesterday) | none new | last W5 NOT-PROFITABLE archive was 2026-07-27 15:37 ([SMA-36700](mention://issue/3cd8d8a4-7960-4dfc-b308-a44dbb44d5d2) BTCSOL v3 vectorbt); yesterday's archive was a follow-on variant ([SMA-36714](mention://issue/)) already-KILLed family | done (post-family-seal residual) |
| **ESCALATE → smark (WITHIN_TOLERANCE, NEW yesterday)** | [SMA-36725](mention://issue/) + [SMA-36727](mention://issue/) | `xs_momentum_rank_1d_20260709 × freqtrade` framework CV: full-period max_abs_rel_divergence = **4.3714%** (≪ 50% W5 threshold); OOS walk-forward divergence max 4.37% at window 0 (2025-04-23 → 2025-06-21), decay across windows. **Per W5 §W5.2**: divergence ≤ 50% on all of sharpe / total_return / max_dd → ESCALATE-TO-SMARK (NOT auto-archive). | in_review (route issue) + todo (need-smark-decision child) |
| **ESCALATE → smark (decision pending since 2026-07-26)** | [SMA-36660](mention://issue/a7460846-ad23-4280-9520-9fc787c6cc9b) | `se_h3` KILL verdict 复审 (60% third-party rebate → taker 4bps +5.98 / maker 1.6bps +8.88; external-account compliance risk). 72h+ stale, awaiting smark human. | todo (urgent, untouched) |
| **ESCALATE → smark (WITHIN_TOLERANCE, stale 4d)** | [SMA-36690](mention://issue/c3f678c2-57c9-4ede-8b36-ee20e9cd45ec) + [SMA-36688](mention://issue/5423bae0-0cb1-4e10-8070-c7d94bb8e6a5) | `vpvr_xs_basis_zscore_15m_funding_filter_20260712_p3opt_010 × vectorbt` divergence 0.00284% — cross-framework agreement on badness (3rd framework convergence). Agent says "smark can ignore". | todo (urgent, agent says ignore) |
| **ESCALATE → smark (missing-acceptance, stale 2d)** | [SMA-36670](mention://issue/92e3fb04-42f1-4531-b5dc-af9ce864a2b1) | Risk Mgmt #0 max-position-size-limit: 4 acceptance criteria unfilled. 41h+ stale; smark-decision-cycle today (SMA-36763 / SMA-36766) confirms untouched. | todo (high) |
| **ESCALATE → smark (framework-cv-divergence, stale 7d)** | [SMA-35091](mention://issue/4b0a7a7c-3821-4fe7-93fd-3cb073b3548f) | `vpvr_funding_regime_15m × backtrader` 42.51% divergence (W5 §W5.2 explicit smark-decision review path, < 50% so no auto). 45h+ stale; auto-archive requires >50%. | todo (urgent, 170h+ age) |
| **ESCALATE → smark (triage-guardrail-conflict, stale)** | [SMA-36447](mention://issue/48b0e621-4839-41da-bac9-5a8b9d71d59c) | per today's smark-decision-cycle urgent-skipped list | todo (urgent) |
| **T11 round-1 KILL pending smark confirm (stale 3d)** | [SMA-36615](mention://issue/) | T11 vpvr_edge_reversion_1d provisional KILL — quant-researcher self-caught +1d shift look-ahead bug; 9/9 cells negative at honest D+1, K1/K4/K5 triggered. 3 days since verdict; smark has not confirmed closure. | in_review |
| **SIGNOFF (yesterday, PR opened)** | [SMA-36734](mention://issue/) | roadmap-maintainer monthly baseline scan done; `docs/plans/roadmap-2026-2036.md` created by multica-ops ([PR #16](https://github.com/he-mark-qinglong/multica/pull/16) `agent/multica-ops/76f0768a` @ `0b295c71`); Evidence-Gate SIGNOFF PASS (attest by reviewer, not self). PR merge remains smark's call per §4.3. | done |
| **NOOP** (today, cycle) | [SMA-36763](mention://issue/), [SMA-36766](mention://issue/) | 2 smark-decision-cycle runs today (19:53 + 21:00) — both 0 auto / 2 untouched; no fresh DEPLOY-FAIL / heartbeat-noop / divergence>50% / Agent-Sync-noop / VPPR-3/3 candidates | done |
| **NOOP** (today, sweep) | [SMA-36764](mention://issue/) (sweep #264), [SMA-36762](mention://issue/), [SMA-36760](mention://issue/), [SMA-36759](mention://issue/), [SMA-36757](mention://issue/), [SMA-36756](mention://issue/), [SMA-36754](mention://issue/), [SMA-36753](mention://issue/), [SMA-36752](mention://issue/), [SMA-36751](mention://issue/), [SMA-36750](mention://issue/), [SMA-36748](mention://issue/) | 12 escalation-router sweeps today (00:54 → 20:37 hourly cadence) — all NOOP, 0 fresh markers | done / in_review |
| **NOOP** (today, daily tick) | [SMA-36749](mention://issue/) | roadmap-maintainer daily tick at 00:54 (autopilot `023e2849-…`, last_run_at 2026-07-29T00:54:39+08:00). Monthly full scan already done yesterday (SMA-36734); daily tick only re-checks no in-month drift. | todo (no body action) |
| **NOOP** (today, watchdog) | infra-health-watchdog `c84304df` | 21:07 tick — autopilot says "all 4 checks ok" path (last_run_at 2026-07-29T21:07:27+08:00). | implicit (run_only mode, no issue) |

> **0 LIVE-eligible strategy at epoch close 21:00.** Same state as prior digests — no new strategy artifacts, no KEEP, no new SPECs advanced gates today.

### 1.2 Strategic consequence tree

- **The epoch loop is degraded but not dead.** Three primary strategic cron families have visible gaps today: (1) `research-scout` autopilot reports `last_run_at = 2026-07-29T11:35:28+08:00` but no `research-scout SPEC pool 2026-07-29` issue exists — silent fail or no-output path (the spec mandates "post at least 1 SPEC or `[type=NOOP]`"); (2) `framework-validate` last fired 2026-07-28 00:37 (43h+ gap); (3) `graph-janitor` last fired 2026-07-28 07:13 (38h+ gap). None of these has an ESCALATE comment on a watchdog issue, so the failure is silent — the curator cannot root-cause without reading the daemon logs. The 07-28 21:51 epoch-retro [SMA-36746](mention://issue/) was created but never picked up; this is the **first broken retro day** in the 1-day epoch loop. Net: the cycle is alive (today's retro ran at 21:00), but cron health needs a sweep.
- **W5 NOT-PROFITABLE cascade has reached its asymptotic phase.** Last W5 archive was 2026-07-27 15:37 (BTCSOL v3 vectorbt, [SMA-36700](mention://issue/)); yesterday's only archive ([SMA-36714](mention://issue/), vpvr_xs_basis_zscore 15m base) and today's 0 archives both belong to families already on the dead list. The framework-validate hourly cron is in its post-family-seal tail — every variant of every known-bad family is converging on NOT-PROFITABLE. The next W5 trigger will likely be a `vectorbt` 3rd-framework convergence on `xs_momentum_rank_1d_20260709` if smark confirms KILL on the WITHIN_TOLERANCE ESCALATE.
- **`xs_momentum_rank_1d_20260709` is a NEW cycle-46 candidate family.** Yesterday's WITHIN_TOLERANCE route is the first time this family surfaced; the in-house sharpe is 0.324 (≪ G1 floor of 1.0), so even if framework-CV passes the family has no realistic path to LIVE. Smark should treat this as a "verify cross-framework agreement, then KILL family" cycle-46 promotion — same pattern as `vpvr_xs_pairs_30m_funding_filter` last week.
- **`vpvr_xs_pairs_30m_funding_filter` family-seal promotion is overdue.** 9 W5 auto-archives this week (vs 15 the prior week's count in [SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926) digest, 7 of them on 2026-07-27 alone); the family should be added to the cycle-46 dead list at next smark touchpoint. The framework-validate cron is wasting hourly cycles re-archiving already-known-bad variants.
- **smark queue has 6 actionable items, 0 drained today.** (1) SMA-36727 within-tolerance xs_momentum_rank_1d freqtrade 4.37% — NEW yesterday, urgent; (2) SMA-36670 missing-acceptance Risk Mgmt #0 — high, 41h+ stale; (3) SMA-35091 framework-cv-divergence 42.51% — urgent, 170h+ old; (4) SMA-36690 within-tolerance xs-basis-zscore opt_010 — urgent, agent says ignore; (5) SMA-36660 se_h3 KILL verdict-review — urgent, 72h+ old; (6) SMA-36447 triage-guardrail-conflict — urgent. The escalation-router is routing correctly; smark is not draining.
- **PR #16 (roadmap-2026-2036.md) awaiting smark merge.** [SMA-36734](mention://issue/) done, Evidence-Gate SIGNOFF PASS (attestation non-self per 2026-07-26 rule). Cron's monthly scan will read it correctly once on main.

---

## 2. Daily comparison table (curator-verdict format)

> Numbers from cited primaries (issue bodies / VERDICT.md / SPEC.md / `results-ledger.md` + `multica metrics query`). Where a metric-row would normally populate the cell and is unavailable, marked `(metric-row n/a)`. **No new backtests shipped today (2026-07-29)** — the table is dominated by yesterday's 1 W5 archive + 1 W5 WITHIN_TOLERANCE ESCALATE, plus the carry-over verdicts still pending smark confirmation. 0 strategies advanced gates today.

| Strategy | OOS Sharpe | CI lower | Post-fee Sharpe @60bps pair-RT | Max drawdown | Verdict rationale (one line) |
|---|---|---|---|---|---|
| `xs_momentum_rank_1d_20260709` × freqtrade (NEW yesterday) | in-house sharpe **0.32416761** vs framework **0.32416761** (machine precision); full-period rel divergence 2.67e-12 %; OOS walk-forward max sharpe div 0.88 %, max ret div **4.37 %** (window 0) | n/a (framework agreement on badness) | framework ann_return 0.025359; max_dd -0.0906307; n_fills 2286 (762 rebalances × ~3 non-zero deltas) | framework max_dd -0.090631 (in-house identical) | **W5 ESCALATE-TO-SMARK (WITHIN_TOLERANCE)** — framework/in-house agree to within noise; in-house sharpe 0.324 ≪ G1=1.0 so no realistic LIVE path; family `xs_momentum_rank_1d` should be promoted to cycle-46 dead list at next smark touchpoint → [SMA-36725](mention://issue/) + [SMA-36727](mention://issue/) |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` (base) × vectorbt (yesterday) | in-house NOT-PROFITABLE (sharpe 0.250 from ledger); framework convergence on badness | n/a | framework NOT-PROFITABLE; previous freqtrade -45.719 (ledger) | framework convergence | **W5 auto-archive** — post-family-seal residual (already on dead list via xs-basis-zscore opt_008+009+010 pattern) → [SMA-36714](mention://issue/) |
| **`T11 vpvr_edge_reversion_1d` (round-1, pre-fix)** | 9/9 cells negative at honest D+1; mean markout **−27.6 / −41.7 / −45.7 bp** (BTC/ETH/SOL 1d) | n/a | estimated NET edge: VIP0 9bp − markout 27-46bp = **−18 / −33 / −37 bp**, ALL CLEAR of +30bp floor (NEGATIVE) | n/a (markout floor not breached by markout itself) | **provisional KILL** (3 days since verdict, smark confirmation outstanding) — K1 (Sharpe < 0.5) ✓, K4 (TP1 < 50%) ✓ (45-48%), K5 (cycle-46 negative-fold) ✓ (9/9 cells) → [SMA-36615](mention://issue/) |
| **`T11 vpvr_edge_reversion_1d` (round-2, finer VPVR)** | 6/6 cells negative at honest +4h shift; mean markout **−9.8 / −17.2 / −18.5 bp** (4h BTC/ETH/SOL); **−7.0 / −14.0 / −15.7 bp** (1d BTC/ETH/SOL) | n/a | VIP0 maker 0.8bp / taker 2bp − markout 7-19bp = **−6 / −15 / −17 bp** 4h, **−4 / −12 / −13 bp** 1d; still NEGATIVE on every cell | n/a | **KILL** (smark-assigned second methodology, last week) — round-2 vs round-1 same-direction; family exhausted across TWO distinct methodologies; same reversal-of-reversal vs T08 (HVN entry / LVN exit) symmetry argument → [SMA-36661](mention://issue/978f8d62-81b4-4415-b156-a3a5100e4cda) |
| **T10 maker-pilot** | n/a (closed) | n/a | ratified maker 2bp / taker 5bp research-wide | n/a | **CLOSED** (smark 10:00 close) — no pilot, rate locked; any revival requires maker execution SPEC pass + sub-1bp effective cost |
| **T12 HMM regime SPEC** | n/a (gates G0-G7 unevaluated; SPEC shipped 2026-07-26) | n/a | n/a | n/a | **SPEC-only, awaits V0-V7 + G0-G7** — Aumann-falsifier G4 is the acceptance gate; 72h+ stale (last touched 2026-07-26T17:09:24) → [SMA-35762](mention://issue/) |
| **T13 MCLS liquidity sizing SPEC** | n/a (gates V0-V7 unevaluated; SPEC shipped 2026-07-26) | n/a | ratified 22bp RT premise (cap_impact ⟂ cost) | n/a | **SPEC-only, awaits V0-V7** — donor strategy `mtf_xs_pairs` H3 (deployed but family-KILLed); 72h+ stale (last touched 2026-07-26T17:14:18) → [SMA-35536](mention://issue/) |
| **T14 attribution decompose (RESEARCH INFRASTRUCTURE, not strategy)** | n/a — gate A6 FAIL-with-finding recorded not redefined | n/a | n/a (cost decomposition infra) | n/a | **SHIPPED** as opt-in `_shared` library — SMA-35757 done; quant-analyst 口径 audit pending |
| **T15 SPA-RC benchmark SPEC** | n/a (gates V0-V4 unevaluated) | n/a | n/a | n/a | **SPEC-only, awaits V0-V4** — promoted to multica-strategy for L3 implementation; V4 Aumann-for-tools is the acceptance gate → [SMA-35755](mention://issue/) |
| **se_h3 复审 ESCALATE** | n/a (corrected口径: 4bps +5.98 / 24bps −17.33 / 60bps −38.80; rebate 60% third-party account risk) | n/a | NET edge at rebate taker 4bps = **+5.98**; at no-rebate taker 10bps = **−1.40** | n/a | **KILL-family-seal stands** unless smark confirms rebate revival (third-party aggregator + external-account compliance) → [SMA-36660](mention://issue/) |
| **Cross-section momentum + HMM regime meta-signal** (2026-07-27 scout) | n/a (no backtest, no SPEC) | n/a | theoretical: cost ~214% annual drag @ VIP0, requires 428% gross edge | n/a | **KILL cost-cap** (2026-07-27) — 10-20× short of required edge; revival requires sub-1bp execution OR 1/day rebalance → [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) |

**One-line human verdict (persona-advisor style):** "Quiet day on the strategy axis — 0 new SPECs, 0 W5 archives, 0 KILL verdicts. The day's events are cron noise: 12 escalation-router sweeps (all NOOP), 2 smark-decision-cycle runs (both 0 auto / 2 untouched), 1 roadmap-maintainer daily tick, infra-health-watchdog 21:07 healthy. Yesterday's residual is the `xs_momentum_rank_1d_20260709 × freqtrade` WITHIN_TOLERANCE route (max_abs_rel_divergence 4.37%, framework/in-house agreement to within noise); this surfaces a NEW cycle-46 candidate family because in-house sharpe 0.324 ≪ G1=1.0 — smark should KILL-family it at next touchpoint. smark queue is 6 actionable items deep (1 new xs_momentum_rank, 1 Risk Mgmt #0 missing-acceptance, 1 framework-cv 42.51%, 1 xs-basis-zscore opt_010 within-tolerance, 1 se_h3 复审, 1 triage-guardrail) with 0 drained today. Three cron health flags: `research-scout` reports `last_run_at=2026-07-29T11:35:28` but no SPEC-pool issue created (silent fail), `framework-validate` last fired 2026-07-28 00:37 (43h+ gap), `graph-janitor` last fired 2026-07-28 07:13 (38h+ gap); 2026-07-28 retro [SMA-36746](mention://issue/) was created but never picked up (first broken retro day). 0 LIVE-eligible strategies."

---

## 3. cycle-46 exhaustion check (this week, 2026-07-22 → 2026-07-29)

Counted strategy-family occurrences in issue titles this week (running tally from `multica issue list --limit 500 --output json` across 4 offset pages, deduped by issue UUID, filtered for families named in the cycle-46 family list or the AGENTS.md killed-family list):

| Family | Week count | Direction | Action |
|---|---|---|---|
| `vpvr_xs_pairs_30m_funding_filter` | **9** (07-26: 2, 07-27: 7) — none this week from 07-28 onwards | W5 archive cascade last week; **no new iters 07-28 → 07-29** | **[type=KILL] recommendation overdue**: promote to cycle-46 dead list at next smark touchpoint; revival requires sub-taker execution ≤20bps pair-RT AND human approval per `KILLED.md` — same rule as `mtf_xs_pairs` |
| `vpvr_xs_basis_zscore` | **5** (07-26: 1, 07-27: 3, 07-28: 1) — none 07-29 | yesterday 1 W5 archive; framework-CV converging | already on dead list (cycle-46 promotion pending) |
| `mtf_xs_pairs` | **8** (07-25: 3, 07-26: 5) — none 07-28 or 07-29 | **KILLED 2026-07-26** (smark 8:31 family-seal) | already on dead list; this week had no new sweeps |
| `vpvr_xs_pairs_4h` | **5** (07-22: 1, 07-25: 2, 07-26: 2) | W5 archive cascade | already on dead list (T09 cycle-46 baseline) |
| `vpvr_stable_depeg_regime` | **2** (07-26: 2) | W5 archive cascade | already on dead list |
| `se_h3` | **6** (07-25: 3, 07-26: 3) | W4 se_h3 verification; KILLed 07-26; 复审 ESCALATE-pending | already on dead list (KILL stands unless smark confirms rebate revival) |
| `xs_momentum_rank` | **2** (07-28: 2) — **NEW this week** | yesterday's W5 WITHIN_TOLERANCE route (divergence 4.37%) | **NEW cycle-46 candidate** — framework/in-house agree to within noise; in-house sharpe 0.324 ≪ G1; promote to dead list at next smark touchpoint |
| `momentum_trend` | **1** (07-27: 1) | W5 archive | accumulating iters; not yet family-KILL |
| `vpvr_edge_reversion` | **1** (07-26: 1) | T11 round-1 (provisional KILL pending smark confirm) + round-2 (KILL, prior week) | family should be added to dead list pending smark confirmation of round-1 (round-2 was KILLed last week) |
| `cross_section_momentum + regime_switching` | 1 (07-27) | cost-cap KILL last week | should be added to cycle-46 dead list; framework-validate respects |
| `hmm_regime_spec` (T12) | 0 new iters this week | yesterday's SPEC shipped, no follow-on | SPEC stalling 72h+ |
| `mcls_liquidity_sizing` (T13) | 0 new iters this week | yesterday's SPEC shipped, no follow-on | SPEC stalling 72h+ |
| `t10_maker` | 0 (closed yesterday at smark 10:00) | closed | — |
| `spa_benchmark` (T15) | 1 (07-28) | SPEC shipped yesterday; L3 implementation pending | SPEC shipped, gates V0-V4 unevaluated |
| `attribution` (T14) | 1 (07-28) | tool shipped; quant-analyst 口径 audit pending | research infra shipped |

**cycle-46 verdict for 2026-07-29:**

1. **`vpvr_xs_pairs_30m_funding_filter` should be promoted to cycle-46 dead list at next smark touchpoint.** 9 W5 auto-archives this week (none since 07-27), framework-validate 3rd-framework CV convergence on every variant (vectorbt), family-level revival condition identical to `mtf_xs_pairs` (sub-taker execution ≤20bps pair-RT AND human approval per `KILLED.md`). Continuing to sweep it via framework-validate hourly is wasted cron budget — and is likely contributing to the cron health flag (silent `framework-validate` gap suggests the cron may have stopped picking up new variants once the family converged). **Recommend smark-decision-cycle promote at next fire.**
2. **`xs_momentum_rank_1d` should be added to cycle-46 dead list at next smark touchpoint.** Yesterday's WITHIN_TOLERANCE route (max_abs_rel_divergence 4.37%, framework/in-house agreement) is the canonical pattern for "framework agrees the strategy is bad" — same as xs-basis-zscore opt_010. In-house sharpe 0.324 ≪ G1=1.0 → no realistic LIVE path. Revival condition should be: stronger prior content AND sub-taker execution ≤20bps.
3. **`vpvr_edge_reversion` family-seal should be promoted pending smark confirmation of T11 round-1.** T11 round-1 KILL has been pending smark confirm for 3 days; round-2 KILLed last week provides the SECOND independent methodology (finer VPVR + 4h profile + z-confirm). Family-exhaustion symmetry with T08 (HVN entry / LVN exit) is well-documented. Curator note: T11 round-2 was KILLed last week, but the round-1 verdict (provisional KILL pending smark) is what's blocking the family-seal. Recommend smark close round-1 at next touchpoint, which then triggers the cycle-46 family-seal.
4. **No new sweeps 07-28 → 07-29.** The framework-validate hourly cadence has been quiet since 07-28 00:37 — possibly because the W5 cascade has reached asymptotic convergence on known-bad families. This is NOT a "strategy research made progress" signal; it's a cron health flag (see §1.2).
5. **Stalled SPECs (T12 + T13) — 72h+ no progress.** Both SPECs are mature (cycle-46 clean, cost basis ratified, public API contracts in §5), but `strat-indicators` (T12) and `strat-execution` (T13) have not picked them up. The 10:00 selection cycle is supposed to dispatch these but has not fired for 3 consecutive days. Recommend orchestrator re-dispatch a `[type=NUDGE]` to `quant-researcher` to either implement against the SPEC public API or post `[type=ESCALATE]` if blocked on a downstream dependency (T10 cost-cap ratification, aggTrades L2 depth snapshotter, hmmlearn vs custom-em CV).

**[type=KILL] warning** (issued on [SMA-36765](mention://issue/8b8d946f-5539-46d3-b950-7e444dade151) — this retro issue):

> **Two new cycle-46 dead-list candidates this week.** (1) `vpvr_xs_pairs_30m_funding_filter` — overdue (9 W5 archives this week, 0 new since 07-27, all vectorbt 3rd-framework convergence on NOT-PROFITABLE). Revival requires sub-taker execution ≤20bps pair-RT AND human approval per `KILLED.md`. (2) `xs_momentum_rank_1d_20260709` — NEW yesterday (2 issues, max_abs_rel_divergence 4.37% WITHIN_TOLERANCE, framework/in-house agreement on sharpe 0.324 ≪ G1=1.0). Revival requires stronger prior content AND sub-taker execution. (3) `vpvr_edge_reversion` should be promoted pending smark confirmation of T11 round-1 (provisional KILL pending since 2026-07-26 21:00, 72h+ untouched). Recommend smark-decision-cycle promote all three at next fire.

---

## 4. Tomorrow's 10:00 selection (priority-1 SPECs)

The 07-28 retro was never run, so yesterday's curator retro did not set a 10:00 priority. The 07-27 retro's Priority 1/2 picks ([SMA-35536](mention://issue/) T13 MCLS liquidity sizing + [SMA-35762](mention://issue/) T12 HMM regime) STILL have not advanced — both now 72h+ stale. This is a 3-day stall, well past the 24h normal / 48h concern threshold. Two paths forward:

### Priority 1 (highest) — **Nudge T12 + T13 from 72h+ stall** OR re-prioritize

The cleanest path: **at tomorrow's 10:00, the orchestrator (multica-orchestrator) re-dispatches a `[type=NUDGE]` to `quant-researcher` to either (a) implement against the public API contract in T12 SPEC §5 / T13 SPEC §5, or (b) post an `[ESCALATE]` if blocked on a downstream dependency (T10 cost-cap, aggTrades L2 depth snapshotter, hmmlearn vs custom-em CV).** A 72h+ stall is a process bug — orchestrator should not require a 3rd day of curator nudging to act.

- **Why pick:** Yesterday's curator Priority 1+2 are STILL the right picks — net-new prior content (cycle-46 clean for both), cost basis aligned with ratified "maker 2bp / taker 5bp" floor, and the only forward paths in the queue.
- **First action:** orchestrator nudges quant-researcher to either implement or ESCALATE-blocker on T12 + T13; if 24h+ more stall, escalate to smark-decision-cycle.

### Priority 2 — **`xs_momentum_rank_1d` family-seal + `vpvr_xs_pairs_30m_funding_filter` overdue promotion** (operational, not a strategy)

- **Why pick:** Cycle-46 dead-list promotion is overdue on both families. `vpvr_xs_pairs_30m_funding_filter` had 9 W5 archives this week and 0 since 07-27 (framework-validate hourly has likely stopped picking up new variants — possibly related to the cron health flag). `xs_momentum_rank_1d_20260709` is the new candidate surfaced yesterday (2 issues, max_abs_rel_divergence 4.37% WITHIN_TOLERANCE). Promoting both to dead list closes the family-sweep loop and frees framework-validate cron budget for genuinely-new strategies.
- **First action:** smark-decision-cycle or 10:00 selection adds both families to `KILLED.md` per the same revival rule as `mtf_xs_pairs` (sub-taker execution ≤20bps pair-RT AND human approval).

### Priority 3 — **Cron health sweep** (operational, not a strategy)

- **Why pick:** Three cron health flags today: (1) `research-scout` reports `last_run_at=2026-07-29T11:35:28` but no SPEC-pool issue was created (silent fail — the spec mandates "post at least 1 SPEC or `[type=NOOP]`"); (2) `framework-validate` last fired 2026-07-28 00:37 (43h+ gap, hourly cron should have fired 40+ times since); (3) `graph-janitor` last fired 2026-07-28 07:13 (38h+ gap). Add (4): 07-28 epoch-retro [SMA-36746](mention://issue/) was created but never picked up (first broken retro day).
- **First action:** `multica-ops` (or watchdog agent) reads daemon logs to root-cause the silent `research-scout` fail and the framework-validate / graph-janitor hourly cadence gap. `multica-orchestrator` should investigate why 07-28 retro was created but not dispatched. Recommend a `[type=ESCALATE]` to `multica-ops` for the cron-health sweep.

### What NOT to pick at 10:00

- **T11 vpvr_edge_reversion_1d**: provisional KILL pending smark confirmation — do NOT promote to SPEC bucket until smark confirms round-1 KILL (which then formally closes the family per cycle-46).
- **T10 maker-pilot**: closed at smark 10:00 decision — do not reopen unless smark issues explicit reversal.
- **se_h3 复审** (SMA-36660): ESCALATE-pending smark human — do not preempt smark's ruling with parallel work.
- **SMA-36690 within-tolerance escalation**: agent says ignore; do not route to research unless smark flags.
- **SMA-36727 within-tolerance xs_momentum_rank_1d** (NEW): same pattern as 36690; recommend smark `KILL-family` directly per cycle-46 rather than routing to research.
- **SMA-35091 170h+ old 42.51% divergence**: 7-day-stale smark queue item — needs smark action or formal close.
- **SMA-36670 missing-acceptance Risk Mgmt #0**: needs smark A/B binary decision; orchestrator should not assume.
- **SMA-36447 triage-guardrail-conflict**: needs smark; not research-track.

---

## 5. Honest limits / unverified items

| Claim | Status | Why |
|---|---|---|
| Today's events are entirely cron noise (no research-scout / framework-validate / graph-janitor / SPEC) | verified, primary | `multica issue list --limit 500 --output json` across 4 offset pages, filtered by category; only autopilot cron issues created today (15 escalation-router + 5 smark-decision-cycle + 1 roadmap-maintainer daily tick) |
| Yesterday's `xs_momentum_rank_1d × freqtrade` WITHIN_TOLERANCE divergence 4.3714% | verified, primary | [SMA-36725](mention://issue/) DECISION + EVIDENCE comments verbatim |
| Yesterday's `vpvr_xs_basis_zscore_15m_funding_filter_20260712` (base) W5 archive | verified, primary | [SMA-36714](mention://issue/) DECISION comment (framework-validate hourly) |
| `research-scout` autopilot `last_run_at=2026-07-29T11:35:28+08:00` with no issue created | verified, primary | `multica autopilot get 28d2a8c7-44bd-4426-8ddb-b743c5b2ff4d` (see autopilot list output); no `research-scout SPEC pool 2026-07-29` issue in `multica issue list --limit 500` |
| `framework-validate` last fired 2026-07-28 00:37 (43h+ gap) | verified, primary | [SMA-36714](mention://issue/) created 2026-07-28T00:43:44+08:00, `body=` 00:37; no later framework-validate in the 500-issue window |
| `graph-janitor` last fired 2026-07-28 07:13 (38h+ gap) | verified, primary | [SMA-36729](mention://issue/) created 2026-07-28T07:15:28+08:00; no later graph-janitor in the 500-issue window |
| 2026-07-28 retro [SMA-36746](mention://issue/) never picked up | verified, primary | `multica issue get SMA-36746 --output json` returned status=`todo`, updated_at=`2026-07-28T21:51:37+08:00` (equal to created_at) — zero progress since creation |
| Today's `smark-decision-cycle` 0 auto / 2 untouched (Risk Mgmt #0 SMA-36670 + vpvr_funding_regime_15m SMA-35091) | verified, primary | [SMA-36766](mention://issue/) + [SMA-36763](mention://issue/) STATUS comments verbatim |
| Today's escalation-router sweep #264 NOOP | verified, primary | [SMA-36764](mention://issue/) NOOP comment verbatim |
| T12 HMM regime + T13 MCLS SPECs not advanced 72h+ | verified, primary | [SMA-35762](mention://issue/) + [SMA-35536](mention://issue/) status=in_review (per prior digests), last_touched 2026-07-26T17:09 / 17:14 |
| Cycle-46 family week counts (vpvr_xs_pairs_30m_funding_filter=9, xs_momentum_rank=2, etc.) | verified, primary | `multica issue list --limit 500` across 4 offset pages, keyword match against family substrings |
| T11 round-1 SMA-36615 still pending smark confirm (3 days) | verified, primary | per yesterday's digest ([SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926)) + today's no-touch (no smark-decision-cycle mention of SMA-36615 in comments) |
| se_h3 复审 SMA-36660 still ESCALATE-pending | verified, primary | per yesterday's digest + still on smark-decision-cycle urgent-skipped list today |
| PR #16 (`docs/plans/roadmap-2026-2036.md`) open, awaiting smark merge | verified, primary | [SMA-36734](mention://issue/) SIGNOFF comment + autopilot list shows PR `agent/multica-ops/76f0768a` @ `0b295c71` |
| `multica metrics query` returning 0 rows for today (new backtests) | verified, primary | `multica metrics query --limit 500 --output json` filtered for 2026-07-29: 1 row, campaign=`""`, sharpe=`None`, kind=`publish_outcomes` (cron bookkeeping, NOT a strategy metric) |
| 6 actionable items in smark queue today | verified, primary | today's `smark-decision-cycle` STATUS comment lists 4 urgent-skipped + 2 untouched-high = 6; cross-checked against escalation-router routes |
| **Root-cause of `research-scout` silent fail / framework-validate 43h gap / graph-janitor 38h gap / 07-28 retro never picked up** | **[UNVERIFIED]** | curator cannot read daemon logs at 21:00 epoch close; recommend `multica-ops` sweep before next 10:00 selection |

---

## 6. Sources cited (primary)

- [SMA-36765](mention://issue/8b8d946f-5539-46d3-b950-7e444dade151) — this retro issue.
- [SMA-36746](mention://issue/) — yesterday's retro (2026-07-28) that never ran (status=`todo`, no comments).
- [SMA-36734](mention://issue/) — yesterday's roadmap-maintainer monthly baseline + PR #16.
- [SMA-36725](mention://issue/) + [SMA-36727](mention://issue/) — yesterday's framework-validate WITHIN_TOLERANCE route on `xs_momentum_rank_1d_20260709 × freqtrade`.
- [SMA-36714](mention://issue/) — yesterday's framework-validate W5 archive (vpvr_xs_basis_zscore 15m base × vectorbt).
- [SMA-36766](mention://issue/) + [SMA-36763](mention://issue/) — today's 2 smark-decision-cycle runs (both NOOP).
- [SMA-36764](mention://issue/) (sweep #264) + [SMA-36762](mention://issue/) etc. — today's 12 escalation-router sweeps (all NOOP).
- [SMA-36749](mention://issue/) — today's roadmap-maintainer daily tick (00:54).
- Autopilot `c84304df` (infra-health-watchdog, last_run_at 2026-07-29T21:07:27+08:00) — 21:07 tick healthy.
- [SMA-36702](mention://issue/8d3a6cdd-45c3-4866-998c-6d38e2577926) — 2026-07-27 epoch-retro digest (prior day reference).
- [SMA-36692](mention://issue/42c609ef-71ca-428a-a1ef-becc4750dfec) — 2026-07-27 research-scout KILL (cross-section momentum + HMM cost-cap, last research output before today's gap).
- [SMA-36615](mention://issue/) — T11 round-1 vpvr_edge_reversion_1d (provisional KILL, 72h+ pending smark confirm).
- [SMA-36661](mention://issue/978f8d62-81b4-4415-b156-a3a5100e4cda) — T11 round-2 KILL (last week).
- [SMA-35762](mention://issue/) (T12 HMM regime SPEC, stalling 72h+), [SMA-35536](mention://issue/) (T13 MCLS sizing SPEC, stalling 72h+).
- [SMA-36660](mention://issue/a7460846-ad23-4280-9520-9fc787c6cc9b) — se_h3 复审 ESCALATE-pending (72h+ untouched).
- [SMA-36690](mention://issue/c3f678c2-57c9-4ede-8b36-ee20e9cd45ec) + [SMA-36688](mention://issue/5423bae0-0cb1-4e10-8070-c7d94bb8e6a5) — xs-basis-zscore opt_010 WITHIN_TOLERANCE ESCALATE (stale 4d, agent says ignore).
- [SMA-36670](mention://issue/92e3fb04-42f1-4531-b5dc-af9ce864a2b1) — Risk Mgmt #0 missing-acceptance (stale 2d, untouched today).
- [SMA-35091](mention://issue/4b0a7a7c-3821-4fe7-93fd-3cb073b3548f) — vpvr_funding_regime_15m × backtrader 42.51% (stale 7d+).
- [SMA-36447](mention://issue/48b0e621-4839-41da-bac9-5a8b9d71d59c) — triage-guardrail-conflict (stale).
- Docs / plans: [`~/multica/docs/plans/multica-quant-permanent-loop-2026-07-25.md`](../../docs/plans/multica-quant-permanent-loop-2026-07-25.md) §4.1 + §7, [`~/multica/AGENTS.md`](../../AGENTS.md) operating-model + comment schema + knowledge-snapshots + cycle-46.
- Strategy-side running JOURNAL: [`~/multica/quant-loop/research/JOURNAL.md`](../quant-loop/research/JOURNAL.md) entries `2026-07-19` through `2026-07-28` (T01 / T04 / T10 / T11 round-1 + round-2 / T12 HMM / T13 MCLS / T14 attribution / T15 SPA-RC / 21:00 retros).
- Prior curator snapshots in [`~/multica/knowledge/curator/`](../curator/): `2026-07-17-debug-summary.md`, `2026-07-18-knowledge-snapshot.md`, `2026-07-26-epoch-digest.md`, `2026-07-27-epoch-digest.md`, `kg_update_2026-07-26.md`.

---

## 7. What changed since the prior digest (2026-07-27 — cycle-46 cascade day)

The 2026-07-27 digest captured 8 W5 auto-archives + 1 cross-section momentum KILL + 1 WITHIN_TOLERANCE ESCALATE + 9 escalation-router sweeps + 2 smark-decision-cycle NOOPs. The 2026-07-29 epoch is a **quiet operational day**, contrasted against that cascade:

1. **No new strategy artifacts.** Today's 19 created issues are 100% autopilot cron (12 escalation-router, 5 smark-decision-cycle, 1 roadmap-maintainer daily tick, 1 this retro). No research-scout, no framework-validate, no graph-janitor, no SPEC, no strategy.
2. **Cron health is degraded (3 silent gaps).** (1) `research-scout` last_run_at=2026-07-29T11:35:28 but no SPEC-pool issue — silent fail per spec ("post at least 1 SPEC or `[type=NOOP]`"). (2) `framework-validate` last fired 2026-07-28 00:37 (43h+ gap, hourly cron should have fired 40+ times). (3) `graph-janitor` last fired 2026-07-28 07:13 (38h+ gap). (4) 2026-07-28 retro [SMA-36746](mention://issue/) was created but never picked up — first broken retro day. Recommend `multica-ops` cron-health sweep.
3. **Yesterday's residual is the `xs_momentum_rank_1d` family-seal opportunity.** New family surfaced via WITHIN_TOLERANCE route (divergence 4.37%, in-house sharpe 0.324 ≪ G1). Promote to cycle-46 dead list at next smark touchpoint.
4. **T12 + T13 SPECs now 72h+ stalled.** Past the 48h "process bug" threshold. Orchestrator should `[type=NUDGE]` quant-researcher at next 10:00 selection.
5. **smark queue is 6 actionable items deep, 0 drained today.** (1) NEW SMA-36727 xs_momentum_rank WITHIN_TOLERANCE; (2) SMA-36670 Risk Mgmt #0 missing-acceptance (high, 41h+); (3) SMA-35091 framework-cv-divergence 42.51% (urgent, 170h+ old); (4) SMA-36690 xs-basis-zscore opt_010 WITHIN_TOLERANCE (urgent, agent says ignore); (5) SMA-36660 se_h3 复审 (urgent, 72h+ old); (6) SMA-36447 triage-guardrail-conflict (urgent).
6. **`vpvr_xs_pairs_30m_funding_filter` family-seal still overdue** (9 W5 archives this week, 0 since 07-27). The cron going quiet on framework-validate may be related to the family converging — promoting it to dead list would close the loop and free cron budget.
7. **0 LIVE-eligible strategies as of 21:00 epoch close.** Same state as prior digests — no new backtests, no new candidates, no KEEP. The se_h3 复審 + T11 round-1 + T10 close remain ESCALATE-pending smark human; outcome does not arrive in time for this epoch.
8. **Cron health is the day's signal.** Quiet on the strategy axis = quiet on the research front; the day's digest is mostly about cron plumbing + smark queue staleness, not strategy.

---

*Curator run via [SMA-36765](mention://issue/8b8d946f-5539-46d3-b950-7e444dade151) (knowledge-curator dispatch, 2026-07-29 21:00+08 Asia/Shanghai). Sources cross-checked: live `multica` CLI on 26 cited SMA-IDs + autopilot get on 5 cron families + multica metrics query (500-row scan, 0 new today) + the strategy-side JOURNAL.md running log. Prior-day snapshots referenced: [`2026-07-17-debug-summary.md`](../curator/2026-07-17-debug-summary.md), [`2026-07-18-knowledge-snapshot.md`](../curator/2026-07-18-knowledge-snapshot.md), [`2026-07-26-epoch-digest.md`](../curator/2026-07-26-epoch-digest.md), [`2026-07-27-epoch-digest.md`](../curator/2026-07-27-epoch-digest.md), [`kg_update_2026-07-26.md`](../curator/kg_update_2026-07-26.md).*