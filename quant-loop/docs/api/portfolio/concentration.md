# `_shared.portfolio.concentration`

Source: `_shared/portfolio/concentration.py`

Theme/sector concentration limiter (I14).

## class `ConcentrationLimiter(limits: 'ConcentrationLimits', themes: 'Mapping[str, str]')`

Stateful book tracker enforcing :class:`ConcentrationLimits`.

### `apply(self, new: 'Position') -> 'None'`

Update the book. Call only after ``check`` returned True.

| Parameter | Type | Default |
|---|---|---|
| `new` | 'Position' | — |

### `check(self, new: 'Position') -> 'Tuple[bool, str]'`

Check and log. Rejections are appended to ``self.rejections``.

| Parameter | Type | Default |
|---|---|---|
| `new` | 'Position' | — |

### `theme_notional(self) -> 'Dict[str, float]'`

Current absolute notional per theme.

## class `ConcentrationLimits(theme_caps: 'Mapping[str, float]' = <factory>, default_cap: 'float | None' = None) -> None`

Per-theme notional caps. ``None`` default cap = unlimited.

## class `ConcentrationRejection(symbol: 'str', theme: 'str', qty: 'float', price: 'float', reason: 'str') -> None`

Audit record of a position change rejected on theme grounds.

## `check_concentration(positions: 'Mapping[str, Position]', new: 'Position', themes: 'Mapping[str, str]', limits: 'ConcentrationLimits') -> 'Tuple[bool, str]'`

Would replacing ``positions[new.symbol]`` with ``new`` keep the new position's theme within its cap? Pure — does not mutate.

| Parameter | Type | Default |
|---|---|---|
| `positions` | 'Mapping[str, Position]' | — |
| `new` | 'Position' | — |
| `themes` | 'Mapping[str, str]' | — |
| `limits` | 'ConcentrationLimits' | — |

## `theme_exposure(positions: 'Mapping[str, Position]', themes: 'Mapping[str, str]') -> 'Dict[str, float]'`

Aggregate absolute notional per theme. Pure.

| Parameter | Type | Default |
|---|---|---|
| `positions` | 'Mapping[str, Position]' | — |
| `themes` | 'Mapping[str, str]' | — |
