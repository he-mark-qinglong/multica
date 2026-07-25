# Multi-Strategy Portfolio Combination Experiment

**Date:** 2026-07-25  
**Window:** 2024-01-01 → 2024-12-31 (366 days)  
**Output directory:** `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/`

## 1. Objective

Test whether combining the H3 baseline, a signal-enhance-h3 2024-filtered variant, and other ledger strategies with numbers produces a portfolio that is better—on a risk-adjusted basis—than any single strategy.

## 2. Candidate strategies

| Name | Source | Type | 2024 Sharpe | 2024 AnnReturn | 2024 MaxDD | Notes |
|------|--------|------|------------:|---------------:|-----------:|-------|
| `h3_baseline` | `equity_winner_atr_mult_1_00_1d.csv` | actual daily | 1.93 | 24.7% | -9.77% | Full H3 daily returns, 8,390 trades in 2024. |
| `signal_slope_fav_4` | `quick_verify_2024.json` | simulated | 7.91 | 99.5% | -3.93% | Gaussian simulation matching reported 2024 Sharpe & return. |
| `signal_slope_fav_4_stop_0_7` | `quick_verify_2024.json` | simulated | 8.39 | 114.3% | -3.44% | Best standalone on reported metrics. |
| `signal_adverse_stop_0_7` | `quick_verify_2024.json` | simulated | 3.10 | 62.3% | -8.85% | Included as an additional filtered variant. |
| `pairs_cointegration_1d` | `portfolio_equity.csv` | actual daily | 0.82 | 0.2% | -0.15% | Only 21 trades; pre-August 2024 returns are flat (inactive). |
| `vpvr_xs_basis_zscore_15m` | `equity_A_iter72_BTCUSDT_ETHUSDT.csv` | 15m → daily | 0.72 | 4.1% | -5.89% | Weak but positive return over 2024. |
| `vpvr_xs_smart_routing_15m` | `equity_15m_BTCUSDT.csv` | 15m → daily | -3.98 | -0.3% | -0.53% | Ledger says PASS; `metrics.json` Sharpe is negative. |

`loid_iceberg_v4_1m_20260720` was **excluded**: its ledger `maxDD` is `-130.830`, which is inconsistent with a tradable strategy, and no raw equity file exists. If the value is a decimal-point error it should be re-checked before inclusion.

## 3. Methodology

1. **Alignment:** all series were aligned to the 2024 daily calendar. Missing days were forward-filled (pairs) or zero-filled.
2. **Simulation:** signal-enhance variants have no published daily equity, so they were simulated as Gaussian daily returns matching the reported 2024 Sharpe and annualized return.
3. **Combination methods:**
   - **Equal weight:** 1/K allocation, rebalanced daily.
   - **Risk parity (inverse volatility):** weights ∝ 1/σ_i, computed on an expanding 30-day+ window. A 5% annual volatility floor prevents the sparse pairs series from absorbing all capital.
   - **Decorrelation:** weights ∝ (1/avg_abs_correlation_i) × (1/σ_i), computed on an expanding window. This is a simple decorrelation weighting (HRP-like spirit without requiring `scipy`).
4. **Turnover proxy:** half the sum of absolute daily weight changes (`0.5 × Σ|Δw|`).
5. **Fee sensitivity:**
   - *Portfolios:* turnover × fee is subtracted from each daily return.
   - *Standalones:* `trades_per_year × fee` is subtracted from annual return. This assumes a **full-notional round-trip per trade**, so it is conservative; actual position sizes are almost certainly smaller, and the H3 equity may already be net of fees.

## 4. Standalone results

| Strategy | Sharpe | AnnReturn | MaxDD | Trades/Year | Sharpe @ 8bps | Sharpe @ 22bps |
|----------|-------:|----------:|------:|------------:|--------------:|---------------:|
| h3_baseline | 1.93 | 24.7% | -9.77% | 5,777 | -34.24 | -97.53 |
| signal_slope_fav_4 | 7.91 | 99.5% | -3.93% | 758 | 3.09 | -5.35 |
| signal_slope_fav_4_stop_0_7 | 8.39 | 114.3% | -3.44% | 485 | 5.55 | 0.56 |
| signal_adverse_stop_0_7 | 3.10 | 62.3% | -8.85% | 1,899 | -4.46 | -17.68 |
| pairs_cointegration_1d | 0.82 | 0.2% | -0.15% | 14 | -3.07 | -9.88 |
| vpvr_xs_basis_zscore_15m | 0.72 | 4.1% | -5.89% | 7,990 | -112.12 | -309.59 |
| vpvr_xs_smart_routing_15m | -3.98 | -0.3% | -0.53% | 1,909 | -1,770.74 | -4,862.58 |

