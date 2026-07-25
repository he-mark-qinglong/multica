"""Pytest suite for upload_artifacts.py.

All tests are offline — they never invoke the real ``multica`` binary. Tests
1, 3, 4 exercise the script's CLI / apply path with fake subprocesses (or
by pointing at the real ``quant-loop/research/swarm/.../gate-ledger-fix``
read-only fixture directory). Test 2 covers the comment-schema self-check
that gates the comment post.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Allow ``from _shared.swarm.upload_artifacts import ...`` when pytest is
# invoked from the repo root. ``parents[2]`` of this file resolves to
# ``quant-loop/`` because the test lives at ``quant-loop/_shared/swarm/``.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _shared.swarm.upload_artifacts import (  # noqa: E402
    _list_top_level_files,
    main,
    valid_comment_first_line,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    """Tiny stand-in for ``subprocess.CompletedProcess`` used by the fake run."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunRecorder:
    """Record each subprocess.run invocation so tests can inspect argv / rc.

    ``per_call_rc`` is an optional list indexed by call number; when provided
    each invocation returns the matching returncode (default 0). The
    ``comment_file_payload`` callable, if set, is invoked with the path
    passed to the comment add and returns the body that will be on disk
    (the fake writes that body so the test can inspect what *would* have
    been posted). It defaults to writing nothing, which still satisfies the
    contract because the script unlinks the temp file in ``finally``.
    """

    def __init__(self, per_call_rc: list[int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.per_call_rc = list(per_call_rc or [])
        self.stdout_payloads: list[str] = []
        self.stderr_payloads: list[str] = []

    def __call__(self, argv, *args, **kwargs):
        self.calls.append(list(argv))
        idx = len(self.calls) - 1
        if idx < len(self.per_call_rc):
            rc = self.per_call_rc[idx]
        else:
            rc = 0
        # When the call targets "issue comment add" with --content-file,
        # ensure the referenced file exists so the script's unlink in
        # ``finally`` is a no-op rather than an error.
        if "comment" in argv and "--content-file" in argv:
            cf_idx = argv.index("--content-file")
            cf_path = Path(argv[cf_idx + 1])
            if not cf_path.exists():
                cf_path.write_text("", encoding="utf-8")
        return _FakeCompletedProcess(returncode=rc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_enumerates_real_dir(capsys: pytest.CaptureFixture[str]) -> None:
    """Dry-run on the real ``gate-ledger-fix`` fixture enumerates >=10 files."""
    run_dir = (
        Path(__file__).resolve().parents[2]
        / "research" / "swarm" / "2026-07-25" / "gate-ledger-fix"
    )
    assert run_dir.is_dir(), f"fixture missing: {run_dir}"

    rc = main([
        str(run_dir),
        "--task-id", "dryrun",
        "--issue-id", "dryrun",
    ])
    out = capsys.readouterr().out

    assert rc == 0, f"dry-run expected exit 0, got {rc}; stdout was:\n{out}"
    plan_lines = [ln for ln in out.splitlines() if ln.startswith("PLAN upload ")]
    assert len(plan_lines) >= 10, (
        f"expected >=10 PLAN upload lines, got {len(plan_lines)}:\n"
        + "\n".join(plan_lines)
    )
    assert "DRY-RUN" in out, f"missing DRY-RUN marker in:\n{out}"


def test_comment_first_line_schema() -> None:
    """``valid_comment_first_line`` accepts the 4 positive shapes and rejects 3 negatives."""
    positives = [
        # canonical +HH form (no minutes)
        "[type=EVIDENCE] 2026-07-25T15:04+08 swarm run x: 10/12 artifacts uploaded to task T",
        # with minutes and colon
        "[type=EVIDENCE] 2026-07-25T15:04+08:00 swarm run x: 10/12 artifacts uploaded to task T",
        # Z (UTC zulu)
        "[type=EVIDENCE] 2026-07-25T15:04Z swarm run x: 10/12 artifacts uploaded to task T",
        # +HHMM with no colon
        "[type=STATUS] 2026-07-25T15:04+0800 status update summary",
    ]
    for p in positives:
        assert valid_comment_first_line(p), f"expected positive match for: {p!r}"

    negatives = [
        # missing [type=...] tag entirely
        "2026-07-25T15:04+08 swarm run x: 10/12 artifacts uploaded to task T",
        # invalid type tag
        "[type=NOTE] 2026-07-25T15:04+08 swarm run x: 10/12 artifacts uploaded to task T",
        # no timezone at all
        "[type=EVIDENCE] 2026-07-25T15:04 swarm run x: 10/12 artifacts uploaded to task T",
    ]
    for n in negatives:
        assert not valid_comment_first_line(n), f"expected negative for: {n!r}"


def test_apply_constructs_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--apply fires exactly N artifact uploads + 1 comment add with proper argv."""
    # Three top-level files (different kinds so we see they all flow through).
    for name in ("alpha.py", "beta.go", "gamma.json"):
        (tmp_path / name).write_text("payload", encoding="utf-8")
    # A .pyc at the top level would have to be filtered out; we leave it
    # out here but verify the helper refuses it in test_apply_failure_continues
    # by relying on the production code's own filter (defensive: add a
    # stray .pyc and confirm it is NOT passed to multica).
    (tmp_path / "noise.pyc").write_text("ignored", encoding="utf-8")

    recorder = _FakeRunRecorder()
    monkeypatch.setattr(subprocess, "run", recorder)

    rc = main([
        str(tmp_path),
        "--task-id", "TASK-XYZ",
        "--issue-id", "ISSUE-XYZ",
        "--apply",
    ])

    assert rc == 0, f"expected clean exit 0, got {rc}"

    # Exactly three artifact adds + one comment add.
    artifact_calls = [c for c in recorder.calls if "artifact" in c and "add" in c]
    comment_calls = [c for c in recorder.calls if "comment" in c and "add" in c]
    assert len(artifact_calls) == 3, (
        f"expected 3 artifact add calls, got {len(artifact_calls)}: {artifact_calls}"
    )
    assert len(comment_calls) == 1, (
        f"expected 1 comment add call, got {len(comment_calls)}: {comment_calls}"
    )

    # First artifact call must contain task-id and the file argv.
    first = artifact_calls[0]
    assert "artifact" in first and "add" in first, first
    assert "TASK-XYZ" in first, first
    # All three files should appear in some artifact call's payload path.
    file_args = {c[4] for c in artifact_calls}
    assert file_args == {
        str(tmp_path / "alpha.py"),
        str(tmp_path / "beta.go"),
        str(tmp_path / "gamma.json"),
    }, file_args
    # Defensive filter check: the .pyc must NOT be uploaded.
    assert not any(str(tmp_path / "noise.pyc") == c[4] for c in artifact_calls), (
        f"top-level .pyc leaked into upload list: {artifact_calls}"
    )

    # The comment call should point at our issue-id.
    comment = comment_calls[0]
    assert "ISSUE-XYZ" in comment, comment
    assert "--content-file" in comment, comment


def test_apply_failure_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-zero artifact upload does not abort the loop; final exit is 1."""
    for name in ("one.py", "two.py", "three.py"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    # First call OK, second call FAILS, third call OK.
    recorder = _FakeRunRecorder(per_call_rc=[0, 1, 0])
    monkeypatch.setattr(subprocess, "run", recorder)

    rc = main([
        str(tmp_path),
        "--task-id", "T",
        "--issue-id", "I",
        "--apply",
    ])

    # We expect exit 1 because at least one failure occurred.
    assert rc == 1, f"expected exit 1, got {rc}"

    artifact_calls = [c for c in recorder.calls if "artifact" in c and "add" in c]
    assert len(artifact_calls) == 3, (
        f"expected all 3 uploads attempted despite failure, got {len(artifact_calls)}"
    )

    # The receipt comment should still be posted, listing 2/3 succeeded and
    # naming the failure (we don't need to read disk here; just confirm the
    # script proceeded past the failure to the comment-post step).
    comment_calls = [c for c in recorder.calls if "comment" in c and "add" in c]
    assert len(comment_calls) == 1, (
        f"expected comment post to occur even on partial failure; "
        f"got {len(comment_calls)} comment calls"
    )