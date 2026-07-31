#!/usr/bin/env python3
"""regen_campaign_tree — regenerate _CAMPAIGN_MINDMAP/campaign-tree.html.

Reads git log (campaign commits) + multica issue state,
builds a fresh sunburst data tree, replaces the JSON data block in
campaign-tree.html, updates the title with current iter# range.

Idempotent. Pure pipeline: scan → fetch → render → write.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
QUANT_LOOP = "/Users/mark/multica/quant-loop"
HTML_PATH = f"{QUANT_LOOP}/_CAMPAIGN_MINDMAP/campaign-tree.html"
MULTICA_BIN = os.environ.get("MULTICA_BIN", "multica")
GIT_RANGE_FETCH = 300  # last N commits to scan


def now_iso() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def run(cmd, cwd=None, check=True) -> str:
    r = subprocess.run(cmd, cwd=cwd or QUANT_LOOP, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


# ---------- git data ----------

CAMPAIGN_PREFIXES = [
    "iter#", "vpvr ", "vpvr_", "xs_", "bb_", "momentum_",
    "feat(vpvr)", "feat(stat-arb)", "feat: campaign",
]


def is_campaign_commit(subject: str) -> bool:
    s = subject.lower()
    return any(s.startswith(p) or p in s for p in CAMPAIGN_PREFIXES)


def git_log_campaign() -> list:
    fmt = "%H%x1f%an%x1f%at%x1f%s"
    out = run(["git", "log", f"-n{GIT_RANGE_FETCH}", f"--format={fmt}"])
    commits = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        sha, author, ts, subject = line.split("\x1f", 3)
        if is_campaign_commit(subject):
            commits.append({
                "sha": sha[:8],
                "ts": int(ts),
                "subject": subject,
            })
    return commits


# ---------- multica data ----------

def multica_get(issue_id: str):
    """Returns issue dict or None."""
    r = subprocess.run(
        [MULTICA_BIN, "issue", "get", issue_id, "--output", "json"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        # CLI returns flat issue object (not wrapped in {"issue": ...})
        return data.get("issue", data)
    except json.JSONDecodeError:
        return None


SMA_RE = re.compile(r"SMA-(\d{4,6})")


def extract_sma_ids(subject: str) -> list:
    return SMA_RE.findall(subject or "")


# ---------- tree builder ----------

def collect_issues(commits: list) -> dict:
    """For each commit, extract SMA IDs, query multica, return {sma_id: issue_dict}."""
    seen = set()
    out = {}
    for c in commits:
        for sma_num in extract_sma_ids(c["subject"]):
            key = f"SMA-{sma_num}"
            if key in seen:
                continue
            seen.add(key)
            issue = multica_get(key)
            if issue:
                out[key] = issue
    return out


def status_label(issue: dict) -> str:
    """Map multica status → tree status."""
    s = (issue.get("status") or "").lower()
    if s == "done":
        return "archived"
    if s == "cancelled":
        return "killed"
    if s in ("in_progress", "in_review"):
        return "live"
    return "stale"


def build_tree(commits: list, issues: dict) -> dict:
    """Build sunburst data tree. Root = all campaigns. Children = iter groups."""
    root = {
        "name": "multica quant-loop campaigns",
        "status": "info",
        "multica_id": "",
        "description": f"{len(commits)} campaign commits across {len(issues)} multica issues. Generated {now_iso()}.",
        "children": [],
    }
    by_iter = {}
    for c in commits:
        m = re.search(r"iter#\s*\d+\+?", c["subject"])
        key = m.group(0) if m else "other"
        by_iter.setdefault(key, []).append(c)

    def iter_sort_key(k):
        m = re.search(r"\d+", k)
        return int(m.group(0)) if m else 0

    for iter_key in sorted(by_iter.keys(), key=iter_sort_key, reverse=True):
        iter_commits = by_iter[iter_key]
        iter_node = {
            "name": iter_key,
            "status": "info",
            "multica_id": "",
            "description": f"{len(iter_commits)} commits in {iter_key}",
            "children": [],
        }
        for c in iter_commits:
            sma_ids = [f"SMA-{n}" for n in extract_sma_ids(c["subject"])]
            sma_id = sma_ids[0] if sma_ids else ""
            issue = issues.get(sma_id, {})
            node = {
                "name": c["subject"][:80],
                "status": status_label(issue) if issue else "stale",
                "multica_id": sma_id,
                "description": (issue.get("title") or c["subject"])[:200],
                "value": 1,
            }
            iter_node["children"].append(node)
        root["children"].append(iter_node)
    return root


# ---------- html renderer ----------

TITLE_RE = re.compile(r"<title>.*?</title>", re.DOTALL)
# Capture from <h1> up to (not including) <span class="axis-tag"> or </header> so
# we replace all auto-generated meta spans each run instead of accumulating them.
HEADER_RE = re.compile(r"<h1>.*?(?=<span class=\"axis-tag\">|</header>)", re.DOTALL)
DATA_BLOCK_START = "/* =================== Data"


def replace_data_block(html: str, new_content: str) -> str:
    """Replace the data block. Handles both marker-comment and bare const formats.
    Always idempotent: finds const DATA = {...} by brace-counting, optionally
    including a preceding /* === comment line."""
    # Find the const DATA = { ... }; by brace-counting
    pat = re.compile(r"const [Dd][Aa][Tt][Aa]\s*=\s*\{", re.DOTALL)
    m = pat.search(html)
    if not m:
        raise RuntimeError("const DATA block not found")
    data_start = m.start()
    # Brace-count to find end of object
    depth = 0
    i = m.end() - 1  # at the opening {
    data_end = None
    while i < len(html):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                data_end = i + 1
                break
        i += 1
    if data_end is None:
        raise RuntimeError("unterminated DATA object")
    # Include trailing semicolon
    if data_end < len(html) and html[data_end] == ';':
        data_end += 1
    # Include preceding /* === comment line if present
    s = data_start
    marker = html.rfind(DATA_BLOCK_START, 0, s)
    if marker >= 0 and marker > s - 200:  # marker within reasonable range
        s = marker
    return html[:s] + new_content + html[data_end:]


def render_html(tree: dict, total_issues: int, total_commits: int) -> str:
    with open(HTML_PATH, "r") as f:
        html = f.read()

    iter_keys = sorted(
        [c["name"] for c in tree["children"] if c["name"].startswith("iter#")],
        key=lambda s: int(re.search(r"\d+", s).group(0)),
    )
    if iter_keys:
        title_range = f"{iter_keys[0]} → {iter_keys[-1]}"
    else:
        title_range = "no iters found"
    new_title = f"<title>Campaign Tree — {title_range} (auto-generated {now_iso()[:10]})</title>"
    html = TITLE_RE.sub(new_title, html, count=1)

    new_header = (
        f'<h1>Campaign Tree <span class="iter">{title_range}</span></h1>'
        f'  <span class="meta">'
        f'<b>{total_commits}</b> campaign commits · '
        f'<b>{total_issues}</b> multica issues · '
        f'updated {now_iso()}'
        f'</span>  '
    )
    html = HEADER_RE.sub(new_header, html, count=1)

    data_json = json.dumps(tree, indent=2, ensure_ascii=False)
    new_data_block = (
        f"{DATA_BLOCK_START} (auto-generated {now_iso()}) =================== */\n"
        f"const DATA = {data_json};\n"
    )
    html = replace_data_block(html, new_data_block)

    return html


def main() -> int:
    log("start")

    log("git log scan...")
    commits = git_log_campaign()
    log(f"  found {len(commits)} campaign commits (last {GIT_RANGE_FETCH} git log)")

    log("multica issue fetch (serial)...")
    issues = collect_issues(commits)
    total_sma_refs = sum(len(extract_sma_ids(c['subject'])) for c in commits)
    log(f"  fetched {len(issues)}/{total_sma_refs} issues")

    log("tree build...")
    tree = build_tree(commits, issues)
    log(f"  root.children = {len(tree['children'])} iter groups")

    log("html render...")
    new_html = render_html(tree, total_issues=len(issues), total_commits=len(commits))

    with open(HTML_PATH, "w") as f:
        f.write(new_html)
    log(f"wrote {len(new_html)} bytes to {HTML_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
