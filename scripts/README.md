# Multica operational scripts

Helper scripts for the self-hosted Multica deployment under `/home/smark/multica`.

## `agents_rebalance.py`

Spreads open work across the idle online agents so 100+ registered agents
don't sit unused while a handful hog the queue.

### What it does

1. Loads `/tmp/issues.jsonl`, `/tmp/agents.json`, `/tmp/runtimes.json`
   (snapshots from `multica issue list` / `agent list` / `runtime list`).
2. Builds a **candidate pool** = issues with status `todo | blocked | in_progress`,
   currently assigned to an agent.  Prioritised:
     - p0 = assigned to an **offline**-runtime agent (stuck on a Mac that's off)
     - p1 = assigned to a known hot agent (`dispatch-critic`, `planner`, etc.)
     - p2 = everything else
3. Builds a **target pool** = online + idle + non-archived agents with
   `cur_load < max_concurrent_tasks`.
4. Round-robin assigns at most `--max-per-agent` issues to each target.
5. For each move:
   - if `status == 'blocked'`, flip to `todo` (unblock)
   - `multica issue assign <id> --to-id <target>`
   - `multica issue rerun <id>`

### Usage

```bash
# refresh snapshots
multica issue list --limit 100 --offset 0  --full-id --output json > /dev/null
# (use the helper pull_issues.py in this directory to page through all 1.6k issues)

# dry run — show what would happen
python3 scripts/agents_rebalance.py --target-agents 40 --max-per-agent 1

# actually do it
python3 scripts/agents_rebalance.py --target-agents 40 --max-per-agent 1 --apply
```

### Why this script exists

We routinely see the dispatch-critic / dispatch-evidence-reviewer /
codex-orchestrator-02 trio hold 50+ open issues each while 120+ idle
agents (mostly curators, advisors, specialist roles) sit unused.  This
script pulls work off the overloaded and offline-runtime agents and
parks one (or more) on each fresh online agent, then triggers a fresh
run so the daemon actually picks them up.

### Idempotency

Re-runs are safe.  The plan only proposes (issue, target) pairs where
the issue is currently assigned to a different agent; existing
assignments to a target are kept.

### Last successful run

- Date: 2026-07-02 14:12 +08
- Moves: 40 (38 unblock + 2 reassign)
- Failures: 0
- Working agents before: 3
- Working agents after : 21 (then climbing)
