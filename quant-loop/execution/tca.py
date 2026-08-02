"""tca — execution-quality transaction cost analysis (E20).

Extended, offline companion to
:mod:`execution.slippage_attribution_p7exec_043.slippage_attribution`
(read that module first — the sign convention and the
spread/impact split are mirrored here).  Where the sibling answers
"how much did each fill bleed vs its arrival reference?", this
module answers the broader TCA question across **two benchmarks**
and **two liquidity roles**:

Benchmarks
----------
* **arrival price** — the strategy's decision-time reference
  (Berkowitz, Logue & Noser 1988: the price at the moment the
  investment decision was made).  ``arrival_slippage_bps`` measures
  everything the execution layer owns.
* **interval VWAP** — the volume-weighted average price over the
  order's working interval.  ``vwap_slippage_bps`` separates "we
  executed badly" from "the market drifted while we worked" — a
  fill can beat the interval VWAP and still lose to arrival (slow
  slicing in a trending market).

Decomposition (per fill)
------------------------
::

    arrival_slippage_bps = spread_cost_bps + impact_bps
    vwap_slippage_bps    = spread_cost_bps + impact_bps + residual_bps

* ``spread_cost_bps`` — the half-spread leg, **liquidity-role
  aware**: a taker pays the half-spread (negative); a maker
  *earns* it (positive — posting at the bid/ask captures the
  spread a marketable order would have paid).
* ``impact_bps`` — the remainder vs arrival: queue-priority loss,
  depth consumption, latency drift inside the interval.
* ``residual_bps`` — the arrival→interval timing drift
  (``vwap_slippage_bps - arrival_slippage_bps``).  Zero when no
  interval VWAP is supplied; with one, the three legs sum exactly
  to the VWAP-benchmark slippage (additive-exact identity, pinned
  by tests).

Sign convention (identical to the sibling): **positive = price
improvement for the trader, negative = cost paid**.

Bucketing
---------
:func:`bucket_by_liquidity` aggregates rows into maker / taker
buckets (count, qty, qty-weighted and plain mean bps, median,
p90-cost) so the strategy can answer "do we bleed on taker legs
and earn on maker legs?" — the maker/taker split the sibling
leaves to ``maker_taker_classifier_p7exec_081``.

References
----------
- Berkowitz, Logue & Noser (1988), "The Total Cost of Transactions
  on the NYSE" — arrival-price (implementation-shortfall) benchmark.
- Kissell & Glantz (2003), "Optimal Trading Strategies" — VWAP
  benchmarking and cost decomposition.
- Almgren & Chriss (2000) — impact vs timing-risk separation.
- Cartea, Jaimungal & Penalva (2015), Ch. 6 — maker spread capture.

Pure functions + frozen dataclasses; no I/O, no runner dependency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "BUCKET_MAKER",
    "BUCKET_TAKER",
    "BucketStats",
    "TCAFill",
    "TCAReport",
    "TCARow",
    "aggregate",
    "arrival_slippage_bps",
    "bucket_by_liquidity",
    "decompose_fill",
    "decompose_fills",
    "vwap_slippage_bps",
]

BUCKET_MAKER = "maker"
BUCKET_TAKER = "taker"

_EPS_BPS = 1e-9


# ---------------------------------------------------------------------------
# Validation helpers (mirrors slippage_attribution's discipline)
# ---------------------------------------------------------------------------


def _validate_side(side: str) -> str:
    if not isinstance(side, str):
        raise ValueError(f"side must be str, got {type(side).__name__}")
    s = side.strip().upper()
    if s not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    return s


def _validate_price(label: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(
            f"{label} must be a real number, got {type(value).__name__}"
        )
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{label} must be finite, got {value!r}")
    if value <= 0:
        raise ValueError(f"{label} must be > 0, got {value}")


# ---------------------------------------------------------------------------
# Benchmark slippage (pure)
# ---------------------------------------------------------------------------


def arrival_slippage_bps(
    *,
    side: str,
    fill_price: float,
    arrival_price: float,
) -> float:
    """Signed slippage vs the arrival (decision-time) price, in bps.

    Positive = improvement.  BUY: ``(arrival - fill)/arrival``;
    SELL: ``(fill - arrival)/arrival``.
    """
    _validate_side(side)
    _validate_price("arrival_price", arrival_price)
    _validate_price("fill_price", fill_price)
    if side.strip().upper() == "BUY":
        return (arrival_price - fill_price) / arrival_price * 10_000.0
    return (fill_price - arrival_price) / arrival_price * 10_000.0


def vwap_slippage_bps(
    *,
    side: str,
    fill_price: float,
    interval_vwap: float,
) -> float:
    """Signed slippage vs the interval VWAP benchmark, in bps.

    Same sign convention as :func:`arrival_slippage_bps`.
    """
    _validate_side(side)
    _validate_price("interval_vwap", interval_vwap)
    _validate_price("fill_price", fill_price)
    if side.strip().upper() == "BUY":
        return (interval_vwap - fill_price) / interval_vwap * 10_000.0
    return (fill_price - interval_vwap) / interval_vwap * 10_000.0


def _half_spread_bps(arrival_bid: float, arrival_ask: float) -> float:
    _validate_price("arrival_bid", arrival_bid)
    _validate_price("arrival_ask", arrival_ask)
    if arrival_bid >= arrival_ask:
        raise ValueError(
            f"crossed book: arrival_bid={arrival_bid} >= "
            f"arrival_ask={arrival_ask}"
        )
    mid = (arrival_bid + arrival_ask) / 2.0
    return (arrival_ask - arrival_bid) / mid * 10_000.0 / 2.0


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TCAFill:
    """One fill to analyse.

    ``arrival_price``    decision-time reference price (required).
    ``arrival_bid`` / ``arrival_ask``
                         book snapshot at arrival; both required for
                         the spread leg (a fill without a book
                         attributes everything to impact).
    ``interval_vwap``    VWAP over the order's working interval;
                         enables the VWAP benchmark and the residual
                         (timing-drift) leg.
    ``is_maker``         True for passive (posted) fills.
    """

    order_id: str
    ts_ns: int
    symbol: str
    side: str
    qty: float
    price: float
    arrival_price: float
    is_maker: bool = False
    arrival_bid: Optional[float] = None
    arrival_ask: Optional[float] = None
    interval_vwap: Optional[float] = None
    venue: Optional[str] = None


@dataclass(frozen=True)
class TCARow:
    """Signed per-fill decomposition.  All bps fields: positive =
    improvement, negative = cost."""

    order_id: str
    symbol: str
    side: str
    is_maker: bool
    qty: float
    price: float
    arrival_slippage_bps: float
    vwap_slippage_bps: Optional[float]
    spread_cost_bps: float
    impact_bps: float
    residual_bps: float


# ---------------------------------------------------------------------------
# Decomposition (pure)
# ---------------------------------------------------------------------------


def decompose_fill(fill: TCAFill) -> TCARow:
    """Decompose one fill into spread + impact (+ residual) legs.

    Additive identities (pinned by tests):

    * ``spread_cost_bps + impact_bps == arrival_slippage_bps``
      (within 1e-9 bps);
    * when ``interval_vwap`` is present,
      ``spread_cost_bps + impact_bps + residual_bps
      == vwap_slippage_bps`` and
      ``residual_bps == vwap_slippage_bps - arrival_slippage_bps``.

    Raises ``ValueError`` on malformed input (bad side, non-positive
    prices / qty, crossed book).
    """
    side = _validate_side(fill.side)
    _validate_price("price", fill.price)
    _validate_price("arrival_price", fill.arrival_price)
    if not isinstance(fill.qty, (int, float)) or isinstance(fill.qty, bool):
        raise ValueError("qty must be a real number")
    if math.isnan(fill.qty) or math.isinf(fill.qty) or fill.qty <= 0:
        raise ValueError(f"qty must be positive and finite, got {fill.qty!r}")

    total = arrival_slippage_bps(
        side=side, fill_price=fill.price,
        arrival_price=fill.arrival_price,
    )
    vwap_slip: Optional[float] = None
    if fill.interval_vwap is not None:
        vwap_slip = vwap_slippage_bps(
            side=side, fill_price=fill.price,
            interval_vwap=fill.interval_vwap,
        )

    has_book = fill.arrival_bid is not None and fill.arrival_ask is not None
    if has_book:
        half = _half_spread_bps(fill.arrival_bid, fill.arrival_ask)  # type: ignore[arg-type]
        # Liquidity-role aware: makers earn the half-spread, takers
        # pay it.
        spread_cost = half if fill.is_maker else -half
        impact = total - spread_cost
    else:
        # No book snapshot: everything is impact (the sibling calls
        # this the NO_BOOK bucket; here the identity is preserved by
        # attributing the whole total to the impact leg).
        spread_cost = 0.0
        impact = total

    residual = (
        (vwap_slip - total) if vwap_slip is not None else 0.0
    )
    return TCARow(
        order_id=fill.order_id,
        symbol=fill.symbol,
        side=side,
        is_maker=fill.is_maker,
        qty=fill.qty,
        price=fill.price,
        arrival_slippage_bps=total,
        vwap_slippage_bps=vwap_slip,
        spread_cost_bps=spread_cost,
        impact_bps=impact,
        residual_bps=residual,
    )


def decompose_fills(fills: Sequence[TCAFill]) -> List[TCARow]:
    """Vector form of :func:`decompose_fill` (order-preserving)."""
    return [decompose_fill(f) for f in fills]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile on a sorted copy."""
    if not values:
        return float("nan")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = pct / 100.0 * (len(s) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return s[lo]
    frac = rank - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


@dataclass(frozen=True)
class BucketStats:
    """Aggregate execution-quality stats for one liquidity bucket.

    ``p90_cost_bps`` is the 90th percentile of *cost* (i.e. of
    ``-arrival_slippage_bps``), so larger is always worse regardless
    of sign convention.
    """

    bucket: str
    n_fills: int
    total_qty: float
    qty_weighted_arrival_bps: float
    mean_arrival_bps: float
    median_arrival_bps: float
    p90_cost_bps: float
    mean_spread_cost_bps: float
    mean_impact_bps: float
    mean_residual_bps: float
    mean_vwap_bps: Optional[float]   # None when no row carried a VWAP

    def to_dict(self) -> Dict[str, object]:
        return {
            "bucket": self.bucket,
            "n_fills": self.n_fills,
            "total_qty": self.total_qty,
            "qty_weighted_arrival_bps": self.qty_weighted_arrival_bps,
            "mean_arrival_bps": self.mean_arrival_bps,
            "median_arrival_bps": self.median_arrival_bps,
            "p90_cost_bps": self.p90_cost_bps,
            "mean_spread_cost_bps": self.mean_spread_cost_bps,
            "mean_impact_bps": self.mean_impact_bps,
            "mean_residual_bps": self.mean_residual_bps,
            "mean_vwap_bps": self.mean_vwap_bps,
        }


def _summarise(bucket: str, rows: Sequence[TCARow]) -> BucketStats:
    if not rows:
        return BucketStats(
            bucket=bucket, n_fills=0, total_qty=0.0,
            qty_weighted_arrival_bps=float("nan"),
            mean_arrival_bps=float("nan"),
            median_arrival_bps=float("nan"),
            p90_cost_bps=float("nan"),
            mean_spread_cost_bps=float("nan"),
            mean_impact_bps=float("nan"),
            mean_residual_bps=float("nan"),
            mean_vwap_bps=None,
        )
    totals = [r.arrival_slippage_bps for r in rows]
    qtys = [r.qty for r in rows]
    total_qty = sum(qtys)
    qw = (
        sum(t * q for t, q in zip(totals, qtys)) / total_qty
        if total_qty > 0 else float("nan")
    )
    vwap_vals = [
        r.vwap_slippage_bps for r in rows
        if r.vwap_slippage_bps is not None
    ]
    return BucketStats(
        bucket=bucket,
        n_fills=len(rows),
        total_qty=total_qty,
        qty_weighted_arrival_bps=qw,
        mean_arrival_bps=sum(totals) / len(totals),
        median_arrival_bps=float(median(totals)),
        p90_cost_bps=_percentile([-t for t in totals], 90.0),
        mean_spread_cost_bps=(
            sum(r.spread_cost_bps for r in rows) / len(rows)
        ),
        mean_impact_bps=sum(r.impact_bps for r in rows) / len(rows),
        mean_residual_bps=sum(r.residual_bps for r in rows) / len(rows),
        mean_vwap_bps=(
            sum(vwap_vals) / len(vwap_vals) if vwap_vals else None
        ),
    )


def bucket_by_liquidity(
    rows: Sequence[TCARow],
) -> Tuple[BucketStats, BucketStats]:
    """Split rows into ``(maker, taker)`` bucket stats."""
    maker_rows = [r for r in rows if r.is_maker]
    taker_rows = [r for r in rows if not r.is_maker]
    return (
        _summarise(BUCKET_MAKER, maker_rows),
        _summarise(BUCKET_TAKER, taker_rows),
    )


@dataclass(frozen=True)
class TCAReport:
    """Full TCA report: overall stats + maker/taker breakdown."""

    overall: BucketStats
    maker: BucketStats
    taker: BucketStats

    def to_dict(self) -> Dict[str, object]:
        return {
            "overall": self.overall.to_dict(),
            "maker": self.maker.to_dict(),
            "taker": self.taker.to_dict(),
        }


def aggregate(rows: Sequence[TCARow]) -> TCAReport:
    """Build a :class:`TCAReport` from decomposed rows."""
    maker, taker = bucket_by_liquidity(rows)
    return TCAReport(
        overall=_summarise("overall", list(rows)),
        maker=maker,
        taker=taker,
    )
