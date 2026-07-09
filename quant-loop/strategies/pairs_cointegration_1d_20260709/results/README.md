# pairs_cointegration_1d_20260709 — backtest results

- **Universe**: BTCUSDT, ETHUSDT, SOLUSDT
- **Active pairs (EG p<0.05)**: 1
- **Total trades**: 21
- **Portfolio pnl**: +2218.39 USD (+2.218%)
- **Fees/slippage (per side)**: 2.0 / 2.0 bps
- **Historical cointegration breaks detected**: 16
- **State machine**: pair_pauses=1  portfolio_pauses=1  blocked_entries=0

## Files

- `pairs_cointegration_1d_20260709/results/pair_selection.csv` — pair_selection
- `pairs_cointegration_1d_20260709/results/per_pair_pnl.csv` — per_pair_pnl
- `pairs_cointegration_1d_20260709/results/hedge_ratio_stability.csv` — hedge_ratio_stability
- `pairs_cointegration_1d_20260709/results/eg_pvalue_timeseries.csv` — eg_pvalue_timeseries
- `pairs_cointegration_1d_20260709/results/portfolio_equity.csv` — portfolio_equity
- `pairs_cointegration_1d_20260709/results/trades_BTCUSDT-SOLUSDT.csv` — trades_BTCUSDT-SOLUSDT
- `pairs_cointegration_1d_20260709/results/run_summary.json` — run_summary
- `pairs_cointegration_1d_20260709/results/metrics.json` — metrics

## Per-pair summary

| pair | trades | win_rate | pnl_usd | pnl_pct |
|------|-------:|---------:|--------:|--------:|
| BTCUSDT-SOLUSDT | 21 | 81.0% | +2218.39 | +2.218%

## Pair selection (full table)

| pair | p_value | alpha | beta | r_squared | selected |
|------|--------:|------:|-----:|----------:|:--------:|
| BTCUSDT-SOLUSDT | 0.048 | +7.730 | 0.786 | 0.814 | yes |
| ETHUSDT-SOLUSDT | 0.2178 | +2.693 | 1.125 | 0.863 | no |
| BTCUSDT-ETHUSDT | 0.4082 | +6.260 | 0.645 | 0.803 | no |

## First 10 historical cointegration breaks

| date | pair | p_before | p_after |
|------|------|---------:|--------:|
| 2024-12-01 | BTCUSDT-ETHUSDT | 0.039 | 0.101 |
| 2025-02-23 | BTCUSDT-ETHUSDT | 0.040 | 0.064 |
| 2025-04-20 | BTCUSDT-ETHUSDT | 0.025 | 0.243 |
| 2025-08-03 | BTCUSDT-ETHUSDT | 0.030 | 0.090 |
| 2026-01-11 | BTCUSDT-ETHUSDT | 0.038 | 0.138 |
| 2026-04-05 | BTCUSDT-ETHUSDT | 0.014 | 0.053 |
| 2024-09-29 | BTCUSDT-SOLUSDT | 0.048 | 0.111 |
| 2024-12-08 | BTCUSDT-SOLUSDT | 0.002 | 0.052 |
| 2025-02-09 | BTCUSDT-SOLUSDT | 0.033 | 0.086 |
| 2025-04-27 | BTCUSDT-SOLUSDT | 0.005 | 0.100 |
