[framework-validate hourly @ 2026-07-18 21:37+08 — autopilot run 0817f8fe-2d50-4041-892c-3b4ffed3db80]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE — cost-fragile)

**Strategy**: `vpvr_xs_pairs_30m_funding_filter_20260712` (iter#81 cross-asset BTCUSDT/SOLUSDT 30m xs-pair z-score + VPVR confluence + funding-blowoff filter; in-house `tag=PROFITABLE`, walk_forward.json available with 3 OOS folds).

**Framework**: freqtrade 2026.6 (rotating-list position 1; first framework ever applied to this strategy).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies.
- `vpvr_xs_pairs_30m_funding_filter_20260712` (PROFITABLE, no frameworks used via the structured `framework_cv_*.json` convention, no recent CV) topped the eligible sort.
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.

## Data handling

- BTCUSDT 30m native parquet; SOLUSDT 15m parquet resampled on-the-fly to 30m via `strategy.resample_ohlcv` (matches in-house `data_loader.py`).
- Common index anchored at `2022-01-01 00:00:00 UTC` for exactly `n_bars=79,294` 30m bars (matches in-house equity CSV row count and trade span 2022-01-01 → 2026-07-10).
- Resampled span: 2022-01-01 00:00:00 → 2026-07-10 23:30:00 (4.52 years, 79,294 30m bars).
- Trades file `trades_A_iter81_BTCUSDT_SOLUSDT.csv` has 5,836 trades spanning 2022-01-03 → 2026-07-10; all 5,836 trades replayed (entry AND exit fall on 30m-aligned bars inside the data window).
- 0 overlapping trades (single-position pair strategy, entry_ts of each trade ≥ exit_ts of previous).

## Cost model

- In-house equity walk is **bar-by-bar MTM**: `pnl_pct_per_bar[i] = pos * (a_ret - b_ret) / 2.0` where `pos=+1` for `long_a_short_b` and `pos=-1` for `short_a_long_b`. The cost is NOT amortized in the bar walk — it is only netted inside each trade's `pnl_pct` column on the trades CSV. The in-house equity CSV shows the GROSS bar walk.
- In-house cost = 1bp fee + 1bp slip × 2 sides × 2 legs = **8bp pair round-trip**.
- Validation replay reproduces the in-house equity CSV to machine precision (see below).
- Freqtrade cost = 4bp fee + 2bp slip × 2 sides × 2 legs = **24bp pair round-trip** (3× the in-house cost).
- Framework replay applies the per-bar gross mark PLUS a freqtrade cost debit at every exit bar (mirroring freqtrade's IStrategy contract for a pair strategy where pnl is marked bar-by-bar and round-trip cost hits on fill).

## OOS walk-forward divergence (3 contiguous folds, BTCUSDT/SOLUSDT 30m)

Folds aligned to in-house `walk_forward.json` test windows: fold1 = 2023-01-01 → 2023-07-01, fold2 = 2023-07-01 → 2024-01-01, fold3 = 2024-01-01 → 2024-07-01.

| metric | inhouse (walk_forward.json mean) | framework (OOS mean) | abs rel divergence % |
|---|---|---|---|
| sharpe | 1.0000 | -5.8485 | **684.84%** |
| ann_total_return | 1.1624 | -0.7540 | **164.87%** |
| max_dd | -0.2532 | -0.6062 | **139.46%** |

`max_abs_rel_divergence_pct = 684.84%` → **> 50% W5 threshold → AUTO-ARCHIVE per W5 (NOT-PROFITABLE)**.

## Validation (in-house replay reproduces in-house equity CSV)

- `n_bars_compared = 79,294`
- `max_abs_rel_err = 4.86e-12` (per-bar drift at the float-write precision level)
- `mean_abs_rel_err = 2.03e-13` (clean)
- `final_abs_rel_err = 8.32e-16` (replayed terminal equity 501,387,019.41 matches in-house CSV 501,387,019.41 to machine precision)
- `n_fills = 5,836` (all trades replayed; 0 skipped)
- Engine reproduces the in-house equity curve before the framework-cost switch confirms the replay logic is sound.

## W5 actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_20260712/results/framework_cv_freqtrade.json`
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_freqtrade.py`
3. ✅ Framework equity curve persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_20260712-freqtrade/equity_recomputed.csv` (79,294 30m bars across 4.52y span)
4. ✅ Validation equity persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_20260712-freqtrade/equity_validation_inhouse_cost.csv` (reproduces in-house CSV to machine precision)
5. ✅ Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_20260712-freqtrade/results.json`
6. ❌ NO ESCALATE-TO-SMARK issued (per W5: divergence > 50% bypasses smark-decision)
7. ❌ NO modification of `metrics.json` (PROFITABLE record preserved with `tag=PROFITABLE`)
8. ❌ NO modification of underlying strategy issue (autopilot run-only mode prohibits issue creation)

## Why this diverges (root cause: cost compounding on a high-trade-frequency strategy)

This strategy is a cross-asset pair strategy that fires very frequently: 5,836 trades over 4.52 years ≈ **1,290 trades/year ≈ 1 trade every 4 hours**. The in-house equity walk has the round-trip cost baked into the per-trade `pnl_pct` column but NOT into the bar-by-bar equity walk, so the in-house equity CSV compounds only the **gross per-bar mark** at `pos * (a_ret - b_ret) / 2.0`. With 8bp in-house RT cost per trade, the average in-house net pnl/trade ≈ +22bps (5,490 winning trades, profit_factor 1.73, win_rate 59.8%), and the in-house equity compounds from $100k → $501M (5,012× return) over the 4.52-year span.

The freqtrade convention applies the RT cost as a per-fill debit against the equity curve. Two compounding factors:

1. **Cost convention**: in-house RT cost = 8bps (1bp fee + 1bp slip per side per leg × 2 sides × 2 legs). Freqtrade RT cost = 24bps (4bp fee + 2bp slip per side per leg × 2 sides × 2 legs). The 16bps incremental cost × 5,836 trades = **933bps linear drag**, but applied as a fraction of compounding equity: `(1 - 0.0024)^5836 ≈ 8.1e-7`, i.e. the freqtrade equity at the terminal bar is ~**0.00008% of the in-house equity** at the terminal bar if costs are applied on the same compounding base. Practically: by 2023-01-01 (after only ~1,300 exits) the framework equity is already **4.3% of the in-house equity** (64k vs 1.49M), and by 2026-07-10 it has decayed to **$418** vs in-house $501M.

2. **Sharpe sign-flip**: In-house walk-forward test windows (3 × 6-month OOS folds from 2023-01 → 2024-07) all show mean OOS Sharpe = 1.0 (range 0.59 → 1.39 per fold), mean OOS total return = 116%, worst OOS max DD = -25.3%. The framework replay over the SAME fold windows shows Sharpe = -5.85, ann return = -75.4%, max DD = -60.6%. The sign-flip on Sharpe is the single most damning signal: the strategy is **structurally fragile to cost assumptions**, and a 3× increase in RT cost (a perfectly reasonable real-world scenario for an institutional perp execution venue vs. an idealized backtest) flips the strategy from "highly profitable" to "catastrophically unprofitable".

This is the same family of cost-fragility signal that surfaced in `vpvr_xs_leadlag_5m_20260711` (4,207,661% sharpe divergence, freqtrade), `vpvr_xs_basis_zscore_15m_funding_filter_20260712` (605% divergence), and the broader run of W5 auto-archives. Each is a strategy whose in-house backtest used a low-cost convention that, when subjected to a more conservative cost model, fails the G1/G3/G5 hard gates.

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_20260712/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_freqtrade.py`
- Framework equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_20260712-freqtrade/equity_recomputed.csv`
- Validation equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_20260712-freqtrade/equity_validation_inhouse_cost.csv`
- Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_20260712-freqtrade/results.json`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json`
- [x] Adapter source committed to strategy dir
- [x] Framework equity curve persisted (79,294 30m bars, 4.52y span)
- [x] Validation equity curve persisted (reproduces in-house CSV to machine precision)
- [x] No ESCALATE-TO-SMARK issued (W5 bypass)
- [x] No metrics.json modification
- [x] No underlying strategy issue (autopilot run-only mode)

## Result wire

`framework-validate hourly @ 2026-07-18 21:37+08 → W5 auto-archive (NOT-PROFITABLE — cost-fragile): vpvr_xs_pairs_30m_funding_filter_20260712 / freqtrade; max_abs_rel_divergence_pct = 684.84% (oos_sharpe 684.84% / ann 164.87% / max_dd 139.46%) > W5 50% threshold.`