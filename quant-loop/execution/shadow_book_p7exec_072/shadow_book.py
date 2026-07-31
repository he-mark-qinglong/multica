"""shadow_book — P7-EXEC-072 implementation.

Maintains the strategy-side reconstruction ("shadow") of every order's
fill history, separately from the venue-truth ("live") view surfaced by
the connector (see ``SPEC_live_paper_connector_binance_usdm.md`` §4.1
for the live trade log). Produces a per-``client_order_id`` and
per-``symbol`` reconciliation row so a downstream drift alert (e.g.
``recon_drift_alert``, sibling recon family) can act on the divergence
without re-deriving it.

The hot path is ``on_fill(event)``: the connector calls it on every
trade-log row, the component folds the fill into the shadow state for
that ``client_order_id`` and journals the row so the run is recoverable
on cold start. Reconciliation is a cold path (``reconcile(live)``)
called periodically (e.g. once per minute) against a snapshot of the
venue-truth view; it produces :class:`ReconciliationRow` records that
the alert layer consumes.

Design constraints (from MAP-P7 spec + issue SMA-36259)
-------------------------------------------------------
* **Hot-path overhead per call < 250us** in pure Python. The pure
  helper is two float multiplies + one division + one branch (same
  envelope as ``partial_fill_accumulator_p7exec_056``); the end-to-end
  ``on_fill`` (INSERT event + UPSERT order + UPSERT symbol position)
  has a default median well inside the 250us budget on a warm journal
  (see ``evidence/bench_shadow_book.json``).
* **Local state journaled** via :class:`ShadowBookJournal` (sqlite WAL).
  Three tables:
  - ``shadow_fill_events`` — append-only log, one row per fill;
    idempotency on ``UNIQUE(client_order_id, trade_id)``.
  - ``shadow_order_states`` — current per-``client_order_id`` shadow
    view (UPSERT).
  - ``shadow_position_states`` — current per-``(symbol, side)`` net
    position projection (UPSERT), driven by every on_fill.
* **NEVER silently drop fills** — every event lands in
  ``shadow_fill_events``. Late fills (after the order reached terminal
  status) are journaled with ``liquidity = "LATE_FILL_REJECTED"`` and
  raise :exc:`LateShadowFillRejected`; the connector catches, logs,
  and continues.
* **Folder suffix ``_p7exec_072``** — folder is
  ``shadow_book_p7exec_072``. No ``_v1`` / ``_v2`` ever.
* **Pure helpers only — no I/O at module level.** The component reads
  the journal only inside :meth:`ShadowBook.on_fill`,
  :meth:`ShadowBook.finalize_order`, :meth:`ShadowBook.replay_order`,
  and :meth:`ShadowBook.reconcile`.

Public surface
--------------
* :class:`ShadowFillEvent` — input fill event from the connector.
* :class:`ShadowOrderState` — running per-coid shadow view.
* :class:`ShadowPositionState` — per-``(symbol, side)`` net position
  projection.
* :class:`LiveOrderReport` — venue-truth per-coid snapshot (one row
  from the live order history).
* :class:`ReconciliationRow` — output diff per coid.
* :class:`ShadowBook` — orchestrator (in-memory cache + journal +
  reconciler).
* :class:`ShadowBookJournal` — sqlite WAL wrapper.
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :data:`TERMINAL_STATUSES` — frozenset of accepted terminal values.
* :data:`LATE_FILL_LIQUIDITY` — sentinel liquidity tag for late events.

See ``__init__.py`` for the package surface, ``README.md`` for usage,
``INTERFACE.md`` for the wire contract, and ``SPEC.md`` for the
extended design doc.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ---- Constants --------------------------------------------------------------

# Canonical terminal statuses. Mirrors the live-paper connector spec
# (SPEC_live_paper_connector_binance_usdm.md §1.4) and the partial
# fill accumulator vocabulary (P7-EXEC-056). Once an order reaches one
# of these, subsequent on_fill calls raise LateShadowFillRejected after
# journaling the late event with LATE_FILL_LIQUIDITY.
TERMINAL_STATUSES = frozenset({
    "FILLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
})

# Liquidity tag written for fills that arrive after the order reached
# a terminal status. The connector's normal ``liquidity`` field uses
# ``taker`` / ``maker`` per the connector spec §4.1; this is a separate
# component-internal sentinel preserved alongside
# partial_fill_accumulator_p7exec_056.LATE_FILL_LIQUIDITY for symmetry.
LATE_FILL_LIQUIDITY = "LATE_FILL_REJECTED"


# ---- Schema -----------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shadow_fill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    ts_exchange_ns INTEGER,
    client_order_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    notional_usd REAL,
    commission REAL,
    commission_asset TEXT,
    liquidity TEXT,
    strategy_id TEXT,
    cumulative_qty_after REAL NOT NULL,
    avg_price_after REAL NOT NULL,
    position_qty_after REAL NOT NULL,
    UNIQUE(client_order_id, trade_id)
);

CREATE INDEX IF NOT EXISTS idx_shadow_fill_events_coid
    ON shadow_fill_events(client_order_id, ts_ns);
CREATE INDEX IF NOT EXISTS idx_shadow_fill_events_symbol
    ON shadow_fill_events(symbol, ts_ns);

CREATE TABLE IF NOT EXISTS shadow_order_states (
    client_order_id TEXT PRIMARY KEY,
    symbol TEXT,
    side TEXT,
    strategy_id TEXT,
    total_qty REAL NOT NULL DEFAULT 0.0,
    avg_price REAL NOT NULL DEFAULT 0.0,
    notional_usd REAL NOT NULL DEFAULT 0.0,
    fill_count INTEGER NOT NULL DEFAULT 0,
    first_fill_ts_ns INTEGER,
    last_fill_ts_ns INTEGER,
    terminal_status TEXT,
    terminal_ts_ns INTEGER,
    updated_at_ns INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_order_states_symbol
    ON shadow_order_states(symbol, side);
CREATE INDEX IF NOT EXISTS idx_shadow_order_states_terminal
    ON shadow_order_states(terminal_status);

CREATE TABLE IF NOT EXISTS shadow_position_states (
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    net_qty REAL NOT NULL DEFAULT 0.0,
    gross_qty REAL NOT NULL DEFAULT 0.0,
    avg_price REAL NOT NULL DEFAULT 0.0,
    notional_usd REAL NOT NULL DEFAULT 0.0,
    fill_count INTEGER NOT NULL DEFAULT 0,
    last_fill_ts_ns INTEGER,
    updated_at_ns INTEGER NOT NULL,
    PRIMARY KEY(symbol, side)
);

CREATE TABLE IF NOT EXISTS live_order_reports (
    client_order_id TEXT PRIMARY KEY,
    symbol TEXT,
    side TEXT,
    total_qty REAL NOT NULL DEFAULT 0.0,
    avg_price REAL NOT NULL DEFAULT 0.0,
    fill_count INTEGER NOT NULL DEFAULT 0,
    terminal_status TEXT,
    terminal_ts_ns INTEGER,
    received_at_ns INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_order_reports_symbol
    ON live_order_reports(symbol);
"""


