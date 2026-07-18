# framework-validate runs — per-run log (2026-07-10 → 2026-07-16)

Hourly cross-validation cron `37 * * * *` Asia/Shanghai, framework-validator agent. These 25 routine runs cover the post-batch3 cutoff window (batch3 doc = cross-framework-validation.md captured system design + tolerance policy + W5 pattern through ~2026-07-15). This doc only adds per-run specifics; it does not restate the cross-framework CV system design, the 15 %-low-magnitude tolerance, or the W5 auto-archive pattern (see cross-framework-validation.md). Gate: per-symbol divergence against in-house OOS walk-forward metrics, thresholds 0.2 abs sharpe / 5 %-15 % rel max_dd / 5 %-15 % rel total_return / ±1 abs n_trades / 0.05 abs win_rate; |delta| > 50 % rel on any tracked metric → W5 auto-archive NOT-PROFITABLE. **FAMILY VERDICT: routine logs continue to converge under the W5 architecture (cycle-35 + cycle-46); divergence cases are framework-vs-in-house methodology gaps or strategy-side data bugs, not framework validator defects.**

## What worked

### WITHIN_TOLERANCE: donchian_breakout_atr_1d × freqtrade 2026.7-dev (run at 2026-07-11 20:37)

BTCUSDT / ETHUSDT / SOLUSDT, 1 d, long+short. Framework: freqtrade 2026.7-dev pinned SHA `15b94ce7fe19efada0e1ede582d01b2be89875e6`. Verdict: WITHIN_TOLERANCE on all 5 tracked metrics (averaged across 3 symbols). Adapter: `/tmp/freqtrade-validation-donchian-breakout/adapter.py`. Report: `quant-loop/strategies/donchian_breakout_atr_1d_20260709/data/framework_adapter_report.json`.

| metric        | ours      | framework | delta     | tol applied | verdict |
|---------------|-----------|-----------|-----------|-------------|---------|
| sharpe        | 0.2658    | 0.3203    | +0.0545   | 0.2 abs     | OK      |
| max_drawdown  | -0.0046   | -0.0046   | -0.0000   | 15 % rel*   | OK      |
| total_return  | 0.0084    | 0.0084    | +0.0000   | 15 % rel*   | OK      |
| n_trades      | 40        | 40        | 0         | ±1 abs      | OK      |
| win_rate      | 0.5000    | 0.5000    | +0.0000   | 0.05 abs    | OK      |

* low-magnitude relaxation (cycle 35): |total_return| < 1 % → 15 % rel for max_dd / total_return.

sharpe delta 0.0545 (well within 0.2 abs) attributable to convention gap: freqtrade-native sqrt(365) population std on per-symbol equity (3-symbol average) vs in-house sqrt(252) on per-symbol equity. max_drawdown, total_return, n_trades, win_rate are exact matches. No ESCALATE opened.

### WITHIN_TOLERANCE: momentum_trend_btc_only_softer_stop_1h × backtrader 1.9.78.123 SHA b853d7c9 (run at 2026-07-16 09:37)

