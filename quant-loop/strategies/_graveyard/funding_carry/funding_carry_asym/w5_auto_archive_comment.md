[framework-validate hourly run | 2026-07-18 19:37 Asia/Shanghai]

# framework CV auto-archive per W5 — funding_carry_asym

Ran vectorbt framework CV on **vpvr-funding-carry-asym** (SMA-34652, iter 2, 15m BTCUSDT, window 2024-02-01 → 2024-04-30).

## Path

- adapter: `strategies/funding_carry_asym/framework_adapter_vectorbt.py`
- result: `strategies/funding_carry_asym/results/framework_cv_vectorbt.json`

## W5 verdict: AUTO-ARCHIVE per W5 (NOT-PROFITABLE)

| metric | inhouse | framework (vectorbt) | abs rel div |
|---|---|---|---|
| sharpe (full-period) | -1.5216 | -0.0824 | **94.6%** |
| total_return (full-period) | -0.0228% | -0.0228% | 0.02% |
| max_dd (full-period) | -2.72% | -0.0272% | **99.0%** |

`max_abs_rel_divergence_pct_full_period = 99.0% > 50%`  → **W5 auto-archive trigger fired** on both sharpe and max_dd metrics.

## Why this triggers W5

1. **Strategy is unprofitable in both engines** — inhouse Sharpe -1.52 (fails G1), framework Sharpe -0.08. Both reject.
2. **Cross-engine numbers disagree by an order of magnitude** on every risk metric (max_dd: -2.72% inhouse vs -0.027% framework), even though the trade list and equity curve come from the same in-house source — meaning the in-house vs framework risk-target / position-sizing / cost-application models differ by 100x.
3. **Inhouse G1-G7 hard gates also fail independently** (Sharpe -1.52 vs ≥1.0; ann_return -0.094% vs ≥15%; PF 0.59 vs >1.5) — pre-W5 NOT-PROFITABLE verdict stands.
4. **G5 framework CV threshold NOT met** (framework OOS Sharpe -0.07 vs ≥1.0 required).

## Auto-archive actions (per W5)

1. ✅ Posted this comment on SMA-34652.
2. ⏭ Next: `multica issue status SMA-34652 done` (NOT-PROFITABLE verdict, kept per audit; metrics.json untouched).

## Result wire

- Output sink: SMA-34652 comment (this file) + status flip
- Evidence: `strategies/funding_carry_asym/results/framework_cv_vectorbt.json` (vectorbt 1.1.0, n_bars=8640, n_folds=4)
- Done criteria: w5_auto_archive=true → status=done with NOT-PROFITABLE verdict

— framework-validator (multica-strategy, autopilot 51e7cb03-f866-47ae-95f2-86d94f23ffa3, run 00f8abec-5457-418b-a2a7-dd7d27772282)
