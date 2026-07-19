[framework-validate hourly @ 2026-07-19 13:37+08 — autopilot run 899a6876-0d09-4b17-9dc8-641b775f453e]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE — cost-fragile)

**Strategy**: `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` (iter#85 V5-loose ETHUSDT/SOLUSDT 30m cross-asset z-score + VPVR confluence + funding-blowoff filter — original V5 settings, no regularization; in-house `tag=NOT-PROFITABLE`, **`walk_forward.json` not produced**).

**Framework**: freqtrade 2026.6 (rotation position 1; first framework ever applied to this strategy — `used=[<none>]` in the scan).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies (cutoff = 2026-07-12T05:39 UTC).
- The previous run at 12:37 consumed `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` (iter#84 ETH/SOL V7 regularized sibling) and the 11:37 run consumed `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` (iter#82). With those dispatched, the top-priority eligible strategy for the 13:37 run is `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` — `tag=NOT-PROFITABLE`, `used=[<none>]`, no recent CV. Sort key `(recent_cv_count=0, total_cv_count=0, name asc)` puts it first.
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.

## Data handling

- ETHUSDT 30m native parquet (79,296 rows, 2022-01-01 00:00 → 2026-07-10 23:30).
- SOLUSDT 30m native parquet (79,296 rows, identical span). No 15m→30m resample needed for the V5-loose ETH/SOL variant — both legs are natively 30m.
- Common index anchored at `2022-01-01 00:00:00 UTC` for exactly `n_bars=79,296` 30m bars (matches in-house equity CSV row count).
- Trades file `trades_A_iter83_ETHUSDT_SOLUSDT.csv` has **5,642 trades** spanning 2022-01-03 → 2026-07-10 (filename `iter83` is a legacy holdover from the original trades persist; the V5-loose iter#85 strategy overwrote the file in place — 5,642 trades matches `metrics.json.n_trades` exactly).
- All 5,642 trades replayed (entry AND exit fall on 30m-aligned bars inside the data window). 0 overlapping trades (single-position pair strategy).
- **No terminal open position** was detected (the final-bar tail of the in-house equity is flat from 2026-07-10 22:30 onward at $131,040.548827).

## Cost model

- In-house equity walk is **bar-by-bar MTM**: `pnl_pct_per_bar[i] = pos * (a_ret - b_ret) / 2.0` where `pos=+1` for `long_a_short_b` and `pos=-1` for `short_a_long_b`. Cost is NOT amortized in the bar walk — it is netted inside each trade's `pnl_pct` column on the trades CSV. The in-house equity CSV shows the GROSS bar walk.
- In-house cost = 1bp fee + 1bp slip × 2 sides × 2 legs = **8bp pair round-trip** (per `config.json: fees_bps_per_side=1.0, slippage_bps_per_side=1.0`).
- Validation replay reproduces the in-house equity CSV to machine precision (see below).
- Freqtrade cost = 4bp fee + 2bp slip × 2 sides × 2 legs = **24bp pair round-trip** (3× the in-house cost).
- Framework replay applies the per-bar gross mark PLUS a freqtrade cost debit at every exit bar (mirroring freqtrade's IStrategy contract for a pair strategy where pnl is marked bar-by-bar and round-trip cost hits on fill).

## OOS walk-forward divergence (3 contiguous folds, ETHUSDT/SOLUSDT 30m V5-loose)

`walk_forward.json` was not produced for the V5-loose variant — same situation as the iter#84 sibling. Per `framework_validate_run_20260719_1137.md` and `framework_validate_run_20260719_1237.md` precedent, in-house aggregated metrics.json serves as the OOS reference; any >50% divergence still auto-archives.

Folds aligned to the xs-pair family OOS test windows:

| fold | span | framework sharpe | framework ann | framework total | framework mdd |
|------|------|-----------------:|--------------:|----------------:|--------------:|
| 1 | 2023-01-01 → 2023-06-30 | -18.647 | -0.969 | -0.820 | -0.821 |
| 2 | 2023-07-01 → 2023-12-31 | -15.064 | -0.928 | -0.735 | -0.739 |
| 3 | 2024-01-01 → 2024-06-30 | -19.498 | -0.959 | -0.796 | -0.797 |
| **OOS mean** | — | **-17.7362** | **-0.9518** | **-0.7834** | **-0.8214** (worst) |

In-house OOS proxy (aggregated `metrics.json`, full 2022-01-01 → 2026-07-10 span):
sharpe = +0.422, total_return = +0.31041, max_dd = -0.22230.

| metric | inhouse (proxy) | framework (OOS mean) | abs rel divergence % |
|---|---:|---:|---:|
| sharpe | +0.422 | -17.7362 | **4302.8817%** |
| ann_total_return | +0.31041 | -0.95176 | **406.6399%** |
| max_dd (worst fold) | -0.22230 | -0.82143 | **269.5026%** |

`max_abs_rel_divergence_pct = 4302.8817%` → **> 50% W5 threshold → AUTO-ARCHIVE per W5 (NOT-PROFITABLE)**. All three metrics independently exceed the 50% threshold.

The Sharpe sign-flip is the structural smoking gun: in-house proxy sharpe = +0.422 (marginally profitable in full sample), framework OOS mean sharpe = -17.7362 (catastrophically losing across all three half-year sub-windows).

## Why the divergence is so large

1. **Cost convention delta (3×)**: in-house RT cost = 8bps (1bp fee + 1bp slip per side per leg × 2 sides × 2 legs). Freqtrade RT cost = 24bps (4bp fee + 2bp slip per side per leg × 2 sides × 2 legs). The 16bps incremental cost × 5,642 trades = **902bps linear drag** — almost 9.02 pp of equity displacement at the entry-exit level — but applied as a fraction of compounding equity, it dominates: by the final bar (2026-07-10) the framework equity has decayed to **$0.17** vs in-house **$131,040.55**, an absolute terminal equity gap of $131,040.38 over 79,296 30m bars.

2. **Cost-fragility geometry of the V5-loose variant**: the V5-loose params use a tighter `zscore_entry_threshold=2.0`, `zscore_exit_threshold=0.5`, and `funding_filter_threshold=0.0005` than the regularized V7 sibling (which is iter#84). The looser thresholds produce more entries (5,642 trades here vs 2,588 for iter#84 over the same span) with smaller edge-per-trade, so the same per-trade cost debit (24bp) extracts a much larger fraction of gross alpha — and in the framework replay, total alpha goes negative.

3. **Sharpe sign-flip across 3 contiguous OOS folds**: unlike iter#84's V7 regularized sibling (which had only one catastrophic fold offsetting two better ones), the V5-loose fails identically across all three folds: OOS sharpe ranges from -15.06 (fold 2) to -19.50 (fold 3), total returns from -73.5% to -82.0%. This is even cleaner evidence of structural cost-fragility than iter#84 (which printed 42507.62% divergence but partly amplified by in-house aggregate proxy over a single span).

4. **Terminal-open-position audit**: the framework replay correctly handled the lack of terminal open position (both legs flat from 2026-07-10 22:30 onward), so the divergence is not an artifact of an un-modeled opening tail. The cost debit is the entire signal.

This is the **5th W5 cost-fragility auto-archive in the xs-pair 30m family** (after `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` iter#82 at 1533.18%, the iter#84 sibling at 42507.62%, and four BTC/SOL variants `vpvr_xs_pairs_30m_funding_filter_20260712`, `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712`, `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717`, `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` at 684%, 295%, 4875%, 341.35% respectively per prior run reports). The V5-loose ETH/SOL variant confirms the pattern: even when in-house parameters are tuned for PROFITABILITY (V7 regularized), the underlying family geometry is cost-fragile. The V5-loose variant here is the "non-regularized" baseline → it shows the same catastrophic pattern as expected.

## Validation (in-house replay reproduces in-house equity CSV)

- `n_bars_compared = 79,296`
- `max_abs_rel_err = 5.24e-12` (per-bar drift at the float-write precision level)
- `mean_abs_rel_err = 2.22e-12`
- `final_abs_rel_err = 3.33e-12` (replayed terminal equity `$131,040.5488` matches in-house CSV `$131,040.5488` to machine precision)
- `n_fills = 5,642` (all trades replayed; 0 skipped)
- Engine reproduces the in-house equity curve before the framework-cost switch confirms the replay logic is sound. Validation is pure in-house-cost (cost_rt=0), so the MTM walk is the GROSS bar mark. The match to in-house CSV proves the adapter faithfully implements the in-house convention; framework-cost divergence is then a genuine cost-fragility signal, not a replay artifact.

## W5 actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/results/framework_cv_freqtrade.json` (`w5_auto_archive: true`, `w5_verdict: "AUTO_ARCHIVED"`, `w5_tipping_metrics: ["sharpe", "ann_total_return", "max_dd"]`).
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_freqtrade.py` (`python3 -m py_compile` PASS).
3. ✅ Framework equity curve persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717-freqtrade/equity_recomputed.csv` (79,296 30m bars across 4.52y span; terminal equity $0.17 from $100k start, -99.99983%).
4. ✅ Validation equity persisted: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717-freqtrade/equity_validation_inhouse_cost.csv` (reproduces in-house CSV to machine precision).
5. ✅ Cached results.json: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717-freqtrade/results.json`.
6. ❌ NO ESCALATE-TO-SMARK issued (per W5: divergence > 50% bypasses smark-decision).
7. ❌ NO modification of `metrics.json` (NOT-PROFITABLE record preserved unchanged; per W5 §3 the tag is not modified even on auto-archive).
8. ❌ NO modification of underlying strategy issue (no dedicated multica issue exists for iter#85 V5-loose specifically: `multica issue search "vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717"` returns the family-level `SMA-35012` (PAPER-TRADING) and `SMA-35036` (PRIMARY EPIC), neither of which is a per-strategy issue that can be auto-archived by this autopilot; the run-only autopilot protocol prohibits issue creation).

## W5 self-check (anti-loop / anti-noise)

- **Auto-archive mandatory, no ESCALATE**: per W5 §W5.2 (2026-07-12 audit append), `|divergence| > 50% → archive NOT-PROFITABLE` without smark-decision. This divergence (4302.88%) is over 86× the threshold, so the auto-archive path is the only correct disposition. ESCALATE would be a violation of W5 protocol.
- **No metrics.json mutation**: per W5 §W5.3 — the record stays complete so future audits can reprocess.
- **No smark awakening**: per W5 audit purpose ("Smark 不会被叫醒处理明显 broken 的策略") — explicit intent of W5 is to silence smark-decision escalation on catastrophic divergences.
- **Run-only autopilot** with no assigned issue ID, per `multica autopilot runs 51e7cb03` (this run id `899a6876-0d09-4b17-9dc8-641b775f453e`, status `running`, `issue_id: null`). No issue mutation is performed or required.

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_freqtrade.py`
- Framework equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717-freqtrade/equity_recomputed.csv`
- Validation equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717-freqtrade/equity_validation_inhouse_cost.csv`
- Cached result: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717-freqtrade/results.json`
- This run report: `/home/smark/multica/quant-loop/workdir/framework_validate_run_20260719_1337.md`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json` (`w5_auto_archive: true`)
- [x] Adapter source committed to strategy dir
- [x] Framework equity curve persisted (79,296 30m bars, 4.52y span)
- [x] Validation equity curve persisted (reproduces in-house CSV to machine precision, max_abs_rel_err 5.24e-12)
- [x] No ESCALATE-TO-SMARK issued (W5 bypass; 4302.88% > 50%)
- [x] No metrics.json modification (NOT-PROFITABLE record preserved)
- [x] No underlying strategy issue (none exists for this iter; autopilot run-only mode)
- [x] Run report written to `/home/smark/multica/quant-loop/workdir/framework_validate_run_20260719_1337.md`

## Result wire

`framework-validate hourly @ 2026-07-19 13:37+08 → W5 auto-archive (NOT-PROFITABLE — cost-fragile): vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717 / freqtrade; max_abs_rel_divergence_pct = 4302.88% (oos_sharpe 4302.88% / ann 406.64% / max_dd 269.50%) > W5 50% threshold; in-house terminal equity $131,040.5488 → framework terminal equity $0.17; 5,642 trades replayed across 79,296 30m bars; xs-pair 30m ETH/SOL V5-loose family confirmed cost-fragile (5th W5 auto-archive in family after iter#82 and iter#84 siblings).`
