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

## Knowledge snapshots (workspace-level, 2026-07-18 onward)

Daily workspace snapshots live under `~/multica/knowledge/curator/<date>-<slug>.md`. Each one is the evidence-backed summary of the day's workspace events; this section is the terse pointer so anyone working in this repo can locate today's facts without re-deriving them.

### 2026-07-18 — framework fixes, H3 ship, runtime split, cron self-tune

- **max_dd sentinel fix (landed 2026-07-18 19:19)** — fractional-replay NAV produced `max_dd ≈ 0` for any profitable strategy (methodology artefact). U2 audit chain ([SMA-34926](https://multica/issue/61804ebc-0987-42a2-b0c4-3c07aa1ceec8) → [SMA-34927](https://multica/issue/e511d7c9-2258-479b-b9a3-22b8f4583595)) fixed daily-resampled portfolio-NAV path so framework max_dd agrees with in-house per-symbol-worst within W5 tolerance. Bug fix itself under [SMA-34922](https://multica/issue/3c857ceb-0729-4315-8af3-d563b5f6b405). Commit SHA not in ledger (unverified).
- **H3 PROFITABLE ship (PR#6)** — `mtf_xs_pairs` H3 BTC+SOL pair passed all gates: OOS walk-forward Sharpe 2.773 (mean of 7 windows), ann 59.8%, bootstrap CI lower 1.914. Commit `26440acd`. ETH/SOL leg (U7) accepted via [SMA-34951](https://multica/issue/0c74f1c0-...). LIVE candidacy still gated on G5 cross-framework CV ([SMA-34966](https://multica/issue/...)). Family `mtf_xs_pairs` not yet exhausted.
- **Agent / runtime split** — 14 agents across 3 runtimes. Kimi `a148b4d2` (5 agents: quant-researcher, quant-analyst, multica-orchestrator, multica-strategy, quant-research-agent). Codex `c3791fa0` (4: knowledge-curator, persona-advisor, multica-ops, ops-worker-1). Codex `07dd8587` (5: multica-code `00589faa`, strategy-worker-1/2, smark-decision-maker, smark-signoff-proxy). `00589faa` k3 403 first seen 2026-07-18T19:24:26; resolved for sign-off chain via M3 swap.
- **Cron self-tune pattern (2026-07-18)** — 4 heavy crons converted to wrapper-style subagent dispatches: `pool` (Idle Agent Dispatcher `0fc298fa`, `*/3 * * * *`, since 2026-07-15T23:11:19), `orchestrator` (multica-dispatch, since 2026-07-10T06:47:12), `decision-triage` (Human Escalation Router, since 2026-07-05T05:32:02), `signoff` (Evidence gatekeeper, since 2026-06-30T18:33:04). Mechanism: each heavy cron now wakes an idle-dispatcher subagent that does the work in-foreground and posts results, instead of running inline in the cron tick.

→ Full evidence + accepted/unverified status: `~/multica/knowledge/curator/2026-07-18-knowledge-snapshot.md`

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
