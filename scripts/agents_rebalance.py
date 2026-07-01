#!/usr/bin/env python3
"""
agents_rebalance.py — spread open Multica issues across idle online agents.

Strategy
--------
- Candidates = issues with status in {todo, blocked, in_progress} currently
  assigned to an agent.  Priority order:
     0  offline-runtime agents (stuck on a Mac that's off)
     1  hot overloaded online agents (dispatch-critic etc.)
     2  other online agents
- Targets = online + idle + not archived + (cur_load < max_concurrent_tasks).
- Round-robin: each target gets up to ``--max-per-agent`` issues.

For each (issue, target_agent):
    1. if status == 'blocked',  issue status todo
    2. issue assign <id> --to-id <target_agent>
    3. issue rerun <id>

Without ``--apply`` the plan is printed and nothing is changed.
"""
import argparse
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict

OPEN_SET = {"backlog", "todo", "in_progress", "in_review", "blocked"}
WORKABLE = {"todo", "blocked", "in_progress"}
HOT_AGENT_PREFIXES = (
    "dispatch-critic", "dispatch-evidence-reviewer",
    "codex-orchestrator-02", "planner", "verifier-general",
)


def load_state():
    issues   = [json.loads(l) for l in open("/tmp/issues.jsonl")]
    agents   = json.load(open("/tmp/agents.json"))
    runtimes = {r["id"]: r for r in json.load(open("/tmp/runtimes.json"))}
    return issues, agents, runtimes


def is_online(runtimes, agent):
    rt = runtimes.get(agent.get("runtime_id", ""), {})
    return rt.get("status") == "online"


def build_pools(issues, agents, runtimes, target_agents_n, max_per_agent):
    agents_by_id = {a["id"]: a for a in agents}
    online_rt_ids = {rid for rid, r in runtimes.items() if r.get("status") == "online"}

    load = Counter()
    for i in issues:
        if i["status"] in OPEN_SET and i.get("assignee_type") == "agent":
            load[i["assignee_id"]] += 1

    # all idle online agents that have headroom
    candidate_targets = [a for a in agents
                         if a.get("runtime_id") in online_rt_ids
                         and a.get("status") == "idle"
                         and not a.get("archived_at")
                         and (a.get("max_concurrent_tasks") or 0) > load.get(a["id"], 0)]
    candidate_targets.sort(key=lambda a: -(a.get("max_concurrent_tasks") or 0))
    target_pool = candidate_targets[:target_agents_n]

    # workable candidates
    candidates = []
    for i in issues:
        if i["status"] not in WORKABLE: continue
        aid = i.get("assignee_id")
        if not aid: continue
        ag = agents_by_id.get(aid)
        if not ag: continue
        online = is_online(runtimes, ag)
        name = ag.get("name") or ""
        is_hot = any(name.startswith(p) for p in HOT_AGENT_PREFIXES)
        if not online:
            prio = 0
        elif is_hot:
            prio = 1
        else:
            prio = 2
        candidates.append((prio, i["status"], i["created_at"],
                           i["id"], i, aid, ag))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))

    return target_pool, candidates, load, agents_by_id


def make_plan(target_pool, candidates, load, max_per_agent):
    plan = []
    used = Counter()
    for prio, st, created, iid, issue, cur_aid, cur_agent in candidates:
        for tgt in target_pool:
            if tgt["id"] == cur_aid: continue
            if used[tgt["id"]] >= max_per_agent: continue
            cap = tgt.get("max_concurrent_tasks") or 1
            cur = load.get(tgt["id"], 0) + used[tgt["id"]]
            if cur >= cap: continue
            plan.append((iid, tgt, cur_agent.get("name") or "?", st == "blocked"))
            used[tgt["id"]] += 1
            break
    return plan


def cli(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def apply(plan, dry_run=False, sleep=0.15):
    ok = fail = 0
    for i, (iid, tgt, src_name, was_blocked) in enumerate(plan, 1):
        short = iid[:8]
        tname = (tgt.get("name") or "?")[:30]
        if dry_run:
            tag = " [unblock]" if was_blocked else ""
            print(f"  [DRY] {i:>3} {short} -> {tname:<30} (was {src_name}){tag}")
            continue
        if was_blocked:
            r = cli(["multica", "issue", "status", iid, "todo"])
            if r.returncode != 0:
                print(f"  [{i:>3}] {short} STATUS->todo FAILED: {r.stderr[:120]}")
                fail += 1; continue
        r = cli(["multica", "issue", "assign", iid, "--to-id", tgt["id"]])
        if r.returncode != 0:
            print(f"  [{i:>3}] {short} ASSIGN->{tname} FAILED: {r.stderr[:120]}")
            fail += 1; continue
        r = cli(["multica", "issue", "rerun", iid])
        if r.returncode != 0:
            print(f"  [{i:>3}] {short} RERUN FAILED: {r.stderr[:120]}")
            fail += 1; continue
        print(f"  [{i:>3}] {short} -> {tname:<30} (was {src_name})")
        ok += 1
        time.sleep(sleep)
    print()
    print(f"  apply summary: ok={ok}  fail={fail}  total={len(plan)}")
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-agents", type=int, default=30)
    ap.add_argument("--max-per-agent", type=int, default=1)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    issues, agents, runtimes = load_state()
    target_pool, candidates, load, agents_by_id = build_pools(
        issues, agents, runtimes, args.target_agents, args.max_per_agent)

    print("=" * 70)
    print(f" target pool: {len(target_pool)} idle-online agents with headroom")
    for t in target_pool[:8]:
        nm = t.get("name") or "?"
        cap = t.get("max_concurrent_tasks") or 0
        cur = load.get(t["id"], 0)
        print(f"   {t['id'][:8]} {nm[:30]:<30} max={cap} cur={cur} free={cap-cur}")
    if len(target_pool) > 8:
        print(f"   ... and {len(target_pool)-8} more")

    print()
    print(f" candidates: {len(candidates)} workable issues (status in todo/blocked/in_progress)")
    prio_n = Counter(c[0] for c in candidates)
    print("   by priority: " + ", ".join(f"p{k}={v}" for k, v in sorted(prio_n.items())))
    status_n = Counter(c[1] for c in candidates)
    print("   by status  : " + ", ".join(f"{k}={v}" for k, v in status_n.most_common()))

    plan = make_plan(target_pool, candidates, load, args.max_per_agent)
    print()
    print(f" PLAN: {len(plan)} moves")
    print("=" * 70)
    if not plan:
        print(" nothing to do")
        return 0
    print()
    apply(plan, dry_run=not args.apply, sleep=args.sleep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
