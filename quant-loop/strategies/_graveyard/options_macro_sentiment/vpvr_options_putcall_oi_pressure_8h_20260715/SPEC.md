# SPEC — vpvr_options_putcall_oi_pressure_8h_20260715 (iter#71)

**Strategy key**: `vpvr_options_putcall_oi_pressure_8h_20260715`
**Iteration**: iter#71
**Primary timeframe**: 8h
**Universe**: ETHUSDT (Binance USD-M linear perp)
**Axis**: VPVR POC reversion gated by put-call OI pressure proxy (taker_buy_share)

## Authoritative spec

This variant adds the **put-call open-interest pressure axis** to the VPVR
family. It is distinct from existing options variants
(`vpvr_options_iv_termstructure_4h_20260715` and
`vpvr_options_iv_skew_1d_20260713`) in three ways:

1. **Signal axis**: pressure proxy derived from buy-aggressive volume fraction
   rather than IV term-structure or IV skew. Models the same informational role
   that put/call OI imbalance plays in dealer-hedging frameworks — when one
   side is over-positioned, the price tends to revert.
2. **Timeframe**: 8h (versus 4h IV-term / 1d IV-skew). Aligns naturally with
   the 8h funding cadence and the campaign's 1m/15m/4h primary focus (extended
   to 8h for options-style cadence).
3. **Entry trigger**: VPVR POC proximity filter combined with PCR-z extremes,
   matching the family contract.

## Data

- Source: `/home/smark/multica/quant-loop/data/perp_30m/ETHUSDT_30m.parquet`
  (Binance USD-M 30m klines, 2022-01-01 → 2026-07-10, 79296 rows).
- Aggregated to 8h OHLCV (open=first, high=max, low=min, close=last,
  volume=sum, quote_volume=sum, taker_buy_quote=sum). Bar count after
  aggregation: ≈4956, matching `data/manifest.txt`.
- Put-call ratio proxy: `pcr_proxy[t] = sum(taker_buy_quote) / sum(quote_volume)`
  on the 8h bar, clipped to [0, 1].
  - `pcr_proxy → 1.0` ⇒ buyers aggressive ⇒ **call-side pressure extreme**
  - `pcr_proxy → 0.0` ⇒ sellers aggressive ⇒ **put-side pressure extreme**

## Indicators

- **VPVR POC**: rolling 90-bar window (≈30 days @ 8h), 24 bins.
- **ATR**: 14-bar on 8h.
- **PCR z-score**: rolling 90-bar (≈30 days @ 8h) z-score of the PCR proxy.

## Entry (long)

1. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer` (default 0.75).
2. `pcr_z[t] < -pcr_z_threshold` (default -1.5): put-side pressure extreme →
   contrarian long.
3. No position held and `bars_since_exit >= cooldown_bars`.

## Entry (short)

1. `|close[t] - vpvr_poc[t]| / atr[t] <= poc_atr_buffer`.
2. `pcr_z[t] > +pcr_z_threshold` (default +1.5): call-side pressure extreme →
   contrarian short.
3. No position held and `bars_since_exit >= cooldown_bars`.

## Exit

- Take profit: `+take_profit_atr_k × ATR` (default 2.5 ATR).
- Hard stop: `-hard_stop_atr_k × ATR` (default 1.5 ATR).
- Time stop: `max_hold_bars` (default 12 bars = 96 h = 4 days).

## Costs

- Fee: 4 bps per fill.
- Slippage: 2 bps per fill.
- Round-trip cost: 12 bps.

## Position sizing

- Vol-target: `risk_target_pct = 0.005` of NAV per trade.

## Walk-forward splits

- 2024-Q1: train 2023, test 2024-Q1
- 2024-Q3: train 2023-Q3→2024-Q2, test 2024-Q3
- 2025-Q2: train 2024-Q2→2025-Q1, test 2025-Q2

## Acceptance

- Sharpe ≥ 0.5 (campaign gate; <0.5 → [NOT-PROFITABLE]).
- n_trades ≥ 30 across the test window.
- max_drawdown magnitude < 50%.

## Notes / limitations

- `data/manifest.txt` flags `OPTIONS-DATA-MISSING` with proxy
  `pcr=taker_buy_share_proxy` — strategy is documented to be a proxy, not a
  true PCR signal. If real Deribit/Binance options OI data becomes available,
  swap `taker_buy_share` for `put_oi / (put_oi + call_oi)` without changing
  the rest of the pipeline.
- 8h aggregation was chosen over 4h to (a) match funding cadence and (b)
  produce enough PCR variability for the 90-bar z-score to be meaningful.
