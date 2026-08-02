# `_shared.ops.pnl_attribution`

Source: `_shared/ops/pnl_attribution.py`

Live PnL attribution (H18).

## class `AttributionRow(strategy: 'str', symbol: 'str', day: 'str', price_pnl: 'float' = 0.0, fee_pnl: 'float' = 0.0, funding_pnl: 'float' = 0.0, slippage_pnl: 'float' = 0.0, closed_qty: 'float' = 0.0) -> None`

PnL decomposition for one (strategy, symbol, day) cell.

## class `Fill(ts: 'float', strategy: 'str', symbol: 'str', side: 'str', qty: 'float', price: 'float', fee: 'float' = 0.0, funding: 'float' = 0.0, reference_price: 'Optional[float]' = None) -> None`

One executed fill.

## `aggregate(rows: 'Sequence[AttributionRow]', by: 'Sequence[str]' = ('strategy',)) -> 'Tuple[AttributionRow, ...]'`

Roll rows up by a subset of ("strategy", "symbol", "day"). Pure.

| Parameter | Type | Default |
|---|---|---|
| `rows` | 'Sequence[AttributionRow]' | — |
| `by` | 'Sequence[str]' | ('strategy',) |

## `attribute_fills(fills: 'Sequence[Fill]') -> 'Tuple[AttributionRow, ...]'`

Decompose a fill stream into per-(strategy, symbol, day) rows. Pure.

| Parameter | Type | Default |
|---|---|---|
| `fills` | 'Sequence[Fill]' | — |
