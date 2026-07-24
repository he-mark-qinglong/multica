# ARCHIVE NOTE — paper_trading_mtf_xs_pairs_eth_sol_20260719

Archived: 2026-07-24 (PLAN_20260724_hf_strategy_optimization, Phase C).
Moved from `strategies/paper_trading_mtf_xs_pairs_eth_sol_20260719/` to
`strategies/_graveyard/paper_trading/`. All code and `results-ledger/`
evidence preserved untouched.

## Why this is archived and no longer running

1. **Target strategy is falsified (NOT-PROFITABLE).**
   Paper target: `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717`
   (iter#85). Its in-house `results/metrics.json` carries `tag=NOT-PROFITABLE`:
   PF 0.7412, Sharpe -6.40, total return -0.99%, win rate 44.8% over 5,642
   trades (2022-01-01 → 2026-07-10). Shadow-executing a falsified signal
   produces no decision value; the G5 CV anchor numbers in the RUNBOOK
   (Sharpe 2.43) came from sign-off context in SMA-34986 and diverged from
   the in-house verdict — exactly the failure mode Phase C separates.

2. **Paper ledger itself was bleeding.**
   Last ledger state (13 trades, 2026-07-20): lifetime PF 0.5187,
   win rate 30.8%, gross PnL -$882.23, equity $99,108 of $100k. The cost
   drag flagged as the first failure mode in the RUNBOOK was confirmed
   live.

3. **Double-accounting bug in the ledger writer.**
   `results-ledger/daily_metrics.csv` contains **two rows for 2026-07-20**
   with diverging values (net_pnl -892.94 vs -891.76), and the header line
   is concatenated directly with the first data row (missing newline:
   `...kill_reason,notes2026-07-20,...`). The daily-metrics append path in
   `paper_runner.py` can write duplicate/corrupted rows, so the ledger
   cannot be trusted as a clean record without manual repair. Do not
   revive this runner without fixing the append logic and re-validating
   against `trades.jsonl`.

4. **Stopped 2026-07-20.**
   Last `system.jsonl` event: `session_end` at 2026-07-20T14:11:48 UTC
   (window expired, kill_triggered=false). No live sessions since; the
   cron that relaunched it has been retired.

## Evidence chain (preserved)

- `RUNBOOK.md` — kill criteria, cost model, operator loop, deploy caveats
- `config.json` — pair/tf/kill thresholds (source of truth at runtime)
- `results-ledger/daily_metrics.csv` — daily ledger (contains the
  duplicate-row bug documented above)
- `results-ledger/equity_curve.csv` — 30m-bar equity ticks
- `results-ledger/trades.jsonl` — full paper-fill provenance
- `results-ledger/system.jsonl` — engine events
- Target strategy verdict: `strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/results/metrics.json`

## Revival conditions

None planned. If ETH/SOL xs-pairs is ever re-proposed, it must go through
the new HF pipeline (pre-registration + CPCV + dual-framework CV + G1-G7
gates) as a fresh strategy, with a rewritten ledger writer — not by
resuming this directory.
