#!/usr/bin/env python3
"""
agents_reclassify_sql.py — same routing policy as agents_reclassify.py
but executes the move via direct SQL against the multica-postgres container.

Bypasses the server's thinking_level validation.  Use only when the CLI
refuses to move an agent (e.g. 'existing thinking_level "medium" is not
valid for runtime "kimi"').
"""
import json, os, subprocess, sys
from collections import Counter

KIMI_KW = [
    "researcher", "advisor", "curator", "philosopher", "historian",
    "analyst", "strategist", "planner", "ethicist", "risk-manager",
    "market-analyzer", "triager", "decomposer", "meta-orchestrator",
    "cost-controller", "strategy-designer", "doc-updater",
    "scientist", "theologian", "psychologist", "economist",
    "physicist", "mathematician", "biologist", "chemist", "lawyer",
    "linguist", "anthropologist", "sociologist",
    "ethics",
]
CODEX_KW = [
    "fix", "hotfix", "quick", "fast", "test", "debug", "runner",
    "responder", "monitor", "build-error", "render", "indicator",
    "playwright", "scrape", "crawl", "fetcher", "poller", "migrator",
    "syncer", "snapshot", "backtest", "chart", "ticker", "replay",
    "trader", "executor", "resolver",
]
CLAUDE_ALWAYS = ["dispatch-", "verifier-", "codex-orchestrator-",
                 "reviewer-", "engineer", "architect", "team-lead",
                 "tech-lead", "lead-", "security-"]


def classify(name, desc):
    n = (name or "").lower()
    d = (desc or "").lower()
    if any(p in n for p in CLAUDE_ALWAYS): return "claude"
    if any(kw in n for kw in CODEX_KW): return "codex"
    if any(kw in n for kw in KIMI_KW): return "kimi"
    if any(kw in d for kw in [
        "research", "analysis", "study", "deep", "reason",
        "philos", "advisor", "curator", "memoir", "essay",
        "synthesis", "argument", "interpretation", "调研", "研究",
    ]):
        return "kimi"
    return "claude"


def load():
    r = subprocess.run(["multica", "agent", "list", "--output", "json"],
                       capture_output=True, text=True)
    agents = json.loads(r.stdout)
    runtimes = {r["id"]: r for r in json.load(open("/tmp/runtimes.json"))}
    return agents, runtimes


def main():
    apply = "--apply" in sys.argv
    agents, runtimes = load()
    target = {}
    for p in ("kimi", "codex", "claude"):
        for r in runtimes.values():
            if r["provider"] == p and r.get("status") == "online":
                target[p] = r["id"]; break
    print("Online target runtimes:")
    for p, rid in target.items():
        print(f"  {p:<7} {rid}")
    print()

    plan = []
    for a in agents:
        cur = runtimes.get(a.get("runtime_id", ""), {})
        cur_prov = cur.get("provider", "?")
        new_prov = classify(a.get("name", ""), a.get("description", ""))
        if cur_prov != new_prov:
            plan.append((a["id"], a.get("name", "?"), cur_prov, new_prov))

    by_move = Counter((c, n) for _, _, c, n in plan)
    print(f"Plan: {len(plan)} moves, {len(agents) - len(plan)} stay")
    for k, v in sorted(by_move.items()):
        print(f"  {v:>4} {k[0]} -> {k[1]}")
    print()

    if not apply:
        print("dry-run — pass --apply to actually UPDATE the database")
        return 0

    # build the SQL
    lines = ["BEGIN;"]
    for aid, name, cur, new in plan:
        rid = target[new]
        # reset fields that are provider-specific
        lines.append(
            f"UPDATE agent SET runtime_id = '{rid}', "
            f"thinking_level = NULL, model = NULL, "
            f"custom_args = '[]'::jsonb, runtime_config = '{{}}'::jsonb, "
            f"updated_at = now() WHERE id = '{aid}';"
        )
    lines.append("COMMIT;")
    sql = "\n".join(lines)

    # write sql to a file (escapes better than -c)
    sql_path = "/tmp/agent_move.sql"
    with open(sql_path, "w") as f:
        f.write(sql)

    print(f"  wrote {len(plan)} UPDATEs to {sql_path} ({len(sql)} bytes)")
    print("  executing via psql...")
    env = os.environ.copy()
    env["PGPASSWORD"] = "multica"
    r = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-U", "multica", "-d", "multica",
         "-v", "ON_ERROR_STOP=1", "-f", sql_path],
        capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print("  SQL FAILED:")
        print(r.stderr[-2000:])
        return 1
    print("  SQL OK")
    print(r.stdout[-1000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
