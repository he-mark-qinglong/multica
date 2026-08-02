# `_shared.portfolio.reporting`

Source: `_shared/portfolio/reporting.py`

HTML portfolio report generator (I20).

## `generate_report(snapshots: 'Sequence[PortfolioSnapshot]', strategy_pnl: 'Optional[Mapping[str, float]]' = None, drawdown_attr: 'Optional[DrawdownAttribution]' = None, periods_per_year: 'int' = 365, title: 'str' = 'Portfolio Report') -> 'str'`

Render the full HTML report. Snapshots must be ts-ordered.

| Parameter | Type | Default |
|---|---|---|
| `snapshots` | 'Sequence[PortfolioSnapshot]' | — |
| `strategy_pnl` | 'Optional[Mapping[str, float]]' | None |
| `drawdown_attr` | 'Optional[DrawdownAttribution]' | None |
| `periods_per_year` | 'int' | 365 |
| `title` | 'str' | 'Portfolio Report' |

## `sparkline(series: 'pd.Series', width: 'int' = 60) -> 'str'`

ASCII/Unicode sparkline of a numeric series, downsampled to width.

| Parameter | Type | Default |
|---|---|---|
| `series` | 'pd.Series' | — |
| `width` | 'int' | 60 |
