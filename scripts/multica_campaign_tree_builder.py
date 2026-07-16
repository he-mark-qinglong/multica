#!/usr/bin/env python3
"""multica_campaign_tree_builder — regenerate _CAMPAIGN_MINDMAP/campaign-tree.html.

Reads git log (campaign commits) + multica issue state + backtest results,
builds a fresh sunburst/mindmap data tree, replaces the JSON data block in
campaign-tree.html, updates the title with current iter# range + axis status.

Idempotent. Run on autopilot cron or manually:
  python3 multica_campaign_tree_builder.py            # write HTML
  python3 multica_campaign_tree_builder.py --dry-run  # preview only

Output:
  /home/smark/multica/quant-loop/_CAMPAIGN_MINDMAP/campaign-tree.html
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
QUANT_LOOP = "/home/smark/multica"
HTML_PATH = f"{QUANT_LOOP}/quant-loop/_CAMPAIGN_MINDMAP/campaign-tree.html"
GIT_RANGE_FETCH = 200  # last N commits to scan
DRY_RUN = "--apply" not in sys.argv


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
]


def is_campaign_commit(subject: str) -> bool:
    s = subject.lower()
    return any(s.startswith(p) or p in s for p in CAMPAIGN_PREFIXES)


def git_log_campaign() -> list[dict]:
    fmt = "%H%x1f%an%x1f%at%x1f%s"
    out = run(["git", "log", "--all", f"-n{GIT_RANGE_FETCH}", f"--format={fmt}"])
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

def multica_get(issue_id: str) -> dict | None:
    """Returns issue dict or None. Supports both flat and {issue: {...}} response shapes."""
    r = subprocess.run(
        ["/usr/local/bin/multica", "issue", "get", issue_id, "--output", "json"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    try:
        payload = json.loads(r.stdout)
        if isinstance(payload, dict):
            return payload.get("issue") or payload
        return None
    except json.JSONDecodeError:
        return None


SMA_RE = re.compile(r"SMA-(\d{4,6})")


def extract_sma_ids(subject: str) -> list[str]:
    return SMA_RE.findall(subject or "")


# ---------- tree builder ----------

def collect_issues(commits: list[dict]) -> dict[str, dict]:
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


def build_tree(commits: list[dict], issues: dict[str, dict]) -> dict:
    """Build sunburst data tree. Root = all campaigns. Children = iter groups."""
    root = {
        "name": "multica quant-loop campaigns",
        "status": "info",
        "multica_id": "",
        "description": f"{len(commits)} campaign commits across {len(issues)} multica issues. Generated {now_iso()}.",
        "children": [],
    }
    by_iter: dict[str, list[dict]] = {}
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
H1_RE = re.compile(r"<h1>.*?</h1>", re.DOTALL)
DATA_BLOCK_START = "/* =================== Data"


def replace_data_block(html: str, new_content: str) -> str:
    """Replace ONLY the `const DATA = {...};` declaration inside the data block.

    This preserves any sibling const declarations (e.g. STATUS_COLOR) that live
    between the data block and the next /* === Sunburst marker. Earlier we used
    a regex that matched the next /* === comment as the end of the data block,
    which silently swallowed STATUS_COLOR and broke the rendering.
    """
    s = html.find(DATA_BLOCK_START)
    if s < 0:
        raise RuntimeError(f"start marker not found: {DATA_BLOCK_START!r}")
    # find "const DATA = " after s
    decl_match = re.search(r"const DATA = ", html[s:])
    if not decl_match:
        raise RuntimeError("const DATA = declaration not found")
    decl_start = s + decl_match.start()
    # find matching closing brace + semicolon (allow nested braces)
    i = s + decl_match.end()
    depth = 0
    while i < len(html):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # consume optional whitespace + semicolon
                j = i + 1
                while j < len(html) and html[j] in " \t\n\r":
                    j += 1
                if j < len(html) and html[j] == ";":
                    j += 1
                return html[:decl_start] + new_content + html[j:]
        i += 1
    raise RuntimeError("could not find matching closing brace for DATA")


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

    new_h1 = (
        f'<h1>Campaign Tree <span class="iter">{title_range}</span></h1>'
    )
    html = H1_RE.sub(new_h1, html, count=1)

    # Replace all accumulated generated meta spans after </h1> with a single fresh one.
    # Preserve any manual spans (e.g. axis-tag, shipped summary) that follow.
    META_RUN_RE = re.compile(
        r"(</h1>)(\s*<span class=\"meta\">.*?</span>)+\s*(?=<span class=\"axis-tag\"|</header>)",
        re.DOTALL,
    )
    new_meta = (
        f'  <span class="meta">'
        f'<b>{total_commits}</b> campaign commits · '
        f'<b>{total_issues}</b> multica issues · '
        f'updated {now_iso()}'
        f'</span>'
    )
    html = META_RUN_RE.sub(r"\1" + new_meta + "  ", html, count=1)

    data_json = json.dumps(tree, indent=2, ensure_ascii=False)
    new_content = f"const DATA = {data_json};"
    html = replace_data_block(html, new_content)

    return html


def main() -> int:
    log(f"start (dry_run={DRY_RUN})")

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

    if DRY_RUN:
        log(f"DRY-RUN: would write {len(new_html)} bytes to {HTML_PATH}")
        return 0

    with open(HTML_PATH, "w") as f:
        f.write(new_html)
    log(f"wrote {len(new_html)} bytes to {HTML_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())