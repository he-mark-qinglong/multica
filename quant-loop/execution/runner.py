"""execution.runner — canonical live-execution runner core.

Rebuilt minimal implementation.  The interface is reconstructed from the
components that consume it (no behavioural changes on their side):

* ``execution/venue_adapter_binance_spot/venue_adapter_binance_spot.py``
  (lines ~100-118) — duck-typed fallbacks pin the exact shapes of
  :class:`BlockReason` and :class:`ComponentResult`.
* ``execution/venue_adapter_binance_perp_p7exec_003/venue_adapter_binance_perp.py``
  — ``on_request(request, journal, ts_ns)`` / ``on_fill(request, ack,
  journal, ts_ns)`` hook signatures (:meth:`BinancePerpAdapter.on_request`,
  :meth:`BinancePerpAdapter.on_fill`), and ``register_with_runner`` which
  requires ``runner.register`` / ``runner.register_on_fill`` /
  ``runner.components`` / ``runner.fill_components`` /
  ``runner.projection_components``.
* ``execution/slippage_attribution_p7exec_043/test_smoke.py`` —
  ``runner.submit(request)`` returns the transport ack dict with a merged
  ``"observations"`` mapping; the runner overrides the hook timestamp with
  ``time.time_ns()``.
* ``execution/venue_adapter_binance_perp_p7exec_003/test_smoke.py`` —
  the runner journals every intent and every terminal ack into the
  canonical ``fills`` table (``event_type`` in ``'intent' | 'fill' |
  'reject'``; NEVER silently dropped), and ``OrderJournal`` is a SQLite
  journal whose ``.conn`` supports both positional and by-name row access
  (``sqlite3.Row``).

Core logic (terminal classification, qty/price coercion) is implemented as
pure module-level functions; state lives only in the journal and the
runner's registration lists.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Component result envelope (shape pinned by the adapters' fallbacks)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockReason:
    """Pre-trade veto returned by a component's ``on_request`` hook.

    See ``venue_adapter_binance_spot.py`` lines ~110-114 for the canonical
    field order the adapters mirror when the runner is not importable.
    """

    component: str
    reason: str
    severity: str = "WARN"


@dataclass(frozen=True)
class ComponentResult:
    """Result envelope returned by every runner component hook."""

    block: Optional[BlockReason] = None
    observation: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Canonical journal
# ---------------------------------------------------------------------------

#: Canonical runner-owned table.  Adapters own their additive tables
#: (``binance_perp_*`` etc.); the runner owns ``fills`` — one ``intent``
#: row per submitted request and one terminal ``fill``/``reject`` row per
#: ack (see venue_adapter_binance_perp test_smoke phase_two).
_FILLS_DDL = """
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    client_order_id TEXT,
    event_type TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    qty REAL,
    price REAL,
    venue TEXT,
    payload TEXT
)
"""


class OrderJournal:
    """SQLite-backed durable order journal.

    Parameters
    ----------
    path
        Database file path, or ``":memory:"`` for an ephemeral journal.
        Components execute their own additive DDL/DML against
        :attr:`conn` directly (``CREATE TABLE IF NOT EXISTS`` pattern),
        so this class only bootstraps the runner-owned ``fills`` table.
    """

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path)
        # Rows must support both positional (``r[0]``) and by-name
        # (``row["n"]``) access — both styles appear in component tests.
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_FILLS_DDL)
        self.conn.commit()

    def record(
        self,
        *,
        ts_ns: int,
        client_order_id: Optional[str],
        event_type: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        venue: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Append one canonical row to ``fills`` and commit immediately."""
        self.conn.execute(
            "INSERT INTO fills (ts_ns, client_order_id, event_type, "
            "symbol, side, qty, price, venue, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(ts_ns),
                client_order_id,
                event_type,
                symbol,
                side,
                qty,
                price,
                venue,
                json.dumps(payload, default=str) if payload is not None else None,
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Outbound transport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutboundTransport:
    """Wire-level transport the runner invokes with the outbound request.

    ``callable_send`` is any ``(request_dict) -> ack_dict`` callable —
    a plain stub in tests, or a callable paper/live transport object such
    as ``BinancePerpPaperTransport`` (which defines ``__call__``).
    """

    callable_send: Callable[[Dict[str, Any]], Dict[str, Any]]

    def send(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self.callable_send(request)


# ---------------------------------------------------------------------------
# Pure helpers (terminal classification / numeric coercion)
# ---------------------------------------------------------------------------

#: Terminal ack statuses that count as a canonical ``fill`` row.
_FILL_STATUSES = frozenset({"filled", "partially_filled"})

#: Terminal ack statuses that count as a canonical ``reject`` row.
_REJECT_STATUSES = frozenset({"rejected", "expired", "canceled", "cancelled"})


def classify_terminal_event(ack: Mapping[str, Any]) -> str:
    """Classify a transport ack into a canonical terminal event type.

    Pure function.  Returns ``"fill"`` or ``"reject"``.  Handles both the
    Binance-style acks (``status`` field: FILLED / PARTIALLY_FILLED /
    EXPIRED / rejected) and minimal stub acks (``{"ok": True/False}``).
    """
    status = str(ack.get("status") or "").strip().lower()
    if status in _FILL_STATUSES:
        return "fill"
    if status in _REJECT_STATUSES:
        return "reject"
    if ack.get("ok") is False:
        return "reject"
    # Binance REST reject bodies carry an error ``code`` without ``ok``.
    if ack.get("code") is not None and not ack.get("ok"):
        return "reject"
    return "fill"


def _coerce_float(value: Any) -> Optional[float]:
    """Best-effort float coercion; ``None`` (not NaN) on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def terminal_qty(ack: Mapping[str, Any], request: Mapping[str, Any]) -> Optional[float]:
    """Resolve the canonical fill qty: executed qty wins, else intended."""
    for key in ("filled_qty", "executedQty", "qty", "quantity"):
        qty = _coerce_float(ack.get(key))
        if qty:
            return qty
    return _coerce_float(request.get("qty") or request.get("quantity"))


def terminal_price(ack: Mapping[str, Any], request: Mapping[str, Any]) -> Optional[float]:
    """Resolve the canonical fill price: average fill price wins."""
    for key in ("avg_price", "avgPrice", "price"):
        price = _coerce_float(ack.get(key))
        if price:
            return price
    return _coerce_float(request.get("price") or request.get("expected_price"))


def _client_order_id(request: Mapping[str, Any], ack: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    if ack:
        coid = ack.get("clientOrderId") or ack.get("client_order_id")
        if coid:
            return str(coid)
    coid = request.get("client_order_id") or request.get("clientOrderId")
    return str(coid) if coid else None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class ExecutionRunner:
    """Dispatch intents through components, transport, and fill observers.

    Wiring protocol (consumed by ``register_with_runner`` in the venue
    adapters):

    * :meth:`register` adds a pre-trade component; every registered
      component with an ``on_request`` hook is consulted in registration
      order before the transport is invoked.  Components implementing the
      projection protocol (``snapshot`` + ``recover``) are additionally
      tracked in :meth:`projection_components`.
    * :meth:`register_on_fill` adds a post-fill observer whose
      ``on_fill`` hook runs after every terminal ack.

    ``submit`` flow: stamp ``ts_ns = time.time_ns()`` (the runner owns the
    clock) → journal the canonical ``intent`` row → dispatch
    ``on_request`` (any :class:`BlockReason` vetoes the send and is
    journaled as a terminal ``reject`` — never silently dropped) →
    ``transport.send`` → journal the terminal ``fill``/``reject`` row →
    dispatch ``on_fill`` → return the ack dict with all hook observations
    merged under ``ack["observations"]``.
    """

    def __init__(self, *, journal: OrderJournal, transport: OutboundTransport):
        self._journal = journal
        self._transport = transport
        self._components: List[Tuple[str, Any]] = []
        self._fill_components: List[Tuple[str, Any]] = []
        self._projection_components: List[Any] = []

    # -- registration -------------------------------------------------------

    def register(self, component: Any, name: Optional[str] = None) -> Any:
        """Register a pre-trade component (``on_request`` hook)."""
        self._components.append((name or type(component).__name__, component))
        if hasattr(component, "snapshot") and hasattr(component, "recover"):
            self._projection_components.append(component)
        return component

    def register_on_fill(self, component: Any, name: Optional[str] = None) -> Any:
        """Register a post-fill observer (``on_fill`` hook)."""
        self._fill_components.append((name or type(component).__name__, component))
        return component

    def components(self) -> List[Tuple[str, Any]]:
        """Registered pre-trade components as ``(name, component)`` pairs."""
        return list(self._components)

    def fill_components(self) -> List[Tuple[str, Any]]:
        """Registered post-fill observers as ``(name, component)`` pairs."""
        return list(self._fill_components)

    def projection_components(self) -> List[Any]:
        """Registered components implementing the projection protocol."""
        return list(self._projection_components)

    # -- hot path -------------------------------------------------------------

    def submit(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Submit one order request; return the ack envelope.

        The ack is the transport's response dict, plus:

        * ``"observations"`` — shallow merge of every hook observation;
        * ``"blocked"`` / ``"blocks"`` — present only when a pre-trade
          component vetoed the request (transport is then NOT invoked).
        """
        req = dict(request)
        ts_ns = time.time_ns()
        observations: Dict[str, Any] = {}
        blocks: List[BlockReason] = []

        self._journal.record(
            ts_ns=ts_ns,
            client_order_id=_client_order_id(req),
            event_type="intent",
            symbol=req.get("symbol"),
            side=req.get("side"),
            qty=_coerce_float(req.get("qty") or req.get("quantity")),
            price=_coerce_float(req.get("price") or req.get("expected_price")),
            venue=req.get("venue"),
            payload=req,
        )

        for _name, component in self._components:
            hook = getattr(component, "on_request", None)
            if hook is None:
                continue
            result = hook(req, self._journal, ts_ns)
            if result is None:
                continue
            if result.observation:
                observations.update(result.observation)
            if result.block is not None:
                blocks.append(result.block)

        if blocks:
            ack: Dict[str, Any] = {
                "ok": False,
                "status": "BLOCKED",
                "clientOrderId": _client_order_id(req),
                "blocked": True,
                "blocks": [
                    {
                        "component": b.component,
                        "reason": b.reason,
                        "severity": b.severity,
                    }
                    for b in blocks
                ],
            }
            self._journal.record(
                ts_ns=ts_ns,
                client_order_id=_client_order_id(req),
                event_type="reject",
                symbol=req.get("symbol"),
                side=req.get("side"),
                qty=_coerce_float(req.get("qty") or req.get("quantity")),
                price=_coerce_float(req.get("price") or req.get("expected_price")),
                venue=req.get("venue"),
                payload=ack["blocks"],
            )
            ack["observations"] = observations
            return ack

        try:
            ack = dict(self._transport.send(req))
        except Exception as exc:  # transport failure is still journaled
            ack = {
                "ok": False,
                "status": "ERROR",
                "clientOrderId": _client_order_id(req),
                "error": f"{type(exc).__name__}: {exc}",
            }

        event_type = classify_terminal_event(ack)
        self._journal.record(
            ts_ns=ts_ns,
            client_order_id=_client_order_id(req, ack),
            event_type=event_type,
            symbol=ack.get("symbol") or req.get("symbol"),
            side=ack.get("side") or req.get("side"),
            qty=terminal_qty(ack, req),
            price=terminal_price(ack, req),
            venue=ack.get("venue") or req.get("venue"),
            payload=ack,
        )

        for _name, component in self._fill_components:
            hook = getattr(component, "on_fill", None)
            if hook is None:
                continue
            result = hook(req, ack, self._journal, ts_ns)
            if result is None:
                continue
            if result.observation:
                observations.update(result.observation)

        ack["observations"] = observations
        return ack
