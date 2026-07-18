[framework-validate hourly @ 2026-07-18 18:37+08 — autopilot run 227298ec-fdd8-4ba8-aa65-25adc5d4ca86]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE)

**Strategy**: `vpvr_xs_basis_zscore_15m_funding_filter_20260712` (iter#72 cross-asset BTCUSDT/ETHUSDT 15m, xs-basis log-price z-score mean-reversion + 96-bar VPVR-POC confluence gate + funding-blowoff filter, max_pairs_active=1).

**Framework**: freqtrade 2026.6 (rotating-list position 1; first framework applied to this strategy per W5).

## Selection evidence
- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies.
- `vpvr_xs_basis_zscore_15m_funding_filter_20260712` topped the eligible sort (NOT-PROFITABLE, no frameworks used via the structured `framework_cv_*.json` convention, no recent CV).
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.
- Note: a `data/framework_adapter_report.json` file existed from an earlier (unstructured) freqtrade sanity check. That file uses the pre-W5 naming convention and is not consulted by `framework_validate_scan.py`; structured CV history begins with this run.

## Data handling
- Strategy owns `data/BTCUSDT__15m.parquet` and `data/ETHUSDT__15m.parquet` (158,587 15m bars each, span 2022-01-01 → 2026-07-10).
- Trade file `results/trades_A_iter72_BTCUSDT_ETHUSDT.csv` has 11,605 trades spanning the same window; all 11,605 trades replayed (entry AND exit fall inside the data window).
- Pair z-score sign: `long_a_short_b` (5,083 trades) / `short_a_long_b` (6,522 trades). Avg bars held = 2.47, max = 26.

## Cost model
- In-house equity walk is **GROSS** (per-bar mark uses raw spread; cost only on trade-level `pnl_pct`). Validation mode replays with `cost_rt = 0` to reproduce the in-house equity CSV.
- Framework replay uses freqtrade's 24 bps pair cost (4bp fee + 2bp slip × 2 sides × 2 legs) amortised over the held bars of each trade.

## OOS walk-forward divergence (3 contiguous folds, BTCUSDT/ETHUSDT 15m)

| metric | inhouse (OOS) | framework (OOS mean) | abs rel divergence % |
|---|---|---|---|
| sharpe | -6.4836 | -45.7353 | **605.40%** |
| ann_total_return | -3.0220 | -0.9979 | **66.98%** |
| max_dd | -3.0985 | -0.9999 | **67.73%** |

`max_abs_rel_divergence_pct = 605.40%` → **> 50% W5 threshold → AUTO-ARCHIVE per W5 (NOT-PROFITABLE)**.

## Validation
- In-house cost replay (cost_rt=0) reproduces the in-house equity CSV:
  - `n_bars_compared = 158,582`
  - `final_abs_rel_err = 1.33e-12` (terminal equity 107501.61 matches in-house CSV to machine precision)
  - `mean_abs_rel_err = 1.97e-04` (per-bar drift at the held-bar rounding level — well below 0.5% threshold)
- Engine reproduces the in-house equity curve before the framework-cost switch confirms the replay logic is sound.

## W5 actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/results/framework_cv_freqtrade.json`
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_freqtrade.py`
3. ✅ Equity curve persisted: `/tmp/framework-validate-vpvr_xs_basis_zscore_15m_funding_filter_20260712-freqtrade/equity_recomputed.csv` (158,587 15m bars across 4.52y span)
4. ✅ Validation equity persisted: `/tmp/framework-validate-vpvr_xs_basis_zscore_15m_funding_filter_20260712-freqtrade/equity_validation_inhouse_cost.csv`
5. ✅ Cached results.json: `/tmp/framework-validate-vpvr_xs_basis_zscore_15m_funding_filter_20260712-freqtrade/results.json`
6. ❌ NO ESCALATE-TO-SMARK issued (per W5: divergence > 50% bypasses smark-decision)
7. ❌ NO modification of `metrics.json` (NOT-PROFITABLE record preserved unchanged)
8. ❌ NO modification of underlying strategy issue (no dedicated multica issue for this iter; autopilot instructions prohibit issue creation in this run)

## Why this diverges (root cause)

The xs-basis pair strategy's in-house equity walk is **GROSS** — cost only appears in trade-level `pnl_pct` for win-rate / profit-factor statistics, never compounded into the per-bar mark. After `validation` reproduces the in-house equity CSV to machine precision (terminal equity 107501.61 vs CSV 107501.61, final_rel_err = 1.3e-12), we switch to the framework cost model (24 bps per trade amortised over held bars ≈ 10 bps per bar on this 2.47-bar-avg-hold strategy).

With ~28,664 cumulative held bars and ~10 bps/framework-cost-per-held-bar, the framework equity drops to zero because each held bar's spread move (avg ~1 bp) is overwhelmed by the framework cost. Two factors compound:

1. **Cost amortization granularity**: 24 bps / 2.47 bars ≈ 9.7 bps per held bar. For 28,664 held bars that's a ~28% compounded drag — but the in-house per-bar mark averages near zero, so the framework's per-bar cost overwhelms the in-house per-bar mark.
2. **In-house convention asymmetry**: the in-house engine reports `total_return_pct = 7.5%` from the gross equity walk, but the trade-level `pnl_pct` (cost-net) shows the strategy loses 27.7 cumulative pnl over 11,605 trades. The framework's cost-applied equity walk surfaces the loss that the gross walk hides.

This is a **structurally meaningful divergence**: the strategy is unprofitable once cost is correctly amortised, and the gross-only in-house equity walk was masking the loss. The W5 auto-archive rule correctly identifies this as NOT-PROFITABLE.

Consistent with the broader pattern (vpvr_funding_aware_v1 / vpvr_funding_asym_4h / vpvr_options_putcall_oi_pressure_8h / vpvr_stable_depeg_regime_4h / vpvr_sentiment_attention_1m / vpvr_macro_calendar_4h / vpvr_funding_term_curve_1h / vpvr_funding_reset_window_1h): each framework CV surfaces a different cost-application asymmetry that the in-house walk doesn't surface, leading to consistent W5 auto-archive verdicts for NOT-PROFITABLE strategies.

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_freqtrade.py`
- Framework equity: `/tmp/framework-validate-vpvr_xs_basis_zscore_15m_funding_filter_20260712-freqtrade/equity_recomputed.csv`
- Validation equity: `/tmp/framework-validate-vpvr_xs_basis_zscore_15m_funding_filter_20260712-freqtrade/equity_validation_inhouse_cost.csv`
- Cached results.json: `/tmp/framework-validate-vpvr_xs_basis_zscore_15m_funding_filter_20260712-freqtrade/results.json`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json`
- [x] Adapter source committed to strategy dir
- [x] Framework equity curve persisted (158,587 15m bars, 4.52y span)
- [x] Validation equity curve persisted (reproduces in-house CSV to machine precision)
- [x] No ESCALATE-TO-SMARK issued (W5 bypass)
- [x] No metrics.json modification
- [x] No underlying strategy issue (none exists for this iter; autopilot run-only mode)

## Result wire

`framework-validate hourly @ 2026-07-18 18:37+08 → W5 auto-archive (NOT-PROFITABLE): vpvr_xs_basis_zscore_15m_funding_filter_20260712 / freqtrade; max_abs_rel_divergence_pct = 605.40% (oos_sharpe 605.40% / ann 66.98% / max_dd 67.73%) > W5 50% threshold.`
