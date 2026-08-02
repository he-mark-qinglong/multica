# `_shared.market_making.maker_simulator`

Source: `_shared/market_making/maker_simulator.py`

Backtest simulator for market-making strategies.

## class `FillRecord(ts: 'pd.Timestamp', side: 'str', price: 'float', qty: 'float', fee_bp: 'float', is_maker: 'bool') -> None`

FillRecord(ts: 'pd.Timestamp', side: 'str', price: 'float', qty: 'float', fee_bp: 'float', is_maker: 'bool')

## class `MakerSimConfig(symbol: 'str' = 'BTCUSDT', vwap_lookback: 'int' = 20, vpvr_bars: 'int' = 200, spread_estimate_ticks: 'int' = 2, gamma: 'float' = 0.1, sigma_window_seconds: 'int' = 60, horizon_seconds: 'float' = 300.0, base_spread_bp: 'float' = 2.0, size_usd: 'float' = 1000.0, tick_size: 'float' = 0.01, inventory_skew_factor: 'float' = 1.0, vol_spread_coeff: 'float' = 0.5, max_inventory_usd: 'float' = 5000.0, fill_penalty_bp: 'float' = 1.0, penalty_decay_per_second: 'float' = 0.5, sweep_threshold: 'int' = 3, sweep_cooldown_seconds: 'float' = 5.0, max_penalty_bp: 'float' = 10.0, expected_sweep_cost_bp: 'float' = 1.74, max_hold_seconds: 'float' = 300.0, tp_bp: 'float' = 5.0, sl_bp: 'float' = 10.0, maker_fee_bp: 'float' = 2.0, taker_fee_bp: 'float' = 5.0, start_ts: 'str' = '2026-04-19', end_ts: 'str' = '2026-04-22', trade_step: 'int' = 1, bar_freq: 'str' = '1min') -> None`

All knobs for the maker simulator.

## class `RoundTrip(entry_ts: 'pd.Timestamp', exit_ts: 'pd.Timestamp', direction: 'str', entry_price: 'float', exit_price: 'float', qty: 'float', pnl_usd: 'float', pnl_bp: 'float', maker_fee_bp: 'float', taker_fee_bp: 'float', exit_reason: 'str') -> None`

RoundTrip(entry_ts: 'pd.Timestamp', exit_ts: 'pd.Timestamp', direction: 'str', entry_price: 'float', exit_price: 'float', qty: 'float', pnl_usd: 'float', pnl_bp: 'float', maker_fee_bp: 'float', taker_fee_bp: 'float', exit_reason: 'str')

## `roundtrip_to_trade(rt: 'RoundTrip') -> 'Trade'`

Convert internal RoundTrip to run_backtest.Trade.

| Parameter | Type | Default |
|---|---|---|
| `rt` | 'RoundTrip' | — |

## `simulate_market_making(aggtrades: 'pd.DataFrame', config: 'MakerSimConfig') -> 'tuple[list[Trade], dict[str, Any]]'`

Run the maker simulator over a slice of aggTrades.

| Parameter | Type | Default |
|---|---|---|
| `aggtrades` | 'pd.DataFrame' | — |
| `config` | 'MakerSimConfig' | — |
