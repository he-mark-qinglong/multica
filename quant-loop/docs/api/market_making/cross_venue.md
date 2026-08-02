# `_shared.market_making.cross_venue`

Source: `_shared/market_making/cross_venue.py`

Cross-venue quoting and arbitrage-edge analytics.

## class `BookTicker(venue: 'str', bid_price: 'float', bid_size: 'float', ask_price: 'float', ask_size: 'float', timestamp: 'float') -> None`

Top-of-book snapshot for one venue.

## class `CrossVenueQuote(venue_a: 'str', venue_b: 'str', spread_bp: 'float', arb_buy_a_sell_b_bp: 'float', arb_buy_b_sell_a_bp: 'float', profitable: 'bool', quote_bid_a: 'float | None', quote_ask_a: 'float | None', edge_at_quote_bp: 'float | None') -> None`

Cross-venue analytics + passive quote offsets for venue A.

## class `VenueFees(maker_bp: 'float' = 0.0, taker_bp: 'float' = 4.0) -> None`

Fee schedule in basis points (positive = cost, negative = rebate).

## `compute_cross_venue_quote(a: 'BookTicker', b: 'BookTicker', fees_a: 'VenueFees', fees_b: 'VenueFees', *, buffer_bp: 'float' = 1.0, tick_size: 'float' = 0.01) -> 'CrossVenueQuote'`

Build the cross-venue quote for venue A against reference venue B.

| Parameter | Type | Default |
|---|---|---|
| `a` | 'BookTicker' | — |
| `b` | 'BookTicker' | — |
| `fees_a` | 'VenueFees' | — |
| `fees_b` | 'VenueFees' | — |
| `buffer_bp` | 'float' | 1.0 |
| `tick_size` | 'float' | 0.01 |

## `cross_spread_bp(a: 'BookTicker', b: 'BookTicker') -> 'float'`

Signed spread of A over B in bp: positive ⇒ A's ask is richer.

| Parameter | Type | Default |
|---|---|---|
| `a` | 'BookTicker' | — |
| `b` | 'BookTicker' | — |

## `taker_edge_bp(buy: 'BookTicker', sell: 'BookTicker', buy_fees: 'VenueFees', sell_fees: 'VenueFees') -> 'float'`

Net taker edge (bp) of buying ``buy``'s ask and selling ``sell``'s bid.

| Parameter | Type | Default |
|---|---|---|
| `buy` | 'BookTicker' | — |
| `sell` | 'BookTicker' | — |
| `buy_fees` | 'VenueFees' | — |
| `sell_fees` | 'VenueFees' | — |
