#!/usr/bin/env python3
"""OKX 订单簿采集器 — supervisor + heartbeat 常驻入口（WS 重写版）。

历史版本（REST 轮询）有致命缺陷：只存衍生指标（imb_5/10/50、wall ratio），
丢弃原始档位数据，下游研究无法重建订单簿。现重写为：

  - 真正的采集 worker 是 ``scripts/collect_okx_book_ws.py``：OKX 公共 WS
    ``books5`` 频道，**存原始 5 档 bid/ask**（price+qty）JSONL，按日期分文件
    写入 ``data/okx_book/``，并经 ``_shared/data/ingest_ts.py`` 打双时间戳；
  - 本脚本只做常驻化：``_shared.ops.supervisor.Supervisor`` 崩溃自动重启
    （指数退避 + launch ledger + pid 文件），heartbeat beat 文件供
    watcher 检测卡死（H14/H15 dead-man's switch），SIGTERM/SIGINT →
    graceful drain。模式与 ``scripts/run_liq_collector.py`` 一致。

Layout under ``workdir/okx_book_collector_logs/`` (default --log-dir):
    launch_XXXX.log        per-launch child stdout/stderr (supervisor)
    version_ledger.jsonl   launch ledger (pid, version_dir, git_hash)
    collector.pid          current child pid
    collector_beat.json    heartbeat beat ({"ts", "state", "child_pid", ...})

Usage:
    python3 scripts/run_okx_book_collector.py [--proxy http://127.0.0.1:7890] \
        [--beat-interval 30] [--log-dir workdir/okx_book_collector_logs]
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _shared.ops.heartbeat import write_beat  # noqa: E402
from _shared.ops.supervisor import (  # noqa: E402
    RestartPolicy,
    Supervisor,
    VersionLedger,
)

DEFAULT_LOG_DIR = ROOT / "workdir" / "okx_book_collector_logs"
WORKER_SCRIPT = ROOT / "scripts" / "collect_okx_book_ws.py"
# Always-on ingest daemon: generous restart budget, 5s → 300s backoff.
DEFAULT_POLICY = RestartPolicy(
    max_restarts=100, base_delay_sec=5.0, max_delay_sec=300.0,
)


def build_collector_cmd(proxy: str, data_dir: str | None = None) -> list[str]:
    """Command line for the WS collector worker."""
    cmd = [sys.executable, str(WORKER_SCRIPT), "--proxy", proxy]
    if data_dir:
        cmd += ["--data-dir", data_dir]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--proxy",
        default=os.environ.get("OKX_PROXY", "http://127.0.0.1:7890"),
        help="'none' for a direct connection",
    )
    ap.add_argument("--beat-interval", type=float, default=30.0)
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--data-dir", default=None,
                    help="override worker output dir (default data/okx_book)")
    args = ap.parse_args()

    log_dir = Path(args.log_dir)
    beat_path = log_dir / "collector_beat.json"
    ledger = VersionLedger(log_dir / "version_ledger.jsonl", log_dir / "collector.pid")
    sup = Supervisor(
        cmd=build_collector_cmd(proxy=args.proxy, data_dir=args.data_dir),
        workdir=ROOT,
        log_dir=log_dir,
        policy=DEFAULT_POLICY,
        ledger=ledger,
        repo_dir=ROOT,
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
    print(f"supervised okx book collector exited with code {code}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
