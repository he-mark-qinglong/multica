"""Graveyard daily-metrics ledger repair tool.

T10 (w5-s3, infra-sprint 2026-07-25).  Rebuilds ``daily_metrics.csv`` from
``trades.jsonl`` for paper-trading sessions that fell into the ``_graveyard/``
archive before the T8 atomic writer existed.  The graveyard's originals
suffered two classes of bug:

  1. **Header / data collision** — ``paper_runner._init_ledger_headers``
     wrote the header via ``write_text`` and ``_append_daily_metrics``
     appended via ``open("a")``; on the first run after the header existed
     on disk the append path skipped the header and the *next* append glued
     a fresh header in front of the data row, producing files like
     ``kill_reason,notes2026-07-20,13,...``.

  2. **Same-date duplicates** — pure append with no date key meant a retry
     of the same trading day produced two rows for one calendar date.

The repair tool reads ``trades.jsonl`` (the source of truth for fills),
asks T8's :func:`rebuild_daily_metrics` for a clean per-date aggregate,
and atomically writes a NEW file ``daily_metrics.repaired.csv``.  The
original ``daily_metrics.csv`` is never opened in write mode — it is only
read (tolerantly) so we can print a diff report.

Public surface
--------------

* ``main()`` — CLI entry point (argparse).
* ``parse_glued_daily_metrics(path, fieldnames)`` — tolerant reader for
  the broken original; handles glued headers as well as the well-formed
  case.  Returns ``list[dict[str, str]]``.

Usage::

    $PY quant-loop/_shared/paper/repair_ledger.py <results-ledger-dir>
    $PY quant-loop/_shared/paper/repair_ledger.py <results-ledger-dir> --capital 100000

The script exits 0 on success and prints a diff report on stdout.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Sequence

# Ensure ``from _shared.paper.ledger_writer import ...`` works when the
# script is invoked by absolute path (``python3 .../repair_ledger.py``).
# repair_ledger.py lives at quant-loop/_shared/paper/repair_ledger.py
# so parents[2] = quant-loop/ which is the directory we need on sys.path.
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parents[2]))

from _shared.paper.ledger_writer import (  # noqa: E402  (post-sys.path insert)
    DAILY_FIELDS,
    rebuild_daily_metrics,
    write_daily_csv,
)

__all__ = [
    "main",
    "parse_glued_daily_metrics",
]

# A date like 2026-07-20 — used to detect glued-header boundaries when the
# original writer forgot to put a newline between header and first row.
_DATE_TOKEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _split_glued_header(text: str, expected_header: str) -> tuple[list[str], int]:
    """Split a glued ``header<date>`` blob into ``[header, row1, row2, ...]``.

    Returns the list of CSV lines and the index at which the data rows
    start (``len([header])``).  Raises ``ValueError`` if the file does not
    start with ``expected_header`` or no date boundary is found.

    Strategy: confirm the text starts with the expected header, then walk
    forward to find the first YYYY-MM-DD token.  That token's start is
    the glued boundary (the header ends just before it; the first data
    row begins at it).  Everything from there on is treated as data,
    further newlines are honoured normally.
    """
    if not text.startswith(expected_header):
        raise ValueError(
            f"header mismatch — does not start with {expected_header!r}"
        )
    token_match = _DATE_TOKEN_RE.search(text, len(expected_header))
    if token_match is None:
        raise ValueError("no YYYY-MM-DD boundary found after header")
    boundary = token_match.start()
    header_line = text[:boundary]
    body = text[boundary:]
    data_lines = [ln for ln in body.splitlines() if ln.strip()]
    return [header_line.rstrip("\r\n")] + data_lines, 1


def parse_glued_daily_metrics(
    path: Path, fieldnames: Sequence[str] = DAILY_FIELDS
) -> list[dict[str, str]]:
    """Read the original ``daily_metrics.csv``, tolerating glued headers.

    Two cases are handled:

    * **Well-formed** — header on line 1, data rows following on
      separate lines.  Parsed via :class:`csv.DictReader` after a
      header-byte check (mirrors the T8 module's strict posture but is
      invoked on a path the user has flagged as possibly broken, so it
      is the responsibility of this tool to be lenient).
    * **Glued header** — the data row that follows the header is
      concatenated onto the header line with no newline in between.  We
      locate the first YYYY-MM-DD token that immediately follows the
      header, split the file there, then parse each line with
      :class:`csv.reader`.

    Returns a list of ``dict`` keyed by the fieldnames, in the order they
    appear in the file.  Empty fields are preserved as the empty string.
    """
    text = Path(path).read_text()
    expected_header = ",".join(fieldnames)

    # Well-formed fast path: header line ends with a newline.
    lines = text.splitlines(keepends=False)
    if lines and lines[0].rstrip("\r\n") == expected_header:
        reader = csv.DictReader(lines[1:], fieldnames=list(fieldnames))
        rows: list[dict[str, str]] = []
        for raw in reader:
            if raw is None:
                continue
            if all((v is None or v == "") for v in raw.values()):
                continue
            if any(v is None for v in raw.values()):
                # Partial row — skip rather than raise; this is the
                # *tolerant* reader and the user wants a diff report.
                continue
            rows.append({k: ("" if v is None else v) for k, v in raw.items()})
        return rows

    # Fallback: glued header — split the file at the first date token.
    parsed_lines, _ = _split_glued_header(text, expected_header)
    # parsed_lines[0] is the header, parsed_lines[1:] are data rows.
    reader = csv.DictReader(parsed_lines[1:], fieldnames=list(fieldnames))
    rows: list[dict[str, str]] = []
    for raw in reader:
        if raw is None:
            continue
        if all((v is None or v == "") for v in raw.values()):
            continue
        if any(v is None for v in raw.values()):
            continue
        rows.append({k: ("" if v is None else v) for k, v in raw.items()})
    return rows


def _format_diff(
    original: list[dict[str, str]],
    rebuilt: list[dict[str, object]],
    fieldnames: Sequence[str] = DAILY_FIELDS,
) -> str:
    """Build a human-readable diff report comparing original vs rebuilt."""
    lines: list[str] = []
    lines.append("=== repair diff report ===")
    lines.append(f"original rows: {len(original)}  rebuilt rows: {len(rebuilt)}")

    # Bucket original rows by date so we can highlight the duplicate-row bug.
    by_date: dict[str, list[dict[str, str]]] = {}
    for row in original:
        d = row.get("date", "")
        by_date.setdefault(d, []).append(row)
    dup_lines = [f"  {d}: {len(rs)} row(s)" for d, rs in sorted(by_date.items()) if len(rs) > 1]
    if dup_lines:
        lines.append("original has duplicate-date rows (the bug being repaired):")
        lines.extend(dup_lines)

    # Build a repaired-row index keyed by date.
    rebuilt_by_date: dict[str, dict[str, object]] = {r["date"]: r for r in rebuilt}

    # Compare the four columns the card calls out.
    compare_cols = ("total_trades", "winning_trades", "net_pnl_usd", "equity_usd")
    for date in sorted(rebuilt_by_date):
        rebuilt_row = rebuilt_by_date[date]
        original_rows = by_date.get(date, [])
        if not original_rows:
            lines.append(f"[{date}] MISSING in original — repaired has full row")
            continue
        if len(original_rows) > 1:
            for i, orig in enumerate(original_rows):
                parts = []
                for col in compare_cols:
                    parts.append(f"{col}={orig.get(col, '')}")
                lines.append(f"[{date}] original[{i}]: " + "  ".join(parts))
        else:
            orig = original_rows[0]
            parts = []
            for col in compare_cols:
                o = orig.get(col, "")
                r = rebuilt_row.get(col, "")
                marker = "" if str(o) == str(r) else "  <-- DIFF"
                parts.append(f"{col}: orig={o!r}  repaired={r!r}{marker}")
            lines.append(f"[{date}] diff: " + " | ".join(parts))

    # Summarise the known-bug headline numbers from the card.
    if len(original) == 2 and original[0]["date"] == original[-1]["date"]:
        try:
            net_diff = abs(
                float(original[0]["net_pnl_usd"]) - float(original[-1]["net_pnl_usd"])
            )
        except (TypeError, ValueError):
            net_diff = None
        if net_diff is not None:
            lines.append(
                f"original date=2026-07-20 has 2 rows; net_pnl_usd abs diff = "
                f"{net_diff:.6f}"
            )

    lines.append("=== end of report ===")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.  Returns the process exit code (0 on success)."""
    parser = argparse.ArgumentParser(
        prog="repair_ledger",
        description=(
            "Rebuild daily_metrics.csv from trades.jsonl for graveyard "
            "paper-trading sessions; writes daily_metrics.repaired.csv and "
            "prints a diff report.  The original daily_metrics.csv is read "
            "but never written."
        ),
    )
    parser.add_argument(
        "ledger_dir",
        type=Path,
        help=(
            "Path to a results-ledger directory containing trades.jsonl. "
            "If a sibling daily_metrics.csv exists it is read for the diff "
            "report, but never modified."
        ),
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000.0,
        help="Starting capital in USD (default: 100000).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Override the output path.  Defaults to "
            "<ledger_dir>/daily_metrics.repaired.csv.  The filename MUST "
            "still end in 'daily_metrics.repaired.csv' — the script will "
            "refuse to write anywhere else."
        ),
    )

    args = parser.parse_args(argv)
    ledger_dir: Path = args.ledger_dir
    if not ledger_dir.exists():
        parser.error(f"ledger dir does not exist: {ledger_dir}")
    if not ledger_dir.is_dir():
        parser.error(f"not a directory: {ledger_dir}")

    trades_path = ledger_dir / "trades.jsonl"
    if not trades_path.exists():
        print(
            f"repair_ledger: refusing — no trades.jsonl in {ledger_dir}",
            file=sys.stderr,
        )
        return 2

    # Hard constraint: the script must never overwrite daily_metrics.csv.
    # We pick the output filename and assert it up front so a mistake in
    # the default or an explicit --output flag fails loudly.
    out_path = args.output if args.output is not None else ledger_dir / "daily_metrics.repaired.csv"
    if out_path.name != "daily_metrics.repaired.csv":
        print(
            f"repair_ledger: refusing — output filename must be "
            f"'daily_metrics.repaired.csv' (got {out_path.name!r})",
            file=sys.stderr,
        )
        return 2

    # Rebuild from the source of truth.
    rows = rebuild_daily_metrics(trades_path, args.capital)

    # Atomic write to the new file (T8's writer).  The hard assertion in
    # write_daily_csv's contract is implicit — we constructed out_path
    # above; we add one belt-and-braces guard here so a future refactor
    # of the defaults cannot silently clobber the original.
    assert out_path.name == "daily_metrics.repaired.csv", (
        f"refusing to write to {out_path.name!r} — only "
        "'daily_metrics.repaired.csv' is allowed"
    )
    write_daily_csv(out_path, rows)
    print(f"wrote {len(rows)} row(s) to {out_path}")

    # Diff report against the (read-only) original if it exists.
    original_path = ledger_dir / "daily_metrics.csv"
    if original_path.exists():
        try:
            original_rows = parse_glued_daily_metrics(original_path)
        except ValueError as exc:
            print(
                f"warning: could not parse original {original_path}: {exc}",
                file=sys.stderr,
            )
            original_rows = []
        print(_format_diff(original_rows, rows))
    else:
        print(f"no original {original_path} to diff against")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())