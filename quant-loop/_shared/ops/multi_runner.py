"""Multi-strategy parallel runner (H9).

Runs N strategy configurations as N child processes in parallel, each
wrapped in its own :class:`IsolationSpec` resource envelope (reusing
``_shared/ops/isolation.py``), all sharing one signal-bus jsonl spill path
(reusing ``_shared/strategy_kit/signal_bus.py`` semantics) passed down via
the ``SIGNAL_BUS_PATH`` environment variable.

The parent process is pure coordination, no strategy logic:

  * **launch** — one ``Popen`` per strategy, isolated per its spec;
  * **aggregate heartbeats** — each strategy owns a beat file; ``poll()``
    runs the freshness check of ``_shared/ops/heartbeat.py`` over all of
    them and routes stale/dead alerts through one shared ``Alerter``
    (``_shared/ops/alerting.py``), so alert routing policy lives in one
    place regardless of how many strategies are up;
  * **shutdown** — SIGTERM to all, then SIGKILL whoever survives the grace
    period (children of a trading process must not be orphaned).

References:
- Erlang/OTP "one-for-one" supervision trees — siblings are independent;
  one strategy's crash never takes the others down.
- Nygard, "Release It!", ch. 5 — bulkheads between co-located workloads.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from _shared.ops.alerting import Alert, AlertLevel, Alerter
from _shared.ops.heartbeat import HeartbeatStatus, check_heartbeat, heartbeat_alert
from _shared.ops.isolation import IsolationSpec, _preexec

SIGNAL_BUS_ENV = "SIGNAL_BUS_PATH"


@dataclass(frozen=True)
class StrategySpec:
    """One strategy to run as a child process.

    Attributes:
        name: strategy identifier (used in alerts and beat paths).
        argv: command to launch, e.g. (sys.executable, "runner.py", "--cfg", ...).
        heartbeat_path: file the child periodically writes beats to.
        isolation: resource envelope for the child.
        env: extra environment variables (merged over the parent env).
    """

    name: str
    argv: Tuple[str, ...]
    heartbeat_path: Path
    isolation: IsolationSpec = IsolationSpec()
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self.argv:
            raise ValueError("argv must be a non-empty command")


def build_child_env(
    spec: StrategySpec,
    signal_bus_path,
    base: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Environment for one child: parent env + spec.env + bus path. Pure."""
    env = dict(os.environ if base is None else base)
    env.update(spec.env)
    env[SIGNAL_BUS_ENV] = str(signal_bus_path)
    return env


def dead_process_alert(
    name: str, returncode: int, now: Optional[float] = None
) -> Alert:
    """CRITICAL alert for a child that exited on its own."""
    return Alert(
        ts=time.time() if now is None else float(now),
        level=AlertLevel.CRITICAL.value,
        rule="process_exit",
        message=f"strategy {name} exited unexpectedly (rc={returncode})",
        context={"strategy": name, "returncode": returncode},
    )


@dataclass(frozen=True)
class PollResult:
    """One coordination round over all children."""

    statuses: Mapping[str, HeartbeatStatus]   # name -> heartbeat status
    returncodes: Mapping[str, int]            # name -> rc of exited children
    alerts: Tuple[Alert, ...]                 # alerts dispatched this round


class MultiRunner:
    """Launches and supervises a fixed set of strategy child processes."""

    def __init__(
        self,
        specs: Sequence[StrategySpec],
        signal_bus_path,
        alerter: Optional[Alerter] = None,
        heartbeat_timeout_sec: float = 60.0,
    ):
        if not specs:
            raise ValueError("specs must be non-empty")
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate strategy names: {names}")
        self.specs: Tuple[StrategySpec, ...] = tuple(specs)
        self.signal_bus_path = Path(signal_bus_path)
        self.alerter = alerter if alerter is not None else Alerter()
        self.heartbeat_timeout_sec = float(heartbeat_timeout_sec)
        self._procs: Dict[str, subprocess.Popen] = {}
        self._exit_alerted: set[str] = set()

    @property
    def processes(self) -> Mapping[str, subprocess.Popen]:
        return dict(self._procs)

    def start_all(self) -> None:
        """Launch every strategy. Idempotent for already-running children."""
        self.signal_bus_path.parent.mkdir(parents=True, exist_ok=True)
        for spec in self.specs:
            proc = self._procs.get(spec.name)
            if proc is not None and proc.poll() is None:
                continue
            self._procs[spec.name] = subprocess.Popen(
                list(spec.argv),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=build_child_env(spec, self.signal_bus_path),
                preexec_fn=_preexec(spec.isolation),
            )

    def poll(self, now: Optional[float] = None) -> PollResult:
        """One aggregation round: heartbeat freshness + unexpected exits.

        Every stale heartbeat and every freshly-detected exit is routed
        through the shared alerter. An exit alerts at most once per child
        lifetime so a dead strategy does not spam every poll round.
        """
        statuses: Dict[str, HeartbeatStatus] = {}
        returncodes: Dict[str, int] = {}
        alerts = []
        for spec in self.specs:
            status = check_heartbeat(
                spec.heartbeat_path, self.heartbeat_timeout_sec, now
            )
            statuses[spec.name] = status
            alerts.append(heartbeat_alert(status, spec.name, now))

            proc = self._procs.get(spec.name)
            if proc is not None:
                rc = proc.poll()
                if rc is not None:
                    returncodes[spec.name] = rc
                    if spec.name not in self._exit_alerted:
                        self._exit_alerted.add(spec.name)
                        alerts.append(dead_process_alert(spec.name, rc, now))
        fired = self.alerter.evaluate(*alerts)
        return PollResult(statuses=statuses, returncodes=returncodes, alerts=fired)

    def stop_all(self, grace_sec: float = 5.0) -> Mapping[str, int]:
        """SIGTERM all children, then SIGKILL survivors after the grace period."""
        for proc in self._procs.values():
            if proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except OSError:
                    pass
        deadline = time.monotonic() + grace_sec
        for proc in self._procs.values():
            remaining = deadline - time.monotonic()
            try:
                proc.wait(timeout=max(0.0, remaining))
            except subprocess.TimeoutExpired:
                proc.kill()
        return {
            name: proc.wait() for name, proc in self._procs.items()
        }
