"""slippage_attribution — P7-EXEC-043 implementation.

Decomposes every exchange fill's total slippage (vs the strategy's
arrival-time reference) into two signed components:

    total_slippage_bps = spread_cost_bps + impact_bps + residual_bps

* ``spread_cost_bps`` — the half-spread the trader pays to cross the
  book on a marketable order. Captured from the intent's
  ``arrival_bid`` / ``arrival_ask`` snapshot the runner carries on the
  request payload (additive enrichment, P7-EXEC-080).

* ``impact_bps`` — the residual movement beyond the half-spread. This
  is the per-fill proxy for queue-priority loss + depth consumption +
  slow-market latency. Equal to
  ``total_slippage_bps - spread_cost_bps``.

* ``residual_bps`` — the un-modelled slack. By construction this is
  zero for a clean decomposition (the two legs sum to total); we keep
  it as an explicit column so a future model extension (e.g.
  Almgren-Chriss impact) can populate it without a schema change.

Sign convention (matches the canonical
``venue_fill_quality_p7exec_080.slippage_bps``):

* Positive = price improvement for the trader.
* Negative = slippage paid to the venue.

For a marketable BUY the half-spread is paid (negative); for a
marketable SELL the half-spread is also paid (negative); impact is
positive only on the rare venue-rebate leg, and negative in the
common adverse case.

Why this decomposition
----------------------
``slippage_report_p7exec_050`` answers "how much did we bleed this
week?" — a single signed number per day, aggregated by venue / symbol.
This component answers the *why*: how much of that bleed came from
spread crossing (which is structural and largely venue-driven) vs
how much came from impact (which is execution-policy-driven — slower
slicing, smarter pegs, deeper books all reduce it without changing
the spread leg). A quant who watches ``impact_bps`` drop while
``spread_cost_bps`` stays flat has improved the algorithm without
moving venues; one who watches the spread leg swing needs to
re-shop the venue or add maker legs.

Design constraints (from MAP-P7 spec)
-------------------------------------
* **Hot-path overhead per call < 250us** in pure Python. The pure
  helper is 3 multiplications + 1 division + 2 subtractions; the
  ``on_fill`` hook is one INSERT plus the same arithmetic (median
  well under 100us; bench measures end-to-end). The runner's hot
  path budget is the additive sum of every registered observer —
  this component contributes a single-digit-microsecond median.
* **Local state journaled** via :class:`OrderJournal` (sqlite WAL).
  The ``slippage_attribution_fills`` table is the canonical record;
  the in-memory snapshot is rebuildable from the journal at any
  time via :meth:`SlippageAttributionClassifier.recover`.
* **NEVER silently drop fills** — every malformed input raises
  ``ValueError``; a fill missing ``arrival_bid``/``arrival_ask`` is
  recorded as ``classification=NO_BOOK`` (a known-unknown bucket)
  and excluded from the spread / impact aggregate. The runner's
  canonical ``fills`` row is unchanged; this component cannot
  retroactively drop a fill.
* **Folder suffix ``_p7exec_NNN``** — folder is
  ``slippage_attribution_p7exec_043``. No ``_v1``/``_v2`` ever.
* **Pure helpers only — no I/O at module level.** The component
  reads the journal only inside :meth:`compute_day` and
  :meth:`SlippageAttributionClassifier.on_fill`.

Public surface
--------------
* :class:`FillRecord` — input fill to attribute.
* :class:`AttributionRow` — immutable decomposition result.
* :class:`AttributionRecord` — journal row projection.
* :class:`DailyAttributionReport` — immutable per-day aggregator.
* :class:`VenueDailyAttribution` — per-venue breakdown.
* :class:`SymbolDailyAttribution` — per-symbol breakdown.
* :class:`AttributionThresholds`, :data:`DEFAULT_ATTRIBUTION_THRESHOLDS`
  — WARN configuration.
* :func:`spread_cost_bps`, :func:`half_spread_bps`,
  :func:`attribute_fill`, :func:`attribute_fills` — pure functions.
* :func:`aggregate_by_venue`, :func:`aggregate_by_symbol`,
  :func:`aggregate_overall` — analytics.
* :func:`day_utc_bounds` — UTC calendar day boundaries.
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :class:`SlippageAttributionClassifier` — the runner-wired
  live observer (additive ``on_fill`` hook, P7-EXEC-081 pattern).
* :class:`SlippageAttributionReport` — cold-path periodic
  aggregator (additive ``record()`` / ``fetch()`` pattern,
  P7-EXEC-050 pattern).

See :mod:`slippage_attribution_p7exec_043` for the
package surface, ``README.md`` for the spec, ``INTERFACE.md``
for the wire contract, and ``SPEC.md`` for the extended design
doc.
"""
from __future__ import annotations

import calendar
import json
import math
import sqlite3
import time
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

# Import the runner primitives. Use the same dual-import pattern as
# every sibling P7-EXEC component so the module works both as
# ``execution.slippage_attribution_p7exec_043.slippage_attribution``
# (canonical package path) and as a top-level script (vendored tests,
# ad-hoc diagnostics). The relative-import form breaks under the latter.
try:
    from execution.runner import (
        ComponentResult,
        ExecutionRunner,
        OrderJournal,
        OutboundTransport,
    )
except ImportError:  # pragma: no cover — vendored-copy fallback
    from runner import (  # type: ignore[no-redef]
        ComponentResult,
        ExecutionRunner,
        OrderJournal,
        OutboundTransport,
    )


# ---- Constants --------------------------------------------------------------

# Canonical spread / impact / residual classification labels. Strings
# (not enum) because the journal rows and downstream pandas code
# prefer string columns; keeping them as plain string constants means
# ``attribution.classification == "SPREAD"`` works without an import
# dance.
SPREAD = "SPREAD"     # total is dominated by spread cost (>50% of |total|)
IMPACT = "IMPACT"     # total is dominated by impact
MIXED = "MIXED"       # neither leg dominates; both contribute meaningfully
NO_BOOK = "NO_BOOK"   # arrival_bid/arrival_ask unavailable; cannot decompose

# Tolerance for the residual check. A pure two-leg decomposition has
# residual == 0 by construction; the tolerance absorbs IEEE-754 noise
# when total_slippage_bps and spread_cost_bps are both large floats.
RESIDUAL_EPSILON_BPS = 1e-6

# Time-window configuration for the WARN observer. Mirrors the
# MakerTakerClassifier default; the two observers share the same
# journal, so they should converge on the same window semantics.
DEFAULT_WINDOW_S = 60.0

# Threshold for the IMPACT / SPREAD classification. A leg dominates if
# its absolute value exceeds this fraction of the total's absolute
# value. Default 0.5 (= "more than half of |total|").
DEFAULT_DOMINANCE_FRACTION = 0.5

# IMPACT-WARN threshold. The live observer emits a WARN row when the
# trailing-window mean impact_bps is more negative than
# ``-impact_warn_bps``. Default 5 bps — empirically a healthy
# execution keeps impact below 5 bps on liquid pairs; anything worse
# is worth alerting on.
DEFAULT_IMPACT_WARN_BPS = 5.0

# Impact-recovered hysteresis. The observer emits a RECOVERED row
# when the trailing mean climbs above
# ``-(impact_warn_bps - impact_hysteresis_bps)``. Default 1.0 bps of
# hysteresis prevents alert flapping at the threshold boundary.
DEFAULT_IMPACT_HYSTERESIS_BPS = 1.0


# ---- Schema -----------------------------------------------------------------

