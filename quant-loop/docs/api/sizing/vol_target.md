# `_shared.sizing.vol_target`

Source: `_shared/sizing/vol_target.py`

Volatility-targeted position sizing layer.

## `apply_vol_target(equity: pandas.Series, target_vol: float = 0.15, lookback: int = 20, floor: float = 0.1, cap: float = 3.0, periods_per_year: int = 365) -> pandas.Series`

Convenience: take an equity curve, return vol-targeted equity curve.

| Parameter | Type | Default |
|---|---|---|
| `equity` | pandas.Series | — |
| `target_vol` | float | 0.15 |
| `lookback` | int | 20 |
| `floor` | float | 0.1 |
| `cap` | float | 3.0 |
| `periods_per_year` | int | 365 |

## `rolling_realized_vol(returns: pandas.Series, lookback: int = 20, periods_per_year: int = 365) -> pandas.Series`

Annualized realized vol from rolling std of returns.

| Parameter | Type | Default |
|---|---|---|
| `returns` | pandas.Series | — |
| `lookback` | int | 20 |
| `periods_per_year` | int | 365 |

## `sharpe_lift(equity_baseline: pandas.Series, equity_sized: pandas.Series, periods_per_year: int = 365) -> float`

Sharpe(sized) - Sharpe(baseline). Positive means vol-targeting helped.

| Parameter | Type | Default |
|---|---|---|
| `equity_baseline` | pandas.Series | — |
| `equity_sized` | pandas.Series | — |
| `periods_per_year` | int | 365 |

## `vol_target_weights(returns: pandas.Series, target_vol: float = 0.15, lookback: int = 20, floor: float = 0.1, cap: float = 3.0, periods_per_year: int = 365) -> pandas.Series`

Daily position-size multiplier to target `target_vol` annualized vol.

| Parameter | Type | Default |
|---|---|---|
| `returns` | pandas.Series | — |
| `target_vol` | float | 0.15 |
| `lookback` | int | 20 |
| `floor` | float | 0.1 |
| `cap` | float | 3.0 |
| `periods_per_year` | int | 365 |
