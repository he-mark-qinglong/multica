"""Per-strategy process resource isolation (H10).

Each strategy runs as a child process wrapped by :func:`run_isolated`,
which enforces an :class:`IsolationSpec`:

  * CPU affinity — ``os.sched_setaffinity`` when the platform supports it
    (Linux; silently skipped on macOS where the syscall does not exist).
  * Memory ceiling — ``resource.setrlimit(RLIMIT_AS)`` inside the child
    where the kernel honors it (Linux), plus a parent-side watchdog that
    polls the child's RSS and SIGKILLs it past the cap. The watchdog is
    the enforcement path on macOS, whose kernel rejects RLIMIT_AS
    lowering with EINVAL; on Linux the rlimit normally fires first and
    the watchdog is a backstop against non-Python allocators.

Exit classification is the point of the wrapper: a child killed for
exceeding its memory ceiling is reported as ``RESOURCE_LIMIT`` (an
ops/capacity event) and never as ``CRASH`` (a strategy bug). Detection,
in order: the watchdog flag, SIGKILL with a memory cap configured,
``MemoryError`` on the child's stderr.

``restart_policy`` (``never`` / ``on_failure`` / ``always``, bounded by
``max_restarts``) mirrors the supervisor semantics in
``_shared/ops/supervisor.py`` but scoped to a single wrapped run.

References:
  - Google SRE Book, ch. 22 "Addressing Cascading Failures" (resource
    isolation between co-located jobs).
  - Bach (1986), "The Design of the UNIX Operating System", ch. 7
    (setrlimit as per-process resource governance).
"""
from __future__ import annotations

import os
import resource
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence, Tuple

RESTART_POLICIES = ("never", "on_failure", "always")


class ExitKind(str, Enum):
    """How a wrapped child terminated."""

    OK = "ok"                        # exit code 0
    CRASH = "crash"                  # nonzero exit / signal, not resource-related
    RESOURCE_LIMIT = "resource_limit"  # killed for exceeding the memory ceiling


@dataclass(frozen=True)
class IsolationSpec:
    """Resource envelope for one strategy process.

    ``cpu_cores`` pins the child to those logical cores when the platform
    supports affinity; ``mem_mb`` caps address space / RSS; ``None``
    disables a limit.
    """

    cpu_cores: Tuple[int, ...] | None = None
    mem_mb: float | None = None
    restart_policy: str = "never"      # never | on_failure | always
    max_restarts: int = 3
    mem_poll_interval_s: float = 0.05

    def __post_init__(self) -> None:
        if self.restart_policy not in RESTART_POLICIES:
            raise ValueError(
                f"restart_policy must be one of {RESTART_POLICIES}, "
                f"got {self.restart_policy!r}"
            )
        if self.cpu_cores is not None:
            if not self.cpu_cores or any(c < 0 for c in self.cpu_cores):
                raise ValueError(f"cpu_cores must be non-negative ints: {self.cpu_cores}")
        if self.mem_mb is not None and self.mem_mb <= 0:
            raise ValueError(f"mem_mb must be positive, got {self.mem_mb}")
        if self.max_restarts < 0:
            raise ValueError("max_restarts must be >= 0")


@dataclass(frozen=True)
class RunResult:
    """Outcome of one :func:`run_isolated` call (including restarts)."""

    argv: Tuple[str, ...]
    exit_kind: str                     # ExitKind value of the FINAL attempt
    returncode: int
    stderr_tail: str                   # last ~500 chars of final stderr
    duration_s: float
    restarts: int                      # number of restart attempts used
    killed_by_watchdog: bool


