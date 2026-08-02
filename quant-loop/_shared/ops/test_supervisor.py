"""Tests for _shared/ops/supervisor.py (H16, H17, H4)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import sys as _sys
import threading
import time

from _shared.ops.supervisor import (
    RestartPolicy,
    Supervisor,
    VersionLedger,
    backoff_delays,
)


FAST = RestartPolicy(max_restarts=3, base_delay_sec=0.01, backoff_factor=2.0,
                     max_delay_sec=10.0, keep_logs=2)


def _py(script: str):
    return [_sys.executable, "-c", script]


# --- pure policy ---------------------------------------------------------------
def test_backoff_delays_exponential_and_capped():
    delays = backoff_delays(RestartPolicy(max_restarts=5, base_delay_sec=1.0,
                                          backoff_factor=2.0, max_delay_sec=5.0))
    assert delays == (1.0, 2.0, 4.0, 5.0, 5.0)


# --- H16: crash auto-restart ---------------------------------------------------
def test_clean_exit_no_restart(tmp_path):
    sup = Supervisor(_py("print('ok')"), workdir=tmp_path, log_dir=tmp_path / "logs",
                     policy=FAST)
    assert sup.supervise() == 0
    assert len(sup.launches) == 1
    assert sup.launches[0].exit_code == 0


def test_crash_restarts_up_to_max_then_gives_up(tmp_path):
    sup = Supervisor(_py("import sys; sys.exit(1)"), workdir=tmp_path,
                     log_dir=tmp_path / "logs", policy=FAST)
    code = sup.supervise()
    assert code == 1
    assert len(sup.launches) == 1 + FAST.max_restarts  # initial + restarts
    assert [l.attempt for l in sup.launches] == [0, 1, 2, 3]


def test_keeps_only_last_k_launch_logs(tmp_path):
    policy = RestartPolicy(max_restarts=4, base_delay_sec=0.001, keep_logs=2)
    sup = Supervisor(_py("import sys; sys.exit(1)"), workdir=tmp_path,
                     log_dir=tmp_path / "logs", policy=policy)
    sup.supervise()
    logs = sorted((tmp_path / "logs").glob("launch_*.log"))
    assert len(logs) == 2  # keep_logs respected


# --- H17: version ledger + rollback -------------------------------------------
def test_version_ledger_records_and_rolls_back(tmp_path):
    ledger = VersionLedger(tmp_path / "versions.jsonl", tmp_path / "runner.pid")
    ledger.record_launch(pid=111, version_dir="/repo/v1", git_hash="aaa111", ts=1.0)
    ledger.record_launch(pid=222, version_dir="/repo/v2", git_hash="bbb222", ts=2.0)
    history = ledger.history()
    assert len(history) == 2
    prev = ledger.rollback()
    assert prev is not None
    assert prev["version_dir"] == "/repo/v1"
    assert prev["git_hash"] == "aaa111"
    assert ledger.current_pid() == 222


def test_rollback_none_without_history(tmp_path):
    ledger = VersionLedger(tmp_path / "versions.jsonl", tmp_path / "runner.pid")
    assert ledger.rollback() is None
    ledger.record_launch(pid=1, version_dir="/repo/v1", git_hash="aaa", ts=1.0)
    assert ledger.rollback() is None  # only one version, nothing to roll back to


def test_supervisor_writes_version_and_pid_via_ledger(tmp_path):
    ledger = VersionLedger(tmp_path / "versions.jsonl", tmp_path / "runner.pid")
    sup = Supervisor(_py("pass"), workdir=tmp_path, log_dir=tmp_path / "logs",
                     policy=FAST, ledger=ledger, repo_dir=tmp_path)
    assert sup.supervise() == 0
    entries = ledger.history()
    assert len(entries) == 1
    assert entries[0]["version_dir"] == str(tmp_path)
    assert "git_hash" in entries[0]
    assert entries[0]["pid"] == ledger.current_pid()


# --- H4: graceful drain ---------------------------------------------------------
SIGTERM_OK = (
    "import signal, sys\n"
    "def h(sig, frame):\n"
    "    print('draining', flush=True)\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, h)\n"
    "import time\n"
    "while True: time.sleep(0.05)\n"
)

SIGTERM_IGNORED = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "while True: time.sleep(0.05)\n"
)


def _run_supervised_and_drain(tmp_path, script, drain_timeout):
    sup = Supervisor(_py(script), workdir=tmp_path, log_dir=tmp_path / "logs",
                     policy=FAST)
    result = {}

    def target():
        result["code"] = sup.supervise()

    t = threading.Thread(target=target)
    t.start()
    time.sleep(0.3)  # let the child spawn
    clean = sup.drain(timeout_sec=drain_timeout)
    t.join(timeout=10)
    return sup, result, clean


def test_drain_graceful_on_sigterm(tmp_path):
    sup, result, clean = _run_supervised_and_drain(tmp_path, SIGTERM_OK, 5.0)
    assert clean is True
    assert result["code"] == 0
    assert not t_alive(sup)  # child reaped; supervise returned


def test_drain_force_kills_stubborn_child(tmp_path):
    sup, result, clean = _run_supervised_and_drain(tmp_path, SIGTERM_IGNORED, 0.3)
    assert clean is False
    t = None
    # Child was SIGKILLed -> supervise returned a signal exit code, no restart.
    assert result["code"] is not None and result["code"] != 0
    assert len(sup.launches) == 1


def t_alive(sup):
    return sup._proc is not None and sup._proc.poll() is None
