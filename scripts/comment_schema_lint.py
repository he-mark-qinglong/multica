#!/usr/bin/env python3
"""Lint multica issue comments for comment-schema compliance.

Agent comments MUST start with:
    [type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>

where TYPE in STATUS, DECISION, EVIDENCE, KILL, ESCALATE, SIGNOFF, NUDGE, NOOP.

Human (member) comments are reported but not flagged as OFFSPEC unless --strict.

Usage:
    python3 scripts/comment_schema_lint.py SMA-36661
    python3 scripts/comment_schema_lint.py SMA-36661 SMA-36660
    python3 scripts/comment_schema_lint.py --all-recent --limit 50
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_TYPES = {
    "STATUS", "DECISION", "EVIDENCE", "KILL", "ESCALATE",
    "SIGNOFF", "NUDGE", "NOOP",
}

# First line must match: [type=TYPE] ISO8601+tz summary
# ISO8601 examples: 2026-07-26T21:45+08  or  2026-07-26T21:45:34+08:00
SCHEMA_RE = re.compile(
    r"^\[type=(" + "|".join(VALID_TYPES) + r")\]\s+"
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z))\s+"
    r"(.+)$"
)


def run_multica(*args: str) -> dict | list:
    cmd = ["multica"] + list(args) + ["--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"multica failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def parse_ts(ts: str) -> datetime | None:
    """Parse ISO8601 timestamp; return None on failure."""
    # Normalize +08 to +08:00
    if re.search(r"[+-]\d{2}$", ts):
        ts = ts[: -3] + ts[-3:] + ":00"
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def lint_comment(comment: dict, strict: bool = False) -> dict | None:
    content = comment.get("content", "")
    first_line = content.splitlines()[0] if content else ""
    author_type = comment.get("author_type", "unknown")

    match = SCHEMA_RE.match(first_line)
    if match:
        c_type, ts_str, summary = match.groups()
        ts = parse_ts(ts_str)
        issue_ts = parse_ts(comment["created_at"])
        ts_ok = bool(ts)
        # Optional: warn if comment timestamp drifts far from created_at
        drift_ok = True
        if ts and issue_ts:
            drift = abs((ts - issue_ts).total_seconds())
            if drift > 3600:
                drift_ok = False
        return {
            "id": comment["id"],
            "author_type": author_type,
            "created_at": comment["created_at"],
            "first_line": first_line,
            "compliant": True,
            "type": c_type,
            "ts_ok": ts_ok,
            "drift_ok": drift_ok,
            "summary": summary,
        }

    # Non-compliant
    if author_type == "agent" or strict:
        return {
            "id": comment["id"],
            "author_type": author_type,
            "created_at": comment["created_at"],
            "first_line": first_line,
            "compliant": False,
            "type": None,
            "ts_ok": False,
            "drift_ok": True,
            "summary": None,
        }
    return None


def lint_issue(issue_id: str, strict: bool = False) -> dict:
    try:
        comments = run_multica("issue", "comment", "list", issue_id)
    except RuntimeError as e:
        return {"issue_id": issue_id, "error": str(e), "offspec": []}

    results = []
    offspec = []
    for c in comments:
        r = lint_comment(c, strict=strict)
        if r:
            results.append(r)
            if not r["compliant"]:
                offspec.append(r)
            elif not r["ts_ok"] or not r["drift_ok"]:
                offspec.append(r)

    return {"issue_id": issue_id, "comments": len(comments), "results": results, "offspec": offspec}


def recent_issues(limit: int = 50) -> list[str]:
    data = run_multica("issue", "list", "--limit", str(limit))
    return [i["identifier"] for i in data.get("issues", [])]


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint multica issue comments for schema compliance")
    parser.add_argument("issues", nargs="*", help="Issue identifiers (e.g. SMA-36661)")
    parser.add_argument("--all-recent", action="store_true", help="Lint recently updated issues")
    parser.add_argument("--limit", type=int, default=50, help="Number of recent issues")
    parser.add_argument("--strict", action="store_true", help="Also flag member comments as OFFSPEC")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    issue_ids = args.issues
    if args.all_recent:
        issue_ids = recent_issues(args.limit)
    if not issue_ids:
        parser.print_help()
        return 1

    reports = []
    for issue_id in issue_ids:
        reports.append(lint_issue(issue_id, strict=args.strict))

    total_offspec = sum(len(r["offspec"]) for r in reports)

    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for rep in reports:
            if "error" in rep:
                print(f"{rep['issue_id']}: ERROR {rep['error']}")
                continue
            print(f"{rep['issue_id']}: {rep['comments']} comments, {len(rep['offspec'])} OFFSPEC")
            for o in rep["offspec"]:
                flag = []
                if not o["compliant"]:
                    flag.append("missing/invalid type tag")
                if not o["ts_ok"]:
                    flag.append("bad timestamp")
                if not o["drift_ok"]:
                    flag.append("timestamp drift >1h")
                print(f"  - {o['created_at']} {o['author_type']}: {o['first_line'][:70]}")
                print(f"    flags: {', '.join(flag)}")
        print(f"\nTotal OFFSPEC: {total_offspec}")

    return 1 if total_offspec > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
