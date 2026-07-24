# SPEC — vpvr_sentiment_attention_1m_20260716 (iter#71)

**Strategy key**: `vpvr_sentiment_attention_1m_20260716`
**Iteration**: iter#71
**Primary timeframe**: 1m
**Universe**: BTCUSDT (Binance USD-M linear perp)
**Axis**: social-attention / sentiment spike (LunarCrush / Santiment) as VPVR reversion confirmation

## Authoritative spec

This is the first VPVR variant to use **crypto social-attention spikes** as a
directional filter. The core idea is that extreme attention (e.g. LunarCrush
"social volume" or Santiment "social dominance") is a short-term sentiment
surrogate: when attention spikes while price is near a high-volume node, the
crowd is usually chasing a local move, creating a short-term mean-reversion
opportunity.

## New-axis differentiation (≥2 axes vs existing variants)

| axis | this variant | nearest existing | difference |
|---|---|---|---|
| data source | LunarCrush / Santiment social-attention metrics | none in `^vpvr_|^bb_|^xs_` | genuinely new axis |
| primary TF | 1m | `vpvr_iceberg_fade_5m_20260711` (microstructure) | different timeframe |
| filter variable | attention z-score (crowdedness) | existing variants use funding/oi/liquidation/OB/volume | new behavioral filter |

No existing `vpvr_*`, `bb_*`, or `xs_*` strategy references LunarCrush, Santiment,
social volume, social dominance, or attention metrics (dedup evidence below).

## Data

- 1m Binance USD-M klines for BTCUSDT.
- 1m attention proxy: social-volume z-score from LunarCrush or Santiment,
  resampled / forward-filled to the 1m bar grid.
  - `attention_z[t] = (social_volume[t] - rolling_mean[social_volume, lookback]) / rolling_std[social_volume, lookback]`
- Span: 2022-01-01 → 2026-07-10.

## Indicators

- **VPVR POC**: rolling 1,440-bar window (1 day @ 1m), 24 bins.
- **ATR**: 14-bar on 1m.
- **Attention z-score**: rolling 360-bar lookback (6h @ 1m).

## Entry (long)

1. `close[t] <= poc[t]` (price at or below POC — locally weak).
2. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer` (default 0.5).
3. `attention_z[t] >= attention_z_threshold` (default +2.0): attention spike
   confirms the local sell-off is crowded → reversion long.
4. No position held and `bars_since_exit >= cooldown_bars`.

## Entry (short)

1. `close[t] >= poc[t]` (price at or above POC — locally strong).
2. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer`.
3. `attention_z[t] >= attention_z_threshold`: attention spike confirms the
   local rally is crowded → reversion short.
4. No position held and `bars_since_exit >= cooldown_bars`.

## Exit

- Take profit: `+take_profit_atr_k × ATR` (default 1.5 ATR).
- Hard stop: `-hard_stop_atr_k × ATR` (default 1.0 ATR).
- Time stop: `max_hold_bars` (default 30 bars = 30 min).

## Costs

- Fee: 4 bps per fill.
- Slippage: 1 bp per fill.

## Position sizing

- Vol-target: `risk_target_pct = 0.005` of NAV per trade.

## Walk-forward splits

- 2024-Q1: train 2023, test 2024-Q1
- 2024-Q3: train 2023-Q3→2024-Q2, test 2024-Q3
- 2025-Q2: train 2024-Q2→2025-Q1, test 2025-Q2

## Acceptance

- Sharpe ≥ 1.0, ann_return ≥ 15%, max_drawdown < 25%, pf > 1.5.
- n_trades ≥ 100 per fold (1m variants need meaningful sample size).