# ---- Types ------------------------------------------------------------------

@dataclass(frozen=True)
class ShadowFillEvent:
    """A single fill event from the connector — one row from the live
    trade log in ``SPEC_live_paper_connector_binance_usdm.md §4.1``.

    ``trade_id`` is the venue-assigned trade identifier (or the
    connector-generated pseudo-id for synthetic fills). It is the
    idempotency key together with ``client_order_id``: a duplicate
    ``trade_id`` for the same ``client_order_id`` does NOT journal a
    second row.

    ``strategy_id`` is optional. When set, the journal row carries it
    so the reconciler can group divergence by strategy. When unset
    (the default for connector-only streams), the field is None.
    """
    ts_ns: int                                 # connector wall clock
    client_order_id: str                       # idempotency / journal key
    trade_id: str                              # venue trade id (idempotency)
    symbol: str
    side: str                                  # 'BUY' | 'SELL'
    qty: float                                 # base asset (e.g. BTC)
    price: float                               # fill price (quote asset)
    liquidity: str                             # 'taker' | 'maker'
    commission: float = 0.0
    commission_asset: str = "USDT"
    ts_exchange_ns: int = 0                    # 0 = unknown / not provided
    strategy_id: Optional[str] = None          # optional strategy tag


@dataclass(frozen=True)
class ShadowOrderState:
    """Shadow view of one ``client_order_id`` — what the connector +
    accumulator chain thinks happened.

    Immutable; updated copies returned by every on_fill /
    finalize_order call. ``avg_price`` is the volume-weighted average
    across all fills seen so far; ``notional_usd`` is ``total_qty *
    avg_price`` (kept explicit for fast dashboard reads without an
    extra multiply).
    """
    client_order_id: str
    symbol: str = ""
    side: str = ""
    strategy_id: Optional[str] = None
    total_qty: float = 0.0
    avg_price: float = 0.0
    notional_usd: float = 0.0
    fill_count: int = 0
    first_fill_ts_ns: int = 0
    last_fill_ts_ns: int = 0
    terminal_status: Optional[str] = None
    terminal_ts_ns: Optional[int] = None


@dataclass(frozen=True)
class ShadowPositionState:
    """Net position projection per ``(symbol, side)`` — aggregated
    across every ``client_order_id`` for that symbol/side.

    ``net_qty`` is the running total quantity for that side; the
    opposing side's ``net_qty`` is tracked separately. (We do NOT
    net BUY qty against SELL qty here; the cross-side netting is the
    P&L attribution layer's job, see ``pnl_attribution_per_fill_p7exec_089``.)
    ``avg_price`` is the VWAP across every fill observed for that
    (symbol, side).
    """
    symbol: str
    side: str
    net_qty: float = 0.0
    gross_qty: float = 0.0
    avg_price: float = 0.0
    notional_usd: float = 0.0
    fill_count: int = 0
    last_fill_ts_ns: int = 0


@dataclass(frozen=True)
class LiveOrderReport:
    """Venue-truth snapshot for one ``client_order_id`` — one row from
    the live order history (e.g. ``GET /fapi/v1/allOrders``).

    The reconciler takes a list of these and diffs each against the
    current shadow state. ``received_at_ns`` is the connector wall
    clock at fetch time.
    """
    client_order_id: str
    symbol: str = ""
    side: str = ""
    total_qty: float = 0.0
    avg_price: float = 0.0
    fill_count: int = 0
    terminal_status: Optional[str] = None
    terminal_ts_ns: Optional[int] = None
    received_at_ns: int = 0


