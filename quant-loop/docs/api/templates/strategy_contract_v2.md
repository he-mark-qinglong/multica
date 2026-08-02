# `_shared.templates.strategy_contract_v2`

Source: `_shared/templates/strategy_contract_v2.py`

Strategy contract v2 — the interface every new (high-frequency) strategy must implement.

## class `ContractError`

Strategy module violates the v2 contract.

## class `Trade(entry_ts: 'pd.Timestamp', exit_ts: 'pd.Timestamp', direction: 'Direction', size_fraction: 'float' = 1.0) -> None`

One closed trade. ``entry_ts``/``exit_ts`` MUST be in ``bars.index``.

## `check_contract(module: 'ModuleType', *, run_synthetic: 'bool' = True, n_bars: 'int' = 500, seed: 'int' = 42) -> 'Dict[str, Any]'`

Validate a strategy module against the v2 contract.

| Parameter | Type | Default |
|---|---|---|
| `module` | 'ModuleType' | — |
| `run_synthetic` | 'bool' | True |
| `n_bars` | 'int' | 500 |
| `seed` | 'int' | 42 |

## `make_synthetic_bars(symbols: 'Sequence[str]' = ('SYNTH',), n_bars: 'int' = 500, *, freq: 'str' = '1h', start: 'str' = '2026-01-01', seed: 'int' = 42) -> 'Dict[str, pd.DataFrame]'`

Deterministic random-walk OHLCV frames keyed by symbol.

| Parameter | Type | Default |
|---|---|---|
| `symbols` | 'Sequence[str]' | ('SYNTH',) |
| `n_bars` | 'int' | 500 |
| `freq` | 'str' | '1h' |
| `start` | 'str' | '2026-01-01' |
| `seed` | 'int' | 42 |

## `validate_module_signature(module: 'ModuleType') -> 'None'`

Check the module exposes ``generate_signals(bars, config)``.

| Parameter | Type | Default |
|---|---|---|
| `module` | 'ModuleType' | — |

## `validate_trades(trades: 'Any', bar_index: 'pd.Index | None' = None) -> 'List[Trade]'`

Validate a ``generate_signals`` return value.

| Parameter | Type | Default |
|---|---|---|
| `trades` | 'Any' | — |
| `bar_index` | 'pd.Index | None' | None |
