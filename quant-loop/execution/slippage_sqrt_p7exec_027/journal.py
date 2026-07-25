"""Write-ahead log (WAL) persistence for the slippage_sqrt calculator.

Two artifacts live under ``journal_dir``:

* ``journal.jsonl`` — append-only estimate rows. Each line is a JSON
  object with ``kind`` set to ``"estimate"`` carrying the round-trip
  inputs (the original ``SlippageSqrtRequest`` fields) AND the produced
  estimate fields. Every call to ``SlippageSqrtCalculator.estimate``
  writes exactly one row. Rows are fsynced to disk so a process
  crash cannot lose rows.

* ``state.json`` — checkpointed view of the aggregate counters,
  written every N requests (default 100). On startup, the calculator
  rehydrates by reading ``state.json`` if newer than ``journal.jsonl``;
  otherwise it replays the journal from scratch. This guarantees
  exactly-once counting of every request.

This module is intentionally minimal: one file handle, one lock, one
append per row. No rotation, no compression, no async — those are the
execution runner's responsibility, not the slippage_sqrt calculator's.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Dict, IO, List, Optional

from .exceptions import (
    SlippageSqrtHalt,
    SlippageSqrtJournalReplayRequired,
    SlippageSqrtJournalWriteError,
)
from .models import CHECKPOINT_FILENAME, JOURNAL_FILENAME


def now_ms() -> int:
    """Monotonic-ish epoch ms. Uses time.time() — wall clock, not perf clock."""
    return int(time.time() * 1000)


@dataclass
class EstimateJournalRow:
    """One row written to ``journal.jsonl``.

    A row is always an estimate (``kind='estimate'``). It carries
    both the original request inputs (so reviewers can recompute the
    estimate from scratch) and the produced bps numbers (so dashboards
    do not have to re-run the kernel for every historical fill).
    Tracker-side metadata (``written_at_ms``, ``kernel_version``,
    ``seq_in_session``) is stamped at write time.
    """

    kind: str  # always "estimate" for this component
    request: Dict[str, Any]
    estimate: Dict[str, Any]
    written_at_ms: int
    kernel_version: str
    seq_in_session: int


@dataclass
class SymbolAggregate:
    """Per-symbol aggregate counters used both in checkpoint state
    and on the live read path.

    Stored as plain Python primitives so the JSON serialisation is
    stable across processes.
    """

    n_requests: int = 0
    cumulative_impact_bps: float = 0.0
    cumulative_qty: float = 0.0
    cumulative_participation: float = 0.0
    min_impact_bps: float = float("inf")
    max_impact_bps: float = 0.0


@dataclass
class Checkpoint:
    """Snapshot of aggregate counters for fast restart."""

    kernel_version: str
    written_at_ms: int
    next_seq_in_session: int
    seen_fill_ids: Dict[str, int] = field(default_factory=dict)  # fill_id -> written_at_ms
    total_requests: int = 0
    kernel_arithmetic_anomaly_count: int = 0
    verdict_counts: Dict[str, int] = field(default_factory=dict)  # verdict -> count
    per_symbol: Dict[str, SymbolAggregate] = field(default_factory=dict)  # symbol -> agg

    def to_json(self) -> str:
        d = asdict(self)
        # SymbolAggregate inside per_symbol needs to be re-shaped on
        # the way out (it serialises as a nested dict already thanks
        # to asdict, so nothing else to do).
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Checkpoint":
        d = json.loads(raw)
        # Reconstruct SymbolAggregate
        if "per_symbol" in d:
            d["per_symbol"] = {
                sym: SymbolAggregate(**agg)
                for sym, agg in d["per_symbol"].items()
            }
        return cls(**d)


class SlippageSqrtJournal:
    """Append-only journal + checkpoint writer for the slippage_sqrt calculator.

    The journal file handle is opened once at construction in append
    mode (line-buffered is *not* used; we explicitly ``flush()`` +
    ``os.fsync()`` after every write). The handle is closed via the
    context manager protocol or the explicit ``close()`` method.
    """

    def __init__(self, journal_dir: Path, kernel_version: str) -> None:
        self._dir = Path(journal_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._kernel_version = kernel_version
        self._lock = RLock()
        self._next_seq = 0
        self._handle: Optional[IO[str]] = None
        self._path = self._dir / JOURNAL_FILENAME
        self._checkpoint_path = self._dir / CHECKPOINT_FILENAME
        # Open in append mode so restarts do not clobber the existing journal.
        self._handle = open(self._path, "a", encoding="utf-8", buffering=1)

    @property
    def journal_path(self) -> Path:
        return self._path

    @property
    def checkpoint_path(self) -> Path:
        return self._checkpoint_path

    # ------------------------------------------------------------------ write
    def write_estimate(self, request_payload: Dict[str, Any], estimate_payload: Dict[str, Any]) -> EstimateJournalRow:
        """Append one estimate row. fsyncs before returning.

        Raises ``SlippageSqrtJournalWriteError`` if the journal
        handle is closed, or the underlying ``write``/``flush``/
        ``fsync`` raises. The calculator does not silently swallow
        journal failures — losing the ability to persist would
        silently drop fills in the order journal, which the parent
        issue forbids.
        """
        with self._lock:
            if self._handle is None:
                raise SlippageSqrtJournalWriteError(
                    f"journal handle is closed; cannot append estimate "
                    f"{request_payload.get('fill_id', '<unknown>')!r}"
                )
            row = EstimateJournalRow(
                kind="estimate",
                request=request_payload,
                estimate=estimate_payload,
                written_at_ms=now_ms(),
                kernel_version=self._kernel_version,
                seq_in_session=self._next_seq,
            )
            line = json.dumps(asdict(row), sort_keys=True)
            try:
                self._handle.write(line + "\n")
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except (OSError, IOError) as exc:
                raise SlippageSqrtJournalWriteError(
                    f"failed to append estimate {request_payload.get('fill_id', '<unknown>')!r}: {exc}"
                ) from exc
            self._next_seq += 1
            return row

    # ----------------------------------------------------------------- replay
    def replay_estimates(self) -> List[Dict[str, Any]]:
        """Read every estimate row from the journal, oldest first.

        Used by the rebuild_checkpoint helper. Errors out on
        corrupted rows — the calculator does NOT silently skip.
        Returns one dict per row; each dict has shape::

            {"request": {...}, "estimate": {...}}
        """
        rows: List[Dict[str, Any]] = []
        if not self._path.exists():
            return rows
        with open(self._path, "r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise SlippageSqrtHalt(
                        f"corrupted journal at {self._path}:{lineno}: {exc}"
                    ) from exc
                if obj.get("kind") != "estimate":
                    raise SlippageSqrtHalt(
                        f"unexpected row kind {obj.get('kind')!r} at {self._path}:{lineno}"
                    )
                rows.append(
                    {
                        "request": obj.get("request", {}),
                        "estimate": obj.get("estimate", {}),
                    }
                )
        return rows

    # ------------------------------------------------------------ checkpoint
    def write_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Atomically write the checkpoint to ``state.json``.

        Uses a tmp-file + ``os.replace`` for atomicity on POSIX.
        Raises ``SlippageSqrtJournalWriteError`` on any failure.
        """
        with self._lock:
            tmp_path = self._checkpoint_path.with_suffix(".json.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(checkpoint.to_json())
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._checkpoint_path)
            except (OSError, IOError) as exc:
                # Best-effort cleanup of the tmp file
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                raise SlippageSqrtJournalWriteError(
                    f"failed to write checkpoint {self._checkpoint_path}: {exc}"
                ) from exc

    def read_checkpoint(self) -> Optional[Checkpoint]:
        """Read the checkpoint if it exists and is newer than the journal.

        Returns ``None`` when no checkpoint is present. Raises
        ``SlippageSqrtJournalReplayRequired`` when the journal is
        present AND non-empty but no checkpoint (or an older
        checkpoint) is available — the calculator should not silently
        drop requests, so the caller is forced to acknowledge the gap.

        A checkpoint newer than the journal is impossible in normal
        operation, but we still raise
        ``SlippageSqrtJournalReplayRequired`` defensively to avoid the
        silent-drop trap.

        Note: an empty (zero-byte) journal file is treated as "no
        journal" because the constructor creates the file at open
        time. Real journal content is only written after the first
        successful ``estimate``.
        """
        # A journal file is "present" only if it has been written to.
        jrn_present = self._path.exists() and self._path.stat().st_size > 0
        ckp_present = self._checkpoint_path.exists() and self._checkpoint_path.stat().st_size > 0

        if not ckp_present:
            if not jrn_present:
                # Cold start: no journal, no checkpoint — return None.
                return None
            # Journal exists but no checkpoint. Force a rebuild.
            raise SlippageSqrtJournalReplayRequired(
                f"journal present at {self._path} but no checkpoint at "
                f"{self._checkpoint_path}; run rebuild_checkpoint.py"
            )

        if not jrn_present:
            # Checkpoint without journal — also impossible in normal
            # operation; force a rebuild.
            raise SlippageSqrtJournalReplayRequired(
                f"checkpoint present at {self._checkpoint_path} but no "
                f"journal at {self._path}; run rebuild_checkpoint.py"
            )

        ckp_mtime = self._checkpoint_path.stat().st_mtime
        jrn_mtime = self._path.stat().st_mtime
        if ckp_mtime < jrn_mtime:
            # Checkpoint older than journal → must rebuild from journal.
            raise SlippageSqrtJournalReplayRequired(
                f"checkpoint at {self._checkpoint_path} is older than "
                f"journal at {self._path}; run rebuild_checkpoint.py"
            )

        try:
            with open(self._checkpoint_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except (OSError, IOError) as exc:
            raise SlippageSqrtHalt(
                f"failed to read checkpoint {self._checkpoint_path}: {exc}"
            ) from exc
        try:
            return Checkpoint.from_json(raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise SlippageSqrtHalt(
                f"corrupted checkpoint at {self._checkpoint_path}: {exc}"
            ) from exc

    # ---------------------------------------------------------------- close
    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.flush()
                    os.fsync(self._handle.fileno())
                except (OSError, IOError):
                    pass
                try:
                    self._handle.close()
                except (OSError, IOError):
                    pass
                self._handle = None

    # -------------------------------------------------------------- helpers
    def advance_seq(self, n: int) -> None:
        """Advance the in-session sequence counter by ``n`` (used by replay)."""
        with self._lock:
            self._next_seq += n

    def current_seq(self) -> int:
        with self._lock:
            return self._next_seq

    def __enter__(self) -> "SlippageSqrtJournal":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False