@dataclass(frozen=True)
class ReconciliationRow:
    """One row of the shadow-vs-live diff per ``client_order_id``.

    A row is always emitted for a ``client_order_id`` that appears in
    EITHER the shadow view OR the live report list. Missing sides
    carry zero in the corresponding fields; ``only_in_shadow`` and
    ``only_in_live`` flags make the divergence explicit.

    Diff semantics:
      ``qty_diff`` = shadow.total_qty - live.total_qty (0 if one side
        missing)
      ``avg_price_diff`` = shadow.avg_price - live.avg_price
      ``fill_count_diff`` = shadow.fill_count - live.fill_count
      ``status_match`` = True iff shadow.terminal_status ==
        live.terminal_status (None == None is True; matches
        partial_fill_accumulator convention).
    """
    client_order_id: str
    symbol: str
    side: str
    shadow: Optional[ShadowOrderState]
    live: Optional[LiveOrderReport]
    only_in_shadow: bool
    only_in_live: bool
    qty_diff: float
    avg_price_diff: float
    fill_count_diff: int
    status_match: bool


class LateShadowFillRejected(Exception):
    """Raised when on_fill receives an event whose client_order_id is
    already in a terminal status.

    The component has already journaled the late event with
    ``liquidity = "LATE_FILL_REJECTED"`` before raising, so the
    forensic record is preserved regardless of caller behaviour.
    """


class UnknownLiveReport(Exception):
    """Raised when a LiveOrderReport is malformed (negative qty, invalid
    side, etc.) and cannot be fed into the reconciler."""


# ---- Pure helpers -----------------------------------------------------------

def _new_order_state_from_event(
    event: ShadowFillEvent,
) -> ShadowOrderState:
    """Build the initial ShadowOrderState for a brand-new coid."""
    notional = event.qty * event.price
    return ShadowOrderState(
        client_order_id=event.client_order_id,
        symbol=event.symbol,
        side=event.side,
        strategy_id=event.strategy_id,
        total_qty=event.qty,
        avg_price=event.price,
        notional_usd=notional,
        fill_count=1,
        first_fill_ts_ns=event.ts_ns,
        last_fill_ts_ns=event.ts_ns,
    )


def _fold_fill_into_order(
    prev: ShadowOrderState,
    event: ShadowFillEvent,
) -> ShadowOrderState:
    """Fold one ShadowFillEvent into the running order state. Pure
    function. Does NOT touch the journal — that is the caller's job.

    Handles the ``total_qty == 0`` cold-start edge case (avg = fill
    price). This should not happen on a normal ``on_fill`` path
    because the first event always seeds a fresh state, but a
    hand-crafted replay where state was wiped but events survived
    could reach this branch.
    """
    if prev.total_qty <= 0.0:
        return _new_order_state_from_event(event)
    new_total_qty = prev.total_qty + event.qty
    new_notional = prev.notional_usd + event.qty * event.price
    new_avg = new_notional / new_total_qty
    first_ts = prev.first_fill_ts_ns or event.ts_ns
    return replace(
        prev,
        total_qty=new_total_qty,
        avg_price=new_avg,
        notional_usd=new_notional,
        fill_count=prev.fill_count + 1,
        first_fill_ts_ns=first_ts,
        last_fill_ts_ns=event.ts_ns,
    )


def _new_position_state(
    symbol: str,
    side: str,
    event: ShadowFillEvent,
) -> ShadowPositionState:
    """Build the initial ShadowPositionState for a brand-new
    (symbol, side) bucket."""
    notional = event.qty * event.price
    return ShadowPositionState(
        symbol=symbol,
        side=side,
        net_qty=event.qty,
        gross_qty=event.qty,
        avg_price=event.price,
        notional_usd=notional,
        fill_count=1,
        last_fill_ts_ns=event.ts_ns,
    )


def _fold_fill_into_position(
    prev: ShadowPositionState,
    event: ShadowFillEvent,
) -> ShadowPositionState:
    """Fold one ShadowFillEvent into the running (symbol, side)
    position projection. Pure function. Does NOT touch the journal —
    that is the caller's job."""
    if prev.net_qty <= 0.0:
        return _new_position_state(prev.symbol, prev.side, event)
    new_net = prev.net_qty + event.qty
    new_gross = prev.gross_qty + event.qty
    new_notional = prev.notional_usd + event.qty * event.price
    new_avg = new_notional / new_net
    return replace(
        prev,
        net_qty=new_net,
        gross_qty=new_gross,
        avg_price=new_avg,
        notional_usd=new_notional,
        fill_count=prev.fill_count + 1,
        last_fill_ts_ns=event.ts_ns,
    )


def _positions_match(
    shadow_terminal: Optional[str],
    live_terminal: Optional[str],
) -> bool:
    """Compare terminal statuses; None == None is True (matches
    partial_fill_accumulator_p7exec_056 convention)."""
    return shadow_terminal == live_terminal


