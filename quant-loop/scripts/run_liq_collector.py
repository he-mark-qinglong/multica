#!/usr/bin/env python3
"""Resident liquidation collector — supervisor + heartbeat (round-4 LIVE).

Keeps ``scripts/collect_liquidations.py`` alive permanently by wrapping it in
``_shared.ops.supervisor.Supervisor`` (crash auto-restart with exponential
backoff, launch ledger + pid file), and writes a heartbeat beat file every
``--beat-interval`` seconds via ``_shared.ops.heartbeat.write_beat`` so a
watcher (``check_heartbeat`` / ``HeartbeatWatcher``) can detect a wedged or
dead collector — the H14/H15 dead-man's switch.

Difference from ``scripts/collect_liquidations_supervised.py`` (F6): that
entry point is supervise-only; this one adds the heartbeat loop and a
SIGTERM/SIGINT → graceful-drain path.

Layout under ``workdir/liq_collector_logs/`` (default --log-dir):
    launch_XXXX.log        per-launch child stdout/stderr (supervisor)
    version_ledger.jsonl   launch ledger (pid, version_dir, git_hash)
    collector.pid          current child pid
    collector_beat.json    heartbeat beat ({"ts", "state", "child_pid", ...})

Usage:
    python3 scripts/run_liq_collector.py [--proxy http://127.0.0.1:7890] \
        [--beat-interval 30] [--log-dir workdir/liq_collector_logs]
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.data.liq_loader import REPO_ROOT, build_collector_cmd  # noqa: E402
from _shared.ops.heartbeat import write_beat  # noqa: E402
from _shared.ops.supervisor import (  # noqa: E402
    RestartPolicy,
    Supervisor,
    VersionLedger,
)

DEFAULT_LOG_DIR = REPO_ROOT / "workdir" / "liq_collector_logs"
# Always-on ingest daemon: generous restart budget, 5s → 300s backoff.
DEFAULT_POLICY = RestartPolicy(
    max_restarts=100, base_delay_sec=5.0, max_delay_sec=300.0,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--proxy",
        default=os.environ.get("LIQ_PROXY", "http://127.0.0.1:7890"),
        help="'none' for a direct connection",
    )
    ap.add_argument("--beat-interval", type=float, default=30.0)
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    args = ap.parse_args()

    log_dir = Path(args.log_dir)
    beat_path = log_dir / "collector_beat.json"
    ledger = VersionLedger(log_dir / "version_ledger.jsonl", log_dir / "collector.pid")
    sup = Supervisor(
        cmd=build_collector_cmd(proxy=args.proxy),
        workdir=REPO_ROOT,
        log_dir=log_dir,
        policy=DEFAULT_POLICY,
        ledger=ledger,
        repo_dir=REPO_ROOT,
    )

    stop = threading.Event()

    def _on_signal(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    result: dict[str, int] = {}

    def _run() -> None:
        result["code"] = sup.supervise()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    while worker.is_alive() and not stop.is_set():
        child = sup._proc  # noqa: SLF001 — status reporting only
        write_beat(
            beat_path,
            state="running",
            extra={
                "child_pid": child.pid if child is not None else None,
                "launches": len(sup.launches),
            },
        )
        stop.wait(args.beat_interval)

    if stop.is_set() and worker.is_alive():
        write_beat(beat_path, state="draining")
        sup.drain(timeout_sec=10.0)
        worker.join(timeout=15.0)

    code = result.get("code", 0)
    write_beat(beat_path, state="stopped", extra={"exit_code": code})
    print(f"supervised liq collector exited with code {code}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
