#!/usr/bin/env python3
"""
9 → 5 (+1) multica project restructure executor.

PLAN: ~/multica/quant-loop/research/project_restructure_plan.md  (16067 bytes)
WORKSPACE: f9a9d34e-b809-4564-b0c0-b781a70a3f25 on .105

USAGE
  ./project_restructure_execute.py             # dry-run by default
  ./project_restructure_execute.py --dry-run   # explicit dry-run
  ./project_restructure_execute.py --apply     # actually mutate
  ./project_restructure_execute.py --rollback  # restore from snapshot TSV

SAFETY
  --dry-run is the DEFAULT. You must pass --apply to mutate.
  Step 1 always writes a snapshot (issues + autopilots) BEFORE any mutation.
  These TSV files are the rollback ledger.

EXIT CODES
  0  success
  1  pre-flight failure (snapshot write failed, plan file missing)
  2  mutation step failed (fail-fast — re-read snapshot before retrying)
  3  rollback failure (some rows could not be restored)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ── Constants (verified against multica CLI on .105) ──────────────────────────

WORKSPACE_ID = "f9a9d34e-b809-4564-b0c0-b781a70a3f25"
RESEARCH_DIR = Path.home() / "multica" / "quant-loop" / "research"
SNAPSHOT_ISSUES = RESEARCH_DIR / "restructure_snapshot.tsv"
SNAPSHOT_AUTOPILOTS = RESEARCH_DIR / "restructure_autopilot_snapshot.tsv"
FINAL_STATE = RESEARCH_DIR / "restructure_final_state.tsv"
PLAN_FILE = RESEARCH_DIR / "project_restructure_plan.md"

# All 9 current projects (titles from plan §2.1).
CURRENT_PROJECTS: dict[str, str] = {
    "3bfac0d2-f958-4cba-91d6-c3c7902b1b6c": "trading-engine",
    "99e0c1c1-350c-44de-9597-4bcea7517d9e": "data-pipeline",
    "915627d9-d97a-440d-9e50-47c46dcb0eb6": "quant-loop-strategies",
    "ba6b1beb-575f-4959-b19a-c5ff2c72cb0e": "M-Infrastructure",
    "70760f01-5240-4f36-ad07-18d32ca4ca5c": "Multica HTTPS",
    "399861eb-fd1d-4a4a-a165-aacacad3ab14": "Strategy Display Engine",
    "d1f4d321-98ed-459d-b3d4-ceacbde591ab": "VPVR Campaign",
    "17412adf-f19f-4817-8198-a1b08ef256f4": "multica_feature_test",
    "c77cd86b-0687-4b1e-8b4e-83124aceb61c": "trading",
}

# 6 target projects (plan §1). "meta" is optional per smark (OPEN Q #1) but
# included as the recommended default. Drop it from this list to fall back to
# the 5-stream model with ROOT GOAL folded into strategy-validation.
TARGET_PROJECTS: list[dict] = [
    {"key": "infra",
     "title": "infra",
     "icon": "🛠️",
     "description": "Backtester, validators, tunnel, daemon, display frontend, data pipeline"},
    {"key": "strategy-discovery",
     "title": "strategy-discovery",
     "icon": "🔬",
     "description": "Hypotheses, research, new strategy directions, campaign specs"},
    {"key": "strategy-validation",
     "title": "strategy-validation",
     "icon": "✅",
     "description": "Framework CV, walk-forward, DSR, bootstrap CI, paper-trade gating"},
    {"key": "live-trading",
     "title": "live-trading",
     "icon": "📈",
     "description": "Paper trading, execution, monitoring"},
    {"key": "ops",
     "title": "ops",
     "icon": "⚙️",
     "description": "Cron, monitoring, health patrol, autopilot, queue balance"},
    {"key": "meta",
     "title": "meta",
     "icon": "🎯",
     "description": "ROOT GOAL SMA-30054, SPEC v1, conventions, cross-cutting governance"},
]

# Active issue migration (plan §3.2). target_key must match TARGET_PROJECTS[i]["key"].
ACTIVE_ISSUE_MIGRATION: list[tuple[str, str]] = [
    ("SMA-35006", "strategy-discovery"),
    ("SMA-35004", "strategy-discovery"),
    ("SMA-35003", "strategy-discovery"),
    ("SMA-35001", "strategy-discovery"),
    ("SMA-35000", "strategy-discovery"),
    ("SMA-34999", "strategy-discovery"),
    ("SMA-34998", "strategy-discovery"),
    ("SMA-34966", "strategy-validation"),
    ("SMA-30199", "meta"),
    ("SMA-30054", "meta"),
    ("SMA-35069", "strategy-validation"),
    ("SMA-35062", "strategy-validation"),
    ("SMA-32071", "ops"),
    ("SMA-2952",  "infra"),
    ("SMA-2951",  "infra"),
    ("SMA-34901", "strategy-discovery"),
]

# 3 hard-blocker autopilots bound to Strategy Display Engine (399861eb).
# Substring match on title. Empty repoint target = workspace-scope (--project "").
BLOCKER_AUTOPILOT_SUBSTR = [
    "Evidence Review Gate",
    "Cross-Project Agent Intel Sync",
    "Workspace Queue Balancer",
]
BLOCKER_REPOINT_TARGET = ""  # workspace-scope (empty string clears per CLI doc)

# Dispatch autopilots to pause during steps 3-7 (substring match on title).
DISPATCH_AUTOPILOT_SUBSTR = [
    "Multica Dispatch",
    "Idle Agent Dispatcher",
]

# Projects to archive after migration (plan §6.3 step 4 — all 9 to retire).
ARCHIVE_PROJECTS: list[str] = list(CURRENT_PROJECTS.keys())


# ── CLI helpers ──────────────────────────────────────────────────────────────

def run(args: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["multica", *args], check=check, capture_output=capture, text=True)


def cli_json(args: list[str]) -> object:
    cp = run(args + ["--output", "json"])
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"FAIL: cannot parse JSON from `multica {' '.join(args)}`\n")
        sys.stderr.write(f"  stdout: {cp.stdout[:500]}\n")
        sys.stderr.write(f"  stderr: {cp.stderr[:500]}\n")
        sys.exit(1)


def unwrap(payload, key_candidates: tuple[str, ...]) -> list:
    """multica list endpoints wrap their array under a key. Try common names."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in key_candidates:
            if k in payload and isinstance(payload[k], list):
                return payload[k]
    return []


