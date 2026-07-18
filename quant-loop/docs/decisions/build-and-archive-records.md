# build & archive records — decision record

Strategy-push orchestrator cycles 41 → 49 (2026-07-08 → 2026-07-13) plus the framework-validation cluster from cycles 18/56/57/58. Scope: the V-series campaign lineage (V5 vol-breakout → V14 cross-exchange funding-arb), the surviving-strategy portfolio decision, the META CV-batch sign-off, the B3 backtest of `bb_reversion_rsi_1m_20260707`, the regression-test autopilot infra gap, the framework-sharpe-convention analysis on sparse-PnL strategies, and the framework-CV adapter pre-flight for V6+V7.

Strategy code lives in `quant-loop/strategies/<name>_<date>/`; campaign results in `quant-loop/results/<name>/`; framework-CV reports in `data/framework_adapter_report.json` per strategy.

**VERDICT: V5 family closed (vol-breakout, 2/7 gates); V8 funding-aware spec-rev2 kept the family alive but at no positive OOS Sharpe; V10–V13 momentum axis exhausted at iter#89 (4 variants, 0/4 ship); V14 cross-exchange funding-arb structurally blocked by max |Δfunding| ≤ 0.0007 (below every spec threshold). The portfolio decision recommends the 6 positive-ann% survivors with explicit correlation/redundancy rules. INFRA-MISSING (regression-test autopilot 2/3 missing) is unresolved and remains a coverage gap.**

## What worked

