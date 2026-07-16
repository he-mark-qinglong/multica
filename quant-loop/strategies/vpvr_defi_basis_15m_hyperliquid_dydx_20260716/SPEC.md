# SPEC — vpvr_defi_basis_15m_hyperliquid_dydx_20260716 (iter#70)

**Strategy key**: `vpvr_defi_basis_15m_hyperliquid_dydx_20260716`
**Iteration**: iter#70
**Primary timeframe**: 15m
**Filter timeframe**: 1h
**Universe**: BTCUSDT (Binance USD-M linear perp)
**Axis**: DeFi perp cross-venue basis (Hyperliquid / dYdX vs Binance USD-M) + VPVR POC reversion

## Authoritative spec

This is the first VPVR variant to use **DeFi perpetual venue basis** as a
regime / entry-quality filter. CEX cross-basis (`vpvr_xs_basis_*`) already
exists, but no prior variant sources its basis from decentralised perp venues
(Hyperliquid and dYdX). The hypothesis is that DeFi perps often lag CEX during
stress, so an extreme DeFi-CEX basis prints a temporary mispricing that
reverts once CEX liquidity catches up.

## New-axis differentiation (≥2 axes vs existing variants)

| axis | this variant | nearest existing | difference |
|---|---|---|---|
| data source | Hyperliquid + dYdX perp mark prices | `vpvr_xs_basis_15m_cross_exchange_20260713` uses Binance/OKX/Bybit CEX | venue-type axis: DeFi perps vs CEX |
| filter variable | DeFi-CEX basis z-score, 1h | `vpvr_xs_basis_*` uses CEX basis z-score | different basis construction and venue universe |
| primary TF | 15m | `vpvr_xs_basis_15m_cross_exchange_20260713` also 15m | same TF, but combined with different filter source makes ≥2 axes distinct |

## Data

- 15m Binance USD-M klines for BTCUSDT (primary price / volume).
- 1h DeFi perp basis proxy: synthetic or fetched from Hyperliquid / dYdX API.
  - `basis_hl[t] = (HL_BTCUSDT_mark - binance_close) / binance_close`
  - `basis_dy[t] = (dYdX_BTCUSD_mark - binance_close) / binance_close`
  - Combined basis = mean(basis_hl, basis_dy), re-sampled to 15m by forward-fill.
- Span: 2022-01-01 → 2026-07-10.

## Indicators

- **VPVR POC**: rolling 480-bar window (5 days @ 15m), 24 bins.
- **ATR**: 14-bar on 15m.
- **DeFi basis z-score**: rolling 168-bar lookback (≈1.75 days @ 15m) on the
  combined DeFi-CEX basis.

## Entry (long)

1. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer` (default 0.75).
2. `basis_z[t] < -basis_z_threshold` (default -1.5): DeFi perps trade at a
   discount to Binance → Binance is relatively cheap, reversion long.
3. No position held and `bars_since_exit >= cooldown_bars`.

## Entry (short)

1. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer`.
2. `basis_z[t] > +basis_z_threshold` (default +1.5): DeFi perps trade at a
   premium to Binance → Binance is relatively rich, reversion short.
3. No position held and `bars_since_exit >= cooldown_bars`.

## Exit

- Take profit: `+take_profit_atr_k × ATR` (default 2.5 ATR).
- Hard stop: `-hard_stop_atr_k × ATR` (default 1.5 ATR).
- Time stop: `max_hold_bars` (default 60 bars = 15h).

## Costs

- Fee: 4 bps per fill.
- Slippage: 2 bps per fill.

## Position sizing

- Vol-target: `risk_target_pct = 0.005` of NAV per trade.

## Walk-forward splits

- 2024-Q1: train 2023, test 2024-Q1
- 2024-Q3: train 2023-Q3→2024-Q2, test 2024-Q3
- 2025-Q2: train 2024-Q2→2025-Q1, test 2025-Q2

## Acceptance

- Sharpe ≥ 1.0, ann_return ≥ 15%, max_drawdown < 25%, pf > 1.5.
- n_trades ≥ 30 per fold.
