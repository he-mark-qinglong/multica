# `_shared.market_making.reservation_price`

Source: `_shared/market_making/reservation_price.py`

Avellaneda-Stoikov reservation price.

## class `ReservationPriceParams(gamma: 'float' = 0.1, sigma_window: 'int' = 60, horizon_seconds: 'float' = 300.0) -> None`

Tunables for the reservation-price formula.

## `reservation_price(fair_value: 'float', inventory_qty: 'float', sigma: 'float', time_remaining: 'float', gamma: 'float' = 0.1) -> 'float'`

Avellaneda-Stoikov reservation price ``r = s - q·γ·σ²·(T-t)``.

| Parameter | Type | Default |
|---|---|---|
| `fair_value` | 'float' | — |
| `inventory_qty` | 'float' | — |
| `sigma` | 'float' | — |
| `time_remaining` | 'float' | — |
| `gamma` | 'float' | 0.1 |

## `rolling_sigma(trades: 'pd.DataFrame', window_seconds: 'int' = 60) -> 'float'`

Per-second realised volatility from recent aggTrades.

| Parameter | Type | Default |
|---|---|---|
| `trades` | 'pd.DataFrame' | — |
| `window_seconds` | 'int' | 60 |
