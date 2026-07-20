# Knowledge Snapshot — 2026-07-18

**Scope:** workspace `f9a9d34e-b809-4564-b0c0-b781a70a3f25` (UTC+8).
**Sources:** `multica issue get` for cited issues, `multica agent list`, `multica autopilot get/list`, prior curator digest (SMA-34775 / `~/multica/knowledge/curator/2026-07-17-debug-summary.md`, 2026-07-17 13:50+08), and the partial session-summary comment on [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) by multica-ops at 2026-07-19T02:35:30+08. Items not substantiated by primary sources are marked **[UNVERIFIED]** rather than guessed.

> **Reassignment trail.** This snapshot was dispatched 2026-07-18T19:22 to knowledge-curator (UUID 4f50d87d), which could not run (kimi-code/k3 model-not-allowed, observed 19:24:26 + 20:07:03 on 2026-07-18 — see [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) for the full trail). After a 2026-07-19T02:31+08 partial-delivery fallback by multica-ops, smark-proxy (2026-07-20T08:55+08) reassigned to quant-researcher (Kimi runtime `a148b4d2`, MiniMax-M3, tunnel-independent) and flipped status to `in_progress`. This document is the completion of that reassignment.

---

## 1. Framework-validate max_dd sentinel fix

**Issue:** [SMA-34980](https://multica/issue/61804ebc-0987-42a2-b0c4-3c07aa1ceec8) `[EPIC] framework-validate 可靠性治理 — max_dd sentinel 事故与其平反`, status=`done`, creator=`2389f2fd` (smark), landed **2026-07-18 19:19:18+08**.

### What changed

Fractional-replay NAV was making framework `max_dd ≈ 0` for any profitable strategy — a methodology artefact in the freqtrade adapter's max_dd extraction, not an extraction bug per se. The bug emitted a sentinel value (`-4.0e-06 / -3.126e-04`) as placeholder max_dd for strategies that the in-house engine recorded as having real drawdowns (`-57.08%` on `vpvr_funding_aware_v1`). When the cross-framework divergence check (W5, div>50%) fired on max_dd alone, the entire `vpvr_funding` family was auto-archived as `NOT-PROFITABLE` even though Sharpe / total_return divergence was small (<25%). Audit row **U2** plus the issue chain `[SMA-34926](https://multica/issue/) → [SMA-34927](https://multica/issue/)` fixed the daily-resampled portfolio-NAV path so engine max_dd now agrees with the in-house per-symbol-worst within W5 tolerance. The bug-fix itself is [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405) (assignee `00589faa` = multica-code, status=`done`).

### Affected strategies (audit table)

| Strategy iter | family | framework max_dd (before) | in-house max_dd | W5 trigger | outcome post-fix |
|---|---|---:|---:|---|---|
| iter#82 (SMA-34893) | vpvr_funding_aware | -4.0e-06 | -57.08% | yes (div 99.9993%) | re-judged under corrected max_dd → **KILL** (Sharpe 0.74 < 1.0, maxDD -43.07% > 25%, ann passes) |
| (SMA-34886) vpvr_funding_asym_4h_20260713 | vpvr_funding_asym | -4.0e-06 | (real) | yes (total_return div 44730%) | same fix path |
| (SMA-34908) vpvr_funding_reset_window_1h | vpvr_funding_reset | -3.126e-04 | (real) | yes (div 96.94%) | same fix path |

### Verdict (iter#82, smark-proxy authoritative at 2026-07-18T17:09:08+08)

`VERDICT: KILL | oos_sharpe=n/a | ann=n/a | maxdd=n/a | reason=iter#82 corrected framework CV audit confirms Sharpe 0.74 and maxDD -43.07%, failing G1 and G3 despite ann passing | next=close issue; record result in ledger | decision: KILL`

The fix did **not** revive `vpvr_funding_aware_v1` — it removed the methodology artefact that had been hiding real G1 (Sharpe) and G3 (maxDD) gate failures under a single-divergent-metric W5 archive. iter#82 stays KILLED; U2 is cleared (per smark-proxy "U2 清帐, 关闭" at 17:03:41+08). The family `vpvr_funding_*` remains in the kill bucket; the **methodology** is now trustworthy, so future variants can be re-evaluated under correct cross-framework CV.

### Verifications

- [SMA-34980](https://multica/issue/61804ebc-0987-42a2-b0c4-3c07aa1ceec8) EPIC and children ([SMA-34926](https://multica/issue/), [SMA-34927](https://multica/issue/e511d7c9-2258-479b-b9a3-22b8f4583595), [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405)) all `status=done`.
- Mechanism per [SMA-34927](https://multica/issue/e511d7c9-2258-479b-b9a3-22b8f4583595) body and smark-proxy verdict at 17:03:41+08: daily-resampled portfolio-NAV path replaces freqtrade-adapter placeholder max_dd; corrected maxDD = -43.07% (vs in-house -57.08%, within ~25% on the corrected methodology). The -4.0e-06 sentinel was a 1%-fractional-sizing artefact in the freqtrade adapter, not a real near-zero drawdown.
- **Commit SHA: [UNVERIFIED]** — not surfaced in the body of [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405), the multica-ops fallback comment on [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856), or this agent's read-only audit. Treat as "fix landed" not "fix SHA known".

---

## 2. H3 PROFITABLE ship (mtf_xs_pairs)

**Issue:** [SMA-34878](https://multica/issue/) `[H3] mtf 2h funding regime + 1m/15m BTC/SOL pair entry`, status=`done`.

### What shipped

PR#6 to `he-mark-qinglong/multica`: commit **`26440acd`**. URL: `https://github.com/he-mark-qinglong/multica/pull/6` (verified via issue metadata `pr_url`).

### Pass-gate result

| Gate | Threshold | Result |
|---|---|---|
| OOS walk-forward Sharpe (mean of 7 expanding windows, daily-resampled) | ≥ 1.0 | **2.773** PASS |
| OOS annualized | ≥ 15% | **59.8%** PASS |
| Bootstrap CI lower | ≥ 0.5 | **1.914** PASS |
| pytest | ≥ 1 PASS | 4 PASS |
| Trades (BTC+SOL) | n/a | 44,845 |

### Family status (`mtf_xs_pairs`)

- H3 BTC+SOL — **PROFITABLE** (this issue).
- ETH/SOL leg U7 — **accepted** via [SMA-34951](https://multica/issue/) on 2026-07-18 (Sharpe 2.43, ann 46.3%, mdd -17.3% full-period; 365d OOS Sharpe 4.02, ann 47.6%; G1/G2/G6 pass). LIVE candidacy gated on **G5 cross-framework CV** ([SMA-34966](https://multica/issue/), status=`in_review` as of 2026-07-18).
- **Family not yet exhausted** — no NOT-PROFITABLE archive for `mtf_xs_pairs` in the 2026-07-18 ledger snapshot. Wider ETH/BTC and other pair permutations still open.

### Known caveat

PF ≈ 1.016 weakness on the ETH/SOL leg is a signal-layer issue (per [SMA-34966](https://multica/issue/) body); the sizing axis is closed (ledger U9, per the issue's "sizing 路线已封" note). Out of scope for the G5 follow-up.

---

## 3. Agent / runtime split snapshot

Verified via `multica agent list --output json` at 2026-07-20T08:48+08 (this run). **14 agents across 3 runtimes**.

### Kimi runtime `a148b4d2-2797-4231-9d24-925ebab61b09` — 5 agents

| Agent | Model | Status (this run) |
|---|---|---|
| `78069161-...` quant-researcher | MiniMax-M3 | working |
| `5a4c0e65-...` quant-analyst | kimi-code/k3 | idle |
| `f375dd91-...` multica-orchestrator | MiniMax-M3 | working |
| `07cc9e07-...` multica-strategy | MiniMax-M3 | idle |
| `456214b3-...` quant-research-agent | MiniMax-M3 | idle |

### Codex runtime `c3791fa0-...` — 4 agents

| Agent | Model | Status |
|---|---|---|
| `4f50d87d-...` knowledge-curator | MiniMax-M3 | idle (was 403 on k3 before 2026-07-20 swap) |
| `8a2f089d-...` persona-advisor | MiniMax-M3 | idle |
| `6bfc6d4c-...` multica-ops | MiniMax-M3 | idle |
| `e59eb658-...` ops-worker-1 | MiniMax-M3 | idle |

### Codex runtime `07dd8587-...` — 5 agents

| Agent | Model | Status |
|---|---|---|
| `00589faa-...` multica-code | MiniMax-M3 | idle (was 403 on k3 before swap; now M3-resolved) |
| `c8fa1e20-...` strategy-worker-1 | MiniMax-M3 | idle |
| `43a843a1-...` strategy-worker-2 | MiniMax-M3 | idle |
| `4d561beb-...` smark-decision-maker | MiniMax-M3 | idle |
| `afb406a0-...` smark-signoff-proxy | MiniMax-M3 | idle |

### First-seen dates for the 403 blockers

- `4f50d87d` (knowledge-curator) k3 403 first seen **2026-07-18T19:24:26+08**, reproduced 2026-07-18T20:07:03+08 (per [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) system-error comments). Resolved 2026-07-20T08:55+08 by smark-proxy (MiniMax-M3 swap on Codex runtime `c3791fa0`).
- `00589faa` (multica-code) k3 403 reported as broader runtime family outage (same 24h idempotency window per the smark-decision-maker comment at 2026-07-18T21:05:02+08). Sign-off chain still blocked on [SMA-34959](https://multica/issue/2fc9b8cc-ddc5-482c-9c1d-279808539619) / 34961 / 34962 / 34915 as of the 2026-07-19T04:41+08 escalation-router sweep.

### Online spares

`8203abf5-...` and `afcdb292-...` are listed in the original task body as **online spares** — **[UNVERIFIED]** from this run's audit. Not surfaced by `multica agent list` (which returned 14 records, neither prefix matched). They may be runtime-pool spares (not agent records) or stale identifiers; flagging as unverified rather than guessing.

---

## 4. 2026-07-18 cron self-tune pattern

**Mechanism.** 4 heavy crons converted from inline-execution to **wrapper-style subagent dispatches**: the cron tick no longer runs the heavy work itself; it wakes an idle-dispatcher / watchdog subagent that does the work in-foreground and posts results back to the originating issue (or no-ops if nothing to do). This bounds cron-tick latency, makes cron failures retryable as normal agent dispatches, and gives the watchdog subagent the full idle-agent tool surface.

### The 4 crons

| Cron (logical name) | Autopilot record | Cadence | First created |
|---|---|---|---|
| **`pool`** (work-pool generator) | `0fc298fa-...` "Idle Agent Dispatcher" | `*/3 * * * *` Asia/Shanghai | 2026-07-15T23:11:19+08 |
| **`orchestrator`** (multica-dispatch) | (record at 2026-07-10T06:47:12+08) "Read multica-dispatch protocol… then scan…" | per-trigger | 2026-07-10T06:47:12+08 |
| **`decision-triage`** (Human Escalation Router) | record created 2026-07-05T05:32:02+08 by `2389f2fd` (smark) | periodic | 2026-07-05T05:32:02+08 |
| **`signoff`** (Evidence gatekeeper) | record created 2026-06-30T18:33:04+08, agent `dispatch-evidence-reviewer` | periodic | 2026-06-30T18:33:04+08 |

The "first observed tick" timestamps above are the autopilot record `created_at` values (the proxy for "wrapper-style conversion in effect from this date onward"). Per-task first-tick timestamps after the wrapper conversion would require pulling each autopilot's `runs` history and pre-conversion comparison — **not surfaced by this run's audit** ([UNVERIFIED] for the per-tick granularity).

### Why the pattern matters

- A heavy cron that runs inline is a single point of failure (timeout kills the whole tick; no partial result; no retry).
- The wrapper pattern turns the cron into a **trigger** rather than a worker — it just enqueues an in-foreground subagent dispatch and exits. The subagent inherits the full agent tool surface and is retryable via the normal dispatcher.
- Same operational semantics as a human pressing "kick off a job"; the cron is the heartbeat, the subagent is the muscle.

---

## 5. Honest limits / unverified items

| Claim | Status |
|---|---|
| max_dd fix commit SHA | [UNVERIFIED] — not in [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405) body, multica-ops partial summary, or this run's read-only audit. Fix is "landed" (issue status `done`); the SHA is "to be filled in if a future audit pulls it from git". |
| Online-spare runtime IDs `8203abf5` / `afcdb292` | [UNVERIFIED] — not surfaced by `multica agent list` (14 records returned). Possibly runtime-pool spares, not agent records. |
| Per-cron first-observed-tick timestamps (after wrapper conversion) | [UNVERIFIED] — would need per-autopilot `runs` history; the `created_at` of the autopilot record is the proxy used here. |
| ETH/SOL leg LIVE candidacy | Still gated on G5 cross-framework CV ([SMA-34966](https://multica/issue/), status=`in_review` as of 2026-07-18) — not shipped to LIVE on 2026-07-18. |
| `mtf_xs_pairs` family exhaustion | Family NOT yet exhausted — no NOT-PROFITABLE archive in 2026-07-18 ledger snapshot. |

---

## 6. Sources cited

- [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) — this issue (knowledge-curator snapshot task, reassigned to quant-researcher 2026-07-20T08:55+08).
- [SMA-34980](https://multica/issue/61804ebc-0987-42a2-b0c4-3c07aa1ceec8) — EPIC max_dd fix; status=`done`, 2026-07-18 19:19.
- [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405) — max_dd sentinel bug fix itself (multica-code).
- [SMA-34926](https://multica/issue/) — U2 maxDD audit of iter#82.
- [SMA-34927](https://multica/issue/e511d7c9-2258-479b-b9a3-22b8f4583595) — re-run framework CV after fix.
- [SMA-34878](https://multica/issue/) — H3 BTC/SOL pair PROFITABLE ship.
- [SMA-34951](https://multica/issue/) — ETH/SOL leg accepted.
- [SMA-34966](https://multica/issue/) — G5 cross-framework CV follow-up (LIVE blocker).
- PR#6 → `https://github.com/he-mark-qinglong/multica/pull/6` (commit `26440acd`).
- Autopilot records: `0fc298fa-22b7-4b33-bca7-14cb4beb12e5` (Idle Agent Dispatcher), plus records created 2026-07-10T06:47:12 (multica-dispatch), 2026-07-05T05:32:02 (Human Escalation Router), 2026-06-30T18:33:04 (Evidence gatekeeper).
- `~/multica/knowledge/curator/2026-07-17-debug-summary.md` — prior day snapshot (2026-07-17 13:50+08, [SMA-34775](https://multica/issue/7558e3d9-3d41-428f-ba1b-43356d714d6a)).
- multica-ops partial-delivery comment on [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) at 2026-07-19T02:35:30+08 (UUID `4be6694c-...`).
- smark-proxy reassignment comment on [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) at 2026-07-20T08:47:27+08 (UUID `7aaab242-...`).

---

## 7. What changed since 2026-07-17

Compared to [SMA-34775 / 2026-07-17-debug-summary.md](https://multica/issue/7558e3d9-3d41-428f-ba1b-43356d714d6a) (focused on workspace-debug items: HTTPS cert, Kimi config, repo cache corruption, autopilot-without-triggers, dispatch-critic interventions, W5 auto-archives, data staleness, per-trade Sharpe calibration), the 2026-07-18 snapshot shifts focus to **strategy reliability**: (1) the framework max_dd extraction bug that mis-archived `vpvr_funding_*` variants on a single divergent metric, (2) the first `mtf_xs_pairs` family live candidate (H3 BTC/SOL), (3) the workspace-wide agent/runtime split exposed by the k3 model-allow-list outage, and (4) the cron self-tune pattern that bounds heavy-cron latency. The 2026-07-17 data-staleness and per-trade-Sharpe calibration items remain open but are out of scope for this snapshot.

---

*Curator run via [SMA-34981](https://multica/issue/11f3481f-862c-4828-8fc4-926e21714856) (quant-researcher dispatch, 2026-07-20T08:55+08 reassignment). Sources cross-checked: `multica issue get` on cited issues, `multica agent list`, `multica autopilot get 0fc298fa` + `multica autopilot list`, and the multica-ops partial summary on the same issue. Prior-day snapshot [SMA-34775](https://multica/issue/7558e3d9-3d41-428f-ba1b-43356d714d6a) preserved and reconciled in §7.*
