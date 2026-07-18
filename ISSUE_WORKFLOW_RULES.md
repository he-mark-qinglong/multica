# Issue Workflow Rules v2.0 | 2026-07-15

> **Mandatory for all 7 super-agents.** Replaces the old "one issue per agent action" pattern.

## Core principle

**Skills carry context, not issues.** A super-agent loads its domain skill and works within ONE issue's context. It does NOT create child issues to "delegate" to itself.

## Issue creation rules

### DO create a new issue when:
- A new strategy campaign starts (one issue per campaign family, not per iteration)
- A new infrastructure project begins (one issue per project)
- Smark creates one manually
- A genuinely new problem is discovered that needs tracking

### DO NOT create a new issue when:
- Running an autopilot cycle → post results as comment on rolling daily issue
- Cross-validating a strategy → post results as comment on the campaign issue
- Recording heartbeat/health check → post as comment on daily health issue
- Discovering an error pattern → post as comment on daily ops issue
- An evidence gate passes → just transition the source issue to done

### Rolling issues (reuse, don't create new)
Each super-agent maintains ONE rolling daily issue for cycle output:

| Agent | Rolling issue title pattern | Content |
|---|---|---|
| multica-orchestrator | `[daily-orchestration YYYY-MM-DD]` | Dispatch results, watchdog summary, evidence gate results |
| multica-strategy | `[daily-strategy YYYY-MM-DD]` | Backtest results, framework CV, VPVR progress |
| multica-ops | `[daily-ops YYYY-MM-DD]` | Heartbeat, error patterns, system health |
| multica-code | (on-demand only) | Code review results |
| knowledge-curator | (on-demand only) | Knowledge queries |
| persona-advisor | (on-demand only) | Persona critiques |
| quant-analyst | (on-demand only) | Analysis results |

### Campaign issue pattern
One issue per strategy family:
```
Title: [Campaign YYYY-MM-DD] <strategy_family> (Sharpe>=1, ann>=15%)
Body: hypothesis, entry/exit rules, timeframe, symbols
Comments: iter#N results, framework CV, metrics, decisions
Status lifecycle: todo → in_progress → done (when family closed)
```

## Autopilot issue creation

Autopilots should default to `run_only` mode. Only use `create_issue` mode when:
- A genuinely new problem requires human attention
- A threshold breach needs escalation

**NEVER** create individual issues for:
- Routine patrol results (no-op cycles)
- Heartbeat health checks
- Evidence gate passes
- Dispatch observations

## Smark decision routing

All `[need-smark-decision]` items go into the **smark-decision digest** (batched every 2h by autopilot), NOT individual issues. The digest:
- Lists all pending decisions with recommendations
- Smark replies once to resolve all
- Auto-resolves safe patterns (per orch-smark-proxy skill)

## Consolidation maintenance

Weekly (Sunday 9am, strategy-archiver autopilot):
- Scan done issues from the week
- Group by domain
- Post a weekly digest comment on the domain's rolling issue
- Close individual fragment issues that are covered by the digest
