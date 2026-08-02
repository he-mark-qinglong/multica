# `_shared.regime.btc_gate`

Source: `_shared/regime/btc_gate.py`

BTC regime classifier — shared across strategies.

## class `FundingRegime(*values)`

## class `RegimeSnapshot(timestamp: pandas.Timestamp, trend: _shared.regime.btc_gate.TrendRegime, vol: _shared.regime.btc_gate.VolRegime, funding: _shared.regime.btc_gate.FundingRegime, ema_fast: float, ema_slow: float, atr_percentile: float, funding_ema_7d: float) -> None`

RegimeSnapshot(timestamp: pandas.Timestamp, trend: _shared.regime.btc_gate.TrendRegime, vol: _shared.regime.btc_gate.VolRegime, funding: _shared.regime.btc_gate.FundingRegime, ema_fast: float, ema_slow: float, atr_percentile: float, funding_ema_7d: float)

## class `TrendRegime(*values)`

## class `VolRegime(*values)`

## `classify_funding(funding_series: pandas.Series, window: int = 21) -> _shared.regime.btc_gate.FundingRegime`

Funding regime from EMA of funding rate (per-8h values).

| Parameter | Type | Default |
|---|---|---|
| `funding_series` | pandas.Series | — |
| `window` | int | 21 |

## `classify_trend(ema_fast: float, ema_slow: float, adx: float = 0.0) -> _shared.regime.btc_gate.TrendRegime`

Trend from EMA cross, optionally gated by ADX.

| Parameter | Type | Default |
|---|---|---|
| `ema_fast` | float | — |
| `ema_slow` | float | — |
| `adx` | float | 0.0 |

## `classify_vol(atr_series: pandas.Series, window: int = 100) -> _shared.regime.btc_gate.VolRegime`

Vol regime from ATR percentile over rolling window.

| Parameter | Type | Default |
|---|---|---|
| `atr_series` | pandas.Series | — |
| `window` | int | 100 |

## `regime_series(ohlcv_4h: pandas.DataFrame, funding_8h: pandas.Series | None = None, ema_fast_period: int = 20, ema_slow_period: int = 50) -> pandas.DataFrame`

Compute regime at every bar of `ohlcv_4h` (vectorized where possible).

| Parameter | Type | Default |
|---|---|---|
| `ohlcv_4h` | pandas.DataFrame | — |
| `funding_8h` | pandas.Series | None | None |
| `ema_fast_period` | int | 20 |
| `ema_slow_period` | int | 50 |

## `regime_snapshot(ohlcv_4h: pandas.DataFrame, funding_8h: pandas.Series | None = None, ema_fast_period: int = 20, ema_slow_period: int = 50) -> _shared.regime.btc_gate.RegimeSnapshot`

Compute regime at the latest bar of `ohlcv_4h`.

| Parameter | Type | Default |
|---|---|---|
| `ohlcv_4h` | pandas.DataFrame | — |
| `funding_8h` | pandas.Series | None | None |
| `ema_fast_period` | int | 20 |
| `ema_slow_period` | int | 50 |
