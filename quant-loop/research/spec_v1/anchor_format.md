# 11-gate Anchor Format v1

> Companion to `STRATEGY_DEV_SPEC.md`. Defines the machine-readable shape of a
> "gate anchor" — the unit of enforcement that maps a spec gate to a multica
> mechanism (autopilot / cron / workflow / pre-commit hook / issue template).
>
> Author: multica-code (run 07dd8587, 2026-07-20)
> Status: proposed (not yet wired)

---

## What an anchor is

A **gate anchor** is the smallest artifact that closes one enforcement gap on one
gate. It is intentionally minimal — one file, one trigger, one decision logic.
Complex multi-agent workflows are out of scope; if a gate needs more than one
file, decompose it into multiple anchors.

Each anchor answers exactly:

1. **Which gate** does this enforce? (1-11)
2. **Who owns the decision** when this anchor fires? (agent UUID or `multica` for platform-level)
3. **How is it triggered** — schedule, event, manual, or template-attached?
4. **What does it do** when it fires? (read, write, gate, escalate)
5. **What issue types** does it touch? (issue_create / status_flip / comment / archive)
6. **What evidence** does it leave? (file path, log line, issue comment marker)

---

## Anchor schema (YAML)

```yaml
# anchor — machine-readable enforcement unit for one (gate, mechanism) pair.
anchor_id: A<gate>-<seq>           # unique, e.g. A8-001
schema_version: 1                  # bump on incompatible changes

gate:
  id: 8                            # 1-11, see STRATEGY_DEV_SPEC.md
  name: "DSR"                      # human-readable, must match spec table

owner:
  agent_id: 07cc9e07-3832-4c38-8df4-565cea79cbf2  # UUID; `null` = platform-level
  agent_type: agent                # agent | member | null
  fallback: human_escalation       # what happens if owner is offline

enforcement:
  mechanism: autopilot | cron | workflow | hook | template | lint
  trigger:
    type: schedule | webhook | on_event | manual | on_commit | on_create
    spec: "37 * * * *"             # cron string OR event name OR 'manual'
    tz: Asia/Shanghai              # if schedule

blast_radius:
  issue_types: [strategy_variant, strategy_backtest, strategy_archive]
  # only issues matching these types are touched; everything else is ignored.
  status_filter: [todo, in_progress, in_review]
  # only issues in these statuses are touched.

action:
  kind: gate | lint | template_gate | escalate | archive
  # gate:          flips status based on criteria (e.g. in_review → blocked)
  # lint:          rejects PR / issue-create based on schema check
  # template_gate: blocks issue-create if template fields missing
  # escalate:      creates high-priority issue for human/agent owner
  # archive:       marks issue [ARCHIVED-NO-PASS] without status flip
  criteria: |
    DSR > 0.5 (computed from in-house + freqtrade + backtrader OOS Sharpe series)
    AND n_trades_total >= 30
  on_pass: comment("[GATE-PASS] <gate>")
  on_fail: comment("[GATE-FAIL] <gate>: <reason>") + status=blocked

evidence:
  artifact: /home/smark/multica/quant-loop/logs/<anchor_id>.jsonl
  # each run appends one line: {ts, issue_id, decision, criteria_result, evidence_ref}
  retention_days: 90

rollback:
  command: "multica autopilot delete <autopilot_id>"
  # how to disable without losing config
  config_backup: /home/smark/.multica/autopilot-backups/<anchor_id>.yaml
```

---

## Worked example: A8-001 (DSR gate)

The simplest possible anchor that closes the DSR enforcement gap (see
`gate_audit_20260720.md`, Gate 8, severity 5).

```yaml
anchor_id: A8-001
schema_version: 1

gate:
  id: 8
  name: "DSR"

owner:
  agent_id: 07cc9e07-3832-4c38-8df4-565cea79cbf2  # strategy-validator
  agent_type: agent
  fallback: human_escalation

enforcement:
  mechanism: autopilot
  trigger:
    type: schedule
    spec: "37 * * * *"
    tz: Asia/Shanghai

blast_radius:
  issue_types: [strategy_backtest]
  status_filter: [in_review]

action:
  kind: gate
  criteria: |
    DSR computed from min(in_house_sharpe, freqtrade_sharpe, backtrader_sharpe)
    AND n_oos_trades_total >= 30
    DSR > 0.5
  on_pass: comment("[GATE-PASS] DSR") + no status flip
  on_fail: comment("[GATE-FAIL] DSR: dsr=<val> trades=<n>") + status=blocked

evidence:
  artifact: /home/smark/multica/quant-loop/logs/A8-001.jsonl
  retention_days: 90

rollback:
  command: "multica autopilot delete <autopilot_id>"
  config_backup: /home/smark/.multica/autopilot-backups/A8-001.yaml
```

---

## Why this shape

- **One file, one decision.** Keeps each anchor reviewable in one screenful.
- **blast_radius is explicit.** No silent scope expansion — if a future run wants
  to touch `done` issues, it must declare that intent in YAML.
- **evidence is mandatory.** Every anchor run leaves a JSONL trail; without it
  we re-create the comment-archaeology problem (see multica-agent-base §Issue
  Metadata guidance).
- **rollback is one command.** Autopilot misbehaves → disable in one call, no
  daemon restart, no source edit. Matches the smark-decision-loop rule "never
  modify active autopilot config without explicit gate".

---

## Anti-patterns (in anchor design)

1. **Mega-anchor** (one anchor touches 4+ gates). Decompose.
2. **Hidden side effects** (anchor creates issues without `blast_radius.issue_types`
   listing them). Reject.
3. **No evidence** (`evidence.artifact` missing). Reject — every anchor must leave
   a trail.
4. **Cross-anchor coupling** (anchor A1 depends on anchor A8 running first).
   Make the dep explicit via `enforcement.trigger.type=on_event` + event name.
5. **Owner mismatch** (anchor for strategy gate owned by ops agent). Reject.

---

## How anchors relate to existing autopilots

Existing 23 autopilots in this workspace are not anchors — they are operational
loops (dispatch, sweep, archive, monitor). An anchor is a **gate enforcer**:
its purpose is to make one of the 11 spec gates un-bypassable. Most existing
autopilots will remain non-anchors; a small subset (Evidence Review Gate,
framework-validate, publish-gate, Strategy Portfolio Diversity Watchdog) can be
*upgraded* into anchors by adding `anchor_id` + `gate.id` to their YAML.

See `anchor_proposals.md` for the top-3 candidate anchors and
`diff_proposal.md` for the concrete changes to autopilot config.