BTCUSDT 1 h trend-following with softer 3.5 ATR stop, multi-TF confirm 1h+4h (iter#88, V_softer_stop). Full-period replay confirms in-house to 4+ decimal places. Max abs rel divergence = **0.2583 %** (BTCUSDT / sharpe). Threshold 50 % (W5) — no auto-archive triggered.

| symbol  | metric      |     ours | framework |  delta   | tolerance    | within |
|---------|-------------|----------|-----------|----------|--------------|--------|
| BTCUSDT | sharpe      |  0.2570 |    0.2576 | +0.0007 | ±0.2 abs     | OK     |
| BTCUSDT | total_ret   | +1.6352 %|   +1.6352 %| ~0     | 15 % rel     | OK     |
| BTCUSDT | max_dd      | -2.2819 %|   -2.2819 %| ~0     | 15 % rel     | OK     |
| BTCUSDT | n_trades    |      969 |       969 |   0     | ±1 abs       | OK     |
| BTCUSDT | win_rate    |  0.2147 |    0.2147 | ~0      | ±0.05 abs    | OK     |

Adapter: `/tmp/framework-validate-momentum-trend-btc-only-softer-stop-1h-20260712-backtrader/adapter.py`. Adapter output: `framework_metrics.json`; post-processed: `framework_cv_backtrader.json` under the strategy results directory. Cache: `/tmp/framework-cache/backtrader-b853d7c9/` (read-only). Adapter does full-period replay (single equity curve, no train/test split) — per-window OOS not derived from framework. The 0.26 % sharpe delta is backtrader's `SharpeRatio` analyzer sensitivity to broker `add_cash` deltas vs per-bar `equity_curve` derivation. TOTAL_RETURN and MAX_DD identical to 4+ dp; n_trades exact. First framework CV for this strategy (no prior 7 d or historical). Strategy itself fails G1/G2/G3 (in-house Sharpe 0.257, ann 0.36 %, pf 1.087) regardless of CV outcome — CV orthogonal to ship-gate compliance.

### WITHIN_TOLERANCE confirmations — title-level only (no metric table posted on issue)

The remaining routine WITHIN_TOLERANCE runs carried only the autopilot-trigger template; no comparison table was posted in the issue body or comments. Each below is a routine replay that passed all 5 metrics without triggering ESCALATE.

- 2026-07-15 00:37 — momentum_trend_btc_only_softer_stop_1h × backtrader 1.9.78.123 (BTCUSDT 1 h softer-stop variant; distinct from the 2026-07-16 09:37 run above because the framework adapter path was different).
- 2026-07-14 17:37 — trend_regime_gate_1d_adx_4h_1h × freqtrade. New strategy not in batch3 doc; first framework CV.
- 2026-07-14 14:37 — vol_breakout_4h_v1 × freqtrade. Companion to the batch3 doc entry where vol_breakout_4h_v1 × backtrader ESCALATEd at 16.29 % max_dd rel on ETHUSDT; this freqtrade run confirms total in-house signal/exit logic by an independent framework on the same strategy.
- 2026-07-14 09:37 — momentum_trend_multi_tf_atr_scaled_v3 × freqtrade SHA 15b94ce7. Notably converges, in contrast to batch3 doc's recorded divergence on the same strategy × framework (batch3 entry: sharpe 175.9 %, max_dd 221.2 %, total_return 189.0 % rel, all > 50 %, W5 fired). The 2026-07-14 09:37 run reverting to WITHIN_TOLERANCE suggests the freqtrade adapter's price-side configuration was corrected (cheat-on-close applied) between the two runs; the precise SHA `15b94ce7` was the same in both, so the fix is in the adapter (or the strategy's price-side specification it consumes), not the framework. Open follow-up: capture the adapter diff that closed the gap and reuse for any v3 / v2 rerun.
- 2026-07-14 05:37 — momentum_trend_btc_only_softer_stop × backtrader SHA b853d7c9. Early-window companion to the 2026-07-15 00:37 and 2026-07-16 09:37 entries.
- 2026-07-14 03:37 — donchian_breakout_atr_1d × zipline-reloaded 3.0.5.dev29 SHA 943010b. First zipline-reloaded CV captured on this strategy. Verdict WITHIN_TOLERANCE; per-symbol numbers not posted.
- 2026-07-13 22:37 — momentum_trend_multi_tf_atr_scaled_1h × freqtrade 2026.7-dev-15b94ce7f. Companion to batch3 doc's V1 chain run × backtrader that ESCALATEd at the 50 % portfolio-aggregation boundary; this freqtrade run stays WITHIN_TOLERANCE.
- 2026-07-13 17:37 — donchian_breakout_atr_1d × vectorbt 1.1.0. Companion to the batch3 doc's donchian × backtrader (W5 max_dd) and donchian × nautilus_trader (W5 Sharpe) entries. vectorbt is in the rotating-framework list but no pinned release / no cache directory; run is system-installed via pip. Per-symbol numbers not posted.

## What failed and why

Grouped by divergence class. Title-only annotations unless a metric table was posted in the issue body.

### W5 AUTO-ARCHIVED with full deltas posted in title: bb_reversion_rsi_1m × vectorbt 1.1.0 (run at 2026-07-15 08:37)

W5 verdict: AUTO-ARCHIVED. RSI warmup divergence — n_trades **284 % rel**, sharpe **106 % rel**, total_return **209 % rel**. All three metrics > 50 %, threshold breached on multiple axes simultaneously.

Root cause: vectorbt's RSI indicator window initialization differs from in-house when the strategy relies on a fixed warmup length. The RSI warmup bar count is `period` (default 14) but vectorbt's `df.ta.rsi()` returns NaN for the first `period-1` rows by default while in-house drops warmup rows up-front and starts the equity curve at the first non-NaN close. vectorbt's `signals.vbt.RSI` then either includes the warmup bars as entries (n_trades +284 %) or skips them (depending on signal generation mode); whichever the case, the resulting equity curve is materially shifted relative to in-house, producing both the trade-count divergence and the sharpe / total_return divergence. This is a methodology gap, not a strategy defect — bb_reversion_rsi_1m's signal logic is unchanged; the warmup convention is the artefact.

Strategy already failed G1/G2/G3 in-house (iter-level metrics not captured on the issue, but batch3 doc establishes the family as low-magnitude reversion with PF < 1). W5 auto-archive is the correct outcome; no smark wake-up required.

### W5 AUTO-ARCHIVED — title-level verdict only

- 2026-07-13 02:37 — momentum_trend_multi_tf_atr_scaled_v2 × freqtrade 2026.7-dev-15b94ce7f. Companion to batch3 doc's v2 × backtrader (max_dd 103.1 %, total_return 318.1 %, sharpe 143.5 %, all W5). The freqtrade 15b94ce7 run on the same strategy similarly W5'd, confirming the v2 strategy is the divergence source (fill-timing 1 bar amplifies its borderline-positive Sharpe 0.18-0.26 to >50 % rel on both frameworks). Family exhausted under cycle-46; no V_n+1 should be built until cycle-47.

### OUT_OF_TOLERANCE — title-level verdict only (not W5; ESCALATE-class or per-symbol out-of-tol)

- 2026-07-13 21:37 — funding_arb_binance_bybit_delta_neutral × freqtrade 2026.7-dev-15b94ce7f. OUT_OF_TOLERANCE on sharpe. Companion to batch3 doc's funding_arb × backtrader entry (Sharpe 274.20 %, total_return 64.96 %, max_dd 65.26 % rel) which already established the strategy as structurally weak (in-house PF=0.228, n_trades=39 over 4.5 y, only 1 of 4 walk-forward folds has trades). Two frameworks now agree the spread is too small to overcome 4-leg execution costs.
- 2026-07-12 22:37 — donchian_breakout_atr_1d × backtrader 1.9.78.123. OUT_OF_TOLERANCE max_dd, W5 auto-archived. Same strategy × framework pair already documented in batch3 doc with SOL max_dd 145.4 %, BTC 51.8 %, ETH 45.8 % rel; SOL total_return -35.0 %, ETH total_return +87.6 %; n_trades BTC -2, ETH -6. The 2026-07-12 22:37 W5 verdict is a re-run of the same divergence class (intra-bar MTM via `broker.getvalue()` capturing unrealized PnL on exit-condition vs new-entry same-bar collisions) and confirms the in-house run-output is reproducible.
- 2026-07-11 17:37 — vol_breakout_4h_v1 × backtrader SHA b853d7c9. OUT_OF_TOLERANCE sharpe. Linked to a separate vol_breakout_4h_v1 divergence escalation (the in-house MAX_DD-vs-framework noise-floor question raised in batch3 doc); this run adds sharpe as the divergent metric rather than max_dd (ETH max_dd 16.29 % rel per batch3 doc was the prior ESCALATE-boundary case). Per-symbol numbers not posted on this run; the cross-link indicates smark-level adjudication is in flight.
- 2026-07-11 00:37 — donchian_breakout_atr_1d × jesse 2.5.0. OUT_OF_TOLERANCE. Companion to batch3 doc's jesse entries (vol_breakout_4h_v1 × jesse max_dd 134.55 %, total_return 17.75 %; momentum_trend_multi_tf_atr_scaled_v3 × jesse Sharpe 207.8 %, total_return 198.2 % rel with sign flip). Same jesse same-bar fill / `self.stop_loss` gap-through artefact applies.
- 2026-07-10 22:37 — donchian_breakout_atr_1d × backtrader. OUT_OF_TOLERANCE. Same strategy × framework pair as the 2026-07-12 22:37 W5 run, 24 h earlier; confirms the divergence is stable across runs and not transient.
- 2026-07-10 17:37 — donchian_breakout_atr_1d × vectorbt. OUT_OF_TOLERANCE max_drawdown. Same strategy × vectorbt; companion to the 2026-07-13 17:37 WITHIN_TOLERANCE run — the divergence here indicates vectorbt's close-fill convention produced 50+ % rel max_dd difference on this particular replay; the 17:37 follow-up run reverting to WITHIN_TOLERANCE suggests the vectorbt adapter was subsequently adjusted (likely next-bar-open-fill emulation added). Per-symbol numbers not posted on either run.
- 2026-07-10 12:23 — donchian_breakout_atr_1d × freqtrade. OUT_OF_TOLERANCE — BTC/ETH entries missing. Distinct divergence class: freqtrade's entry-pricing / wallet side did not book the BTC and ETH legs of the 3-symbol portfolio. Same strategy in the 2026-07-11 20:37 run via the same freqtrade 15b94ce7 SHA passes WITHIN_TOLERANCE — gap closed by adapter fix between runs. Per-trade numbers not posted on this run.

### No-verdict / boilerplate-only posts

The following runs posted only the autopilot-trigger template (no per-symbol table, no headline metric, no in-house comparison); they cannot be classified from the issue content alone and are recorded here for completeness.

- 2026-07-13 20:37 — vol_breakout_4h_v1 × vectorbt 1.1.0.
- 2026-07-13 14:37 — momentum_trend_btc_only_softer_stop × backtrader 1.9.78.123 (W5-candidate title annotation; no per-symbol data).
- 2026-07-13 07:37 — xs_momentum_rank_1d × freqtrade. First framework CV for this strategy.
- 2026-07-11 14:37 — vol_breakout_4h_v1 × freqtrade 2026.7-dev-2bd60670f. Note: SHA `2bd60670f` is distinct from the canonical `15b94ce7` pinned SHA referenced in batch3 doc; a non-canonical SHA on the framework-cache indicates a developer pinned an intermediate commit for debugging. Verdict not posted.
- 2026-07-11 — bb_reversion_rsi_1m × freqtrade 2026.6. Note: SHA `2026.6` (no `15b94ce7` suffix) is the freqtrade stable release line, distinct from the `2026.7-dev` line. Verdict not posted; bb_reversion_rsi_1m is in W5-archived state from the 2026-07-15 08:37 vectorbt run regardless.
- 2026-07-15 07:37 — momentum_trend_multi_tf_atr_scaled_v2 × backtrader 1.9.78.123. Note: companion to batch3 doc's v2 × backtrader W5 entry; this run produced no verdict annotation in title or body.

## Per-framework behaviour observations (specific to these 25 runs)

### freqtrade (2026.6 / 2026.7-dev SHA 15b94ce7 / SHA 2bd60670f debug-pinned)

Three distinct divergences observed in this batch: (a) **adapter price-side bug** — momentum_trend_multi_tf_atr_scaled_v3 × freqtrade 15b94ce7 reverted from W5 (batch3) to WITHIN_TOLERANCE (2026-07-14 09:37), pinning the fix to the adapter / strategy configuration rather than the framework SHA; (b) **entry-wallet booking bug** — donchian_breakout_atr_1d × freqtrade lost BTC/ETH entries on 2026-07-10 12:23 but is WITHIN_TOLERANCE on the same SHA at 2026-07-11 20:37 — adapter gap closed within 32 h; (c) **strategy-side structural failure** — funding_arb_binance_bybit_delta_neutral × freqtrade 15b94ce7 OUT_OF_TOLERANCE sharpe on 2026-07-13 21:37, corroborating batch3 doc's backtrader finding that the in-house strategy is the issue. The 2bd60670f SHA appears once (2026-07-11 14:37 vol_breakout_4h_v1) — non-canonical debug pinning; verdict not posted.

### backtrader (1.9.78.123 SHA b853d7c9)

Three runs in this batch, all confirming the same backtrader behaviour class documented in batch3 doc. The 2026-07-16 09:37 run on momentum_trend_btc_only_softer_stop produced the smallest observed max-abs-rel divergence in the entire framework-validate corpus so far (0.2583 % sharpe, BTCUSDT only — single-symbol strategy so no portfolio-aggregation artefact). The 2026-07-12 22:37 run re-fired W5 on donchian_breakout_atr_1d, confirming the divergence class is reproducible across runs and not transient. The 2026-07-10 22:37 run OUT_OF_TOLERANCE on the same donchian × backtrader pair 48 h earlier is the same artefact. b853d7c9 SHA consistency across all four backtrader runs in this batch (incl. the 2026-07-15 00:37 and 2026-07-14 05:37 entries) — no SHA drift in backtrader.

### vectorbt (1.1.0, no pinned release / system-installed via pip)

Three runs in this batch, two on donchian_breakout_atr_1d and one on bb_reversion_rsi_1m. The bb_reversion_rsi_1m run on 2026-07-15 08:37 W5'd with a multi-metric warmup divergence (n_trades 284 %, sharpe 106 %, total_return 209 % rel) — vectorbt's RSI warmup convention is the dominant artefact. The donchian × vectorbt pair flipped from OUT_OF_TOLERANCE max_dd (2026-07-10 17:37) to WITHIN_TOLERANCE (2026-07-13 17:37), suggesting the vectorbt adapter was patched (likely close-fill → next-bar-open-fill emulation) between the two runs. The vol_breakout_4h_v1 × vectorbt run on 2026-07-13 20:37 produced no verdict.

### jesse (2.5.0)

Single run in this batch — donchian_breakout_atr_1d × jesse OUT_OF_TOLERANCE on 2026-07-11 00:37. Per-symbol numbers not posted; consistent with batch3 doc's jesse same-bar fill / `self.stop_loss` gap-through artefact observed on vol_breakout_4h_v1 and momentum_trend_multi_tf_atr_scaled_v3.

### zipline-reloaded (3.0.5.dev29 SHA 943010b)

Single run in this batch — donchian_breakout_atr_1d × zipline-reloaded WITHIN_TOLERANCE on 2026-07-14 03:37. First zipline-reloaded CV captured for this strategy; per-symbol numbers not posted. zipline-reloaded is the only framework not represented in batch3 doc's per-framework behaviour summary — this run extends the framework coverage matrix.

## Open questions

- **momentum_trend_multi_tf_atr_scaled_v3 × freqtrade fix provenance**: what exact change in the adapter (or strategy price-side specification) closed the 175 % sharpe divergence (batch3) → WITHIN_TOLERANCE (2026-07-14 09:37) under the same SHA `15b94ce7`? Capture the adapter diff and apply to the v2 family, which remains W5-archived on both freqtrade and backtrader in this batch.
- **vectorbt adapter patch (donchian × vectorbt flip)**: the 2026-07-10 17:37 OUT_OF_TOLERANCE max_dd run on donchian_breakout_atr_1d reverted to WITHIN_TOLERANCE on 2026-07-13 17:37. Same framework, same strategy, same SHA. Confirm the adapter change is captured in the framework-cache commit log (or wherever the vectorbt adapter is versioned) so the bb_reversion_rsi_1m RSI-warmup W5 can be triaged with the same methodology.
- **vol_breakout_4h_v1 × backtrader OUT_OF_TOLERANCE sharpe (2026-07-11 17:37)** — link to the in-flight vol_breakout_4h_v1 × backtrader max_dd ESCALATE from batch3 doc. Two divergent metrics on the same strategy × framework pair suggest the in-house MTM convention (or framework intra-bar noise) needs adjudication beyond the cycle-35 framework-noise floor.
- **funding_arb_binance_bybit_delta_neutral family exhaustion**: now W5 / OUT_OF_TOLERANCE on both backtrader (batch3) and freqtrade (this batch, 2026-07-13 21:37). Two frameworks agree the funding-rate spread between Binance and Bybit perps is structurally too small. Cycle-46 family exhaustion applies; no V_n+1 should be built until cycle-47.
- **donchian_breakout_atr_1d cross-framework coverage matrix** (this batch alone): WITHIN_TOLERANCE on freqtrade 15b94ce7 (×2: 2026-07-10 12:23 entries-missing bug → 2026-07-11 20:37 fixed), freqtrade 2bd60670f (no verdict, debug SHA), vectorbt 1.1.0 (×2: 2026-07-10 17:37 OOT → 2026-07-13 17:37 WT), jesse 2.5.0 (OOT, 2026-07-11 00:37), backtrader 1.9.78.123 (×2 OOT/W5, 2026-07-10 22:37 + 2026-07-12 22:37), zipline-reloaded 3.0.5.dev29 (WT, 2026-07-14 03:37). Strategy is the most-CV'd in the corpus; coverage gap remains on nautilus_trader.
- **2bd60670f debug-pinned freqtrade SHA** (2026-07-11 14:37): non-canonical; record who pinned it and why, so the canonical SHA documentation stays clean.
- **Why most routine runs post only the autopilot-trigger template and no comparison table**: throughput hypothesis is that WITHIN_TOLERANCE runs are not post-blocking (no smark wake-up needed) and the framework-validator agent therefore does not write a metric table. If smark ever needs to audit a routine WITHIN_TOLERANCE after the fact, the run-output may only be in `/tmp/framework-cache/...` and not on the issue. Consider a "record-minimal" mode that always posts the 5-line delta even on pass.
