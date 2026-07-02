#!/usr/bin/env python3
"""Page every issue in the workspace into /tmp/issues.jsonl.

The multica server silently caps --limit at 100 regardless of the value
you pass, so we have to page with --offset in 100s and dedup by id.
"""
import json, subprocess, sys
from pathlib import Path

OUT = Path("/tmp/issues.jsonl")

def fetch():
    seen = set()
    out = []
    offset = 0
    while True:
        r = subprocess.run(
            ["multica", "issue", "list", "--limit", "100",
             "--offset", str(offset), "--output", "json"],
            capture_output=True, text=True, cwd="/home/smark/multica",
        )
        if r.returncode != 0:
            print("ERR:", r.stderr[:200], file=sys.stderr)
            break
        page = json.loads(r.stdout)
        batch = page.get("issues", [])
        if not batch:
            break
        for i in batch:
            if i["id"] in seen:
                continue
            seen.add(i["id"])
            out.append(i)
        if not page.get("has_more"):
            break
        offset += 100
    return out


def main():
    issues = fetch()
    OUT.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in issues) + "\n")
    print("wrote", len(issues), "issues to", OUT)


if __name__ == "__main__":
    main()
