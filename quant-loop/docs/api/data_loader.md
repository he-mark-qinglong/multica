# `_shared.data_loader`

Source: `_shared/data_loader.py`

Authoritative unified data loader for quant-loop strategies.

## `available(symbol: 'str') -> 'dict'`

Return a coverage report for ``symbol``.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |

## `data_root() -> 'Path'`

Return the canonical ``data/`` directory for quant-loop.

## `load_aggtrades(symbol: 'str', start, end, columns: 'Optional[Sequence[str]]' = None) -> 'pd.DataFrame'`

Load an aggTrades window for ``symbol``.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `start` | — | — |
| `end` | — | — |
| `columns` | 'Optional[Sequence[str]]' | None |

## `load_bars(symbol: 'str', tf: 'str', start=None, end=None, columns: 'Optional[Sequence[str]]' = None) -> 'pd.DataFrame'`

Load klines for ``symbol``/``tf``.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `tf` | 'str' | — |
| `start` | — | None |
| `end` | — | None |
| `columns` | 'Optional[Sequence[str]]' | None |

## `load_funding(symbol: 'str', start=None, end=None, columns: 'Optional[Sequence[str]]' = None) -> 'pd.DataFrame'`

Load 8h funding-rate history for ``symbol``.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `start` | — | None |
| `end` | — | None |
| `columns` | 'Optional[Sequence[str]]' | None |
