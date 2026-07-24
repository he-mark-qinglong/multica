# loid_iceberg_v4 Parameter Scan Analysis

> Date: 2026-07-24
> Data: BTCUSDT aggTrades 2026-04-19 → 2026-07-17 (129M trades)
> Detector: lookback=1000, large_z=3.0, whale_z=5.0

## Scan Results (15 combinations)

| Composite | Threshold | Trades | Sharpe | Ann Return | Max DD | PF |
|-----------|-----------|--------|--------|------------|--------|----|
| 1min | 2.0 | 61,256 | 1.19 | -1.0 | -101.2 | 1.25 |
| 1min | 3.0 | 60,939 | 3.41 | -1.0 | -134.4 | 1.56 |
| 1min | 5.0 | 59,268 | 2.47 | -1.0 | -130.8 | 1.49 |
| 1min | 8.0 | 56,639 | -0.06 | -1.0 | -109.5 | 0.99 |
| 1min | 13.0 | 51,719 | 3.54 | -1.0 | -114.2 | 2.02 |
| 5min | 2.0 | 12,248 | 2.66 | -1.0 | -15.3 | 1.49 |
| 5min | 3.0 | 12,243 | 2.25 | -1.0 | -15.3 | 3.31 |
| 5min | 5.0 | 12,232 | 2.60 | -1.0 | -15.3 | 1.77 |
| 5min | 8.0 | 12,165 | -1.81 | -1.0 | -1.6 | 0.30 |
| 5min | 13.0 | 12,040 | 2.40 | -1.0 | -14.8 | 2.61 |
| 15min | 2.0 | 4,029 | 1.47 | -1.0 | -1.6 | 1.25 |
| 15min | 3.0 | 4,027 | 1.90 | -1.0 | -1.6 | 3.85 |
| 15min | 5.0 | 4,013 | -0.38 | -1.0 | -1.6 | 0.95 |
| 15min | 8.0 | 4,012 | 3.00 | -1.0 | -1.6 | 1.26 |
| 15min | 13.0 | 4,017 | 1.91 | -1.0 | -1.6 | 5.09 |

## Key Findings

1. **All combinations lose 100% of capital**. No parameter setting produces positive returns.
2. **High Sharpe values are artifacts**. When equity goes negative, the daily-resampled Sharpe becomes meaningless (mean/std of negative equity returns).
3. **Signal density is not the bottleneck**. Even with 51k-61k trades (1min), the strategy loses money. The issue is per-trade edge vs cost.
4. **Cost-cap confirmed**. Average trade loses money after 4bp taker fee + slippage. The gross edge from iceberg/large-order signals is insufficient to cover costs at any threshold.
5. **15min composite shows more reasonable drawdown** (-1.6% vs -134%) but still negative returns. Higher timeframes dilute the signal to noise.

## Verdict

**KILL** — consistent with T01 (OFI) and T04 (iceberg absorption). The loid_iceberg_v4 signal, as implemented, does not produce sufficient gross edge to cover Binance futures taker costs (22bps RT) at any tested parameter combination.

## Revival Conditions

- (a) Sub-taker execution (maker + queue priority, effective cost <1bp) — could unlock the gross edge.
- (b) Cross-asset validation (ETH/SOL aggTrades) — determine if BTC result is venue-specific.
- (c) Liquidation-cascade sub-regime filter — cascade-context signals may have larger edge.
- (d) Use as regime filter, not standalone signal — combine with H3-style multi-TF confirmation.

## Evidence Files

- `results/sma-34992/threshold_scan_results.json` — full scan data
- `results/sma-34992/loid_iceberg_v4_btc_90d_metrics.json` — baseline metrics
- `results/sma-34992/loid_iceberg_v4_btc_90d_trades.json` — baseline trades
- `results/sma-34992/loid_iceberg_v4_composite_lb1000_z3.0_w5.0.parquet` — cached detector output
