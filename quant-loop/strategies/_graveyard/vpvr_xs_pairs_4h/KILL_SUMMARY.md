# KILL_SUMMARY — vpvr_xs_pairs_4h 家族（3 个策略）

**Archived**: 2026-07-24 (Phase C of `PLAN_20260724_hf_strategy_optimization.md`)
**Verdict**: KILL — T09 CPCV 12 variant 全灭，4h 单 TF pair stat-arb 是死轴。

## 策略清单

| 策略 | 关键证据 |
|------|----------|
| vpvr_xs_pairs_4h_zscore_vpvr_20260710 | T09 主体：CPCV（n_groups=6, k_test=2, purge=500, embargo=250）0/12 预注册候选过门；每个 variant 至少一个负 OOS fold（worst-fold Sharpe -1.03 ~ -2.84）；20bp 乐观成本角也不同时满足 mean≥0.5 且 worst≥0.0。`results/{cpcv_metrics.json,cpcv_summary.txt,params_optimized.json,walk_forward.json,framework_cv_*.json}` |
| vpvr_xs_pairs_btc_sol_4h_20260712 | 同族 4h 变体（in-sample Sharpe ~0.46-0.57，OOS 不过门）。`results/{cpcv_sweep.json,framework_cv_*.json,walk_forward.json}` |
| cointegration_pairs_vpvr_poc_4h_20260714 | 4h cointegration + VPVR POC 变体，随 T09 死轴归档。`results/framework_cv_vectorbt.json` |

## Kill 原因

- **T09 thread**（`research/THREADS/T09-vpvr-xs-pairs-4h-cpcv-optimization.md`，killed 2026-07-21，SMA-35167）：严格 CPCV walk-forward 下 5 轴参数笛卡尔扫描（zscore_entry × vpvr_poc_attractor_strength × vpvr_hvn_threshold × exit_zscore × cost_bps_total）无任何组合通过验收门（mean OOS Sharpe ≥ 0.5、worst-fold ≥ 0.0、DSR > 0）。
- 结构性发现：worst-fold 恒负 → 不是参数调优问题，是 4h 单 TF pair stat-arb 机制本身无 OOS edge。
- 计划 §1.1 判决一致。

## Revival 条件

- 4h 单时间框架 pair stat-arb 不再重试（T09 结论：dead axis）。
- 可复用资产：`run_optimize_cpcv.py` 已计划复制为 `_shared/templates/preregistered_cpcv.py`（Phase D），CPCV harness 本身保留在 `_shared/validation/cpcv.py`。
- 注意区分：`strategies/pairs_cointegration_1d_20260709/`（1d 频率，walk-forward OOS Sharpe 3.60）是正期望基准，**未**归档。

## 证据路径

- `research/THREADS/T09-vpvr-xs-pairs-4h-cpcv-optimization.md`
- `research/JOURNAL.md`（T09 条目）+ `research/OPEN_QUESTIONS.md` T09（status: killed）
- 各策略目录内 `results/` —— 证据链完整保留
- `PLAN_20260724_hf_strategy_optimization.md` §1.1
