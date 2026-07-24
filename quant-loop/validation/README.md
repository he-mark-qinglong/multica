# OOS Validation Harness (G1–G7)

Automated out-of-sample validation for quant-loop strategy variants. On a new
variant commit, the harness runs the variant's native engine **and** replays
its trade decisions in independent frameworks (backtrader, freqtrade;
optionally vectorbt) across 3 contiguous OOS windows, then emits a pass/fail
verdict against the G1–G7 hard gates. A failing verdict blocks the merge.

## Usage

```bash
cd quant-loop
python3 -m validation.oos_harness --variant <variant_name>      # 3 windows, all frameworks
python3 -m validation.oos_harness --variant <variant_name> --frameworks native,backtrader
```

Exit codes: `0` = PASS, `1` = gate FAIL (merge blocked), `2` = harness error.

Output: `<variant>/results/validation/verdict.json` + `verdict.md`, plus the
same summary on stdout for CI logs.

## Commit trigger

`ci/validate_changed_variants.sh` maps `git diff BASE...HEAD` onto variant
directories and runs the harness for each changed variant; any gate failure
exits 1 and blocks the merge. Wire it into the merge pipeline (pre-merge
check / self-hosted runner with data + freqtrade/backtrader installed).

## Gates (strategy-layer rules, 2026-07-11)

| gate | threshold | evaluated on |
|------|-----------|--------------|
| G1 | mean Sharpe >= 1.0 | native engine, full span, mean across symbols |
| G2 | min(annualized_full, mean_OOS_annualized) >= 15% | native, full + OOS windows |
| G3 | profit_factor > 1.5 | native, full span |
| G4 | max_drawdown < 25% | native, full span, worst symbol |
| G5 | OOS walk-forward Sharpe >= 1.0 in **both** backtrader and freqtrade | framework replays, 3 windows |
| G6 | bootstrap 95% CI lower of annualized Sharpe >= 0.5 | native pooled OOS daily returns (10000 resamples, seed=42) |
| G7 | one-sided t-test p < 0.0125 on per-trade returns | native pooled OOS trades (Bonferroni 0.05/4) |

All frameworks share `validation/metrics.py` formulas (daily-return Sharpe
×sqrt(365), positive-fraction drawdown, gross-profit/loss profit factor), so
framework comparisons use identical math.

## How framework CV works

The native engine produces trade *decisions* (entry/exit timestamps,
direction). Each replay framework re-prices those decisions independently —
next-bar-open fills, configured fees — instead of trusting native fill
prices. The intentional one-bar offset vs native signal-close fills is the
engine-assumption difference G5 exists to expose. Fees = `fees_bps_per_side`
+ `slippage_bps_per_side` from the variant's `config.json`; sizing follows
`sizing.per_signal_weight_pct` / `max_gross_exposure_pct`.

## Variant contract

A variant is harness-runnable if either:

1. **Strategy contract v2 (generic pipeline, Phase D 2026-07-24 — preferred
   for new HF strategies)**: the variant ships `signals.py` exposing
   `generate_signals(df, cfg) -> iterable of Trade | dict` (trades carry
   `entry_ts`/`exit_ts` on-bar timestamps, `direction`, optional
   `size_fraction`) plus `data_loader.py` with
   `load_all(symbols, timeframe) -> {sym: df}`. No per-strategy framework
   adapters, no inline equity walk: the native leg runs through
   `_shared.run_backtest.run_backtest(cost_mode="fill")` and the same trade
   schedule is replayed by the backtrader / freqtrade / vectorbt adapters.
   A framework whose engine is not installed is recorded in
   `report["framework_skips"]` and skipped instead of crashing the harness.
   Programmatic equivalent: `validation.generic_harness.run_generic_validation(
   generate_signals, config, data, frameworks=[...])`.
2. **VPVR-family convention (legacy)**: `data_loader.py` with
   `load_all(symbols, timeframe) -> {sym: df}` and `strategy.py` with
   `run_backtest(df, cfg) -> result` exposing `.equity_curve` and `.trades`
   (Trade with `entry_date/exit_date/direction/entry_price/exit_price/pnl_pct`), or
3. **escape hatch (legacy)**: a `harness_adapter.py` in the variant dir exposing
   `run(df, cfg, symbol) -> (equity: pd.Series, trades: list[dict])`.

Variants without any contract exit 2 (UNSUPPORTED) — for new engine families,
prefer contract v2 over adding `harness_adapter.py`.

Note: the vectorbt leg's metrics are recorded in the report but do not feed
gate G5 (G5 evaluates backtrader/freqtrade only); vectorbt is an
informational third replay.

## Data

Loaders read 1m kline parquet from the canonical host path
(`/home/smark/services/strategy_display_engine_data/...`) and cache/resample
locally; the harness inherits that mechanism unchanged. Runs must happen on a
host with that data mounted.
