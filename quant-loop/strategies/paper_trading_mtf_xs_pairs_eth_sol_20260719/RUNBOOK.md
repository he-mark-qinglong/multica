# Paper-Trading Runbook — mtf_xs_pairs ETH/SOL leg (SMA-35012)

Phase 1: **shadow execution on live Binance USD-M data; no real capital**.
Issue: SMA-35012. Sign-off: SMA-34986. Strategy target:
`vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` (iter#85).

## Files

```
strategies/paper_trading_mtf_xs_pairs_eth_sol_20260719/
├── config.json                     # source of truth for pair/tf/kill thresholds
├── paper_runner.py                 # subcommands: init / scaffold / kill-check
├── RUNBOOK.md                      # this file
└── results-ledger/
    ├── daily_metrics.csv           # daily ledger (PF/Sharpe/DD/trade-count)
    ├── equity_curve.csv            # 30m-bar equity tick log
    └── trades.jsonl                # one trade per line, paper fills
```

## Backtest anchor (G5 CV passed, per SMA-34986)

| metric          | backtest OOS |
|-----------------|--------------|
| Sharpe          | 2.43 |
| ann return %    | 46.27 |
| profit factor   | 1.016        |
| bootstrap CI lo | 1.68         |
| trades          | 39,211       |

Known weak point: PF 1.016 is thin — cost drag is the first thing to watch.

## Kill criteria (auto-halt on ANY of)

1. live PF < 1.0 after ≥ 100 trades
2. maxDD > 1.5× backtest maxDD
3. rolling 20d Sharpe < 0

Implementation: `paper_runner.py kill-check` reads `daily_metrics.csv` and
emits exit code 2 when a kill condition is met, so the cron can halt cleanly.

## Cost model

Single source of truth: `_shared/execution/cost_model.py`. The runner
imports `apply_cost` / `BINANCE_FUTURES` — **no per-strategy hardcoded
fees**. Default impact factor 0.05 (large-cap futures).

## Daily operator loop

```bash
# 1. Initialise / inspect scaffold (one-shot at deploy)
python3 /home/smark/multica/quant-loop/strategies/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py init

# 2. Cron / loop tick — fetch latest 30m bars, run strategy, append row to daily_metrics.csv
#    (left as a thin shim — the actual signal generator lives in the strategy dir)

# 3. Pre-trade kill-criteria check
python3 /home/smark/multica/quant-loop/strategies/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py kill-check
# exit 0 = green; exit 2 = KILL; non-zero !=2 = broken
```

## Weekly review cadence

Every 7d, post a comment on SMA-35012 with:
- rolling Sharpe (last 7d)
- profit factor (cumulative)
- max drawdown (cumulative)
- trade count

If kill criteria fire, post a comment on SMA-35012 with `state=blocked`
and the kill reason; flip issue status to `blocked` per AGENTS.md.

## Phase transitions

Phase 2 (minimal real capital) is **only** entered after:
1. ≥ 4 weeks of paper trading, AND
2. explicit smark approval recorded in a comment on SMA-35012.

Until both, `real_capital=false` in `config.json` and the runner stays
in shadow mode.

## Caveats pinned at deploy

- The target strategy (`v5_loose_20260717`) is `tag=NOT-PROFITABLE` in
  its in-house metrics.json (PF 0.74, sharpe 0.49). The G5 CV pass
  numbers in this runbook come from the **sign-off context** in
  SMA-34986 and are what we anchor against. Any divergence between the
  paper-trading ledger and these anchors must be surfaced in the
  weekly review.
- The framework_cv_freqtrade pass on the BTC/SOL and BTC/BNB
  counterparts surfaced structural cost fragility. Cost drag is the
  first failure mode to watch on the live leg — exactly as flagged in
  the issue body.