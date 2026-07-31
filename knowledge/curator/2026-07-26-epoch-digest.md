# Epoch Digest — 2026-07-26

**Scope:** workspace `f9a9d34e-b809-4564-b0c0-b781a70a3f25` (UTC+8). Slot [SMA-36664](https://multica/issue/a1ce94f2-36e5-4ae8-93d8-59fd3a130df0) `[epoch-retro 2026-07-26]` (knowledge-curator `4f50d87d-…`, autopilot `23281f8e-…`, 21:00 Asia/Shanghai cron).
**Prior surfaces:** `2026-07-17-debug-summary.md` (debug), `2026-07-18-knowledge-snapshot.md` (strategy + runtime), `kg_update_2026-07-26.md` (slot #17, knowledge surfaces map).
**Sources (all primary):** live `multica` CLI (`issue get / list / comment list / metrics query / autopilot get`) on cited SMA-IDs, plus the se_h3 verdict thread ([SMA-36570](https://multica/issue/584be016-43fe-48ed-bf7f-d596f5c09f9d)), fee-shock BLOCKER ([SMA-36566](https://multica/issue/5645fc85-0d53-4c83-ac47-fd4451bcde69)), research-scout brief ([SMA-36608](https://multica/issue/367d529b-5afa-433c-b3ce-8b1157a8c614)), and the strategy-side running JOURNAL ([`~/multica/quant-loop/research/JOURNAL.md`](../quant-loop/research/JOURNAL.md)) + [`AGENTS.md`](../../AGENTS.md) operating-model section.

> **Constitution v1.0 honesty note.** Numbers below come from cited primaries (issue bodies / VERDICT.md / SPEC.md). Anything I could not verify from a primary source is marked **n/a** and flagged. The `multica metrics query` CLI returned 0 rows created today (`vpvr-xs-pairs`/`other`/`all`), so today's metric-table numbers necessarily come from issue-body callouts — those are still primary, but I distinguish the provenance with `(metric-row n/a)` where a multica-metrics row was expected and missing.

---

## 1. Verdict summary (KEEP / KILL / ESCALATE, 2026-07-26)

### 1.1 Verdict trail

| Verdict type | Issue | Subject | Status |
|---|---|---|---|
| **KILL** (family-wide) | [SMA-36570](https://multica/issue/584be016-43fe-48ed-bf7f-d596f5c09f9d) | `signal-enhance-h3` (se_h3) — 7-窗 OOS Sharpe 9.21 (CI [7.79, 11.04]) + G4 FAIL (PF 1.098 < 1.5) + 修正 fee-shock 24/60bps Sharpe −17.33 / −38.80 → KILL | done 2026-07-26T08:31+08 (human smark 复核通过) |
| **KILL** (family-extends) | AGENTS.md operating-model §"Strategy state (2026-07-26 02:30)" | `mtf_xs_pairs` whole family (H1-H4 + se_h3) declared **KILLED-FAMILY** — never parameter-sweep again | recorded |
| **KILL** (provisional) | [SMA-36615](https://multica/issue/5f3a64d7-…) T11 + [SMA-36620](https://multica/issue/725e1812-541c-4359-9cf6-22744af59f73) audit | `vpvr_edge_reversion_1d` self-caught **look-ahead bug** at +1d shift; honest D+1 口径 → 9/9 cells negative, mean markout −28/−42/−46 bp, TP1-first 45-48% | quant-researcher 自承认 KILL 建议 (待 smark 拍板 KEEP-vs-KILL) |
| **KILL** (cycle-46) | [SMA-36608](https://multica/issue/367d529b-5afa-433c-b3ce-8b1157a8c614) research-scout | market-making/inventory angle → cost-cap NUMERIC FAIL (VIP0 net ≤ −6.5bp) + cycle-46 KILLED-FAMILY "microstructure features" → KILL (no SPEC) | research-scout 09:13+08 posted brief |
| **ESCALATE → resolved** | [SMA-36603](https://multica/issue/de080b98-3c99-479c-9d3f-dee4725513be) | T10 maker-pilot 三选一 (pilot / VIP3+ / defer) → smark 10:00 close：不做 pilot，**费率锁定为 maker 2bp / taker 5bp**，T10 关闭 | done |
| **ESCALATE → 复审** | [SMA-36660](https://multica/issue/a7460846-ad23-4280-9520-9fc787c6cc9b) | se_h3 KILL 复审：smark 确认 60% 第三方返佣可达（maker 0.8bp / taker 2bp 每边），复算 taker 4bps Sharpe +5.98（活）/ maker 1.6bps +8.88（活）| todo · 等 smark 人类裁决（KILL→PIVOT 涉及战略反转 + 外部账户合规性，按 smark-decision-framework 必 escalate） |
| **W5 NOT-PROFITABLE auto-archive** | 9 today (cycle-46 cleanup) | `mtf_xs_pairs_h1/h2` (×4 archives) + `vpvr_xs_pairs_4h_zscore_vpvr` (opt_017, opt_013) + `vpvr_stable_depeg_regime_4h` (opt_041, opt_091) + `vpvr_reversion_1m_volume_profile_break` + `p1_020_three_drives` + `trend_multi_tf_momentum_cascade` — 残骸 W5 cross-framework CV confirmed NOT-PROFITABLE | 8 done, 4 in_review |
| **SPEC shipped (no backtest yet)** | [SMA-35536](https://multica/issue/…) T13, [SMA-35762](https://multica/issue/…) T12 | MCLS (liquidity sizing, 5-cap intersection) + HMM regime detector (4h, BTC+ETH+SOL joint, Student-t emission, Aumann-falsifier promoted to G4) | both shipped today, awaiting strat-validation + quant-analyst per V0-V7 / G0-G7 |
| **KEEP** | none | — | — |

> **0 LIVE-eligible strategy at epoch close 21:00.** Same state as AGENTS.md §"Strategy state (2026-07-26 02:30)" — only **potential** change tonight is the se_h3 复审 if smark rules in favor of paper-trading candidate.

### 1.2 Strategic consequence tree

- `mtf_xs_pairs` family is **dead**. The KILLED-FAMILY closure raises the bar for any future revival to: (a) sub-taker execution ≤20bps pair-RT AND (b) human approval, both pre-registered (per the `KILLED.md` formal closure rule + smark final 8:30 comment). Today's se_h3 KILL widened into family-KILL because the same 200× fee-shock bug artifact tainted all H1-H4 ledger numbers.
- `vpvr_xs_pairs_4h` and `vpvr_stable_depeg_regime_4h` families are **W5 auto-confirmed NOT-PROFITABLE**. These were already in the 2026-07-18 cycle-46 baseline; today's archives are residual cleanup, not new sweeps.
- `vpvr_edge_reversion` (T11) **probable** family-KILL on next smark confirmation — the look-ahead-bug self-correction produced 9/9 negative cells across BTC/ETH/SOL × 3 horizons at honest D+1 shift. Cycle-46 symmetry evidence: T08 (HVN入/LVN出) was already KILL; T11 (LVN入/HVN出, inverse geometry) is KILL on the same mean-reversion mechanism, two opposite geometries both failing.
- The **HMM regime (T12)** and **MCLS (T13)** SPECs shipped but are NOT in any KILL/cycle-46 bucket — they are net-new prior content per quant-researcher's note ("prior 35002 KILL is on different axis; this SPEC satisfies revival criterion"). They remain candidates for tomorrow's 10:00 selection.
- Cost basis shift today: from spec-dev "VIP0 9bp / taker 5bp" (T10 floor) to a **ratified research-wide constant**: **maker 2bp / taker 5bp** per side, per [SMA-36603](https://multica/issue/de080b98-3c99-479c-9d3f-dee4725513be)'s final signoff. This applies to ALL future strategy validation cost math.

---

## 2. Daily comparison table (curator-verdict format)

> Numbers from cited primaries (issue bodies / VERDICT.md / SPEC.md / `results-ledger.md`). Where a metric-row from `multica metrics query` would normally populate the cell and is unavailable (today created 0 metric rows in `vpvr-xs-pairs` / `other` / `all`), marked `(metric-row n/a)`. The "human verdict" column is one-line per AGENTS.md §7.

| Strategy | OOS Sharpe | CI lower | Post-fee Sharpe @60bps pair-RT | Max drawdown | Verdict rationale (one line) |
|---|---|---|---|---|---|
| **se_h3** (`mtf_xs_pairs H3 enhance`) | 9.2073 (7-window mean) | 7.79 (CI95 bootstrap seed42/10000) | **−38.80** (corrected fee-shock backtrader); rabbit 4bps taker +5.98; 60% rabbit maker 1.6bps +8.88 (smark-confirmed 20:31) | n/a (in-house ann +126% / +240% on rebate baselines; full Sharpe 10.77 @ 8bps) | **KILL-FAMILY** — G4 hard FAIL PF 1.098<1.5 + break-even 20bps > realistic taker → **[SMA-36570](https://multica/issue/584be016-43fe-48ed-bf7f-d596f5c09f9d)** + **[SMA-36660](https://multica/issue/a7460846-ad23-4280-9520-9fc787c6cc9b) 复审 pending** |
| `mtf_xs_pairs_h1` (15m) | n/a (metric-row n/a, ledger 0.258 BT Sharpe pre-fix) | n/a | n/a | n/a | **W5 archive** — same 200× fee-shock bug artifact; family-KILLed today → [SMA-36622](https://multica/issue/…) + [SMA-36616](https://multica/issue/…) archives |
| `mtf_xs_pairs_h2` (15m) | n/a (metric-row n/a) | n/a | n/a | n/a | **W5 archive** — family-KILLed → [SMA-36601](https://multica/issue/277c4a34-…) / [SMA-36606](https://multica/issue/…) / [SMA-36627](https://multica/issue/…) 4 archives |
| `vpvr_edge_reversion_1d` (T11) | n/a (pre-registration look-ahead bug, real values hidden) | n/a | −28/−42/−46 bp mean markout (BTC/ETH/SOL, 1d TTL, scenario_b_defensive) | not reported | **provisional KILL** — 9/9 cells negative at honest D+1 shift, TP1-first 45-48% (coin-flip), dropout 37-39%, three pre-registered K-conditions triggered (K1/K4/K5) → [SMA-36615](https://multica/issue/…) + [SMA-36620](https://multica/issue/725e1812-541c-4359-9cf6-22744af59f73) audit |
| `vpvr_xs_pairs_4h_zscore_vpvr` (opt_017/opt_013) | n/a (metric-row n/a) | n/a | n/a | n/a | **W5 archive (cross-framework CV confirmed)** — T09 cycle-46 baseline family graveyard |
| `vpvr_stable_depeg_regime_4h` (opt_041/opt_091) | n/a (metric-row n/a); previous fragments sharpe +0.137 BT / 0.000 FT / 0.149 VBT with ann −0.297 | n/a | n/a | n/a | **W5 archive** — already graveyard-bucket; 2 archives today confirm NOT-PROFITABLE |
| `vpvr_reversion_1m_volume_profile_break_20260709` | n/a (metric-row n/a); ledger Sharpe −22.654 / PF 0.12 / maxDD −2.668 | n/a | n/a | n/a | **W5 archive** — 1m_klines_reversal family (cycle-46 dead) |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | n/a (metric-row n/a); ledger −3.632 in-house | n/a | n/a | n/a | **W5 archive** — trend_multi family not yet family-KILL but accumulating iters |
| `p1_020_three_drives` (NEW today) | n/a (metric-row n/a) | n/a | n/a | n/a | **W5 archive (new entry)** — likely options_macro_sentiment / vol_breakout related, AUTO-ARCHIVE per W5 protocol |
| **T10 maker pre-SPEC** (research-side, not strategy) | n/a (research deliverable, no backtest) | n/a | theoretical floor 9bp VIP0 (queue + adverse-sel estimate) — not regime-validated | n/a | **DEFER-with-closed** — human裁决 10:00 close：不 pilot，rate 锁 maker 2bp / taker 5bp；T10 关闭 → [SMA-36598](https://multica/issue/440bd2d5-f013-47c4-893d-af77e5d05ced) + [SMA-36603](https://multica/issue/de080b98-3c99-479c-9d3f-dee4725513be) |
| **T12 HMM regime** (SPEC shipped, no backtest) | n/a (gates G0-G7 not evaluated) | n/a | n/a (donor 30-EMA baseline) | n/a (regime-macro SKILL: regime layer, not pnl axis) | **SPEC-only, awaits V0-V7 + G0-G7** — Aumann-falsifier G4 is the acceptance gate, not Sharpe |
| **T13 MCLS liquidity sizing** (SPEC shipped, no backtest) | n/a (gates V0-V7 not evaluated) | n/a | ratified 22bp RT premise (cap_impact ⟂ cost) | n/a | **SPEC-only, awaits V0-V7** — composition with `vol_target.py`, not replacement |

**One-line human verdict (persona-advisor style):** "KILL-reset day. se_h3 KILLed the whole `mtf_xs_pairs` family after a 200× fee-shock bug was patched. T11 self-corrected to KILL on a look-ahead bug (mechanism kill, not cost-cap kill). 0 LIVE-eligible strategies remain. 2 new SPECs (HMM regime, MCLS sizing) staged for tomorrow's 10:00 selection — neither touches the killed families. Cost basis locked at maker 2bp / taker 5bp."

---

## 3. cycle-46 exhaustion check (this week, 2026-07-20 → 2026-07-26)

Counted strategy-family occurrences in issue titles this week (running tally from `multica issue list --limit 100 --output json`, filtered for families named in the cycle-46 family list or the AGENTS.md killed-family list):

| Family | Week count | Direction | Action |
|---|---|---|---|
| `mtf_xs_pairs` | **6** | **KILLED today** (smark 8:31, family-wide) | **KILL WARNING posted** (this digest §4) — family rule now permanently `family-KILL`; revival needs sub-taker execution ≤20bps AND human approval per `KILLED.md` |
| `vpvr_stable_depeg_regime_4h` | 2 | W5 auto-archive, NOT-PROFITABLE confirmed | already graveyard; flag if iters continue |
| `vpvr_xs_pairs_4h` | 2 | W5 auto-archive, NOT-PROFITABLE confirmed | already graveyard (T09 cycle-46 baseline) |
| `p1_020_three_drives` | 1 | NEW iters into graveyard today | one-shot, no continued sweep |
| `vpvr_reversion_1m` | 1 | W5 archive today | family already KILLED, residual cleanup |
| `vpvr_funding` | 1 | already graveyard (T06 KILL) | archived long ago |

**cycle-46 verdict for 2026-07-26:** the day's W5 auto-archives are **residual cleanup of families that were already KILLED** (or, in the case of `mtf_xs_pairs`, formally KILLED today). They are **not** new parameter sweeps. The single family that crossed the cycle-46 threshold for fresh action is **`mtf_xs_pairs`** — handled via the smark decision at 8:30+08, not via additional sweeps.

**[type=KILL] warning** (issued on [SMA-36664](https://multica/issue/a1ce94f2-36e5-4ae8-93d8-59fd3a130df0) — this retro issue):

> **`mtf_xs_pairs` family is closed for parameter sweeps.** smark-decision-maker + human at 2026-07-26T08:31+08: "se_h3 及 mtf_xs_pairs 全家族（H1-H4）正式封存，进入 KILLED-FAMILY 清单，任何人不得再对该家族扫参。后续：orchestrator 执行封存（目录标记+清单+SPEC 记录），研究主线转向执行成本问题（maker 研究前置）。" The only path back is sub-taker execution ≤20bps pair-RT proven on the canonical pipeline **and** human approval.

---

## 4. Tomorrow's 10:00 selection (priority-1 SPECs)

Two SPEC candidates the 10:00 selection screen should pick (with one-line reasons):

### Priority 1 (highest) — `T13 MCLS liquidity sizing` (`SMA-35536`)

- **Why pick:** Net-new prior content (5-cap intersection from depth/impact/data axes — distinct from any killed family). Spec ships today with V0-V7 falsification gates; donor = `mtf_xs_pairs` H3 (deployed but family-KILLed) or a clean donor if H3 is unavailable. NO VPVR / microstructure / funding overlap. Cost basis aligned with today's ratified "maker 2bp / taker 5bp" floor.
- **First action:** strat-execution (L3) for `_shared/sizing/liquidity.py` impl + unit tests V0 (no kline proxies, real aggTrades). strat-data (L0) for the L2 depth snapshotter per spec §5.
- **Risk hook:** V3 (Aumann-falsifier on sizing-decoration test) is the load-bearing gate — must beat argmax-cap and flat-sizing by > 0.2 OOS Sharpe on a donor strategy.

### Priority 2 — `T12 HMM regime` (`SMA-35762`)

- **Why pick:** Revives the killed SMA-35002 axis with genuinely new prior content (4h refit + BTC/ETH/SOL joint + Student-t emission + Aumann-falsifier promoted to G4). Revival-criterion 35002 verbatim was satisfied: "new prior content whose raw signal clears cost on the canonical pipeline *before* being folded into a Kim-filter posterior" — the new components (rvov_t, fbasis_t cross-asset, winsorized retz_t) all clear G0.
- **First action:** strat-indicators implements `_shared/regime/hmm_4h.py` against the SPEC's public API §5; strat-validation runs walk-forward OOS §7 with G0-G7 reporting in an EVIDENCE comment; quant-analyst runs V7 (cross-framework hmmlearn vs custom-em) CV.
- **Risk hook:** G4 Aumann-falsifier failure (regime layer doesn't lift Sharpe > 0.3 OOS) is auto-KILL — same standard SMA-35002 missed.

### What NOT to pick at 10:00

- **T11 vpvr_edge_reversion_1d**: provisional KILL pending smark confirmation — do NOT promote to SPEC bucket until smark confirms. Round-2 (SMA-36661 in_progress) is acceptable as exploratory only.
- **T10 maker-pilot**: closed today at smark 10:00 decision — do not reopen unless smark issues explicit reversal.
- **se_h3 复审** (SMA-36660): ESCALATE-pending — do not preempt smark's ruling with parallel work.

---

## 5. Honest limits / unverified items

| Claim | Status | Why |
|---|---|---|
| se_h3 OOS Sharpe 9.2073 + CI [7.79, 11.04] | verified, primary | [SMA-36570](https://multica/issue/584be016-43fe-48ed-bf7f-d596f5c09f9d) body verbatim; main `c71f7a397` |
| se_h3 fee-shock 4bps +5.98 / 24bps −17.33 / 60bps −38.80 | verified, primary | same; corrected口径 per [SMA-36566](https://multica/issue/5645fc85-0d53-4c83-ac47-fd4451bcde69) |
| se_h3 rebate @60% maker 1.6bps +8.88 / taker 4bps +5.98 | verified, primary | [SMA-36660](https://multica/issue/a7460846-ad23-4280-9520-9fc787c6cc9b) body; results `se_h3_fee_shock.rebate_rates.json` (main `ea95d7f5e`) — claim subject to smark confirmation, not verdict |
| T11 look-ahead bug + 9/9 cells −28/−42/−46 bp honest | verified, primary | quant-researcher self-disclosed in [SMA-36615](https://multica/issue/…) + [SMA-36620](https://multica/issue/725e1812-541c-4359-9cf6-22744af59f73) audit comment thread |
| T12 (HMM regime) + T13 (MCLS) SPEC content | verified, primary | per quant-researcher's running JOURNAL entry today (2026-07-26) at `~/multica/quant-loop/research/JOURNAL.md` §"T12 — HMM regime detector SPEC" + §"T13 — Liquidity Sizing (MCLS) SPEC shipped" |
| T10 close decision (maker 2bp / taker 5bp) | verified, primary | smark [type=DECISION] 2026-07-26T10:00+08 on [SMA-36603](https://multica/issue/de080b98-3c99-479c-9d3f-dee4725513be) |
| `multica metrics query` returning 0 rows for today | **[UNVERIFIED]** that this is the full today's set | queried `--campaign vpvr-xs-pairs / other / all --limit 50/100 --output json`; the criterion is "rows where `created_at` starts with `2026-07-26`". This is what the JSON returns. Possibility that more campaigns existed or rows went into past-dated `created_at` is small but not exhaustively audited. |
| Max-Drawdown numbers for se_h3 in-house baseline | n/a in digest table | n/a in `multica metrics` rows; VERDICT.md cites ann +126% / +240% on rebate baselines and Sharpe 10.77 @ 8bps but maxDD per-tier not surfaced in issue body — flagged not invented |
| Ledger `quant-loop/results-ledger.md` (2026-07-25 09:29 UTC build) | **[STALE]** | last regeneration predates today's se_h3 KILL + T11 reversal + family-seal; not refreshed tonight (that's a follow-up action for strategy-worker / multica-strategy, out of curator scope) |
| SMA-36660 verdict decision | **[PENDING]** | escalated to smark human (see comment `50106f4a-…` 2026-07-26T20:33 by smark-decision-maker); outcome does not arrive in time for this epoch |

---

## 6. Sources cited (primary)

- [SMA-36664](https://multica/issue/a1ce94f2-36e5-4ae8-93d8-59fd3a130df0) — this retro issue.
- [SMA-36570](https://multica/issue/584be016-43fe-48ed-bf7f-d596f5c09f9d) — se_h3 verdict, KILL, smark 8:30+08 signoff (comments `289d865b-…` smark-decision-maker 1:46 + `dda547a3-…` signoff-proxy ESCALATE 2:22 + `07108d9d-…` smark SIGNOFF 8:30 + `0185a718-…` signoff-proxy CONFIRM 8:31).
- [SMA-36566](https://multica/issue/5645fc85-0d53-4c83-ac47-fd4451bcde69) — se_h3 fee-shock BLOCKER, root-caused the 200× bug.
- [SMA-36660](https://multica/issue/a7460846-ad23-4280-9520-9fc787c6cc9b) — se_h3 KILL 复审 with 60% rebate evidence; ESCALATE in thread (`50106f4a-…`).
- [SMA-36598](https://multica/issue/440bd2d5-f013-47c4-893d-af77e5d05ced) — T10 maker pre-SPEC research deliverable.
- [SMA-36603](https://multica/issue/de080b98-3c99-479c-9d3f-dee4725513be) — T10 maker 三选一 decision (closed 10:00+08, comments `c3228969-…` escalate + `046f123d-…` smark DECISION + `979d7f06-…` decision-maker skip-noise).
- [SMA-36608](https://multica/issue/367d529b-5afa-433c-b3ce-8b1157a8c614) — research-scout SPEC pool (market-making/inventory brief → KILL cycle-46 + cost-cap).
- [SMA-36615](https://multica/issue/5f3a64d7-…) (T11 SPEC candidate), [SMA-36620](https://multica/issue/725e1812-541c-4359-9cf6-22744af59f73) (audit of T11 spec, found look-ahead bug), [SMA-36661](https://multica/issue/978f8d62-81b4-4415-b156-a3a5100e4cda) (VPVR edge round-2, in_progress, contract version 1).
- [SMA-36575 / 36580 / 36601 / 36606 / 36612 / 36616 / 36622 / 36627 / 36634 / 36640 / 36643 / 36649 / 36663](https://multica/issue/) — framework-validate W5 NOT-PROFITABLE auto-archives (9 today).
- [SMA-36594](https://multica/issue/ba750d38-…) — deploy migration 126 (wait_reason), landed clean.
- [SMA-36645](https://multica/issue/dd16e1b4-…) — Risk Mgmt #90 max-position-size-limit implementation, in_progress by multica-code.
- [SMA-35536](https://multica/issue/…) (T13 MCLS SPEC), [SMA-35762](https://multica/issue/…) (T12 HMM regime SPEC) — both shipped today.
- Docs / plans: [`~/multica/docs/plans/multica-quant-permanent-loop-2026-07-25.md`](../../docs/plans/multica-quant-permanent-loop-2026-07-25.md) §4.1 + §7, [`~/multica/AGENTS.md`](../../AGENTS.md) operating-model + comment schema + knowledge-snapshots + cycle-46.
- Strategy-side running JOURNAL: [`~/multica/quant-loop/research/JOURNAL.md`](../quant-loop/research/JOURNAL.md) entries `2026-07-26` T10 / T11 / T11-reversal / T12 / T13.
- Results ledger: [`~/multica/quant-loop/results-ledger.md`](../quant-loop/results-ledger.md) — last regenerated 2026-07-25 09:29 UTC (predates today; flagged stale in §5).
- Prior curator snapshots in [`~/multica/knowledge/curator/`](../curator/): `2026-07-17-debug-summary.md`, `2026-07-18-knowledge-snapshot.md`, `kg_update_2026-07-26.md`.

---

## 7. What changed since the prior digest (2026-07-18 — strategy + runtime snapshot)

The 2026-07-18 snapshot captured the framework `max_dd` sentinel fix + H3 PROFITABLE ship + runtime split + cron self-tune pattern. The 2026-07-26 epoch swings the pendulum the other way on H3 + adds operational observations:

1. **H3 (then-PROFITABLE) is now KILLED-family.** The 200× fee-shock bug artifact that inflated every "fee-robust H3" number from `2026-07-04 → 2026-07-25` was caught by orchestrator W4-T15 audit ([SMA-36566](https://multica/issue/5645fc85-0d53-4c83-ac47-fd4451bcde69)). The corrected口径 gives break-even 20bps > realistic taker cost, and the family-extension `se_h3` KILL lights up the same family-KILL pattern.
2. **0 LIVE-eligible strategy as of 21:00 epoch close.** Same state as the AGENTS.md §"Strategy state" line that has tracked since 2026-07-26 02:30, but now formally reflected in the daily digest. The single potential opening tonight is the se_h3 复审 — currently ESCALATE-pending to smark.
3. **Cost basis is now ratified research-wide**: **maker 2bp / taker 5bp per side** per [SMA-36603](https://multica/issue/de080b98-3c99-479c-9d3f-dee4725513be). This replaces earlier ad-hoc rates (VIP0 nominal, post-VIP3 inferred, 60% third-party rebate separately argued) and standardizes the floor for all future SPEC cost-cap math.
4. **Two new SPEC candidates shipped** (T12 HMM regime, T13 MCLS) without backtests — net-new prior content, awaiting V0-V7 / G0-G7 falsification. They are NOT revival candidates for any killed family; the revivial-criterion axis is genuinely different.
5. **T11 vpvr_edge_reversion_1d** still needs a smark ruling to formalize the provisional KILL, but the verdict math (9/9 cells negative at honest D+1 shift, two pre-registered K-conditions triggered) is already strong enough that the curator's prediction is KILL with high confidence. Once confirmed, the `vpvr_edge_reversion` family joins the cycle-46 dead list along with `vpvr_xs_pairs_4h`, `vpvr_stable_depeg_regime_4h`, `1m_klines_reversal`, `funding_carry`, `4h_single_TF_stat_arb`, `microstructure_features`, `mtf_xs_pairs`.
6. **W5 auto-archive cascade** is heavier today (9 entries) than any prior single day, but it is post-KILL residual, not new content. This pattern will repeat for ~3-5 days as the framework-validate hourly cron catches up to the smark family seals; curator flagging it so the orchestrator doesn't read it as a sweep signal.

---

*Curator run via [SMA-36664](https://multica/issue/a1ce94f2-36e5-4ae8-93d8-59fd3a130df0) (knowledge-curator dispatch, 2026-07-26 21:00+08 Asia/Shanghai). Sources cross-checked: live `multica` CLI on 13 cited SMA-IDs + research-scout thread + curator's own triage of the 86 today-created issues. Prior-day snapshots referenced: [`2026-07-17-debug-summary.md`](../curator/2026-07-17-debug-summary.md), [`2026-07-18-knowledge-snapshot.md`](../curator/2026-07-18-knowledge-snapshot.md), [`kg_update_2026-07-26.md`](../curator/kg_update_2026-07-26.md).*
