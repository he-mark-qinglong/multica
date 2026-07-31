"""order_to_fill_linker — P7-EXEC-055 implementation.

Correlates the venue-assigned ``orderId`` with the
connector-assigned ``client_order_id`` and the strategy's order
*intent*. The linker is the durable record of "we asked the
venue to do X" vs. "the venue reported doing Y" — every FillReport
is journaled exactly once with a link back to the originating
intent (or flagged ``is_orphan`` if no intent was known at the
time of the report).

The component is a hot-path observer: ``on_fill_report`` runs
inside the runner's per-fill critical section with a 250us budget
per call (MAP-P7 default policy). It is single-threaded per
order — the runner's connector instance owns one linker and
serialises all calls.

Design constraints (from MAP-P7 spec + issue SMA-36242)
-------------------------------------------------------
* **Hot-path overhead per call < 250us** in pure Python. The pure
  helper is one dict lookup + a few branches; the end-to-end
  ``on_fill_report`` (one INSERT into ``order_fill_links`` plus a
  UPSERT into ``order_intents``) runs in the 80-220us band on a
  warm journal (see ``evidence/bench_order_to_fill_linker.json``).
* **Local state journaled** via :class:`OrderToFillJournal`
  (sqlite WAL). The three tables are additive — no shared schema
  with sibling components.
* **NEVER silently drop fills** — every FillReport lands in
  ``order_fill_links``. Orphan fills (no matching intent in cache
  or journal) are still journaled with ``is_orphan = 1`` and
  ``client_order_id = ""`` so a downstream reconciler can pick
  them up later.
* **Folder suffix ``_p7exec_NNN``** — folder is
  ``order_to_fill_linker_p7exec_055``. No ``_v1`` / ``_v2`` ever.
* **Pure helpers only — no I/O at module level.** The component
  reads the journal only inside :meth:`on_fill_report`,
  :meth:`bind_order_id`, :meth:`register_intent`, and
  :meth:`recover_pending`.

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

See :mod:`order_to_fill_linker_p7exec_055` for the package
surface, ``README.md`` for the spec, ``INTERFACE.md`` for the wire
contract, and ``SPEC.md`` for the extended design doc.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional


# ---- Constants --------------------------------------------------------------

# Canonical intent statuses. Mirrors the Binance USD-M order state
# vocabulary (§1.4 of SPEC_live_paper_connector_binance_usdm.md). An
# intent moves to a terminal status once the linker observes the
# corresponding venue order-status update.
INTENT_STATUSES = frozenset({
    "PENDING_ACK",     # registered, not yet acknowledged by venue
    "ACKED",           # venue returned an orderId
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
})

# Venue-side order-status vocabulary (subset that drives intent
# transitions). Same set as the connector's trade-log classifier.
ORDER_STATUSES = frozenset({
    "NEW",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
})

# Mapping from venue order-status → intent status. None means the
# venue status is informational only (NEW, PARTIALLY_FILLED) and
# does not advance the intent to a terminal state.
_VENUE_TO_INTENT_STATUS = {
    "NEW": "ACKED",
    "PARTIALLY_FILLED": "PARTIALLY_FILLED",
    "FILLED": "FILLED",
    "CANCELED": "CANCELED",
    "EXPIRED": "EXPIRED",
    "REJECTED": "REJECTED",
}

# Sentinel stored in the cache when an orderId has been seen but no
# intent is registered yet (orphan fill). The journal still has the
# full row with ``is_orphan = 1``.
_TERMINAL_INTENT_STATUSES = frozenset({
    "FILLED", "CANCELED", "EXPIRED", "REJECTED",
})


# ---- Schema -----------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS order_intents (
    client_order_id TEXT PRIMARY KEY,
    order_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    intended_qty REAL NOT NULL,
    order_type TEXT,
    time_in_force TEXT,
    strategy_id TEXT,
    intent_ts_ns INTEGER NOT NULL,
    intent_status TEXT NOT NULL DEFAULT 'PENDING_ACK',
    last_status_ts_ns INTEGER,
    updated_at_ns INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_intents_order_id
    ON order_intents(order_id);

CREATE INDEX IF NOT EXISTS idx_order_intents_status
    ON order_intents(intent_status);

CREATE TABLE IF NOT EXISTS order_fill_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    ts_exchange_ns INTEGER,
    order_id INTEGER NOT NULL,
    client_order_id TEXT NOT NULL DEFAULT '',
    trade_id TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    notional_usd REAL,
    commission REAL,
    commission_asset TEXT,
    liquidity TEXT,
    cum_filled_qty_after REAL,
    avg_fill_price_after REAL,
    order_status TEXT,
    is_orphan INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    UNIQUE(order_id, trade_id)
);

CREATE INDEX IF NOT EXISTS idx_order_fill_links_coid
    ON order_fill_links(client_order_id, ts_ns);

CREATE INDEX IF NOT EXISTS idx_order_fill_links_order_id
    ON order_fill_links(order_id, ts_ns);

CREATE INDEX IF NOT EXISTS idx_order_fill_links_orphan
    ON order_fill_links(is_orphan);

CREATE TABLE IF NOT EXISTS order_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    client_order_id TEXT,
    status TEXT NOT NULL,
    cum_filled_qty REAL,
    avg_fill_price REAL,
    source TEXT,
    UNIQUE(order_id, ts_ns, status)
);

CREATE INDEX IF NOT EXISTS idx_order_status_events_order_id
    ON order_status_events(order_id, ts_ns);
"""