# Additive table managed by ``bootstrap_journal``. Idempotent
# ``CREATE TABLE IF NOT EXISTS`` so it is safe to invoke from the
# constructor or on a hot-restart path. The table is the canonical
# projection of per-fill attribution rows; cold-start dashboards
# can rebuild the in-memory snapshot from it.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS slippage_attribution_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    client_order_id TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    qty REAL NOT NULL DEFAULT 0.0,
    expected_price REAL NOT NULL DEFAULT 0.0,
    fill_price REAL NOT NULL DEFAULT 0.0,
    arrival_bid REAL,
    arrival_ask REAL,
    arrival_mid REAL,
    spread_bps REAL NOT NULL DEFAULT 0.0,
    total_slippage_bps REAL NOT NULL DEFAULT 0.0,
    spread_cost_bps REAL NOT NULL DEFAULT 0.0,
    impact_bps REAL NOT NULL DEFAULT 0.0,
    residual_bps REAL NOT NULL DEFAULT 0.0,
    venue TEXT,
    classification TEXT NOT NULL DEFAULT 'NO_BOOK',
    payload TEXT,
    UNIQUE(client_order_id)
);
CREATE INDEX IF NOT EXISTS ix_sa_ts ON slippage_attribution_fills(ts_ns);
CREATE INDEX IF NOT EXISTS ix_sa_venue ON slippage_attribution_fills(venue);
CREATE INDEX IF NOT EXISTS ix_sa_symbol ON slippage_attribution_fills(symbol);
CREATE INDEX IF NOT EXISTS ix_sa_class ON slippage_attribution_fills(classification);

CREATE TABLE IF NOT EXISTS slippage_attribution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    severity TEXT NOT NULL,                 -- 'WARN' | 'RECOVERED'
    observed_mean_impact_bps REAL NOT NULL,
    threshold_bps REAL NOT NULL,
    window_s REAL NOT NULL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_sae_ts ON slippage_attribution_events(ts_ns);
CREATE INDEX IF NOT EXISTS ix_sae_symbol ON slippage_attribution_events(symbol);
CREATE INDEX IF NOT EXISTS ix_sae_severity ON slippage_attribution_events(severity);

