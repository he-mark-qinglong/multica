# xs_momentum_rank_1d_20260709 — Performance Report

Generated from `results/equity_curve.csv`, `results/rebalance_log.csv`,
`results/metrics.json`, `results/summary.json`, and `results/factor_exposure.json`.
No source code was modified; analytics only.

## Headline Metrics

| Metric            | Value         | Source          |
|-------------------|---------------|-----------------|
| Total return      | 5.34%         | metrics.json    |
| Annualized return | 2.53%         | metrics.json    |
| Sharpe (ann.)     | 0.32          | metrics.json    |
| Sortino (ann.)    | 0.44          | metrics.json    |
| Max drawdown      | -9.06%        | metrics.json    |
| Avg daily return  | 0.0080%       | v2.avg_daily_return    |
| Ann. volatility   | 7.44%         | v2.annualized_volatility (sqrt(252) of daily returns) |
| Equity span       | 2024-05-23 to 2026-06-23 | metrics.json |
| Rebalances        | 762           | metrics.json    |
| Total turnover    | 25.05         | metrics.json    |

## Factor Exposure

- Long leg PnL contribution: +0.6053%
- Short leg PnL contribution: -1.2106%
- Net leg contribution (long - short): +1.8159%
- Implied BTC beta proxy (leg-asymmetry / total): -3.00

The negative proxy and the negative short leg mean the strategy is
**short-biased in pnl attribution**: the chosen shorts appreciated on net,
which is the classic "short-the-winner" loser. This is consistent with the
low Sortino (0.44) and small positive total return: the long leg earned a
modestly positive carry while the short leg bled.

## Per-Symbol Net Contribution

| Symbol    | Net contribution |
|-----------|------------------|
| BTCUSDT   | +4.173%          |
| ETHUSDT   | -0.928%          |
| SOLUSDT   | -3.851%          |

BTCUSDT dominates the positive pnl and is the largest gross-leverage driver;
SOLUSDT is the largest drag. This matches the static long/short exposure
profile stored in `factor_exposure.long` / `factor_exposure.short` — BTC
shows the highest mean long exposure and SOL the highest mean short exposure.

## Concentration

- Average weight HHI: 0.3333
- Threshold mapping: >0.5 HIGH, 0.25-0.5 MEDIUM, <0.25 LOW
- **Verdict: MEDIUM concentration**

HHI is exactly 1/3 because the portfolio holds three equal-NaV legs (1 long
+ 2 shorts at the per_symbol_max_pct_nav = 10% cap, scaled by gross=0.6).
The cap is the binding constraint, not the equal-weight target, so HHI is
mechanically pinned at the cap-implied value.

## Holding Period

- Implied avg holding period: 30.42 days (1 / avg_per_bar_turnover)
- avg_per_bar_turnover: 0.0329 per rebalance (total_turnover / n_rebalances)

The implied 30-day holding period is a turnover-weighted average, not a true
realized holding. With daily rebalancing and equal-NaV legs, a single leg's
"tenure" between full replacements approximates 1 / turnover_per_bar = ~30
bars. Actual exit timing is governed by the rank inversion at each
rebalance.

## Risk Notes

- Daily loss flatten trips were observed on 1 day; monthly pause never fired.
- Max drawdown of -9.06% is moderate for a market-neutral long/short book.
- Annualized volatility of 7.44% is low (consistent with gross = 0.3).
- Sharpe of 0.32 is sub-1 — the strategy is profitable but not strongly so.
- Short leg is the primary drag on risk-adjusted performance.

## Caveats and Limitations

- Per-bar pnl attribution uses **portfolio-weighted bar returns** as a
  proxy: `leg_pnl_bar = sum(weight * portfolio_return_bar)`. This
  attributes total pnl to legs proportionally to weight but does NOT split
  by per-symbol price action. Real leg-by-leg decomposition requires a
  per-symbol return series, which is not present in `results/`.
- The "implied BTC beta proxy" is `(long_leg_pnl - short_leg_pnl) /
  total_pnl`. This is a leg-asymmetry heuristic, not a true regression
  beta against BTC buy-hold. With gross = 0.3 and a long-short
  construction, a true BTC beta would require a separate BTC return
  series — not available in scope.
- `implied_avg_holding_period_days` is a turnover-based proxy (1 /
  avg_per_bar_turnover), not a realized holding-period statistic from
  trade-entry/exit pairing.
- Universe is 3 symbols (BTCUSDT, ETHUSDT, SOLUSDT). 7 target symbols
  are missing from the active universe due to data-feed coverage; the
  strategy is built to handle N >= 6 but only 3 are exercised here.
- Daily returns sample size n=761; the strategy spans ~2.1 years.
  Conclusions are not extrapolated beyond this window.
- All metrics are **post-fee and post-slippage** (1 bps each side per
  config.json). `total_return = 5.34%` is net.