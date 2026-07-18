[framework-validate hourly @ 2026-07-19 03:37+08 — autopilot run bd850125-2fd6-4242-8247-cf4a9068ef02]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE — cost-fragile)

**Strategy**: `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` (iter#83 regularized cross-asset BTCUSDT/SOLUSDT 30m xs-pair z-score + VPVR confluence + funding-blowoff filter; in-house `tag=PROFITABLE`, `walk_forward.json` produced, 3 OOS windows).

**Framework**: freqtrade 2026.6 (rotating-list position 1; first framework ever applied to this strategy).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies.
- `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` (PROFITABLE, no frameworks used via the structured `framework_cv_*.json` convention, no recent CV) topped the eligible sort by `(recent_cv_count=0, total_cv_count=0, name asc)`.
- The previous run at 02:37 picked `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` (also `used=[<none>]` but `name` sorts after `_regularized_20260712`); the run at 03:37 picks the next lexicographically smaller untouched name.
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.

## Data handling

- BTCUSDT 30m native parquet (79,296 rows, 2022-01-01 00:00 → 2026-07-11 00:00).
- SOLUSDT 15m native parquet (158,587 rows) → resample to 30m via `resample_ohlcv(rule="30min", agg={open:first, high:max, low:min, close:last, volume:sum}, dropna on open)` mirroring `data_loader.load_all()` exactly.
- Common index anchored at `2022-01-01 00:00:00 UTC` for exactly `n_bars=79,294` 30m bars (matches in-house equity CSV row count and trade span 2022-01-01 → 2026-07-10).
- Trades file `trades_A_iter83_BTCUSDT_SOLUSDT.csv` has 2,703 trades spanning 2022-01-05 → 2026-07-10; all 2,703 trades replayed (entry AND exit fall on 30m-aligned bars inside the data window).
- 0 overlapping trades (single-position pair strategy, entry_ts of each trade ≥ exit_ts of previous).

## Cost model

- In-house equity walk is **bar-by-bar MTM**: `pnl_pct_per_bar[i] = pos * (a_ret - b_ret) / 2.0` where `pos=+1` for `long_a_short_b` and `pos=-1` for `short_a_long_b`. The cost is NOT amortized in the bar walk — it is only netted inside each trade's `pnl_pct` column on the trades CSV. The in-house equity CSV shows the GROSS bar walk.
- In-house cost = 1bp fee + 1bp slip × 2 sides × 2 legs = **8bp pair round-trip**.
- Validation replay reproduces the in-house equity CSV to machine precision (see below).
- Freqtrade cost = 4bp fee + 2bp slip × 2 sides × 2 legs = **24bp pair round-trip** (3× the in-house cost).
- Framework replay applies the per-bar gross mark PLUS a freqtrade cost debit at every exit bar (mirroring freqtrade's IStrategy contract for a pair strategy where pnl is marked bar-by-bar and round-trip cost hits on fill).

## OOS walk-forward divergence (3 contiguous folds, BTCUSDT/SOLUSDT 30m)

Folds aligned to the xs-pair family OOS test windows from `walk_forward.json`:
fold1 = 2023-01-01 → 2023-07-01 (in-house test_sharpe=1.327, test_return=0.767, test_mdd=-0.181),
fold2 = 2023-07-01 → 2024-01-01 (in-house test_sharpe=0.386, test_return=0.262, test_mdd=-0.212),
fold3 = 2024-01-01 → 2024-07-01 (in-house test_sharpe=1.897, test_return=1.133, test_mdd=-0.107).

In-house OOS aggregate: `mean_test_sharpe=1.2034`, `mean_test_return=0.7204`, `worst_test_mdd=-0.2121`.

| metric | inhouse (OOS) | framework (OOS) | abs rel divergence % |
|---|---|---|---|
| sharpe | 1.2034 | -2.9044 | **341.35%** |
| ann_total_return | 0.7204 | -0.3564 | **149.47%** |
| max_dd | -0.2121 | -0.4767 | **124.72%** |

`max_abs_rel_divergence_pct = 341.35%` → **> 50% W5 threshold → AUTO-ARCHIVE per W5 (NOT-PROFITABLE)**.

The Sharpe sign-flip is the structural smoking gun: in-house OOS mean sharpe = +1.20 (cleanly profitable), framework OOS mean sharpe = -2.90 (catastrophically losing). All three metrics independently exceed the 50% threshold.

## Validation (in-house replay reproduces in-house equity CSV)

- `n_bars_compared = 79,294`
- `max_abs_rel_err = 4.72e-12` (per-bar drift at the float-write precision level)
- `mean_abs_rel_err = 5.18e-13` (clean)
- `final_abs_rel_err = 5.56e-14` (replayed terminal equity $7,166,133.697893 matches in-house CSV $7,166,133.697893 to machine precision)
- `n_fills = 2,703` (all trades replayed; 0 skipped)
- Engine reproduces the in-house equity curve before the framework-cost switch confirms the replay logic is sound.

## W5 actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/results/framework_cv_freqtrade.json`
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_freqtrade.py`
3. ✅ Framework equity curve persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712-freqtrade/equity_recomputed.csv` (79,294 30m bars across 4.52y span; terminal equity $10,912.40, -89.09% from start)
4. ✅ Validation equity persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712-freqtrade/equity_validation_inhouse_cost.csv` (reproduces in-house CSV to machine precision)
5. ✅ Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712-freqtrade/results.json`
6. ❌ NO ESCALATE-TO-SMARK issued (per W5: divergence > 50% bypasses smark-decision)
7. ❌ NO modification of `metrics.json` (PROFITABLE record preserved unchanged; mtime remains at original)
8. ❌ NO modification of underlying strategy issue (no dedicated multica issue for this iter; autopilot run-only mode prohibits issue creation; `multica issue search` returns 0 issues for `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` and `BTCUSDT/SOLUSDT` regularized)

## Why this diverges (root cause: cost compounding on a high-trade-frequency pair strategy, even for in-house PROFITABLE)

This is the most damning data point in the W5 cost-fragility series so far. Unlike the previous BTC/DOGE and BTC/BNB and BTC/SOL baselines (each of which was in-house NOT-PROFITABLE before the framework-cost switch), this is the **regularized iter#83** that in-house cleared `evidence_gate.sharpe_threshold = 0.5` with a 9.5× margin (in-house sharpe = 4.7525 vs gate = 0.5) and printed **terminal equity $7,166,134** (a 70.66× return on $100k starting capital). Yet the freqtrade replay over the SAME data window prints **terminal equity $10,912** (an 89.09% loss) and a max drawdown of -90.47% (max equity $103,862 in 2022-01-21, never recovered, min equity $9,897 in 2026-01-31).

Two compounding factors:

1. **Cost convention delta**: in-house RT cost = 8bps (1bp fee + 1bp slip per side per leg × 2 sides × 2 legs). Freqtrade RT cost = 24bps (4bp fee + 2bp slip per side per leg × 2 sides × 2 legs). The 16bps incremental cost × 2,703 trades = **432bps linear drag**, but applied as a fraction of compounding equity it dominates: by 2022-01-21 (just 16 days in, after only ~24 exits) the framework equity has already peaked at $103,862 (vs in-house trending toward $130k+), and by 2026-07-10 it has decayed to **$10,912** vs in-house $7,166,134 (a $7.16M absolute loss). The strategy is structurally a **cost amplifier**: each trade's contribution to the equity curve is `pos * (a_ret - b_ret) / 2.0 - cost_rt/avg_hold_bars` (because cost amortized over the ~2.39 bars per trade), and the cost debit per bar exceeds the average gross spread mark when cost_rt=24bps.

2. **Sharpe sign-flip magnitude**: In-house OOS aggregate metrics show Sharpe = +1.20 (mean across 3 OOS folds), mean OOS return = +72.04%, worst OOS max_dd = -21.21%. The framework replay over the SAME fold windows shows Sharpe = -2.90, mean OOS return = -35.64%, worst max DD = -47.67%. The Sharpe sign-flip is the most damning signal: even the **regularized in-house PROFITABLE variant** is structurally fragile to cost assumptions, and a 3× increase in RT cost (a perfectly reasonable real-world scenario for an institutional perp execution venue vs. an idealized backtest) takes the strategy from "spectacularly profitable" (sharpe 4.75, terminal equity 70.66×) to "catastrophically unprofitable" (-89% terminal equity at the full-data level, mean OOS sharpe -2.90).

This is the same family of cost-fragility signal that surfaced in `vpvr_xs_leadlag_5m_20260711` (4,207,661% sharpe divergence, freqtrade), `vpvr_xs_basis_zscore_15m_funding_filter_20260712` (605% divergence), `vpvr_xs_pairs_30m_funding_filter_20260712` (684% divergence, BTC/SOL baseline), `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` (36,968% divergence, BTC/BNB), and the 02:37 BTC/DOGE run (4,954% divergence). Each is a strategy whose in-house backtest used a low-cost convention that, when subjected to a more conservative cost model, fails the G1/G3/G5 hard gates. The BTC/SOL regularized variant is the cleanest evidence so far that **even the in-house "PROFITABLE" xs-pair regularization is structurally cost-fragile** — the regularization is doing in-sample smoothing (lowering walk-forward ratio, raising in-sample sharpe) but it does not change the per-trade cost economics, so the freqtrade cost switch breaks the strategy identically to the un-regularized baseline.

Per cycle-46 family exhaustion: this is the 4th xs-pair 30m BTC/SOL cost-fragility auto-archive under W5 (`vpvr_xs_pairs_30m_funding_filter_20260712`, `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712`, `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717`, and now `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712`). The xs-pair 30m family on BTC/SOL is now confirmed cost-fragile across all in-house variants: NOT-PROFITABLE ones fail catastrophically (4,954% / 684% / etc.), the PROFITABLE regularized one fails identically (-89% terminal, -2.90 OOS sharpe). This is the empirical evidence base for closing the family per cycle-46 rule #3 ("do not iterate same family beyond cycle-46 exhaustion"); the family is not just "low Sharpe" — it's structurally cost-amplifying and cannot be salvaged by parameter regularization.

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_freqtrade.py`
- Framework equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712-freqtrade/equity_recomputed.csv`
- Validation equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712-freqtrade/equity_validation_inhouse_cost.csv`
- Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712-freqtrade/results.json`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json`
- [x] Adapter source committed to strategy dir
- [x] Framework equity curve persisted (79,294 30m bars, 4.52y span)
- [x] Validation equity curve persisted (reproduces in-house CSV to machine precision, max_abs_rel_err 4.72e-12)
- [x] No ESCALATE-TO-SMARK issued (W5 bypass)
- [x] No metrics.json modification (PROFITABLE record preserved)
- [x] No underlying strategy issue (none exists for this iter; autopilot run-only mode)

## Result wire

`framework-validate hourly @ 2026-07-19 03:37+08 → W5 auto-archive (NOT-PROFITABLE — cost-fragile): vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712 / freqtrade; max_abs_rel_divergence_pct = 341.35% (oos_sharpe 341.35% / ann 149.47% / max_dd 124.72%) > W5 50% threshold. In-house terminal equity $7,166,134 → framework terminal equity $10,912. xs-pair 30m BTC/SOL family confirmed cost-fragile across all in-house variants (4th W5 auto-archive in family).`
