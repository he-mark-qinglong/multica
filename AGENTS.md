# Repository Guidelines

This file provides guidance to AI agents when working with code in this repository.

> **Single source of truth:** This file is a concise pointer document.
> All authoritative architecture, coding rules, commands, and conventions
> live in **CLAUDE.md** at the project root. Read that file first.

## Quick Reference

### Architecture

Go backend + monorepo frontend (pnpm workspaces + Turborepo) with shared packages.

- `server/` — Go backend (Chi router, sqlc, gorilla/websocket)
- `apps/web/` — Next.js frontend (App Router)
- `apps/desktop/` — Electron desktop app
- `packages/core/` — Headless business logic (Zustand stores, React Query hooks, API client)
- `packages/ui/` — Atomic UI components (shadcn/Base UI, zero business logic)
- `packages/views/` — Shared business pages/components
- `packages/tsconfig/` — Shared TypeScript config

### State Management (critical)

- **React Query** owns all server state (issues, members, agents, inbox, workspace list)
- **Zustand** owns all client state (current workspace selection, view filters, drafts, modals)
- All Zustand stores live in `packages/core/` — never in `packages/views/` or app directories
- WS events invalidate React Query — never write directly to stores

### Package Boundaries (hard rules)

- `packages/core/` — zero react-dom, zero localStorage, zero process.env
- `packages/ui/` — zero `@multica/core` imports
- `packages/views/` — zero `next/*`, zero `react-router-dom`, use `NavigationAdapter` for routing
- `apps/web/platform/` — only place for Next.js APIs

### Commands

```bash
make dev              # Auto-setup + start everything
pnpm typecheck        # TypeScript check
pnpm test             # TS unit tests (Vitest)
make test             # Go tests
make check            # Full verification pipeline
```

See CLAUDE.md for the complete command reference.

- **Org structure (v1.0 2026-07-25)** — `docs/plans/agent-org-structure-2026-07-25.md`. Layers: L1 协调 (orchestrator/ops) → L2 研究主线 (quant-researcher/research-agent/analyst, single-threaded) → L3 执行 (strategy-worker-1/2, multica-strategy/code, ops-worker-1) → L4 判决签核 (decision-maker/signoff-proxy) → L5 知识表达 (curator/persona-advisor). Every agent's instructions carry a 协作定位 block (accept/reject/route). Rejection discipline: wrong-layer task → blocked + ESCALATE comment naming the right assignee, never force it. Sign-off isolation: L2/L3 never sign their own output.

