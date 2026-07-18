[framework-validate hourly @ 2026-07-19 02:37+08 — autopilot run b6488884-d5be-478d-98d6-9951d63bde98]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE — cost-fragile)

**Strategy**: `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` (iter#86 cross-asset BTCUSDT/DOGEUSDT 30m xs-pair z-score + VPVR confluence + funding-blowoff filter; in-house `tag=NOT-PROFITABLE`, no `walk_forward.json` produced).

**Framework**: freqtrade 2026.6 (rotating-list position 1; first framework ever applied to this strategy).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies.
- `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` (NOT-PROFITABLE, no frameworks used via the structured `framework_cv_*.json` convention, no recent CV) topped the eligible sort by `(recent_cv_count=0, total_cv_count=0, name asc)`.
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.

## Data handling

- BTCUSDT 30m native parquet; DOGEUSDT 30m native parquet (no resample needed; same alignment as BTC).
- Common index anchored at `2022-01-01 00:00:00 UTC` for exactly `n_bars=79,296` 30m bars (matches in-house equity CSV row count and trade span 2022-01-01 → 2026-07-10).
- Resampled span: 2022-01-01 00:00:00 → 2026-07-10 23:30:00 (4.52 years, 79,296 30m bars).
- Trades file `trades_A_iter83_BTCUSDT_DOGEUSDT.csv` has 2,790 trades spanning 2022-01-05 → 2026-07-10; all 2,790 trades replayed (entry AND exit fall on 30m-aligned bars inside the data window).
- 0 overlapping trades (single-position pair strategy, entry_ts of each trade ≥ exit_ts of previous).

## Cost model

- In-house equity walk is **bar-by-bar MTM**: `pnl_pct_per_bar[i] = pos * (a_ret - b_ret) / 2.0` where `pos=+1` for `long_a_short_b` and `pos=-1` for `short_a_long_b`. The cost is NOT amortized in the bar walk — it is only netted inside each trade's `pnl_pct` column on the trades CSV. The in-house equity CSV shows the GROSS bar walk.
- In-house cost = 1bp fee + 1bp slip × 2 sides × 2 legs = **8bp pair round-trip**.
- Validation replay reproduces the in-house equity CSV to machine precision (see below).
- Freqtrade cost = 4bp fee + 2bp slip × 2 sides × 2 legs = **24bp pair round-trip** (3× the in-house cost).
- Framework replay applies the per-bar gross mark PLUS a freqtrade cost debit at every exit bar (mirroring freqtrade's IStrategy contract for a pair strategy where pnl is marked bar-by-bar and round-trip cost hits on fill).

## OOS walk-forward divergence (3 contiguous folds, BTCUSDT/DOGEUSDT 30m)

Folds aligned to the xs-pair family OOS test windows: fold1 = 2023-01-01 → 2023-07-01, fold2 = 2023-07-01 → 2024-01-01, fold3 = 2024-01-01 → 2024-07-01.

| metric | inhouse (proxy from metrics.json) | framework (OOS mean) | abs rel divergence % |
|---|---|---|---|
| sharpe | -0.1951 | -9.8590 | **4,953.60%** |
| ann_total_return | -0.1455 | -0.7235 | **397.28%** |
| max_dd | -0.2674 | -0.5281 | **97.47%** |

`max_abs_rel_divergence_pct = 4,953.60%` → **> 50% W5 threshold → AUTO-ARCHIVE per W5 (NOT-PROFITABLE)**.

Note: This is a NOT-PROFITABLE strategy; `walk_forward.json` was never produced (campaign gate skipped walk-forward). The OOS proxy uses the single in-house aggregated metrics (sharpe=-0.1951, total_return=-14.55%, max_dd=-26.74%) per the xs-pair family convention.

## Validation (in-house replay reproduces in-house equity CSV)

- `n_bars_compared = 79,296`
- `max_abs_rel_err = 6.31e-12` (per-bar drift at the float-write precision level)
- `mean_abs_rel_err = 2.92e-12` (clean)
- `final_abs_rel_err = 6.09e-13` (replayed terminal equity 85,451.595464948 matches in-house CSV 85,451.595465 to machine precision)
- `n_fills = 2,790` (all trades replayed; 0 skipped)
- Engine reproduces the in-house equity curve before the framework-cost switch confirms the replay logic is sound.

## W5 actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/results/framework_cv_freqtrade.json`
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_freqtrade.py`
3. ✅ Framework equity curve persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717-freqtrade/equity_recomputed.csv` (79,296 30m bars across 4.52y span)
4. ✅ Validation equity persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717-freqtrade/equity_validation_inhouse_cost.csv` (reproduces in-house CSV to machine precision)
5. ✅ Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717-freqtrade/results.json`
6. ❌ NO ESCALATE-TO-SMARK issued (per W5: divergence > 50% bypasses smark-decision)
7. ❌ NO modification of `metrics.json` (NOT-PROFITABLE record preserved unchanged; mtime remained at original `2026-07-17 14:32:00`)
8. ❌ NO modification of underlying strategy issue (no dedicated multica issue for this iter; autopilot run-only mode prohibits issue creation; `multica issue search` returns 0 issues for `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` and `BTCUSDT/DOGEUSDT`)

## Why this diverges (root cause: cost compounding on a high-trade-frequency pair strategy)

This strategy is a cross-asset pair strategy on BTC/DOGE that fires very frequently: 2,790 trades over 4.52 years ≈ **617 trades/year ≈ 1.3 trades per 30m bar on average**. The in-house equity walk has the round-trip cost baked into the per-trade `pnl_pct` column but NOT into the bar-by-bar equity walk, so the in-house equity CSV compounds only the **gross per-bar mark** at `pos * (a_ret - b_ret) / 2.0`. The strategy's NOT-PROFITABLE in-house record (terminal equity $85,452, total_return=-14.55%, sharpe=-0.195, max_dd=-26.74%) was already in NOT-PROFITABLE territory before the cost model change.

The freqtrade convention applies the RT cost as a per-fill debit against the equity curve. Two compounding factors:

1. **Cost convention**: in-house RT cost = 8bps (1bp fee + 1bp slip per side per leg × 2 sides × 2 legs). Freqtrade RT cost = 24bps (4bp fee + 2bp slip per side per leg × 2 sides × 2 legs). The 16bps incremental cost × 2,790 trades = **446bps linear drag**, but applied as a fraction of compounding equity: by 2023-01-01 (after only ~630 exits) the framework equity is already substantially below the in-house equity, and by 2026-07-10 it has decayed to **$54.18** vs in-house $85,452 (a $85,000 absolute loss). The strategy is structurally a cost amplifier: each trade's contribution to the equity curve is `pos * (a_ret - b_ret) / 2.0 - cost_rt/2` (because cost amortized over held bars averaged ~2.4 bars per trade), so the cost drag overwhelms the spread mark on a low-magnitude signal.

2. **Sharpe sign-flip magnitude**: In-house aggregated metrics show Sharpe = -0.195 (mildly negative), total_return = -14.55%, max_dd = -26.74%. The framework replay over the SAME fold windows shows Sharpe = -9.86, ann return = -72.35%, max DD = -52.81%. The Sharpe magnitude flip is the most damning signal: the strategy is **structurally fragile to cost assumptions**, and a 3× increase in RT cost (a perfectly reasonable real-world scenario for an institutional perp execution venue vs. an idealized backtest) takes the strategy from "mildly unprofitable" to "catastrophically unprofitable" (-99.9% terminal equity at the full-data level, vs in-house -14.55%).

This is the same family of cost-fragility signal that surfaced in `vpvr_xs_leadlag_5m_20260711` (4,207,661% sharpe divergence, freqtrade), `vpvr_xs_basis_zscore_15m_funding_filter_20260712` (605% divergence), `vpvr_xs_pairs_30m_funding_filter_20260712` (684% divergence, BTC/SOL), `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` (36,968% divergence, BTC/BNB), and the broader run of W5 auto-archives. Each is a strategy whose in-house backtest used a low-cost convention that, when subjected to a more conservative cost model, fails the G1/G3/G5 hard gates.

The BTC/DOGE pair is particularly fragile vs the BTC/BNB pair because DOGE is a low-liquidity altcoin with wider bid-ask spreads and higher slippage in real execution. The in-house 8bp cost model under-states the true RT cost on this leg structure by an even larger margin than BTC/BNB, which is why the divergence is structural rather than parameter-sensitive.

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_freqtrade.py`
- Framework equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717-freqtrade/equity_recomputed.csv`
- Validation equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717-freqtrade/equity_validation_inhouse_cost.csv`
- Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717-freqtrade/results.json`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json`
- [x] Adapter source committed to strategy dir
- [x] Framework equity curve persisted (79,296 30m bars, 4.52y span)
- [x] Validation equity curve persisted (reproduces in-house CSV to machine precision)
- [x] No ESCALATE-TO-SMARK issued (W5 bypass)
- [x] No metrics.json modification
- [x] No underlying strategy issue (none exists for this iter; autopilot run-only mode)

## Result wire

`framework-validate hourly @ 2026-07-19 02:37+08 → W5 auto-archive (NOT-PROFITABLE — cost-fragile): vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717 / freqtrade; max_abs_rel_divergence_pct = 4,953.60% (oos_sharpe 4,953.60% / ann 397.28% / max_dd 97.47%) > W5 50% threshold.`