# Multica operational scripts

Helper scripts for the self-hosted Multica deployment under `/home/smark/multica`.

## `agents_reclassify.py` & `agents_reclassify_sql.py`

Re-route every agent to the provider runtime that matches its specialty.

### Routing policy

| Provider | Specialty                                                  |
|----------|------------------------------------------------------------|
| `kimi`   | long thinking, NOT coding (research, analysis, strategy, advisors, curators, philosophers, planners, triagers, document writers) |
| `codex`  | quick + interruptible (fixers, runners, renderers, scrapers, testers, migrators) |
| `claude` | rigorous + long-running coding (engineers, code reviewers, security, architects, team leads, orchestrators, dispatch-* workflow agents, and the default for anything unclear) |

### How it works

`agents_reclassify.py` calls `multica agent update --runtime-id <new>` for
every agent that is on the wrong provider.  It works for agents that have
no provider-specific thinking_level, but **fails with 400** for any agent
whose current `thinking_level` is not valid for the destination runtime
(common when moving a `medium`-thinking agent to kimi).

`agents_reclassify_sql.py` is the same logic but writes directly to
Postgres in one transaction, also clearing `thinking_level`, `model`,
`custom_args`, and `runtime_config`.  Use it when the CLI refuses.

### Usage

```bash
# dry-run — show the plan
python3 scripts/agents_reclassify.py

# apply via the CLI (skips working agents, clears runtime_config)
python3 scripts/agents_reclassify.py --apply --skip-working --reset-runtime-config

# apply via direct SQL (bypasses thinking_level validation)
python3 scripts/agents_reclassify_sql.py --apply
```

### Last reclassify

- Date: 2026-07-02 15:00 +08
- CLI pass: 20 moved (null-thinking agents)
- SQL pass: 120 moved (medium-thinking agents cleared)
- Final distribution: claude=31, codex=5, kimi=132, total=168

## `agents_rebalance.py`

Spread open Multica issues across idle online agents, with role-aware
routing that picks the right provider for each issue's label, and an
optional floor for kimi-runtime working agents.

### Routing policy

| Issue label substring                | Preferred provider |
|--------------------------------------|--------------------|
| analysis / research / strategy / memo / doc / plan / design | `kimi` |
| fix / hotfix / quick / test / build / migrate / render / chart | `codex` |
| code / review / security / architecture / implement / refactor | `claude` |
| (no match)                           | `claude` |

The full pool of idle online agents is searched.  For each issue, the
script first looks for a target on the preferred provider; if no agent
in that provider has a free slot, it falls back to any other provider.
While the kimi-runtime working count is below `--min-kimi-working`,
kimi targets are prioritised regardless of the issue's preferred
provider.

### Usage

```bash
# refresh snapshots first
multica agent list  --output json > /tmp/agents.json
multica runtime list --output json > /tmp/runtimes.json
multica issue list  --limit 100 --offset 0 --full-id --output json > /dev/null
# (use the helper pull_issues.py in this directory to page through all 1.6k issues)

# dry run
python3 scripts/agents_rebalance.py --max-moves 40 --min-kimi-working 3

# actually do it
python3 scripts/agents_rebalance.py --max-moves 40 --min-kimi-working 3 --apply
```

### Last successful run

- Date: 2026-07-02 15:10 +08
- Moves: 40 (13 kimi, 1 codex, 26 claude), 0 failures
- kimi working before: 1
- kimi working after : 4 (above --min-kimi-working=3 floor)
- total working       : 20 (claude=15, codex=1, kimi=4)
