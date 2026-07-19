[framework-validate hourly @ 2026-07-19 14:37+08 — autopilot run 2728faab-60b7-4cf8-a20c-66d18d34de3c]

## WITHIN_TOLERANCE — reproduction check PASS (NOT-PROFITABLE stands, reproducible)

**Strategy**: `vpvr_xs_smart_routing_15m_20260715` (iter#105, Campaign SMA-34206, axis: multi-venue smart routing microprice divergence + TWAP-sliced vol-aware exit; BTCUSDT 15m; microprice = Binance taker-buy-share proxy, MULTI-VENUE-DATA-MISSING). In-house `tag=NOT-PROFITABLE` (sharpe -4.4332, total_return -2.0914%, max_dd -2.2109%, 2,772 trades, PF 0.567, WR 30.0%).

**Framework**: freqtrade 2026.6 (rotating-list position 1; first framework ever applied to this strategy — `used=[<none>]` in the scan).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` reports 26 terminal strategies (cutoff = 2026-07-12T06:46 UTC).
- Sort key `(recent_cv_count, total_cv_count, name asc)`: `vpvr_xs_smart_routing_15m_20260715` sorts FIRST — the only terminal strategy with zero frameworks used and zero CV in the past 7 days. All 25 others have CV records dated 2026-07-17/18/19.
- Rotating list: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` is the first unused.

## Data handling

- BTCUSDT 15m native parquet (`live_data/BTCUSDT_15m.parquet`, 159,282 rows total) span-limited to the in-house window 2022-01-01 00:00 → 2026-07-10 23:45 UTC = **158,587 bars** (matches `summary.json.n_bars` exactly).
- Trades: 2,772 in `results/trades_A_15m_BTCUSDT.csv`; **2,772 replayed, 0 skipped** (all entry/exit timestamps land on 15m bars inside the window).

## Cost model

- In-house: `fee_bps_per_fill=4.0 + slippage_bps_per_fill=2.0` per side → **12bp round trip**.
- Freqtrade: 4bp fee + 2bp slip per side → **12bp round trip** (IDENTICAL).
- Therefore this run is a **reproduction check**: any divergence is replay/convention error, not cost-fragility (contrast with the xs-pair 30m family, where freqtrade's 3× cost delta was the entire signal).

## Validation (engine self-check before trusting the framework run)

Replay at in-house cost vs `results/equity_15m_BTCUSDT.csv`:

- `n_bars_compared = 158,587`
- `max_abs_rel_err = 3.005e-06` (per-bar float-write precision; in-house amortises cost over `bars_held+1` bars incl. one post-exit bar, the lib over `(entry,exit]` — total cost identical)
- `final_abs_rel_err = 4.453e-09` (terminal equity matches to 9 decimal places)
- replay max_dd -0.022109 vs in-house -0.022109 (abs diff 1.4e-07)

## Full-span divergence (like-for-like vs metrics.json)

| metric | inhouse (metrics.json) | framework | abs rel divergence |
|---|---:|---:|---:|
| sharpe (trade-formula μ/σ×√tpy) | -4.4332 | -4.4346 | **0.0332%** |
| total_return | -0.020914 | -0.020914 | **0.0000%** |
| max_dd | -0.022109 | -0.022109 | **0.0011%** |

Supplementary: framework NAV-bar sharpe -4.2957 (formula reference only, not compared).

## OOS walk-forward divergence (3 folds: 2023H1 / 2023H2 / 2024H1)

`walk_forward.json` was not produced for this NOT-PROFITABLE strategy. Per 20260719_1137/1237/1337 precedent, in-house record serves as the reference — but here the fold metrics are computed on BOTH sides with the SAME NAV-bar formula (15m bars/yr = 35,064) on the framework NAV and on the in-house equity CSV slices, i.e. strictly like-for-like (no cross-formula comparison, the SMA-34922 sentinel class of bug).

| fold | fw sharpe | ih sharpe | fw total | ih total | fw mdd | ih mdd |
|------|----------:|----------:|---------:|---------:|-------:|-------:|
| 2023H1 | -6.1806 | -6.1947 | -0.0027 | -0.0027 | -0.0027 | -0.0027 |
| 2023H2 | -7.5505 | -7.5602 | -0.0029 | -0.0029 | -0.0029 | -0.0029 |
| 2024H1 | -5.6469 | -5.6486 | -0.0034 | -0.0034 | -0.0035 | -0.0035 |
| **mean/worst** | **-6.4593** | **-6.4678** | **-0.0030** | **-0.0030** | **-0.0035** | **-0.0035** |

| metric | framework (OOS mean) | inhouse (OOS mean) | abs rel divergence |
|---|---:|---:|---:|
| sharpe | -6.4593 | -6.4678 | **0.1314%** |
| total_return | -0.0030 | -0.0030 | **0.0011%** |
| max_dd (worst) | -0.0035 | -0.0035 | **0.0037%** |

`max_abs_rel_divergence_pct = 0.1314%` → **≤ 50% W5 threshold → WITHIN_TOLERANCE**.

## Verdict

**WITHIN_TOLERANCE.** The in-house NOT-PROFITABLE verdict (iter#105) is fully reproducible under the freqtrade 2026.6 cost model: identical costs, identical replay convention (validated to 3e-06), divergence ≤0.14% on every metric on both full-span and 3-fold OOS walk-forward. The strategy loses -2.09% over 4.52 years with PF 0.567 / WR 30.0% — the NOT-PROFITABLE tag is honest, not an implementation artefact. No W5 auto-archive action (strategy already terminal; nothing broken to archive), no ESCALATE-TO-SMARK (clean reproduction, per 2026-07-15 vol_breakout_2tf WITHIN_TOLERANCE precedent: "no W5 auto-archive, no ESCALATE-TO-SMARK needed").

## Actions taken

1. ✅ `framework_cv_freqtrade.json` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_smart_routing_15m_20260715/results/framework_cv_freqtrade.json` (`w5_auto_archive: false`, `w5_verdict: WITHIN_TOLERANCE`, max_abs_rel_divergence 0.1314%).
2. ✅ `framework_adapter_freqtrade.py` written: `/home/smark/multica/quant-loop/strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_freqtrade.py` (`python3 -m py_compile` PASS).
3. ✅ Framework equity persisted: `/tmp/framework-validate-vpvr_xs_smart_routing_15m_20260715-freqtrade/equity_recomputed.csv` (158,587 15m bars, 4.52y span).
4. ✅ Validation equity persisted: `/tmp/framework-validate-vpvr_xs_smart_routing_15m_20260715-freqtrade/equity_validation_inhouse_cost.csv` (reproduces in-house CSV, max_rel_err 3.0e-06).
5. ✅ Cached results: `/tmp/framework-validate-vpvr_xs_smart_routing_15m_20260715-freqtrade/results.json`.
6. ❌ NO ESCALATE-TO-SMARK (divergence 0.13% — clean reproduction, not a smark-judgment case).
7. ❌ NO metrics.json modification (record preserved; W5 §3).
8. ❌ NO issue creation/mutation (run-only autopilot; WITHIN_TOLERANCE prescribes no issue action; strategy already terminal NOT-PROFITABLE).

## Output sink (auditable)

- CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_smart_routing_15m_20260715/results/framework_cv_freqtrade.json`
- Adapter source: `/home/smark/multica/quant-loop/strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_freqtrade.py`
- Framework equity: `/tmp/framework-validate-vpvr_xs_smart_routing_15m_20260715-freqtrade/equity_recomputed.csv`
- Validation equity: `/tmp/framework-validate-vpvr_xs_smart_routing_15m_20260715-freqtrade/equity_validation_inhouse_cost.csv`
- Cached result: `/tmp/framework-validate-vpvr_xs_smart_routing_15m_20260715-freqtrade/results.json`
- This run report: `/home/smark/multica/quant-loop/workdir/framework_validate_run_20260719_1437.md`

## Done-criteria checklist

- [x] Output sink written: `framework_cv_freqtrade.json` (`w5_verdict: WITHIN_TOLERANCE`)
- [x] Adapter source committed to strategy dir (py_compile PASS)
- [x] Framework equity curve persisted (158,587 15m bars, 4.52y span)
- [x] Validation equity curve persisted (reproduces in-house CSV, max_abs_rel_err 3.0e-06, n_fills 2,772/2,772)
- [x] OOS walk-forward divergence table (3 folds, like-for-like NAV formula both sides)
- [x] No ESCALATE-TO-SMARK (0.1314% ≤ 50%, clean reproduction)
- [x] No metrics.json / issue mutation (run-only protocol, WITHIN_TOLERANCE)
- [x] Run report written

## Result wire

`framework-validate hourly @ 2026-07-19 14:37+08 → WITHIN_TOLERANCE: vpvr_xs_smart_routing_15m_20260715 (iter#105) × freqtrade 2026.6 — first CV ever for this strategy (0 frameworks used, 0 recent CV); freqtrade cost 12bp rt = in-house 12bp rt → reproduction check; validation replay matches in-house equity to max_rel_err 3.0e-06 over 158,587 15m bars / 2,772 trades; max_abs_rel_divergence 0.1314% (full-span sharpe 0.033% / total_ret 0.000% / max_dd 0.001%; OOS 3-fold sharpe 0.131% / tot 0.001% / mdd 0.004%) ≤ W5 50% → in-house NOT-PROFITABLE (sharpe -4.43, tot -2.09%, mdd -2.21%, PF 0.567) confirmed reproducible; no auto-archive, no ESCALATE, no issue mutation.`
