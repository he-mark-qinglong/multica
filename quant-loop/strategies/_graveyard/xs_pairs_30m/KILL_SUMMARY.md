# KILL_SUMMARY — vpvr_xs_pairs_30m funding-filter 家族（10 个策略）

**Archived**: 2026-07-24 (Phase C of `PLAN_20260724_hf_strategy_optimization.md`)
**Verdict**: KILL — 10/10 framework CV NOT-PROFITABLE，双框架（freqtrade/backtrader）一致负 edge。

## 策略清单与 framework CV Sharpe

| 策略 | metrics Sharpe | 证据 |
|------|---------------|------|
| vpvr_xs_pairs_30m_funding_filter_20260712 | -4.86 | `results/{metrics.json,framework_cv_backtrader.json,framework_cv_freqtrade.json,walk_forward.json}` |
| vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717 | -10.76 | `results/{metrics.json,framework_cv_*.json}` |
| vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717 | -3.85 | `results/{metrics.json,framework_cv_*.json}` |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712 | -2.51 | `results/{metrics.json,framework_cv_*.json,walk_forward.json}` |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717 | （backtrader 重放变体，未通过） | 目录内结果文件 |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717 | -2.90 | `results/{metrics.json,framework_cv_*.json,walk_forward.json}` |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712 | -2.72 | `results/{metrics.json,framework_cv_*.json,walk_forward.json}` |
| vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712 | -3.82 | `results/{metrics.json,framework_cv_*.json,walk_forward.json}` |
| vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717 | -3.76 | `results/{metrics.json,framework_cv_*.json}` |
| vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717 | -6.40 | `results/{metrics.json,framework_cv_*.json}` |

## Kill 原因

- 计划 §1.1 判决："30m xs_pairs funding filter：10/10 framework CV NOT-PROFITABLE → KILL"。
- 参数放宽（v5_loose）、正则化（regularized）、换 pair（btc_bnb / btc_doge / eth_sol）、换框架（v10_backtrader）全部失败 —— 属于结构性负 edge，非参数问题。
- 多代重试（v3 → v5_loose → v10）符合 cycle-46 family exhaustion 判据。

## Revival 条件

- 30m 单时间框架 + funding filter 的 xs_pairs 结构不再重试。
- 可复用的正期望先例是同族不同结构：`mtf_xs_pairs H3`（2h funding regime 门 + 1m 入场 + 15m ATR sizing，OOS Sharpe 2.773）—— 多周期确认 + regime 过滤 + 成本感知 sizing 是幸存结构，单 TF 30m 不是。

## 证据路径

- 各策略目录内 `results/framework_cv_{freqtrade,backtrader}.json`、`metrics.json`、`walk_forward.json` —— 证据链完整保留
- `PLAN_20260724_hf_strategy_optimization.md` §1.1
- 对照（非本家族，保留不归档）：`strategies/paper_trading_mtf_xs_pairs_eth_sol_20260719/`
