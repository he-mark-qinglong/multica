# `_shared.l2.bookdepth`

Source: `_shared/l2/bookdepth.py`

Loader for Binance public-data ``bookDepth`` snapshots (B4).

## `bookdepth_rows_to_snapshots(rows: 'Sequence[Tuple[int, int, float, float]]') -> 'List[BookState]'`

Convert ``(ts_ns, percentage, depth, notional)`` rows to snapshots.

| Parameter | Type | Default |
|---|---|---|
| `rows` | 'Sequence[Tuple[int, int, float, float]]' | — |

## `load_bookdepth_parquet(path: "'str | Path'") -> 'List[BookState]'`

Load a bookDepth parquet file into a list of :class:`BookState`.

| Parameter | Type | Default |
|---|---|---|
| `path` | "'str | Path'" | — |