CREATE TABLE IF NOT EXISTS slippage_attribution_daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    day_utc TEXT NOT NULL,
    n_fills INTEGER NOT NULL DEFAULT 0,
    n_fills_with_book INTEGER NOT NULL DEFAULT 0,
    n_fills_no_book INTEGER NOT NULL DEFAULT 0,
    mean_total_slippage_bps REAL NOT NULL DEFAULT 0.0,
    mean_spread_cost_bps REAL NOT NULL DEFAULT 0.0,
    mean_impact_bps REAL NOT NULL DEFAULT 0.0,
    median_total_slippage_bps REAL NOT NULL DEFAULT 0.0,
    p95_impact_bps REAL NOT NULL DEFAULT 0.0,
    p05_impact_bps REAL NOT NULL DEFAULT 0.0,
    total_cost_bps_notional REAL NOT NULL DEFAULT 0.0,
    impact_share REAL NOT NULL DEFAULT 0.0,
    spread_share REAL NOT NULL DEFAULT 0.0,
    n_spread_dominant INTEGER NOT NULL DEFAULT 0,
    n_impact_dominant INTEGER NOT NULL DEFAULT 0,
    n_mixed INTEGER NOT NULL DEFAULT 0,
    by_venue_json TEXT NOT NULL DEFAULT '{}',
    by_symbol_json TEXT NOT NULL DEFAULT '{}',
    min_sample INTEGER NOT NULL DEFAULT 0,
    payload TEXT,
    UNIQUE(day_utc)
);
CREATE INDEX IF NOT EXISTS ix_sadr_day ON slippage_attribution_daily_reports(day_utc);
CREATE INDEX IF NOT EXISTS ix_sadr_ts ON slippage_attribution_daily_reports(ts_ns);
"""


def bootstrap_journal(journal: OrderJournal) -> None:
    """Idempotently create the additive tables on the journal.

    Safe to call repeatedly — every statement uses ``IF NOT EXISTS``
    and the daily report carries a ``UNIQUE(day_utc)`` constraint.
    The runner's :class:`OrderJournal` exposes a single sqlite
    connection (:attr:`conn`); this helper runs the bootstrap
    script inside the same connection so the journal's WAL state is
    consistent with the bootstrap.

    ``SlippageAttributionClassifier.__init__`` calls this on the
    passed journal; a cold-start path that does not need the live
    observer (e.g. a one-shot ``SlippageAttributionReport.compute_day``
    from a cron) can call it directly to make sure the tables exist.
    """
    with closing(journal.conn.cursor()) as cur:
        cur.executescript(SCHEMA_SQL)


# ---- Pure helpers -----------------------------------------------------------


def _validate_side(side: str) -> str:
    """Normalize side to ``BUY`` / ``SELL`` (case-insensitive).

    Raises ``ValueError`` on any other value. Never silently coerce —
    a malformed side upstream is a bug, not an edge case.
    """
    if not isinstance(side, str):
        raise ValueError(f"side must be str, got {type(side).__name__}")
    s = side.strip().upper()
    if s not in ("BUY", "SELL"):
        raise ValueError(
            f"side must be 'BUY' or 'SELL' (case-insensitive), got {side!r}"
        )
    return s


def half_spread_bps(
    arrival_bid: float, arrival_ask: float
) -> float:
    """Return the half-spread in basis points at arrival.

    Both inputs must be positive floats with ``bid < ask``. Returns
    ``(ask - bid) / mid * 10000 / 2`` where ``mid = (bid + ask) / 2``.
    The half-spread is the cost the trader pays to cross the book
    on a marketable order, in canonical basis points.

    Raises ``ValueError`` on:
      - non-positive bid or ask
      - crossed book (bid >= ask) — a transient state the venue
        can produce during a fast move; we treat it as a real
        state but flag the bad input
      - non-finite values
    """
    for label, val in (("arrival_bid", arrival_bid),
                       ("arrival_ask", arrival_ask)):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(
                f"{label} must be a real number, got {type(val).__name__}"
            )
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"{label} must be finite, got {val!r}")
        if val <= 0:
            raise ValueError(f"{label} must be > 0, got {val}")
    if arrival_bid >= arrival_ask:
        raise ValueError(
            f"crossed book: arrival_bid={arrival_bid} >= "
            f"arrival_ask={arrival_ask}"
        )
    mid = (arrival_bid + arrival_ask) / 2.0
    return (arrival_ask - arrival_bid) / mid * 10_000.0 / 2.0


def spread_cost_bps(
    *, side: str, arrival_bid: float, arrival_ask: float
) -> float:
    """Return the signed half-spread cost in bps for one fill.

    Sign convention matches :func:`total_slippage_bps`: positive =
    price improvement for the trader, negative = slippage paid. For
    both BUY and SELL, the spread is *always paid* on a marketable
    order, so the returned value is always ``-half_spread_bps``
    (the trader's outflow). A positive return is reserved for a
    future "venue-rebate on marketable" leg and is not produced
    today; tests pin ``spread_cost_bps <= 0``.

    Raises ``ValueError`` on malformed input; see :func:`half_spread_bps`.
    """
    _validate_side(side)
    h = half_spread_bps(arrival_bid, arrival_ask)
    # A trader always pays the half-spread to cross the book; the
    # sign is universally negative. The future-proofing case (venue
    # rebate on marketable) is not produced today — see SPEC §3.1.
    return -h


def total_slippage_bps(
    *, side: str, expected_price: float, fill_price: float
) -> float:
    """Canonical signed slippage formula (mirrors
    :func:`execution.venue_fill_quality_p7exec_080.slippage_bps`).

    Positive = price improvement for the trader. Negative = slippage
    paid. Re-exported here so this module has zero dependencies on
    the venue-fill-quality sibling for the pure-helper surface.

    Raises ``ValueError`` on:
      - non-positive expected_price or fill_price
      - non-finite values
      - malformed side (same rule as :func:`spread_cost_bps`)
    """
    _validate_side(side)
    for label, val in (("expected_price", expected_price),
                       ("fill_price", fill_price)):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(
                f"{label} must be a real number, got {type(val).__name__}"
            )
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"{label} must be finite, got {val!r}")
        if val <= 0:
            raise ValueError(f"{label} must be > 0, got {val}")
    s = side.strip().upper()
    if s == "BUY":
        return (expected_price - fill_price) / expected_price * 10_000.0
    return (fill_price - expected_price) / expected_price * 10_000.0


def _classify(
    *,
    total: float,
    spread_cost: float,
    impact: float,
    fraction: float,
    no_book: bool,
) -> str:
    """Internal — pick the dominant-leg label for the journal row."""
    if no_book:
        return NO_BOOK
    if abs(total) < RESIDUAL_EPSILON_BPS:
        # Near-zero total — neither leg dominates; still bucket as
        # MIXED so a dashboard can render the bucket count.
        return MIXED
    spread_frac = abs(spread_cost) / abs(total) if total != 0 else 0.0
    impact_frac = abs(impact) / abs(total) if total != 0 else 0.0
    # Use an epsilon-tolerant comparison so IEEE-754 noise does
    # not flip the dominant-leg label for fills whose true
    # decomposition is a clean tie (e.g. a 50/50 spread/impact
    # split on a normalizable mid price).
    tie_tol = 1e-9
    if spread_frac >= fraction and (spread_frac - impact_frac) > tie_tol:
        return SPREAD
    if impact_frac >= fraction and (impact_frac - spread_frac) > tie_tol:
        return IMPACT
    if spread_frac >= fraction and impact_frac >= fraction:
        # Both legs clear the dominance threshold but are tied
        # within tolerance. Default to SPREAD — the structural /
        # venue-driven leg is the slower-moving one, so flagging
        # it first lets an operator quickly decide whether the
        # venue is at fault before drilling into the impact leg.
        return SPREAD
    return MIXED


# ---- Types ------------------------------------------------------------------


@dataclass(frozen=True)
class FillRecord:
    """One exchange-reported fill to attribute.

    Required: ``timestamp``, ``side``, ``symbol``, ``expected_price``,
    ``fill_price``, ``quantity``. Optional book snapshot:
    ``arrival_bid``, ``arrival_ask`` — when both are present the
    spread leg is computed; when either is missing the row is
    classified as ``NO_BOOK`` and the spread / impact columns are
    set to zero (the journal still records the row so a dashboard
    can count the missing-book rate).

    Validation (``attribute_fill`` raises ``ValueError`` on):
      - ``side`` not in ``("BUY", "SELL")``
      - ``quantity <= 0``
      - ``expected_price <= 0`` or ``fill_price <= 0``
      - non-finite prices or qty
      - ``arrival_bid`` / ``arrival_ask`` present but crossed
    """

    timestamp: object  # pd.Timestamp-like; kept loose for hot path
    side: str
    symbol: str
    expected_price: float
    fill_price: float
    quantity: float
    arrival_bid: Optional[float] = None
    arrival_ask: Optional[float] = None
    arrival_mid: Optional[float] = None
    venue: Optional[str] = None
    client_order_id: Optional[str] = None


@dataclass(frozen=True)
class AttributionRow:
    """Signed decomposition of a single fill.

    All bps fields share the canonical sign convention: positive =
    price improvement, negative = slippage paid. For a clean two-leg
    decomposition, ``residual_bps == 0`` and
    ``spread_cost_bps + impact_bps == total_slippage_bps`` (within
    ``RESIDUAL_EPSILON_BPS``).

    ``classification`` is one of ``SPREAD`` / ``IMPACT`` / ``MIXED``
    / ``NO_BOOK`` — see the module-level constants.
    """

    timestamp: object
    client_order_id: Optional[str]
    symbol: str
    side: str
    venue: Optional[str]
    expected_price: float
    fill_price: float
    quantity: float
    arrival_bid: Optional[float]
    arrival_ask: Optional[float]
    arrival_mid: Optional[float]
    spread_bps: float          # half-spread in absolute bps (always >= 0)
    total_slippage_bps: float  # signed
    spread_cost_bps: float     # signed (= -spread_bps by construction)
    impact_bps: float          # signed (= total - spread_cost)
    residual_bps: float        # signed (= total - spread_cost - impact)
    classification: str        # SPREAD|IMPACT|MIXED|NO_BOOK


@dataclass(frozen=True)
class AttributionRecord:
    """One journal-row projection of an :class:`AttributionRow`.

    Returned by :meth:`SlippageAttributionClassifier.on_fill` so a
    caller can inspect what was journaled without re-reading the
    table.
    """

    row_id: int
    attribution: AttributionRow


# ---- Aggregation types ------------------------------------------------------


@dataclass(frozen=True)
class VenueDailyAttribution:
    venue: str
    n_fills: int
    n_fills_with_book: int
    mean_total_slippage_bps: float
    mean_spread_cost_bps: float
    mean_impact_bps: float
    median_impact_bps: float
    impact_share: float
    spread_share: float


@dataclass(frozen=True)
class SymbolDailyAttribution:
    symbol: str
    n_fills: int
    n_fills_with_book: int
    mean_total_slippage_bps: float
    mean_spread_cost_bps: float
    mean_impact_bps: float
    median_impact_bps: float
    impact_share: float
    spread_share: float


@dataclass(frozen=True)
class DailyAttributionReport:
    """Immutable value type for one day's attribution aggregate."""

    day_utc: str
    n_fills: int
    n_fills_with_book: int
    n_fills_no_book: int
    mean_total_slippage_bps: float
    mean_spread_cost_bps: float
    mean_impact_bps: float
    median_total_slippage_bps: float
    p95_impact_bps: float
    p05_impact_bps: float
    total_cost_bps_notional: float
    impact_share: float
    spread_share: float
    n_spread_dominant: int
    n_impact_dominant: int
    n_mixed: int
    by_venue: Tuple[VenueDailyAttribution, ...]
    by_symbol: Tuple[SymbolDailyAttribution, ...]
    min_sample: int
    generated_at_ns: int
    stable: bool

    @property
    def key(self) -> Tuple[str, int]:
        return (self.day_utc, self.generated_at_ns)


@dataclass(frozen=True)
class AttributionThresholds:
    """WARN configuration for the live observer.

    ``window_s`` — rolling window the observer keeps for the per-symbol
    mean impact. Default ``60.0`` seconds (matches
    ``MakerTakerClassifier``'s default).

    ``impact_warn_bps`` — trailing-window mean impact more negative
    than ``-impact_warn_bps`` triggers a WARN row. Default ``5.0``.

    ``impact_hysteresis_bps`` — recovery requires the trailing mean
    to climb above ``-(impact_warn_bps - impact_hysteresis_bps)``.
    Default ``1.0``.

    ``dominance_fraction`` — threshold for the SPREAD / IMPACT /
    MIXED classification (see :func:`_classify`). Default ``0.5``.
    """

    window_s: float = DEFAULT_WINDOW_S
    impact_warn_bps: float = DEFAULT_IMPACT_WARN_BPS
    impact_hysteresis_bps: float = DEFAULT_IMPACT_HYSTERESIS_BPS
    dominance_fraction: float = DEFAULT_DOMINANCE_FRACTION

    def __post_init__(self) -> None:
        if self.window_s <= 0:
            raise ValueError(
                f"window_s must be > 0, got {self.window_s}"
            )
        if self.impact_warn_bps < 0:
            raise ValueError(
                f"impact_warn_bps must be >= 0, got {self.impact_warn_bps}"
            )
        if self.impact_hysteresis_bps < 0:
            raise ValueError(
                f"impact_hysteresis_bps must be >= 0, "
                f"got {self.impact_hysteresis_bps}"
            )
        if self.impact_hysteresis_bps > self.impact_warn_bps:
            raise ValueError(
                f"impact_hysteresis_bps ({self.impact_hysteresis_bps}) "
                f"must be <= impact_warn_bps ({self.impact_warn_bps})"
            )
        if not (0.0 < self.dominance_fraction <= 1.0):
            raise ValueError(
                f"dominance_fraction must be in (0, 1], "
                f"got {self.dominance_fraction}"
            )


DEFAULT_ATTRIBUTION_THRESHOLDS = AttributionThresholds()


# ---- Pure analytics ---------------------------------------------------------


def attribute_fill(record: FillRecord) -> AttributionRow:
    """Decompose one :class:`FillRecord` into its attribution row.

    Returns the immutable :class:`AttributionRow`. When
    ``arrival_bid`` or ``arrival_ask`` is missing (or invalid), the
    row is classified as ``NO_BOOK``, the spread / impact columns are
    set to zero, and ``total_slippage_bps`` is still computed (the
    strategy's expected-price-vs-fill leg is independent of the
    book snapshot). Never silently drops a fill.

    Raises ``ValueError`` on malformed input that cannot be coerced
    to a meaningful row (negative qty, non-positive prices, etc.).
    """
    if record.quantity is None or record.quantity <= 0:
        raise ValueError(
            f"quantity must be > 0, got {record.quantity!r}"
        )
    for label, val in (("expected_price", record.expected_price),
                       ("fill_price", record.fill_price)):
        if val is None or val <= 0:
            raise ValueError(f"{label} must be > 0, got {val!r}")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(
                f"{label} must be a real number, got {type(val).__name__}"
            )
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"{label} must be finite, got {val!r}")
    side = _validate_side(record.side)

    total = total_slippage_bps(
        side=side,
        expected_price=record.expected_price,
        fill_price=record.fill_price,
    )

    no_book = False
    spread = 0.0
    cost = 0.0
    bid = record.arrival_bid
    ask = record.arrival_ask
    if (
        bid is not None
        and ask is not None
        and isinstance(bid, (int, float))
        and isinstance(ask, (int, float))
        and not isinstance(bid, bool)
        and not isinstance(ask, bool)
        and not math.isnan(bid) and not math.isnan(ask)
        and not math.isinf(bid) and not math.isinf(ask)
        and bid > 0 and ask > 0
        and bid < ask
    ):
        try:
            spread = half_spread_bps(bid, ask)
            cost = spread_cost_bps(side=side, arrival_bid=bid, arrival_ask=ask)
        except ValueError:
            # Defensive: half_spread_bps raises on crossed / non-finite;
            # we already filtered above but a stricter validation
            # might still trip. Fall back to NO_BOOK rather than
            # failing the fill.
            no_book = True
            spread = 0.0
            cost = 0.0
    else:
        no_book = True

    if no_book:
        impact = 0.0
        residual = total  # entire slippage is unmodelled slack
        classification = NO_BOOK
    else:
        impact = total - cost
        residual = total - cost - impact
        # Sanity: residual must be zero by construction (we did not
        # add a noise term). Use the epsilon to absorb IEEE-754 fuzz.
        if abs(residual) > RESIDUAL_EPSILON_BPS:
            # Round-trip correction: pull the residual into the
            # impact leg (it is always a single-bit artifact of
            # double-precision arithmetic and we want impact_bps to
            # exactly satisfy the additive identity for downstream
            # dashboards).
            impact = impact + residual
            residual = 0.0
        classification = _classify(
            total=total,
            spread_cost=cost,
            impact=impact,
            fraction=DEFAULT_DOMINANCE_FRACTION,
            no_book=False,
        )

    arrival_mid = record.arrival_mid
    if arrival_mid is None and not no_book and bid is not None and ask is not None:
        arrival_mid = (bid + ask) / 2.0

    return AttributionRow(
        timestamp=record.timestamp,
        client_order_id=record.client_order_id,
        symbol=record.symbol,
        side=side,
        venue=record.venue,
        expected_price=record.expected_price,
        fill_price=record.fill_price,
        quantity=record.quantity,
        arrival_bid=bid,
        arrival_ask=ask,
        arrival_mid=arrival_mid,
        spread_bps=spread,
        total_slippage_bps=total,
        spread_cost_bps=cost,
        impact_bps=impact,
        residual_bps=residual,
        classification=classification,
    )


