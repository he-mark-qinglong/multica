# multica Project Restructure Plan — 9 → 5 Value-Stream Projects

> **Status: PLAN ONLY. Do not execute without smark approval.**
> Author: kimi subagent (design pass) · Date: 2026-07-20
> Workspace: `f9a9d34e-b809-4564-b0c0-b781a70a3f25` on .105

## 1. Goal

Collapse the current 9 projects (which mix strategy work with ops work and
contain 2 dead / 2 completed shells) into **5 value-stream projects** aligned
with the quant production loop, plus an optional **6th `meta` project** for
cross-cutting governance (root goal, specs, conventions).

Target streams:

| # | Project | Owner runtime | Scope |
|---|---|---|---|
| 1 | `infra` | Claude (coding agents) | backtester, validators, tunnel, daemon, display frontend, data pipeline |
| 2 | `strategy-discovery` | Kimi (research) | hypotheses, research, new strategy directions, campaign specs |
| 3 | `strategy-validation` | Kimi (research) | framework CV, walk-forward, DSR, bootstrap CI, paper-trade gating |
| 4 | `live-trading` | Codex (ops) | paper trading, execution, monitoring |
| 5 | `ops` | Codex (ops) | cron, monitoring, health patrol, autopilot, queue balance |
| 6 | `meta` *(optional)* | — | ROOT GOAL SMA-30054, SPEC v1, conventions, cross-cutting governance |

---

## 2. Current State (audited 2026-07-20)

### 2.1 Current 9 projects

| Title | ID | Total | Done | Active¹ | Status |
|---|---|---|---|---|---|
| trading-engine | `3bfac0d2-f958-4cba-91d6-c3c7902b1b6c` | 6 | 6 | **0** | planned |
| data-pipeline | `99e0c1c1-350c-44de-9597-4bcea7517d9e` | 3 | 3 | **0** | planned |
| quant-loop-strategies | `915627d9-d97a-440d-9e50-47c46dcb0eb6` | 39 | 29 | **10** | planned |
| M-Infrastructure | `ba6b1beb-575f-4959-b19a-c5ff2c72cb0e` | 49 | 46 | **3** | planned |
| Multica HTTPS | `70760f01-5240-4f36-ad07-18d32ca4ca5c` | 4 | 2 | 2 | planned |
| Strategy Display Engine | `399861eb-fd1d-4a4a-a165-aacacad3ab14` | 1 | 1 | **0** | in_progress |
| VPVR Campaign | `d1f4d321-98ed-459d-b3d4-ceacbde591ab` | 21 | 20 | 1 | in_progress |
| multica_feature_test | `17412adf-f19f-4817-8198-a1b08ef256f4` | 0 | 0 | 0 | **completed** |
| trading | `c77cd86b-0687-4b1e-8b4e-83124aceb61c` | 0 | 0 | 0 | **cancelled** |

¹ Active = status not in {done, cancelled, archived}.

**Totals**: 123 issues in projects · 107 done/cancelled · **16 active** to relocate.

### 2.2 Root goal & special issues

| Issue | Title | Status | Current project |
|---|---|---|---|
| **SMA-30054** | [ROOT GOAL] Financial trading profit — multica 是工具不是产品 | in_progress / urgent | quant-loop-strategies |
| SMA-30199 | [SPEC v1] Strategy Development Spec — anchors 11-gate pipeline | in_review | quant-loop-strategies |
| SMA-32071 | [weekly-cron] Issue-compressor | in_progress | M-Infrastructure |

### 2.3 Autopilot dependencies (BLOCKER)

Three autopilots carry a non-null `project_id` and will break if their project is
retired without re-pointing:

| Autopilot | Bound project | Cadence | Agent |
|---|---|---|---|
| Evidence Review Gate | Strategy Display Engine (`399861eb`) | 30m | orchestrator (f375dd91) |
| Cross-Project Agent Intel Sync | Strategy Display Engine (`399861eb`) | 2h | quant-analyst (5a4c0e65) |
| Workspace Queue Balancer | Strategy Display Engine (`399861eb`) | 30m | orchestrator (f375dd91) |

All other ~20 autopilots (framework-validate, dispatch, graph-janitor,
smark-decision-loop, strategy-archiver, DEPLOY-FAIL-DETECT, REGRESSION-TEST,
Workspace-Pruner, etc.) have `project_id=null` (workspace-scoped) and are **safe**.