- **V8 SPEC-REV2 + BUILD-V8-rev2** (`vpvr_funding_aware_v1_20260711`, iter#82, multi-agent review cycle 57): dropped the 20-bar EMA trend filter after funding-rate-analyst independently showed BTC +108% → -30% reversal with the filter on; kept pure Rule A (long BTC-PERP + ETH-PERP when `funding_sum_24h_bps < 0`, skip SOL). Introduced `CarryLedger` infrastructure (per-event `PnL_carry += -position_units × funding_rate × mark_price`), half-open `[t-24h, t)` funding window, and the 2% cumulative-carry stop. Walk-forward extended 8 → 16 windows; bootstrap 95% CI (N=10,000, seed=42) on OOS Sharpe; FWER Bonferroni α=0.0125 across V5-V8. Framework CV gate (cycle-56 lesson) folded into the spec — both freqtrade AND backtrader OOS walk-forward Sharpe must clear 1.0. README/SPEC/SUMMARY all updated; B7 code-reviewer sign-off blocked on B3 outcome.

- **Bucket 2 MTM fix on `vpvr_reversion_1d_20260621/strategy.py`** (covered in `operations-decisions.md`): `mtm_equity: Optional[pd.Series] = None` param on `_summarize`; 33/33 tests pass; resolved 4 CV divergences cleanly. Caused by worker envs not seeing the parent strategy; orchestrator applied the patch directly.

- **V8 carry-stop rule** (from vpvr-specialist Q5): fires at -2% cumulative carry per-position. Distinct from price-stop (which fires at `low_4h < entry_price - 2.0 × ATR_4h_20_at_entry`). This separation is the architectural lesson: price-risk and carry-cost are different risk dimensions and need different stop semantics. Time-stop 60 4h bars (10 trading days) remains as the third exit.

- **Path A spec edit on dispatcher autopilot** (covered in `operations-decisions.md`): dispatcher pool 0 → 306 after dropping the `model != null AND model != "" AND archived_at == null` literal in the description column. The Go-side admission gate `server/internal/service/agent_ready.go:AgentReadiness` already checked `archived_at IS NULL + runtime_id IS NOT NULL + runtime status='online'` (no `model`) — so the literal filter was a duplicate admission gate that blocked every agent with empty `model`. Single-line description delta (2591 → 2606 chars); no daemon restart; no backfill; no quant-loop source touched.

- **Bucket 1 fill-timing deviation doc-only patch** (covered in `operations-decisions.md`): 8 strategies documented `[DEVIATION] fill-timing: framework open[t+1] vs in-house close[t]` in `SPEC.md` or `config.json:notes`. No code change; n_trades and win_rate unaffected; signal-identical.

- **`bb_reversion_rsi_1m_20260707` B3 backtest** (completed 2026-07-10): full B3 run produced the canonical metrics JSON used by the regression-test baseline (pinned reference under the ops homedir). Sharpe 32.6124 bit-identical to the last 4 regression-test runs; n_trades 6; maxDD -5.5e-06. This is the ONLY strategy that has consistently cleared the regression autopilot's smoke-test surface (1/3 of the original list — see INFRA-MISSING below). The 32.61 Sharpe is a per-trade annualization artifact on a 1m freq-trade-mean-reversion that fires ~6× per multi-year window; daily-resampled Sharpe is what the framework validators use.

## What failed and why

### V5 vol_breakout_4h_v1_20260711 (iter#79, post-V4 pivot to trend-following)

| stage | outcome | cause |
|---|---|---|
| SPEC (cycle 41) | Pivoted off xs_pairs family — overfit-prone (IS 7.87 / OOS 0.99 / wf_ratio 0.126). V5 hypothesis: trend-following on vol-expansion is a different bet. | 2/5 ship-gates PASS, 3/5 FAIL |
| BUILD B1+B2 (cycle 42) | `realized_vol(20)`, `vol_median(120)`, `vol_regime`, `range_high/low(20)`, vol-targeted sizing, `BARS_PER_YEAR_4h=2190`, 1 position max per symbol. 5 unit tests pass; 10 tests verify in cycle 43. | Implementation OK |
| B2.0 diagnose 0-trades bug (early cycle, separate from V5): STRATEGY_DEV_SPEC §10 diagnostic on `poc_ema_combo_1m_real_20260707` found a signal predicate where bar-level stats showed conjunction component #2 (`close < ema`) collapsed to ~0 over 3.47M 1m bars. Pipeline bug, not edge. Report-only, 3 fix hypotheses, no code change. | Lesson: 0 trades across 6.6y of 1m data is pipeline bug; diagnose before concluding "no edge" |
| B3 walk-forward (cycle 43) | 8 sequential non-overlapping windows, train 720 + test 168 + step 168 (≈30d train, 7d test). 17 OOS trades across 8 windows. Mean OOS Sharpe 2.8024 (95% CI 2.32–3.28). | Statistical power is near-zero at n=17 |
| ARCHIVE (cycle 47) | **NOT-PROFITABLE: 2/5 ship-gates PASS, 3/5 FAIL.** Annualized return +2.94% (binding gate — less than CPI ~3.4%/yr, far less than BTC buy-and-hold +135%). n_trades_total=17 < 100 legacy gate; per_symbol avg 5.67 < 25 legacy gate; framework CV never run (no freqtrade / backtrader adapter); max DD -5.38% with +2.94% annualized = 1.84y to MDD-recovery. | Annualized return is the binding gate, not Sharpe |

Per-cycle lesson: **the high Sharpe was on 17 trades and meaningless.** Standard error = `1/√17 = 0.24`. The 95% CI of OOS Sharpe is positive, but the trade-level edge estimate is statistically uninformative. Lessons from prior failed strategies (bb_reversion_rsi Sharpe 2.08 → -4.55 in freqtrade; vpvr_reversion_1m_kama Sharpe 0.83 → 0.32 in backtrader) confirm: in-house Sharpe ≠ framework Sharpe without CV. V5 family is closed per smark directive "don't waste time, no V7 on vol-following family". V5 is the LAST single-TF vol-breakout variant; V6 (multi-TF trend-following) and V7 (volume-edge) are different families.

### V10 momentum_trend_multi_tf_atr_scaled_1h_20260712 (iter#86) — pivot to trend-following

- **In-sample + 3/4 OOS windows showed real edge**: w0 +5.27, w2 +4.33, w3 +5.28 Sharpe; w1 **-14.77 catastrophic** (likely FTX/Luna crash regime where 4h EMA trend kept giving "long" signals while price fell sharply).
- **1h entry inside 4h trend = catching falling knives**. 4h EMA(50) too slow — by the time trend reads "down", position is already -2.5 ATR underwater. Stops at -2.5/-3.5 ATR don't help because the move is faster than the stop distance.
- Spec: 4h trend EMA(50) slope > 0; 1h RSI(14) cross 50 in trend direction; 1h ADX(14) > 20 confirmation; trailing stop at 2.5 × ATR(14); vol-scaled sizing.

### V11 momentum_trend_multi_tf_atr_scaled_v2_1h_20260712 (iter#87) — add regime filter + hard stop

- Two fixes on top of V10: regime filter (24h realized vol / 30d avg realized vol > 2.0 → skip entry) and hard stop at -2.5 ATR(14).
- **Result: 2/4 OOS positive, mean -0.90 (regression).** Regime filter calibration was wrong; V11 BTC alone Sharpe +0.42, V11 ETH alone Sharpe -0.10. ETH was the weak link.

### V12 momentum_trend_btc_only_softer_stop_1h_20260712 (iter#88) — drop ETH, soften stop

- Removed the regime filter (calibration wrong in V11), kept hard stop but softened -2.5 ATR → **-3.5 ATR** (give recovery more room), restricted instruments to BTCUSDT ONLY.
- Hypothesis: BTC alone is the alpha carrier; ETH is noise.
- Decision tree: w1 still < -2 but other windows > 5 → acceptable; any window < -5 → fail. Verdict: also failed momentum axis.

### V13 momentum_intraday_fast_15m_btc_20260712 (iter#89) — last momentum axis attempt at 15m

- Hypothesis: at 15m, 1h EMA(20) gives faster trend signal, 15m entries inside 1h trend = faster reaction, stops at -1.5 ATR (smaller move needed) reachable before catastrophic loss.
- Spec: 15m entry + 1h trend filter; 1h EMA(20) slope > 0 (long) or < 0 (short); 15m RSI(14) crosses 55 (long) or 45 (short) — slightly off 50 for momentum confirmation; 15m ADX(14) > 18 (lower threshold at 15m); hard stop at -1.5 ATR(14); BTCUSDT only.
- **V13 also fails — momentum axis genuinely dead in this regime.**
- Pivot policy per spec: pivot to (a) cross-exchange funding arbitrage (delta-neutral, no regime exposure) — which became V14; OR (b) accept that no single strategy passes the strict gate, focus on portfolio of weak-edge strategies.

### V14 funding_arb_binance_bybit_delta_neutral_20260713 (iter#90) — cross-exchange funding arb

- **Delta-neutral by construction** — long-spot-on-cheaper-exchange + short-perp-on-expensive-exchange. PnL = funding spread × time. No regime dependency.
- Live observed spread: Bybit 0.000048 vs Binance 0.000076 = 2.8 bps/8h ≈ 25%/year annualized gross.
- Spec: `|funding_spread| > 5 bps/8h` entry; exit on `< 2 bps`, 14-day force-close, sign-flip > 3 bps; 4 windows 1y train / 6m test; annualized_net ≥ 10% / wf_ratio ≥ 0.5 / min_oos_sharpe ≥ 0.
- **Final outcome (NOT-PROFITABLE, framework-CV surfaced)**: see `framework-sharpe-convention analysis` section below.

### Framework-sharpe-convention analysis: V14 sparse-PnL freqtrade divergence

| metric | ours | freqtrade | delta abs | abs div | within tol |
|---|---|---|---|---|---|
| sharpe | -0.957 | -1.191 | -0.234 | 24.5% | NO (>0.2 abs) |
| max_drawdown | -0.012 | -0.012 | +0.000 | 0.9% | YES (5% rel) |
| total_return | -0.012 | -0.012 | +0.000 | 0.0% | YES (5% rel) |
| n_trades | 39 | 39 | 0 | - | YES (±1) |
| win_rate | 0.179 | 0.179 | 0 | - | YES (5% abs) |

W5 auto-archive: NO (max abs-rel = 24.5% < 50% threshold).

**Root cause** (NOT a strategy bug, NOT a framework bug): in-house annualization is per-8h-bucket USD PnL × `sqrt(3 × 365.25) ≈ × 33.12`; freqtrade annualization is daily resample + pct_change × `sqrt(365) ≈ × 19.10`. Both are theoretically equivalent under IID daily-aggregated returns. **But funding-arb PnL is sparse** (39 trades / 4956 buckets = 0.8% trade density). The daily-resample path concentrates PnL into 39 daily bars out of 1656 calendar days, biasing std upward — inflates |sharpe| when PnL is negative (-0.957 → -1.191).

The other 4 metrics agree within tolerance (max_drawdown 0.9% rel, total_return 0.0% rel, n_trades ±0, win_rate ±0) — equity curve and trade log are correctly passed through.

**Three plausible paths** enumerated for review:

1. **Document the convention difference** in strategy SPEC; no code change. Lowest cost; leaves the framework-CV rule unenforced for sparse-PnL strategies.
2. **Add a tolerance carve-out** for sparse-PnL strategies (e.g. relax sharpe abs tol to 0.3 when trade-density < 5%). Codifies the convention difference but means framework-CV becomes a function of trade density.
3. **Tighten in-house annualization** to `sqrt(365)` for comparability with mainstream frameworks. Biggest code change but produces directly comparable sharpe; requires backtest re-run to verify other strategies don't regress.

This is the first time the cycle-45 framework-CV rule intersects with sparse-PnL metric convention mismatch. The rule was written assuming liquid-trade-density strategies. The cycle-46 cross-sectional pairs finding has been recorded separately in `vpvr-family.md`.

### INFRA-MISSING: regression-test autopilot 2/3 smoke-test strategies not on disk

- REGRESSION-TEST autopilot `58578d84-2918-480e-906e-3c8ff179c697` ran on 2026-07-12 and could not find 2 of 3 smoke-test strategies:
  - `vpvr_reversion_1m_nostop_20260630` (pinned at commit `1fddb05f845ef1bd454058a0dd2eaf01bc3bddd7`)
  - `london_open_breakout_15m_20260707` (same pinned commit)
- Both directories absent under `quant-loop/strategies/`; neither name appears in `git log --all --diff-filter=A --diff-filter=D`.
- `bb_reversion_rsi_1m_20260707` ran cleanly (Sharpe 32.6124 bit-identical to baseline).
- Likely root causes: (1) strategies never committed (working-tree only) and later pruned, OR (2) smoke-test list stale, should retarget at currently-shipped strategies.
- **Action needed from smark** (unresolved): restore the 2 strategies at pinned commits, OR retarget autopilot to 3 currently-present shipped strategies, OR drop to 1 strategy.
- Coverage gap: only 1/3 of the smoke-test surface is exercised each day. Until resolved, regressions in the un-tested strategy classes (vpvr_reversion_1m variants, london_open_breakout variants) cannot be caught by the autopilot.

### META BATCH: 21 CV issues needing smark sign-off (covered in `operations-decisions.md`)

The 21 cross-validation issues from the CV-triage campaign decomposed 14 unique CV divergences into 5 buckets:

- **Bucket 1 — fill-timing deviation** (8 issues): doc-only patch on `SPEC.md` or `config.json:notes`; signal-identical (n_trades, win_rate unaffected). Resolved.
- **Bucket 2 — `_summarize` intra-trade MTM** (4 issues): orchestrator applied `mtm_equity` array (every-bar mark) + optional `mtm_equity: Optional[pd.Series] = None` param on `_summarize`; 33/33 tests pass; 4 CV issues resolved. Resolved.
- **Bucket 3 — freqtrade VpvrVwapTrail_5m custom_exit dtype** (3 issues): Path B chosen (relax tolerance + document as `[DEVIATION] freqtrade custom_exit adapter`); framework-validator Agent Identity forbids editing `/tmp/framework-cache/freqtrade-*/`; actual adapter outside framework cache already uses `df.iloc[-1]` (line 255) — silent-`None` datetime pattern not present. Resolved via Bucket 2 supersede.
- **Bucket 4/5/6 — donchian + bb_reversion_rsi multi-framework** (6 issues): three buckets share one meta-root-cause (cost-noise dominance on 0.17–0.40% baseline) plus two distinct real bugs (backtrader SOL re-entry + freqtrade contract_precision). Resolution: relax `total_return` / `max_drawdown` tolerances 5% rel → 15% rel when in-house total_return < 1%; 1-line adapter guard for backtrader SOL re-entry cooldown; freqtrade stake fix `$1000 → $5000`. Resolved.

Full bucket-by-bucket disposition in `operations-decisions.md` "Daily smark-decision-cycle outcomes" and "Dispatch bucket triage outcomes" sections.

### Framework-CV adapter pre-flight for V6 + V7 (unblock B4-B6)

Two strategies in flight need framework CV at B4-B6:
- V6 `vpvr_vol_breakout_2tf_v1_20260711` (iter#80) — 4h coarse + 1h fine trend-following
- V7 `vpvr_volume_edge_3tf_v1_20260711` (iter#81) — 1m + 15m + 4h volume-edge

CV targets: freqtrade 2026.6 in `/tmp/framework-cache/freqtrade-*/` (cannot modify freqtrade code; only strategy data feeds) and backtrader 1.9.78.123. Fill convention must match (signal at `bar[t].close`, fill at `bar[t+1].open + 1bp/side`).

Deliverable: two freqtrade + two backtrader adapter stub files, ONE per strategy, that (1) map indicator output → framework-native signal events, (2) wire the same exit priority from the spec, (3) print `framework_in_house_oos_sharpe_X` and `framework_in_house_oos_sharpe_Y` for direct comparison, (4) output `data/framework_adapter_report.json` per strategy.

Pre-flight reason: V6 B3 walk-forward was running. When it landed, framework CV had to be ready to dispatch immediately — not "wait 1-2 hours for adapter". The CV step is mandatory per cycle-45 discipline. The existing `framework-validator` agent has handled 8+ prior CVs (bb_reversion_rsi, vpvr_reversion_1m_kama, etc.).

Out-of-scope: don't run the actual backtest yet (gated on V6 B3 / V7 data-fetch); don't modify framework source code.

### U5 dispatch: funding_carry ETH/SOL 1m real data (2026-07-18)

- Family: `funding_carry`, symbol: ETH and SOL (multi-symbol on the 1m axis), timeframe: 1m, status: never run with real data on SOL.
- Prior art to avoid duplicating: BTC/ETH 1m real funding-carry → Sharpe -147 / ann -3.78% (no carry edge at 1m); 1m OBI variant ran on synthetic (CCXT/Binance/OKX/Deribit blocked at runtime); BTC 15m funding threshold → STRUCTURAL FAIL (BTC 30d funding max = 0.0001); BTC-only cross-exchange funding delta 4h → STRUCTURAL FAIL.
- Gates G1–G7: OOS Sharpe ≥ 1.0 / ann ≥ 15% / maxDD ≤ 25% / PF ≥ 1.5 / framework CV OOS ≥ 1.0 / bootstrap CI lower > 0.5 / Bonferroni α=0.0125. Trade-floor: ≥ 30 trades OOS.
- Outcome: cancelled same day at 2026-07-18 15:04:36+08. Verdict not recorded (no comment posted). Aligned with the structural lesson from `vpvr-family.md` (iter#109–111) where the funding-carry-asym axis exhausted at the threshold level — observed funding max 0.0001 never crosses the 0.0003 gate.

## Portfolio decision: surviving-strategy correlation + recommendation

After cycle-46's 13 cancellations + cycle-47's V5 archive, **14 strategies are in active space**:

- **Positive ann%** (6): `vpvr_xs_pairs_4h_zscore_vpvr` (rank 1, but PF<1 bug — bug-class flag), `vpvr_reversion_1d_xs_idio`, V5 vol_breakout_4h (archived but data still valid for analysis), `xs_momentum_rank_1d`, `pairs_cointegration_1d`, `vpvr_reversion_1d_hvn_lvn_exit`.
- **Zero ann%** (5): `donchian_breakout_atr_1d`, `bb_reversion_rsi_1m`, `vpvr_reversion_15m_donchian_regime`, `vpvr_reversion_1m_kama_reversal`, V6 `vpvr_vol_breakout_2tf_v1` (in flight), V7 `vpvr_volume_edge_3tf_v1` (in flight).

Tasks delivered (per the portfolio correlation issue):

1. **Per-strategy metrics matrix** — sharpe, sortino, calmar, max DD, win rate, profit_factor, n_trades, total_return, annualized_return, period_years, with consistent definitions across all 14.
2. **Pairwise correlation matrix** of daily returns. Redundant pairs (corr > 0.7) — likely expressing the same edge; doesn't matter for portfolio combination. Anti-correlated pairs (corr < -0.2) — natural diversifiers. Mixed (0.2 < corr < 0.7) — diversification.
3. **Equal-weight portfolios** for (a) all 14, (b) top-5 by Sharpe, (c) top-5 by Sharpe + diversification filter, (d) anti-correlation-only. Report portfolio metrics.
4. **Bug-class detection** — any strategies with `profit_factor < 1` but positive Sharpe → flag as look-ahead or fill-convention bug. Already known: `vpvr_xs_pairs_4h_zscore` (PF=0.92, Sharpe 0.43). Surface others if any.
5. **Output file**: `quant-loop/strategies/PORTFOLIO_REPORT.md` with full results + recommendation (which 3-5 strategies to ship first as a coordinated portfolio).

**Why this matters**: the campaign net-zero problem isn't only that single strategies fail — it's that each one is tested in isolation. A combination of weak-but-diversified strategies may net better than one strong strategy. With 14 survivors, correlation analysis finds the natural portfolio.

## Backlog items kept (NOT deleted — real work, not documentation)

The following are **active implementation tasks**, kept on the worktracker as the cohort's "do not delete" carve-out:

- **Funding-rate fetcher** (Python script + CLI; pulls Binance funding history via REST; writes CSV/Parquet to `quant-loop/data/funding/`): shipped 2026-07-17. Endpoint `GET https://fapi.binance.com/fapi/v1/fundingRate`, forward-walking `startTime` cursor, 60ms inter-page sleep, 4^attempt backoff on 429/418, 8h ± 60min cadence validator, ≥95% coverage. Report `fetch_report_funding.json` produced; BTCUSDT/ETHUSDT/SOLUSDT refreshed.
- **Iceberg order detector prototype** (cluster BTC 1m trade sizes above a 3-sigma rolling baseline; reusable module/script for later VPVR confluence): shipped `trade_size_clusterer.py` + CLI `run_size_clusterer.py` + 13 tests + README; supersedes the prior `e431d11b` bar-aggregate prototype. Out of scope: backtest construction, multi-symbol orchestration.
- **Funding-rate oscillator** (normalize funding to z-score, identify >2σ extremes as signals): depends on the funding-rate fetcher. Pick the rolling window (24h or 7d) that makes the >2σ signal rate ≈ 1% of samples; for each signal record timestamp / symbol / funding value / z-score / next 4h/12h/24h price outcome. Save outputs under `quant-loop/strategies/funding-oscillator/`. Gate: report observed statistics only; downstream validation checks OOS Sharpe.
- **Funding-rate spread heatmap** (BTC vs ETH vs SOL, 1h intervals, last 30 days): reuses the fetcher; resamples funding to 1h buckets; computes per-pair spread series; renders heatmap (matplotlib or seaborn) + saves PNG + underlying CSV. Parent strategy: `vpvr-funding-carry-asym` (smark-approved). Timeframes strictly 1h per spec — NOT 1m/15m/4h.

Four short-form test/calibration items also retained (`test`, `test`, `test-via-user-uuid`, `test-resolve-smark-member-v3`) — these are member-resolution + smoke-test scaffolding that the dispatcher and regression-test autopilot still depend on; removing them would re-create the INFRA-MISSING regression-test gap.

## Open questions

1. **Framework-sharpe-convention carve-out for sparse-PnL strategies** — which of the three paths (document / tolerance carve-out / tighten in-house annualization) becomes canonical? Affects every strategy with n_trades < 5% trade density (funding-carry family primarily).
2. **INFRA-MISSING regression-test autopilot gap** — restore the 2 pinned-commit strategies, retarget the autopilot, or drop to 1 strategy? Until resolved, 2/3 of the regression surface is uncovered daily.
3. **V14 framework-CV disposition** — V14 is NOT-PROFITABLE on the convention-corrected number, but the cycle-45 framework-CV rule's intersection with sparse-PnL is unresolved. If path #1 (document only) is chosen, V14 stays archived on the in-house number; if path #3 (tighten in-house annualization) is chosen, V14 needs re-run.
4. **V8 carry-aware family forward path** — V8 SPEC-REV2 + BUILD-V8-rev2 is in code-reviewer sign-off (B7). If V8 also fails framework CV, the funding-aware family is exhausted at one rebuild (cycle-46).
5. **bb_reversion_rsi_1m_20260707 annualization caveat** — Sharpe 32.61 is per-trade artifact; daily-resampled Sharpe is what framework validators use. The high Sharpe-as-32 number should not be cited in portfolio decisions; the daily-resampled Sharpe is the comparable metric.
6. **U5 ETH/SOL 1m verdict** — task was dispatched 2026-07-18 15:04:18+08 and cancelled same day 15:04:36+08 with no comment posted. No metrics JSON, no verdict line emitted. If real data was the gating constraint, document that constraint explicitly (per the funding-data-blocked precedent at iter#107 funding-reset-window and the `xs_smart_routing_15m` bybit/okx klines absence) and KILL with that reason.
7. **V13 pivot policy execution** — V13 failed momentum axis. Spec said pivot to (a) cross-exchange funding arb (which became V14, also failed) or (b) portfolio of weak-edge strategies. (a) is exhausted at one rebuild; (b) is what the portfolio correlation analysis recommends.

---

Sources: `quant-loop/strategies/<name>_<date>/results/{summary,metrics,walk_forward_summary}.json`, campaign verdict tables 2026-07-08 → 2026-07-18, framework-CV reports `data/framework_adapter_report.json`, regression baseline (pinned reference under the ops homedir), daily smark-decision-cycle outcomes (already in `operations-decisions.md`). Iteration numbers (iter#NN) are the campaign counter and appear in branch names; they are NOT issue tracker references.