# `_shared.portfolio.account_view`

Source: `_shared/portfolio/account_view.py`

Strategy-level independent account views (I12, partial I11).

## class `AccountView(account_id: 'str', initial_capital: 'float', final_equity: 'float', realized_pnl: 'float', unrealized_pnl: 'float', total_fees: 'float', n_fills: 'int', positions: 'Dict[str, float]', equity_curve: 'pd.Series', total_return: 'float') -> None`

Per-strategy (or pool) reconstructed account.

## class `Fill(ts: 'pd.Timestamp', strategy_id: 'str', symbol: 'str', qty: 'float', price: 'float', fee: 'float' = 0.0) -> None`

One execution. ``qty`` is signed: positive = buy, negative = sell.

## `build_account_views(fills: 'Sequence[Fill]', initial_capital: 'float', mode: 'str' = 'isolated', capital_weights: 'Optional[Mapping[str, float]]' = None) -> 'Dict[str, AccountView]'`

Reconstruct per-strategy account views from a tagged fill stream.

| Parameter | Type | Default |
|---|---|---|
| `fills` | 'Sequence[Fill]' | — |
| `initial_capital` | 'float' | — |
| `mode` | 'str' | 'isolated' |
| `capital_weights` | 'Optional[Mapping[str, float]]' | None |
