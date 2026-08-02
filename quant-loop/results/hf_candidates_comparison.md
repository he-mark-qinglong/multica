# High-Frequency Strategy Candidates Comparison

> Focus: 1m / 5m timeframe strategies. Generated from results-ledger.md.

## Summary

- Total HF strategies evaluated: 21
- PASS: 0
- HOLD: 1
- KILL: 15
- UNTESTED: 5

## Detailed Comparison

| Strategy | TF | Status | Sharpe(in-house) | BT Sharpe | FT Sharpe | VBT Sharpe | PF | maxDD | Trades | Verdict |
|----------|----|--------|------------------|-----------|-----------|------------|----|-------|--------|---------|
| ``loid_iceberg_v4_1m_20260720`` | 1m | ACTIVE | 2.468 | — | — | — | 1.49 | -130.830 | 59268 | HOLD |
| ``mtf_h2_vpvr_edge_1m_15m_2h_20260718`` | 1m | ACTIVE | — | — | — | — | — | — | — | UNTESTED |
| ``mtf_vpvr_edge_zscore_1m_15m_2h_20260718`` | 1m | ACTIVE | — | — | — | — | — | — | — | UNTESTED |
| ``mtf_xs_pairs_1m_15m_2h_h3_20260718`` | 1m | ACTIVE | — | — | — | — | — | — | — | UNTESTED |
| ``vol_breakout_vpvr_val_fade_1h_5m_20260714`` | 5m | ACTIVE | — | — | — | — | — | — | — | UNTESTED |
| ``vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720`` | 1m | ACTIVE | — | — | — | — | — | — | — | UNTESTED |
| ``bb_reversion_rsi_1m_20260707`` | 1m | GRAVEYARD(1m_klines_reversal) | 2.081 | — | — | — | — | -0.001 | 26945 | KILL |
| ``bb_reversion_rsi_1m_20260707_p3opt_049`` | 1m | GRAVEYARD(1m_klines_reversal) | 2.159 | — | — | — | — | -0.001 | 29768 | KILL |
| ``bb_reversion_rsi_1m_20260707_p3opt_050`` | 1m | GRAVEYARD(1m_klines_reversal) | 1.152 | — | — | — | — | -0.001 | 17473 | KILL |
| ``bb_reversion_rsi_1m_20260707_p3opt_099`` | 1m | GRAVEYARD(1m_klines_reversal) | 1.870 | — | — | — | — | -0.002 | 27313 | KILL |
| ``bb_reversion_rsi_1m_20260707_p3opt_100`` | 1m | GRAVEYARD(1m_klines_reversal) | 1.870 | — | — | — | — | -0.002 | 27313 | KILL |
| ``vpvr_iceberg_fade_5m_20260711`` | 5m | GRAVEYARD(1m_klines_reversal) | — | — | — | — | — | — | — | KILL |
| ``vpvr_iceberg_fade_v2_5m_20260711`` | 5m | GRAVEYARD(1m_klines_reversal) | — | — | — | — | — | — | — | KILL |
| ``vpvr_microstructure_5m_volume_delta_20260710`` | 5m | GRAVEYARD(1m_klines_reversal) | — | — | — | — | — | — | — | KILL |
| ``vpvr_mtf_reversion_5m_consensus_20260710`` | 5m | GRAVEYARD(1m_klines_reversal) | — | — | -6.344 | — | — | -0.008 | 6689 | KILL |
| ``vpvr_obi_micro_v2_1m_20260714`` | 1m | GRAVEYARD(1m_klines_reversal) | — | — | — | — | — | — | — | KILL |
| ``vpvr_reversion_1m_kama_reversal_20260709`` | 1m | GRAVEYARD(1m_klines_reversal) | — | — | — | 13.700 | — | 0.000 | 2 | KILL |
| ``vpvr_reversion_1m_volume_profile_break_20260709`` | 1m | GRAVEYARD(1m_klines_reversal) | -22.654 | -46.178 | 0.000 | — | 0.12 | -2.668 | 6751 | KILL |
| ``vpvr_reversion_5m_vwap_trail_20260709`` | 5m | GRAVEYARD(1m_klines_reversal) | — | — | — | -9.621 | — | -0.016 | 6536 | KILL |
| ``vpvr_sentiment_attention_1m_20260716`` | 1m | GRAVEYARD(1m_klines_reversal) | -7.813 | -27.091 | 0.000 | — | 0.39 | -0.031 | 79 | KILL |
| ``vpvr_xs_leadlag_5m_20260711`` | 5m | GRAVEYARD(1m_klines_reversal) | — | 0.000 | 0.000 | — | 0.71 | -0.008 | 1495 | KILL |

## Key Insights

1. **Cost-cap dominates 1m/5m klines strategies**: All graveyarded 1m/5m strategies show negative framework CV Sharpe or in-house Sharpe that doesn't cover costs.
2. **mtf_xs_pairs H3 is the only positive-expectation HF candidate**: Multi-timeframe (1m entry + 15m sizing + 2h regime) is the proven template.
3. **loid_iceberg_v4 is the only untested HF axis with real data**: aggTrades order flow remains unproven but has exclusive data asset.
4. **bb_reversion_rsi shows classic high-Sharpe illusion**: In-house Sharpe 2.0+ but near-zero total return after costs.

## Next Actions

- Complete loid_iceberg_v4 90d parameter scan (Phase E).
- Evaluate H3 variants (H1/H2/H4) with the unified Phase B-D pipeline.
- Consider T10 sub-taker execution research to unlock microstructure edges.
