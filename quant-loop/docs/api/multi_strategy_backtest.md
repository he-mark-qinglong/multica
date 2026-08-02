# `_shared.multi_strategy_backtest`

Source: `_shared/multi_strategy_backtest.py`

Multi-strategy portfolio backtest (B15).

## class `MultiStrategyConfig(initial_capital: 'float' = 100000.0, cost_bps_rt: 'float' = 24.0, freq_per_year: 'int' = 8760, weighting: 'Weighting' = 'equal', symbol: 'str' = 'SYNTH') -> None`

Configuration for :func:`run_multi_strategy_backtest`.

## class `StrategySpec(name: 'str', trades: 'Tuple[Trade, ...]', weight: 'float' = 1.0) -> None`

One strategy's trade schedule over the shared bars.

## `run_multi_strategy_backtest(bars: 'pd.DataFrame', strategies: 'Sequence[StrategySpec]', *, config: 'MultiStrategyConfig' = MultiStrategyConfig(initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760, weighting='equal', symbol='SYNTH')) -> 'Dict[str, Any]'`

Portfolio backtest over N strategies sharing one bar frame.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `strategies` | 'Sequence[StrategySpec]' | — |
| `config` | 'MultiStrategyConfig' | MultiStrategyConfig(initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760, weighting='equal', symbol='SYNTH') |
