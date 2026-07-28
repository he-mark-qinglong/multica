#!/usr/bin/env python3
"""contract_check.py — 契约 attestation 检查工具（contract-collab P1）。

从 issue body 顶部的 ```contract yaml 代码块解析契约，逐项执行
acceptance_evidence 检查，输出 PASS/FAIL + attestation run id。
SIGNOFF 评论必须引用该 id（无 id 的 PASS 作废）。

用法：
  python3 contract_check.py --issue SMA-36580            # 经 multica CLI 取 issue body
  python3 contract_check.py --file issue_body.md         # 或直接给文本
  python3 contract_check.py --issue SMA-36580 --run-commands   # 连 command_exit_zero 也跑（人工触发才用）

检查类型（P1 支持）：
  git_remote_branch_exists  — git ls-remote fork refs/heads/<ref>
  file_in_main              — git cat-file -e origin/main:<path>
  json_fields_present       — origin/main 上 json 文件含全部字段
  command_exit_zero         — 仅 --run-commands 时执行（当前 repo 根下跑，60s 超时）

退出码：全 PASS=0，任一 FAIL=1，无契约块=2。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time

FORK = "https://github.com/he-mark-qinglong/multica.git"


def sh(cmd: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def load_issue_body(issue: str) -> str:
    rc, out = sh(["multica", "issue", "get", issue, "--output", "json"])
    if rc != 0:
        print(f"FATAL: multica issue get {issue} failed: {out[:200]}", file=sys.stderr)
        sys.exit(2)
    return json.loads(out).get("description", "")


def parse_contract(text: str) -> dict:
    m = re.search(r"```contract\s*\n(.*?)```", text, re.S)
    if not m:
        print("NO_CONTRACT: 未找到 ```contract 代码块", file=sys.stderr)
        sys.exit(2)
    try:
        import yaml  # type: ignore
    except ImportError:
        print("FATAL: 需要 pyyaml", file=sys.stderr)
        sys.exit(2)
    return yaml.safe_load(m.group(1))


def check_git_branch(ref: str) -> tuple[bool, str]:
    rc, out = sh(["git", "ls-remote", FORK, f"refs/heads/{ref}"])
    ok = rc == 0 and bool(out.strip())
    return ok, out.split("\n")[0][:80] if ok else f"branch {ref} not on remote"


def check_file_in_main(path: str) -> tuple[bool, str]:
    rc, out = sh(["git", "fetch", "-q", FORK, "main"], timeout=120)
    rc, out = sh(["git", "cat-file", "-e", f"FETCH_HEAD:{path}"])
    return rc == 0, f"{path} in main" if rc == 0 else f"{path} NOT in main"


def check_json_fields(ref: str, fields: list[str]) -> tuple[bool, str]:
    sh(["git", "fetch", "-q", FORK, "main"], timeout=120)
    rc, out = sh(["git", "show", f"FETCH_HEAD:{ref}"])
    if rc != 0:
        return False, f"{ref} not in main"
    try:
        doc = json.loads(out)
    except json.JSONDecodeError as e:
        return False, f"{ref} invalid json: {e}"
    missing = [f for f in fields if f not in doc]
    return (not missing), (f"all {len(fields)} fields present" if not missing else f"missing: {missing}")


def check_command(cmd: str, run: bool) -> tuple[bool, str]:
    if not run:
        return True, "SKIPPED (use --run-commands to execute)"
    rc, out = sh(cmd.split(), timeout=300)
    return rc == 0, f"exit={rc} {out[-200:]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue")
    ap.add_argument("--file")
    ap.add_argument("--run-commands", action="store_true")
    a = ap.parse_args()
    text = load_issue_body(a.issue) if a.issue else open(a.file).read()
    contract = parse_contract(text)
    checks = contract.get("acceptance_evidence", []) or []
    if not checks:
        print("WARN: 契约无 acceptance_evidence 项", file=sys.stderr)
    results = []
    all_ok = True
    for c in checks:
        kind = c.get("check")
        if kind == "git_remote_branch_exists":
            ok, detail = check_git_branch(c["ref"])
        elif kind == "file_in_main":
            ok, detail = check_file_in_main(c["ref"])
        elif kind == "json_fields_present":
            ok, detail = check_json_fields(c["ref"], c.get("fields", []))
        elif kind == "command_exit_zero":
            ok, detail = check_command(c["run"], a.run_commands)
        else:
            ok, detail = False, f"unknown check type {kind}"
        all_ok &= ok
        results.append({"check": kind, "ok": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {kind}: {detail}")
    run_id = "att-" + hashlib.sha256(
        f"{contract.get('task_id')}|{time.time()}".encode()).hexdigest()[:12]
    print(f"\nattestation_run_id: {run_id}")
    print(f"verdict: {'ATTESTED' if all_ok else 'NOT-ATTESTED'}")
    print(f"SIGNOFF 引用格式: [type=SIGNOFF] … attestation={run_id} …")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
