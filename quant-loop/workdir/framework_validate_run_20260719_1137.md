[framework-validate hourly @ 2026-07-19 11:37+08 — autopilot run 51b451aa-99c0-46a6-a0b4-19e4f51e169a]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE)

**Strategy**: `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` (iter#82 ETHUSDT/SOLUSDT 30m cross-asset z-score + VPVR confluence + funding-blowoff filter; in-house `tag=NOT-PROFITABLE`).

**Framework**: freqtrade 2026.6 (rotation position 1; first framework recorded for this strategy).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` found 26 terminal strategies.
- The canonical queue selected `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` first among candidates with no framework CV record and no CV in the preceding 7 days.
- Rotation: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`; `freqtrade` was unused for this strategy.

## Data and replay

- ETHUSDT 15m parquet and SOLUSDT 15m parquet, each resampled to 30m with `open=first, high=max, low=min, close=last, volume=sum`, matching `data_loader.py`.
- Common sample: 79,294 30m bars, `2022-01-01 00:00:00` → `2026-07-10 23:30:00`.
- 2,525 trades loaded; 2,525 completed trades replayed; 0 skipped.
- The in-house equity CSV contains a terminal open `short_a_long_b` position beginning `2026-07-10 18:30:00` that is not present in the closed-trades CSV. The adapter detected and included its final-bar marks without inventing an exit fee.

## OOS walk-forward comparison

In-house reference is `results/walk_forward.json` (3 chronological folds):

| metric | in-house OOS | freqtrade OOS | absolute relative divergence |
|---|---:|---:|---:|
| mean Sharpe | -0.661005 | -10.795415 | **1533.182%** |
| mean annualized/total return | -0.279873 | -0.764402 | **173.124%** |
| worst max drawdown | -0.635693 | -0.588703 | **7.392%** |

`max_abs_rel_divergence = 1533.182% > 50%` → **W5 AUTO-ARCHIVE / NOT-PROFITABLE; no ESCALATE-TO-SMARK**. The strategy already fails the negative annualized-return and profitability gates in-house; framework replay confirms severe cost fragility.

## Validation and checks

- In-house replay validation: `n_bars_compared=79,294`, `max_abs_rel_err=5.40e-12`, `mean_abs_rel_err=2.47e-12`, `final_abs_rel_err=2.69e-13`.
- Terminal equity: in-house `$106,341.179153`; reproduced `$106,341.179153`.
- `python3 -m py_compile .../framework_adapter_freqtrade.py`: PASS.
- Candidate tests: **11 passed, 2 skipped**, 1 existing `PytestReturnNotNoneWarning`.
- `metrics.json` was not modified. No strategy issue mutation was performed because this run has no assigned issue ID and the run-only workflow provides no corresponding issue target.

## Output sink (auditable)

1. CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/results/framework_cv_freqtrade.json`
2. Adapter: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_freqtrade.py`
3. Framework equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712-freqtrade/equity_recomputed.csv`
4. In-house replay equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712-freqtrade/equity_validation_inhouse_cost.csv`
5. Cached CV result: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712-freqtrade/results.json`
6. This run report: `/home/smark/multica/quant-loop/workdir/framework_validate_run_20260719_1137.md`

## Result wire

`framework-validate hourly @ 2026-07-19 11:37+08 → W5 auto-archive (NOT-PROFITABLE): vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712 / freqtrade; max_abs_rel_divergence_pct=1533.182% (Sharpe 1533.182%, annualized return 173.124%, maxDD 7.392%) > 50%; in-house terminal equity $106,341.179153 reproduced to 2.69e-13 relative error.`