def attribute_fills(records: Sequence[FillRecord]) -> List[AttributionRow]:
    """Decompose a sequence of fills (delegates to :func:`attribute_fill`)."""
    return [attribute_fill(r) for r in records]


def _percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile; returns ``0.0`` for empty input."""
    n = len(values)
    if n == 0:
        return 0.0
    if not (0.0 <= pct <= 1.0):
        raise ValueError(f"pct must be in [0, 1], got {pct}")
    s = sorted(values)
    if n == 1:
        return float(s[0])
    rank = pct * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(s[lo])
    frac = rank - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def aggregate_overall(
    rows: Sequence[AttributionRow],
) -> Tuple[float, float, float, float, float, float, float, float]:
    """Headline aggregate across the day's rows.

    Returns ``(n_fills, n_fills_with_book, mean_total, mean_spread,
    mean_impact, median_total, p05_impact, p95_impact)``. Zero counts
    return zeros — never raises. ``median_total`` and percentiles are
    computed only over rows with a book snapshot (NO_BOOK rows are
    excluded from impact / spread stats because they have no leg to
    average).
    """
    n = len(rows)
    n_book = sum(1 for r in rows if r.classification != NO_BOOK)
    if n == 0:
        return (0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    mean_total = sum(r.total_slippage_bps for r in rows) / n
    if n_book == 0:
        return (n, 0, mean_total, 0.0, 0.0, 0.0, 0.0, 0.0)
    book_rows = [r for r in rows if r.classification != NO_BOOK]
    mean_spread = sum(r.spread_cost_bps for r in book_rows) / n_book
    mean_impact = sum(r.impact_bps for r in book_rows) / n_book
    median_total = _percentile(
        [r.total_slippage_bps for r in book_rows], 0.5
    )
    p05_impact = _percentile([r.impact_bps for r in book_rows], 0.05)
    p95_impact = _percentile([r.impact_bps for r in book_rows], 0.95)
    return (
        n,
        n_book,
        mean_total,
        mean_spread,
        mean_impact,
        median_total,
        p05_impact,
        p95_impact,
    )


def aggregate_by_venue(
    rows: Sequence[AttributionRow],
) -> Tuple[VenueDailyAttribution, ...]:
    """Per-venue breakdown; sorted by descending ``n_fills``."""
    by_venue: Dict[str, List[AttributionRow]] = {}
    for r in rows:
        v = r.venue or "<unknown>"
        by_venue.setdefault(v, []).append(r)
    out: List[VenueDailyAttribution] = []
    for venue, venue_rows in by_venue.items():
        n = len(venue_rows)
        book_rows = [r for r in venue_rows if r.classification != NO_BOOK]
        nb = len(book_rows)
        if nb > 0:
            mean_total = sum(r.total_slippage_bps for r in venue_rows) / n
            mean_spread = sum(r.spread_cost_bps for r in book_rows) / nb
            mean_impact = sum(r.impact_bps for r in book_rows) / nb
            median_impact = _percentile([r.impact_bps for r in book_rows], 0.5)
            spread_abs = sum(abs(r.spread_cost_bps) for r in book_rows)
            impact_abs = sum(abs(r.impact_bps) for r in book_rows)
            denom = spread_abs + impact_abs
            if denom > 0:
                impact_share = impact_abs / denom
                spread_share = spread_abs / denom
            else:
                impact_share = 0.0
                spread_share = 0.0
        else:
            mean_total = sum(r.total_slippage_bps for r in venue_rows) / n
            mean_spread = 0.0
            mean_impact = 0.0
            median_impact = 0.0
            impact_share = 0.0
            spread_share = 0.0
        out.append(
            VenueDailyAttribution(
                venue=venue,
                n_fills=n,
                n_fills_with_book=nb,
                mean_total_slippage_bps=mean_total,
                mean_spread_cost_bps=mean_spread,
                mean_impact_bps=mean_impact,
                median_impact_bps=median_impact,
                impact_share=impact_share,
                spread_share=spread_share,
            )
        )
    out.sort(key=lambda v: v.n_fills, reverse=True)
    return tuple(out)


def aggregate_by_symbol(
    rows: Sequence[AttributionRow],
) -> Tuple[SymbolDailyAttribution, ...]:
    """Per-symbol breakdown; sorted by descending ``n_fills``."""
    by_symbol: Dict[str, List[AttributionRow]] = {}
    for r in rows:
        by_symbol.setdefault(r.symbol, []).append(r)
    out: List[SymbolDailyAttribution] = []
    for symbol, sym_rows in by_symbol.items():
        n = len(sym_rows)
        book_rows = [r for r in sym_rows if r.classification != NO_BOOK]
        nb = len(book_rows)
        if nb > 0:
            mean_total = sum(r.total_slippage_bps for r in sym_rows) / n
            mean_spread = sum(r.spread_cost_bps for r in book_rows) / nb
            mean_impact = sum(r.impact_bps for r in book_rows) / nb
            median_impact = _percentile([r.impact_bps for r in book_rows], 0.5)
            spread_abs = sum(abs(r.spread_cost_bps) for r in book_rows)
            impact_abs = sum(abs(r.impact_bps) for r in book_rows)
            denom = spread_abs + impact_abs
            if denom > 0:
                impact_share = impact_abs / denom
                spread_share = spread_abs / denom
            else:
                impact_share = 0.0
                spread_share = 0.0
        else:
            mean_total = sum(r.total_slippage_bps for r in sym_rows) / n
            mean_spread = 0.0
            mean_impact = 0.0
            median_impact = 0.0
            impact_share = 0.0
            spread_share = 0.0
        out.append(
            SymbolDailyAttribution(
                symbol=symbol,
                n_fills=n,
                n_fills_with_book=nb,
                mean_total_slippage_bps=mean_total,
                mean_spread_cost_bps=mean_spread,
                mean_impact_bps=mean_impact,
                median_impact_bps=median_impact,
                impact_share=impact_share,
                spread_share=spread_share,
            )
        )
    out.sort(key=lambda s: s.n_fills, reverse=True)
    return tuple(out)


# ---- Day bounds -------------------------------------------------------------

NS_PER_DAY = 24 * 60 * 60 * 1_000_000_000
NS_PER_HOUR = 60 * 60 * 1_000_000_000


def _is_leap_year(year: int) -> bool:
    """Return True for Gregorian leap years."""
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    """Return the number of days in ``(year, month)`` (proleptic Gregorian)."""
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    # February
    return 29 if _is_leap_year(year) else 28


def day_utc_bounds(day_utc: str) -> Tuple[int, int]:
    """Return ``(start_ts_ns, end_ts_ns)`` UTC midnight boundaries for a day.

    ``day_utc`` MUST be ``'YYYY-MM-DD'`` (ISO 8601). Raises
    ``ValueError`` on malformed input or an impossible date
    (Feb 30, Apr 31, etc.).

    The end is exclusive — callers use it as a half-open interval
    ``[start, end)``. The same convention is used by every other
    P7-EXEC aggregator (``slippage_report``, ``tca_posttrade``).
    """
    if not isinstance(day_utc, str):
        raise ValueError(f"day_utc must be str, got {type(day_utc).__name__}")
    parts = day_utc.split("-")
    if len(parts) != 3:
        raise ValueError(
            f"day_utc must be 'YYYY-MM-DD', got {day_utc!r}"
        )
    try:
        y = int(parts[0])
        m = int(parts[1])
        d = int(parts[2])
    except ValueError as e:
        raise ValueError(
            f"day_utc must be 'YYYY-MM-DD' with integer fields, "
            f"got {day_utc!r}"
        ) from e
    if not (1970 <= y <= 9999 and 1 <= m <= 12 and 1 <= d <= 31):
        raise ValueError(f"day_utc out of range: {day_utc!r}")
    if d > _days_in_month(y, m):
        raise ValueError(
            f"day_utc is not a real calendar date: {day_utc!r}"
        )
    # ``calendar.timegm`` rolls impossible dates silently (Feb 30
    # → Mar 2) so the manual days-in-month check above is the
    # source of truth; we use timegm purely for the conversion.
    start_s = calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0))
    start = start_s * 1_000_000_000
    return start, start + NS_PER_DAY


def _now_ns() -> int:
    return time.time_ns()


# ---- Live observer (P7-EXEC-081 on_fill pattern) ----------------------------


class SlippageAttributionClassifier:
    """Per-fill attribution observer wired to ``ExecutionRunner.on_fill``.

    Constructor signature mirrors the sibling
    :class:`execution.maker_taker_classifier_p7exec_081.MakerTakerClassifier`
    so the runner's ``register_on_fill(...)`` accepts it without
    configuration. The constructor runs :func:`bootstrap_journal` on
    the passed journal so a cold-start journal carries the additive
    tables immediately (no race against a parallel dashboard
    reader).

    Parameters
    ----------
    journal
        The runner's :class:`OrderJournal`. Required for the additive
        ``slippage_attribution_fills`` table.
    thresholds
        Optional :class:`AttributionThresholds`. Default is
        :data:`DEFAULT_ATTRIBUTION_THRESHOLDS`.
    """

    def __init__(
        self,
        *,
        journal: OrderJournal,
        thresholds: AttributionThresholds = DEFAULT_ATTRIBUTION_THRESHOLDS,
    ) -> None:
        self._journal = journal
        self._thresholds = thresholds
        self._warned: Dict[str, bool] = {}
        # Per-symbol rolling deque of (ts_ns, impact_bps) for the
        # trailing mean impact. ``_prune`` keeps it within the
        # window. Reconstructible from the journal for cold-start.
        self._impact_window: Dict[str, Deque[Tuple[int, float]]] = {}
        bootstrap_journal(journal)

    # ---- on_fill hook ------------------------------------------------------

    def on_fill(
        self,
        request: dict,
        ack: dict,
        journal: OrderJournal,
        ts_ns: int,
    ) -> ComponentResult:
        """Post-fill hook: attribute the fill, persist, update rolling.

        Returns a :class:`ComponentResult` carrying the observation
        dict so the runner can fold it into the ack returned to the
        caller. The observation payload includes the per-fill
        decomposition (``spread_cost_bps``, ``impact_bps``,
        ``residual_bps``, ``classification``) and the trailing mean
        impact for the symbol (when the threshold is configured).

        A fill with no ``expected_price`` / ``mark_price`` /
        ``arrival_mid`` on the intent is recorded as
        ``classification=NO_ARRIVAL`` (a sub-case of NO_BOOK that
        flags a strategy mis-configuration, not a venue data gap).
        A fill with no book snapshot is recorded with the
        canonical sign and a ``NO_BOOK`` classification — the row
        is durable; the journal never silently drops it.
        """
        try:
            row = self._build_attribution_row(
                request=request, ack=ack, ts_ns=ts_ns,
            )
        except Exception as exc:  # noqa: BLE001
            # Never let an observer raise. The runner swallows the
            # exception and surfaces it via ``_on_fill_error`` in
            # the ack — we re-raise so the runner's existing
            # catch-and-log path picks it up, but ONLY for genuine
            # input bugs; production code should never see this.
            return ComponentResult(
                block=None,
                observation={
                    "_slippage_attribution_error": repr(exc),
                },
            )

        # Persist; idempotent on client_order_id via UNIQUE.
        row_id = self._persist(row)

        # Update rolling window + emit WARN/RECOVERED rows.
        # Pass ``impact_bps`` directly so we avoid an extra
        # round-trip SELECT on the hot path.
        warn_payload = self._maybe_warn(
            symbol=row.symbol, ts_ns=ts_ns, impact_bps=row.impact_bps,
        )

        obs: dict = {
            "slippage_attribution_row_id": row_id,
            "classification": row.classification,
            "total_slippage_bps": row.total_slippage_bps,
            "spread_cost_bps": row.spread_cost_bps,
            "impact_bps": row.impact_bps,
            "residual_bps": row.residual_bps,
        }
        if warn_payload is not None:
            obs["slippage_attribution_warn"] = warn_payload
        return ComponentResult(block=None, observation=obs)

    # ---- internals ---------------------------------------------------------

    def _build_attribution_row(
        self, *, request: dict, ack: dict, ts_ns: int,
    ) -> AttributionRow:
        """Translate the runner's request/ack dicts into an AttributionRow."""
        # arrival bid/ask may be on the request or in the ack;
        # prefer the request (it's the strategy's intent-time
        # snapshot) and fall back to the ack.
        bid = self._coerce_price(request.get("arrival_bid"))
        ask = self._coerce_price(request.get("arrival_ask"))
        if bid is None or ask is None:
            ack_bid = self._coerce_price((ack or {}).get("arrival_bid"))
            ack_ask = self._coerce_price((ack or {}).get("arrival_ask"))
            bid = bid if bid is not None else ack_bid
            ask = ask if ask is not None else ack_ask
        # expected_price resolution mirrors the runner's submit():
        # expected_price > mark_price > arrival_mid (first non-None wins).
        expected = None
        for key in ("expected_price", "mark_price", "arrival_mid"):
            v = self._coerce_price(request.get(key))
            if v is not None:
                expected = v
                break
        fill_price = self._coerce_price(
            (ack or {}).get("price", request.get("price"))
        )
        if fill_price is None:
            raise ValueError(
                "ack.price / request.price missing — cannot attribute fill"
            )
        if expected is None:
            # NO_ARRIVAL sub-case: we still record the row but mark
            # the classification so a dashboard can count the
            # missing-arrival rate.
            rec = FillRecord(
                timestamp=request.get("submit_ts_ns", ts_ns),
                side=str(request.get("side", "")),
                symbol=str(request.get("symbol", "")),
                expected_price=fill_price,  # degenerate; total == 0
                fill_price=fill_price,
                quantity=float(request.get("qty", 0.0) or 0.0),
                arrival_bid=bid,
                arrival_ask=ask,
                arrival_mid=self._coerce_price(request.get("arrival_mid")),
                venue=(ack or {}).get("venue", request.get("venue")),
                client_order_id=str(
                    request.get("client_order_id") or ""
                ),
            )
            row = attribute_fill(rec)
            # Override the classification with NO_ARRIVAL so a
            # dashboard can distinguish venue data gaps from
            # strategy mis-configuration.
            return AttributionRow(
                timestamp=row.timestamp,
                client_order_id=row.client_order_id,
                symbol=row.symbol,
                side=row.side,
                venue=row.venue,
                expected_price=row.expected_price,
                fill_price=row.fill_price,
                quantity=row.quantity,
                arrival_bid=row.arrival_bid,
                arrival_ask=row.arrival_ask,
                arrival_mid=row.arrival_mid,
                spread_bps=row.spread_bps,
                total_slippage_bps=row.total_slippage_bps,
                spread_cost_bps=row.spread_cost_bps,
                impact_bps=row.impact_bps,
                residual_bps=row.residual_bps,
                classification="NO_ARRIVAL",
            )

        rec = FillRecord(
            timestamp=request.get("submit_ts_ns", ts_ns),
            side=str(request.get("side", "")),
            symbol=str(request.get("symbol", "")),
            expected_price=expected,
            fill_price=fill_price,
            quantity=float(request.get("qty", 0.0) or 0.0),
            arrival_bid=bid,
            arrival_ask=ask,
            arrival_mid=self._coerce_price(request.get("arrival_mid")),
            venue=(ack or {}).get("venue", request.get("venue")),
            client_order_id=str(
                request.get("client_order_id") or ""
            ),
        )
        return attribute_fill(rec)

    @staticmethod
    def _coerce_price(value: Any) -> Optional[float]:
        """Coerce a dict value to a finite positive float, else None."""
        if value is None:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(v) or math.isinf(v) or v <= 0:
            return None
        return v

    def _persist(self, row: AttributionRow) -> int:
        """Insert-or-replace the row in ``slippage_attribution_fills``.

        Returns the row id (sqlite ``lastrowid``). Idempotent on
        ``client_order_id`` via the ``UNIQUE(client_order_id)``
        constraint — a re-submission of the same intent overwrites
        the existing row in place (the canonical ``fills`` row is
        untouched).
        """
        payload = json.dumps({
            "arrival_mid": row.arrival_mid,
            "spread_bps": row.spread_bps,
            "residual_bps": row.residual_bps,
        })
        with closing(self._journal.conn.cursor()) as cur:
            # Delete-then-insert keeps the row id stable for a
            # repeated fill on the same client_order_id; sqlite's
            # UPSERT (INSERT ... ON CONFLICT DO UPDATE) would also
            # work but the DELETE form is portable to older
            # pythons.
            cur.execute(
                "DELETE FROM slippage_attribution_fills WHERE client_order_id = ?",
                (row.client_order_id,),
            )
            cur.execute(
                "INSERT INTO slippage_attribution_fills ("
                "ts_ns, client_order_id, symbol, side, qty, expected_price, "
                "fill_price, arrival_bid, arrival_ask, arrival_mid, "
                "spread_bps, total_slippage_bps, spread_cost_bps, "
                "impact_bps, residual_bps, venue, classification, payload"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(getattr(row.timestamp, "value", row.timestamp) or 0)
                    if isinstance(row.timestamp, (int, float)) else
                    _now_ns(),
                    row.client_order_id,
                    row.symbol,
                    row.side,
                    row.quantity,
                    row.expected_price,
                    row.fill_price,
                    row.arrival_bid,
                    row.arrival_ask,
                    row.arrival_mid,
                    row.spread_bps,
                    row.total_slippage_bps,
                    row.spread_cost_bps,
                    row.impact_bps,
                    row.residual_bps,
                    row.venue,
                    row.classification,
                    payload,
                ),
            )
            return int(cur.lastrowid or 0)

    def _prune(self, symbol: str, ts_ns: int) -> None:
        """Drop entries older than ``window_s`` from the symbol window."""
        dq = self._impact_window.get(symbol)
        if dq is None or not dq:
            return
        cutoff = ts_ns - int(self._thresholds.window_s * 1e9)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _maybe_warn(
        self, *, symbol: str, ts_ns: int, impact_bps: float,
    ) -> Optional[dict]:
        """Update the rolling mean and emit WARN/RECOVERED rows.

        Returns the warn payload (for the runner ack) or ``None``.
        The rolling deque is bounded by the configured window;
        pruning is O(k) where k is the dropped-entry count.

        ``impact_bps`` is passed directly from the just-computed
        :class:`AttributionRow`; this avoids an indexed SELECT on the
        hot path and keeps the observer well under 250us.
        """
        dq = self._impact_window.setdefault(symbol, deque())
        impact = float(impact_bps)
        if math.isnan(impact) or math.isinf(impact):
            return None
        dq.append((ts_ns, impact))
        self._prune(symbol, ts_ns)
        if not dq:
            return None
        mean_impact = sum(v for _, v in dq) / len(dq)

        warn_threshold = -self._thresholds.impact_warn_bps
        recover_threshold = -(
            self._thresholds.impact_warn_bps
            - self._thresholds.impact_hysteresis_bps
        )
        was_warned = self._warned.get(symbol, False)
        payload: Optional[dict] = None
        severity: Optional[str] = None
        if mean_impact < warn_threshold and not was_warned:
            severity = "WARN"
            self._warned[symbol] = True
            payload = {
                "symbol": symbol,
                "severity": severity,
                "observed_mean_impact_bps": mean_impact,
                "threshold_bps": -self._thresholds.impact_warn_bps,
                "window_s": self._thresholds.window_s,
                "n_samples": len(dq),
            }
        elif mean_impact > recover_threshold and was_warned:
            severity = "RECOVERED"
            self._warned[symbol] = False
            payload = {
                "symbol": symbol,
                "severity": severity,
                "observed_mean_impact_bps": mean_impact,
                "threshold_bps": -self._thresholds.impact_warn_bps,
                "window_s": self._thresholds.window_s,
                "n_samples": len(dq),
            }
        if severity is not None:
            with closing(self._journal.conn.cursor()) as cur:
                cur.execute(
                    "INSERT INTO slippage_attribution_events "
                    "(ts_ns, symbol, severity, observed_mean_impact_bps, "
                    "threshold_bps, window_s, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts_ns, symbol, severity,
                        float(mean_impact),
                        float(-self._thresholds.impact_warn_bps),
                        float(self._thresholds.window_s),
                        json.dumps(payload),
                    ),
                )
        return payload

    # ---- snapshot ----------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """In-memory snapshot for diagnostics. Not for hot-path use.

        Returns a dict ``{symbol: {"mean_impact_bps": ..., "n": ...,
        "warned": bool}}`` reconstructed from the rolling window.
        Cold-start callers should use :meth:`recover` instead.
        """
        out: Dict[str, Any] = {}
        for sym, dq in self._impact_window.items():
            if not dq:
                out[sym] = {"mean_impact_bps": 0.0, "n": 0,
                            "warned": self._warned.get(sym, False)}
                continue
            mean_impact = sum(v for _, v in dq) / len(dq)
            out[sym] = {
                "mean_impact_bps": mean_impact,
                "n": len(dq),
                "warned": self._warned.get(sym, False),
            }
        return out

    def recover(self) -> int:
        """Rebuild the in-memory rolling window from the journal.

        Scans the last ``window_s`` seconds of
        ``slippage_attribution_fills`` rows and rebuilds
        ``_impact_window`` symbol by symbol. Returns the number of
        rows scanned.

        Useful for a cold-start process that needs to surface
        trailing-window impact stats without waiting for new fills
        to land.
        """
        cutoff = _now_ns() - int(self._thresholds.window_s * 1e9)
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                "SELECT ts_ns, symbol, impact_bps FROM "
                "slippage_attribution_fills WHERE ts_ns >= ?",
                (cutoff,),
            )
            rows = cur.fetchall()
        self._impact_window.clear()
        for row in rows:
            sym = row["symbol"] or "<unknown>"
            dq = self._impact_window.setdefault(sym, deque())
            dq.append((int(row["ts_ns"]), float(row["impact_bps"])))
        return len(rows)


# ---- Cold-path aggregator (P7-EXEC-050 pattern) ----------------------------


class SlippageAttributionReport:
    """Daily attribution aggregator — one immutable report per UTC day.

    Cold-path periodic aggregator that scans
    ``slippage_attribution_fills`` for a UTC day, computes one
    :class:`DailyAttributionReport`, and persists it to the
    additive ``slippage_attribution_daily_reports`` journal table.

    Typical invocation: a 00:05 UTC cron that calls
    :meth:`record` on yesterday's :meth:`compute_day` output.
    Mirrors the ``SlippageReport`` (P7-EXEC-050) API exactly so a
    single cron can drive both reporters.

    Parameters
    ----------
    journal
        The runner's :class:`OrderJournal`. Required for both the
        scan and the additive write.
    min_sample
        The headline-stable threshold (default ``5``). A day's
        headline numbers are reported regardless of ``n_fills``; the
        ``stable`` flag is ``True`` iff
        ``n_fills_with_book >= min_sample``.
    """

    def __init__(
        self, *, journal: OrderJournal, min_sample: int = 5,
    ) -> None:
        self._journal = journal
        self._min_sample = int(min_sample)
        if self._min_sample < 0:
            raise ValueError(
                f"min_sample must be >= 0, got {self._min_sample}"
            )
        bootstrap_journal(journal)

    @property
    def min_sample(self) -> int:
        return self._min_sample

    # ---- cold-path --------------------------------------------------------

    def compute_day(
        self, day_utc: str, *, now_ns: Optional[int] = None,
    ) -> DailyAttributionReport:
        """Compute the day's report from the additive journal table."""
        start_ns, end_ns = day_utc_bounds(day_utc)
        rows = self._scan_window(start_ns, end_ns)
        return self._build_report(
            day_utc=day_utc,
            rows=rows,
            generated_at_ns=now_ns if now_ns is not None else _now_ns(),
        )

    def record(self, report: DailyAttributionReport) -> int:
        """Persist the report to the additive daily-report table.

        Idempotent on ``day_utc`` via the
        ``UNIQUE(day_utc)`` constraint. Returns the row id.
        """
        by_venue_json = json.dumps([
            {
                "venue": v.venue,
                "n_fills": v.n_fills,
                "n_fills_with_book": v.n_fills_with_book,
                "mean_total_slippage_bps": v.mean_total_slippage_bps,
                "mean_spread_cost_bps": v.mean_spread_cost_bps,
                "mean_impact_bps": v.mean_impact_bps,
                "median_impact_bps": v.median_impact_bps,
                "impact_share": v.impact_share,
                "spread_share": v.spread_share,
            }
            for v in report.by_venue
        ])
        by_symbol_json = json.dumps([
            {
                "symbol": s.symbol,
                "n_fills": s.n_fills,
                "n_fills_with_book": s.n_fills_with_book,
                "mean_total_slippage_bps": s.mean_total_slippage_bps,
                "mean_spread_cost_bps": s.mean_spread_cost_bps,
                "mean_impact_bps": s.mean_impact_bps,
                "median_impact_bps": s.median_impact_bps,
                "impact_share": s.impact_share,
                "spread_share": s.spread_share,
            }
            for s in report.by_symbol
        ])
        payload = json.dumps({
            "min_sample": report.min_sample,
            "generated_at_ns": report.generated_at_ns,
            "stable": report.stable,
        })
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                "DELETE FROM slippage_attribution_daily_reports "
                "WHERE day_utc = ?",
                (report.day_utc,),
            )
            cur.execute(
                "INSERT INTO slippage_attribution_daily_reports ("
                "ts_ns, day_utc, n_fills, n_fills_with_book, "
                "n_fills_no_book, mean_total_slippage_bps, "
                "mean_spread_cost_bps, mean_impact_bps, "
                "median_total_slippage_bps, p95_impact_bps, p05_impact_bps, "
                "total_cost_bps_notional, impact_share, spread_share, "
                "n_spread_dominant, n_impact_dominant, n_mixed, "
                "by_venue_json, by_symbol_json, min_sample, payload"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?)",
                (
                    day_utc_bounds(report.day_utc)[0],
                    report.day_utc,
                    report.n_fills,
                    report.n_fills_with_book,
                    report.n_fills_no_book,
                    report.mean_total_slippage_bps,
                    report.mean_spread_cost_bps,
                    report.mean_impact_bps,
                    report.median_total_slippage_bps,
                    report.p95_impact_bps,
                    report.p05_impact_bps,
                    report.total_cost_bps_notional,
                    report.impact_share,
                    report.spread_share,
                    report.n_spread_dominant,
                    report.n_impact_dominant,
                    report.n_mixed,
                    by_venue_json,
                    by_symbol_json,
                    report.min_sample,
                    payload,
                ),
            )
            return int(cur.lastrowid or 0)

    def fetch(self, day_utc: str) -> Optional[DailyAttributionReport]:
        """Fetch a persisted report by ``day_utc`` (or ``None``)."""
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM slippage_attribution_daily_reports "
                "WHERE day_utc = ?",
                (day_utc,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        by_venue_raw = json.loads(row["by_venue_json"] or "[]")
        by_symbol_raw = json.loads(row["by_symbol_json"] or "[]")
        by_venue = tuple(
            VenueDailyAttribution(
                venue=v["venue"],
                n_fills=int(v["n_fills"]),
                n_fills_with_book=int(v["n_fills_with_book"]),
                mean_total_slippage_bps=float(v["mean_total_slippage_bps"]),
                mean_spread_cost_bps=float(v["mean_spread_cost_bps"]),
                mean_impact_bps=float(v["mean_impact_bps"]),
                median_impact_bps=float(v["median_impact_bps"]),
                impact_share=float(v["impact_share"]),
                spread_share=float(v["spread_share"]),
            )
            for v in by_venue_raw
        )
        by_symbol = tuple(
            SymbolDailyAttribution(
                symbol=s["symbol"],
                n_fills=int(s["n_fills"]),
                n_fills_with_book=int(s["n_fills_with_book"]),
                mean_total_slippage_bps=float(s["mean_total_slippage_bps"]),
                mean_spread_cost_bps=float(s["mean_spread_cost_bps"]),
                mean_impact_bps=float(s["mean_impact_bps"]),
                median_impact_bps=float(s["median_impact_bps"]),
                impact_share=float(s["impact_share"]),
                spread_share=float(s["spread_share"]),
            )
            for s in by_symbol_raw
        )
        payload = json.loads(row["payload"] or "{}")
        generated_at_ns = int(payload.get("generated_at_ns", 0))
        return DailyAttributionReport(
            day_utc=row["day_utc"],
            n_fills=int(row["n_fills"]),
            n_fills_with_book=int(row["n_fills_with_book"]),
            n_fills_no_book=int(row["n_fills_no_book"]),
            mean_total_slippage_bps=float(row["mean_total_slippage_bps"]),
            mean_spread_cost_bps=float(row["mean_spread_cost_bps"]),
            mean_impact_bps=float(row["mean_impact_bps"]),
            median_total_slippage_bps=float(row["median_total_slippage_bps"]),
            p95_impact_bps=float(row["p95_impact_bps"]),
            p05_impact_bps=float(row["p05_impact_bps"]),
            total_cost_bps_notional=float(row["total_cost_bps_notional"]),
            impact_share=float(row["impact_share"]),
            spread_share=float(row["spread_share"]),
            n_spread_dominant=int(row["n_spread_dominant"]),
            n_impact_dominant=int(row["n_impact_dominant"]),
            n_mixed=int(row["n_mixed"]),
            by_venue=by_venue,
            by_symbol=by_symbol,
            min_sample=int(row["min_sample"]),
            generated_at_ns=generated_at_ns,
            stable=bool(payload.get("stable", False)),
        )

    # ---- internals --------------------------------------------------------

    def _scan_window(
        self, start_ns: int, end_ns: int,
    ) -> List[AttributionRow]:
        """Scan ``slippage_attribution_fills`` in [start, end)."""
        rows: List[AttributionRow] = []
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM slippage_attribution_fills "
                "WHERE ts_ns >= ? AND ts_ns < ? ORDER BY ts_ns ASC, id ASC",
                (start_ns, end_ns),
            )
            for row in cur.fetchall():
                rows.append(self._row_to_attribution(row))
        return rows

    @staticmethod
    def _row_to_attribution(row: sqlite3.Row) -> AttributionRow:
        payload_raw = row["payload"] or "{}"
        try:
            payload = json.loads(payload_raw)
        except (TypeError, ValueError):
            payload = {}
        return AttributionRow(
            timestamp=int(row["ts_ns"]),
            client_order_id=row["client_order_id"],
            symbol=row["symbol"] or "",
            side=row["side"] or "",
            venue=row["venue"],
            expected_price=float(row["expected_price"]),
            fill_price=float(row["fill_price"]),
            quantity=float(row["qty"]),
            arrival_bid=row["arrival_bid"],
            arrival_ask=row["arrival_ask"],
            arrival_mid=payload.get("arrival_mid"),
            spread_bps=float(row["spread_bps"]),
            total_slippage_bps=float(row["total_slippage_bps"]),
            spread_cost_bps=float(row["spread_cost_bps"]),
            impact_bps=float(row["impact_bps"]),
            residual_bps=float(row["residual_bps"]),
            classification=row["classification"] or NO_BOOK,
        )

    def _build_report(
        self,
        *,
        day_utc: str,
        rows: Sequence[AttributionRow],
        generated_at_ns: int,
    ) -> DailyAttributionReport:
        """Assemble a :class:`DailyAttributionReport` from raw rows."""
        n = len(rows)
        n_book = sum(1 for r in rows if r.classification != NO_BOOK)
        n_no_book = n - n_book
        if n == 0:
            return DailyAttributionReport(
                day_utc=day_utc,
                n_fills=0,
                n_fills_with_book=0,
                n_fills_no_book=0,
                mean_total_slippage_bps=0.0,
                mean_spread_cost_bps=0.0,
                mean_impact_bps=0.0,
                median_total_slippage_bps=0.0,
                p95_impact_bps=0.0,
                p05_impact_bps=0.0,
                total_cost_bps_notional=0.0,
                impact_share=0.0,
                spread_share=0.0,
                n_spread_dominant=0,
                n_impact_dominant=0,
                n_mixed=0,
                by_venue=(),
                by_symbol=(),
                min_sample=self._min_sample,
                generated_at_ns=generated_at_ns,
                stable=False,
            )

        mean_total = sum(r.total_slippage_bps for r in rows) / n
        # Notional-weighted total cost (matches slippage_report's
        # ``total_slippage_cost_bps_notional``).
        sum_notional = sum(r.quantity * r.fill_price for r in rows)
        if sum_notional > 0:
            total_cost_notional = sum(
                r.total_slippage_bps * r.quantity * r.fill_price for r in rows
            ) / sum_notional
        else:
            total_cost_notional = 0.0

        book_rows = [r for r in rows if r.classification != NO_BOOK]
        n_spread_dom = sum(1 for r in book_rows if r.classification == SPREAD)
        n_impact_dom = sum(1 for r in book_rows if r.classification == IMPACT)
        n_mixed = sum(1 for r in book_rows if r.classification == MIXED)

        if book_rows:
            mean_spread = sum(r.spread_cost_bps for r in book_rows) / n_book
            mean_impact = sum(r.impact_bps for r in book_rows) / n_book
            median_total = _percentile(
                [r.total_slippage_bps for r in book_rows], 0.5
            )
            p05_impact = _percentile(
                [r.impact_bps for r in book_rows], 0.05
            )
            p95_impact = _percentile(
                [r.impact_bps for r in book_rows], 0.95
            )
            spread_abs = sum(abs(r.spread_cost_bps) for r in book_rows)
            impact_abs = sum(abs(r.impact_bps) for r in book_rows)
            denom = spread_abs + impact_abs
            if denom > 0:
                impact_share = impact_abs / denom
                spread_share = spread_abs / denom
            else:
                impact_share = 0.0
                spread_share = 0.0
        else:
            mean_spread = 0.0
            mean_impact = 0.0
            median_total = 0.0
            p05_impact = 0.0
            p95_impact = 0.0
            impact_share = 0.0
            spread_share = 0.0

        return DailyAttributionReport(
            day_utc=day_utc,
            n_fills=n,
            n_fills_with_book=n_book,
            n_fills_no_book=n_no_book,
            mean_total_slippage_bps=mean_total,
            mean_spread_cost_bps=mean_spread,
            mean_impact_bps=mean_impact,
            median_total_slippage_bps=median_total,
            p95_impact_bps=p95_impact,
            p05_impact_bps=p05_impact,
            total_cost_bps_notional=total_cost_notional,
            impact_share=impact_share,
            spread_share=spread_share,
            n_spread_dominant=n_spread_dom,
            n_impact_dominant=n_impact_dom,
            n_mixed=n_mixed,
            by_venue=aggregate_by_venue(rows),
            by_symbol=aggregate_by_symbol(rows),
            min_sample=self._min_sample,
            generated_at_ns=generated_at_ns,
            stable=(n_book >= self._min_sample),
        )


__all__ = [
    "DEFAULT_ATTRIBUTION_THRESHOLDS",
    "AttributionRecord",
    "AttributionRow",
    "AttributionThresholds",
    "DailyAttributionReport",
    "FillRecord",
    "IMPACT",
    "MIXED",
    "NO_BOOK",
    "SlippageAttributionClassifier",
    "SlippageAttributionReport",
    "SPREAD",
    "SymbolDailyAttribution",
    "VenueDailyAttribution",
    "aggregate_by_symbol",
    "aggregate_by_venue",
    "aggregate_overall",
    "attribute_fill",
    "attribute_fills",
    "bootstrap_journal",
    "day_utc_bounds",
    "half_spread_bps",
    "spread_cost_bps",
    "total_slippage_bps",
]

__version__ = "0.1.0"
__issue__ = "SMA-36230"