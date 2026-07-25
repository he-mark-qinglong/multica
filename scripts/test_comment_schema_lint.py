#!/usr/bin/env python3
"""Tests for scripts/comment_schema_lint.py.

Covers the AGENTS.md "Comment Schema Convention (mandatory 2026-07-19)":
    [type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>

Each positive fixture matches exactly one of the 8 types and demonstrates a
distinct timezone shape (or a UTC variant). Each negative fixture isolates a
single failure mode so the violation message can be asserted.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# `scripts/` is on sys.path so `comment_schema_lint` imports without packaging.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import comment_schema_lint as csl  # noqa: E402  (import after sys.path tweak)


# ---------------------------------------------------------------------------
# Positive fixtures — one per TYPE, covering TZ variants.
# ---------------------------------------------------------------------------
POSITIVE_FIXTURES = [
    pytest.param(
        "[type=STATUS] 2026-07-19T22:45+08 run 3c4ddf23 started",
        id="STATUS-+08-short",
    ),
    pytest.param(
        "[type=DECISION] 2026-07-19T23:25:30+08:00 chose X over Y because cost",
        id="DECISION-+08:00-with-seconds",
    ),
    pytest.param(
        "[type=EVIDENCE] 2026-07-20T08:00Z CV sharpe -4.86",
        id="EVIDENCE-Z",
    ),
    pytest.param(
        "[type=KILL] 2026-07-19T23:25+0800 vpvr_xs_pairs killed, framework CV sharpe -4.86",
        id="KILL-+0800-no-colon",
    ),
    pytest.param(
        "[type=ESCALATE] 2026-07-19T20:00+08 question: top up token-plan quota?",
        id="ESCALATE-+08",
    ),
    pytest.param(
        "[type=SIGNOFF] 2026-07-25T10:00+08 approving deliverable per gate evidence",
        id="SIGNOFF-+08",
    ),
    pytest.param(
        "[type=NUDGE] 2026-07-25T10:05+08 re-dispatch to strategy-worker-1",
        id="NUDGE-+08",
    ),
    pytest.param(
        "[type=NOOP] 2026-07-25T21:00+08 nothing to do: SPEC pool empty",
        id="NOOP-+08",
    ),
]


@pytest.mark.parametrize("text", POSITIVE_FIXTURES)
def test_lint_positive(text: str) -> None:
    """Every positive fixture must lint clean."""
    violations = csl.lint_comment(text)
    assert violations == [], f"expected clean lint for {text!r}, got {violations}"


@pytest.mark.parametrize("text", POSITIVE_FIXTURES)
def test_lint_positive_with_body(text: str) -> None:
    """A valid schema line + arbitrary body must still lint clean."""
    body = text + "\n\n- bullet 1\n- bullet 2\n\n```code\nfoo\n```\n"
    assert csl.lint_comment(body) == []


# ---------------------------------------------------------------------------
# Negative fixtures — each isolates ONE failure mode.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected_keyword",
    [
        pytest.param(
            "[type=PROGRESS] 2026-07-19T22:45+08 foo",
            "missing or unknown [type=PROGRESS]",
            id="unknown-type",
        ),
        pytest.param(
            "[type=STATUS] 2026-07-19T22:45 foo",
            "timestamp missing timezone offset",
            id="missing-timezone",
        ),
        pytest.param(
            "[type=STATUS] 2026-07-19T22:45+08",
            "empty summary",
            id="empty-summary",
        ),
        pytest.param(
            "hello\n[type=STATUS] 2026-07-19T22:45+08 foo",
            "missing or unknown",
            id="tag-not-on-first-line",
        ),
        pytest.param(
            "[STATUS] 2026-07-19T22:45+08 foo",
            "missing or unknown",
            id="missing-type-equals",
        ),
        pytest.param(
            "",
            "empty input",
            id="empty-input",
        ),
    ],
)
def test_lint_negative(text: str, expected_keyword: str) -> None:
    """Each negative fixture must produce at least one violation carrying the keyword."""
    violations = csl.lint_comment(text)
    assert violations, f"expected violations for {text!r}"
    joined = "\n".join(violations)
    assert expected_keyword in joined, (
        f"expected keyword {expected_keyword!r} in {violations!r}"
    )


# ---------------------------------------------------------------------------
# Case-sensitivity probes — the spec mandates uppercase types.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "[type=status] 2026-07-19T22:45+08 foo",   # lowercase
        "[type=Status] 2026-07-19T22:45+08 foo",   # title case
        "[type=STATUS ] 2026-07-19T22:45+08 foo",  # trailing space inside tag
    ],
    ids=["lowercase", "titlecase", "trailing-space-in-tag"],
)
def test_lint_case_sensitive(text: str) -> None:
    """Type must be uppercase + exact; deviant cases must fail with a 'missing or unknown' message."""
    violations = csl.lint_comment(text)
    assert violations, f"expected violations for {text!r}"
    joined = "\n".join(violations)
    assert "missing or unknown" in joined, (
        f"expected 'missing or unknown' keyword for case violation, got {violations!r}"
    )


# ---------------------------------------------------------------------------
# Cross-card contract pin — T13 comment-janitor imports these names.
# ---------------------------------------------------------------------------
def test_public_api_contract() -> None:
    """Pin the cross-card API: `lint_comment(text) -> list[str]` + `main(argv) -> int`.

    T13 comment-janitor does `from comment_schema_lint import lint_comment`; do not
    rename, do not change signature, do not change return type without coordination.
    """
    assert callable(csl.lint_comment), "lint_comment must be callable"
    assert callable(csl.main), "main must be callable"

    # Smoke-test the contract: returns a list, not None / not a generator.
    result = csl.lint_comment("[type=STATUS] 2026-07-19T22:45+08 ok summary")
    assert isinstance(result, list), f"lint_comment must return list, got {type(result).__name__}"
    assert all(isinstance(v, str) for v in result), "every violation must be a str"


# ---------------------------------------------------------------------------
# CLI tests — exercise the file/stdin pipeline + exit codes.
# ---------------------------------------------------------------------------
def test_main_cli_file_mix(tmp_path: Path) -> None:
    """Mixed bag: one OK file + one FAIL file -> exit code 1 + both lines on stdout."""
    ok = tmp_path / "ok.md"
    fail = tmp_path / "fail.md"
    ok.write_text("[type=STATUS] 2026-07-25T10:00+08 good summary\n", encoding="utf-8")
    fail.write_text("[type=STATUS] 2026-07-25T10:00 no timezone here\n", encoding="utf-8")

    rc = csl.main([str(ok), str(fail)])
    assert rc == 1, f"expected rc=1 (any failure), got {rc}"


def test_main_cli_single_ok(tmp_path: Path) -> None:
    """A single OK file -> exit code 0."""
    ok = tmp_path / "ok.md"
    ok.write_text("[type=NOOP] 2026-07-25T21:00+08 nothing to do\n", encoding="utf-8")

    rc = csl.main([str(ok)])
    assert rc == 0, f"expected rc=0 for clean lint, got {rc}"


def test_main_cli_missing_file(tmp_path: Path) -> None:
    """A path that doesn't exist -> exit code 2 (not 1, not 0)."""
    ghost = tmp_path / "nope.md"
    rc = csl.main([str(ghost)])
    assert rc == 2, f"expected rc=2 for missing file, got {rc}"


