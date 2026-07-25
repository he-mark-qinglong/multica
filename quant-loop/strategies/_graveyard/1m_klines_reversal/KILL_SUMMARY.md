# KILL_SUMMARY — 1m/5m klines 价格反转家族（16 个目录）

**Archived**: 2026-07-24 (Phase C of `PLAN_20260724_hf_strategy_optimization.md`)
**Verdict**: KILL — 1m/5m klines VPVR/价格反转信号被结构性证伪（cost-cap），9 个变体 OOS FAIL + W5 + 双框架负 edge，cycle-46 family exhausted。

## 策略清单

| 策略 | 说明 |
|------|------|
| bb_reversion_rsi_1m_20260707 | 基线 bb+RSI 1m 反转；`results/metrics.json` |
| bb_reversion_rsi_1m_20260707_p3opt_049 / _050 / _099 / _100 | 同一基线的 4 个 p3opt 参数变体，随基线一并归档 |
| vpvr_reversion_1m_kama_reversal_20260709 | KAMA 反转变体 |
| vpvr_reversion_1m_volume_profile_break_20260709 | volume-profile break 变体 |
| vpvr_sentiment_attention_1m_20260716 | 合成/proxy 情绪数据驱动，无真实数据，Sharpe -7.8（计划 §1.1 单独点名） |
| vpvr_obi_micro_v2_1m_20260714 | orderbook imbalance 微观变体 |
| vpvr_mtf_reversion_5m_consensus_20260710 | 5m 多周期共识反转 |
| vpvr_xs_leadlag_5m_20260711 | 5m 跨品种 lead-lag |
| vpvr_iceberg_fade_5m_20260711 / vpvr_iceberg_fade_v2_5m_20260711 | iceberg fade v1/v2（klines 推断版，非 aggTrades 版） |
| vpvr_microstructure_5m_volume_delta_20260710 | 5m volume-delta 微观结构 |
| vol_breakout_1m_15m_vpvr_confluence_u6_20260718 | 1m/15m 双 TF 放量突破 + VPVR 汇合（U6） |
| vpvr_reversion_5m_vwap_trail_20260709 | 5m 反转 + VWAP trailing；vectorbt CV Sharpe -9.621，ledger line 48 KILL（2026-07-25 补归档） |

## Kill 原因

- 计划 §1.1 判决："1m/5m klines VPVR/价格反转：9 个变体 OOS FAIL + W5 + 双框架负 edge → KILL（cycle-46 exhausted）"。
- **cost-cap 结构性约束**：1m 高频策略毛边必须 ≥5× 往返成本（perp ~10.83bp RT）才有意义；klines 价格反转类信号的毛边系统性低于该阈值（计划 §5 风险边界）。
- 与 T01 的关系：T01（1m taker-flow OFI）同样是 execution-bound 结论 —— klines 粒度无法支撑高频 edge，真实订单流（aggTrades）才是唯一未证伪的高频轴。
- `vpvr_sentiment_attention_1m` 另属"合成/proxy 数据驱动信号"判决：无真实数据支撑，当前实现 KILL。

## Revival 条件

- **不得**基于 1m/5m klines 价格数据重试任何反转/均值回归 prior。
- 高频方向仅允许：真实 aggTrades 订单流信号 + 多周期确认 + 低摩擦执行（计划 §2.3 新管道）。幸存参照：`loid_iceberg_v4_1m_20260720`（未归档，Phase E 推进对象）。
- sentiment/attention 方向 revival 需真实数据源（非 proxy），且作为 regime filter 而非直接信号。

## 证据路径

- 各策略目录内 `results/`（metrics.json、walk_forward.json、framework_cv_*.json）—— 证据链完整保留
- `research/THREADS/T01-ofi-aggtrades.md`（execution-bound 结论）
- `PLAN_20260724_hf_strategy_optimization.md` §1.1、§5