# ── Snapshot ─────────────────────────────────────────────────────────────────

def cell(v) -> str:
    """Render a TSV cell: None becomes empty string."""
    return "" if v is None else str(v)


def write_tsv(path: Path, header: list[str], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(cell(v) for v in r) + "\n")
    tmp.replace(path)


def all_issues() -> list[dict]:
    """Paginate through every issue in the workspace.

    The multica CLI silently caps --limit at 100 per request regardless of the
    value passed (verified 2026-07-20 on .105: --limit 500 still returns 100).
    Use step=100 and follow has_more until exhausted.
    """
    issues: list[dict] = []
    offset = 0
    step = 100
    while True:
        data = cli_json(["issue", "list", "--limit", str(step), "--offset", str(offset)])
        batch = unwrap(data, ("issues",))
        issues.extend(batch)
        if not (isinstance(data, dict) and data.get("has_more")):
            break
        offset += step
    return issues


def all_autopilots() -> list[dict]:
    return unwrap(cli_json(["autopilot", "list"]), ("autopilots",))


def all_projects() -> list[dict]:
    p = cli_json(["project", "list"])
    if isinstance(p, list):
        return p
    return unwrap(p, ("projects",))


# ── Steps ────────────────────────────────────────────────────────────────────

def step1_snapshot(dry: bool) -> None:
    label = "DRY-RUN pre-snapshot" if dry else "PRE-MUTATION snapshot"
    print(f"[STEP 1] {label}")
    issues = all_issues()
    autos = all_autopilots()
    issue_rows = [(i["identifier"], i.get("project_id"), i.get("status"), i.get("title"))
                  for i in issues]
    auto_rows = [(a["id"], a.get("project_id"), a.get("status"), a.get("title"))
                 for a in autos]
    try:
        write_tsv(SNAPSHOT_ISSUES,
                  ["identifier", "project_id", "status", "title"],
                  issue_rows)
        write_tsv(SNAPSHOT_AUTOPILOTS,
                  ["autopilot_id", "project_id", "status", "title"],
                  auto_rows)
    except OSError as e:
        sys.stderr.write(f"FAIL: snapshot write failed: {e}\n")
        sys.exit(1)
    print(f"  OK: {len(issues)} issues   → {SNAPSHOT_ISSUES}")
    print(f"  OK: {len(autos)} autopilots → {SNAPSHOT_AUTOPILOTS}")


