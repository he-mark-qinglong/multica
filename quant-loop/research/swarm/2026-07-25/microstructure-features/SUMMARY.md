# microstructure-features feasibility study

**Period:** 2026-04-01 00:00:00 -> 2026-07-17 23:59:00
**Symbols:** BTCUSDT, SOLUSDT
**Cost:** 4.0 bps round-trip

## What was done

1. Built per-1m trade-flow microstructure features from Binance aggTrades partitions,
   loading one month at a time to stay within memory limits.
2. Features include volume/order-flow imbalance, aggressive buy/sell ratios,
   whale-trade notional share ($100k threshold), trade intensity, short-term
   cumulative flow, and rolling z-scored flow pressure.
3. Ran H3 (BTC/SOL 1m pair z-score + funding regime) baseline on the same window.
4. Ran microstructure-filtered H3 variants: require confirming per-leg flow
   pressure (BTC - SOL) before entering a mean-reversion signal.
5. Evaluated standalone feature predictive power via Spearman correlation to
   forward returns and a simple logistic-regression sign classifier.

## Key numbers

| Variant | Trades | Sharpe | Ann % | MaxDD % | PF | WinRate % | Avg trade (bps) |
|---------|--------|--------|-------|---------|----|-----------|-----------------|
| H3_baseline | 1943 | 4.106 | 40.26 | -2.62 | 1.723 | 25.4 | -7.14 |
| flow_pressure_z_thr0.0 | 1943 | 4.106 | 40.26 | -2.62 | 1.723 | 25.4 | -7.14 |

### Standalone feature predictive power

- **BTCUSDT**: strongest Spearman rho = -0.0260 (buy_count_ratio vs 5m forward return, n=155255)
  - 1m sign-prediction test accuracy = 0.510 (naive baseline = 0.506)
  - 5m sign-prediction test accuracy = 0.511 (naive baseline = 0.506)
  - 15m sign-prediction test accuracy = 0.505 (naive baseline = 0.508)
- **SOLUSDT**: strongest Spearman rho = -0.0460 (close_loc vs 1m forward return, n=155519)
  - 1m sign-prediction test accuracy = 0.519 (naive baseline = 0.545)
  - 5m sign-prediction test accuracy = 0.509 (naive baseline = 0.522)
  - 15m sign-prediction test accuracy = 0.507 (naive baseline = 0.519)

## Gate check (G1-G7 / T1)

- **Baseline failed gates:** G6, G7
- **Best variant failed gates:** G6, G7
- G5 (framework CV) and G6/G7 (bootstrap/DSR) were not run in this quick-feasibility pass.
  G1-G4/T1 are evaluated on the full in-sample window using daily-resampled metrics.

## Verdict: continue or KILL?

**HOLD / close-to-KILL for this feature set.** No microstructure filter beat the baseline; standalone predictive power is near zero, suggesting these features do not add material edge to the H3 template on this window.

## Next 1-2 concrete actions

1. Run the same H3+microstructure pipeline over the full available history    (not only 2026-04..07 where aggTrades exist) to confirm whether the 2026 window is    representative or a lucky segment.
2. If the improvement persists, expand the feature set to order-book derived signals    (book imbalance, queue position, bid-ask bounce) and run a proper walk-forward    threshold optimization with G1-G7 certification; otherwise kill this branch.
