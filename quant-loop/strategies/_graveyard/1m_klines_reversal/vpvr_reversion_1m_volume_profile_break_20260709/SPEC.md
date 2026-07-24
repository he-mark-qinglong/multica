# SPEC — vpvr_reversion_1m_volume_profile_break_20260709 (iter#69 V5)

**Strategy key**: `vpvr_reversion_1m_volume_profile_break_20260709`
**Iteration**: iter#69 (Spec V5)
**Primary timeframe**: 1m
**Universe**: BTCUSDT (Binance USD-M linear perp)
**Axis**: failed value-area breakout with volume confirmation as VPVR reversion edge

## Authoritative spec (V5)

V5 is the first VPVR variant to trade **failed value-area breakouts** on 1m
data. The intuition:

1. A *legitimate* breakout from the value area is a structural move that
   should hold; price stays outside the band for many bars.
2. A *failed* breakout — price pierces above VAH or below VAL with a
   volume spike but immediately re-enters the value area — is a liquidity
   grab. Informed participants took the other side. Mean-reversion back
   toward POC is the highest-probability next move.

The signal is the **re-enter bar**, not the breakout bar itself; we never
chase the initial move. Entry is sized small (vol-target 0.5% NAV per
trade) because 1m data needs a high trade count for the campaign's
Sharpe ≥ 1.0 / ann ≥ 15% bar to be reliable.

## New-axis differentiation (≥2 axes vs existing variants)

| axis | this variant | nearest existing variant | difference |
|---|---|---|---|
| entry trigger | failed value-area breakout + volume spike + re-enter bar | `vpvr_reversion_1m_kama_reversal_20260709` (KAMA flip) | completely different trigger |
| confirmation | relative volume (vol / rolling median) above `vol_spike_k` | `vpvr_sentiment_attention_1m_20260716` uses attention z-score | different signal layer |
| structural anchor | POC + VAH/VAL of rolling volume profile | `vpvr_sentiment_attention_1m_20260716` uses single POC | value area matters, not just POC |

No existing `vpvr_*` strategy uses the failed-breakout + re-enter pattern
combined with relative-volume confirmation.

## Data

- 1m Binance USD-M klines for BTCUSDT.
- Source: `strategies/vpvr_reversion_1m_volume_profile_break_20260709/data/fapi_BTCUSDT__1m.parquet`
- Span: 2024-04-23 → 2026-06-23 (≈1.14M bars).
- Columns: `open, high, low, close, volume`.

## Indicators

- **VPVR profile**: rolling 1,440-bar window (1 day @ 1m), 24 bins.
  Computed via `_vpvr_poc` (POC) and a dedicated `_vpvr_value_area`
  helper that emits VAH/VAL by expanding outward from POC until 70% of
  the window's volume is covered.
- **ATR**: 14-bar, close-based.
- **Relative volume**: `vol_ratio = volume / rolling_median(volume, 360)`,
  i.e. instantaneous volume vs the rolling 6h baseline.

## Entry (short — failed upside break)

1. Within the last `break_lookback_bars` bars (default 12), `close` was
   above `vah` on at least one bar AND `vol_ratio >= vol_spike_k`
   (default 2.0) on at least one of those breakout bars.
2. Current `close <= vah` (price re-entered the value area from above).
3. `close > poc` (still above POC; we are not chasing through the range).
4. `vol_ratio` no longer in spike regime (`vol_ratio < vol_spike_k`) on
   the entry bar — selling pressure is exhausted.
5. No position held and `bars_since_exit >= cooldown_bars`.

## Entry (long — failed downside break)

1. Within the last `break_lookback_bars` bars, `close` was below `val`
   on at least one bar AND `vol_ratio >= vol_spike_k` on at least one
   of those break-down bars.
2. Current `close >= val` (price re-entered the value area from below).
3. `close < poc`.
4. `vol_ratio < vol_spike_k` on the entry bar — buying pressure
   exhausted.
5. Cooldown respected.

## Exit

- **Take profit (mean-reversion target)**: target distance is
  `tp_atr_k × ATR` (default 1.0) measured from the entry price in the
  favorable direction (`+tp_atr_k × atr/entry_px` for long,
  `-tp_atr_k × atr/entry_px` for short).
- **Hard stop**: `sl_atr_k × ATR` (default 1.5) against the trade
  (so `-sl_atr_k × atr/entry_px` for long); wider than kama baseline
  because a failed-break re-entry can chop.
- **Breakout-resume stop**: price *re*-breaks beyond the previous
  extreme (`vah` for shorts, `val` for longs). This is the
  signal-specific stop: if the breakout resumes, our thesis is broken.
- **Time stop**: `max_hold_bars` (default 30 = 30 minutes).

## Costs

- Fee: 4 bps per fill.
- Slippage: 1 bp per fill.

## Position sizing

- Vol-target: `risk_target_pct = 0.005` of NAV per trade.
- Compounded mark-to-market on every bar the position is held.

## Walk-forward splits

- 2024-Q3: train 2024-Q2, test 2024-Q3
- 2025-Q1: train 2024-Q3→2024-Q4, test 2025-Q1
- 2025-Q3: train 2025-Q1→2025-Q2, test 2025-Q3

(Training-phase data window depends on data start; see `walk_forward.folds`
in `config.json`. The B3 run below reports metrics on the full
2024-04-23 → 2026-06-23 span since the spec V5 ships with frozen
parameters; fold-level PnL is left to the walk-forward sub-issue.)

## Acceptance

- Sharpe ≥ 1.0, ann_return ≥ 15%, max_drawdown > -25%, profit factor > 1.5.
- n_trades ≥ 200 over the dataset (1m window is 14 months × ~30 trades
  per day = ~13k, so 200 trades is a low bar; we want > 1k to trust
  the variance).

## Notes

- This is iter#69. The two prior iters explored (1) raw POC touch and
  (2) HVN-only reversion; both produced positive but noisy equity
  curves. V5 adds the failed-breakout trigger and the relative-volume
  confirmation on top of the POC reversion base.
- B3 evidence gate: `pytest` on `tests/test_signals.py`, plus the
  contents of `results/metrics.json`.
