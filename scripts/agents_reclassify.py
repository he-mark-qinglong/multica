#!/usr/bin/env python3
"""
agents_reclassify.py — re-route every agent to the right provider runtime.

Routing policy
--------------
- kimi  : long thinking, NOT coding
          (research, analysis, strategy, advisors, curators, philosophers,
           planners, triagers, document writers, gatekeepers)
- codex : quick + interruptible
          (fixers, runners, renderers, scrapers, testers, migrators)
- claude: rigorous + long-running coding
          (engineers, code reviewers, security, architects, team leads,
           orchestrators, dispatch-* workflow agents, and the default
           for anything that does not clearly fit above)

The runtime_id of each agent is updated in-place.  No other field is touched
unless ``--reset-runtime-config`` is passed, which clears runtime_config and
custom_args so the agent picks up the new runtime's defaults cleanly.

Re-runs are idempotent: agents already on the right provider are skipped.
"""
import argparse
import json
import subprocess
import sys
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
# Always stays on claude (rigorous, workflow-critical)
CLAUDE_ALWAYS = ["dispatch-", "verifier-", "codex-orchestrator-",
                 "reviewer-", "engineer", "architect", "team-lead",
                 "tech-lead", "lead-", "security-"]


def classify(name, desc):
    n = (name or "").lower()
    d = (desc or "").lower()
    if any(p in n for p in CLAUDE_ALWAYS):
        return "claude"
    if any(kw in n for kw in CODEX_KW):
        return "codex"
    if any(kw in n for kw in KIMI_KW):
        return "kimi"
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


def target_runtime(runtimes, provider):
    for r in runtimes.values():
        if r["provider"] == provider and r.get("status") == "online":
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--reset-runtime-config", action="store_true",
                    help="also clear runtime_config and custom_args on move")
    ap.add_argument("--skip-working", action="store_true",
                    help="skip agents currently in 'working' state")
    args = ap.parse_args()

    agents, runtimes = load()
    target = {p: target_runtime(runtimes, p) for p in ("kimi", "codex", "claude")}
    for p, r in target.items():
        if not r:
            print(f"ERROR: no online runtime for provider={p}", file=sys.stderr)
            return 1
    print("Online runtimes by provider:")
    for p, r in target.items():
        print(f"  {p:<7} {r['id']}  {r['name']}")
    print()

    plan = []
    for a in agents:
        cur = runtimes.get(a.get("runtime_id", ""), {})
        cur_prov = cur.get("provider", "?")
        new_prov = classify(a.get("name", ""), a.get("description", ""))
        skip = args.skip_working and a.get("status") == "working"
        if cur_prov != new_prov and not skip:
            plan.append((a, cur_prov, new_prov))

    by_move = Counter((c, n) for _, c, n in plan)
    print(f"Plan: {len(plan)} moves, {len(agents) - len(plan)} stay")
    for k, v in sorted(by_move.items()):
        print(f"  {v:>4} {k[0]} -> {k[1]}")
    print()
    for a, c, n in plan[:30]:
        nm = a.get("name", "?")
        print(f"  {c:>6} -> {n:<6}  {nm}")
    if len(plan) > 30:
        print(f"  ... and {len(plan) - 30} more")
    print()

    if not args.apply:
        print("dry-run — pass --apply to actually move agents")
        return 0

    ok = fail = 0
    for i, (a, cur, new) in enumerate(plan, 1):
        rt = target[new]
        cmd = ["multica", "agent", "update", a["id"], "--runtime-id", rt["id"]]
        if args.reset_runtime_config:
            cmd += ["--runtime-config", "{}", "--custom-args", "[]"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL  {a['id']} {a.get('name'):<30} {cur}->{new}: {r.stderr[:140]}")
        if i % 25 == 0:
            print(f"  ... moved {i}/{len(plan)}", flush=True)
    print()
    print(f"apply summary: ok={ok}  fail={fail}  total={len(plan)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