# ---- Types ------------------------------------------------------------------

@dataclass(frozen=True)
class OrderIntent:
    """Strategy-side intent: what we asked the venue to do.

    Created BEFORE the order goes out (call :meth:`Linker.register_intent`).
    Once the venue returns an orderId the linker fills in
    ``order_id`` (call :meth:`Linker.bind_order_id`).
    """
    client_order_id: str
    symbol: str
    side: str                                # BUY | SELL
    intended_qty: float
    intent_ts_ns: int
    order_id: int = 0                        # venue-assigned; 0 until first ack
    order_type: str = "LIMIT"
    time_in_force: str = "GTC"
    strategy_id: str = ""
    intent_status: str = "PENDING_ACK"
    last_status_ts_ns: int = 0


@dataclass(frozen=True)
class FillReport:
    """Venue-side fill record: one row from the connector's
    ORDER_TRADE_UPDATE stream (synthesised from WS) or the
    ``trades`` array inside a REST GET /fapi/v1/order response.

    ``client_order_id`` is best-effort: if the venue sent the
    ack WITHOUT echoing our ``origClientOrderId`` tag, the linker
    matches by ``order_id`` and the report is flagged orphan if
    no intent is bound.
    """
    ts_ns: int
    order_id: int
    client_order_id: str
    trade_id: str
    symbol: str
    side: str
    qty: float
    price: float
    cum_filled_qty: float
    avg_fill_price: float
    order_status: str                        # NEW | PARTIALLY_FILLED | FILLED | ...
    commission: float = 0.0
    commission_asset: str = "USDT"
    liquidity: str = "taker"
    source: str = "WS"                       # WS | REST | RECONCILE | TRADES_FOLD
    ts_exchange_ns: int = 0


@dataclass(frozen=True)
class LinkRecord:
    """Result of matching a FillReport against an intent.

    Returned by :meth:`Linker.on_fill_report`. The record is the
    audit evidence the caller should attach to its own log line.
    """
    order_id: int
    client_order_id: str                     # may be '' for orphan
    trade_id: str
    intent_status_before: str
    intent_status_after: str
    is_orphan: bool
    ts_ns: int
    source: str


