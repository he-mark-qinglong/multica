# Paper-Trading Runbook — mtf_xs_pairs ETH/SOL leg (SMA-35012)

Phase 1: **shadow execution on live Binance USD-M data; no real capital**.
Issue: SMA-35012. Sign-off: SMA-34986. Strategy target:
`vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` (iter#85).

## Files

```
strategies/paper_trading_mtf_xs_pairs_eth_sol_20260719/
├── config.json                     # source of truth for pair/tf/kill thresholds
├── paper_runner.py                 # subcommands: init / scaffold / kill-check / live
├── fill_engine.py                  # WS subscriber + signal runner + fill simulator
├── kill_criteria.py                # smark absolute + issue-body kill evaluator
├── RUNBOOK.md                      # this file
└── results-ledger/
    ├── daily_metrics.csv           # daily ledger (PF/Sharpe/DD/trade-count)
    ├── equity_curve.csv            # 30m-bar equity tick log
    ├── trades.jsonl                # one paper fill per line, full provenance
    └── system.jsonl                # engine events (warmup, ws, fills, kill)
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

1. **SMARK absolute**: maxDD > 5.0% (added per SMA-35012 DECISION 2026-07-20T21:41+08)
2. **SMARK absolute**: daily_loss > 2.0% (added per SMA-35012 DECISION 2026-07-20T21:41+08)
3. live PF < 1.0 after ≥ 100 trades
4. maxDD > 1.5× backtest maxDD
5. rolling 20d Sharpe < 0

Implementation: `kill_criteria.evaluate()` runs after every fill and after
every WS bar close. `paper_runner.py kill-check` reads `daily_metrics.csv`
and emits exit code 2 when a kill condition is met, so the cron can halt
cleanly. The `live` subcommand also halts in-process (closes WS, appends
final daily row, exits 2) when a trigger fires.

## Cost model

Single source of truth: `_shared/execution/cost_model.py`. The fill-engine
imports `apply_cost` / `BINANCE_FUTURES` — **no per-strategy hardcoded
fees**. Default impact factor 0.05 (large-cap futures).

## Operator loop

```bash
# 1. Initialise / inspect scaffold (one-shot at deploy)
python3 paper_runner.py init

# 2. Live fill-engine: WS subscribe + REST seed + signal + fill + ledger
#    Bounded runtime (default 30 min) so the cron can re-launch periodically.
#    Appends to trades.jsonl, equity_curve.csv, daily_metrics.csv.
#    Halt with Ctrl-C, or wait for window to expire.
python3 paper_runner.py live --window-min 30
#    exit 0 = clean exit, fills processed
#    exit 2 = KILL trigger fired
#    exit 3 = data feed blocked (ESCALATE)

# 3. Pre-trade kill-criteria check (re-runs evaluator on current ledger)
python3 paper_runner.py kill-check
#    exit 0 = green; exit 2 = KILL; non-zero !=2 = broken
```

## Live fill-engine architecture (added 2026-07-20)

The `live` subcommand runs three phases in order:

1. **Warmup** — loads historical 30m parquet for ETHUSDT + SOLUSDT from
   `quant-loop/strategies/.../data/`, runs the strategy's
   `run_backtest` once to establish a baseline trade list and
   `last_entry_ts` (the most recent warmup trade entry).
2. **REST seed** — fetches the latest 200 30m bars per symbol from
   `https://fapi.binance.com/fapi/v1/klines` (unauthenticated public
   market data). Appends to history, re-runs strategy, diffs new trades
   against `last_entry_ts`, processes each new trade through
   `_shared.execution.cost_model.apply_cost`. This phase exists so the
   engine produces paper fills without waiting 30 min for the next bar
   close.
3. **WS subscribe** — opens `wss://fstream.binance.com/stream?streams=
   ethusdt@kline_30m/solusdt@kline_30m` (public, no auth). On each bar
   close: append → re-run strategy → diff → apply cost → write ledger
   → evaluate kill criteria. Bounded by `--window-min`.

Cost-model sizing: `notional = equity * per_pair_notional_pct` (1% of
$100k = $1k per trade). Round-trip cost is `apply_cost(notional, adv_usd,
BINANCE_FUTURES, "taker", 0.05)`. The 60/40 split between
`fees_usd` / `slippage_usd` in the trade log row is the model default
for Binance futures (4 bps taker + 2 bps slip).

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
- The `live` subcommand subscribes only to public market-data WS
  (`fstream.binance.com`) — no testnet account, no API keys, no order
  endpoints. Order placement (the SMA-34937 connector family) remains
  upstream-blocked for any future phase 2 real-money wiring, but
  phase 1 paper does not need it.