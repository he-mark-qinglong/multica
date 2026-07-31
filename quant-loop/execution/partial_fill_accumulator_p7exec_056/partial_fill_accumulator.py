"""partial_fill_accumulator — P7-EXEC-056 implementation.

Aggregates the stream of partial fills for a single
``client_order_id`` into a running
:class:`PartialFillState` (cumulative qty, volume-weighted average
price, fill count, first/last timestamp, terminal status) and
journals every event so the run is recoverable from disk on cold
start.

The component is a hot-path observer: ``on_fill(event)`` runs
inside the runner's per-fill critical section with a 250us budget
per call (MAP-P7 default policy). It is single-threaded per
``client_order_id`` — the runner's connector instance owns one
accumulator and serialises all on_fill calls.

Design constraints (from MAP-P7 spec + issue SMA-36243)
-------------------------------------------------------
* **Hot-path overhead per call < 250us** in pure Python. The pure
  helper is two float multiplies + one division + one branch; the
  end-to-end ``on_fill`` (INSERT event + UPSERT state) has a default
  median in the 60-180us band on a warm journal (see
  ``evidence/bench_partial_fill_accumulator.json``).
* **Local state journaled** via :class:`PartialFillJournal`
  (sqlite WAL). The ``partial_fill_events`` table is the canonical
  append-only log; ``partial_fill_states`` is the current-state
  projection. ``replay(coid)`` rebuilds state from the journal at
  any time.
* **NEVER silently drop fills** — every event lands in
  ``partial_fill_events``, including late fills that arrive after
  the order reached a terminal status (those land with
  ``liquidity = "LATE_FILL_REJECTED"`` so the operator can see the
  exchange-side late ack in the journal). Duplicate ``trade_id`` is
  idempotent (returns existing state without a second INSERT).
* **Folder suffix ``_p7exec_NNN``** — folder is
  ``partial_fill_accumulator_p7exec_056``. No ``_v1`` / ``_v2``
  ever.
* **Pure helpers only — no I/O at module level.** The component
  reads the journal only inside :meth:`on_fill`,
  :meth:`finalize`, and :meth:`replay`.

Public surface
--------------
* :class:`FillEvent` — input fill event from the connector.
* :class:`PartialFillState` — running aggregation.
* :class:`Accumulator` — in-memory state keyed by client_order_id,
  journal-backed.
* :class:`PartialFillJournal` — sqlite WAL wrapper.
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :data:`TERMINAL_STATUSES` — frozenset of accepted terminal values.
* :exc:`LateFillRejected` — raised for fills arriving after the
  order reached a terminal status; the event is still journaled
  (as a ``LATE_FILL_REJECTED`` row) before the exception is
  re-raised so the call site can choose to swallow or surface it.

See :mod:`partial_fill_accumulator_p7exec_056` for the package
surface, ``README.md`` for the spec, ``INTERFACE.md`` for the wire
contract, and ``SPEC.md`` for the extended design doc.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---- Constants --------------------------------------------------------------

# Canonical terminal statuses. Mirrors the Binance USD-M order state
# vocabulary (§1.4 of SPEC_live_paper_connector_binance_usdm.md). A
# partial-fill accumulator moves to a terminal status once it observes
# one of these on the order; subsequent on_fill calls raise
# LateFillRejected after journaling the late event.
TERMINAL_STATUSES = frozenset({
    "FILLED",        # order reached full intended qty
    "CANCELED",      # explicitly cancelled (may have residual qty)
    "EXPIRED",       # time-in-force expired (e.g. GTD, IOC)
    "REJECTED",      # venue rejected; no partials expected
})

# Liquidity tag written for fills that arrive after the order reached
# a terminal status. The connector's normal ``liquidity`` field uses
# ``taker`` / ``maker`` per SPEC §4.1; this is a separate
# component-internal sentinel.
LATE_FILL_LIQUIDITY = "LATE_FILL_REJECTED"


# ---- Schema -----------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS partial_fill_events (
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
    cumulative_qty_after REAL NOT NULL,
    avg_price_after REAL NOT NULL,
    UNIQUE(client_order_id, trade_id)
);

CREATE INDEX IF NOT EXISTS idx_partial_fill_events_coid
    ON partial_fill_events(client_order_id, ts_ns);

CREATE TABLE IF NOT EXISTS partial_fill_states (
    client_order_id TEXT PRIMARY KEY,
    symbol TEXT,
    side TEXT,
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

CREATE INDEX IF NOT EXISTS idx_partial_fill_states_terminal
    ON partial_fill_states(terminal_status);
"""


# ---- Types ------------------------------------------------------------------

