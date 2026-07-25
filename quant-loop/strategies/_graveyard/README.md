# strategies/_graveyard — 已证伪策略归档

归档原则（PLAN_20260724_hf_strategy_optimization.md §2.1 / Phase C）：已判 KILL 的策略目录整体迁入此目录，`results/` 证据链随目录保留，**不删除任何文件**。各家族子目录的 `KILL_SUMMARY.md` 写明 kill 原因与 revival 条件。

未归档（仍在 `strategies/`）：`mtf_xs_pairs_1m_15m_2h_*`（H3 为 live 候选族）、`loid_iceberg_v4_1m_20260720`（唯一幸存高频资产，Phase E 推进对象）、`pairs_cointegration_1d_20260709`（PASS）、`_indicators/`（共享指标库）。

---

## 1m_klines_reversal/ — 1m/5m klines 价格反转家族（16 个）

家族判决：PLAN §1.1「9 个变体 OOS FAIL + W5 + 双框架负 edge → KILL（cycle-46 exhausted）」；毛边系统性低于 cost-cap（perp ~10.83bp RT ×5）。证据：`results-ledger.md` Graveyard 表 lines 62-76；JOURNAL 2026-07-20 T01（execution-bound 同结论）。

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| bb_reversion_rsi_1m_20260707 | 1m bb+RSI 反转基线，cost-cap 下无税后 edge | ledger line 62；PLAN §1.1 |
| bb_reversion_rsi_1m_20260707_p3opt_049 / _050 / _099 / _100 | 基线的 4 个 p3opt 参数变体，随基线一并 kill | ledger lines 63-66 |
| vol_breakout_1m_15m_vpvr_confluence_u6_20260718 | 1m/15m 放量突破 + VPVR 汇合，同族证伪 | ledger line 67 |
| vpvr_iceberg_fade_5m_20260711 / vpvr_iceberg_fade_v2_5m_20260711 | 5m iceberg fade v1/v2（klines 推断版），反转家族 kill | ledger lines 68-69 |
| vpvr_microstructure_5m_volume_delta_20260710 | 5m volume-delta 微观结构，klines 粒度无 edge | ledger line 70 |
| vpvr_mtf_reversion_5m_consensus_20260710 | 5m 多周期共识反转，freqtrade CV Sharpe -6.344 | ledger line 71 |
| vpvr_obi_micro_v2_1m_20260714 | 1m orderbook imbalance 微观变体，cost-cap kill | ledger line 72 |
| vpvr_reversion_1m_kama_reversal_20260709 | 1m KAMA 反转，n=2 无统计意义 | ledger line 73 |
| vpvr_reversion_1m_volume_profile_break_20260709 | 1m volume-profile break，in-house Sharpe -22.654 | ledger line 74 |
| vpvr_sentiment_attention_1m_20260716 | 合成/proxy 情绪数据，无真实数据源，Sharpe -7.813 | ledger line 75；PLAN §1.1 单独点名 |
| vpvr_xs_leadlag_5m_20260711 | 5m 跨品种 lead-lag，双框架 Sharpe 0.000 | ledger line 76 |
| vpvr_reversion_5m_vwap_trail_20260709 | 5m 反转 + VWAP trailing，vectorbt CV Sharpe -9.621 | ledger line 48（2026-07-25 补归档） |

## funding_carry/ — funding-carry 系（4 个）

家族判决：PLAN §1.1「T06 三次重试均失败，Sharpe -1.52 → KILL」；JOURNAL 2026-07-19：max_dd sentinel 修复后 `vpvr_funding_aware_v1` 复核仍 KILL（Sharpe 0.74 < G1，maxDD -43.07% > G3），T06 revival 需全新 prior（非 funding/VPVR）。证据：`results-ledger.md` lines 77-80。

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| funding_carry / funding_carry_asym | funding-carry 基线与 asym 变体，T06 三次重试全灭 | ledger lines 77-78；JOURNAL 2026-07-19 |
| funding_oscillator_mr | funding 振荡器均值回归，同族 kill | ledger line 79 |
| sma_34925_btc_funding_delta | BTC funding delta 单变体，同族 kill | ledger line 80 |

## options_macro_sentiment/ — 合成/proxy 数据驱动信号（8 个）

家族判决：PLAN §1.1「合成/proxy 数据驱动信号 → KILL 当前实现」（sentiment_attention 无真实数据 Sharpe -7.8 为代表）；revival 需真实数据源且仅作 regime filter。证据：`results-ledger.md` lines 81-88。

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| vpvr_macro_calendar_4h_20260715 | 宏观日历事件，freqtrade/vbt Sharpe ≈0 无 edge | ledger line 81 |
| vpvr_onchain_proxy_1h_20260711 | on-chain proxy 数据，BT/FT Sharpe -0.220/-0.185 | ledger line 82 |
| vpvr_options_gamma_1d_20260711 | options gamma proxy，无真实数据支撑 | ledger line 83 |
| vpvr_options_iv_skew_1d_20260713 | IV skew proxy，同族 kill | ledger line 84 |
| vpvr_options_iv_termstructure_4h_20260715 | IV 期限结构 proxy，同族 kill | ledger line 85 |
| vpvr_options_putcall_oi_pressure_8h_20260715 | put/call OI pressure，in-house Sharpe -1.172 | ledger line 86 |
| vpvr_stable_depeg_regime_4h_20260716 / _p3opt_091 | 稳定币 depeg regime，framework CV Sharpe -0.201/0.000 | ledger lines 87-88 |

