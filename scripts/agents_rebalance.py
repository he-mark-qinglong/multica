#!/usr/bin/env python3
"""
agents_rebalance.py — spread open Multica issues across idle online agents
with role-aware routing.

Routing policy (matches the new agent distribution after reclassify):
  - kimi  (132 agents): long thinking, non-code  -> analysis/research/strategy/doc
  - codex (5 agents)  : quick, interruptible      -> fix/hotfix/test/quick
  - claude (31 agents): rigorous, long-running   -> code/review/architecture

Each candidate issue's labels (cat=code, cat=analysis, ...) pick a
preferred provider.  The full pool of idle online agents is searched for
a target on that provider first; if no free slot is available, fall
back to any idle online agent.

The rebalance also enforces a floor of --min-kimi-working kimi agents
working in steady state by prioritising kimi targets while we are below
the floor.
"""
import argparse
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict

OPEN_SET = {"backlog", "todo", "in_progress", "in_review", "blocked"}
WORKABLE = {"todo", "blocked", "in_progress"}

LABEL_PROVIDER = [
    (["analysis", "research", "strategy", "memo", "doc", "plan", "design",
      "调研", "研究", "策略", "设计", "哲学", "分析", "审查"], "kimi"),
    (["fix", "hotfix", "quick", "test", "build", "migrate", "snapshot",
      "render", "chart", "indicator", "playwright", "scrape"], "codex"),
    (["code", "review", "security", "architecture", "implement", "refactor",
      "feature", "bug"], "claude"),
]
DEFAULT_PROVIDER = "claude"


def label_to_provider(issue):
    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]
    text = " ".join(labels) if labels else \
           (issue.get("title", "") + " " + issue.get("identifier", "")).lower()
    for kws, prov in LABEL_PROVIDER:
        for kw in kws:
            if kw in text:
                return prov
    return DEFAULT_PROVIDER


def load_state():
    issues   = [json.loads(l) for l in open("/tmp/issues.jsonl")]
    agents   = json.load(open("/tmp/agents.json"))
    runtimes = {r["id"]: r for r in json.load(open("/tmp/runtimes.json"))}
    return issues, agents, runtimes


def provider_of(runtimes, agent):
    rt = runtimes.get(agent.get("runtime_id", ""), {})
    return rt.get("provider")


def build_pools(issues, agents, runtimes):
    agents_by_id = {a["id"]: a for a in agents}

    load = Counter()
    for i in issues:
        if i["status"] in OPEN_SET and i.get("assignee_type") == "agent":
            load[i["assignee_id"]] += 1

    working_by_provider = Counter()
    for a in agents:
        if a.get("status") == "working":
            working_by_provider[provider_of(runtimes, a)] += 1

    # ALL idle online agents with headroom
    idle_pool = []
    for a in agents:
        rt = runtimes.get(a.get("runtime_id", ""), {})
        if rt.get("status") != "online": continue
        if a.get("status") != "idle": continue
        if a.get("archived_at"): continue
        cap = a.get("max_concurrent_tasks") or 0
        if cap <= load.get(a["id"], 0): continue
        a["_provider"] = provider_of(runtimes, a)
        a["_free"] = cap - load.get(a["id"], 0)
        idle_pool.append(a)

    # candidates
    candidates = []
    for i in issues:
        if i["status"] not in WORKABLE: continue
        aid = i.get("assignee_id")
        if not aid: continue
        ag = agents_by_id.get(aid)
        if not ag: continue
        if not ag.get("runtime_id"): continue
        cur_prov = provider_of(runtimes, ag)
        preferred = label_to_provider(i)
        candidates.append((i["status"], i["created_at"], i["id"], i, aid, ag,
                           cur_prov, preferred))
    # priority: status (todo first, then blocked, then in_progress), then created
    candidates.sort(key=lambda x: (
        {"todo": 0, "blocked": 1, "in_progress": 2}.get(x[0], 3),
        x[1]))

    return idle_pool, candidates, load, working_by_provider, agents_by_id