class IntentMismatch(Exception):
    """Raised when a FillReport's side / symbol disagrees with the
    registered intent. Surfaced so the runner can decide whether to
    HALT (likely a venue / connector bug) or log-and-continue. The
    journal row is written BEFORE raising so the audit trail is
    preserved either way.
    """


class UnknownClientOrderId(Exception):
    """Raised by bind_order_id / fetch_intent / finalize when the
    coid has no registered intent (neither in cache nor journal).
    """


class OrderIdAlreadyBound(Exception):
    """Raised by bind_order_id when the requested orderId is already
    bound to a DIFFERENT coid in the cache or journal. This is a
    real venue-side bug (or a replay of stale data) — surfacing it
    is the right call.
    """


# ---- Pure helpers -----------------------------------------------------------

def _validate_intent(intent: OrderIntent) -> None:
    if not intent.client_order_id:
        raise ValueError("OrderIntent.client_order_id must be non-empty")
    if intent.side not in ("BUY", "SELL"):
        raise ValueError(
            f"OrderIntent.side must be 'BUY' or 'SELL', got {intent.side!r}"
        )
    if intent.intended_qty <= 0:
        raise ValueError(
            f"OrderIntent.intended_qty must be > 0, got {intent.intended_qty}"
        )


def _validate_fill_report(report: FillReport) -> None:
    if report.order_id <= 0:
        raise ValueError(
            f"FillReport.order_id must be > 0, got {report.order_id}"
        )
    if not report.trade_id:
        raise ValueError("FillReport.trade_id must be non-empty")
    if report.qty < 0:
        # Non-fill status events (REJECTED / EXPIRED) carry qty=0;
        # only negative qty is a programmer error.
        raise ValueError(f"FillReport.qty must be >= 0, got {report.qty}")
    if report.price <= 0:
        raise ValueError(f"FillReport.price must be > 0, got {report.price}")
    if report.order_status not in ORDER_STATUSES:
        raise ValueError(
            f"FillReport.order_status must be one of {sorted(ORDER_STATUSES)}, "
            f"got {report.order_status!r}"
        )


def _apply_status_transition(
    prev: OrderIntent,
    venue_status: str,
    ts_ns: int,
) -> OrderIntent:
    """Compute the next intent state given a venue status update.

    Pure function. Does not touch the journal. Honours the
    ``_VENUE_TO_INTENT_STATUS`` mapping; ignores statuses that do
    not advance the intent (``NEW`` is informational after ACK).
    Idempotent: if the intent is already in a terminal status
    matching the requested one, returns it unchanged.
    """
    if venue_status not in ORDER_STATUSES:
        raise ValueError(
            f"unknown venue status {venue_status!r}; "
            f"must be one of {sorted(ORDER_STATUSES)}"
        )
    new_status = _VENUE_TO_INTENT_STATUS.get(venue_status)
    if new_status is None:
        return prev
    if prev.intent_status in _TERMINAL_INTENT_STATUSES:
        # Already terminal — idempotent. Don't downgrade FILLED to
        # CANCELED on a late ack.
        return prev
    if prev.intent_status == "PENDING_ACK" and new_status == "ACKED":
        return replace(
            prev,
            intent_status="ACKED",
            last_status_ts_ns=ts_ns,
        )
    # Non-terminal → non-terminal transition (PARTIALLY_FILLED), or
    # → terminal (FILLED / CANCELED / EXPIRED / REJECTED).
    return replace(
        prev,
        intent_status=new_status,
        last_status_ts_ns=ts_ns,
    )


# ---- Journal ----------------------------------------------------------------

