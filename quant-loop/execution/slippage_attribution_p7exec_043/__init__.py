"""slippage_attribution — P7-EXEC-043.

Decomposes every exchange fill's total slippage (vs the strategy's
arrival-time reference) into two signed components:

    total_slippage_bps = spread_cost_bps + impact_bps + residual_bps

* :attr:`spread_cost_bps` — the half-spread the trader pays to
  cross the book; captured from the intent's ``arrival_bid`` /
  ``arrival_ask`` snapshot.
* :attr:`impact_bps` — the residual movement beyond the half-spread.
  The per-fill proxy for queue-priority loss + depth consumption +
  slow-market latency.
* :attr:`residual_bps` — the unmodelled slack. Zero by construction
  for the two-leg decomposition; reserved for future model
  extensions (e.g. Almgren-Chriss impact).

Sign convention matches the canonical
``venue_fill_quality_p7exec_080.slippage_bps``:

* Positive = price improvement.
* Negative = slippage paid.

Why
---
``slippage_report_p7exec_050`` answers "how much did we bleed this
week?" — a single signed number per day. This component answers
the *why*: how much of that bleed came from spread crossing
(structural, venue-driven) vs how much came from impact
(execution-policy-driven). A quant who watches ``impact_bps`` drop
while ``spread_cost_bps`` stays flat has improved the algorithm
without moving venues; one who watches the spread leg swing needs
to re-shop the venue or add maker legs.

The component is a hybrid live-observer + cold-path aggregator:

* :class:`SlippageAttributionClassifier` — live observer wired to
  :class:`execution.runner.ExecutionRunner.on_fill` (additive
  P7-EXEC-081 pattern, same as
  :class:`execution.maker_taker_classifier_p7exec_081.MakerTakerClassifier`).
  Persists one row per fill to the additive
  ``slippage_attribution_fills`` table and emits IMPACT-WARN /
  RECOVERED observations.
* :class:`SlippageAttributionReport` — cold-path periodic aggregator
  (additive P7-EXEC-050 pattern, same as
  :class:`execution.slippage_report_p7exec_050.SlippageReport`).
  Computes one :class:`DailyAttributionReport` per UTC day,
  persists to ``slippage_attribution_daily_reports``.

Folder convention: ``slippage_attribution_p7exec_043/`` per the
MAP-P7 Live Trading Infrastructure project rule (suffix
``_p7exec_NNN``, never ``_v1`` / ``_v2``).

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
  :func:`total_slippage_bps`,
  :func:`attribute_fill`, :func:`attribute_fills` — pure functions.
* :func:`aggregate_by_venue`, :func:`aggregate_by_symbol`,
  :func:`aggregate_overall` — analytics.
* :func:`day_utc_bounds` — UTC calendar day boundaries.
* :func:`bootstrap_journal` — idempotent schema bootstrap.
* :class:`SlippageAttributionClassifier` — runner-wired live
  observer.
* :class:`SlippageAttributionReport` — cold-path periodic
  aggregator.
* :data:`SPREAD`, :data:`IMPACT`, :data:`MIXED`, :data:`NO_BOOK` —
  dominant-leg classification labels.

See :mod:`execution.slippage_attribution_p7exec_043.slippage_attribution`
for the implementation, ``README.md`` for the spec,
``INTERFACE.md`` for the wire contract, and ``SPEC.md`` for the
extended design doc.
"""
from .slippage_attribution import (
    DEFAULT_ATTRIBUTION_THRESHOLDS,
    IMPACT,
    MIXED,
    NO_BOOK,
    SPREAD,
    AttributionRecord,
    AttributionRow,
    AttributionThresholds,
    DailyAttributionReport,
    FillRecord,
    SlippageAttributionClassifier,
    SlippageAttributionReport,
    SymbolDailyAttribution,
    VenueDailyAttribution,
    aggregate_by_symbol,
    aggregate_by_venue,
    aggregate_overall,
    attribute_fill,
    attribute_fills,
    bootstrap_journal,
    day_utc_bounds,
    half_spread_bps,
    spread_cost_bps,
    total_slippage_bps,
)

__all__ = [
    # Constants
    "SPREAD", "IMPACT", "MIXED", "NO_BOOK",
    # Types
    "FillRecord", "AttributionRow", "AttributionRecord",
    "DailyAttributionReport", "VenueDailyAttribution",
    "SymbolDailyAttribution",
    # Thresholds
    "AttributionThresholds", "DEFAULT_ATTRIBUTION_THRESHOLDS",
    # Pure functions
    "half_spread_bps", "spread_cost_bps", "total_slippage_bps",
    "attribute_fill", "attribute_fills",
    "aggregate_overall", "aggregate_by_venue", "aggregate_by_symbol",
    "day_utc_bounds",
    # Bootstrap + components
    "bootstrap_journal",
    "SlippageAttributionClassifier",
    "SlippageAttributionReport",
]

__version__ = "0.1.0"
__issue__ = "SMA-36230"