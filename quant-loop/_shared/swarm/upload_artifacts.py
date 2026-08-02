"""Swarm-run artifact uploader: push collected files to multica + post an EVIDENCE receipt.

CLI::

    upload_artifacts.py <run_dir> --task-id <id> --issue-id <id>
                        [--apply] [--kind <other|metrics|equity|plot|log|dataset>]

Without ``--apply`` the script is a strict dry-run: it prints the planned
``multica artifact add`` and ``multica issue comment add`` invocations but
performs zero IO against multica or the network. This lets the harness prove
end-to-end that a run would upload correctly before granting network access.

Scope notes (mirroring the T13 task card):

* Only **top-level** files under ``run_dir`` are eligible. ``__pycache__`` is
  excluded naturally because it is a directory; ``*.pyc`` would only ever
  live inside ``__pycache__/`` so it is also excluded, and we defensively
  skip any top-level ``*.pyc`` too. Sub-directory product trees are NOT
  supported in this sprint.
* ``manifest.json`` is optional. If present, ``run_id`` and ``parent_issue``
  are pulled from it; otherwise the directory name stands in for ``run_id``
  and a stderr warning is emitted.
* The EVIDENCE comment is validated against the AGENTS.md comment schema
  before it is posted. If validation fails the comment is **not** posted
  and the script exits 1.

Exit codes:

* ``0`` — dry-run plan emitted (no IO), or --apply and all artifact uploads
  succeeded and the receipt comment was posted.
* ``1`` — at least one artifact ``multica artifact add`` returned non-zero
  under --apply, OR the self-checked comment failed schema validation
  (in which case the comment is **not** posted).
* ``2`` — required arguments missing or ``run_dir`` not a directory; the
  argparse default for missing required args is 2.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Comment-schema self-check.
#
# NOTE: an earlier revision optionally delegated to
# <parent-repo>/scripts/comment_schema_lint.lint_comment as the "cross-card
# contract".  That API drifted: it now takes a comment *dict* (not a raw
# string) and its SCHEMA_RE rejects the bare ``+HH`` offset form that the
# AGENTS.md schema (and this module's tests) treat as canonical.  The local
# regex below is therefore the single authority again.
# ---------------------------------------------------------------------------

_RE = re.compile(
    r"^\[type=(STATUS|DECISION|EVIDENCE|KILL|ESCALATE|SIGNOFF|NUDGE|NOOP)\]"
    r" \d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}(:?\d{2})?|Z) .+"
)


def valid_comment_first_line(line: str) -> bool:
    """Return True iff ``line`` matches the AGENTS.md comment-schema first line.

    Wrapped into a free function so tests can target it directly without the
    subprocess harness.
    """
    return bool(_RE.match(line))


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------


def _list_top_level_files(run_dir: Path) -> list[Path]:
    """Return sorted top-level files under ``run_dir``, excluding ``*.pyc``.

    ``Path.iterdir`` is non-recursive by design (see module docstring).
    """
    return sorted(
        p for p in run_dir.iterdir()
        if p.is_file() and not p.name.endswith(".pyc")
    )


def _load_run_id(run_dir: Path) -> tuple[str, str | None]:
    """Return ``(run_id, parent_issue_or_None)`` from manifest.json or dir name."""
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with manifest_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            run_id = str(data.get("run_id") or run_dir.name)
            parent = data.get("parent_issue")
            return run_id, parent
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"WARN: could not parse {manifest_path}: {exc}; "
                "falling back to directory name as run_id",
                file=sys.stderr,
            )
    else:
        print(
            "WARN: no manifest.json under run_dir; using directory name as run_id",
            file=sys.stderr,
        )
    return run_dir.name, None


def _now_iso() -> str:
    """Local-tz ISO 8601 timestamp at minute precision — matches comment schema."""
    return datetime.now().astimezone().isoformat(timespec="minutes")


# ---------------------------------------------------------------------------
# Apply path
# ---------------------------------------------------------------------------


def _upload_one(
    f: Path, task_id: str, run_id: str, kind: str, timeout: int = 120
) -> tuple[int, str, str]:
    """Invoke ``multica artifact add`` for a single file. Returns ``(rc, stdout, stderr)``."""
    meta = json.dumps({"run_id": run_id, "file": f.name})
    argv = [
        "multica", "artifact", "add", task_id, str(f),
        "--kind", kind, "--meta", meta,
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _post_comment(issue_id: str, body: str) -> tuple[int, str, str]:
    """Write ``body`` to a temp file and invoke ``multica issue comment add``."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
    try:
        argv = [
            "multica", "issue", "comment", "add", issue_id,
            "--content-file", str(tmp_path),
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _build_comment(
    *,
    run_id: str,
    run_dir: Path,
    files: list[Path],
    uploaded: int,
    total: int,
    failures: list[tuple[str, str]],
    task_id: str,
    overall: str | None,
) -> str:
    """Compose the EVIDENCE receipt body. First line is schema-validated."""
    timestamp = _now_iso()
    first_line = (
        f"[type=EVIDENCE] {timestamp} swarm run {run_id}: "
        f"{uploaded}/{total} artifacts uploaded to task {task_id}"
    )
    lines: list[str] = [first_line, ""]
    lines.append(f"- run_dir: `{run_dir}`")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- task_id: `{task_id}`")
    lines.append(f"- uploaded: **{uploaded}/{total}**")
    if overall is not None:
        lines.append(f"- acceptance.overall: `{overall}`")
    lines.append("")
    lines.append("### Files")
    for f in files:
        lines.append(f"- `{f.name}`")
    if failures:
        lines.append("")
        lines.append("### Failures")
        for name, err in failures:
            err_one = err.strip().splitlines()[0] if err.strip() else "(no stderr)"
            lines.append(f"- `{name}`: `{err_one}`")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="upload_artifacts.py",
        description=(
            "Dry-run by default. Pass --apply to actually invoke "
            "``multica artifact add`` per file and post the EVIDENCE receipt."
        ),
    )
    parser.add_argument("run_dir", help="Path to the swarm run directory.")
    parser.add_argument(
        "--task-id", required=True,
        help="multica task/issue id that owns the uploaded artifacts.",
    )
    parser.add_argument(
        "--issue-id", required=True,
        help="multica issue id that will receive the EVIDENCE receipt comment.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Execute the uploads and post the comment (default: dry-run only).",
    )
    parser.add_argument(
        "--kind", default="other",
        choices=["other", "metrics", "equity", "plot", "log", "dataset"],
        help="Artifact kind passed to ``multica artifact add`` (default: other).",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        print(
            f"ERROR: run_dir does not exist or is not a directory: {run_dir}",
            file=sys.stderr,
        )
        return 2

    run_id, _parent_issue = _load_run_id(run_dir)
    files = _list_top_level_files(run_dir)
    kind = args.kind
    task_id = args.task_id
    issue_id = args.issue_id

    # Dry-run branch — print the plan and exit 0 unconditionally. The
    # harness relies on the prefix "PLAN upload " and the trailing DRY-RUN
    # line for grep-based acceptance.
    if not args.apply:
        for f in files:
            print(
                f"PLAN upload {f.name} -> "
                f"multica artifact add {task_id} {f} --kind {kind}"
            )
        print(
            f"PLAN comment {issue_id} (EVIDENCE receipt, {len(files)} files)"
        )
        print(
            "DRY-RUN: no changes made. Re-run with --apply to execute."
        )
        return 0

    # Apply branch — actually push.
    failures: list[tuple[str, str]] = []
    uploaded = 0
    for f in files:
        rc, _stdout, stderr = _upload_one(f, task_id, run_id, kind)
        if rc != 0:
            failures.append((f.name, stderr))
        else:
            uploaded += 1

    total = len(files)
    overall: str | None = None
    acceptance_path = run_dir / "acceptance.json"
    if acceptance_path.exists():
        try:
            with acceptance_path.open("r", encoding="utf-8") as fh:
                a = json.load(fh)
            if isinstance(a, dict) and "overall" in a:
                overall = str(a["overall"])
        except (json.JSONDecodeError, OSError):
            # Non-fatal; just leave overall as None.
            overall = None

    body = _build_comment(
        run_id=run_id,
        run_dir=run_dir,
        files=files,
        uploaded=uploaded,
        total=total,
        failures=failures,
        task_id=task_id,
        overall=overall,
    )

    # Self-check first line against comment schema before posting.
    first_line = body.split("\n", 1)[0]
    if not valid_comment_first_line(first_line):
        print(
            "ERROR: constructed EVIDENCE receipt fails schema check; "
            f"refusing to post. First line: {first_line!r}",
            file=sys.stderr,
        )
        return 1

    rc, _stdout, stderr = _post_comment(issue_id, body)
    if rc != 0:
        print(f"ERROR: comment post failed: {stderr}", file=sys.stderr)
        return 1

    if failures:
        # Uploads completed but some failed — surface that distinctly from
        # a clean run while still posting the receipt (the failures are
        # already enumerated in the body).
        print(
            f"WARN: {len(failures)}/{total} artifact uploads failed; "
            "see receipt body for details.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: uploaded {uploaded}/{total} artifacts and posted receipt to {issue_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
