# `_shared.l2.book`

Source: `_shared/l2/book.py`

L2 order-book reconstruction engine (B4).

## class `BookDiff(ts_ns: 'int', bids: 'Tuple[Level, ...]' = (), asks: 'Tuple[Level, ...]' = ()) -> None`

One incremental book update (absolute per-level quantities).

## class `BookState(ts_ns: 'int', bids: 'Tuple[Level, ...]', asks: 'Tuple[Level, ...]') -> None`

Immutable order-book state.

### `apply_diff(self, diff: "'BookDiff'") -> "'BookState'"`

Apply an incremental diff, returning a new :class:`BookState`.

| Parameter | Type | Default |
|---|---|---|
| `diff` | "'BookDiff'" | — |

### `depth_qty(self, side: 'str', n_levels: 'Optional[int]' = None) -> 'float'`

Total resting quantity over the first ``n_levels`` (default all).

| Parameter | Type | Default |
|---|---|---|
| `side` | 'str' | — |
| `n_levels` | 'Optional[int]' | None |

### `levels_through(self, side: 'str', limit_price: 'float') -> 'Tuple[Level, ...]'`

Levels of ``side`` that a marketable order at ``limit_price`` would trade against, in walk order.

| Parameter | Type | Default |
|---|---|---|
| `side` | 'str' | — |
| `limit_price` | 'float' | — |

### `top(self, n: 'int', side: 'str') -> 'Tuple[Level, ...]'`

Top-``n`` levels of one side in book order.

| Parameter | Type | Default |
|---|---|---|
| `n` | 'int' | — |
| `side` | 'str' | — |

### `weighted_depth_price(self, side: 'str', qty: 'float') -> 'Optional[float]'`

Volume-weighted average price to fill ``qty`` from ``side``.

| Parameter | Type | Default |
|---|---|---|
| `side` | 'str' | — |
| `qty` | 'float' | — |

## `snapshot(ts_ns: 'int', bids: 'Sequence[Level]', asks: 'Sequence[Level]') -> 'BookState'`

Build a :class:`BookState` from a full depth snapshot.

| Parameter | Type | Default |
|---|---|---|
| `ts_ns` | 'int' | — |
| `bids` | 'Sequence[Level]' | — |
| `asks` | 'Sequence[Level]' | — |
