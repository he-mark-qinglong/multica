"""Cross-venue quoting and arbitrage-edge analytics.

Given the top-of-book of two venues (A = the quoting venue, B = the
reference/hedge venue), compute:

  - the signed cross-venue spread in basis points,
  - the taker arbitrage edge in both directions **net of both sides'
    fees** (buy A → sell B, and buy B → sell A),
  - a passive quote offset on venue A: the bid is anchored to B's ask,
    net of A's maker fee, B's hedge (taker) fee, and a safety buffer —
    i.e. "bid on A such that if filled, hedging at B's ask still clears
    the buffer". Symmetrically for the ask anchored to B's bid.

Break-even algebra (buy on A at price P, sell on B at ``b.ask``):

    profit/unit = b.ask * (1 - f_b_taker) - P * (1 + f_a_maker)  ≥  buffer
    ⇒  P* = b.ask * (1 - f_b_taker - buffer) / (1 + f_a_maker)

P* is then floored to the tick grid (never rounded up — rounding up
would eat the buffer). The ask side mirrors this with ceiling rounding.

References
----------
  - Makarov & Schoar (2020), "Trading and arbitrage in cryptocurrency
    markets", Journal of Financial Economics 135(2) — persistent
    cross-exchange price deviations in crypto markets.
  - Foucault, Pagano & Röell (2013), *Market Liquidity*, ch. 7 —
    liquidity and price discovery across fragmented markets.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BookTicker:
    """Top-of-book snapshot for one venue."""

    venue: str
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    timestamp: float


@dataclass(frozen=True)
class VenueFees:
    """Fee schedule in basis points (positive = cost, negative = rebate)."""

    maker_bp: float = 0.0
    taker_bp: float = 4.0


@dataclass(frozen=True)
class CrossVenueQuote:
    """Cross-venue analytics + passive quote offsets for venue A.

    ``quote_bid_a`` / ``quote_ask_a`` are None when either book is invalid
    (crossed or non-positive prices). ``edge_at_quote_bp`` is the expected
    edge (bp) if ``quote_bid_a`` fills and the hedge executes at B's ask.
    """

    venue_a: str
    venue_b: str
    spread_bp: float               # (a.ask - b.bid) / mid * 1e4, signed
    arb_buy_a_sell_b_bp: float     # taker edge net of fees, both directions
    arb_buy_b_sell_a_bp: float
    profitable: bool               # either taker direction nets > 0
    quote_bid_a: float | None
    quote_ask_a: float | None
    edge_at_quote_bp: float | None


def _valid(book: BookTicker) -> bool:
    return (
        book.bid_price > 0.0
        and book.ask_price > 0.0
        and book.ask_price > book.bid_price
    )


def cross_spread_bp(a: BookTicker, b: BookTicker) -> float:
    """Signed spread of A over B in bp: positive ⇒ A's ask is richer."""
    mid = (a.ask_price + b.bid_price) / 2.0
    return (a.ask_price - b.bid_price) / mid * 10_000.0


def taker_edge_bp(
    buy: BookTicker,
    sell: BookTicker,
    buy_fees: VenueFees,
    sell_fees: VenueFees,
) -> float:
    """Net taker edge (bp) of buying ``buy``'s ask and selling ``sell``'s bid."""
    mid = (buy.ask_price + sell.bid_price) / 2.0
    proceeds = sell.bid_price * (1.0 - sell_fees.taker_bp / 10_000.0)
    cost = buy.ask_price * (1.0 + buy_fees.taker_bp / 10_000.0)
    return (proceeds - cost) / mid * 10_000.0


def _floor_to_tick(price: float, tick: float) -> float:
    return math.floor(price / tick) * tick


def _ceil_to_tick(price: float, tick: float) -> float:
    return math.ceil(price / tick) * tick


def compute_cross_venue_quote(
    a: BookTicker,
    b: BookTicker,
    fees_a: VenueFees,
    fees_b: VenueFees,
    *,
    buffer_bp: float = 1.0,
    tick_size: float = 0.01,
) -> CrossVenueQuote:
    """Build the cross-venue quote for venue A against reference venue B."""
    spread = cross_spread_bp(a, b)
    edge_a_b = taker_edge_bp(a, b, fees_a, fees_b)
    edge_b_a = taker_edge_bp(b, a, fees_b, fees_a)

    if not (_valid(a) and _valid(b)):
        return CrossVenueQuote(
            venue_a=a.venue, venue_b=b.venue,
            spread_bp=spread,
            arb_buy_a_sell_b_bp=edge_a_b,
            arb_buy_b_sell_a_bp=edge_b_a,
            profitable=False,
            quote_bid_a=None, quote_ask_a=None, edge_at_quote_bp=None,
        )

    f_a_m = fees_a.maker_bp / 10_000.0
    f_b_t = fees_b.taker_bp / 10_000.0
    buf = buffer_bp / 10_000.0

    # Bid on A anchored to B's ask (buy A, hedge-sell B).
    raw_bid = b.ask_price * (1.0 - f_b_t - buf) / (1.0 + f_a_m)
    quote_bid = _floor_to_tick(raw_bid, tick_size)

    # Ask on A anchored to B's bid (sell A, hedge-buy B).
    raw_ask = b.bid_price * (1.0 + f_b_t + buf) / (1.0 - f_a_m)
    quote_ask = _ceil_to_tick(raw_ask, tick_size)

    # Expected edge if the A bid fills and we hedge at B's ask.
    if quote_bid > 0.0:
        edge_at_quote = (
            b.ask_price * (1.0 - f_b_t) - quote_bid * (1.0 + f_a_m)
        ) / quote_bid * 10_000.0
    else:
        edge_at_quote = None

    return CrossVenueQuote(
        venue_a=a.venue, venue_b=b.venue,
        spread_bp=spread,
        arb_buy_a_sell_b_bp=edge_a_b,
        arb_buy_b_sell_a_bp=edge_b_a,
        profitable=(edge_a_b > 0.0 or edge_b_a > 0.0),
        quote_bid_a=quote_bid,
        quote_ask_a=quote_ask,
        edge_at_quote_bp=edge_at_quote,
    )


__all__ = [
    "BookTicker",
    "CrossVenueQuote",
    "VenueFees",
    "compute_cross_venue_quote",
    "cross_spread_bp",
    "taker_edge_bp",
]
