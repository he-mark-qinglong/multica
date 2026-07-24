# Pair Backtester Verification Harness

This directory verifies the `vpvr_xs_pairs_30m_funding_filter_*` strategy family against the existing freqtrade framework adapter without changing the source strategy directories.

## Audit target inventory

The four killed variants are:

- `vpvr_xs_pairs_30m_funding_filter_20260712`
- `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712`
- `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717`
- `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712`

Each directory contains the same strategy-local data inventory:

- `data/BTCUSDT__30m.parquet`
- `data/SOLUSDT__15m.parquet`
- `data/BTCUSDT__funding.parquet`
- `data/SOLUSDT__funding.parquet`

Each also contains these execution entry points:

- `run_backtest.py` — in-house backtest runner
- `framework_adapter_freqtrade.py` — existing freqtrade CV/replay adapter

No separate executor shell script or executor module exists in these four directories. Workspace-wide data enumeration returned 293 files when this harness was prepared; the four strategy-local inventories above were opened and verified directly.

## Run the cross-engine harness

From this directory:

```bash
python3 run_pair_harness.py <strategy_dir>
```

Example:

```bash
python3 run_pair_harness.py \
  /home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_20260712
```

The harness performs the following steps:

1. Validates that the strategy has `config.json`, `run_backtest.py`, `framework_adapter_freqtrade.py`, and strategy-local parquet data.
2. Fingerprints the source `run_backtest.py`, `results/metrics.json`, and parquet files.
3. Copies the complete strategy into an isolated `/tmp/pair-harness-*` work directory.
4. Forces the copied in-house config and copied framework adapter to use a 24bp pair round-trip cost.
5. Runs `run_backtest.py` in the copy, then runs the copied `framework_adapter_freqtrade.py`.
6. Compares in-house and framework Sharpe plus annualized return.
7. Re-fingerprints the source artifacts and fails if any source file changed.

Engine logs and numeric deltas go to stderr. The final stdout line is a JSON summary suitable for automation. A successful result includes this shape:

```json
{
  "strategy": "vpvr_xs_pairs_30m_funding_filter_20260712",
  "inhouse_sharpe": -4.86,
  "framework_sharpe": -4.86,
  "inhouse_ann_return": -0.70,
  "framework_ann_return": -0.70,
  "gap_pct": 0.04,
  "verdict": "PASS"
}
```

Exit status is `0` for `PASS`, `1` for a completed comparison that is `FAIL`, and `2` for a harness or engine error. The isolated work directory is retained under `/tmp` and included in the JSON so a failed run can be inspected without touching the source strategy.

## Cost basis

Both engines use **24bp pair round-trip cost**:

```text
(4bp fee + 2bp slippage) × 2 sides × 2 legs = 24bp
```

The existing runners do not expose cost as a command-line parameter. Therefore the harness changes only the isolated copy:

- copied `config.json`: `fees_bps_per_side=4.0`, `slippage_bps_per_side=2.0`
- copied adapter constants: in-house and freqtrade fee/slippage constants set to `4.0` and `2.0`

The source config, data, metrics, runner, adapter, and results are not modified.

## PASS and FAIL criteria

For each metric, the relative gap is:

```text
abs(inhouse - framework) / max(abs(framework), 0.01)
```

A run is `PASS` only when both conditions hold:

- Sharpe relative gap is at most `0.01` (1%).
- Annualized-return relative gap is at most `0.01` (1%).

`gap_pct` is the larger of the two gaps in percentage points. Any larger gap is `FAIL`, and stderr prints both values, signed deltas, and relative gaps.

The in-house annualized return is computed from the generated in-house equity timestamps and terminal equity. The framework annualized return is read from `framework_cv_freqtrade.json` at `framework.ann_total_return`.

## Synthetic minimal reproduction

Run:

```bash
python3 test_minimal_repro.py
```

The script creates exactly 50 BTC bars and 50 SOL bars, runs the real strategy implementation, and passes its synthetic trades through both existing replay engines. It reports one focused check for every suspect area:

1. **Funding timing/filter** — injects a funding blow-off at bar 20, verifies that future events do not alter earlier bars, records whether the event is visible on its own timestamp, and fails if the strategy opens a trade while the funding gate is false.
2. **Cross-symbol alignment** — gives every raw SOL bar a known five-minute timestamp offset. Raw overlap must be zero; the existing 30-minute resampler must produce exactly 50 timestamp-aligned bars before execution.
3. **Pair sizing** — compares the no-cost engine curve with an independent 50/50-leg oracle: `direction × (BTC_return - SOL_return) / 2`. This catches notional double-counting across legs.
4. **Fees and slippage** — checks every synthetic exit for one exact 24bp debit and requires the in-house strategy equity, in-house adapter replay, and freqtrade replay to agree within 1%.
5. **Z-score boundary** — uses lookback 8, requires the first finite z-score at position 7, then mutates bars after a cutoff and verifies that all earlier z-scores remain unchanged.

The current synthetic baseline intentionally exposes a funding-filter logic failure: entries are observed while the computed funding gate is false. The other alignment, sizing, cost, rolling-window, and dual-engine checks pass. After the strategy fix, this script should return `PASS` with exit status `0`.

The framework adapter replays the in-house trade list; it does not independently regenerate pair signals. Therefore a full-data harness `PASS` proves accounting and replay agreement, but it cannot by itself disprove signal look-ahead or an ineffective entry gate. Always require both the synthetic diagnostic and all four full-data harness runs.

## Post-fix verification sequence

After a fix lands:

1. Run `python3 test_minimal_repro.py`. Inspect any failed suspect check before using full historical data.
2. Run `python3 run_pair_harness.py <strategy_dir>` for each of the four directories listed above.
3. Require four JSON results with `verdict: PASS`, `gap_pct <= 1.0`, and `source_integrity_verified: true`.
4. If any full-data run fails, inspect its retained `/tmp/pair-harness-*` directory and compare the separately printed Sharpe and annualized-return deltas.
5. Do not accept a full-data `PASS` while the synthetic diagnostic still fails; the adapter's shared trade list can hide a strategy-signal bug.