def _preexec(spec: IsolationSpec):  # pragma: no cover - runs in child
    """Build the child-side setup: affinity first, then the rlimit."""

    def setup() -> None:
        if spec.cpu_cores is not None and hasattr(os, "sched_setaffinity"):
            try:
                os.sched_setaffinity(0, set(spec.cpu_cores))
            except OSError:
                pass  # invalid cores on this host — affinity is best-effort
        if spec.mem_mb is not None:
            limit = int(spec.mem_mb * 1024 * 1024)
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ValueError, OSError):
                pass  # macOS rejects RLIMIT_AS lowering; watchdog enforces

    return setup


def _rss_mb(pid: int) -> float | None:
    """Resident set size of ``pid`` in MB, via ps(1) — portable macOS/Linux."""
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        return int(out.stdout.strip()) / 1024.0
    except (ValueError, subprocess.SubprocessError):
        return None


class _Watchdog:
    """Polls child RSS; SIGKILLs the child past the memory ceiling."""

    def __init__(self, proc: subprocess.Popen, spec: IsolationSpec):
        self.killed = False
        self._stop = threading.Event()
        self._proc = proc
        self._spec = spec
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        assert self._spec.mem_mb is not None
        while not self._stop.is_set():
            if self._proc.poll() is not None:
                return
            rss = _rss_mb(self._proc.pid)
            if rss is not None and rss > self._spec.mem_mb:
                self.killed = True
                self._proc.kill()
                return
            self._stop.wait(self._spec.mem_poll_interval_s)


def _classify(
    returncode: int, stderr: str, killed_by_watchdog: bool, spec: IsolationSpec
) -> ExitKind:
    """Decide OK vs CRASH vs RESOURCE_LIMIT. Pure."""
    if returncode == 0:
        return ExitKind.OK
    if killed_by_watchdog:
        return ExitKind.RESOURCE_LIMIT
    if spec.mem_mb is not None and returncode == -signal.SIGKILL:
        # rlimit-induced SIGKILL (Linux) or external OOM killer.
        return ExitKind.RESOURCE_LIMIT
    if spec.mem_mb is not None and "MemoryError" in stderr:
        # Python child hit RLIMIT_AS: malloc failed, traceback on stderr.
        return ExitKind.RESOURCE_LIMIT
    return ExitKind.CRASH


def _run_once(argv: Sequence[str], spec: IsolationSpec) -> RunResult:
    start = time.monotonic()
    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=_preexec(spec),
    )
    watchdog = _Watchdog(proc, spec) if spec.mem_mb is not None else None
    if watchdog is not None:
        watchdog.start()
    _, stderr = proc.communicate()
    if watchdog is not None:
        watchdog.stop()
    killed = watchdog.killed if watchdog is not None else False
    kind = _classify(proc.returncode, stderr or "", killed, spec)
    return RunResult(
        argv=tuple(argv),
        exit_kind=kind.value,
        returncode=proc.returncode,
        stderr_tail=(stderr or "")[-500:],
        duration_s=time.monotonic() - start,
        restarts=0,
        killed_by_watchdog=killed,
    )


def run_isolated(argv: Sequence[str], spec: IsolationSpec) -> RunResult:
    """Run ``argv`` as a child process under ``spec``.

    Applies the restart policy: ``on_failure`` retries while the exit
    kind is not OK; ``always`` retries unconditionally. Retries are
    bounded by ``spec.max_restarts``. Returns the final attempt's result
    with the ``restarts`` counter filled in.
    """
    if not argv:
        raise ValueError("argv must be a non-empty command")

    attempts = 0
    while True:
        result = _run_once(argv, spec)
        should_restart = (
            spec.restart_policy == "always"
            or (spec.restart_policy == "on_failure"
                and result.exit_kind != ExitKind.OK.value)
        )
        if not should_restart or attempts >= spec.max_restarts:
            return RunResult(
                argv=result.argv,
                exit_kind=result.exit_kind,
                returncode=result.returncode,
                stderr_tail=result.stderr_tail,
                duration_s=result.duration_s,
                restarts=attempts,
                killed_by_watchdog=result.killed_by_watchdog,
            )
        attempts += 1