## paper_trading/ — paper trading 归档（1 个）

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| paper_trading_mtf_xs_pairs_eth_sol_20260719 | paper harness 有重复记账 bug 且已停跑，整体归档（非策略证伪） | ledger line 89；PLAN §Phase C.4 |

## vpvr_funding/ — vpvr_funding 系（12 个）

家族判决：PLAN §1.1 funding-carry 系 KILL；JOURNAL 2026-07-19 确认 sentinel 修复后 verdict 不变，"Family `vpvr_funding_*` stays in the kill bucket"。证据：`results-ledger.md` lines 90-101。

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| vpvr_funding_asym_4h_20260713 | 4h funding asym，BT/FT Sharpe -0.232/-0.217 | ledger line 90 |
| vpvr_funding_aware_v1_20260711 | funding-aware v1，sentinel 修复后仍 KILL（maxDD -43%） | ledger line 91；JOURNAL 2026-07-19 |
| vpvr_funding_carry_asym_v2_20260718 | funding-carry-asym v2 重试，同族 kill | ledger line 92 |
| vpvr_funding_delta_1h_20260711 / _asym / _mtf / _pair | 1h funding delta 四个变体，FT Sharpe 0.000 | ledger lines 93-96 |
| vpvr_funding_hvn_lvn_20260718 / _confluence | funding + HVN/LVN 结构，T08 regime-conditional 不 ship | ledger lines 97-98；JOURNAL 2026-07-20 T08 |
| vpvr_funding_regime_15m_20260711 | 15m funding regime，BT Sharpe -5.428 | ledger line 99 |
| vpvr_funding_reset_window_1h_20260715 | funding reset 窗口，BT/FT Sharpe -1.611/-1.393 | ledger line 100 |
| vpvr_funding_term_curve_1h_20260714 | funding 期限曲线，in-house Sharpe -0.846 | ledger line 101 |

## vpvr_xs_pairs_4h/ — 4h 单 TF pair stat-arb（3 个）

家族判决：PLAN §1.1「T09 CPCV 12 variant 全灭，worst-fold 恒负 → KILL」；JOURNAL 2026-07-21 T09（SMA-35167）：0/12 预注册变体过门，结构性 negative-fold，cycle-46 exhausted；与 SMA-33997 V12/V13/V14 4h 家族 kill 一致。证据：`results-ledger.md` lines 102-104。

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| cointegration_pairs_vpvr_poc_4h_20260714 | 4h 协整对 + VPVR POC，同族 kill | ledger line 102 |
| vpvr_xs_pairs_4h_zscore_vpvr_20260710 | T09 主体，CPCV 12 variant 全灭 | ledger line 103；JOURNAL 2026-07-21 T09 / SMA-35167 |
| vpvr_xs_pairs_btc_sol_4h_20260712 | 4h BTC/SOL pair，BT/FT Sharpe -0.768/-0.153 | ledger line 104 |

## xs_pairs_30m/ — 30m xs_pairs funding filter（10 个）

家族判决：PLAN §1.1「10/10 framework CV NOT-PROFITABLE → KILL」。证据：`results-ledger.md` lines 105-114。

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| vpvr_xs_pairs_30m_funding_filter_20260712 | 基线，framework CV Sharpe -4.863/-4.865 | ledger line 105；JOURNAL kill entry（SMA-34966 链） |
| vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717 | BTC/BNB v5 loose，Sharpe -10.759 | ledger line 106 |
| vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717 | BTC/DOGE，Sharpe -3.847 | ledger line 107 |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712 | BTC/SOL regularized，Sharpe -2.507 | ledger line 108 |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717 | BTC/SOL v10 backtrader 复跑，NOT-PROFITABLE | ledger line 109 |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717 | BTC/SOL v10 optimize，Sharpe -2.900 | ledger line 110 |
| vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712 | BTC/SOL v3，Sharpe -2.726 | ledger line 111 |
| vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712 / _20260717 / _v5_loose_20260717 | ETH/SOL 三个变体，Sharpe -3.826 / -3.761 / -6.405 | ledger lines 112-114 |

## momentum_trend/ — 动量趋势（1 个，2026-07-25 补归档）

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | 1h 多周期动量 v3，backtrader CV Sharpe -0.159 | ledger line 20（注意：v1/v2 UNTESTED、`momentum_trend_btc_only_softer_stop` PASS，均未归档） |

## vpvr_reversion/ — 反转/均值回归补归档（2 个，2026-07-25）

| 目录 | 一行 kill 原因 | 证据 |
|------|---------------|------|
| vpvr_inverse_reversion_4h_funding_filter_20260712 | 4h inverse reversion + funding filter，freqtrade Sharpe 0.001 ≈ 无 edge | ledger line 37 |
| vpvr_reversion_15m_donchian_regime_20260709 | 15m 反转 + donchian regime，klines 反转家族证伪延伸 | ledger line 45；PLAN §1.1 |

---

## 相关但未在此归档的 kill（无独立策略目录）

- **T01 OFI（SMA-35037，JOURNAL 2026-07-20）**：Cont-Kukanov-Stoikov OFI on real BTC 1m aggTrades → KILL（cost-cap，毛边低于执行成本）。
- **T04 iceberg absorption（SMA-35021，JOURNAL 2026-07-22）**：resting-liquidity absorption 交易假设 → KILL（cost-cap，毛边 ≤5.44bp < 半成本）。注意：`loid_iceberg_v4_1m_20260720` **不在此 kill 范围**（detector 保留为非交易用途 + Phase E 推进对象），仍在 `strategies/`。
