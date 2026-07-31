"""shadow_book — P7-EXEC-072.

The strategy-side reconstruction ("shadow") of every order's fill
history, maintained alongside the venue-truth ("live") view surfaced by
the connector (see ``SPEC_live_paper_connector_binance_usdm.md §4.1``
for the live trade log). Produces a per-``client_order_id`` and
per-``symbol`` reconciliation row so a downstream drift alert (e.g.
``recon_drift_alert``, sibling recon family) can act on the divergence
without re-deriving it.

Why
----
A live trading strategy tracks its own idea of what happened
("shadow"): what it dispatched, what fills it expects, what
cumulative qty / VWAP it has accumulated. The venue independently
tracks what actually happened ("live"): the user-data stream, the
``/fapi/v1/allOrders`` history, the ``/fapi/v2/positionRisk``
positions snapshot. If shadow and live disagree, one of three things
is true: (1) the venue is the source of truth and our shadow missed
a fill (data loss); (2) the shadow over-counted (state bug); (3) both
are correct but the runner hasn't reconciled yet (latency). All three
are actionable; ``shadow_book`` is the component that makes the diff
mechanical and queryable.

The hot path is ``on_fill(event)``: the connector calls it on every
trade-log row, the component folds the fill into the shadow state for
that ``client_order_id``, journals the row, and updates the per-``(symbol,
side)`` position projection. Reconciliation is a cold path
(``record_live_reports`` then ``reconcile``) called periodically (e.g.
once per minute) against a snapshot of the venue-truth view.

The component is a hot-path observer: ``on_fill(event)`` runs inside
the runner's per-fill critical section with a 250us budget per call
(MAP-P7 default policy). It is single-threaded per ``client_order_id``
— the runner's connector instance owns one shadow book and serialises
all on_fill calls.

Folder convention: ``shadow_book_p7exec_072/`` per the MAP-P7 Live
Trading Infrastructure project rule (suffix ``_p7exec_NNN``, never
``_v1`` / ``_v2``).

Public surface
--------------
* :class:`ShadowFillEvent` — input fill event from the connector.
* :class:`ShadowOrderState` — running per-coid shadow view.
* :class:`ShadowPositionState` — per-``(symbol, side)`` net position
  projection.
* :class:`LiveOrderReport` — venue-truth per-coid snapshot.
* :class:`ReconciliationRow` — output diff per coid.
* :class:`ShadowBook` — orchestrator (in-memory cache + journal +
  reconciler).
* :class:`ShadowBookJournal` — sqlite WAL wrapper.
* :func:`reconcile` — pure diff function (cold path).
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :data:`TERMINAL_STATUSES` — frozenset of accepted terminal values.
* :data:`LATE_FILL_LIQUIDITY` — sentinel liquidity tag for late events.
* :exc:`LateShadowFillRejected` — raised on fills after terminal
  status; the event is journaled with ``LATE_FILL_LIQUIDITY`` before
  raising.
* :exc:`UnknownLiveReport` — raised when a ``LiveOrderReport`` has an
  unknown ``terminal_status``.

See :mod:`shadow_book_p7exec_072.shadow_book` for the implementation,
``README.md`` for usage, ``INTERFACE.md`` for the wire contract, and
``SPEC.md`` for the extended design doc.
"""
from .shadow_book import (
    LATE_FILL_LIQUIDITY,
    TERMINAL_STATUSES,
    LateShadowFillRejected,
    LiveOrderReport,
    ReconciliationRow,
    SCHEMA_SQL,
    ShadowBook,
    ShadowBookJournal,
    ShadowFillEvent,
    ShadowOrderState,
    ShadowPositionState,
    UnknownLiveReport,
    bootstrap_journal,
    reconcile,
)

__all__ = [
    "ShadowFillEvent",
    "ShadowOrderState",
    "ShadowPositionState",
    "LiveOrderReport",
    "ReconciliationRow",
    "ShadowBook",
    "ShadowBookJournal",
    "LateShadowFillRejected",
    "UnknownLiveReport",
    "LATE_FILL_LIQUIDITY",
    "TERMINAL_STATUSES",
    "bootstrap_journal",
    "reconcile",
    "SCHEMA_SQL",
]

__version__ = "0.1.0"
__issue__ = "SMA-36259"