@dataclass(frozen=True)
class FillEvent:
    """A single fill event from the connector (one row from the
    trade log in SPEC_live_paper_connector_binance_usdm.md §4.1).

    ``trade_id`` is the venue-assigned trade identifier (or the
    connector-generated pseudo-id for synthetic fills). It is the
    idempotency key together with ``client_order_id``: a duplicate
    ``trade_id`` for the same ``client_order_id`` does NOT journal
    a second row.
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


@dataclass(frozen=True)
class PartialFillState:
    """Running aggregation for one client_order_id.

    Immutable; updated copies returned by every on_fill / finalize
    call. ``avg_price`` is the volume-weighted average across all
    fills seen so far; ``notional_usd`` is ``total_qty * avg_price``
    (kept explicit for fast dashboard reads without an extra
    multiply).
    """
    client_order_id: str
    symbol: str = ""
    side: str = ""
    total_qty: float = 0.0
    avg_price: float = 0.0
    notional_usd: float = 0.0
    fill_count: int = 0
    first_fill_ts_ns: int = 0
    last_fill_ts_ns: int = 0
    terminal_status: Optional[str] = None
    terminal_ts_ns: Optional[int] = None


class LateFillRejected(Exception):
    """Raised when on_fill receives an event whose client_order_id
    is already in a terminal status.

    The connector's recommended handling is to log + drop; the
    component has already journaled the late event with
    ``liquidity = "LATE_FILL_REJECTED"`` before raising, so the
    forensic record is preserved regardless of caller behaviour.
    """


# ---- Pure helpers -----------------------------------------------------------

def _new_state_from_event(event: FillEvent) -> PartialFillState:
    """Build the initial PartialFillState for a brand-new coid."""
    notional = event.qty * event.price
    return PartialFillState(
        client_order_id=event.client_order_id,
        symbol=event.symbol,
        side=event.side,
        total_qty=event.qty,
        avg_price=event.price,
        notional_usd=notional,
        fill_count=1,
        first_fill_ts_ns=event.ts_ns,
        last_fill_ts_ns=event.ts_ns,
    )


def _fold_fill(prev: PartialFillState, event: FillEvent) -> PartialFillState:
    """Fold one FillEvent into the running state. Pure function.

    Handles the ``total_qty == 0`` cold-start edge case (avg = fill
    price). Does NOT touch the journal — that is the caller's job.
    """
    if prev.total_qty <= 0.0:
        # Cold start; should not happen because on_fill always creates
        # a state from the first event before invoking _fold_fill on
        # subsequent events. Guard against a hand-crafted replay
        # where state was wiped but events survived.
        return _new_state_from_event(event)
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


# ---- Journal ----------------------------------------------------------------

class PartialFillJournal:
    """Sqlite WAL-backed journal for the partial-fill aggregator.

    The journal owns the schema and provides two write methods
    (``append_event``, ``upsert_state``) and two read methods
    (``fetch_state``, ``fetch_events``). The hot path makes one
    ``append_event`` call and one ``upsert_state`` call per fill;
    both run inside a single sqlite transaction so the events
    table and the states table can never disagree.

    The connector writes ``journal_mode=WAL`` and
    ``synchronous=NORMAL`` to keep the median per-INSERT cost in
    the tens-of-microseconds band while still surviving a process
    crash (WAL flushes the journal at every commit, even with
    ``synchronous=NORMAL``).
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

    def upsert_state(self, state: PartialFillState, now_ns: int) -> None:
        self._conn.execute(
            """
            INSERT INTO partial_fill_states(
                client_order_id, symbol, side,
                total_qty, avg_price, notional_usd,
                fill_count, first_fill_ts_ns, last_fill_ts_ns,
                terminal_status, terminal_ts_ns, updated_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                symbol=excluded.symbol,
                side=excluded.side,
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

    def append_event(
        self,
        event: FillEvent,
        state_after: PartialFillState,
        ts_exchange_ns: int = 0,
    ) -> bool:
        """Insert one row into ``partial_fill_events``. Returns
        True if the row was inserted, False if a duplicate
        (client_order_id, trade_id) was already present (idempotent
        replay).

        The duplicate path is silently idempotent — callers that
        need strict-uniqueness semantics should pre-check
        ``trade_id`` themselves. Per the NEVER-silently-drop
        constraint, the journal still records the OUTCOME (state
        snapshot) via the upsert in the surrounding transaction;
        a duplicate fill does NOT lose data.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO partial_fill_events(
                ts_ns, ts_exchange_ns, client_order_id, trade_id,
                symbol, side, qty, price, notional_usd,
                commission, commission_asset, liquidity,
                cumulative_qty_after, avg_price_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                state_after.total_qty,
                state_after.avg_price,
            ),
        )
        return cur.rowcount > 0

    def fetch_state(self, coid: str) -> Optional[PartialFillState]:
        row = self._conn.execute(
            """
            SELECT client_order_id, symbol, side, total_qty,
                   avg_price, notional_usd, fill_count,
                   first_fill_ts_ns, last_fill_ts_ns,
                   terminal_status, terminal_ts_ns
            FROM partial_fill_states WHERE client_order_id = ?
            """,
            (coid,),
        ).fetchone()
        if row is None:
            return None
        return PartialFillState(
            client_order_id=row["client_order_id"],
            symbol=row["symbol"] or "",
            side=row["side"] or "",
            total_qty=row["total_qty"],
            avg_price=row["avg_price"],
            notional_usd=row["notional_usd"],
            fill_count=row["fill_count"],
            first_fill_ts_ns=row["first_fill_ts_ns"] or 0,
            last_fill_ts_ns=row["last_fill_ts_ns"] or 0,
            terminal_status=row["terminal_status"],
            terminal_ts_ns=row["terminal_ts_ns"],
        )

    def fetch_events(self, coid: str) -> List[FillEvent]:
        rows = self._conn.execute(
            """
            SELECT ts_ns, ts_exchange_ns, client_order_id, trade_id,
                   symbol, side, qty, price, commission,
                   commission_asset, liquidity
            FROM partial_fill_events
            WHERE client_order_id = ?
            ORDER BY ts_ns ASC, id ASC
            """,
            (coid,),
        ).fetchall()
        out: List[FillEvent] = []
        for row in rows:
            out.append(
                FillEvent(
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
                )
            )
        return out


