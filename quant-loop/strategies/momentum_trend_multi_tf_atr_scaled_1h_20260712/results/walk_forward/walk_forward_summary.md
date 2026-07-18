# momentum_trend_multi_tf_atr_scaled_1h_20260712 — Walk-forward summary

> Verdict: **[NOT-PROFITABLE]** — all 3 issue-scoped gates fail.
> Iteration: #86 (cycle-46 pivot to trend-following).
> Date: 2026-07-13.

## Issue-scoped evidence gate

| gate                          | threshold | observed  | pass? |
|-------------------------------|-----------|-----------|-------|
| in-sample sharpe              | ≥ 0.5     | 0.260     | ❌    |
| wf_ratio                      | ≥ 0.5     | 0.104     | ❌    |
| min_oos_sharpe                | ≥ 0       | -14.77    | ❌    |

`verdict: [NOT-PROFITABLE]`

## Why this outcome is honest, not a bug

The strategy **does** fire — 829 OOS trades across 4 windows with profit
factor 1.09 and positive (but tiny) annualized return (+1.1%/yr). What
fails is the **risk-adjusted** metrics:

1. **In-sample Sharpe 0.26 vs gate 0.5** — the multi-TF filter is too
   restrictive. The 4h trend gate removes many trend-day signals that
   would have produced positive returns in 1h-only mode. Cycle-46 lesson
   #2 ("trend filters destroy carry") reproduced exactly.

2. **Per-window Sharpe -14.77 in window 2** — the OOS window that
   overlaps the late-2024 ETH drawdown produces a string of losing
   trades that dominate the per-trade Sharpe calc. The cycle-44 lesson
   about per-window Sharpe being sensitive to a few outlier trades is
   at play here.

3. **wf_ratio 0.10** — full-period Sharpe (0.26) does not predict OOS
   Sharpe (0.03). The xs_pairs family suffered the same wf_ratio
   failure (cycle-45 lesson #1); this iteration does NOT support the
   hypothesis "trend strategies will have naturally lower in-sample fit
   but stable OOS performance".

## Hypothesis test results

| hypothesis                                                         | result |
|--------------------------------------------------------------------|--------|
| H1: trend strategies have lower in-sample fit (out-of-market bias) | partially confirmed (Sharpe 0.26 < typical reversion 0.6-1.0) |
| H2: multi-TF confirmation reduces false signals                   | NOT confirmed (wf_ratio still < 0.5; OOS min < 0) |
| H3: ATR-scaled sizing reduces OOS decay                           | NOT confirmed (OOS Sharpe -14.77 min) |

## Schedule & aggregate

- 4 windows, train 8760 (1y) / test 4380 (6m) / step 4380 (6m).
- OOS annualized mean: **+1.11% / yr** (positive but below the 15% bar).
- OOS profit factor mean: **1.09** (marginal).
- OOS max drawdown max: **1.15%** (very low; the strategy is conservative).
- Total OOS trades: 829 (~210 per 6-month window).

## What to do next (per cycle-46 family exhaustion rule)

The `trend_multi_tf_atr_scaled` family has now had **1 iteration** in
cycle-46. The family is **NOT exhausted** yet. Per the family-exhaustion
rule, the next iteration may try:

- Loosen the 4h trend filter (slope threshold = 0 may be too strict —
  the EMA slope has lots of zero-crossings near 0).
- Test single-TF (1h only) to isolate the multi-TF cost.
- Add an asymmetric exit (let winners run 4-6 ATR instead of 2.5).

Hard user gates G1-G7 (Sharpe ≥ 1.0, MDD < 25%, etc.) remain deferred —
this iteration is the methodology test, not a ship candidate.

## Files written

- `results/summary.json` — per-symbol + portfolio full-period metrics.
- `results/equity_<SYM>.csv`, `results/trades_<SYM>.csv` — per-symbol
  equity + closed trades.
- `results/equity_portfolio.csv` — combined portfolio equity.
- `results/walk_forward/windows.json` — the schedule.
- `results/walk_forward/per_window_<NN>_<train|test>/` — per-window
  artifacts.
- `results/walk_forward/walk_forward_summary.json` — gate verdict.