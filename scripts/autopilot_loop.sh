#!/usr/bin/env bash
# autopilot_loop.sh — keep working count >=20 and kimi >=3 every 5 min.
# Refreshes the snapshots first so the rebalance makes fresh plans.
set -euo pipefail
cd /home/smark/multica
export PATH="$HOME/.local/bin:$PATH"

# 1) pull fresh snapshots
multica agent list   --output json > /tmp/agents.json
multica runtime list --output json > /tmp/runtimes.json
python3 scripts/pull_issues.py

# 2) close autopilot self-loop tasks that piled up in_review / todo
python3 scripts/cleanup_autopilot.py --apply

# 3) rebalance: spread real work to idle agents, kimi floor 3, max 60 moves
python3 scripts/agents_rebalance.py --max-moves 60 --max-per-agent 1 \
                                     --min-kimi-working 3 --apply

echo "[autopilot_loop $(date -Is)] done"
