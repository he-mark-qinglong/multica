# KILL_SUMMARY — funding_carry 家族（4 个策略）

**Archived**: 2026-07-24 (Phase C of `PLAN_20260724_hf_strategy_optimization.md`)
**Verdict**: KILL — T06 三次重试均失败，funding-carry 方向结构性证伪。

## 策略清单

| 策略 | 关键证据 |
|------|----------|
| funding_carry | 原始 carry 实现，T06 基线证伪（Sharpe -1.52 量级） |
| funding_carry_asym | 门禁复核：all_gates_pass=False，1 pass / 5 fail → FAIL。`results/summary.json` |
| funding_oscillator_mr | funding oscillator mean-reversion，随 T06 家族证伪归档 |
| sma_34925_btc_funding_delta | BTC funding-delta 实验（SMA-34925），无通过复核的证据 |

## Kill 原因

- **T06 thread（funding-as-carry）**：计划 §1.1 判决 —— "funding-carry 系：T06 三次重试均失败，Sharpe -1.52 → KILL"。
- 根本机制问题：long-pays-carry 方向承担负 carry 成本，信号 edge 无法覆盖成本 + 回撤门（G1/G3）。
- 与 T08 的区别已确认：T08 是新 prior（funding-as-timing-filter），不改变本家族的 KILL 判决。

## Revival 条件

- 全新 prior 来源，**不得基于 funding-carry / funding-delta**。T06 revival 条件明确排除 funding/VPVR prior content，防止三次重试的 anti-pattern 重演。

## 证据路径

- `research/JOURNAL.md`（T06 条目，2026-07-20）
- `research/OPEN_QUESTIONS.md` T06（status: killed）
- `funding_carry_asym/results/summary.json`（门禁 FAIL 明细）
- `PLAN_20260724_hf_strategy_optimization.md` §1.1
