[framework-validate hourly @ 2026-07-19 12:37+08 — autopilot run 695f0780-597b-4cac-8f5b-1ca8fc2a1936]

## W5 AUTO-ARCHIVE (NOT-PROFITABLE)

**Strategy**: `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` (iter#84 ETHUSDT/SOLUSDT 30m cross-asset z-score + VPVR confluence + funding-blowoff filter; V7 regularized; entry_z=2.5, lookback=192, max_hold=96, fund_thr=0.0003). In-house tag=NOT-PROFITABLE (sharpe 0.0265, total_return -1.77%, max_dd -23.17%, 2588 trades).

**Framework**: freqtrade 2026.6 (rotation position 1; first framework recorded for this strategy; real IStrategy import).

## Selection evidence

- `python3 /home/smark/multica/quant-loop/workdir/framework_validate_scan.py` found 26 terminal strategies (cutoff = 2026-07-12T04:39 UTC).
- Pre-run state: `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` was the top-priority eligible candidate — done status (NOT-PROFITABLE tag), zero frameworks used, and no CV in the preceding 7 days. It precedes the three `..._20260717` candidates that share the same shape.
- Rotation: `freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`. `freqtrade` was the first unused.

## Data and replay

- Both legs are native 30m parquets (ETH/SOL V7 has no 15m resample, distinct from the BTC/SOL variant). 79,296 common 30m bars, span 2022-01-01 00:00:00 → 2026-07-10 23:30:00 (4.523 years).
- 2,588 trades loaded; 2,588 closed trades replayed; 0 skipped; 0 out-of-window.
- Replay self-check: in-house gross equity walk reproduced to `max_abs_rel_err = 5.70e-12` and `final_abs_rel_err = 3.47e-12` (machine precision) — divergence below is cost-model/fill-convention, not replay error.
- No terminal open position was detected (last trade closed at 2026-07-10 17:30:00 and final equity is flat from 2026-07-10 22:30:00 onward).

## OOS walk-forward divergence (3 folds: 2023H1 / 2023H2 / 2024H1)

| metric | inhouse (proxy from metrics.json, walk_forward.json absent) | freqtrade (OOS mean) | abs rel divergence |
|---|---:|---:|---:|
| sharpe | +0.0265 | -11.2455 | **42,507.62%** |
| ann_total_return | -0.01775 | -0.80022 | **4,408.74%** |
| max_dd (worst fold) | -0.23173 | -0.56792 (worst fold) | **145.08%** |

`max_abs_rel_divergence_pct = 42,507.62%`  (threshold 50.0 → auto-archive fires).
Tipping metrics: **all three** (`sharpe`, `ann_total_return`, `max_dd`).

The in-house proxy uses the single aggregated metrics.json because `walk_forward.json` was not produced for this strategy. This is a deliberate choice consistent with `framework_validate_run_20260719_1137.md` precedent — when walk_forward.json is absent the aggregated metrics serve as the OOS reference; any >50% divergence still auto-archives. The proxy itself is a poor fit because the in-house aggregate covers the entire 2022-01 → 2026-07 span (where the strategy is roughly break-even at sharpe 0.027), while the framework OOS covers three half-year sub-windows where cost sensitivity is far more punishing.

## Why the divergence is so large

- In-house cost model is 8 bp pair round-trip (1 bp fee + 1 bp slip per side per leg × 2 legs × 2 sides). Freqtrade's default cost model is **24 bp pair round-trip** (4 bp fee + 2 bp slip per side per leg × 2 legs × 2 sides) — 3× the in-house cost basis.
- The strategy holds an average of ~2.45 bars per trade with 2,588 trades — even a 16 bp additional cost per trade compounds to a ~41 pp drag over the full sample, which explains why the framework equity terminates at $1,955 (-99.80%) vs in-house $98,225 (-1.77%).
- This is the same cost-fragility pattern flagged in the prior ETH/SOL iter#82 run (max_abs_rel_divergence 1,533.182%) — the strategy's edge does not survive realistic institutional cost assumptions.

## Validation and checks

- In-house replay validation: `n_bars_compared=79,296`, `max_abs_rel_err=5.70e-12`, `mean_abs_rel_err=2.68e-12`, `final_abs_rel_err=3.47e-12`.
- Terminal equity: in-house `$98,225.181436`; reproduced `$98,225.181436`.
- `python3 -m py_compile framework_adapter_freqtrade.py`: PASS.
- `metrics.json` was not modified. `metrics.json` `tag` remains `NOT-PROFITABLE` per W5 §3.
- Per the run-only autopilot protocol, no strategy issue mutation was performed. The framework-validate autopilot 51e7cb03 carries no assigned issue ID; smark-decision issue auto-archive by issue-status-flip would require the strategy issue to be in scope (the iter#84 ETH/SOL variant does not appear to have an associated strategy issue at the time of this run).

## Output sink (auditable)

1. CV record: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/results/framework_cv_freqtrade.json`
2. Adapter: `/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_freqtrade.py`
3. Framework equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717-freqtrade/equity_recomputed.csv`
4. In-house replay equity: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717-freqtrade/equity_validation_inhouse_cost.csv`
5. Cached CV result: `/tmp/framework-validate-vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717-freqtrade/results.json`
6. This run report: `/home/smark/multica/quant-loop/workdir/framework_validate_run_20260719_1237.md`

## Result wire

`framework-validate hourly @ 2026-07-19 12:37+08 → W5 auto-archive (NOT-PROFITABLE): vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717 / freqtrade; max_abs_rel_divergence_pct=42507.62% (Sharpe 42507.62%, annualized return 4408.74%, maxDD 145.08%) all > 50%; in-house terminal equity $98,225.181436 reproduced to 3.47e-12 relative error; freqtrade 24bp pair RT cost vs in-house 8bp pair RT cost explains the gap.`