def make_plan(idle_pool, candidates, load, max_per_agent, max_moves,
              min_kimi_working, current_kimi_working, enable_kimi=False):
    """For each candidate, find a target on the preferred provider first,
    then fall back.  Stop when ``max_moves`` is reached or pool is empty."""
    # group pool by provider; sort each group by free capacity desc
    pool_by_prov = defaultdict(list)
    for a in idle_pool:
        pool_by_prov[a["_provider"]].append(a)
    for prov in pool_by_prov:
        pool_by_prov[prov].sort(key=lambda a: -a["_free"])

    # kimi is also kept first while we're below the floor
    plan = []
    used = Counter()
    kimi_picks = 0

    for st, created, iid, issue, cur_aid, cur_agent, cur_prov, preferred in candidates:
        if len(plan) >= max_moves:
            break
        # Decide provider order: preferred first, then a kimi-fallback if we
        # still need to satisfy the floor, then everything else.
        below_floor = kimi_picks + current_kimi_working < min_kimi_working
        # When kimi is disabled, reroute kimi-preferred issues to claude
        if not enable_kimi and preferred == "kimi":
            preferred = "claude"
        order = [preferred]
        if enable_kimi and below_floor and "kimi" not in order:
            order.append("kimi")
        fallback = ("claude", "codex", "kimi") if enable_kimi else ("claude", "codex")
        for p in fallback:
            if p not in order:
                order.append(p)
        for prov in order:
            for tgt in pool_by_prov.get(prov, []):
                if tgt["id"] == cur_aid: continue
                if used[tgt["id"]] >= max_per_agent: continue
                plan.append((iid, tgt, cur_agent.get("name") or "?",
                             st == "blocked", preferred, prov))
                used[tgt["id"]] += 1
                if prov == "kimi": kimi_picks += 1
                break
            else:
                continue
            break
    return plan


def cli(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def apply(plan, dry_run=False, sleep=0.15):
    ok = fail = 0
    for i, (iid, tgt, src_name, was_blocked, pref, prov) in enumerate(plan, 1):
        short = iid[:8]
        tname = (tgt.get("name") or "?")[:30]
        if dry_run:
            tag = " [unblock]" if was_blocked else ""
            print(f"  [DRY] {i:>3} {short} -> {tname:<30} (want {pref:<6}-> {prov}, was {src_name}){tag}")
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
        print(f"  [{i:>3}] {short} -> {tname:<30} ({pref:<6}-> {prov}, was {src_name})")
        ok += 1
        time.sleep(sleep)
    print()
    print(f"  apply summary: ok={ok}  fail={fail}  total={len(plan)}")
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-moves", type=int, default=40)
    ap.add_argument("--max-per-agent", type=int, default=1)
    ap.add_argument("--min-kimi-working", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--enable-kimi", action="store_true", help="Include kimi targets (default off; kimi runtime shims tasks to instant completion).")
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    issues, agents, runtimes = load_state()
    idle_pool, candidates, load, working_by_prov, agents_by_id = build_pools(
        issues, agents, runtimes)

    by_prov = Counter(a["_provider"] for a in idle_pool)
    print("=" * 70)
    print(f" working-by-provider now: {dict(working_by_prov)}")
    print(f" idle pool: {len(idle_pool)}  (with headroom)")
    for p, n in by_prov.most_common():
        print(f"   {n:>4}  {p}")

    pref_n = Counter(c[7] for c in candidates)
    status_n = Counter(c[0] for c in candidates)
    print()
    print(f" candidates: {len(candidates)} workable issues")
    print("   by preferred provider: " + ", ".join(f"{k}={v}" for k, v in pref_n.most_common()))
    print("   by status            : " + ", ".join(f"{k}={v}" for k, v in status_n.most_common()))

    plan = make_plan(idle_pool, candidates, load, args.max_per_agent, args.max_moves,
                     args.min_kimi_working, working_by_prov.get("kimi", 0),
                     enable_kimi=args.enable_kimi)
    kimi_n = sum(1 for (_, tgt, *_) in plan if tgt["_provider"] == "kimi")
    codex_n = sum(1 for (_, tgt, *_) in plan if tgt["_provider"] == "codex")
    claude_n = sum(1 for (_, tgt, *_) in plan if tgt["_provider"] == "claude")
    print()
    print(f" PLAN: {len(plan)} moves  "
          f"(kimi +{kimi_n}, codex +{codex_n}, claude +{claude_n})")
    print(f" kimi working will go: {working_by_prov.get('kimi',0)} -> "
          f"~{working_by_prov.get('kimi',0) + kimi_n}")
    print("=" * 70)
    if not plan:
        print(" nothing to do")
        return 0
    print()
    apply(plan, dry_run=not args.apply, sleep=args.sleep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
