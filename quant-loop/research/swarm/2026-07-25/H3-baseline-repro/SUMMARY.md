# H3-baseline-repro — SUMMARY

Date: 2026-07-25
Direction: H3-baseline-repro
Output directory: `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-baseline-repro`

## What was done

- Re-used the already-run outputs from `repro_h3_baseline.py`:
  - `equity_full_history.csv`
  - `trades_full_history.csv`
  - `walk_forward_windows.csv`
- Computed full-history and walk-forward OOS metrics from those CSVs.
- Compared the local numbers to the PR#6 branch evidence.
- Generated `metrics.json`, `SUMMARY.md`, and the two summary plots.

No production code was modified.

## Key numbers

### Full-history (gross, shared H3 pipeline)

| Metric | Value |
|--------|-------|
| Sharpe (bar-based, annualized) | `1.327` |
| Sharpe (daily-resampled) | `1.468` |
| Annualized return (bar-based) | `27.66%` |
| Annualized return (daily-resampled) | `27.67%` |
| Max drawdown (bar-based) | `-16.26%` |
| Profit factor (bar-based) | `1.010` |
| Trades | `40963` |
| Win rate (per trade) | `27.23%` |
| Trades per day | `24.10` |
| Calmar | `1.701` |
| Sortino | `0.956` |

### Walk-forward OOS (7 windows)

| Metric | Local value | PR#6 reference | Match? |
|--------|-------------|----------------|--------|
| Mean OOS Sharpe (daily-resampled) | `1.875` | `2.773` | `NO` |
| Mean OOS ann. return | `31.79%` | `59.75%` | `NO` |
| Worst OOS maxDD | `-13.30%` | `-12.62%` | — |
| Mean OOS PF | `1.012` | — | — |
| Bootstrap CI lower | `0.888` | `1.914` | `NO` |

### Fee sensitivity (fee-shock replay on gross equity)

| Model | Pair RT bps | Sharpe | Ann return | Max DD |
|-------|-------------|--------|------------|--------|
| in-house | 4 | `1.368` | `25.46%` | `-14.86%` |
| freqtrade | 24 | `0.870` | `14.89%` | `-17.26%` |
| backtrader | 60 | `-0.021` | `-1.96%` | `-27.16%` |

## G1-G7 certification (approximate)

- Certification result: `FAIL`
- Failed gates: `['G4']`
- Reasons:
  - G4 profit_factor > 1.5: got '?', expected Profit factor > 1.5

## Is the baseline reproducible?

**No — the local rerun does not reproduce the PR#6 headline numbers.**

- Local mean OOS Sharpe is `1.875` vs PR#6 `2.773`.
- Local mean OOS annualized return is `31.79%` vs PR#6 `59.75%`.
- Local bootstrap CI lower is `0.888` vs PR#6 `1.914`.

### Likely reasons for the gap

1. **Data span / alignment**: The local rerun uses the global `perp_1m` snapshot
   (BTC 2019-09 → 2026-07, SOL 2020-09 → 2026-07) clipped to the funding period
   (2021-11-20 → 2026-07-17). PR#6 was reportedly run on a different snapshot /
   alignment, and the 7-window OOS mean is sensitive to the exact start/end bars.
2. **Cost model mismatch**: The shared H3 backtest records per-trade cost in the
   trade log but does **not** debit it from the equity curve. PR#6 numbers are
   cost-adjusted (in-house 4 bps RT, etc.). The fee-shock replay here is an
   approximation and confirms the strategy is highly cost-sensitive.
3. **Profit factor**: Local PF is ~1.01, far below the G4 gate of 1.5, consistent
   with the known H3 "PF fail" noted in `config_btcsol.json`. PR#6 likely used a
   different sizing/cost convention or a shorter evaluation window that flattered PF.

## Continue or KILL?

**Do not KILL yet.** The engine and signal logic are stable, but the published
OOS Sharpe is **not locally verified**. H3 should remain a HOLD until the data /
windowing discrepancy is resolved.

## Next 1-2 concrete actions

1. **Audit the PR#6 snapshot**: obtain the exact parquet files, date range, and
   train/test boundaries used in PR#6, rerun this pipeline on that snapshot, and
   reconcile the OOS Sharpe.
2. **Fix the H3 cost model**: patch `_backtest_pair` so that fees/slippage are
   debited from the bar-return equity curve (not just the trade log), then
   re-evaluate G1-G7 and fee sensitivity before any live candidacy decision.
