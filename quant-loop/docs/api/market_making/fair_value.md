# `_shared.market_making.fair_value`

Source: `_shared/market_making/fair_value.py`

Fair value estimation for market making.

## class `FairValue(mid: 'float', microprice: 'float', vwap: 'float', vpvr_poc: 'float', composite: 'float', timestamp: 'pd.Timestamp') -> None`

Composite fair-value estimate.

## class `MarketSnapshot(timestamp: 'pd.Timestamp', bid_price: 'float', ask_price: 'float', bid_volume: 'float', ask_volume: 'float', last_price: 'float', recent_trades: 'pd.DataFrame', bars: 'pd.DataFrame') -> None`

Minimal market state needed for fair-value computation.

## `compute_fair_value(snapshot: 'MarketSnapshot', vwap_lookback: 'int' = 20, vpvr_bars: 'int' = 200, weights: 'Optional[dict[str, float]]' = None) -> 'FairValue'`

Fuse three estimators into a single composite fair value.

| Parameter | Type | Default |
|---|---|---|
| `snapshot` | 'MarketSnapshot' | — |
| `vwap_lookback` | 'int' | 20 |
| `vpvr_bars` | 'int' | 200 |
| `weights` | 'Optional[dict[str, float]]' | None |

## `microprice(bid_price: 'float', ask_price: 'float', bid_volume: 'float', ask_volume: 'float') -> 'float'`

Glosten-Harris (1988) microprice.

| Parameter | Type | Default |
|---|---|---|
| `bid_price` | 'float' | — |
| `ask_price` | 'float' | — |
| `bid_volume` | 'float' | — |
| `ask_volume` | 'float' | — |

## `rolling_vwap(trades: 'pd.DataFrame', lookback: 'int' = 20) -> 'float'`

Volume-weighted average price over the most recent *lookback* trades.

| Parameter | Type | Default |
|---|---|---|
| `trades` | 'pd.DataFrame' | — |
| `lookback` | 'int' | 20 |

## `vpvr_fair_value(high: 'pd.Series', low: 'pd.Series', volume: 'pd.Series', num_bins: 'int' = 200) -> 'float'`

VPVR Point-of-Control as fair value.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `volume` | 'pd.Series' | — |
| `num_bins` | 'int' | 200 |
