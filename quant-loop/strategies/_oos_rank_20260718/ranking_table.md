# OOS Walk-Forward Ranking — 3 vpvr_funding variants (2026-07-18)
Variants pulled from framework-validate 2026-07-18 series.
OOS = last 40% of persisted equity curve, walk-forward in non-overlapping windows.
Daily-resampled Sharpe = sqrt(365) × mean(daily_pct_change) / std(daily_pct_change).
Annualised return = (1 + total_ret)^(365/days) − 1 over the OOS portion.

| Rank | Variant | TF | Instruments | Folds | Pooled Sharpe (1d) | Pooled ann. return | Pooled max DD | G1 (Sharpe>=1) | G2 (ann>=15%) | G3 (DD>-50%) | n_pass |
|------|---------|----|-------------|-------|--------------------|--------------------|---------------|----------------|---------------|--------------|--------|
| 1 | `vpvr_funding_aware_v1_20260711` | 4h | BTCUSDT,ETHUSDT | 23 | 0.693 | 23.11% | -29.20% | ❌ | ✅ | ✅ | 2/3 |
| 2 | `vpvr_funding_asym_4h_20260713` | 4h | BTCUSDT,ETHUSDT | 23 | 0.630 | 0.00% | -0.13% | ❌ | ❌ | ✅ | 1/3 |
| 3 | `vpvr_funding_reset_window_1h_20260715` | 1h | BTCUSDT | 94 | -1.634 | -0.24% | -0.46% | ❌ | ❌ | ✅ | 1/3 |

## Provenance
- **Source**: persisted `equity_*.csv` files in `multica/quant-loop/strategies/<variant>/results/`.
- **Calendar assumption (inference)**: bars uniformly spaced at the strategy timeframe covering 2022-01-01 → 2026-07-10. Cross-checked against aware_v1 BTCUSDT 4h parquet (9912 rows, same span).
- **Verification**: every Sharpe / ann_return / max_dd number is computed directly from the on-disk equity CSV via `pandas`; only the calendar position-of-bar inference is non-verified.
- **Gates**: G1 = Sharpe >= 1.0, G2 = annualised return >= 15%, G3 = max DD > -50%.
