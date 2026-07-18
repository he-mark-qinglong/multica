# VPVR family — decision record

Volume-Profile (VPVR) anchored mean-reversion / confluence strategies. Active 2026-06-21 → 2026-07-18, 35+ shipped-and-archived variants across the campaign sweeps below (iter#52 → iter#111). Compiled 2026-07-18.

Gate throughout: OOS walk-forward Sharpe ≥ 1.0 (daily-resampled, √365) AND annualized ≥ 15%, profit_factor > 1.5, maxDD < 25%, plus second-framework cross-validation (CV) on OOS. Data: Binance USD-M klines (BTC/ETH/SOL), 1m source 2024-04-23 → 2026-06-23 plus later windows; funding history 90d–4y depending on variant.

**Family verdict: no variant passed the gate. Family declared exhausted 2026-07-15 (cycle-46 rule) and archived.** One variant's archival is conditional on a maxDD audit — see Open questions. Strategy code: `quant-loop/strategies/vpvr_*/`; merged to main 2026-07-10 (merge commit `43f24005e`, 8 dirs); iter#65–69 branch `feat/vpvr-variant-sweep-iter65-69-20260709` at commit `3a869189`.

## What worked

Nothing passed the gate. The following produced real signal but failed on a secondary dimension — they are the family's reusable findings:

- **funding-aware long-bias 4h** (`vpvr_funding_aware_v1_20260711`, iter#82): funding_sum_24h > 0 regime filter, long-only, BTC+ETH 4h. In-sample aggregate Sharpe 7.16 (freqtrade 5.83), total return 144.3% (131.9%), ann 21.1%, 165 trades, maxDD -57.1%. The ONLY variant with cross-framework-consistent Sharpe and returns (CV div 18.6% / 8.6%). Archived solely on maxDD divergence (-57% vs ~0, div 100%) — DD computation suspect, audit pending. Independently failed walk-forward OOS 2/4 windows.
- **xs-pairs 30m + funding filter** (`vpvr_xs_pairs_30m_funding_filter_20260712`): BTC/SOL z-score pair, VPVR confluence, funding filter. OOS mean Sharpe 0.99 with 3/3 walk-forward windows positive — real but weak alpha. In-sample Sharpe 7.87 → wf_ratio 0.126, overfit gate FAIL. Regularized rebuild (zscore_entry 2.0→2.5, lookback 96→192, max_hold 48→96, funding 0.0005→0.0003) still failed (IS 5.10, deflated Sharpe z -0.36). Lesson directly motivated the multi-TF (1m/15m/2h) campaign design outside this family.
- **funding-delta 1h** (`vpvr_funding_delta_1h_20260711`, iter#86): only pf > 1 of its sweep (Sharpe 0.05, pf 1.018, WR 51.9%, 291 trades). 89% of exits were vol_breach, 8% profit_lock — signal real, execution far too conservative. All 3 execution rebuilds (asym / mtf / pair, iter#87) NOT-PROFITABLE → axis exhausted.
- Near-misses rejected on ship-gate where the signal was not at fault: liquidation-cascade 15m ETH leg (Sharpe 0.35, pf 1.43, iter#84); options IV-skew 1d BTC leg (Sharpe 0.42, pf 1.18, iter#91).

## What failed and why

### 1d era (iter#52–64, 2026-06-21 → 2026-07-11) — timeframe banned

- `vpvr_reversion_1d_20260621` (iter#52): Sharpe@10bps 0.23, PF 1.71, maxDD -7.2% — below gate.
- 9 parameter overlays (kama_er, er_holdfilter, poc_stability, vol_filter, lvn_filter, xs_idio, dd_sizing, decay_exit, hvn_lvn_exit): audit Sharpes 0.35–0.51 on n=3–8 trades each — under the n>100 sample rule; individually inconclusive, collectively no lift over baseline.
- bb_overlay: repeated framework-CV OUT_OF_TOLERANCE escalations; root-caused to fill-timing convention (next-bar-open) and intra-trade MTM vs realized equity accounting — convention gaps, not alpha.
- iter#56 1m rebuild (`vpvr_reversion_1m_window14784_20260629`): source never committed; the original "Sharpe 90.78" was demo data. Lesson: no commit = no evidence.
- 2026-07-11 user directive: 13 strategies with ann < 0 archived in one batch; **1d timeframe banned**. Family pivoted to 1m/15m/4h.

### iter#65–69 (2026-07-09, commit `3a869189`) — 5/5 NOT-PROFITABLE

| variant | TF | entry | exit | symbols | n | Sharpe | maxDD% |
|---|---|---|---|---|---|---|---|
| V1 `vpvr_reversion_5m_vwap_trail_20260709` | 5m | VWAP rejection + vol spike | ATR trail 3x | BTC+ETH | 6536 | -0.82 | -1.58 |
| V2 `vpvr_reversion_15m_donchian_regime_20260709` | 15m | Donchian + ADX | vol-aware regime switch | BTC | 960 | +0.10 | -0.32 |
| V3 `vpvr_reversion_1m_kama_reversal_20260709` | 1m | KAMA reversal + RSI div | time-stop 60 bars | BTC | 4632 | +0.83 | — |
| V4 `vpvr_xs_reversion_1d_momentum_filter_20260709` | 1d | xs momentum filter | vol-target | BTC+ETH+SOL | 87 | -0.39 | -9.15 |
| V5 `vpvr_reversion_1m_volume_profile_break_20260709` | 1m | HVN breakout | trail + profit-lock | BTC | 26659 | ≈-1.3 | -1.89 |

V3 passed the old ≥0.5 bar ("legacy-PASS") on the full-window BTC rerun (the original SOL run was sparse, n=2) but is below the current OOS ≥ 1.0 gate — do not promote; second-framework CV also degraded it (0.83 in-house → 0.32 backtrader). V4 killed with the 1d ban. Root-cause review before iter#70: all five lost on different axes (TF / entry / exit / universe), so random axis rotation had negative expected value.

### iter#70–72 (2026-07-10, merged to main `43f24005e`) — sweep NOT-PROFITABLE

- `vpvr_micro_reversion_1h_funding_filter_20260710`: Sharpe 0.29, ann ≈ 0 — funding filter starves trades.
- `vpvr_mtf_reversion_5m_consensus_20260710`: in-house Sharpe -0.51 (n=3436); later framework CV div 1708% → auto-archived as unreproducible.
- `vpvr_regime_reversion_4h_vol_switch_20260710`: NOT-PROFITABLE.
- iter#72+ (2026-07-11, cross-asset / funding-regime / on-chain axes): `vpvr_funding_regime_15m_20260711` Sharpe -3.81, pf 0.71 (4200 trades — overtrades, later CV div 233%); `vpvr_xs_leadlag_5m_20260711` pf 0.71; `vpvr_onchain_proxy_1h_20260711` pf 0.84, n=30. 3/3 NOT-PROFITABLE, campaign closed.

### iter#73–83 (2026-07-11..12) — funding + xs-pairs axes

- iter#75 `vpvr_xs_pairs_4h_zscore_vpvr_20260710`: Sharpe +0.33, pf 0.905, n=1323, maxDD -12.6%; walk-forward mean test Sharpe -0.416, wf_ratio -1.245 → ship-gate FAIL.
- iter#80/81 `vpvr_vol_breakout_2tf_v1_20260711` + `vpvr_volume_edge_3tf_v1_20260711`: in-house Sharpe 16.37 exposed as per-trade annualization artifact (√8766 over 71 trades) vs freqtrade daily-resampled 0.76 → annualization convention fixed house-wide; variants below gate under either convention (volume_edge 8-window WF FAIL, Sharpe -0.26).
- iter#82 `vpvr_funding_aware_v1_20260711`: see What worked + Open questions.
- iter#83 `vpvr_xs_pairs_mr_1m_v1_20260711`: 16-window walk-forward gate FAIL. xs_pairs_30m line: see What worked.

### iter#84–94 (2026-07-11..14) — microstructure + funding-delta

- iter#84 `vpvr_liquidation_cascade_15m_20260711`: NOT-PROFITABLE (ETH leg near-miss above). `vol_breakout_2tf_vpvr_confluence_4h`: Sharpe 0.38/0.38/-0.54 (BTC/ETH/SOL), portfolio +2.14% — consistent across 3 frameworks but below gate → consistency ≠ profit.
- iter#85 `vpvr_orderbook_imbalance_5m_20260711`: Sharpe -13.29, pf 0.21.
- iter#86–87 funding-delta: see What worked. V_asym rebuild improved returns 4x but Sharpe 0.10 / ann 0.012% → axis exhausted.
- iter#90–92: `vpvr_funding_asym_4h_20260713` in-house Sharpe -0.22 vs freqtrade +4.19, total-return div 44730% → implementation broken, auto-archived. `vpvr_oi_divergence_4h_20260713`: NOT-PROFITABLE. `vpvr_options_iv_skew_1d_20260713`: near-miss above.
- iter#94: `vpvr_regime_blend_4h_20260714`, `vpvr_obi_micro_v2_1m_20260714`, `vpvr_mtf_consensus_v2_4h_20260714` — 3/3 NOT-PROFITABLE under the full G1–G5 gate.

### iter#97–108 (2026-07-14..15) — term-structure / calendar / routing

- iter#97 `vpvr_funding_term_curve_1h_20260714`: Sharpe -0.98, n=1314 — steady bleed. `vpvr_liquidation_heatmap_15m_20260714`, `vpvr_xs_corr_breakdown_4h_20260714`: NOT-PROFITABLE.
- iter#103 `vpvr_options_iv_termstructure_4h_20260715`: Deribit data absent → realized-vol proxy; Sharpe -0.20, PF 0.84, n=59 → FAIL on proxy data.
- iter#104 `vpvr_options_putcall_oi_pressure_8h_20260715`: PCR is a taker-buy-share proxy, not real OI; Sharpe -0.67, PF 0.72, n=98 → FAIL on proxy data.
- iter#105 `vpvr_xs_smart_routing_15m_20260715`: 2772 trades overtrading, PF 0.57, Sharpe -4.43; bybit/okx klines absent.
- iter#106 `vpvr_macro_calendar_4h_20260715`: framework-CV Sharpe div 930% → in-house result unreproducible → KILL.
- iter#107 `vpvr_tod_session_filter_15m_20260715`: NOT-PROFITABLE.
- iter#108 `vpvr_funding_reset_window_1h_20260715`: framework Sharpe ≈ 0 vs in-house -1.16 (div ~100%) → unreproducible → KILL.

### iter#109–111 + 2026-07-17 follow-ups — funding-carry-asym (fresh axis after exhaustion)

Mechanism: funding carry + term-curve inversion + regime overlay; enter long when funding > threshold at VPVR HVN support, asymmetric execution. Rationale: funding is the carry-bearing flow (BTC +6.6%/yr, ETH +6.0%/yr, ACF(1)=0.78 over 4y) and sticky (median same-sign run 2 events/16h).

- 15m backtest (BTC 2026-06-10→07-10, 2881 bars): **0 trades** — observed funding max 0.0001 < 0.0003 gate; gate never crossed.
- 15m hot window: 63 trades, Sharpe -1.52, ann -0.09%, PF 0.59 → TP/SL mis-specified; variants paused.
- 1m OBI variant (2026-07-17, real data): 38 trades, 28.9% win, Sharpe ≈ -147 (artifact scale) → no carry edge at 1m.
- 15m term-spread (2026-07-17, real data): 345 trades steady bleed, Sharpe -47.4, ann -46.1%.
- funding-carry + options (2026-07-17, 3 variants): exchange options endpoints blocked on the runtime → synthetic data only, logic-only runs (Sharpe -5.9 / -34.3 / -8.6) — no alpha verdict possible; data constraint must be fixed first.
- cross-exchange funding-delta 4h (BTC/ETH/SOL): multi-symbol Sharpe -1.42, ann -28.6%. BTC-only leg looked strong (Sharpe 6.65, ann 22.8%) but the formal BTC-only test (2026-07-18, Binance×Bybit 1650d overlap) found max |Δfunding| = 0.000707 — below every spec threshold (0.0005/0.001/0.0015) → 0 entries on OOS; 3-framework agreement; 1bp sensitivity still negative (Sharpe -1.63). Structural exhaustion — the signal simply never fires.

### Framework-CV (W5) auto-archive pattern

Rule: on OOS walk-forward, if any of Sharpe / total_return / maxDD diverges > 50% abs between in-house and a second framework → auto-archive as unreproducible (no escalation). 17 family validation runs ended this way. Two distinct causes, worth separating:

1. **Broken implementation** — divergence on direction/magnitude of returns: funding_asym_4h (44730%), mtf_reversion_5m (1708%), funding_regime_15m (233%), volume_profile_break (89–224%), inverse_reversion_4h.
2. **Genuine no-edge confirmed** — both frameworks agree the number is bad; the second engine just prices exits differently.
3. **Exception** — funding_aware_v1: Sharpe/returns consistent, ONLY maxDD diverged (100%). A DD-only divergence is an audit trigger, not proof of no-edge; see Open questions.

## Methodology lessons (family-level)

1. Sharpe annualization: per-trade mean/std × √bars inflates grotesquely (16.37 vs 0.76 on the same equity). House standard: daily-resampled equity × √365.
2. In-sample parameter sweeps overfit single-TF variants (IS 7.87 / OOS 0.99 / wf_ratio 0.126). OOS walk-forward is the ONLY pass gate; in-sample numbers are not evidence.
3. Framework consistency is necessary, not sufficient: a 3-framework-consistent Sharpe 0.38 still fails the gate.
4. No commit = no evidence: iter#56's "Sharpe 90.78" was demo data; the source was never committed and could not be re-audited.
5. Proxy data produces proxy verdicts: every options variant failed on realized-vol/PCR proxies; synthetic data is banned — fix the data constraint before opening an axis.
6. Sample-size discipline: overlay audits with n=3–8 trades are inconclusive by construction (n > 100 rule); a single n=20 level-touch audit of the VPVR level detector (2026-07-17) came out "weakens baseline, inconclusive" — same rule.
7. Exhaustion rule (cycle-46): after 35+ archived variants a family is declared exhausted; exactly one rebuild per axis, then archive; a fresh axis requires a written spec with a failure-mode-avoidance table covering the prior archives.
8. maxDD accounting differs across engines (intra-trade MTM vs realized) — a DD-only divergence is an audit trigger, not an archive trigger.

## Open questions

1. **maxDD audit of `vpvr_funding_aware_v1_20260711`** (4h BTC+ETH long-only, funding_sum_24h vol-regime): Sharpe 7.16/5.83 and total return 144.3%/131.9% agree across two frameworks; archived ONLY on DD divergence (-57% vs ~0). If in-house DD is the artifact, this is the family's only gate-consistent candidate. Also needs a clean walk-forward (failed 2/4 pre-audit).
2. **funding_carry_asym threshold**: 30d observed funding max 0.0001 never crossed the 0.0003 gate → 0 trades. Percentile-based or ≤ 0.01% threshold untested; a 0.02–0.08% sweep is in flight but still sits above observed funding.
3. **vol_breakout_2tf_vpvr on 1m/15m**: the 4h version was framework-consistent (Sharpe 0.38) but sub-gate; TF-downshift never tried.
4. **Options axes with real Deribit data** (IV term-structure, PCR/OI pressure): all attempts ran on proxies; no real-data attempt exists.
5. **ETH/SOL 1m funding-carry with real data**: 1m attempts were BTC/ETH-only (real) or synthetic; SOL 1m never run.

---

Sources: `quant-loop/strategies/vpvr_*/results/{metrics,summary}.json`, campaign verdict tables 2026-07-09 → 2026-07-18, cross-framework validation reports (freqtrade 2026.6 / vectorbt 1.1.0 / backtrader 1.9.78.123). Iteration numbers (iter#NN) are the family's campaign counter and appear in branch names.