def step2_verify_flag(dry: bool) -> None:
    print("[STEP 2] Verify `multica autopilot update --project \"\"` clears project_id")
    cp = run(["autopilot", "update", "--help"], check=False)
    help_txt = cp.stdout
    has_project = "--project" in help_txt
    has_clear_hint = "empty string to clear" in help_txt or "use empty string" in help_txt.lower()
    print(f"  --project flag present: {has_project}")
    print(f"  'empty string to clear' documented: {has_clear_hint}")
    if not has_project:
        sys.stderr.write("FAIL: --project flag not present in autopilot update help.\n")
        sys.exit(2)
    if not has_clear_hint:
        print("  WARN: 'empty string to clear' hint missing — flag may not clear.")
    print("  OK")


def find_autopilot(substr: str, *, must_have: str | None = None) -> dict:
    for a in all_autopilots():
        if substr.lower() not in a.get("title", "").lower():
            continue
        if must_have and (a.get("project_id") or "") != must_have:
            continue
        return a
    return {}


def step3_pause_dispatch(dry: bool) -> list[str]:
    print("[STEP 3] Pause Multica Dispatch + Idle Agent Dispatcher")
    paused: list[str] = []
    for substr in DISPATCH_AUTOPILOT_SUBSTR:
        ap = find_autopilot(substr)
        if not ap:
            sys.stderr.write(f"FAIL: dispatch autopilot matching '{substr}' not found.\n")
            sys.exit(2)
        if dry:
            print(f"  DRY: would pause  id={ap['id']}  title={ap['title']}  status={ap.get('status')}")
            paused.append(ap["id"])
            continue
        cp = run(["autopilot", "update", ap["id"], "--status", "paused"], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"FAIL: pause {ap['title']}: {cp.stderr.strip()}\n")
            sys.exit(2)
        cur = cli_json(["autopilot", "get", ap["id"]])
        if cur.get("status") != "paused":
            sys.stderr.write(f"FAIL: {ap['title']} did not transition to paused (got {cur.get('status')}).\n")
            sys.exit(2)
        print(f"  OK: paused  id={ap['id']}  title={ap['title']}")
        paused.append(ap["id"])
    return paused


def step4_repoint_blockers(dry: bool) -> list[tuple[str, str]]:
    print(f"[STEP 4] Re-point 3 BLOCKER autopilots to project_id={BLOCKER_REPOINT_TARGET!r} (workspace-scope)")
    repointed: list[tuple[str, str]] = []
    target_pid = "399861eb-fd1d-4a4a-a165-aacacad3ab14"
    for substr in BLOCKER_AUTOPILOT_SUBSTR:
        ap = find_autopilot(substr, must_have=target_pid)
        if not ap:
            sys.stderr.write(
                f"FAIL: blocker autopilot '{substr}' not bound to Strategy Display Engine.\n"
                f"  Snapshot may have drifted; re-take it.\n"
            )
            sys.exit(2)
        old_pid = ap.get("project_id") or ""
        if dry:
            print(f"  DRY: would repoint  id={ap['id']}  {old_pid!r} → ''  title={ap['title']}")
            repointed.append((ap["id"], old_pid))
            continue
        cp = run(["autopilot", "update", ap["id"], "--project", BLOCKER_REPOINT_TARGET], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"FAIL: repoint {ap['title']}: {cp.stderr.strip()}\n")
            sys.exit(2)
        cur = cli_json(["autopilot", "get", ap["id"]])
        if cur.get("project_id"):
            sys.stderr.write(
                f"FAIL: repoint of {ap['title']} did not clear project_id "
                f"(still {cur.get('project_id')!r}).\n"
            )
            sys.exit(2)
        print(f"  OK: repointed  id={ap['id']}  {old_pid!r} → ''  title={ap['title']}")
        repointed.append((ap["id"], old_pid))
    return repointed


def step5_create_projects(dry: bool) -> dict[str, str]:
    print(f"[STEP 5] Create {len(TARGET_PROJECTS)} target projects")
    out: dict[str, str] = {}
    for spec in TARGET_PROJECTS:
        if dry:
            print(f"  DRY: would create  title={spec['title']}  icon={spec['icon']}")
            print(f"        description: {spec['description']}")
            out[spec["key"]] = f"<{spec['key']}-id>"
            continue
        cp = run([
            "project", "create",
            "--title", spec["title"],
            "--description", spec["description"],
            "--icon", spec["icon"],
            "--status", "planned",
        ], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"FAIL: create {spec['title']}: {cp.stderr.strip()}\n")
            sys.exit(2)
        try:
            p = json.loads(cp.stdout)
        except json.JSONDecodeError:
            p = next((x for x in all_projects() if x["title"] == spec["title"]), None)
        if not p or not p.get("id"):
            sys.stderr.write(f"FAIL: create {spec['title']}: no id returned\n")
            sys.stderr.write(f"  stdout: {cp.stdout[:500]}\n")
            sys.exit(2)
        out[spec["key"]] = p["id"]
        print(f"  OK: created  title={spec['title']}  id={p['id']}")
    return out