---

## 3. Current → Target Migration Map

### 3.1 Project-level routing

| Current project | → Target | Rationale |
|---|---|---|
| trading-engine | `infra` | All 6 issues are backtester/exec/funding-fetcher/iceberg-detector build work. Core engine components. |
| data-pipeline | `infra` | OHLCV backfill, live_data pool, symlinks = data plumbing for the backtester. |
| Multica HTTPS | `infra` | HTTPS cutover, JWT, redirect URIs = platform infrastructure. |
| Strategy Display Engine | `infra` | Visualization platform (VPVR overlay, /compare). Belongs with ops-frontend. **See §2.3 blocker first.** |
| VPVR Campaign | `strategy-discovery` | Active issue SMA-34901 is a confluence backtest hypothesis. Historic campaign archive. |
| quant-loop-strategies | **SPLIT** → discovery + validation + meta | 8 STRATEGY-EXPLORATION issues → discovery; G5 CV (SMA-34966) → validation; ROOT GOAL + SPEC → meta. |
| M-Infrastructure | **SPLIT** → validation + ops + infra | framework-validate runs → validation; weekly-cron compressor → ops; daemon/dispatch/graph-janitor → infra/ops. |
| multica_feature_test | **archive** | completed, 0 issues, throwaway experiment. |
| trading | **archive** | cancelled, 0 issues, history-only. |

### 3.2 Active-issue-level routing (the 16 that actually move)

| Issue | Status | Current | → Target | Reason |
|---|---|---|---|---|
| SMA-35006 | backlog | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 10 |
| SMA-35004 | backlog | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 08 |
| SMA-35003 | backlog | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 07 |
| SMA-35001 | in_progress | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 05 |
| SMA-35000 | in_progress | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 04 |
| SMA-34999 | in_review | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 03 |
| SMA-34998 | in_progress | quant-loop-strategies | discovery | STRATEGY-EXPLORATION 02 |
| SMA-34966 | in_progress | quant-loop-strategies | **validation** | G5 cross-framework CV (blocks LIVE) |
| SMA-30199 | in_review | quant-loop-strategies | **meta** (or validation) | SPEC v1 — anchors 11-gate pipeline |
| SMA-30054 | in_progress | quant-loop-strategies | **meta** | ROOT GOAL (see §5) |
| SMA-35069 | in_review | M-Infrastructure | **validation** | framework-validate run |
| SMA-35062 | in_review | M-Infrastructure | **validation** | framework-validate run |
| SMA-32071 | in_progress | M-Infrastructure | **ops** | weekly-cron issue-compressor |
| SMA-2952 | backlog | Multica HTTPS | **infra** | JWT claims |
| SMA-2951 | backlog | Multica HTTPS | **infra** | GOOGLE_REDIRECT_URI |
| SMA-34901 | in_progress | VPVR Campaign | **discovery** | VPVR confluence backtest |

### 3.3 Done/cancelled issues — recommendation: **leave in place, archive project**

Moving ~107 historical issues gains nothing and risks comment-thread
discontinuity. Instead:

1. Keep done/cancelled issues in their current projects.
2. Set those projects to `status=archived` (or `cancelled`) via
   `multica project status`.
3. Active work forward goes into the 5 (＋optional meta) new projects.

This shrinks the migration from ~123 issues to **16** and preserves history.

---

## 4. Per-Target Ship Criteria + Owner

| Target | Owner runtime | Ship criteria (definition of done for issues in this stream) |
|---|---|---|
| `infra` | Claude coding agents | Backtester passes G1–G7 unit tests · validators CI-green · tunnel/daemon auto-reconnect verified · display frontend serves `/compare` 200 · data pool integrity check green |
| `strategy-discovery` | Kimi research | Each hypothesis has falsifiable spec + pre-registration · backtest verdict (PASS/FAIL) posted as comment · `results-ledger.md` updated · redundant variants merged or killed |
| `strategy-validation` | Kimi research | 3-window × 3-framework CV completed · walk-forward OOS Sharpe ≥ 1.0 (≥3 extended windows) · DSR + bootstrap CI lower bound ≥ 0.5 reported · paper-trade gating decision recorded |
| `live-trading` | Codex ops | Paper connector live ≥ 7d without crash · execution slippage ≤ 2 bps vs model · monitoring alerts wired · kill-switch tested |
| `ops` | Codex ops | All autopilots active (paused ones intentional) · queue depth < 10 · 0 stale `in_progress` > 72 h · daily health digest posted · workspace-pruner green |
| `meta` | smark (+ orchestrator proxy) | ROOT GOAL referenced by all stream READMEs · SPEC current · conventions indexed |

