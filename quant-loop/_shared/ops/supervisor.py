"""Process supervisor: crash-restart, version rollback, graceful drain (H16, H17, H4).

Wraps a long-running command (typically the paper runner) and provides:

- **Crash auto-restart** (H16) — exponential backoff, at most ``max_restarts``
  attempts; the last ``keep_logs`` per-launch log files are retained.
- **Version ledger + rollback** (H17) — every launch records the code version
  (git hash + directory) in a JSONL ledger and a pid file; ``rollback()``
  returns the previous version's info so an operator can pin the runner back.
- **Graceful drain** (H4) — ``drain(timeout)`` sends SIGTERM; the wrapped
  runner is expected to stop opening new positions and unwind existing ones.
  If it has not exited within the timeout, it is force-killed (SIGKILL).

References:
- Erlang/OTP supervisor behaviour — "let it crash" with bounded restart
  intensity (max_restarts within a window) instead of infinite respawn.
- Nygard, "Release It!", ch. 5 — circuit breaker + timeouts around restarts.
- Linux SIGTERM convention: graceful shutdown means "finish in-flight work,
  take no new work".
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple


# --- Restart policy ---------------------------------------------------------
@dataclass(frozen=True)
class RestartPolicy:
    """Tunables for crash-restart behaviour."""

    max_restarts: int = 5
    base_delay_sec: float = 1.0
    backoff_factor: float = 2.0
    max_delay_sec: float = 60.0
    keep_logs: int = 10
    clean_exit_codes: Tuple[int, ...] = (0,)


def backoff_delays(policy: RestartPolicy = RestartPolicy()) -> Tuple[float, ...]:
    """Pure: the delay before each restart attempt, capped at max_delay_sec."""
    delays = []
    for attempt in range(policy.max_restarts):
        delay = policy.base_delay_sec * (policy.backoff_factor ** attempt)
        delays.append(min(delay, policy.max_delay_sec))
    return tuple(delays)


# --- Launch bookkeeping -----------------------------------------------------
@dataclass(frozen=True)
class LaunchRecord:
    """One supervised child process launch."""

    attempt: int              # 0 = initial launch, 1..N = restarts
    pid: int
    started_ts: float
    exit_code: Optional[int]
    log_path: str
    version: Mapping[str, str] = field(default_factory=dict)


def get_git_hash(repo_dir) -> str:
    """Best-effort short git hash; 'unknown' outside a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# --- Version ledger (H17) ---------------------------------------------------
class VersionLedger:
    """JSONL ledger of launched versions + pid file; supports rollback()."""

    def __init__(self, ledger_path, pid_path):
        self.ledger_path = Path(ledger_path)
        self.pid_path = Path(pid_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)

    def record_launch(
        self,
        pid: int,
        version_dir: str,
        git_hash: str = "",
        ts: Optional[float] = None,
    ) -> Mapping[str, object]:
        entry = {
            "ts": time.time() if ts is None else float(ts),
            "pid": int(pid),
            "version_dir": str(version_dir),
            "git_hash": git_hash,
        }
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        self.pid_path.write_text(f"{entry['pid']}\n")
        return entry

    def history(self) -> Tuple[Mapping[str, object], ...]:
        if not self.ledger_path.exists():
            return ()
        entries = []
        for line in self.ledger_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return tuple(entries)

    def current_pid(self) -> Optional[int]:
        try:
            return int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def rollback(self) -> Optional[Mapping[str, object]]:
        """Return the previous version's info (the one before the latest).

        An operator uses the returned ``version_dir`` / ``git_hash`` to relaunch
        the runner pinned to the prior code version. None if there is no
        previous version to roll back to.
        """
        history = self.history()
        if len(history) < 2:
            return None
        return history[-2]


# --- Supervisor -------------------------------------------------------------
class Supervisor:
    """Runs ``cmd`` as a child process with restart + drain semantics."""

    def __init__(
        self,
        cmd: Sequence[str],
        workdir,
        log_dir,
        policy: RestartPolicy = RestartPolicy(),
        ledger: Optional[VersionLedger] = None,
        repo_dir=None,
    ):
        self.cmd = tuple(str(c) for c in cmd)
        self.workdir = Path(workdir)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.ledger = ledger
        self.repo_dir = Path(repo_dir) if repo_dir is not None else self.workdir
        self.launches: list[LaunchRecord] = []
        self._proc: Optional[subprocess.Popen] = None

    # -- internal ------------------------------------------------------------
    def _version_info(self) -> Mapping[str, str]:
        return {
            "version_dir": str(self.workdir),
            "git_hash": get_git_hash(self.repo_dir),
        }

    def _log_path(self, attempt: int) -> Path:
        return self.log_dir / f"launch_{attempt:04d}.log"

    def _prune_logs(self) -> None:
        logs = sorted(self.log_dir.glob("launch_*.log"))
        for old in logs[: max(0, len(logs) - self.policy.keep_logs)]:
            old.unlink(missing_ok=True)

    def _spawn(self, attempt: int) -> LaunchRecord:
        log_path = self._log_path(attempt)
        log_fh = log_path.open("ab")
        proc = subprocess.Popen(
            list(self.cmd),
            cwd=str(self.workdir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group -> signals stay scoped
        )
        log_fh.close()  # child keeps its own fd
        self._proc = proc
        version = self._version_info()
        if self.ledger is not None:
            self.ledger.record_launch(
                pid=proc.pid,
                version_dir=version["version_dir"],
                git_hash=version["git_hash"],
            )
        return LaunchRecord(
            attempt=attempt,
            pid=proc.pid,
            started_ts=time.time(),
            exit_code=None,
            log_path=str(log_path),
            version=version,
        )

    # -- public API -----------------------------------------------------------
    def supervise(self) -> int:
        """Run the child, restarting on crash with exponential backoff.

        Returns the final exit code. A clean exit (code in
        ``policy.clean_exit_codes``) stops the loop; crashes are retried at
        most ``policy.max_restarts`` times. Stops early and returns the code
        if ``drain()`` was requested.
        """
        delays = backoff_delays(self.policy)
        attempt = 0
        self._drain_requested = False
        while True:
            record = self._spawn(attempt)
            self._prune_logs()
            exit_code = self._proc.wait()
            record = LaunchRecord(
                attempt=record.attempt,
                pid=record.pid,
                started_ts=record.started_ts,
                exit_code=exit_code,
                log_path=record.log_path,
                version=record.version,
            )
            self.launches.append(record)
            self._proc = None
            if self._drain_requested or exit_code in self.policy.clean_exit_codes:
                return exit_code
            if attempt >= self.policy.max_restarts:
                return exit_code
            time.sleep(delays[attempt])
            attempt += 1

    def drain(self, timeout_sec: float = 30.0) -> bool:
        """Graceful stop (H4): SIGTERM, wait up to timeout, then SIGKILL.

        The wrapped runner must treat SIGTERM as "stop opening new positions
        and close existing ones". Returns True if the child exited within the
        timeout (clean drain), False if it had to be force-killed.
        """
        self._drain_requested = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return True
        proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.01)
        proc.kill()
        proc.wait()
        return False
