"""CLI helper to materialise a state.json from a journal.jsonl.

Usage::

    python -m slippage_sqrt_p7exec_027.rebuild_checkpoint <journal_dir>

Used when the journal exists but the checkpoint has been lost
(e.g. disk crash, operator-rotation, manual wipe). Walks every
estimate row in the journal and rebuilds the aggregate counters,
writing a single fresh ``state.json``.

This is the recovery path for ``SlippageSqrtJournalReplayRequired``.
Because the kernel is a pure function, every aggregate (per-symbol
cumulative impact bps, per-verdict tally, total requests, min/max
per symbol) can be reconstructed deterministically by replaying the
journal rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .exceptions import SlippageSqrtHalt, SlippageSqrtJournalReplayRequired
from .journal import Checkpoint, SlippageSqrtJournal, SymbolAggregate, now_ms
from .models import KERNEL_VERSION, VERDICTS_ALL, _is_nonnegative_number


def rebuild(journal_dir: Path) -> Checkpoint:
    """Walk every estimate row in the journal and rebuild a checkpoint."""
    journal_dir = Path(journal_dir)
    journal = SlippageSqrtJournal(journal_dir, KERNEL_VERSION)

    rows = journal.replay_estimates()

    per_symbol: dict[str, SymbolAggregate] = {}
    verdict_counts: dict[str, int] = {v: 0 for v in VERDICTS_ALL}
    total = 0
    anomalies = 0

    for row in rows:
        request = row.get("request", {})
        estimate = row.get("estimate", {})
        fill_id = request.get("fill_id") or estimate.get("fill_id")
        symbol = request.get("symbol") or estimate.get("symbol")
        if not symbol:
            raise SlippageSqrtHalt(
                f"journal row is missing 'symbol': fill_id={fill_id!r}"
            )

        impact_bps = float(estimate.get("temporary_impact_bps", 0.0))
        verdict = estimate.get("verdict", "")
        if verdict not in VERDICTS_ALL:
            raise SlippageSqrtHalt(
                f"unknown verdict {verdict!r} for fill_id={fill_id!r}"
            )

        if not _is_nonnegative_number(impact_bps) or impact_bps != impact_bps:
            # NaN / -inf / etc. — count as anomaly but still include
            # the row in totals so the journal replay is faithful.
            anomalies += 1

        agg = per_symbol.get(symbol)
        if agg is None:
            agg = SymbolAggregate()
            per_symbol[symbol] = agg
        agg.n_requests += 1
        agg.cumulative_impact_bps += impact_bps if impact_bps == impact_bps else 0.0
        agg.cumulative_qty += float(request.get("qty", 0.0))
        agg.cumulative_participation += float(estimate.get("participation", 0.0))
        if impact_bps == impact_bps:  # not NaN
            if agg.n_requests == 1 or impact_bps < agg.min_impact_bps:
                agg.min_impact_bps = impact_bps
            if impact_bps > agg.max_impact_bps:
                agg.max_impact_bps = impact_bps

        verdict_counts[verdict] += 1
        total += 1

    ckp = Checkpoint(
        kernel_version=KERNEL_VERSION,
        written_at_ms=now_ms(),
        next_seq_in_session=len(rows),
        seen_fill_ids={},
        total_requests=total,
        kernel_arithmetic_anomaly_count=anomalies,
        verdict_counts=verdict_counts,
        per_symbol=per_symbol,
    )
    journal.write_checkpoint(ckp)
    journal.close()
    return ckp


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="slippage_sqrt_p7exec_027.rebuild_checkpoint",
        description=(
            "Materialise a state.json from a journal.jsonl under "
            "<journal_dir>. Use after SlippageSqrtJournalReplayRequired."
        ),
    )
    parser.add_argument(
        "journal_dir",
        help="Directory containing journal.jsonl (and where state.json will be written).",
    )
    args = parser.parse_args(argv)
    journal_dir = Path(args.journal_dir)
    if not journal_dir.exists():
        print(f"error: journal_dir does not exist: {journal_dir}", file=sys.stderr)
        return 2

    try:
        ckp = rebuild(journal_dir)
    except SlippageSqrtHalt as exc:
        print(f"halt: {exc}", file=sys.stderr)
        return 1

    print(
        f"rebuild_checkpoint: wrote state.json with "
        f"{ckp.total_requests} requests across "
        f"{len(ckp.per_symbol)} symbols"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))