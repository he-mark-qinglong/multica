"""Tests for ops/isolation.py (H10)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import sys as _sys

import pytest

from _shared.ops.isolation import (
    ExitKind, IsolationSpec, RunResult, run_isolated,
)

PY = _sys.executable


def _run_code(code, spec):
    return run_isolated([PY, "-c", code], spec)


def test_clean_exit_is_ok():
    res = _run_code("print('hello')", IsolationSpec())
    assert res.exit_kind == ExitKind.OK.value
    assert res.returncode == 0
    assert res.restarts == 0


def test_exception_is_crash_not_resource_limit():
    res = _run_code("raise ValueError('bug')", IsolationSpec(mem_mb=512))
    assert res.exit_kind == ExitKind.CRASH.value
    assert res.returncode != 0
    assert "ValueError" in res.stderr_tail


def test_memory_hog_classified_as_resource_limit():
    """A real child process that blows past a 64MB ceiling must be
    reported as RESOURCE_LIMIT, never as CRASH."""
    code = (
        "x = []"
        "\nfor _ in range(500):"
        "\n    x.append(bytearray(1024 * 1024))"  # 1MB chunks → 500MB
    )
    res = _run_code(code, IsolationSpec(mem_mb=64, mem_poll_interval_s=0.01))
    assert res.exit_kind == ExitKind.RESOURCE_LIMIT.value
    assert res.returncode != 0


def test_memory_within_limit_is_ok():
    code = "x = bytearray(4 * 1024 * 1024); print(len(x))"
    res = _run_code(code, IsolationSpec(mem_mb=512))
    assert res.exit_kind == ExitKind.OK.value


def test_sigkill_with_mem_cap_is_resource_limit():
    # External OOM-style kill under a configured cap looks identical.
    code = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    res = _run_code(code, IsolationSpec(mem_mb=512))
    assert res.exit_kind == ExitKind.RESOURCE_LIMIT.value


def test_sigkill_without_mem_cap_is_crash():
    code = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    res = _run_code(code, IsolationSpec())
    assert res.exit_kind == ExitKind.CRASH.value


def test_restart_policy_on_failure_recovers(tmp_path):
    """Child fails until a marker file exists; second attempt succeeds."""
    marker = tmp_path / "attempted"
    code = (
        f"import pathlib, sys"
        f"\nm = pathlib.Path({str(marker)!r})"
        f"\nif m.exists(): sys.exit(0)"
        f"\nm.touch(); sys.exit(1)"
    )
    spec = IsolationSpec(restart_policy="on_failure", max_restarts=3)
    res = _run_code(code, spec)
    assert res.exit_kind == ExitKind.OK.value
    assert res.restarts == 1


def test_restart_policy_never_does_not_retry(tmp_path):
    marker = tmp_path / "attempted"
    code = (
        f"import pathlib, sys"
        f"\npathlib.Path({str(marker)!r}).touch()"
        f"\nsys.exit(1)"
    )
    res = _run_code(code, IsolationSpec(restart_policy="never"))
    assert res.exit_kind == ExitKind.CRASH.value
    assert res.restarts == 0


def test_restart_bounded_by_max_restarts():
    res = _run_code("import sys; sys.exit(1)",
                    IsolationSpec(restart_policy="on_failure", max_restarts=2))
    assert res.exit_kind == ExitKind.CRASH.value
    assert res.restarts == 2


def test_spec_validation():
    with pytest.raises(ValueError):
        IsolationSpec(restart_policy="sometimes")
    with pytest.raises(ValueError):
        IsolationSpec(mem_mb=0)
    with pytest.raises(ValueError):
        IsolationSpec(cpu_cores=(-1,))


def test_run_isolated_rejects_empty_argv():
    with pytest.raises(ValueError):
        run_isolated([], IsolationSpec())