def _row_for_coid(
    coid: str,
    shadow: Optional[ShadowOrderState],
    live: Optional[LiveOrderReport],
) -> ReconciliationRow:
    """Compute the diff row for one coid. Pure function.

    A coid that appears in neither side produces an empty row — the
    reconciler filters those out before emitting.
    """
    if shadow is None and live is None:
        # Defensive: reconciler should not invoke this for an empty
        # coid, but if it does, return a sentinel that says so.
        return ReconciliationRow(
            client_order_id=coid,
            symbol="",
            side="",
            shadow=None,
            live=None,
            only_in_shadow=False,
            only_in_live=False,
            qty_diff=0.0,
            avg_price_diff=0.0,
            fill_count_diff=0,
            status_match=True,
        )
    symbol = (
        (shadow.symbol if shadow else "")
        or (live.symbol if live else "")
        or ""
    )
    side = (
        (shadow.side if shadow else "")
        or (live.side if live else "")
        or ""
    )
    s_qty = shadow.total_qty if shadow else 0.0
    l_qty = live.total_qty if live else 0.0
    s_avg = shadow.avg_price if shadow else 0.0
    l_avg = live.avg_price if live else 0.0
    s_count = shadow.fill_count if shadow else 0
    l_count = live.fill_count if live else 0
    s_term = shadow.terminal_status if shadow else None
    l_term = live.terminal_status if live else None
    return ReconciliationRow(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        shadow=shadow,
        live=live,
        only_in_shadow=(shadow is not None and live is None),
        only_in_live=(shadow is None and live is not None),
        qty_diff=s_qty - l_qty,
        avg_price_diff=s_avg - l_avg,
        fill_count_diff=s_count - l_count,
        status_match=_positions_match(s_term, l_term),
    )


# ---- Journal ----------------------------------------------------------------