---

## 5. ROOT GOAL SMA-30054 — Placement Decision

**Recommendation: 6th `meta` project.**

- It is priority=urgent, status=in_progress, no parent, cross-cutting by nature
  ("multica 是工具不是产品" — applies to every stream).
- Placing it inside any one of the 5 streams dilutes ownership and mis-anchors
  the issue graph.
- Duplication across all 5 (as parent reference) risks divergence — one source
  of truth is better.
- A `meta` project also absorbs SMA-30199 (SPEC v1) and future governance
  issues (verdict-block conventions, results-ledger pinning SMA-34924, etc.).

If smark rejects a 6th project: fall back to **`strategy-validation`**
(validation is the closest thing to a governance chokepoint in the 5-stream
model) and reference SMA-30054 from each stream's README.

---

## 6. Migration Mechanics

### 6.1 CLI capabilities (verified)

- `multica project create --title <t> --description <d> --lead <agent> --icon <e>` ✓
- `multica project status <id> --status archived` ✓ (retire old)
- `multica issue update <id> --project <new_project_id>` ✓ **single-issue, supports project move**
- `multica autopilot update` (re-point `project_id`) ✓ — **must check `autopilot update --help` for the exact flag name before running**

**No bulk migrate command exists.** Migration is a loop over `issue update`.

### 6.2 Pre-flight backup (mandatory, reversible)

```bash
# On .105, snapshot every issue's current project_id before touching anything.
mkdir -p ~/multica/quant-loop/research/restructure-backup
for pid in 3bfac0d2-f958-4cba-91d6-c3c7902b1b6c \
           99e0c1c1-350c-44de-9597-4bcea7517d9e \
           915627d9-d97a-440d-9e50-47c46dcb0eb6 \
           ba6b1beb-575f-4959-b19a-c5ff2c72cb0e \
           70760f01-5240-4f36-ad07-18d32ca4ca5c \
           399861eb-fd1d-4a4a-a165-aacacad3ab14 \
           d1f4d321-98ed-459d-b3d4-ceacbde591ab; do
  multica issue list --project $pid --limit 500 --output json \
    | jq -r '.issues[] | "\(.identifier)\t\(.project_id)\t\(.status)\t\(.title)"' \
    > ~/multica/quant-loop/research/restructure-backup/issues_${pid:0:8}.tsv
done
multica autopilot list --output json \
  | jq -r '.autopilots[] | "\(.id)\t\(.project_id // "null")\t\(.title)"' \
  > ~/multica/quant-loop/research/restructure-backup/autopilots.tsv
```

This TSV is the rollback ledger.

### 6.3 Ordered migration steps (each step independently reversible)

1. **Create the 5 + 1 target projects** (record new IDs):
   ```bash
   multica project create --title infra --description "Backtester, validators, tunnel, daemon, display frontend, data pipeline" --icon 🛠️ --lead <claude-agent-id>
   multica project create --title strategy-discovery --description "Hypotheses, research, new strategy directions" --icon 🔬 --lead <kimi-agent-id>
   multica project create --title strategy-validation --description "Framework CV, walk-forward, DSR, paper-trade gating" --icon ✓ --lead <kimi-agent-id>
   multica project create --title live-trading --description "Paper trading, execution, monitoring" --icon 📈 --lead <codex-agent-id>
   multica project create --title ops --description "Cron, monitoring, health patrol, autopilot" --icon ⚙️ --lead <codex-agent-id>
   multica project create --title meta --description "ROOT GOAL, SPEC, cross-cutting governance" --icon 🎯
   ```
2. **Re-point the 3 autopilots bound to Strategy Display Engine** (BLOCKER — do this before retiring `399861eb`):
   ```bash
   # Verify exact flag first:
   multica autopilot update --help
   # Then for each of: Evidence Review Gate, Cross-Project Agent Intel Sync, Workspace Queue Balancer
   multica autopilot update <ap-id> --project <infra-id>   # or --project "" for workspace-scope
   ```
