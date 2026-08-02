# `_shared.market_making.live_quoter`

Source: `_shared/market_making/live_quoter.py`

Live execution bridge — connects the quoting engine to venue adapters.

## class `LiveQuoter(config: 'LiveQuoterConfig', transport: 'QuoterTransport')`

Main live quoting loop.

### `on_fill(self, side: 'str', price: 'float', qty: 'float', ts: 'pd.Timestamp')`

Called by the transport when an order is filled.

| Parameter | Type | Default |
|---|---|---|
| `side` | 'str' | — |
| `price` | 'float' | — |
| `qty` | 'float' | — |
| `ts` | 'pd.Timestamp' | — |

### `run(self)`

Main loop. Runs until max_runtime or KeyboardInterrupt.

## class `LiveQuoterConfig(api_key: 'str' = '', api_secret: 'str' = '', testnet: 'bool' = True, venue: 'str' = 'binance_perp', symbol: 'str' = 'BTCUSDT', tick_size: 'float' = 0.01, gamma: 'float' = 0.1, sigma_window_seconds: 'int' = 60, horizon_seconds: 'float' = 300.0, base_spread_bp: 'float' = 2.0, size_usd: 'float' = 100.0, inventory_skew_factor: 'float' = 1.0, vol_spread_coeff: 'float' = 0.5, max_inventory_usd: 'float' = 1000.0, fill_penalty_bp: 'float' = 1.0, penalty_decay_per_second: 'float' = 0.5, sweep_threshold: 'int' = 3, sweep_cooldown_seconds: 'float' = 5.0, max_penalty_bp: 'float' = 10.0, expected_sweep_cost_bp: 'float' = 1.74, max_hold_seconds: 'float' = 300.0, tp_bp: 'float' = 5.0, sl_bp: 'float' = 10.0, maker_fee_bp: 'float' = 2.0, taker_fee_bp: 'float' = 5.0, kelly_fraction: 'float' = 0.25, kelly_pnl_history_bp: 'list[float]' = <factory>, tick_interval_seconds: 'float' = 1.0, max_runtime_seconds: 'float' = 0.0) -> None`

Everything needed to run the quoter live.

## class `QuoterTransport()`

Abstract transport — override for real venue connection.

### `cancel_all(self, symbol: 'str') -> 'int'`

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |

### `cancel_order(self, order_id: 'str') -> 'bool'`

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |

### `get_book_ticker(self, symbol: 'str') -> 'tuple[float, float, float, float]'`

Returns (bid_price, bid_qty, ask_price, ask_qty).

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |

### `get_position(self, symbol: 'str') -> 'float'`

Returns net position quantity.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |

### `get_recent_trades(self, symbol: 'str', limit: 'int' = 50) -> 'pd.DataFrame'`

Returns DataFrame with columns: ts, price, qty, is_buyer_maker.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `limit` | 'int' | 50 |

### `market_order(self, side: 'str', qty: 'float') -> 'bool'`

Place a market (taker) order for immediate fill.

| Parameter | Type | Default |
|---|---|---|
| `side` | 'str' | — |
| `qty` | 'float' | — |

### `place_order(self, side: 'str', price: 'float', qty: 'float', is_maker: 'bool' = True) -> 'str | None'`

Place an order. Returns order_id or None.

| Parameter | Type | Default |
|---|---|---|
| `side` | 'str' | — |
| `price` | 'float' | — |
| `qty` | 'float' | — |
| `is_maker` | 'bool' | True |

## `run_live_quoter(config_path: 'str | Path', transport: 'QuoterTransport | None' = None)`

One-call entry point.

| Parameter | Type | Default |
|---|---|---|
| `config_path` | 'str | Path' | — |
| `transport` | 'QuoterTransport | None' | None |
