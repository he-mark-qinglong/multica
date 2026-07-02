# multica/scripts

Operational scripts for the smark-multica self-host workspace.

## Layout

| file | what it does |
|---|---|
| `agents_reclassify.py`     | CLI route 168 agents to the right provider (claude/codex/kimi) using `multica agent update`. Useful but limited by the API: agents with an invalid `thinking_level` for the target runtime are rejected (HTTP 400). |
| `agents_reclassify_sql.py` | Same routing logic, but applies via direct SQL to Postgres. Bypasses the server's `thinking_level` validation in one transaction. |
| `agents_rebalance.py`      | Spread workable issues (todo/blocked/in_progress) across idle online agents. Label-keyword routing decides the preferred provider; `--min-kimi-working` (default 3) keeps kimi above a floor. Default `--max-moves 40`, `--max-per-agent 1`. |
| `cleanup_autopilot.py`     | Cancel (soft-delete) autopilot-tagged issues stuck in `in_review` or `todo`. Autopilot titles start with `[<prefix>]` (Dispatch-Critic, Evidence-Gate, PUMP-*, DRAIN-*, …); agent-runtime tasks accumulate there because agents cannot move multica-internal housekeeping to `done`. Dry-run by default; pass `--apply` to commit. |
| `pull_issues.py`           | Page every issue in the workspace into `/tmp/issues.jsonl`. The server silently caps `--limit` at 100 regardless of the value you pass, so paging with `--offset` and dedup-by-id is required. |
| `autopilot_loop.sh`        | Cron wrapper: refresh snapshots → cleanup autopilot → rebalance. Run every 5 minutes from cron to keep working count above 20 and kimi above 3. |

## Provider classification policy

| Provider | Specialty | Routing in `agents_rebalance.py` |
|---|---|---|
| claude (31) | rigorous, long-running code | `code, review, security, architecture, implement, refactor, feature, bug` |
| codex (5)   | quick, interruptible        | `fix, hotfix, quick, test, build, migrate, snapshot, render, chart, indicator, playwright, scrape` |
| kimi (132)  | long thinking, non-code     | `analysis, research, strategy, memo, doc, plan, design, 调研, 研究, 策略, 设计, 哲学, 分析, 审查` |

Default provider when no label match: `claude`.

## Quick start

```bash
# 1. refresh snapshots
multica agent list   --output json > /tmp/agents.json
multica runtime list --output json > /tmp/runtimes.json
python3 scripts/pull_issues.py

# 2. cancel autopilot self-loop tasks
python3 scripts/cleanup_autopilot.py --apply

# 3. dispatch real work
python3 scripts/agents_rebalance.py --max-moves 60 --min-kimi-working 3 --apply

# 4. install cron (every 5 min)
echo "*/5 * * * * /home/smark/multica/scripts/autopilot_loop.sh >> /var/log/multica_autopilot.log 2>&1" | crontab -
```

## CLI quirks worth knowing

- `multica issue list --limit 500` returns at most 100 regardless; page with `--offset` and dedup by id (`scripts/pull_issues.py` does this).
- `multica agent update` accepts `--thinking-level` (not in `--help`, but server-side flag exists); rejected by API when the agent's current level is invalid for the new provider — bypass with `agents_reclassify_sql.py`.
- `multica issue status` is the only "delete" semantics available (no `delete`/`archive` subcommand); for autopilot housekeeping we move to `cancelled`.
