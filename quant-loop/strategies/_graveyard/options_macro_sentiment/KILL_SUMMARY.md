# KILL_SUMMARY — options / macro / sentiment 家族（8 个目录）

**Archived**: 2026-07-24 (Phase C of `PLAN_20260724_hf_strategy_optimization.md`)
**Verdict**: KILL — 合成/proxy 数据驱动信号证伪；options/macro 系无任何通过复核的证据，多数为数据探索残存目录。

## 策略清单

| 策略 | 说明 |
|------|------|
| vpvr_options_gamma_1d_20260711 | options gamma exposure 1d；目录仅含 data/，无可复核结果 |
| vpvr_options_iv_skew_1d_20260713 | IV skew 1d；无通过复核证据 |
| vpvr_options_iv_termstructure_4h_20260715 | IV term-structure 4h；无通过复核证据 |
| vpvr_options_putcall_oi_pressure_8h_20260715 | put/call OI pressure 8h；无通过复核证据 |
| vpvr_macro_calendar_4h_20260715 | 宏观日历事件 4h；无通过复核证据 |
| vpvr_onchain_proxy_1h_20260711 | on-chain proxy 1h；proxy 数据，属合成数据判决 |
| vpvr_stable_depeg_regime_4h_20260716 | 稳定币 depeg regime 4h；`results/` 内证据保留 |
| vpvr_stable_depeg_regime_4h_20260716_p3opt_091 | 同上 p3opt 参数变体 |

## Kill 原因

- 计划 §1.1 判决："合成/proxy 数据驱动信号：sentiment_attention 无真实数据，Sharpe -7.8 → KILL 当前实现"。同一判决覆盖 onchain proxy 与其他无真实数据源的变体。
- options 系（gamma / IV skew / IV term-structure / putcall OI）：数据获取未落地为可复核回测，家族零通过记录，随 Phase C 归档。
- macro calendar / stable depeg：regime 类信号，无任何 framework CV / walk-forward 通过证据。

## Revival 条件

- 任何 options/macro/onchain 方向 revival 的前提：**真实、可持续获取的数据源** + 落入新管道（预注册 + CPCV + 双框架 CV + G1-G7 门控，计划 §2.3）。
- regime 类信号只允许作为过滤器（参照 `mtf_xs_pairs H3` 的 2h funding regime 门），不作为独立信号源。

## 证据路径

- 各策略目录内现存文件（多为 data/ 与代码；有 results/ 的予以完整保留）
- `PLAN_20260724_hf_strategy_optimization.md` §1.1