- **Dispatch landing protocol (hard-won 2026-07-25)** — multica daemon tasks run in EPHEMERAL workspaces (`~/multica_workspaces/`, GC'd ~1h after completion). ANY dispatched execution task MUST end with: `git add <only task files>` → commit → `git push https://github.com/he-mark-qinglong/multica.git HEAD:agent/<agent-name>/<task-id>` → post branch name + acceptance output in the issue comment. No push = work destroyed. Never write "no git operations" into EXECUTION task cards (that rule is for planning agents only). Harvest = parent merges agent branches to main.

## Comment Schema Convention (mandatory 2026-07-19)

Every comment posted by an agent on a multica issue MUST start with a type tag on the first line:

`[type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>`

where `<TYPE>` ∈:

- `STATUS` — progress update (what was done, what's next)
- `DECISION` — chose X over Y, with reason
- `EVIDENCE` — factual data (metrics, test results, CV numbers)
- `KILL` — strategy/issue killed, with reason + evidence pointer
- `ESCALATE` — requesting human (smark) decision; must include the actual question
- `SIGNOFF` — agent or smark-proxy approving/rejecting deliverable
- `NUDGE` — re-dispatch prompt to another agent/runtime
- `NOOP` — explicit "nothing to do" with reason (cron sweeps especially)

The body that follows is free-form markdown, but the first line MUST match the schema. Validator (TBD `comment-janitor` cron) will flag violations with `OFFSPEC` label.

### Examples

- `[STATUS] 2026-07-19T22:45+08 run 3c4ddf23 started on SMA-30199 — Claude picking up SPEC v1 work`
- `[KILL] 2026-07-19T23:25+08 vpvr_xs_pairs_30m_funding_filter_20260712 — framework CV sharpe -4.86 vs in-house +0.46, walk_forward_ratio 0.127`
- `[ESCALATE] 2026-07-19T20:00+08 question: should we top up token-plan quota to resume vpvr-funding-carry-asym, or pause? (decision B taken by smark-proxy)`

### What this enables

- Long-term searchability (find all DECISION comments in a date range)
- Automated extraction of KILL/ESCALATE history
- comment-janitor cron can flag drift / missing schema
- downstream analytics (decision provenance, escalation latency)

## Contract Execution Rules (mandatory 2026-07-26)

Every research/execution issue dispatched with a `task-contract-template.yaml` block MUST be executed according to the contract. The contract is not advisory text; missing any hard requirement blocks SIGNOFF.

### 1. No contract, no dispatch

- orchestrator MUST embed the contract block at the top of the issue description before claiming begins.
- worker/agent MAY refuse to claim if the contract block is missing, malformed, or assigns the wrong layer.

### 2. Attestation isolation

- If `acceptance_evidence.requires_attestation_run: true` (or any equivalent flag), the evidence gate MUST be run by an agent **other than the assignee**.
- The attestation agent posts an `[EVIDENCE]` comment containing:
  - `attestation_id`: a short random id (e.g. `att-7f3a9b2`)
  - `repro_command`: the exact command used
  - `sha`: the Git SHA of the branch tested
  - `result`: PASS / FAIL with key numbers
- SIGNOFF comments MUST reference the `attestation_id`. Signoff without a valid attestation_id is invalid.
- Self-attestation by the assignee is allowed ONLY if the contract explicitly sets `self_attestation: true`.

### 3. Signoff chain discipline

- `signoff_chain` lists the required approvers in order.
- `smark-signoff-proxy` is the default L5 final approver for execution tasks.
- smark (human member) MAY:
  - post a `[DECISION]` comment giving the verdict, then let `smark-signoff-proxy` emit the formal `[SIGNOFF]`
  - bypass the proxy in emergencies and post `[SIGNOFF]` directly, but MUST include the word `bypass` in the summary
- Research-line KEEP/KILL verdicts remain with `smark-decision-maker` + human signoff; never let an L2/L3 agent sign off its own strategy output.

### 4. Acceptance evidence checklist

Before SIGNOFF, every required item in `acceptance_evidence` MUST be verified and referenced in the attestation or signoff comment:

- `git_remote_branch_exists` — branch pushed to `he-mark-qinglong/multica`
- `file_in_main` — file present in `main` after merge
- `visualization_bundle_exists` — see `docs/plans/quant-visualization-mandate.md`
- `attestation_run` — independent evidence gate run

### 5. Comment schema enforcement

- Agent comments missing the `[type=...]` tag are **OFFSPEC**.
- `comment-janitor` cron SHOULD flag OFFSPEC comments and add the `OFFSPEC` label.
- A SIGNOFF comment that is OFFSPEC does not count as valid signoff.

### 6. Issue status

- Use `done` to close a completed issue. `closed` is not a valid status in the database check constraint.
- Do NOT change status to `done` until all acceptance evidence and signoff are complete.

## Knowledge snapshots (workspace-level, 2026-07-18 onward)

Daily workspace snapshots live under `~/multica/knowledge/curator/<date>-<slug>.md`. Each one is the evidence-backed summary of the day's workspace events; this section is the terse pointer so anyone working in this repo can locate today's facts without re-deriving them.

### 2026-07-18 — framework fixes, H3 ship, runtime split, cron self-tune

- **max_dd sentinel fix (landed 2026-07-18 19:19)** — fractional-replay NAV produced `max_dd ≈ 0` for any profitable strategy (methodology artefact). U2 audit chain ([SMA-34926](https://multica/issue/61804ebc-0987-42a2-b0c4-3c07aa1ceec8) → [SMA-34927](https://multica/issue/e511d7c9-2258-479b-b9a3-22b8f4583595)) fixed daily-resampled portfolio-NAV path so framework max_dd agrees with in-house per-symbol-worst within W5 tolerance. Bug fix itself under [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405). Commit SHA not in ledger (unverified).
- **H3 PROFITABLE ship (PR#6)** — `mtf_xs_pairs` H3 BTC+SOL pair passed all gates: OOS walk-forward Sharpe 2.773 (mean of 7 windows), ann 59.8%, bootstrap CI lower 1.914. Commit `26440acd`. ETH/SOL leg (U7) accepted via [SMA-34951](https://multica/issue/0c74f1c0-...). LIVE candidacy still gated on G5 cross-framework CV ([SMA-34966](https://multica/issue/...)). Family `mtf_xs_pairs` not yet exhausted.
- **Agent / runtime split** — 14 agents across 3 runtimes. Kimi `a148b4d2` (5 agents: quant-researcher, quant-analyst, multica-orchestrator, multica-strategy, quant-research-agent). Codex `c3791fa0` (4: knowledge-curator, persona-advisor, multica-ops, ops-worker-1). Codex `07dd8587` (5: multica-code `00589faa`, strategy-worker-1/2, smark-decision-maker, smark-signoff-proxy). `00589faa` k3 403 first seen 2026-07-18T19:24:26; resolved for sign-off chain via M3 swap.
- **Cron self-tune pattern (2026-07-18)** — 4 heavy crons converted to wrapper-style subagent dispatches: `pool` (Idle Agent Dispatcher `0fc298fa`, `*/3 * * * *`, since 2026-07-15T23:11:19), `orchestrator` (multica-dispatch, since 2026-07-10T06:47:12), `decision-triage` (Human Escalation Router, since 2026-07-05T05:32:02), `signoff` (Evidence gatekeeper, since 2026-06-30T18:33:04). Mechanism: each heavy cron now wakes an idle-dispatcher subagent that does the work in-foreground and posts results, instead of running inline in the cron tick.

→ Full evidence + accepted/unverified status: `~/multica/knowledge/curator/2026-07-18-knowledge-snapshot.md`

## Quant research operating model (2026-07-25 onward, authoritative)

Full plan: `docs/plans/multica-quant-permanent-loop-2026-07-25.md`. Read it before doing any strategy/validation work. Ten-year vision (top layer, read for direction decisions): `docs/plans/vision-10y-2026-2036.md`. Terse rules:

- **Model policy** — ALL agents run `caocao-m3` (MiniMax-M3 via local gateway). K3 is not used. If m3 can't decide, ESCALATE to human — never upgrade the model.
- **Orchestration** — multica server @ `192.168.0.105:8080` is the dispatch hub. Local CLI/daemon point there, NOT localhost:8080 (stale Docker). Runtimes: Mac (MacBook-Pro-2): Claude `940d2b93`, Codex `0e57fd85`, Kimi `2ff52f36`; server-105 (`192.168.0.105`, systemd `multica-daemon.service`): Claude `07dd8587`, Codex `c3791fa0`, Kimi `a148b4d2` (names still say "(smark)" — they are the old registrations revived on .105, no rename API). **ALL 14 agents run on the two Kimi runtimes since 2026-07-25 (7+7)** — Mac Kimi: quant-researcher, knowledge-curator, persona-advisor, quant-analyst, multica-orchestrator, multica-code, multica-ops; .105 Kimi: multica-strategy, strategy-worker-1/2, ops-worker-1, quant-research-agent, smark-decision-maker, smark-signoff-proxy. Claude/Codex runtimes stay online as spare capacity with zero agents.
- **105 model path** — kimi `caocao-m3` on .105 → `127.0.0.1:18091` → systemd `caocao-tunnel.service` (ssh → smark@192.168.0.102 → 10.6.0.91). Verified 200 2026-07-25. A stale duplicate `multica-minimax-tunnel.service` (via .101, broken host key) was killed; do not resurrect it. NOTE: 10.6.0.91 is ONLY reachable via .102 — if .102 sleeps/goes offline the model path dies fleet-wide (tunnels auto-recover when it's back).
- **Disk incident (2026-07-25 23:47)** — .105 hit 100% disk (73G in `~/multica_workspaces/<ws-uuid>/`, 591 orphaned task workspaces; daemon GC_TTL=4h exists but leaks orphans across restarts) → postgres crash-looped in recovery → server returned misleading 401 "invalid token" (real cause: DB unreachable). Fixed: deleted workspaces >3h old (57G freed), restarted `multica-postgres-1`. Safety net now in .105 crontab: hourly workspace sweep (>6h) + 4h disk >90% alert to `~/disk-alert.log`. If CLI suddenly 401s, check .105 disk FIRST before re-login.
- **Comment-wake guard (deployed 2026-07-25)** — comments on done/cancelled issues NEVER wake agents (server `triggerTasksForComment` terminal-status guard; verified live on .105). To re-activate a terminal issue, change its status first. Gate is also strict server-side now (W2-T1): missing metric field = fail, sharpe-only no longer passes.
- **Infra sprint status (2026-07-25 22:25)** — Wave-0 CLOSED (24/24). Wave-1: 22/24 done (W1-T8/SMA-36467 + W1-T11/SMA-36458 redispatched — agents reported done but never pushed branches; work lost to workspace GC). **W4-T06 dual-anchor gate PASSED (orchestrator-rerun: 8428 trades bit-identical vs base engine; 704 trades/Sharpe 8.0735 vs quick_verify) — Wave 2/3 W4 UNBLOCKED.** 4 wave-2/3 tasks landed early (INT-06, W2-T9, W3-T10, W5-T13). main=f5db9e102. Lessons: (1) m3 "completed" ≠ landed — only merged artifacts in main count; W3-T1 paths.py had to be redone by orchestrator. (2) Agent branches are cumulative per-workspace (one merge can carry several tasks' commits). (3) signal-enhance-h3 full_history research code was uncommitted in the Mac worktree — salvaged to main (9771f32c8). Deferred merges: SMA-34962 CI-hook chain (3f31139b, needs overlap review), slippage_sqrt (392ab3be, P7 campaign). Assignments in `docs/plans/infra-sprint-2026-07-25/issue-map.json`. If `pnpm typecheck` goes red with broken `@multica/*` symlinks, run `pnpm install` first.
- **Epoch loop (1d)** — 09:00 research-scout 调研/刷新 SPEC 池（主流方法论调研，cost-cap 预检先行）→ 10:00 选 1-2 个 → 白天实现+回测 → 17:00 全管线验证 → 20:00 KEEP/KILL 判决 → 21:00 epoch-retro 日报+归档+次日优先级。当天无产出记 NOOP，次日照常。
- **Validation = one authoritative path** — `_shared/run_backtest.py` + `compute_metrics.py`; pre-registered SPEC; full-history 7-window walk-forward OOS; dual-framework CV; 60bps fee shock; G gates; independent sign-off. Research agents never sign off their own strategy.
- **Visualization mandate (2026-07-26)** — No KEEP/KILL verdict without human-reviewable trade-history images. Every strategy must ship in `results/`: `equity_curve.png` (combined + long + short with drawdowns), `trade_history_long.png`, `trade_history_short.png` (K-line + entry/exit markers, ≥50 trades across ≥3 regimes), `trade_diagnostic.png`, and `returns_heatmap.png`. VPVR-based strategies additionally ship `vpvr_overlay.png`. See `docs/plans/quant-visualization-mandate.md` and use `_shared.visualization.StrategyVisualizer`. Missing visuals = attestation fails automatically. Researcher comment must explain the 3 largest drawdowns and 5 largest contributing trades using the images.
- **Strategy state (2026-07-26 02:30, post se_h3 verdict)** — NO live-eligible strategy exists. **se_h3 KILLED by smark-decision-maker 2026-07-26 (SMA-36570)**: 7-window OOS Sharpe 9.21 (CI [7.79,11.04]) was real, but corrected fee shock (SMA-36566: per_trade_fraction must be 1.0 not 0.005 — 200× understatement bug that also falsified the family's historical fee-robustness claims) gives 4bps +5.98 / 24bps −17.33 / 60bps −38.80; break-even cost 20bps pair-RT vs mean gross 17.78bps/trade; G4 PF 1.098 < 1.5 (win-rate-driven fragility). **The entire mtf_xs_pairs family (H1–H4 + se_h3) is KILLED-FAMILY — never parameter-sweep it again.** Prior "H1 fee-robust +0.728" was the same 200× artifact. Killed families: 1m/5m klines reversal, funding-carry, 4h single-TF stat-arb, microstructure features, **mtf_xs_pairs whole family**. Open path forward: the only way any thin-margin stat-arb lives is execution cost ≤20bps → maker execution research (vision T10) is now the critical prerequisite, not a side quest. 57+ falsified dirs in `quant-loop/strategies/_graveyard/`.
- **Infra (launchd-managed since 2026-07-25)** — `com.smark.caocao-tunnel` (18091), `com.smark.multica-daemon`, `com.smark.caocao-model-proxy` (18092, rewrites caocao-m3→MiniMax-M3 for Codex) all run under launchd with KeepAlive; plists in `~/Library/LaunchAgents/`. If Codex tasks report model errors, check 18092 first (`launchctl list | grep caocao`). Watchdog: autopilot `infra-health-watchdog` `c84304df` (*/10min).
- **Daemon bandwidth (updated 2026-07-25 17:45)** — `MULTICA_DAEMON_MAX_CONCURRENT_TASKS`: **Mac=12, .105=20**. Mac = MacBookAir M1 8C/16GB — macOS "used memory" is misleading (compressor+reclaimable); trust `memory_pressure` free% and CPU idle, not `top` PhysMem. Measured at 6 slots: 58% mem free, 59% CPU idle, kimi procs ~8% CPU → real ceiling is much higher than 6; load spikes seen earlier were self-inflicted (own pytest/typecheck). Raise via `~/Library/LaunchAgents/com.smark.multica-daemon.plist` + `launchctl kickstart -k gui/<uid>/com.smark.multica-daemon`; on .105 edit `~/.config/systemd/user/multica-daemon.service` + `systemctl --user daemon-reload && restart multica-daemon`. Restarts are safe: interrupted tasks return to queue via claim-lease expiry and re-dispatch. Per-agent caps: workers max_concurrent=20, quant-researcher=6, smark-*=3. Known issue: L1 autopilot dispatches starve behind L3 batches when daemon is full — fix spec'd in SMA-36539 (wait_reason classification + fairness).
- **Swarm capacity (tested 2026-07-25)** — AgentSwarm hard max is **128 items per call** (compiled constant `MAX_AGENT_SWARM_SUBAGENTS`, no config/env override; 300 rejected at arg validation, 128/128 probes completed OK). Split bigger jobs into multiple 128-item calls. Concurrency (queue depth, not the cap) IS tunable via env `KIMI_CODE_AGENT_SWARM_MAX_CONCURRENCY` (positive int).
- **Parallelism doctrine (smark 2026-07-25)** — Infrastructure/execution work (pipeline code, batch backtests, window/param fan-out, archival) → multi-agent swarm. **Strategy ideation and hypothesis reasoning → single-threaded**: one research main thread holds the evolving context end-to-end; never fan out idea generation and stitch fragments. Swarm may only execute validation of an already-formed SPEC; verdicts return to the same main thread.
- **Git** — push via HTTPS to fork `he-mark-qinglong/multica` (origin `multica-ai/multica` is read-only, 403). Never commit others' uncommitted changes in the worktree.
- **Python** — always `/Users/mark/sdk/mamba-envs/trading/bin/python3` (default python3 lacks pyarrow).

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **multica** (97334 symbols, 252907 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/multica/context` | Codebase overview, check index freshness |
| `gitnexus://repo/multica/clusters` | All functional areas |
| `gitnexus://repo/multica/processes` | All execution flows |
| `gitnexus://repo/multica/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
