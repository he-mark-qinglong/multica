# SPEC — vpvr_reversion_1m_kama_reversal_20260709 (iter#67 V3)

**Strategy key**: `vpvr_reversion_1m_kama_reversal_20260709`
**Iteration**: iter#67 (campaign SMA-31410 V3)
**Primary timeframe**: 1m
**Universe**: SOLUSDT (Binance USD-M linear perp)
**Axis**: Kaufman Adaptive Moving Average (KAMA) slope reversal as the directional trigger, gated on VPVR POC reversion proximity.

## Why this variant

V3 of SMA-31410 explores *non-fundamental, microstructure-only* reversion
signals. Unlike V1 (VWAP trail — momentum ex-BTC) and V2 (Donchian regime —
breakout skip), V3 uses a pure indicator-based reversal detector:

- **Kaufman Adaptive Moving Average** (Kaufman 1995, "Smarter Trading") —
  adapts its smoothing constant between a fast EMA (period 2) and a slow
  EMA (period 30) based on an Efficiency Ratio.
- **VPVR POC** rolling over a 1-day window (1440 bars @ 1m) with 24
  bins — the structural "fair value" liquidity level.
- The two combine: when KAMA turns around *and* the close is within 0.6
  ATR of POC, mean-reversion is the higher-probability outcome (price
  overshoots POC, indicator snaps, then snaps back).

## New-axis differentiation (≥2 axes vs existing variants)

| axis | this variant | nearest existing | difference |
|---|---|---|---|
| entry signal | KAMA-slope turnaround | `vpvr_reversion_1d_20260621_kama_er` (V1: 1d, ER-derived KAMA filter, not turnaround signal) | different timeframe, different KAMA use |
| timeframe | 1m | `vpvr_reversion_5m_vwap_trail_20260709` (V1 of iter#65-69), `vpvr_reversion_15m_donchian_regime_20260709` (V2 of iter#65-69) | genuinely different intra-day cadence |
| indicator logic | KAMA slope *turnaround* (sign change over lookback) | existing variants use VWAP / Donchian / vol filters | different primitive |

No existing `vpvr_*` / `bb_*` / `xs_*` strategy uses KAMA slope-turnaround as
the primary entry trigger on 1m (dedup confirmed via directory sweep).

## Data

- 1m Binance USD-M klines for SOLUSDT (`fapi_SOLUSDT__1m.parquet`).
- Span: 2024-04-23 → 2026-06-23 (1,140,096 bars, 100% 1-min cadence, no gaps).
- Index: `openTime` (UTC-naive → coerced to UTC).

## Indicators

- **KAMA(close, period=10, fast=2, slow=30)** — adaptive smoothing.
- **KAMA slope turnaround**: `slope_now = KAMA[t] - KAMA[t-1]`,
  `slope_then = KAMA[t-lookback] - KAMA[t-lookback-1]`, signal when
  sign flips AND `|slope_now - slope_then| / ATR >= 0.20` (default).
- **VPVR POC**: rolling 1,440-bar window, 24 bins.
- **ATR(14)** on 1m.

## Entry (long)

1. `kama_turn[t] == +1` (slope just flipped negative→non-negative by ≥0.20 ATR)
2. `close[t] <= vpvr_poc[t]`
3. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer` (0.6 ATR)
4. `bars_since_exit >= cooldown_bars` (5 bars)

## Entry (short)

1. `kama_turn[t] == -1` (slope flipped positive→non-positive by ≥0.20 ATR)
2. `close[t] >= vpvr_poc[t]`
3. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer`
4. `cooldown_bars` cleared

## Exit

- Take profit: `+take_profit_atr_k × ATR` (1.5 ATR)
- Hard stop: `-hard_stop_atr_k × ATR` (1.0 ATR)
- Time stop: 30 bars max hold

## Costs & sizing

- Fee: 4 bps per fill; slippage: 1 bp per fill (10 bps round-trip).
- Sizing: `risk_target_pct = 0.005` of NAV per trade (vol-target lite).

## Walk-forward (3 folds)

- 2024-Q3: test 2024-10-01 → 2024-12-31
- 2025-Q2: test 2025-04-01 → 2025-06-30
- 2025-Q4: test 2025-10-01 → 2025-12-31

## Acceptance

- Sharpe ≥ 1.0, ann_return ≥ 15%, max_drawdown > -25%, profit_factor > 1.5.
- n_trades ≥ 50 across the full sample (1m cadence typically produces 100+).

## B3 evidence gate

1. `pytest -v tests/test_signals.py` ≥ 1 PASS
2. `python run_backtest.py` writes `results/{summary,metrics}.json` + `results/trades_1m_solusdt.csv`
3. `cat results/metrics.json` — real numbers
