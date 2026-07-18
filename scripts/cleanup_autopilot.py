#!/usr/bin/env python3
"""Cleanup autopilot self-loop tasks that pile up in in_review / todo.

Rule: a task is "invalid" when its title starts with "[<autopilot-prefix>]"
and its status is in_review or todo.  These are tasks that agents cannot
move to done (they are multica-system-internal housekeeping), so they
accumulate.  We cancel them.

Blocked, non-bracket, and terminal statuses are kept untouched.
"""
import argparse
import json
import subprocess
import sys
import time
from collections import Counter

TARGET_STATUSES = ("in_review", "todo", "backlog")

# Sometimes an autopilot task hides without the leading bracket.  We do NOT
# treat those as autopilot by default; they need manual review.  Pass
# --aggressive to extend the rule to "PUMP-/DRAIN-/SYNC-/PULSE/..." too.
EXTRA_AUTOPILOT_KW = (
    "PUMP-", "DRAIN-", "SYNC-", "PULSE", "Agent-Sync", "Agent-Promotion",
    "Blocked-Unblock", "DRAIN-META",
)


def fetch_status(status):
    out = []
    offset = 0
    while True:
        r = subprocess.run(
            ["multica", "issue", "list", "--status", status,
             "--limit", "100", "--offset", str(offset), "--output", "json"],
            capture_output=True, text=True, cwd="/home/smark/multica",
        )
        if r.returncode != 0:
            print("ERR", r.stderr[:120])
            break
        page = json.loads(r.stdout)
        batch = page.get("issues", [])
        if not batch:
            break
        out.extend(batch)
        if not page.get("has_more"):
            break
        offset += 100
    return out


def is_autopilot(issue, aggressive=False):
    title = issue.get("title", "")
    if not title.startswith("["):
        return False
    end = title.find("]")
    if end <= 0 or end > 40:
        return False
    prefix = title[1:end]
    if aggressive:
        return any(prefix.startswith(k) for k in EXTRA_AUTOPILOT_KW) or True
    # default rule: bracket-prefixed title = autopilot by observation
    return True


def collect():
    issues = []
    for st in TARGET_STATUSES:
        items = fetch_status(st)
        for i in items:
            if is_autopilot(i):
                i["_status"] = st
                issues.append(i)
    return issues


def status_cmd(iid, target):
    return ["multica", "issue", "status", iid, target]


def apply(plan, dry_run, sleep):
    ok = fail = 0
    for i, issue in enumerate(plan, 1):
        iid = issue["id"]
        ident = issue.get("identifier", "?")
        title = issue.get("title", "")[:60]
        st = issue["_status"]
        if dry_run:
            print(f"  [DRY] {i:>3} {ident:<10} {st:<11} {title}")
            continue
        r = subprocess.run(status_cmd(iid, "cancelled"),
                           capture_output=True, text=True, cwd="/home/smark/multica")
        if r.returncode != 0:
            print(f"  [{i:>3}] {ident} CANCEL FAIL: {r.stderr[:120]}")
            fail += 1
            continue
        print(f"  [{i:>3}] {ident:<10} {st:<11} -> cancelled   {title}")
        ok += 1
        time.sleep(sleep)
    print()
    print(f"  summary: ok={ok}  fail={fail}  total={len(plan)}")
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    plan = collect()
    print("=" * 70)
    print("autopilot cleanup plan")
    print("=" * 70)
    print(f"total to cancel: {len(plan)}")
    by_st = Counter(i["_status"] for i in plan)
    print(f"by status: {dict(by_st)}")
    pfx_n = Counter()
    for i in plan:
        t = i["title"]
        end = t.find("]")
        pfx_n[t[1:end]] += 1
    print("by prefix (top 20):")
    for k, v in pfx_n.most_common(20):
        print(f"  {k:<35} {v}")
    print()
    print("samples (first 6):")
    for i in plan[:6]:
        print(f"  {i['identifier']:<10} {i['_status']:<11} {i['title'][:70]}")
    print()
    if not plan:
        return 0
    apply(plan, dry_run=not args.apply, sleep=args.sleep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
