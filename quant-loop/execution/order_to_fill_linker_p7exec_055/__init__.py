"""order_to_fill_linker — P7-EXEC-055.

Correlates the venue-assigned ``orderId`` with the
connector-assigned ``client_order_id`` and the strategy's order
*intent*. The linker is the durable record of "we asked the venue
to do X" vs. "the venue reported doing Y" — every FillReport is
journaled exactly once with a link back to the originating intent
(or flagged ``is_orphan`` if no intent was known at the time of
the report).

Why
----
Three sibling components already cover parts of this:

* ``partial_fill_accumulator_p7exec_056`` aggregates per-coid fills
  for the VWAP / fill-rate dashboards.
* ``venue_fill_quality_p7exec_080`` scores each venue's fill quality.
* ``pnl_attribution_per_fill_p7exec_089`` attributes PnL per fill.

None of them maps an incoming FillReport back to the *intent* the
strategy issued. That mapping is the linker's job — when the
venue sends an ORDER_TRADE_UPDATE carrying only ``orderId`` (the
common case after a WS reconnect), the linker is the component
that knows which ``client_order_id`` (and which strategy, which
edge, which tag) that orderId belongs to.

Public surface
--------------
* :class:`OrderIntent` — strategy-side intent (what we asked).
* :class:`FillReport` — venue-side fill (what they reported).
* :class:`LinkRecord` — result of linking a fill to an intent.
* :class:`Linker` — in-memory cache + journal-backed matcher.
* :class:`OrderToFillJournal` — sqlite WAL wrapper.
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :data:`INTENT_STATUSES` — frozenset of accepted intent statuses.
* :data:`ORDER_STATUSES` — frozenset of accepted order statuses.
* :exc:`IntentMismatch` — fill disagrees with known intent.
* :exc:`UnknownClientOrderId` — register/bind on an unknown coid.
* :exc:`OrderIdAlreadyBound` — bind an orderId that is already
  bound to a different coid.

See :mod:`order_to_fill_linker_p7exec_055.order_to_fill_linker`
for the implementation, ``README.md`` for the usage, ``INTERFACE.md``
for the wire contract, and ``SPEC.md`` for the extended design doc.
"""
from .order_to_fill_linker import (
    INTENT_STATUSES,
    ORDER_STATUSES,
    SCHEMA_SQL,
    FillReport,
    IntentMismatch,
    LinkRecord,
    Linker,
    OrderIdAlreadyBound,
    OrderIntent,
    OrderToFillJournal,
    UnknownClientOrderId,
    bootstrap_journal,
)

__all__ = [
    "OrderIntent",
    "FillReport",
    "LinkRecord",
    "Linker",
    "OrderToFillJournal",
    "IntentMismatch",
    "UnknownClientOrderId",
    "OrderIdAlreadyBound",
    "INTENT_STATUSES",
    "ORDER_STATUSES",
    "SCHEMA_SQL",
    "bootstrap_journal",
]

__version__ = "0.1.0"
__issue__ = "SMA-36242"