"""CLI entry point mirroring ``python -m live_data.open_interest_history``.

Run with::

    python -m live_data.open_interest_history --help

or::

    cd quant-loop && python -m live_data.open_interest_history --help

Migrated verbatim from ``trading/src/data/open_interest_history.py``
``main()`` at ``da0020de89575c0694b5763c0628a486612d6256``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Sequence

from ._helpers import SUPPORTED_PERIODS, windowed_iter
from .backfiller import OIBackfiller
from .manager import OpenInterestDataManager


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m live_data.open_interest_history",
        description=(
            "Backfill historical open interest for perpetual swaps into "
            "local parquet files."
        ),
    )
    p.add_argument("--exchange", default="binance", choices=list(OIBackfiller.EXCHANGES))
    p.add_argument("--symbol", action="append", default=[],
                   help="Friendly symbol, repeatable (e.g. --symbol BTC --symbol ETH).")
    p.add_argument("--period", action="append", default=None,
                   help="One or more periods from "
                        f"{list(SUPPORTED_PERIODS)}. Defaults to all.")
    p.add_argument("--start-ms", type=int, default=None,
                   help="Start (Unix ms). Default: resume from earliest local row, "
                        "or 30 days back if no local file.")
    p.add_argument("--end-ms", type=int, default=None,
                   help="End (Unix ms). Default: now.")
    p.add_argument("--data-dir", default="./data/open_interest",
                   help="Base directory for parquet storage.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute windowing plan only; no network, no disk writes.")
    p.add_argument("--show-config", action="store_true",
                   help="Print resolved config (exchanges, periods, default data dir) and exit.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)

    if args.show_config:
        print("open_interest_history config:")
        print(f"  exchanges:  {list(OIBackfiller.EXCHANGES)}")
        print(f"  periods:    {list(SUPPORTED_PERIODS)}")
        print(f"  data-dir:   {args.data_dir}")
        print(f"  argv:       {vars(args)}")
        return 0

    if not args.symbol:
        print("error: --symbol is required (use --symbol BTC, repeatable). "
              "Pass --show-config to print config without backfilling.",
              file=sys.stderr)
        return 2

    periods = tuple(args.period) if args.period else SUPPORTED_PERIODS
    manager = OpenInterestDataManager(args.data_dir)

    if args.dry_run:
        for symbol in args.symbol:
            for period in periods:
                start = args.start_ms
                end = args.end_ms or int(time.time() * 1000)
                if start is None:
                    start = end - 30 * 24 * 60 * 60 * 1000
                windows = list(windowed_iter(start, end, period))
                print(f"[dry-run] {symbol} {period}: "
                      f"{len(windows)} window(s) covering "
                      f"{datetime.fromtimestamp(start/1000, tz=timezone.utc)} -> "
                      f"{datetime.fromtimestamp(end/1000, tz=timezone.utc)}")
        return 0

    loader = OIBackfiller(args.exchange)
    for symbol in args.symbol:
        for period in periods:
            df = loader.backfill(
                symbol,
                period=period,
                start_ms=args.start_ms,
                end_ms=args.end_ms,
                manager=manager,
            )
            print(f"✅ {symbol} {period}: {len(df)} rows")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())