# ---------------------------------------------------------------------------
# Stdin / process-level test — uses a fresh interpreter to avoid sys.stdin capture.
# ---------------------------------------------------------------------------
def test_main_stdin_process(tmp_path: Path) -> None:
    """stdin path: pipe a valid comment through a subprocess and expect rc=0 + 'OK <stdin>'."""
    payload = "[type=STATUS] 2026-07-19T22:45+08 ok summary\n"
    script = HERE / "comment_schema_lint.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=10,
    )
    assert proc.returncode == 0, f"expected rc=0, got {proc.returncode}; stderr={proc.stderr!r}"
    assert "OK <stdin>" in proc.stdout, f"expected 'OK <stdin>' in stdout, got {proc.stdout!r}"


def test_main_stdin_process_fails(tmp_path: Path) -> None:
    """stdin path with a bad payload -> rc=1 + FAIL line on stdout + violation on stderr."""
    payload = "[type=BOGUS] 2026-07-19T22:45+08 foo\n"
    script = HERE / "comment_schema_lint.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=10,
    )
    assert proc.returncode == 1, f"expected rc=1, got {proc.returncode}"
    assert "FAIL" in proc.stdout, f"expected FAIL in stdout, got {proc.stdout!r}"
    assert "missing or unknown" in proc.stderr, (
        f"expected 'missing or unknown' on stderr, got {proc.stderr!r}"
    )


def test_main_dash_stdin(tmp_path: Path) -> None:
    """Explicit '-' as the only arg also reads stdin (parity with 'no args')."""
    payload = "[type=KILL] 2026-07-19T23:25+0800 vpvr_xs_pairs killed\n"
    script = HERE / "comment_schema_lint.py"
    proc = subprocess.run(
        [sys.executable, str(script), "-"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=10,
    )
    assert proc.returncode == 0, f"expected rc=0, got {proc.returncode}; stderr={proc.stderr!r}"
    assert "OK <stdin>" in proc.stdout, proc.stdout