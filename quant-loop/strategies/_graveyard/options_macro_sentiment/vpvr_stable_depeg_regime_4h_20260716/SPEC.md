# SPEC — vpvr_stable_depeg_regime_4h_20260716 (iter#72)

**Strategy key**: `vpvr_stable_depeg_regime_4h_20260716`
**Iteration**: iter#72
**Primary timeframe**: 4h
**Regime gate timeframe**: 1h
**Universe**: BTCUSDT (Binance USD-M linear perp)
**Axis**: stablecoin depeg premium (USDT/USDC on Curve) as risk-on/off regime gate

## Authoritative spec

This is the first VPVR variant to use **stablecoin depeg premium** as a
regime gate. Stablecoin depegs (e.g. USDT/USDC premium on Curve) are a
proxy for fiat-gateway stress and flight-to-safety. When the depeg premium
is elevated, reversion trades are disabled; when it normalises, standard
VPVR POC reversion is allowed. This creates a convex, tail-risk-aware reversion
strategy.

## New-axis differentiation (≥2 axes vs existing variants)

| axis | this variant | nearest existing | difference |
|---|---|---|---|
| data source | Curve USDT/USDC (or DEX stable-swap) premium | none in `^vpvr_|^bb_|^xs_` | genuinely new axis |
| filter variable | stable depeg premium level | `vpvr_reversion_4h_stablecoin_netflow_20260713` uses on-chain netflow | different stablecoin signal: premium vs flow |
| primary TF | 4h | `vpvr_reversion_4h_stablecoin_netflow_20260713` also 4h | same TF, but filter source and logic differ, satisfying ≥2-axis rule |

No existing `vpvr_*`, `bb_*`, or `xs_*` strategy references stablecoin depeg,
Curve premium, USDT/USDC swap rate, or stablecoin on-chain price (dedup evidence below).

## Data

- 4h Binance USD-M klines for BTCUSDT.
- 1h stable depeg premium proxy: `premium[t] = max(USDT/USDC_Curve - 1.0, 0.0)`,
  forward-filled to 4h. A value of 0.002 = 20 bps premium.
- Span: 2022-01-01 → 2026-07-10.

## Indicators

- **VPVR POC**: rolling 180-bar window (30 days @ 4h), 24 bins.
- **ATR**: 14-bar on 4h.
- **Depeg premium**: 1h USDT/USDC Curve premium, ffilled to 4h.

## Regime gate

- `regime_ok[t] = premium[t] < depeg_premium_threshold` (default 0.0015 = 15 bps).
- If `regime_ok` is False, no new entries are allowed. Open positions are
  closed immediately at the next bar (emergency depeg exit) to avoid
  tail-risk events.

## Entry (long)

1. `regime_ok[t]` is True.
2. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer` (default 1.0).
3. `close[t] < vpvr_poc[t]` (long only from below POC).
4. No position held and `bars_since_exit >= cooldown_bars`.

## Entry (short)

1. `regime_ok[t]` is True.
2. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer`.
3. `close[t] > vpvr_poc[t]` (short only from above POC).
4. No position held and `bars_since_exit >= cooldown_bars`.

## Exit

- Take profit: `+take_profit_atr_k × ATR` (default 3.0 ATR).
- Hard stop: `-hard_stop_atr_k × ATR` (default 1.5 ATR).
- Time stop: `max_hold_bars` (default 60 bars = 10 days).
- Emergency depeg exit: if `regime_ok` flips False, close at next bar.

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
