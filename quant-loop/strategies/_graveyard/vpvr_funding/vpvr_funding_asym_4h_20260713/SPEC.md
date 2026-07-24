# V3 — vpvr_funding_asym_4h_20260713 (iter#92)

VPVR POC + funding asymmetry with asymmetric execution (TP:SL = 4:1.5).

## Data

| field | source | path |
|---|---|---|
| OHLCV | binance_usdm 4h | `live_data/BTCUSDT_4h.parquet`, `ETHUSDT_4h.parquet` (9912 bars each, 2022-01-01 → 2026-07-10) |
| Funding | Bybit linear + Binance fallback | `funding_analysis/{SYM}_bybit_funding.parquet` (8h events, ffilled to 4h) |

Funding annualized in basis points: `fundingRate * 3 (events/day) * 365 * 10000`.

## Universe
BTCUSDT + ETHUSDT (independent sizing per symbol, combined PnL book).

## Indicators
- VPVR POC: rolling 180-bar window (30 days @ 4h), 24 bins.
- ATR: 14-bar.
- Funding rolling z-score: 360-bar lookback.
- Funding annualized basis points.

## Entry (long)
1. Annualized funding < -10 bps (extremely negative — shorts paying longs).
2. Funding z-score < -1.5 (significant deviation).
3. Price within 1.0 ATR of VPVR POC.

## Entry (short) — symmetric
1. Annualized funding > +10 bps.
2. Funding z-score > +1.5.
3. Price within 1.0 ATR of VPVR POC.

## Exit (asymmetric)
- Take profit: +4.0 ATR (cycle-46 lesson: 2:1 was insufficient, use 4:1.5).
- Hard stop: -1.5 ATR.
- Time stop: 90 bars (15 days @ 4h).

## Costs
- Fee: 0.04% per fill (4 bps).
- Slippage: 0.02% per fill (2 bps).
- Funding carry: ±0.01% per 4h bar the position is held.

## Walk-forward splits (3 folds)
- 2024-Q1: train 2023, test 2024-Q1
- 2024-Q3: train 2023-Q3→2024-Q2, test 2024-Q3
- 2025-Q2: train 2024-Q2→2025-Q1, test 2025-Q2

## Verdict thresholds
- Sharpe ≥ 1.0 → [PROFITABLE]
- Sharpe < 1.0 → [NOT-PROFITABLE], archived.

## Honest caveats
1. Cycle-46 family exhaustion: `vpvr_funding_delta_*` and `vpvr_funding_*`
   are closed families. This is the cycle-47 single rebuild permitted by
   campaign-tree rules.
2. Funding carry drags on hold time. A trade that hits +4 ATR over 90 bars
   pays 0.01% × 90 = 0.9% in carry, which is meaningful.
3. ETHUSDT funding is more volatile than BTCUSDT (cycle-46 stats: ETH p99
   |funding| ≈ 4.3 bps vs BTC 3.9 bps) so ETH will likely have more
   asymmetric moves but also more whipsaws.