3. **Migrate the 16 active issues** (loop, one per line in §3.2):
   ```bash
   multica issue update SMA-35006 --project <discovery-id>
   # ... etc
   ```
4. **Retire old projects** (archive, do not delete — preserves history):
   ```bash
   multica project status <trading-engine-id> --status archived
   # ... trading-engine, data-pipeline, Multica HTTPS, Strategy Display Engine,
   #     VPVR Campaign, quant-loop-strategies, M-Infrastructure
   multica project status <multica_feature_test-id> --status archived   # already completed
   multica project status <trading-id> --status archived                # already cancelled
   ```
5. **Update autopilot description text** that hard-codes old project names
   (graph-janitor, dispatch, framework-validate descriptions reference
   "active project" generically — these are fine; only fix any that name a
   specific retired project by title).
6. **Smoke test**: run one cycle of each critical autopilot manually and
   confirm issues land in the right project; verify ROOT GOAL SMA-30054 is
   reachable from the new `meta` project.

### 6.4 Rollback

- Per-issue: `multica issue update <id> --project <old-id>` using the backup TSV.
- Per-project: `multica project status <id> --status planned` to revive.
- Per-autopilot: re-point `project_id` back to `399861eb` from the TSV.
- The 5 + 1 new projects can be `delete`d if no issues were moved in (do not
  delete once issues are in them — archive instead).

---

## 7. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **3 autopilots break on Display Engine retirement** | High if missed | Dispatch/evidence-gate silence | §6.3 step 2 — re-point before archiving; verify with a manual run |
| ROOT GOAL becomes orphaned / loses visibility | Medium | High | §5 — dedicated `meta` project; orchestrator skill already references it |
| Done-issue history threads break if we bulk-move 107 issues | Medium | Medium | §3.3 — leave done/cancelled in place, archive projects instead |
| Autopilot descriptions name retired projects by title | Low | Low | §6.3 step 5 — grep descriptions, update |
| `meta` project introduces a 6th stream smark didn't ask for | Medium | Low | Exit plan mode with smark before creating; fall back to validation if rejected |
| Agent lead assignments (`lead_id`) become orphaned on archive | Low | Cosmetic | Acceptable; leads are advisory |
| Migration mid-cycle disrupts in-flight autopilot tasks | Medium | Medium | Execute during a known idle window; pause `Multica Dispatch` + `Idle Agent Dispatcher` during steps 3–4 |

---

## 8. Effort Estimate

| Step | Time |
|---|---|
| Pre-flight backup + dry-run mapping review with smark | 20 min |
| Create 5 + 1 target projects | 15 min |
| Re-point 3 autopilots + verify | 15 min |
| Migrate 16 active issues | 30 min |
| Archive 9 old projects | 10 min |
| Update autopilot description text | 30 min |
| Smoke test + verification | 30 min |
| Buffer / rollback drills | 30 min |
| **Total** | **≈ 3 h** |

---

## 9. Open Questions for smark

1. **6th `meta` project — yes or no?** Recommended yes (ROOT GOAL + SPEC +
   conventions). Fall back: fold into `strategy-validation`.
2. **Done/cancelled issues — move or leave?** Recommended leave-in-place +
   archive project. Alternative: full historical migration (~107 issues,
   +2–3 h).
3. **Autopilot re-pointing target for the 3 bound ones** — should they go to
   `infra` (since Display Engine is infra) or become workspace-scoped
   (`project_id=null`)? Recommended workspace-scope (`null`) — these are
   cross-cutting dispatch/evidence autopilots, not display-engine-specific.
4. **Execution window** — when can dispatch + idle-dispatcher be paused
   safely for steps 3–4?

---

## 10. Summary

- 9 → 5 (＋optional `meta`) value-stream projects.
- Only **16 active issues** actually need to move (done/cancelled stay put).
- **One hard blocker**: 3 autopilots bound to Strategy Display Engine must be
  re-pointed before that project is archived.
- ROOT GOAL SMA-30054 → recommended `meta` project.
- Migration is reversible, ~3 h, single `issue update --project` loop.
- **No execution performed. This document is the plan.**