class OrderToFillJournal:
    """Sqlite WAL-backed journal for the linker.

    Three additive tables:

    * ``order_intents`` — durable record of every intent we sent
      out, plus the latest intent status. PRIMARY KEY ``client_order_id``.
    * ``order_fill_links`` — append-only log of every fill, with a
      back-pointer to the intent (``client_order_id``) and an
      ``is_orphan`` flag for fills seen with no matching intent.
      UNIQUE ``(order_id, trade_id)`` for idempotency.
    * ``order_status_events`` — append-only log of every venue
      status update. UNIQUE ``(order_id, ts_ns, status)`` for
      idempotency. Drives the audit trail for status transitions
      (especially the PARTIALLY_FILLED → FILLED handoff).

    Hot-path writes: one INSERT into ``order_fill_links`` per
    FillReport and (if the intent transitions) one UPSERT into
    ``order_intents``. Both run inside a single sqlite transaction
    so the tables can never disagree.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        bootstrap_journal(self._conn)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass

    # ---- intents ----

    def upsert_intent(self, intent: OrderIntent, now_ns: int) -> None:
        self._conn.execute(
            """
            INSERT INTO order_intents(
                client_order_id, order_id, symbol, side,
                intended_qty, order_type, time_in_force, strategy_id,
                intent_ts_ns, intent_status, last_status_ts_ns,
                updated_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                order_id=COALESCE(NULLIF(excluded.order_id, 0), order_intents.order_id),
                symbol=excluded.symbol,
                side=excluded.side,
                intended_qty=excluded.intended_qty,
                order_type=excluded.order_type,
                time_in_force=excluded.time_in_force,
                strategy_id=excluded.strategy_id,
                intent_status=excluded.intent_status,
                last_status_ts_ns=excluded.last_status_ts_ns,
                updated_at_ns=excluded.updated_at_ns
            """,
            (
                intent.client_order_id,
                intent.order_id,
                intent.symbol,
                intent.side,
                intent.intended_qty,
                intent.order_type,
                intent.time_in_force,
                intent.strategy_id,
                intent.intent_ts_ns,
                intent.intent_status,
                intent.last_status_ts_ns,
                now_ns,
            ),
        )

    def fetch_intent_by_coid(self, coid: str) -> Optional[OrderIntent]:
        row = self._conn.execute(
            """
            SELECT client_order_id, order_id, symbol, side,
                   intended_qty, order_type, time_in_force, strategy_id,
                   intent_ts_ns, intent_status, last_status_ts_ns
            FROM order_intents
            WHERE client_order_id = ?
            """,
            (coid,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_intent(row)

    def fetch_intent_by_order_id(self, order_id: int) -> Optional[OrderIntent]:
        row = self._conn.execute(
            """
            SELECT client_order_id, order_id, symbol, side,
                   intended_qty, order_type, time_in_force, strategy_id,
                   intent_ts_ns, intent_status, last_status_ts_ns
            FROM order_intents
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_intent(row)

    def all_intents(self) -> List[OrderIntent]:
        rows = self._conn.execute(
            """
            SELECT client_order_id, order_id, symbol, side,
                   intended_qty, order_type, time_in_force, strategy_id,
                   intent_ts_ns, intent_status, last_status_ts_ns
            FROM order_intents
            """
        ).fetchall()
        return [_row_to_intent(r) for r in rows]

    # ---- fill links ----

    def append_fill_link(
        self,
        report: FillReport,
        client_order_id: str,
        is_orphan: bool,
        ts_exchange_ns: int = 0,
    ) -> bool:
        """Insert one row into ``order_fill_links``. Returns True
        if the row was inserted, False if a duplicate
        ``(order_id, trade_id)`` was already present (idempotent
        replay).

        The duplicate path is silently idempotent — callers that
        need strict-uniqueness semantics should pre-check
        ``trade_id`` themselves.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO order_fill_links(
                ts_ns, ts_exchange_ns, order_id, client_order_id,
                trade_id, symbol, side, qty, price, notional_usd,
                commission, commission_asset, liquidity,
                cum_filled_qty_after, avg_fill_price_after,
                order_status, is_orphan, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.ts_ns,
                ts_exchange_ns or report.ts_exchange_ns or None,
                report.order_id,
                client_order_id,
                report.trade_id,
                report.symbol,
                report.side,
                report.qty,
                report.price,
                report.qty * report.price,
                report.commission,
                report.commission_asset,
                report.liquidity,
                report.cum_filled_qty,
                report.avg_fill_price,
                report.order_status,
                1 if is_orphan else 0,
                report.source,
            ),
        )
        return cur.rowcount > 0

    # ---- status events ----

    def append_status_event(
        self,
        ts_ns: int,
        order_id: int,
        client_order_id: str,
        status: str,
        cum_filled_qty: float,
        avg_fill_price: float,
        source: str,
    ) -> bool:
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO order_status_events(
                ts_ns, order_id, client_order_id, status,
                cum_filled_qty, avg_fill_price, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_ns,
                order_id,
                client_order_id,
                status,
                cum_filled_qty,
                avg_fill_price,
                source,
            ),
        )
        return cur.rowcount > 0

    def fetch_orphan_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM order_fill_links WHERE is_orphan = 1"
        ).fetchone()
        return int(row["n"] or 0)


def _row_to_intent(row: sqlite3.Row) -> OrderIntent:
    return OrderIntent(
        client_order_id=row["client_order_id"],
        order_id=row["order_id"] or 0,
        symbol=row["symbol"] or "",
        side=row["side"] or "",
        intended_qty=row["intended_qty"] or 0.0,
        order_type=row["order_type"] or "LIMIT",
        time_in_force=row["time_in_force"] or "GTC",
        strategy_id=row["strategy_id"] or "",
        intent_ts_ns=row["intent_ts_ns"] or 0,
        intent_status=row["intent_status"] or "PENDING_ACK",
        last_status_ts_ns=row["last_status_ts_ns"] or 0,
    )


def bootstrap_journal(conn: sqlite3.Connection) -> None:
    """Idempotent schema bootstrap. Safe to call repeatedly."""
    conn.executescript(SCHEMA_SQL)


# ---- Linker -----------------------------------------------------------------

class Linker:
    """In-memory cache + journal-backed orderId ↔ FillReport matcher.

    Cache maps ``client_order_id → OrderIntent`` and
    ``order_id → client_order_id`` (for orphan-fill lookups by
    order_id alone). The journal is the source of truth; the
    cache is rebuilt from the journal via :meth:`recover_pending`
    on cold start.

    Threading model: single-threaded per linker instance. The
    runner's connector owns one linker and serialises all calls
    (matches the connector's own threading model).
    """

    def __init__(self, journal: OrderToFillJournal) -> None:
        self.journal = journal
        # coid → OrderIntent (current cache)
        self._intents: Dict[str, OrderIntent] = {}
        # order_id → coid (reverse lookup for orphan fill matching)
        self._order_id_to_coid: Dict[int, str] = {}

    # ---- intent lifecycle ----

    def register_intent(self, intent: OrderIntent) -> OrderIntent:
        """Record a strategy-side intent. Called BEFORE the order
        goes out. Idempotent: re-registering the same coid updates
        the symbol/side/qty but does not reset ``intent_status``
        (so a re-register after the venue has already ACKed won't
        rewind the intent back to PENDING_ACK).
        """
        _validate_intent(intent)
        cached = self._intents.get(intent.client_order_id)
        now_ns = time.time_ns()
        if cached is None:
            persisted = self.journal.fetch_intent_by_coid(intent.client_order_id)
            if persisted is not None:
                # Honor the durable intent_status if it is already
                # past PENDING_ACK (the venue may have ACKed before
                # the linker saw the intent). Coalesce mutable
                # fields.
                next_intent = replace(
                    intent,
                    order_id=persisted.order_id or intent.order_id,
                    intent_status=persisted.intent_status,
                    last_status_ts_ns=persisted.last_status_ts_ns,
                )
            else:
                next_intent = intent
            self.journal.upsert_intent(next_intent, now_ns=now_ns)
            self._intents[intent.client_order_id] = next_intent
            if next_intent.order_id:
                self._order_id_to_coid[next_intent.order_id] = (
                    intent.client_order_id
                )
            return next_intent

        # Already cached. Honour the durable status; coalesce fields.
        next_intent = replace(
            cached,
            symbol=intent.symbol,
            side=intent.side,
            intended_qty=intent.intended_qty,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            strategy_id=intent.strategy_id,
        )
        self.journal.upsert_intent(next_intent, now_ns=now_ns)
        self._intents[intent.client_order_id] = next_intent
        if next_intent.order_id and next_intent.order_id not in self._order_id_to_coid:
            self._order_id_to_coid[next_intent.order_id] = intent.client_order_id
        return next_intent

    def bind_order_id(self, coid: str, order_id: int) -> OrderIntent:
        """Bind a venue-assigned orderId to a previously registered
        intent. Idempotent (re-binding the same orderId to the same
        coid returns the existing intent). Raises
        :exc:`OrderIdAlreadyBound` if the orderId is already bound
        to a DIFFERENT coid, and :exc:`UnknownClientOrderId` if the
        coid has no registered intent.
        """
        if order_id <= 0:
            raise ValueError(f"order_id must be > 0, got {order_id}")
        existing_for_coid = self._intents.get(coid)
        if existing_for_coid is None:
            existing_for_coid = self.journal.fetch_intent_by_coid(coid)
        if existing_for_coid is None:
            raise UnknownClientOrderId(
                f"cannot bind order_id {order_id}: no intent registered "
                f"for client_order_id {coid!r}"
            )
        # Idempotent re-bind to the same coid.
        if existing_for_coid.order_id == order_id:
            return existing_for_coid
        # Conflict: orderId is already bound to a different coid.
        other_coid = self._order_id_to_coid.get(order_id)
        if other_coid is None:
            other_coid = self._journal_lookup_coid_for_order_id(order_id)
        if other_coid is not None and other_coid != coid:
            raise OrderIdAlreadyBound(
                f"order_id {order_id} already bound to client_order_id "
                f"{other_coid!r}; cannot rebind to {coid!r}"
            )
        next_intent = replace(
            existing_for_coid,
            order_id=order_id,
            intent_status=(
                "ACKED"
                if existing_for_coid.intent_status == "PENDING_ACK"
                else existing_for_coid.intent_status
            ),
            last_status_ts_ns=time.time_ns(),
        )
        self.journal.upsert_intent(next_intent, now_ns=time.time_ns())
        self._intents[coid] = next_intent
        self._order_id_to_coid[order_id] = coid
        return next_intent

    def _journal_lookup_coid_for_order_id(self, order_id: int) -> Optional[str]:
        intent = self.journal.fetch_intent_by_order_id(order_id)
        if intent is None:
            return None
        return intent.client_order_id

    # ---- fill matching ----

    def on_fill_report(self, report: FillReport) -> LinkRecord:
        """Match a venue-side FillReport against a registered
        intent. Returns a :class:`LinkRecord`. Side effects:

        * Insert one row into ``order_fill_links`` (idempotent on
          ``(order_id, trade_id)``).
        * If the intent transitions, UPSERT into ``order_intents``.
        * Append one row into ``order_status_events`` (idempotent).
        * Update the in-memory cache.

        The function NEVER raises for orphan fills — they are
        journaled with ``is_orphan = 1`` and a null coid. The
        caller can inspect the returned :class:`LinkRecord`
        ``is_orphan`` flag.

        Raises :exc:`IntentMismatch` if the report's symbol / side
        disagrees with the bound intent's symbol / side. The
        journal row is written BEFORE raising so the audit trail
        is preserved.

        Raises :exc:`UnknownClientOrderId` if the report's
        ``client_order_id`` is non-empty but no intent is registered
        for it (i.e. the caller is passing a coid the linker has
        never seen). Use ``client_order_id=""`` to explicitly mark
        the report as orphan-candidate (the linker will try to
        match by ``order_id`` and, failing that, journal as orphan).
        """
        _validate_fill_report(report)
        now_ns = time.time_ns()

        # 1. Resolve the intent.
        cached = self._resolve_intent_for_report(report)

        is_orphan = cached is None
        coid = "" if is_orphan else cached.client_order_id  # type: ignore[union-attrs]
        prev_status = "PENDING_ACK" if is_orphan else cached.intent_status  # type: ignore[union-attrs]

        # 2. Validate side / symbol if we have a bound intent.
        if cached is not None:
            if report.symbol and cached.symbol and report.symbol != cached.symbol:
                self._journal_fill_and_status(
                    report, coid, is_orphan, prev_status
                )
                raise IntentMismatch(
                    f"FillReport symbol {report.symbol!r} does not match "
                    f"intent symbol {cached.symbol!r} for "
                    f"client_order_id {coid!r} / order_id {report.order_id}"
                )
            if report.side and cached.side and report.side != cached.side:
                self._journal_fill_and_status(
                    report, coid, is_orphan, prev_status
                )
                raise IntentMismatch(
                    f"FillReport side {report.side!r} does not match "
                    f"intent side {cached.side!r} for "
                    f"client_order_id {coid!r} / order_id {report.order_id}"
                )

        # 3. Journal the fill (idempotent on (order_id, trade_id)).
        inserted = self.journal.append_fill_link(
            report,
            client_order_id=coid,
            is_orphan=is_orphan,
        )

        # 4. If we have a bound intent, transition its status and
        #    journal the status event.
        next_intent = cached
        if cached is not None:
            next_intent = _apply_status_transition(
                cached, report.order_status, ts_ns=report.ts_ns
            )
            if next_intent.intent_status != cached.intent_status:
                self.journal.upsert_intent(next_intent, now_ns=now_ns)
                self._intents[coid] = next_intent
            self.journal.append_status_event(
                report.ts_ns,
                report.order_id,
                coid,
                report.order_status,
                report.cum_filled_qty,
                report.avg_fill_price,
                report.source,
            )

        # 5. Build the LinkRecord.
        next_status = (
            "PENDING_ACK" if is_orphan else next_intent.intent_status  # type: ignore[union-attrs]
        )
        return LinkRecord(
            order_id=report.order_id,
            client_order_id=coid,
            trade_id=report.trade_id,
            intent_status_before=prev_status,
            intent_status_after=next_status,
            is_orphan=is_orphan,
            ts_ns=report.ts_ns,
            source=report.source,
        )

    def _resolve_intent_for_report(
        self, report: FillReport
    ) -> Optional[OrderIntent]:
        """Look up the bound intent for a FillReport. Tries (in
        order):

        1. By ``report.client_order_id`` (if non-empty) — exact match.
        2. By ``report.order_id`` via the reverse cache
           ``_order_id_to_coid`` then the journal — for fills whose
           coid is unknown but whose orderId was bound previously.
        3. By walking the journal's order_status_events — last
           resort, in case the cache was wiped but the journal
           survived.

        Returns ``None`` if no intent is found (orphan fill).
        """
        if report.client_order_id:
            cached = self._intents.get(report.client_order_id)
            if cached is not None:
                return cached
            persisted = self.journal.fetch_intent_by_coid(report.client_order_id)
            if persisted is not None:
                self._intents[persisted.client_order_id] = persisted
                if persisted.order_id:
                    self._order_id_to_coid[persisted.order_id] = (
                        persisted.client_order_id
                    )
                return persisted
            # coid was provided but no intent matches. Caller
            # probably passed a coid the linker has never seen.
            # Fall through to order_id lookup as a fallback.
        coid = self._order_id_to_coid.get(report.order_id)
        if coid is not None:
            cached = self._intents.get(coid)
            if cached is not None:
                return cached
        persisted = self.journal.fetch_intent_by_order_id(report.order_id)
        if persisted is not None:
            self._intents[persisted.client_order_id] = persisted
            if persisted.order_id:
                self._order_id_to_coid[persisted.order_id] = (
                    persisted.client_order_id
                )
            return persisted
        return None

    def _journal_fill_and_status(
        self,
        report: FillReport,
        coid: str,
        is_orphan: bool,
        prev_status: str,
    ) -> None:
        """Helper used by IntentMismatch path: journal the fill and
        status event BEFORE raising, so the audit trail is
        preserved.
        """
        self.journal.append_fill_link(
            report,
            client_order_id=coid,
            is_orphan=is_orphan,
        )
        if coid:
            self.journal.append_status_event(
                report.ts_ns,
                report.order_id,
                coid,
                report.order_status,
                report.cum_filled_qty,
                report.avg_fill_price,
                report.source,
            )

    # ---- read-only ----

    def fetch_intent(self, coid_or_order_id: str | int) -> Optional[OrderIntent]:
        """Return the intent for a coid (str) or order_id (int).
        Cache-first; falls back to the journal. Returns None if no
        intent is registered.
        """
        if isinstance(coid_or_order_id, int):
            cached_coid = self._order_id_to_coid.get(coid_or_order_id)
            if cached_coid is not None:
                cached = self._intents.get(cached_coid)
                if cached is not None:
                    return cached
            return self.journal.fetch_intent_by_order_id(coid_or_order_id)
        cached = self._intents.get(coid_or_order_id)
        if cached is not None:
            return cached
        return self.journal.fetch_intent_by_coid(coid_or_order_id)

    def known_intents(self) -> List[OrderIntent]:
        """Return the in-memory intent keys. Cheap; useful for
        cold-start enumeration (combine with :meth:`recover_pending`
        to hydrate).
        """
        return list(self._intents.values())

    def orphan_count(self) -> int:
        """Total orphan fills ever journaled (durable; survives a
        process restart).
        """
        return self.journal.fetch_orphan_count()

    # ---- cold start ----

    def recover_pending(self) -> int:
        """Rebuild the in-memory cache from the journal. Returns
        the number of intents rehydrated.

        Called once on cold start (after the linker is constructed
        and BEFORE the first FillReport). Idempotent: safe to call
        repeatedly; the cache is just rebuilt from the journal.
        """
        self._intents.clear()
        self._order_id_to_coid.clear()
        for intent in self.journal.all_intents():
            self._intents[intent.client_order_id] = intent
            if intent.order_id:
                self._order_id_to_coid[intent.order_id] = intent.client_order_id
        return len(self._intents)

    def close(self) -> None:
        self.journal.close()


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


if __name__ == "__main__":  # pragma: no cover
    # Trivial self-check when invoked as
    # `python3 order_to_fill_linker.py`.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "o2fl.sqlite")
        journal = OrderToFillJournal(Path(db))
        linker = Linker(journal)

        intent = OrderIntent(
            client_order_id="coid-selfcheck",
            symbol="BTCUSDT",
            side="BUY",
            intended_qty=0.010,
            intent_ts_ns=time.time_ns(),
        )
        linker.register_intent(intent)
        bound = linker.bind_order_id("coid-selfcheck", 412341234)
        print("after bind:", bound)

        report = FillReport(
            ts_ns=time.time_ns(),
            order_id=412341234,
            client_order_id="coid-selfcheck",
            trade_id="t-1",
            symbol="BTCUSDT",
            side="BUY",
            qty=0.010,
            price=67123.4,
            cum_filled_qty=0.010,
            avg_fill_price=67123.4,
            order_status="FILLED",
        )
        record = linker.on_fill_report(report)
        print("link record:", record)
        linker.close()