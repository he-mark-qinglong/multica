"""partial_fill_accumulator — P7-EXEC-056.

Aggregates the stream of partial fills for one ``client_order_id``
into a running :class:`PartialFillState` (cumulative qty,
volume-weighted average price, fill count, first/last timestamp,
terminal status) and journals every event so the run is
recoverable from disk on cold start.

Why
----
The live-paper connector (SPEC_live_paper_connector_binance_usdm)
emits one trade-log row per fill (§4.1). Strategies that want
post-fill observability — slippage attribution
(P7-EXEC-043), VWAP realised price (P7-EXEC-026), fill-rate
dashboards — need a per-order rolling aggregate, not a raw event
stream. ``partial_fill_accumulator`` is that aggregate: one
immutable :class:`PartialFillState` per ``client_order_id`,
updateable on every fill, durable across a process restart via
the sqlite WAL journal.

The component is a hot-path observer: ``on_fill(event)`` runs
inside the runner's per-fill critical section with a 250us budget
per call (MAP-P7 default policy). It is single-threaded per
``client_order_id`` — the runner's connector instance owns one
accumulator and serialises all on_fill calls.

Folder convention: ``partial_fill_accumulator_p7exec_056/`` per
the MAP-P7 Live Trading Infrastructure project rule (suffix
``_p7exec_NNN``, never ``_v1`` / ``_v2``).

Public surface
--------------
* :class:`FillEvent` — input fill event from the connector.
* :class:`PartialFillState` — running aggregation.
* :class:`Accumulator` — in-memory state keyed by client_order_id,
  journal-backed.
* :class:`PartialFillJournal` — sqlite WAL wrapper.
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :data:`TERMINAL_STATUSES` — frozenset of accepted terminal values.
* :data:`LATE_FILL_LIQUIDITY` — sentinel liquidity tag for late events.
* :exc:`LateFillRejected` — raised on fills after terminal status.

See :mod:`partial_fill_accumulator_p7exec_056.partial_fill_accumulator`
for the implementation, ``README.md`` for the spec,
``INTERFACE.md`` for the wire contract, and ``SPEC.md`` for the
extended design doc.
"""
from .partial_fill_accumulator import (
    LATE_FILL_LIQUIDITY,
    TERMINAL_STATUSES,
    Accumulator,
    FillEvent,
    LateFillRejected,
    PartialFillJournal,
    PartialFillState,
    SCHEMA_SQL,
    bootstrap_journal,
)

__all__ = [
    "FillEvent",
    "PartialFillState",
    "Accumulator",
    "PartialFillJournal",
    "LateFillRejected",
    "LATE_FILL_LIQUIDITY",
    "TERMINAL_STATUSES",
    "bootstrap_journal",
    "SCHEMA_SQL",
]

__version__ = "0.1.0"
__issue__ = "SMA-36243"