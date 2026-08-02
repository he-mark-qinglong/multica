# `_shared.market_making.strategy_sweeper`

Source: `_shared/market_making/strategy_sweeper.py`

Automated strategy discovery engine.

## class `CandidateResult(candidate: 'StrategyCandidate', n_trades: 'int', avg_pnl_bp: 'float', win_rate: 'float', profit_factor: 'float', sharpe: 'float', max_drawdown_bp: 'float', deflated_sharpe: 'float', passed_gates: 'bool', failed_gate_names: 'list[str]', pnl_history_bp: 'list[float]', elapsed_seconds: 'float') -> None`

Result of testing one candidate.

## class `StrategyCandidate(id: 'str', symbol: 'str', signal_type: 'str', timeframe: 'str', params: 'dict', generation: 'int' = 0) -> None`

One strategy configuration to be tested.

## class `SweepConfig(symbols: 'list[str]' = <factory>, signal_types: 'list[str]' = <factory>, funding_grid: 'dict' = <factory>, momentum_grid: 'dict' = <factory>, mean_revert_grid: 'dict' = <factory>) -> None`

What to search over.

## `enumerate_candidates(config: 'SweepConfig') -> 'list[StrategyCandidate]'`

Generate all candidates from the parameter grid.

| Parameter | Type | Default |
|---|---|---|
| `config` | 'SweepConfig' | — |

## `evaluate_candidate(pnl_history_bp: 'list[float]', n_trials: 'int' = 1) -> 'dict'`

Evaluate a PnL history through simplified gate checks.

| Parameter | Type | Default |
|---|---|---|
| `pnl_history_bp` | 'list[float]' | — |
| `n_trials` | 'int' | 1 |

## `run_sweep(candidates: 'list[StrategyCandidate]', data_loader: 'Callable[[str, str], Any]', n_trials: 'int' = 0, verbose: 'bool' = True) -> 'list[CandidateResult]'`

Execute all candidates and return ranked results.

| Parameter | Type | Default |
|---|---|---|
| `candidates` | 'list[StrategyCandidate]' | — |
| `data_loader` | 'Callable[[str, str], Any]' | — |
| `n_trials` | 'int' | 0 |
| `verbose` | 'bool' | True |

## `signal_funding_carry(fund_data: 'pd.DataFrame', params: 'dict', bars_1m: 'pd.DataFrame | None' = None) -> 'list[dict]'`

Funding carry signal: counter-funding position at each funding event.

| Parameter | Type | Default |
|---|---|---|
| `fund_data` | 'pd.DataFrame' | — |
| `params` | 'dict' | — |
| `bars_1m` | 'pd.DataFrame | None' | None |

## `signal_mean_revert(prices: 'np.ndarray', qtys: 'np.ndarray', timestamps: 'pd.Series', params: 'dict') -> 'list[dict]'`

VWAP mean reversion signal.

| Parameter | Type | Default |
|---|---|---|
| `prices` | 'np.ndarray' | — |
| `qtys` | 'np.ndarray' | — |
| `timestamps` | 'pd.Series' | — |
| `params` | 'dict' | — |

## `signal_momentum(prices: 'np.ndarray', timestamps: 'pd.Series', params: 'dict') -> 'list[dict]'`

Momentum signal: enter in direction of recent return.

| Parameter | Type | Default |
|---|---|---|
| `prices` | 'np.ndarray' | — |
| `timestamps` | 'pd.Series' | — |
| `params` | 'dict' | — |

## `summarize_results(results: 'list[CandidateResult]') -> 'dict'`

Summarize sweep results.

| Parameter | Type | Default |
|---|---|---|
| `results` | 'list[CandidateResult]' | — |
