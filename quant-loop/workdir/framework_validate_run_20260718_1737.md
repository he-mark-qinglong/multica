[framework-validate hourly @ 2026-07-18 17:37+08 — autopilot run 1526d9eb-2ea1-4369-b7c3-1665d07f819f]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE)

**Strategy**: `vpvr_stable_depeg_regime_4h_20260716` (iter#72 single-symbol BTCUSDT 4h, USDT-margined linear perp, stablecoin depeg premium (USDT/USDC) as risk-on/off regime filter on 4h VPVR-POC reversion).

**Framework**: freqtrade 2026.6 (rotating-list position 1; first framework ever applied to this strategy).

## Selection evidence
- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies.
- `vpvr_stable_depeg_regime_4h_20260716` topped the eligible sort (NOT-PROFITABLE, no frameworks used, no recent CV).
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.

## Data handling
- Repo holds only `perp_1m/BTCUSDT_1m.parquet` (3.6M rows, 2019-09-08 → 2026-07-17). No `perp_4h` on disk.
- Adapter resamples 1m → 4h on the fly: open = first 1m open in bucket, high = max, low = min, close = last 1m close.
- Resampled span: 15,025 4h bars from 2019-09-08 16:00 to 2026-07-17 16:00 (6.86 years).
- Trade file `trades_4h_BTCUSDT.csv` has 123 trades spanning 2023-02-09 → 2027-07-19; 97 trades replayed (entry AND exit fall inside 1m data window), 26 missed as out-of-window (post-2026-07-17 synthetic forward tail).

## OOS walk-forward divergence (5 folds, BTCUSDT 4h USDT-margined linear perp)

| metric | inhouse | framework (OOS mean) | abs rel divergence % |
|---|---|---|---|
| sharpe | 3.8737 | 1.954e-05 | **99.9995%** |
| ann_total_return | 0.007613 | 0.0 | **100.00%** |
| max_dd | -0.001025 | -5.501e-05 | **94.63%** |

`max_abs_rel_divergence_pct = 100.0000%` → **> 50% W5 threshold → AUTO-ARCHIVE per W5 (NOT-PROFITABLE)**.

## W5 actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_stable_depeg_regime_4h_20260716/results/framework_cv_freqtrade.json`
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_freqtrade.py`
3. ✅ Equity curve persisted: `/tmp/framework-validate-vpvr_stable_depeg_regime_4h_20260716-freqtrade/equity_recomputed.csv` (15,025 4h bars across 6.86y span)
4. ✅ `results.json` cached at `/tmp/framework-validate-vpvr_stable_depeg_regime_4h_20260716-freqtrade/results.json`
5. ❌ NO ESCALATE-TO-SMARK issued (per W5: divergence > 50% bypasses smark-decision)
6. ❌ NO modification of `metrics.json` (NOT-PROFITABLE record preserved with `B1 synthetic sanity run` note)
7. ❌ NO modification of underlying strategy issue (strategy has no dedicated multica issue; tag already terminal NOT-PROFITABLE)

## Why this diverges (root cause)
The freqtrade IStrategy contract applies `pnl_pct × risk_target_pct` linearly across held 4h bars (3–60 bars on this 4h strategy). With 97 trades over 15,025 4h bars, the per-bar deltas are on the order of `~5e-7` to `~5e-5`, producing equity drifts on the order of `~1e-5`. The in-house engine instead marks-to-market at exit and reports an aggregated `pnl_pct = gross_move - round_trip_cost`, surfacing the realized exit PnL. Per-bar vs exit-MTM is a well-known divergence source for linear-apply contracts vs spot-MTM engines — consistent with SMA-34886 / 34893 / 34903 / 34908 / 34933 (vpvr_funding_asym_4h / vpvr_funding_aware_v1 / vpvr_funding_regime_15m / vpvr_funding_reset_window_1h / vpvr_funding_term_curve_1h), the 15:37 run (vpvr_options_putcall_oi_pressure_8h), and the 16:37 run (vpvr_sentiment_attention_1m).

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_stable_depeg_regime_4h_20260716/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_freqtrade.py`
- Equity curve: `/tmp/framework-validate-vpvr_stable_depeg_regime_4h_20260716-freqtrade/equity_recomputed.csv`
- Cached results.json: `/tmp/framework-validate-vpvr_stable_depeg_regime_4h_20260716-freqtrade/results.json`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json`
- [x] Adapter source committed to strategy dir
- [x] Equity curve persisted (15,025 4h bars, 6.86y span)
- [x] Run-output issue created at status `done` per `[framework-validate {{date}}]` template
- [x] No ESCALATE-TO-SMARK issued (W5 bypass)
- [x] No metrics.json modification
- [x] No underlying strategy issue (none exists for this iter)