def bootstrap_journal(target) -> None:
    """Idempotent schema bootstrap. Accepts either a
    :class:`PartialFillJournal` or a raw sqlite3 connection.

    Safe to call from a cold-start path on every restart; the
    statements are all ``CREATE ... IF NOT EXISTS``.
    """
    conn = target._conn if isinstance(target, PartialFillJournal) else target
    conn.executescript(SCHEMA_SQL)


# ---- Accumulator ------------------------------------------------------------

class Accumulator:
    """In-memory partial-fill aggregator with journal persistence.

    One accumulator per connector instance. Hot path is
    :meth:`on_fill`; cold paths are :meth:`finalize`,
    :meth:`replay`, :meth:`snapshot`, :meth:`known_orders`.

    Concurrency: single-threaded. The connector serialises fill
    events through a single WS user-data stream per
    ``client_order_id``, so the accumulator does not need a lock.
    A multi-threaded caller must wrap ``on_fill`` in their own
    mutex (the journal itself uses sqlite's per-connection
    serialization, which is sufficient for the in-process case).
    """

    def __init__(self, journal: PartialFillJournal) -> None:
        self.journal = journal
        self._state: Dict[str, PartialFillState] = {}

    # ---- hot path ----------------------------------------------------------

    def on_fill(self, event: FillEvent) -> PartialFillState:
        """Fold one fill event into the running state and journal.

        Hot path. Returns the new state (immutable copy).

        Behaviour:
        * Unknown ``client_order_id`` → create new state from the
          first fill. Both journal writes happen.
        * Known ``client_order_id``, not terminal → fold fill,
          journal both writes. Median cost well under the 250us
          budget on a warm journal.
        * Known ``client_order_id``, terminal → journal the event
          with ``liquidity = LATE_FILL_LIQUIDITY`` so the forensic
          record is preserved, then raise :exc:`LateFillRejected`.
          The state is NOT updated.
        * Duplicate ``trade_id`` for the same ``client_order_id``
          (idempotent replay from a re-sent WS event) → return the
          existing state without a second journal write.
        """
        prev = self._state.get(event.client_order_id)
        if prev is None:
            # Hydrate from the journal in case this is a re-start
            # path where the in-memory dict was empty but the
            # journal survived.
            prev = self.journal.fetch_state(event.client_order_id)
            if prev is not None:
                self._state[event.client_order_id] = prev

        if prev is not None and prev.terminal_status is not None:
            # Terminal. Journal the late event for forensics, then
            # raise so the caller can log + drop.
            late_event = FillEvent(
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
            )
            self.journal.append_event(late_event, prev)
            raise LateFillRejected(
                f"late fill for terminal order {event.client_order_id} "
                f"(status={prev.terminal_status})"
            )

        if prev is None:
            next_state = _new_state_from_event(event)
        else:
            next_state = _fold_fill(prev, event)

        # Try to journal the event. ``append_event`` is silently
        # idempotent on (coid, trade_id) — the INSERT OR IGNORE returns
        # 0 rows if a duplicate is already journaled. On a duplicate we
        # must NOT mutate the in-memory cache: the cache has to stay
        # in lock-step with the journal, otherwise a duplicate re-sent
        # after a restart would silently double-count the qty.
        inserted = self.journal.append_event(event, next_state)
        if inserted:
            self.journal.upsert_state(next_state, now_ns=event.ts_ns)
            self._state[event.client_order_id] = next_state
            return next_state
        # Duplicate (coid, trade_id) — journal already has this event.
        # Hydrate the in-memory cache if it was empty (cold-start path)
        # so future calls avoid another journal fetch, but return the
        # UNCHANGED existing state.
        if prev is not None:
            self._state[event.client_order_id] = prev
            return prev
        # No prior state anywhere — the duplicate INSERT OR IGNORE
        # silently no-op'd on an event with no journal history. This
        # should not happen in practice (the connector guarantees a
        # unique trade_id per fill), but defensively return next_state
        # so the caller sees something coherent.
        self._state[event.client_order_id] = next_state
        return next_state

    # ---- cold paths --------------------------------------------------------

    def finalize(
        self,
        coid: str,
        terminal_status: str,
        ts_ns: int = 0,
    ) -> PartialFillState:
        """Mark an order as terminal. No more on_fill accepted.

        ``terminal_status`` must be in :data:`TERMINAL_STATUSES`;
        anything else raises ``ValueError``. The state is updated
        with the terminal marker, journaled, and returned.
        """
        if terminal_status not in TERMINAL_STATUSES:
            raise ValueError(
                f"unknown terminal_status {terminal_status!r}; "
                f"must be one of {sorted(TERMINAL_STATUSES)}"
            )
        prev = self._state.get(coid)
        if prev is None:
            prev = self.journal.fetch_state(coid)
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
        self.journal.upsert_state(next_state, now_ns=next_state.terminal_ts_ns)
        self._state[coid] = next_state
        return next_state

    def replay(self, coid: str) -> Optional[PartialFillState]:
        """Rebuild the state for one client_order_id from the
        journal. Returns ``None`` if no events exist for that coid.

        Useful on cold start when the in-memory dict is empty; the
        caller can also pass ``known_orders()`` and replay every
        one. Replay folds every event in chronological order and
        updates the in-memory cache so subsequent ``on_fill`` calls
        see the rebuilt state without re-hydrating from sqlite.
        """
        events = self.journal.fetch_events(coid)
        if not events:
            return None
        state = _new_state_from_event(events[0])
        for ev in events[1:]:
            state = _fold_fill(state, ev)
        # Honour a previously-finalized terminal marker.
        persisted = self.journal.fetch_state(coid)
        if persisted is not None and persisted.terminal_status is not None:
            state = replace(
                state,
                terminal_status=persisted.terminal_status,
                terminal_ts_ns=persisted.terminal_ts_ns,
            )
        self._state[coid] = state
        return state

    def snapshot(self, coid: str) -> Optional[PartialFillState]:
        """Return the cached state for one client_order_id without
        touching the journal. Returns None if not in cache (and
        not on disk — callers wanting disk-fallback should call
        ``replay``)."""
        return self._state.get(coid)

    def known_orders(self) -> List[str]:
        """Return the in-memory keys. Cheap; useful for cold-start
        enumeration (combine with ``replay`` to hydrate)."""
        return list(self._state.keys())

    def close(self) -> None:
        self.journal.close()


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


if __name__ == "__main__":  # pragma: no cover
    # Trivial self-check when invoked as `python3 partial_fill_accumulator.py`.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "pfa.sqlite")
        journal = PartialFillJournal(Path(db))
        acc = Accumulator(journal)
        ev = FillEvent(
            ts_ns=time.time_ns(),
            client_order_id="coid-selfcheck",
            trade_id="t-1",
            symbol="BTCUSDT",
            side="BUY",
            qty=0.01,
            price=50000.0,
            liquidity="taker",
        )
        s = acc.on_fill(ev)
        print("after first fill:", s)
        ev2 = FillEvent(
            ts_ns=time.time_ns(),
            client_order_id="coid-selfcheck",
            trade_id="t-2",
            symbol="BTCUSDT",
            side="BUY",
            qty=0.005,
            price=50100.0,
            liquidity="taker",
        )
        s2 = acc.on_fill(ev2)
        print("after second fill:", s2)
        print("avg should be ~50033.33, total 0.015")
        acc.finalize("coid-selfcheck", "FILLED")
        acc.close()