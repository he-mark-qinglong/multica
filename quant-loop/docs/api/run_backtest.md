# `_shared.run_backtest`

Source: `_shared/run_backtest.py`

Authoritative in-house equity-walk engine — per-bar compounding.

## class `Trade(entry_ts: 'pd.Timestamp', exit_ts: 'pd.Timestamp', direction: 'Direction', size_fraction: 'float' = 1.0) -> None`

One closed trade. ``entry_ts``/``exit_ts`` MUST be in ``bars.index``.

## `run_backtest(bars: 'pd.DataFrame', trades: 'List[Trade]', *, initial_capital: 'float' = 100000.0, cost_bps_rt: 'float' = 24.0, cost_mode: 'CostMode' = 'fill', freq_per_year: 'int' = 8760) -> 'Dict[str, Any]'`

Per-bar compounding equity walk — backtrader-compatible.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `trades` | 'List[Trade]' | — |
| `initial_capital` | 'float' | 100000.0 |
| `cost_bps_rt` | 'float' | 24.0 |
| `cost_mode` | 'CostMode' | 'fill' |
| `freq_per_year` | 'int' | 8760 |

## `run_backtest_validation(bars: 'pd.DataFrame', trades: 'List[Trade]', **kwargs: 'Any') -> 'Dict[str, Any]'`

Alias for :func:`run_backtest` — keeps the validation-mode surface.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `trades` | 'List[Trade]' | — |
| `kwargs` | 'Any' | — |
