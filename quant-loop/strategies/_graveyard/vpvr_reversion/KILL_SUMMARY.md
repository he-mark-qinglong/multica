# KILL_SUMMARY — vpvr_reversion 家族（2 个目录）

**Archived**: 2026-07-25 (graveyard 迁移补充批次)
**Verdict**: KILL — 两个目录均已在 `results-ledger.md` 判 KILL；属价格反转/均值回归轴的证伪延伸。

## 策略清单

| 策略 | 说明 |
|------|------|
| vpvr_inverse_reversion_4h_funding_filter_20260712 | 4h inverse reversion + funding filter；freqtrade CV Sharpe 0.001（≈无 edge），ledger line 37 KILL |
| vpvr_reversion_15m_donchian_regime_20260709 | 15m 反转 + donchian regime 门；ledger line 45 KILL，klines 反转家族（cycle-46 exhausted）的 15m 变体 |

## Kill 原因

- `vpvr_inverse_reversion_4h_funding_filter`：freqtrade 框架复核 Sharpe 0.001，税后无 edge；funding filter 未能挽救 4h 反转结构（与 PLAN §1.1 funding 系/反转系判决一致）。
- `vpvr_reversion_15m_donchian_regime`：属已证伪的 klines 价格反转轴（PLAN §1.1 "1m/5m klines VPVR/价格反转 → KILL, cycle-46 exhausted" 的同一信号族，15m 粒度变体），ledger KILL verdict 在案。

## Revival 条件

- 不得对 klines 价格反转 prior 做参数重扫；revival 需非反转结构的新 prior（regime filter / 多周期确认 / 真实订单流）。

## 证据路径

- 各目录内 `results/`（metrics.json、framework_cv_freqtrade.json、trades/equity csv、validation/）
- `results-ledger.md` line 37、line 45（KILL 行）
- `PLAN_20260724_hf_strategy_optimization.md` §1.1（klines 反转家族判决）
