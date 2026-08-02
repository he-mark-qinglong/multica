# `_shared.queue_aware_backtest`

Source: `_shared/queue_aware_backtest.py`

Queue-position-aware wrapper around the authoritative backtester (B6).

## class `FillDecision(entry_ts: 'pd.Timestamp', order_type: 'OrderType', fill_probability: 'float', filled: 'bool', fill_ratio: 'float') -> None`

Audit record for one entry order.

## class `LimitTrade(entry_ts: 'pd.Timestamp', exit_ts: 'pd.Timestamp', direction: "Literal['long', 'short']", size_fraction: 'float' = 1.0, order_type: 'OrderType' = 'limit', ticks_from_best: 'int' = 1, seconds_in_queue: 'float' = 30.0, market_fill_rate: 'float' = 0.13) -> None`

A Trade plus the order-book context needed by the queue model.

## class `QueueAwareConfig(mode: 'FillMode' = 'expected', seed: 'int' = 42, queue_params: 'QueueParams' = QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), min_fill_probability: 'float' = 0.0, initial_capital: 'float' = 100000.0, cost_bps_rt: 'float' = 24.0, freq_per_year: 'int' = 8760) -> None`

Configuration for :func:`run_queue_aware_backtest`.

## `compare_queue_impact(bars: 'pd.DataFrame', trades: 'Sequence[LimitTrade]', *, config: 'QueueAwareConfig' = QueueAwareConfig(mode='expected', seed=42, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), min_fill_probability=0.0, initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760)) -> 'Dict[str, Any]'`

Contrast-experiment report: queue-aware vs naive fill sequences.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `trades` | 'Sequence[LimitTrade]' | — |
| `config` | 'QueueAwareConfig' | QueueAwareConfig(mode='expected', seed=42, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), min_fill_probability=0.0, initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760) |

## `run_queue_aware_backtest(bars: 'pd.DataFrame', trades: 'Sequence[LimitTrade]', *, config: 'QueueAwareConfig' = QueueAwareConfig(mode='expected', seed=42, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), min_fill_probability=0.0, initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760)) -> 'Dict[str, Any]'`

Run the authoritative engine on a queue-filtered trade schedule.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `trades` | 'Sequence[LimitTrade]' | — |
| `config` | 'QueueAwareConfig' | QueueAwareConfig(mode='expected', seed=42, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), min_fill_probability=0.0, initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760) |
