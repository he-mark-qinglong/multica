# `_shared.strategy_kit.factor_library`

Source: `_shared/strategy_kit/factor_library.py`

Built-in factor library (metric A5) — production-grade cross-asset / crypto-perp factors with paper-backed definitions.

## class `FactorSpec(name: 'str', compute: 'Callable[..., pd.Series]', direction: 'int', reference: 'str', required_columns: 'Tuple[str, ...]', params: 'Mapping[str, ParamSpec]' = <factory>, description: 'str' = '') -> None`

Metadata for one library factor.

## `amihud_illiq(data: 'pd.DataFrame', window: 'int' = 20) -> 'pd.Series'`

Rolling mean of |return| / dollar volume, scaled x1e9 (Amihud 2002). Direction +1: illiquid names earn an illiquidity premium.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 20 |

## `basis_perp_spot(data: 'pd.DataFrame', window: 'int' = 24) -> 'pd.Series'`

Trailing mean of the (perp - spot)/spot basis. Direction -1: rich positive basis = longs paying up -> fade (cash-and-carry short leg).

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 24 |

## `compute_factor(name: 'str', data: 'pd.DataFrame', **params) -> 'pd.Series'`

Compute factor ``name`` after checking required columns are present.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `data` | 'pd.DataFrame' | — |
| `params` | — | — |

## `funding_change(data: 'pd.DataFrame', window: 'int' = 8) -> 'pd.Series'`

Change in funding rate over the last ``window`` bars; a positive shock = fresh long crowding -> negative forward returns.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 8 |

## `funding_level(data: 'pd.DataFrame', window: 'int' = 24) -> 'pd.Series'`

Trailing mean of the funding rate. Direction -1: persistently high positive funding signals overcrowded longs — short them, collect funding (funding-carry).

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 24 |

## `get_factor_spec(name: 'str') -> 'FactorSpec'`

Return the :class:`FactorSpec` for ``name`` (KeyError if unknown).

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

## `kyle_lambda(data: 'pd.DataFrame', window: 'int' = 20) -> 'pd.Series'`

Rolling Kyle lambda: OLS slope of Δprice on signed sqrt-volume over ``window`` bars — the price impact per unit of signed order flow (Kyle 1985). High lambda = thin, informed market -> fade the move.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 20 |

## `list_factors() -> 'Dict[str, FactorSpec]'`

name -> FactorSpec for every library factor (sorted by name).

## `momentum_12_1(data: 'pd.DataFrame', lookback: 'int' = 252, skip: 'int' = 21) -> 'pd.Series'`

Classic 12-1 momentum: return from ``t-lookback`` to ``t-skip``, skipping the most recent ``skip`` bars to avoid the short-term reversal horizon (Jegadeesh & Titman 1993).

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `lookback` | 'int' | 252 |
| `skip` | 'int' | 21 |

## `oi_change_proxy(data: 'pd.DataFrame', window: 'int' = 20) -> 'pd.Series'`

Signed-volume accumulation z-score: sign(ret) * volume, rolled up and z-scored — a bar-level proxy for open-interest change (positioning build-up in the direction of the move).

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 20 |

## `reversal_5d(data: 'pd.DataFrame', window: 'int' = 5) -> 'pd.Series'`

Trailing ``window``-bar simple return. Direction is -1: recent losers outperform (short-term reversal, Jegadeesh 1990).

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 5 |

## `vol_of_vol(data: 'pd.DataFrame', vol_window: 'int' = 20, vov_window: 'int' = 20) -> 'pd.Series'`

Std of the rolling realised-vol series itself.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `vol_window` | 'int' | 20 |
| `vov_window` | 'int' | 20 |

## `vol_realized(data: 'pd.DataFrame', window: 'int' = 20, periods_per_year: 'int' = 365) -> 'pd.Series'`

Annualised realised volatility of log close returns over ``window``.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 20 |
| `periods_per_year` | 'int' | 365 |

## `volume_zscore(data: 'pd.DataFrame', window: 'int' = 20) -> 'pd.Series'`

(volume - rolling mean) / rolling std of volume.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 20 |

## `vpin_proxy(data: 'pd.DataFrame', window: 'int' = 20, vol_window: 'int' = 30) -> 'pd.Series'`

VPIN approximation (Easley et al. 2012): classify each bar's volume as buy/sell via the normal CDF of the volatility-scaled return (``Phi(ret / sigma)``), then take the rolling mean of |buy - sell| / total volume. High VPIN = toxic flow -> negative forward returns.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `window` | 'int' | 20 |
| `vol_window` | 'int' | 30 |
