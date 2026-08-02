#!/usr/bin/env python3
"""Keep collect_liquidations.py alive under _shared.ops.supervisor (F6).

Thin CLI over ``_shared.data.liq_loader.run_supervised``: crash auto-restart
with exponential backoff, launch ledger + pid file under
``workdir/liq_collector_logs/``.

Usage:
    python3 scripts/collect_liquidations_supervised.py [--proxy http://127.0.0.1:7890]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.data.liq_loader import run_supervised  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--proxy", default="http://127.0.0.1:7890")
    args = ap.parse_args()
    code = run_supervised(proxy=args.proxy)
    print(f"supervised collector exited with code {code}")
    sys.exit(code)


if __name__ == "__main__":
    main()