def step6_migrate_issues(dry: bool, target_ids: dict[str, str]) -> None:
    print(f"[STEP 6] Migrate {len(ACTIVE_ISSUE_MIGRATION)} active issues")
    for ident, target_key in ACTIVE_ISSUE_MIGRATION:
        new_pid = target_ids.get(target_key)
        if not new_pid:
            sys.stderr.write(f"FAIL: no target project id for key={target_key}\n")
            sys.exit(2)
        if dry:
            print(f"  DRY: would move  {ident}  →  {target_key}  ({new_pid})")
            continue
        cp = run(["issue", "update", ident, "--project", new_pid], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"FAIL: {ident}: {cp.stderr.strip()}\n")
            sys.exit(2)
        # verify by re-fetching the issue's project_id
        cur = cli_json(["issue", "get", ident])
        if (cur.get("project_id") or "") != new_pid:
            sys.stderr.write(
                f"FAIL: {ident} did not move to {new_pid} (got {cur.get('project_id')!r})\n"
            )
            sys.exit(2)
        print(f"  OK: {ident}  →  {new_pid}  ({target_key})")


def step7_archive(dry: bool) -> None:
    print(f"[STEP 7] Archive {len(ARCHIVE_PROJECTS)} old projects")
    for pid in ARCHIVE_PROJECTS:
        title = CURRENT_PROJECTS.get(pid, pid[:8])
        if dry:
            print(f"  DRY: would archive  id={pid}  title={title}")
            continue
        cp = run(["project", "update", pid, "--status", "archived"], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"FAIL: archive {title}: {cp.stderr.strip()}\n")
            sys.exit(2)
        cur = cli_json(["project", "list"])
        # find this project's current status from list
        proj = next((x for x in cur if x["id"] == pid), None)
        if not proj or proj.get("status") != "archived":
            sys.stderr.write(f"FAIL: archive {title} did not transition (got {proj.get('status') if proj else None})\n")
            sys.exit(2)
        print(f"  OK: archived  id={pid}  title={title}")


def step8_resume(dry: bool, paused_ids: list[str]) -> None:
    print(f"[STEP 8] Resume {len(paused_ids)} dispatch autopilots")
    for ap_id in paused_ids:
        if dry:
            print(f"  DRY: would resume  id={ap_id}")
            continue
        cp = run(["autopilot", "update", ap_id, "--status", "active"], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"FAIL: resume {ap_id}: {cp.stderr.strip()}\n")
            sys.exit(2)
        cur = cli_json(["autopilot", "get", ap_id])
        if cur.get("status") != "active":
            sys.stderr.write(f"FAIL: {ap_id} did not transition to active (got {cur.get('status')})\n")
            sys.exit(2)
        print(f"  OK: resumed  id={ap_id}")


def step9_final_snapshot(dry: bool) -> None:
    label = "DRY-RUN post-mortem" if dry else "POST-MUTATION snapshot"
    print(f"[STEP 9] {label}")
    issues = all_issues()
    rows = [(i["identifier"], i.get("project_id"), i.get("status")) for i in issues]
    try:
        write_tsv(FINAL_STATE, ["identifier", "project_id", "status"], rows)
    except OSError as e:
        sys.stderr.write(f"FAIL: final snapshot write failed: {e}\n")
        sys.exit(1)
    print(f"  OK: {len(issues)} issues   → {FINAL_STATE}")


# ── Rollback ─────────────────────────────────────────────────────────────────