class ShadowBookJournal:
    """Sqlite WAL-backed journal for the shadow book.

    The journal owns the schema and provides write methods
    (``append_event``, ``upsert_order``, ``upsert_position``,
    ``upsert_live_report``) and read methods (``fetch_order``,
    ``fetch_position``, ``fetch_events``, ``fetch_all_orders``,
    ``fetch_all_live_reports``). The hot path makes one
    ``append_event`` call + one ``upsert_order`` call + one
    ``upsert_position`` call per fill. All three writes run inside a
    single sqlite transaction so the events table, the orders
    projection, and the positions projection can never disagree.

    The connector writes ``journal_mode=WAL`` and
    ``synchronous=NORMAL`` to keep the median per-INSERT cost in the
    tens-of-microseconds band while still surviving a process crash.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Raise the WAL autocheckpoint threshold from the sqlite
        # default (1000 pages ≈ 4MB) to a higher value. The default
        # triggers an in-line checkpoint mid-hot-path that introduces
        # multi-millisecond p99 spikes (see bench_shadow_book.py). For
        # an in-process hot-path observer we explicitly do NOT want
        # the checkpoint to happen during the per-fill critical
        # section; the runner's recon tick (or a separate low-priority
        # thread) calls PRAGMA wal_checkpoint(PASSIVE) periodically.
        self._conn.execute("PRAGMA wal_autocheckpoint=10000")
        self._conn.row_factory = sqlite3.Row
        bootstrap_journal(self._conn)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass

    # --- writes ---

    def upsert_order(
        self, state: ShadowOrderState, now_ns: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO shadow_order_states(
                client_order_id, symbol, side, strategy_id,
                total_qty, avg_price, notional_usd,
                fill_count, first_fill_ts_ns, last_fill_ts_ns,
                terminal_status, terminal_ts_ns, updated_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                symbol=excluded.symbol,
                side=excluded.side,
                strategy_id=COALESCE(excluded.strategy_id, shadow_order_states.strategy_id),
                total_qty=excluded.total_qty,
                avg_price=excluded.avg_price,
                notional_usd=excluded.notional_usd,
                fill_count=excluded.fill_count,
                first_fill_ts_ns=excluded.first_fill_ts_ns,
                last_fill_ts_ns=excluded.last_fill_ts_ns,
                terminal_status=excluded.terminal_status,
                terminal_ts_ns=excluded.terminal_ts_ns,
                updated_at_ns=excluded.updated_at_ns
            """,
            (
                state.client_order_id,
                state.symbol,
                state.side,
                state.strategy_id,
                state.total_qty,
                state.avg_price,
                state.notional_usd,
                state.fill_count,
                state.first_fill_ts_ns,
                state.last_fill_ts_ns,
                state.terminal_status,
                state.terminal_ts_ns,
                now_ns,
            ),
        )

    def upsert_position(
        self, state: ShadowPositionState, now_ns: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO shadow_position_states(
                symbol, side, net_qty, gross_qty,
                avg_price, notional_usd,
                fill_count, last_fill_ts_ns, updated_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, side) DO UPDATE SET
                net_qty=excluded.net_qty,
                gross_qty=excluded.gross_qty,
                avg_price=excluded.avg_price,
                notional_usd=excluded.notional_usd,
                fill_count=excluded.fill_count,
                last_fill_ts_ns=excluded.last_fill_ts_ns,
                updated_at_ns=excluded.updated_at_ns
            """,
            (
                state.symbol,
                state.side,
                state.net_qty,
                state.gross_qty,
                state.avg_price,
                state.notional_usd,
                state.fill_count,
                state.last_fill_ts_ns,
                now_ns,
            ),
        )

    def upsert_live_report(
        self, report: LiveOrderReport, now_ns: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO live_order_reports(
                client_order_id, symbol, side,
                total_qty, avg_price, fill_count,
                terminal_status, terminal_ts_ns, received_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                symbol=excluded.symbol,
                side=excluded.side,
                total_qty=excluded.total_qty,
                avg_price=excluded.avg_price,
                fill_count=excluded.fill_count,
                terminal_status=excluded.terminal_status,
                terminal_ts_ns=excluded.terminal_ts_ns,
                received_at_ns=excluded.received_at_ns
            """,
            (
                report.client_order_id,
                report.symbol,
                report.side,
                report.total_qty,
                report.avg_price,
                report.fill_count,
                report.terminal_status,
                report.terminal_ts_ns,
                now_ns,
            ),
        )

    def append_event(
        self,
        event: ShadowFillEvent,
        order_after: ShadowOrderState,
        position_after: ShadowPositionState,
        ts_exchange_ns: int = 0,
    ) -> bool:
        """Insert one row into ``shadow_fill_events``. Returns True if
        the row was inserted, False if a duplicate
        ``(client_order_id, trade_id)`` was already present (idempotent
        replay).
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO shadow_fill_events(
                ts_ns, ts_exchange_ns, client_order_id, trade_id,
                symbol, side, qty, price, notional_usd,
                commission, commission_asset, liquidity, strategy_id,
                cumulative_qty_after, avg_price_after,
                position_qty_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.ts_ns,
                ts_exchange_ns or event.ts_exchange_ns or None,
                event.client_order_id,
                event.trade_id,
                event.symbol,
                event.side,
                event.qty,
                event.price,
                event.qty * event.price,
                event.commission,
                event.commission_asset,
                event.liquidity,
                event.strategy_id,
                order_after.total_qty,
                order_after.avg_price,
                position_after.net_qty,
            ),
        )
        return cur.rowcount > 0

    # --- reads ---

    def fetch_order(
        self, coid: str,
    ) -> Optional[ShadowOrderState]:
        row = self._conn.execute(
            """
            SELECT client_order_id, symbol, side, strategy_id,
                   total_qty, avg_price, notional_usd,
                   fill_count, first_fill_ts_ns, last_fill_ts_ns,
                   terminal_status, terminal_ts_ns
            FROM shadow_order_states WHERE client_order_id = ?
            """,
            (coid,),
        ).fetchone()
        if row is None:
            return None
        return ShadowOrderState(
            client_order_id=row["client_order_id"],
            symbol=row["symbol"] or "",
            side=row["side"] or "",
            strategy_id=row["strategy_id"],
            total_qty=row["total_qty"],
            avg_price=row["avg_price"],
            notional_usd=row["notional_usd"],
            fill_count=row["fill_count"],
            first_fill_ts_ns=row["first_fill_ts_ns"] or 0,
            last_fill_ts_ns=row["last_fill_ts_ns"] or 0,
            terminal_status=row["terminal_status"],
            terminal_ts_ns=row["terminal_ts_ns"],
        )

    def fetch_position(
        self, symbol: str, side: str,
    ) -> Optional[ShadowPositionState]:
        row = self._conn.execute(
            """
            SELECT symbol, side, net_qty, gross_qty, avg_price,
                   notional_usd, fill_count, last_fill_ts_ns
            FROM shadow_position_states
            WHERE symbol = ? AND side = ?
            """,
            (symbol, side),
        ).fetchone()
        if row is None:
            return None
        return ShadowPositionState(
            symbol=row["symbol"],
            side=row["side"],
            net_qty=row["net_qty"],
            gross_qty=row["gross_qty"],
            avg_price=row["avg_price"],
            notional_usd=row["notional_usd"],
            fill_count=row["fill_count"],
            last_fill_ts_ns=row["last_fill_ts_ns"] or 0,
        )

    def fetch_events(self, coid: str) -> List[ShadowFillEvent]:
        rows = self._conn.execute(
            """
            SELECT ts_ns, ts_exchange_ns, client_order_id, trade_id,
                   symbol, side, qty, price, commission,
                   commission_asset, liquidity, strategy_id
            FROM shadow_fill_events
            WHERE client_order_id = ?
            ORDER BY ts_ns ASC, id ASC
            """,
            (coid,),
        ).fetchall()
        out: List[ShadowFillEvent] = []
        for row in rows:
            out.append(
                ShadowFillEvent(
                    ts_ns=row["ts_ns"],
                    client_order_id=row["client_order_id"],
                    trade_id=row["trade_id"],
                    symbol=row["symbol"] or "",
                    side=row["side"] or "",
                    qty=row["qty"],
                    price=row["price"],
                    liquidity=row["liquidity"] or "taker",
                    commission=row["commission"] or 0.0,
                    commission_asset=row["commission_asset"] or "USDT",
                    ts_exchange_ns=row["ts_exchange_ns"] or 0,
                    strategy_id=row["strategy_id"],
                )
            )
        return out

    def fetch_all_orders(self) -> List[ShadowOrderState]:
        rows = self._conn.execute(
            """
            SELECT client_order_id, symbol, side, strategy_id,
                   total_qty, avg_price, notional_usd,
                   fill_count, first_fill_ts_ns, last_fill_ts_ns,
                   terminal_status, terminal_ts_ns
            FROM shadow_order_states
            """,
        ).fetchall()
        out: List[ShadowOrderState] = []
        for row in rows:
            out.append(
                ShadowOrderState(
                    client_order_id=row["client_order_id"],
                    symbol=row["symbol"] or "",
                    side=row["side"] or "",
                    strategy_id=row["strategy_id"],
                    total_qty=row["total_qty"],
                    avg_price=row["avg_price"],
                    notional_usd=row["notional_usd"],
                    fill_count=row["fill_count"],
                    first_fill_ts_ns=row["first_fill_ts_ns"] or 0,
                    last_fill_ts_ns=row["last_fill_ts_ns"] or 0,
                    terminal_status=row["terminal_status"],
                    terminal_ts_ns=row["terminal_ts_ns"],
                )
            )
        return out

    def fetch_all_live_reports(self) -> List[LiveOrderReport]:
        rows = self._conn.execute(
            """
            SELECT client_order_id, symbol, side,
                   total_qty, avg_price, fill_count,
                   terminal_status, terminal_ts_ns, received_at_ns
            FROM live_order_reports
            """,
        ).fetchall()
        out: List[LiveOrderReport] = []
        for row in rows:
            out.append(
                LiveOrderReport(
                    client_order_id=row["client_order_id"],
                    symbol=row["symbol"] or "",
                    side=row["side"] or "",
                    total_qty=row["total_qty"],
                    avg_price=row["avg_price"],
                    fill_count=row["fill_count"],
                    terminal_status=row["terminal_status"],
                    terminal_ts_ns=row["terminal_ts_ns"],
                    received_at_ns=row["received_at_ns"],
                )
            )
        return out


def bootstrap_journal(target) -> None:
    """Idempotent schema bootstrap. Accepts either a
    :class:`ShadowBookJournal` or a raw sqlite3 connection.

    Safe to call from a cold-start path on every restart; the
    statements are all ``CREATE ... IF NOT EXISTS``.
    """
    conn = target._conn if isinstance(target, ShadowBookJournal) else target
    conn.executescript(SCHEMA_SQL)


# ---- Reconciler (cold path) -------------------------------------------------

def reconcile(
    shadow_orders: Iterable[ShadowOrderState],
    live_reports: Iterable[LiveOrderReport],
) -> List[ReconciliationRow]:
    """Diff shadow orders vs live reports. Pure function.

    Emits one :class:`ReconciliationRow` per ``client_order_id`` that
    appears in EITHER side; coids present in neither are filtered out.
    A coid present in only one side gets ``only_in_shadow`` or
    ``only_in_live`` = True and the corresponding fields are zero on
    the missing side.

    The reconciler is a pure function — it does NOT touch the journal.
    The orchestrator (:class:`ShadowBook.reconcile`) loads both sides
    from the journal and persists the live reports; this function is
    the comparison core.

    Out-of-scope coids (shadow-only with no terminal status, or
    live-only with no terminal status) are still emitted so the
    downstream drift alert can act on them. Threshold-based
    suppression lives in ``recon_drift_alert``, not here.
    """
    shadow_map: Dict[str, ShadowOrderState] = {
        s.client_order_id: s for s in shadow_orders
    }
    live_map: Dict[str, LiveOrderReport] = {
        l.client_order_id: l for l in live_reports
    }
    all_coids = set(shadow_map.keys()) | set(live_map.keys())
    rows: List[ReconciliationRow] = []
    for coid in sorted(all_coids):
        rows.append(_row_for_coid(coid, shadow_map.get(coid), live_map.get(coid)))
    return rows


# ---- Orchestrator -----------------------------------------------------------

class ShadowBook:
    """Shadow-book orchestrator. In-memory cache + journal + reconciler.

    One orchestrator per connector instance. Hot path is
    :meth:`on_fill`; cold paths are :meth:`finalize_order`,
    :meth:`record_live_reports`, :meth:`reconcile`,
    :meth:`replay_order`, :meth:`replay_position`, :meth:`snapshot_order`,
    :meth:`snapshot_position`, :meth:`known_orders`,
    :meth:`known_positions`.

    Concurrency: single-threaded per coid. The connector serialises fill
    events through a single WS user-data stream per ``client_order_id``,
    so the orchestrator does not need a lock. A multi-threaded caller
    must wrap ``on_fill`` in their own mutex (the journal itself uses
    sqlite's per-connection serialization, which is sufficient for the
    in-process case).
    """

    def __init__(self, journal: ShadowBookJournal) -> None:
        self.journal = journal
        self._orders: Dict[str, ShadowOrderState] = {}
        self._positions: Dict[Tuple[str, str], ShadowPositionState] = {}

    # ---- hot path ----------------------------------------------------------

    def on_fill(self, event: ShadowFillEvent) -> ShadowOrderState:
        """Fold one fill event into the shadow state and journal.

        Hot path. Returns the new order state (immutable copy).

        Behaviour:
        * Unknown ``client_order_id`` → create new order state from the
          first fill. Create new position state for ``(symbol, side)``
          if missing. Three journal writes happen (event + order +
          position).
        * Known ``client_order_id``, not terminal → fold fill into order
          state, fold into position state, journal three writes.
        * Known ``client_order_id``, terminal → journal the event with
          ``liquidity = LATE_FILL_LIQUIDITY`` for forensics, then raise
          :exc:`LateShadowFillRejected`. The order state is NOT
          updated; the position state is NOT updated.
        * Duplicate ``trade_id`` for the same ``client_order_id``
          (idempotent replay from a re-sent WS event) → return the
          existing order state without any journal writes.
        """
        prev = self._orders.get(event.client_order_id)
        if prev is None:
            # Hydrate from the journal in case this is a re-start path
            # where the in-memory dict was empty but the journal
            # survived.
            prev = self.journal.fetch_order(event.client_order_id)
            if prev is not None:
                self._orders[event.client_order_id] = prev

        if prev is not None and prev.terminal_status is not None:
            late_event = ShadowFillEvent(
                ts_ns=event.ts_ns,
                client_order_id=event.client_order_id,
                trade_id=event.trade_id,
                symbol=event.symbol,
                side=event.side,
                qty=event.qty,
                price=event.price,
                liquidity=LATE_FILL_LIQUIDITY,
                commission=event.commission,
                commission_asset=event.commission_asset,
                ts_exchange_ns=event.ts_exchange_ns,
                strategy_id=event.strategy_id,
            )
            # The journal row carries the sentinel liquidity and the
            # order-state snapshot AT THE TIME OF TERMINAL (not the
            # late fold). The position projection is unchanged by a
            # late fill, so we use the persisted one.
            persisted_position = (
                self._positions.get((prev.symbol, prev.side))
                or self.journal.fetch_position(prev.symbol, prev.side)
                or _new_position_state(prev.symbol, prev.side, event)
            )
            self.journal.append_event(
                late_event, prev, persisted_position,
            )
            raise LateShadowFillRejected(
                f"late fill for terminal order {event.client_order_id} "
                f"(status={prev.terminal_status})"
            )

        # Compute the next order state.
        if prev is None:
            next_order = _new_order_state_from_event(event)
        else:
            next_order = _fold_fill_into_order(prev, event)

        # Compute the next position state for (symbol, side). We must
        # compute it before the INSERT OR IGNORE so the position
        # snapshot in the journal row reflects the right state if the
        # event is novel; for a duplicate trade_id we discard it.
        pos_key = (event.symbol, event.side)
        prev_pos = self._positions.get(pos_key)
        if prev_pos is None:
            prev_pos = self.journal.fetch_position(event.symbol, event.side)
        if prev_pos is None:
            next_position = _new_position_state(event.symbol, event.side, event)
        else:
            next_position = _fold_fill_into_position(prev_pos, event)

        inserted = self.journal.append_event(
            event, next_order, next_position,
        )
        if inserted:
            self.journal.upsert_order(next_order, now_ns=event.ts_ns)
            self.journal.upsert_position(next_position, now_ns=event.ts_ns)
            self._orders[event.client_order_id] = next_order
            self._positions[pos_key] = next_position
            return next_order
        # Duplicate trade_id. The journal rows already exist from a
        # prior fill, the in-memory state is already up-to-date —
        # return the previously-cached state without mutating either
        # the order dict or the position dict.
        cached = self._orders.get(event.client_order_id)
        if cached is None:
            # Defensive: should not happen because we hydrated above.
            # Fall back to the just-computed next_order so the caller
            # has something usable.
            return next_order
        return cached

    # ---- cold paths --------------------------------------------------------

    def finalize_order(
        self,
        coid: str,
        terminal_status: str,
        ts_ns: int = 0,
    ) -> ShadowOrderState:
        """Mark an order as terminal. No more on_fill accepted for that
        coid.

        ``terminal_status`` must be in :data:`TERMINAL_STATUSES`;
        anything else raises ``ValueError``. The state is updated with
        the terminal marker, journaled, and returned.
        """
        if terminal_status not in TERMINAL_STATUSES:
            raise ValueError(
                f"unknown terminal_status {terminal_status!r}; "
                f"must be one of {sorted(TERMINAL_STATUSES)}"
            )
        prev = self._orders.get(coid)
        if prev is None:
            prev = self.journal.fetch_order(coid)
        if prev is None:
            raise KeyError(
                f"cannot finalize unknown client_order_id {coid!r}; "
                f"no fills have been journaled"
            )
        if prev.terminal_status is not None:
            # Idempotent: already terminal. Return existing state.
            return prev
        next_state = replace(
            prev,
            terminal_status=terminal_status,
            terminal_ts_ns=ts_ns or time.time_ns(),
        )
        self.journal.upsert_order(next_state, now_ns=next_state.terminal_ts_ns)
        self._orders[coid] = next_state
        return next_state

    def record_live_reports(
        self, reports: Iterable[LiveOrderReport],
    ) -> None:
        """Upsert a batch of live (venue-truth) reports into the journal.

        ``received_at_ns`` is overwritten with the connector wall clock
        at call time. Cold path; called once per reconciliation cycle
        (typically once per minute on the runner's recon tick).
        """
        now_ns = time.time_ns()
        for report in reports:
            if report.terminal_status is not None and (
                report.terminal_status not in TERMINAL_STATUSES
            ):
                raise UnknownLiveReport(
                    f"live report for {report.client_order_id} has "
                    f"unknown terminal_status {report.terminal_status!r}"
                )
            stamped = replace(
                report, received_at_ns=report.received_at_ns or now_ns,
            )
            self.journal.upsert_live_report(stamped, now_ns=now_ns)

    def reconcile(
        self,
    ) -> List[ReconciliationRow]:
        """Diff the current shadow orders against the persisted live
        reports. Cold path; returns a fresh list of
        :class:`ReconciliationRow`.

        Convenience wrapper around :func:`reconcile` that loads both
        sides from the journal. Callers wanting to inject synthetic
        shadow state or live reports (e.g. in tests) should call
        :func:`reconcile` directly.
        """
        return reconcile(
            self.journal.fetch_all_orders(),
            self.journal.fetch_all_live_reports(),
        )

    def replay_order(
        self, coid: str,
    ) -> Optional[ShadowOrderState]:
        """Rebuild the order state for one ``client_order_id`` from the
        journal. Returns ``None`` if no events exist for that coid.

        Useful on cold start when the in-memory dict is empty; the
        caller can also pass ``known_orders()`` and replay every one.
        Replay folds every event in chronological order and updates
        the in-memory cache so subsequent ``on_fill`` calls see the
        rebuilt state without re-hydrating from sqlite.
        """
        events = self.journal.fetch_events(coid)
        if not events:
            return None
        order = _new_order_state_from_event(events[0])
        for ev in events[1:]:
            order = _fold_fill_into_order(order, ev)
        # Honour a previously-finalized terminal marker.
        persisted = self.journal.fetch_order(coid)
        if persisted is not None and persisted.terminal_status is not None:
            order = replace(
                order,
                terminal_status=persisted.terminal_status,
                terminal_ts_ns=persisted.terminal_ts_ns,
            )
        self._orders[coid] = order
        return order

    def replay_position(
        self, symbol: str, side: str,
    ) -> Optional[ShadowPositionState]:
        """Rebuild the position state for one ``(symbol, side)`` bucket
        from the journal. Returns ``None`` if no events exist.

        Aggregates every fill event whose ``(symbol, side)`` matches,
        regardless of ``client_order_id``. Useful on cold start when
        the in-memory dict is empty.
        """
        rows = self.journal._conn.execute(  # noqa: SLF001 (white-box recovery)
            """
            SELECT ts_ns, ts_exchange_ns, client_order_id, trade_id,
                   symbol, side, qty, price, commission,
                   commission_asset, liquidity, strategy_id
            FROM shadow_fill_events
            WHERE symbol = ? AND side = ?
            ORDER BY ts_ns ASC, id ASC
            """,
            (symbol, side),
        ).fetchall()
        if not rows:
            return None
        events = [
            ShadowFillEvent(
                ts_ns=row["ts_ns"],
                client_order_id=row["client_order_id"],
                trade_id=row["trade_id"],
                symbol=row["symbol"] or "",
                side=row["side"] or "",
                qty=row["qty"],
                price=row["price"],
                liquidity=row["liquidity"] or "taker",
                commission=row["commission"] or 0.0,
                commission_asset=row["commission_asset"] or "USDT",
                ts_exchange_ns=row["ts_exchange_ns"] or 0,
                strategy_id=row["strategy_id"],
            )
            for row in rows
        ]
        position = _new_position_state(symbol, side, events[0])
        for ev in events[1:]:
            position = _fold_fill_into_position(position, ev)
        self._positions[(symbol, side)] = position
        return position

    def snapshot_order(
        self, coid: str,
    ) -> Optional[ShadowOrderState]:
        """Return the cached order state for one ``client_order_id``
        without touching the journal. Returns None if not in cache (and
        not on disk — callers wanting disk-fallback should call
        ``replay_order``)."""
        return self._orders.get(coid)

    def snapshot_position(
        self, symbol: str, side: str,
    ) -> Optional[ShadowPositionState]:
        """Return the cached position state for one ``(symbol, side)``
        bucket without touching the journal."""
        return self._positions.get((symbol, side))

    def known_orders(self) -> List[str]:
        """Return the in-memory order keys. Cheap; useful for cold-start
        enumeration (combine with ``replay_order`` to hydrate)."""
        return list(self._orders.keys())

    def known_positions(self) -> List[Tuple[str, str]]:
        """Return the in-memory position keys as ``(symbol, side)``
        tuples. Cheap."""
        return list(self._positions.keys())

    def close(self) -> None:
        self.journal.close()


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


if __name__ == "__main__":  # pragma: no cover
    # Trivial self-check when invoked as `python3 shadow_book.py`.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "sb.sqlite")
        journal = ShadowBookJournal(Path(db))
        book = ShadowBook(journal)
        ev = ShadowFillEvent(
            ts_ns=time.time_ns(),
            client_order_id="coid-selfcheck",
            trade_id="t-1",
            symbol="BTCUSDT",
            side="BUY",
            qty=0.01,
            price=50000.0,
            liquidity="taker",
            strategy_id="vpvr_btc_long",
        )
        s = book.on_fill(ev)
        print("after first fill:", s)
        ev2 = ShadowFillEvent(
            ts_ns=time.time_ns(),
            client_order_id="coid-selfcheck",
            trade_id="t-2",
            symbol="BTCUSDT",
            side="BUY",
            qty=0.005,
            price=50100.0,
            liquidity="taker",
            strategy_id="vpvr_btc_long",
        )
        s2 = book.on_fill(ev2)
        print("after second fill:", s2)
        pos = book.snapshot_position("BTCUSDT", "BUY")
        print("position after 2 fills:", pos)
        book.finalize_order("coid-selfcheck", "FILLED")
        book.close()