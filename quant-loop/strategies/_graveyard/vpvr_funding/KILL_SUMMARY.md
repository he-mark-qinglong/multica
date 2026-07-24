# KILL_SUMMARY — vpvr_funding 家族（12 个策略）

**Archived**: 2026-07-24 (Phase C of `PLAN_20260724_hf_strategy_optimization.md`)
**Verdict**: KILL — funding/VPVR prior 已结构性证伪（T06），max_dd sentinel 修复后复核维持 KILL。

## 策略清单

| 策略 | 关键证据 |
|------|----------|
| vpvr_funding_aware_v1_20260711 | iter#82 复核：Sharpe 0.74 < G1，maxDD -43.07% > G3 → KILL（smark-proxy 2026-07-18T17:09）。`results/{metrics.json,summary.json,walk_forward.json,framework_cv_*.json}` |
| vpvr_funding_asym_4h_20260713 | 同家族，framework CV 负 edge。`results/framework_cv_*.json` |
| vpvr_funding_reset_window_1h_20260715 | summary Sharpe -1.39。`results/summary.json` |
| vpvr_funding_term_curve_1h_20260714 | 同家族证伪。`results/` |
| vpvr_funding_regime_15m_20260711 | summary Sharpe -3.81。`results/summary.json` |
| vpvr_funding_hvn_lvn_20260718 | T08 前身变体，regime-conditional。`results/metrics.json` |
| vpvr_funding_hvn_lvn_confluence_20260718 | T08：in-sample PROFITABLE 但 trigger（funding>0.03%）2024-05→2026-07 零事件，结构性死触发；3 个 HOLD 门结构性不满足 → research-complete-hold，非 ship-eligible。`results/metrics.json` |
| vpvr_funding_delta_1h_20260711 | funding-delta 变体，framework CV 未通过。`results/framework_cv_backtrader.json` |
| vpvr_funding_delta_1h_asym_20260711 | 无有效复核结果，随家族归档 |
| vpvr_funding_delta_1h_mtf_20260711 | 同上 |
| vpvr_funding_delta_1h_pair_20260711 | 同上 |
| vpvr_funding_carry_asym_v2_20260718 | CPCV NOT-PROFITABLE（mean_oos_sharpe<1.2; min_pf<1.4; mdd 超限；trade-count 不足）。`results/{cpcv_metrics.json,metrics.json}` |

## Kill 原因

- **T06（funding-as-carry prior）**：三次重试均失败（Sharpe -1.52 量级），thread killed。JOURNAL.md 2026-07-20 条目：max_dd sentinel 修复（SMA-34922/SMA-34980）**没有**复活 vpvr_funding_aware_v1 —— 修正后 Sharpe 0.74 仍低于 G1，maxDD -43.07% 超 G3。methodology artefact 掩盖的是真实 gate failure。
- **T08（funding-as-timing-filter 新 prior）**：唯一 in-sample 真实 edge 的变体，但 funding>0.03% 触发器在当前数据 regime 结构性死亡（两年多零事件），campaign 于 2026-07-22 关闭为 research-complete-hold。
- 计划 §1.1 判决：funding/VPVR 系全部 KILL，不再投入。

## Revival 条件

- T06  revival 条件（不变）：**全新的 prior 来源，且不得基于 funding/VPVR**。任何 funding-carry 方向的 retry 视为违反纪律（T06 anti-pattern）。
- T08 revival 条件：数据 regime 变化使 funding>0.03% 事件恢复出现（需新 campaign iteration，T10+ 编号），非对同一家族的重试。

## 证据路径

- `research/JOURNAL.md`（2026-07-20 T06/T08 条目）
- `research/THREADS/T08-vpvr-funding-hvn-lvn-confluence.md`
- 各策略目录内 `results/`（metrics.json、summary.json、walk_forward.json、framework_cv_*.json、cpcv_metrics.json）—— 证据链完整保留
- `PLAN_20260724_hf_strategy_optimization.md` §1.1