def rollback() -> None:
    print(f"[ROLLBACK] Restoring from {SNAPSHOT_ISSUES.name} + {SNAPSHOT_AUTOPILOTS.name}")
    if not SNAPSHOT_ISSUES.exists() or not SNAPSHOT_AUTOPILOTS.exists():
        sys.stderr.write(
            f"FAIL: snapshot files missing:\n"
            f"  {SNAPSHOT_ISSUES}  exists={SNAPSHOT_ISSUES.exists()}\n"
            f"  {SNAPSHOT_AUTOPILOTS}  exists={SNAPSHOT_AUTOPILOTS.exists()}\n"
        )
        sys.exit(3)
    failures = 0

    print(f"  Restoring issue project_ids from {SNAPSHOT_ISSUES.name}")
    with open(SNAPSHOT_ISSUES) as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            ident, old_pid = parts[0], parts[1]
            cp = run(["issue", "update", ident, "--project", old_pid], check=False)
            if cp.returncode != 0:
                sys.stderr.write(f"  FAIL: {ident}: {cp.stderr.strip()}\n")
                failures += 1
            else:
                print(f"  OK: {ident} → {old_pid or '(null)'}")

    print(f"  Restoring autopilot project_ids from {SNAPSHOT_AUTOPILOTS.name}")
    with open(SNAPSHOT_AUTOPILOTS) as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            ap_id, old_pid = parts[0], parts[1]
            cp = run(["autopilot", "update", ap_id, "--project", old_pid], check=False)
            if cp.returncode != 0:
                sys.stderr.write(f"  FAIL: autopilot {ap_id}: {cp.stderr.strip()}\n")
                failures += 1
            else:
                print(f"  OK: autopilot {ap_id} → {old_pid or '(null)'}")

    print("  Restoring archived projects → planned")
    for pid in ARCHIVE_PROJECTS:
        cp = run(["project", "update", pid, "--status", "planned"], check=False)
        if cp.returncode != 0:
            sys.stderr.write(f"  FAIL: restore project {pid}: {cp.stderr.strip()}\n")
            failures += 1
        else:
            print(f"  OK: project {pid} → planned")

    if failures:
        sys.stderr.write(f"\nROLLBACK completed with {failures} failures.\n")
        sys.exit(3)
    print("\nROLLBACK complete.")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="9 → 5 (+1) multica project restructure executor.")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="Print every action but make no mutations (DEFAULT).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually execute mutations. Requires explicit opt-in.")
    ap.add_argument("--rollback", action="store_true",
                    help="Restore from restructure_snapshot.tsv and revert project statuses.")
    args = ap.parse_args()

    apply_mode = args.apply
    dry = not apply_mode

    if args.rollback:
        rollback()
        return

    banner = "=" * 72
    print(banner)
    print("MULTICA PROJECT RESTRUCTURE — 9 → 5 (+1)")
    print(f"Mode:        {'APPLY (MUTATION)' if apply_mode else 'DRY-RUN (no mutation)'}")
    print(f"Plan:        {PLAN_FILE}")
    print(f"Workspace:   {WORKSPACE_ID}")
    print(f"Snapshots:   {SNAPSHOT_ISSUES.name}, {SNAPSHOT_AUTOPILOTS.name}")
    print(f"Final state: {FINAL_STATE.name}")
    if apply_mode:
        print()
        print("*** WARNING: this MUTATES projects and autopilots. ***")
        print("*** A snapshot was taken in step 1. To rollback, run: ***")
        print("***    ./project_restructure_execute.py --rollback   ***")
    print(banner)

    if not PLAN_FILE.exists():
        sys.stderr.write(f"FAIL: plan file missing: {PLAN_FILE}\n")
        sys.exit(1)

    # Action counter for easy verification of dry-run output.
    counter = {"DRY": 0, "OK": 0, "FAIL": 0}

    # Step 1 — snapshot
    step1_snapshot(dry)
    counter["OK"] += 2

    # Step 2 — verify flag
    step2_verify_flag(dry)

    # Step 3 — pause dispatch
    paused = step3_pause_dispatch(dry)
    for _ in paused:
        counter["DRY" if dry else "OK"] += 1

    # Step 4 — repoint blockers (HARD GATE: fail-fast)
    repointed = step4_repoint_blockers(dry)
    for _ in repointed:
        counter["DRY" if dry else "OK"] += 1

    # Step 5 — create target projects
    target_ids = step5_create_projects(dry)
    for _ in TARGET_PROJECTS:
        counter["DRY" if dry else "OK"] += 1

    # Step 6 — migrate active issues
    step6_migrate_issues(dry, target_ids)
    for _ in ACTIVE_ISSUE_MIGRATION:
        counter["DRY" if dry else "OK"] += 1

    # Step 7 — archive old
    step7_archive(dry)
    for _ in ARCHIVE_PROJECTS:
        counter["DRY" if dry else "OK"] += 1

    # Step 8 — resume dispatch
    step8_resume(dry, paused)
    for _ in paused:
        counter["DRY" if dry else "OK"] += 1

    # Step 9 — final state snapshot
    step9_final_snapshot(dry)
    counter["OK"] += 1

    print()
    print(banner)
    print(f"DONE.  Mode: {'APPLY' if apply_mode else 'DRY-RUN'}")
    print(f"Action counts: DRY={counter['DRY']}  OK={counter['OK']}  FAIL={counter['FAIL']}")
    if dry:
        print("No mutations performed. Re-run with --apply to execute.")
    print(banner)


if __name__ == "__main__":
    main()
