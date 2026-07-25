# KILL_SUMMARY — momentum_trend 家族（1 个目录）

**Archived**: 2026-07-25 (graveyard 迁移补充批次)
**Verdict**: KILL — `results-ledger.md` line 20：framework CV (backtrader) Sharpe **-0.159**，双框架复核后无正 edge。

## 策略清单

| 策略 | 说明 |
|------|------|
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | 1h 多周期动量趋势 v3（ATR scaled）；v1/v2 仍 UNTESTED 留在 strategies/，本目录仅归档已被 ledger 判 KILL 的 v3 |

## Kill 原因

- ledger KILL verdict：backtrader 框架 CV Sharpe -0.159，参数化后的 v3 变体未能通过复核。
- 注意边界：家族未整体证伪 —— 同族 `momentum_trend_btc_only_softer_stop_1h_20260712` 在 ledger 中为 PASS，v1/v2 为 UNTESTED；本归档只针对 v3 这一被判决的变体。

## Revival 条件

- 不得对 v3 做参数重扫（cycle-46）；若重做动量趋势轴，需新 prior 结构（非 ATR-scaled 参数变体）。

## 证据路径

- 目录内 `results/`（framework_cv_backtrader.json、walk_forward/、summary.json、equity/trades csv）
- `results-ledger.md` Active Strategies 表 line 20（KILL 行）
