# vpvr_funding_carry_asym_v2 — VPVR confluence + funding EMA(differential)

> SMA-34990. V2 supersedes V1 (SMA-34652 / `funding_carry_asym`).
> V1 failed on cost fragility (W5 divergence: in-house +4.19 Sharpe vs
> freqtrade -0.22). V2 uses the new shared cost model + CPCV validation.

## Hypothesis

Perpetual funding rates carry an asymmetric edge: when funding-mean on
one asset deviates far from its trailing baseline, the carry cost
pressures the dominant side to flatten. Combine that with a volume-
distribution gate (price sitting in the cheap half of the value area,
or the rich half) to time entries, then confirm with a higher-TF
trend filter so we don't fade a strong move.

The edge lives at the 15m cadence with 1m execution and 4h trend;
multi-TF confirmation is the whole point.

## Signal design

### Primary — funding EMA(7d) differential

- Funding rate (Binance USDT-M perp, 8h cadence).
- Compute a 7-day EMA of the funding rate (21 funding events).
- ENTER long when funding_ema > +0.0001 (1 bp per 8h, statistically
  significant).
- ENTER short when funding_ema < -0.0001.
- No-look-ahead: funding at bar `t` is the rate paid strictly before
  bar `t`'s open (ffill of events onto bar index, then `shift(1)`).
- Window is computed on the 8h-event cadence, not per bar.

### Confluence — 15m VPVR VAH/VAL band

- Rolling VPVR over a 30-day trailing window (180 × 15m bars) at the
  15m cadence.
- Snapshot the VAH/VAL every 16 bars (4h of 15m bars).
- ENTER long only if `close <= midpoint(VAH, VAL)` (price in the
  lower half of the value area — cheap relative to distribution).
- ENTER short only if `close >= midpoint(VAH, VAL)`.

### Trend filter — 4h EMA(50) slope

- 4h EMA(50) of close.
- Slope = `ema(close, 50).diff()` per 4h bar.
- ENTER long only if `slope > 0`.
- ENTER short only if `slope < 0`.

### Timeframe alignment

- **1m** — execution (TP/SL/time-stop on 1m bars).
- **15m** — signal (funding EMA + VPVR VAH/VAL band).
- **4h** — trend filter (EMA(50) slope direction).

All three are reindexed to the 1m bar stream via ffill (the 4h/15m
level at a 1m bar is the most recent 4h/15m bar's value).

## Costs, sizing, validation

- **Cost model**: `_shared/execution/cost_model.apply_cost()` with
  `BINANCE_FUTURES` (4 bp taker) and square-root slippage. Round-trip
  cost is computed at each entry/exit from the notional and ADV.
- **Position sizing**: `_shared/sizing/vol_target.apply_vol_target()`
  with `target_vol=0.20` (perp funding is vol-heavier).
- **Metrics validation**: `_shared/validators/metrics_validator.validate_metrics()`
  on every backtest result before any SHIP claim.
- **OOS validation**: `_shared/validation/cpcv.cpcv()` with
  `n_groups=6, k_test=2, purge_bars=200, embargo_bars=100` per
  `_shared/validation/README.md`. DSR computed for multiple-testing
  correction.

## Acceptance criteria (all must hold for SHIP)

1. Mean OOS Sharpe across CPCV paths ≥ **1.2**.
2. **Deflated Sharpe Ratio** > 0.5 — accounts for the 50+ strategies
   tried in this campaign.
3. Profit factor on aggregated OOS trades ≥ **1.4**.
4. Max drawdown > -25% on aggregated OOS.
5. ≥30 trades per CPCV fold (statistical power).
6. Walk-forward consistency: std of per-fold Sharpe < 1.5.

## Symbols

- BTCUSDT (primary, deepest funding history).
- ETHUSDT (secondary, only after BTC passes).
- SOLUSDT (tertiary, only after BTC+ETH pass).

## Out of scope (V2)

- Cross-symbol spread (left to pairs family).
- Options-implied funding (left to vol surface family).
- On-chain carry (left to defi family).
- 1d TF (banned by smark directive 2026-07-11).

## Anti-patterns explicitly forbidden

- Hardcoded cost (use shared `cost_model.apply_cost()`).
- Fixed `risk_target_pct` (use shared `vol_target.apply_vol_target()`).
- Single-window OOS (use shared `cpcv.cpcv()`).
- Multiple-testing without DSR.