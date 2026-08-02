# `_shared.attribution.decompose`

Source: `_shared/attribution/decompose.py`

Performance attribution — strategy-level PnL decomposition.

## class `CostSpec(fee_bps_per_side: 'float', slippage_bps_per_side: 'float', fills_per_round_trip: 'int' = 2) -> None`

Per-side bps + fill count. ``fills_per_round_trip`` is 2 for a single-instrument round trip (entry+exit), 4 for a pair (2 legs).

## class `LedgerError`

Raised when a trade ledger violates the input contract (sentinels).

## `alpha_beta(daily_net: 'pd.Series', market_daily: 'pd.Series') -> 'Dict'`

OLS of daily net strategy returns on a market return series.

| Parameter | Type | Default |
|---|---|---|
| `daily_net` | 'pd.Series' | — |
| `market_daily` | 'pd.Series' | — |

## `attribute(trades: 'pd.DataFrame', scenarios: 'Sequence[tuple]', reference: 'str | None' = None) -> 'Dict'`

Full attribution report.

| Parameter | Type | Default |
|---|---|---|
| `trades` | 'pd.DataFrame' | — |
| `scenarios` | 'Sequence[tuple]' | — |
| `reference` | 'str | None' | None |

## `normalize_trades(df: 'pd.DataFrame') -> 'pd.DataFrame'`

Detect schema, compute gross returns from prices, enforce sentinels.

| Parameter | Type | Default |
|---|---|---|
| `df` | 'pd.DataFrame' | — |

## `write_report(report: 'Dict', path: 'str') -> 'str'`

Byte-deterministic JSON write (sorted keys, fixed separators).

| Parameter | Type | Default |
|---|---|---|
| `report` | 'Dict' | — |
| `path` | 'str' | — |
