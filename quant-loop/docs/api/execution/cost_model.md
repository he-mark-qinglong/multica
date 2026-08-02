# `_shared.execution.cost_model`

Source: `_shared/execution/cost_model.py`

Authoritative execution cost model for quant-loop strategies.

## class `Venue(name: str, taker_fee_bps: float, maker_fee_bps: float, has_bnb_discount: bool = False, fixed_pure_slippage_bps: Optional[float] = None) -> None`

Venue(name: str, taker_fee_bps: float, maker_fee_bps: float, has_bnb_discount: bool = False, fixed_pure_slippage_bps: Optional[float] = None)

## `apply_cost(notional_usd: float, adv_usd: float, venue: _shared.execution.cost_model.Venue = Venue(name='binance_spot', taker_fee_bps=10.0, maker_fee_bps=7.5, has_bnb_discount=True, fixed_pure_slippage_bps=None), side: Literal['taker', 'maker'] = 'taker', impact_factor: float = 0.1) -> float`

Total round-trip cost in USD for a single-leg entry+exit.

| Parameter | Type | Default |
|---|---|---|
| `notional_usd` | float | — |
| `adv_usd` | float | — |
| `venue` | _shared.execution.cost_model.Venue | Venue(name='binance_spot', taker_fee_bps=10.0, maker_fee_bps=7.5, has_bnb_discount=True, fixed_pure_slippage_bps=None) |
| `side` | Literal['taker', 'maker'] | 'taker' |
| `impact_factor` | float | 0.1 |

## `cost_as_pct(notional_usd: float, adv_usd: float, **kwargs) -> float`

Round-trip cost as a fraction of notional (e.g. 0.0016 = 16bp).

| Parameter | Type | Default |
|---|---|---|
| `notional_usd` | float | — |
| `adv_usd` | float | — |
| `kwargs` | — | — |

## `futures_cost_model(venue: _shared.execution.cost_model.Venue = Venue(name='binance_usdt_futures', taker_fee_bps=4.0, maker_fee_bps=2.0, has_bnb_discount=False, fixed_pure_slippage_bps=7.0))`

Build the extended ``factor_backtester.CostModel`` for a futures venue.

| Parameter | Type | Default |
|---|---|---|
| `venue` | _shared.execution.cost_model.Venue | Venue(name='binance_usdt_futures', taker_fee_bps=4.0, maker_fee_bps=2.0, has_bnb_discount=False, fixed_pure_slippage_bps=7.0) |

## `slippage_bps(notional_usd: float, adv_usd: float, impact_factor: float = 0.1) -> float`

Square-root slippage in basis points (spot path only).

| Parameter | Type | Default |
|---|---|---|
| `notional_usd` | float | — |
| `adv_usd` | float | — |
| `impact_factor` | float | 0.1 |
