# `_shared.strategy_kit.indicators`

Source: `_shared/strategy_kit/indicators.py`

Technical indicator library (metric A6) — vectorized, pure functions.

## `accumulation_distribution(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', volume: 'pd.Series') -> 'pd.Series'`

Chaikin Accumulation/Distribution line.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `volume` | 'pd.Series' | — |

## `atr(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', period: 'int' = 14) -> 'pd.Series'`

Wilder ATR. Constant bars (h=l=c) -> 0.0.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `period` | 'int' | 14 |

## `bollinger_bands(close: 'pd.Series', window: 'int' = 20, num_std: 'float' = 2.0) -> 'pd.DataFrame'`

Middle/upper/lower band + %b + bandwidth. Constant window -> width 0, %b NaN.

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `window` | 'int' | 20 |
| `num_std` | 'float' | 2.0 |

## `cci(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', window: 'int' = 20) -> 'pd.Series'`

Commodity Channel Index (Lambert 1980). Constant window -> NaN.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `window` | 'int' | 20 |

## `donchian_channels(high: 'pd.Series', low: 'pd.Series', window: 'int' = 20) -> 'pd.DataFrame'`

Upper = rolling max(high), lower = rolling min(low), mid = mean of both.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `window` | 'int' | 20 |

## `ema(s: 'pd.Series', span: 'int') -> 'pd.Series'`

Exponential moving average (alpha = 2/(span+1)), seeded at first value.

| Parameter | Type | Default |
|---|---|---|
| `s` | 'pd.Series' | — |
| `span` | 'int' | — |

## `keltner_channels(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', ema_window: 'int' = 20, atr_window: 'int' = 10, mult: 'float' = 2.0) -> 'pd.DataFrame'`

EMA(center) +/- mult * ATR bands.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `ema_window` | 'int' | 20 |
| `atr_window` | 'int' | 10 |
| `mult` | 'float' | 2.0 |

## `macd(s: 'pd.Series', fast: 'int' = 12, slow: 'int' = 26, signal: 'int' = 9) -> 'pd.DataFrame'`

MACD line / signal line / histogram.

| Parameter | Type | Default |
|---|---|---|
| `s` | 'pd.Series' | — |
| `fast` | 'int' | 12 |
| `slow` | 'int' | 26 |
| `signal` | 'int' | 9 |

## `mom(close: 'pd.Series', window: 'int' = 10) -> 'pd.Series'`

Momentum: c - c[n].

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `window` | 'int' | 10 |

## `obv(close: 'pd.Series', volume: 'pd.Series') -> 'pd.Series'`

On-Balance Volume (Granville 1963). Unchanged close -> 0 contribution.

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `volume` | 'pd.Series' | — |

## `parabolic_sar(high: 'pd.Series', low: 'pd.Series', af_start: 'float' = 0.02, af_step: 'float' = 0.02, af_max: 'float' = 0.2) -> 'pd.Series'`

Parabolic SAR (Wilder 1978). Not vectorizable by nature (path-dependent acceleration factor) — implemented as a tight python loop over numpy arrays. len < 2 -> all-NaN output.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `af_start` | 'float' | 0.02 |
| `af_step` | 'float' | 0.02 |
| `af_max` | 'float' | 0.2 |

## `roc(close: 'pd.Series', window: 'int' = 12) -> 'pd.Series'`

Rate of change in percent: 100 * (c/c[n] - 1).

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `window` | 'int' | 12 |

## `rsi(close: 'pd.Series', period: 'int' = 14) -> 'pd.Series'`

Relative Strength Index in [0, 100].

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `period` | 'int' | 14 |

## `sma(s: 'pd.Series', window: 'int') -> 'pd.Series'`

Simple moving average. NaN until ``window`` samples available.

| Parameter | Type | Default |
|---|---|---|
| `s` | 'pd.Series' | — |
| `window` | 'int' | — |

## `stochastic(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', k_window: 'int' = 14, d_window: 'int' = 3, smooth_k: 'int' = 3) -> 'pd.DataFrame'`

%K (smoothed) and %D. h == l over the window -> %K NaN.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `k_window` | 'int' | 14 |
| `d_window` | 'int' | 3 |
| `smooth_k` | 'int' | 3 |

## `trix(close: 'pd.Series', window: 'int' = 15) -> 'pd.Series'`

TRIX (Hutson 1991): 1-bar ROC (in %) of a triple EMA.

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `window` | 'int' | 15 |

## `true_range(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series') -> 'pd.Series'`

True range; first bar falls back to high - low.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |

## `vwap_session(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', volume: 'pd.Series', session: 'str' = '1D') -> 'pd.Series'`

Session-anchored VWAP of the typical price (h+l+c)/3.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `volume` | 'pd.Series' | — |
| `session` | 'str' | '1D' |

## `willr(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series', window: 'int' = 14) -> 'pd.Series'`

Williams %R in [-100, 0]. h == l -> NaN.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `close` | 'pd.Series' | — |
| `window` | 'int' | 14 |

## `wma(s: 'pd.Series', window: 'int') -> 'pd.Series'`

Linearly weighted moving average (recent bars weigh more).

| Parameter | Type | Default |
|---|---|---|
| `s` | 'pd.Series' | — |
| `window` | 'int' | — |