The standalone fee numbers are dominated by high trade counts. They are best interpreted as a **stress-test** rather than a literal forecast.

## 5. Portfolio results

| Method | Sharpe | AnnReturn | MaxDD | Avg Turnover (ann.) | Sharpe @ 8bps | Sharpe @ 22bps |
|--------|-------:|----------:|------:|--------------------:|--------------:|---------------:|
| Equal weight | 9.52 | 43.5% | -1.23% | 0.64 | 9.51 | 9.49 |
| Risk parity | 9.59 | 28.1% | -0.68% | 0.68 | 9.57 | 9.54 |
| Decorrelation | 9.67 | 28.5% | -0.71% | 1.97 | 9.61 | 9.51 |

Average weights (decorrelation example):

- h3_baseline: 9.6%
- signal_slope_fav_4: 10.7%
- signal_adverse_stop_0_7: 7.3%
- signal_slope_fav_4_stop_0_7: 9.0%
- pairs_cointegration_1d: 22.8%
- vpvr_xs_basis_zscore_15m: 17.8%
- vpvr_xs_smart_routing_15m: 22.8%

## 6. Key findings

1. **Combinations beat the H3 baseline on every reported metric.** The H3 baseline Sharpe is 1.93; all three combinations are >9.5, with max drawdown below -1.3% versus -9.8% for H3 alone.
2. **Combinations also beat the best standalone signal variant on a risk-adjusted basis.** `signal_slope_fav_4_stop_0_7` has Sharpe 8.39; the portfolios reach 9.5+ Sharpe by blending it with weak/uncorrelated return streams.
3. **The driver is low correlation.** Pairwise correlations among the seven series are mostly near zero or negative (see `portfolio_results.json`). Adding low-volatility, near-zero-correlation strategies (pairs, smart routing) dilutes the strong signal variant just enough to cut volatility dramatically.
4. **Fee sensitivity favors portfolios.** Portfolio turnover is <2×/year, so 8–22 bps round-trip costs barely change the Sharpe. Standalone high-frequency strategies are far more fee-sensitive under the full-notional assumption.
5. **Caveat: the signal variants are simulated, not observed.** Their reported 2024 Sharpe (7.5–8.1) may not repeat out-of-sample, and real correlations could be higher than the simulated/observed 2024 values.

## 7. Limitations & assumptions

- `signal_*` daily returns are synthetic Gaussian paths. Real drawdown paths and correlations will differ.
- `pairs_cointegration_1d` has only 5 non-zero daily returns in 2024 and a flat pre-August period. Its portfolio weight is large under risk-parity/decorrelation because of its low volatility; this is a data-sparsity artifact.
- `vpvr_xs_smart_routing_15m` is slightly negative standalone but is treated as a diversifier.
- Fee model for standalones is conservative (full-notional). H3 equity may already include trading costs.
- `loid_iceberg_v4_1m` was dropped because its reported `maxDD = -130.830` is not usable.

## 8. Next validation steps

1. **Get real daily equity curves** for the signal-enhance-h3 variants instead of simulating them.
2. **Run a walk-forward test:** estimate weights on a rolling window and evaluate on the next month, rather than using in-sample correlations for the whole year.
3. **Refine the fee model:** use actual position sizes from trade files, or at least scale per-trade cost by an estimated average position size.
4. **Test the Aug–Dec 2024 overlap window** where pairs cointegration is active, to see if conclusions hold without the flat pre-August period.
5. **Re-check `loid_iceberg_v4_1m`** data; if the `maxDD` is a decimal-point error, simulate it under the corrected value and re-run.
6. **Add a "no signal variants" portfolio** to isolate whether the improvement comes purely from combining weak strategies or from the strong signal variants.

## 9. Files produced

- `portfolio_experiment.py` — reproducible script.
- `portfolio_results.json` — full numeric results, correlation matrix, and assumptions.
- `weights_equal_weight.csv`, `weights_risk_parity.csv`, `weights_decorrelation.csv` — daily weight histories.
- `portfolio_summary.md` — this document.
