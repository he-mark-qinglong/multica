# `_shared.portfolio.exposure`

Source: `_shared/portfolio/exposure.py`

Portfolio exposure limiter (I13).

## class `ExposureLimiter(limits: 'ExposureLimits')`

Stateful book tracker enforcing :class:`ExposureLimits`.

### `apply(self, new: 'Position') -> 'None'`

Update the book. Call only after ``check`` returned True.

| Parameter | Type | Default |
|---|---|---|
| `new` | 'Position' | — |

### `check(self, new: 'Position', equity: 'float') -> 'Tuple[bool, str]'`

Check and log. Rejections are appended to ``self.rejections``.

| Parameter | Type | Default |
|---|---|---|
| `new` | 'Position' | — |
| `equity` | 'float' | — |

### `gross_notional(self) -> 'float'`

### `net_notional(self) -> 'float'`

## class `ExposureLimits(max_total_notional: 'float | None' = None, max_symbol_notional: 'float | None' = None, max_direction_notional: 'float | None' = None, max_leverage: 'float | None' = None) -> None`

Hard caps. ``None`` disables a cap.

## class `Position(symbol: 'str', qty: 'float', price: 'float') -> None`

Signed position in one symbol. ``qty`` positive = long.

## class `Rejection(symbol: 'str', qty: 'float', price: 'float', reason: 'str') -> None`

Audit record of a rejected position change.

## `check_exposure(positions: 'Mapping[str, Position]', new: 'Position', limits: 'ExposureLimits', equity: 'float') -> 'Tuple[bool, str]'`

Would replacing ``positions[new.symbol]`` with ``new`` stay within limits? Pure function — does not mutate anything.

| Parameter | Type | Default |
|---|---|---|
| `positions` | 'Mapping[str, Position]' | — |
| `new` | 'Position' | — |
| `limits` | 'ExposureLimits' | — |
| `equity` | 'float' | — |
