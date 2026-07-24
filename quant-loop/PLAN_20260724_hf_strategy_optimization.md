# 高频策略优化全新计划 — 2026-07-24

> 基于 7 路并行全面分析（策略资产、验证框架、研究线程、数据管道、高频深挖、代码知识图谱、实盘运营）。
> 核心结论先行：**1m/5m klines 价格反转类信号已被结构性证伪（cost-cap），唯一未被证伪的高频轴是真实 aggTrades 订单流 + 多周期确认 + 低摩擦执行。**
> 旧内容仅作背景证据库，后续按本计划清理。

---

## 1. 现状判决（Why this plan）

### 1.1 已证伪的方向（不要再投入）

| 方向 | 证据 | 结论 |
|------|------|------|
| 1m/5m klines VPVR/价格反转 | 9 个变体 OOS FAIL + W5 + 双框架负 edge | KILL（cycle-46 exhausted） |
| funding-carry 系 | T06 三次重试均失败，Sharpe -1.52 | KILL |
| 4h 单 TF pair stat-arb | T09 CPCV 12 variant 全灭，worst-fold 恒负 | KILL |
| 30m xs_pairs funding filter | 10/10 framework CV NOT-PROFITABLE | KILL |
| 合成/proxy 数据驱动信号 | sentiment_attention 无真实数据，Sharpe -7.8 | KILL 当前实现 |

### 1.2 唯一幸存的高频资产

- `loid_iceberg_v4_1m_20260720`：代码 + 测试 + 7 symbol × 90d aggTrades 齐备，但真实 90d 回测未落地。
- 先例（SMA-34803）：iceberg 信号密度极低（30d 仅 22 flag），需先解决密度 vs 单笔期望问题。

### 1.3 已验证的正确范式

- `mtf_xs_pairs H3`（BTC/SOL）：OOS Sharpe 2.773、ann 59.8%、CI 下界 1.914。结构 = 2h funding regime 门 + 1m 入场 + 15m ATR sizing。
- `pairs_cointegration_1d`：walk-forward OOS Sharpe 3.60，freqtrade CV 通过。
- 共同特征：**多周期确认 + regime 过滤 + 成本感知 sizing**。

### 1.4 基础设施核心问题

- 两套矛盾的门禁（`validation/gates.py` Bonferroni vs `_shared/gates/enforce.py` DSR）
- 三份指标实现、两套成本模型并存
- `_shared/run_backtest.py`（权威 per-bar 复利引擎）未提交，采用率仅 15/469
- 73 个 framework adapter 共 ~30k LOC 复制粘贴
- 126 个文件硬编码 `/home/smark` 路径
- paper trading harness 已停跑 4 天，有重复记账 bug，不适合高频

---

## 2. 目标

1. **清理与归档**：把已证伪的 100+ 策略目录移入 `strategies/_graveyard/`，保留 results 作为证据；删除幽灵目录与冗余数据。
2. **统一基础设施**：一个回测引擎（`_shared/run_backtest.py`）、一套指标（`compute_metrics.py`）、一套门禁（DSR 版）、一份成本（`factor_backtester.CostModel`）。
3. **建立高频策略新管道**：只支持 aggTrades/订单流信号 + 多周期确认，预注册 + CPCV + 双框架 CV + G1-G7 门控。
4. **推进唯一活策略**：完成 `loid_iceberg_v4` 90d 回测，输出参数-密度-期望扫描。
5. **建立结果台账**：`results-ledger.md` 记录所有历史策略 verdict 与新高频策略进展。
6. **知识图谱固化**：把可复用组件整理为 `_shared/` 标准模块，禁止新策略内联重复实现。

---

## 3. 执行阶段

### Phase A — 安全提交与快照（Day 0）

**目的**：防止误删未提交的权威代码。

1. `git add` 并提交 `_shared/run_backtest.py`、`_shared/test_run_backtest.py`、`_shared/validation/compute_metrics.py`、`_shared/validation/test_compute_metrics.py`、`_shared/validators/framework_cv_validator.py`、`_shared/validators/test_framework_cv_validator.py`。
2. 创建分支 `chore/pre-cleanup-snapshot`，打 tag `snapshot-20260724`。
3. 把 `/tmp/iceberg_audit/` 的 `absorb_audit.py` 和 `t04_audit.json` 收进 `research/T04/`。

**验收**：`git status` 干净，权威引擎与验证器受版本控制。

### Phase B — 基础设施统一（Day 1-2）

**目的**：消灭口径分裂，让后续所有复核可复现。

1. **统一门禁**：让 `validation/gates.py` 委托 `_shared/gates/enforce.py`，G7 统一为 DSR；删除 Bonferroni 路径。
2. **统一指标**：以 `_shared/validation/compute_metrics.py` 9 键 dict 为唯一 schema；`validation/metrics.py` 改为薄包装；修正 `win_rate` 按 trade 计算；统一 max_dd 符号。
3. **统一成本**：`_shared/execution/cost_model.py` 的 futures 路径引用 `factor_backtester.CostModel` 的 ratified 常数（11bps/side fee-inclusive）；spot 路径保留但标注用途。
4. **引擎向量化**：把 `_shared/run_backtest.py` 的 per-bar 循环改为 numpy 切片累加，支持 1m 全量数据（~2.4M bars）。
5. **路径清理**：全仓库把 `/home/smark` 硬编码改为 `Path(__file__).resolve().parents[N]` 相对定位；清除 `sys.path.insert` hack。
6. **数据契约**：以 `data/manifests/volatility_edge_2026-07-20/` 为模板，为 perp 5m/15m（从 perp_1m 重采样）生成新 manifest，统一 10 列 schema，标注 `market: usdm_perp`。

**验收**：
- `pytest _shared/ backtest/ validation/` 全绿。
- 任一旧策略用统一管线重跑，结果与历史 verdict 偏差 <5%。
- 1m 全量回测单策略耗时 <5 分钟。

### Phase C — 旧策略清理与归档（Day 2-3）

**目的**：把证据链与开发面分离。

1. **graveyard 迁移**：
   - `strategies/_graveyard/vpvr_funding/`：12 个 funding 系
   - `strategies/_graveyard/funding_carry/`：4 个
   - `strategies/_graveyard/xs_pairs_30m/`：10 个
   - `strategies/_graveyard/vpvr_xs_pairs_4h/`：3 个
   - `strategies/_graveyard/1m_klines_reversal/`：bb、kama_reversal、volume_profile_break、mtf_reversion_5m、xs_leadlag、iceberg_fade v1/v2、obi_micro_v2
   - `strategies/_graveyard/options_macro_sentiment/`：8 个
2. **每个 graveyard 子目录写 `KILL_SUMMARY.md`**：链接 T01/T04/T06/T08/T09 线程，写明 kill 原因与 revival 条件。
3. **删除**：~40 个幽灵目录（仅 `data/` + `.pytest_cache`）、`*.pre-*` 备份、`__pycache__`、`data/perp_1m/*.csv`（1.07G 冗余）、`freqtrade_v10/`、`v10_grid_v2.*`。
4. **保留**：`paper_trading_mtf_xs_pairs_eth_sol_20260719/` 整体归档（标注重复记账 bug），`results/`、`reports/`、`research/`、`docs/decisions/`。

**验收**：`strategies/` 下活跃策略目录 <15 个；`du -sh strategies/` 下降 >50%。

### Phase D — 高频新管道建设（Day 3-5）

**目的**：只支持未被证伪的高频轴。

1. **策略契约 v2**：
   - 新策略只输出 `Trade` 列表（信号层），equity walk 一律走 `_shared/run_backtest.run_backtest(cost_mode="fill")`。
   - 禁止策略内联指标实现；VPVR/ATR/RSI 统一从 `_shared/indicators/` 导入。
   - 禁止策略本地数据副本；统一从 `data/perp_*` + `data/trades/` + manifest 读取。
2. **预注册模板**：复制 `vpvr_xs_pairs_4h_zscore_vpvr_20260710/run_optimize_cpcv.py` 为 `_shared/templates/preregistered_cpcv.py`。
3. **framework CV 通用化**：新策略不再写 per-strategy adapter；直接使用 `validation/adapters/` 的三框架重放。
4. **成本扩展**：在 `CostModel` 中加入 funding 费率序列注入与 maker/taker 混合参数（为 T10 sub-taker 执行研究预留）。
5. **增量信号接口**：为后续 paper/live 设计流式指标协议（rolling state update），避免每 bar 全量重跑。

**验收**：用模板新建一个 dummy 高频策略，30 分钟内完成从信号到双框架 CV verdict。

### Phase E — loid_iceberg_v4 推进（Day 5-7）

**目的**：回答唯一活高频策略的密度-期望问题。

1. 修复 `run_first_btc_90d.py` 的硬编码路径，跑通 90d BTC aggTrades 回测。
2. 参数扫描：z 阈值 {2, 3, 4, 5} × lookback {500, 1000, 2000} × composite 窗口 {1m, 5m, 15m}，预注册后冻结。
3. 输出：每个参数组合的 flag 密度、毛边、税后 Sharpe、最大回撤。
4. 判定标准（继承 T09 纪律）：mean OOS Sharpe ≥ 0.5 且 worst-fold ≥ 0.0 且 DSR > 0。
5. 若全部失败：按 cost-cap 归档，写 KILL_SUMMARY；若部分通过：进入 Phase F 对比优化。

**验收**：`results/loid_iceberg_v4/` 下有完整扫描报告与 verdict。

### Phase F — 结果台账与对比优化（Day 7+）

1. 创建 `quant-loop/results-ledger.md`，初始导入所有历史策略 verdict（从 `framework_cv_*.json`、`metrics.json`、graveyard KILL_SUMMARY 解析）。
2. 创建 `scripts/compare_hf_candidates.py`，对比 PASS/HOLD 高频策略：Sharpe（full/OOS/bootstrap CI）、ann return、maxDD、PF、成本敏感度、turnover。
3. 输出 `results/hf_candidates_comparison.md`。
4. 把 H3（若分支可合并）与 cointegration_1d 纳入对比，作为正期望基准。

---

## 4. 知识图谱（可复用组件）

### 4.1 引擎层
- `_shared/run_backtest.py` — per-bar 复利回测引擎（唯一权威）
- `backtest/factor_backtester.py` — CostModel 成本装配（唯一事实源）

### 4.2 指标层
- `_shared/indicators/`（新建，从 `strategies/_indicators/` 迁移）— VPVR、ATR、RSI、regime
- `_shared/regime/btc_gate.py` — trend/vol/funding 三维分类

### 4.3 验证层
- `validation/oos_harness.py` — 三框架 CV 编排
- `_shared/validation/cpcv.py` — CPCV + DSR
- `_shared/validation/compute_metrics.py` — 9 键指标唯一 schema
- `_shared/validators/*` — 哨兵/背离防线
- `_shared/gates/enforce.py` — 统一门禁

### 4.4 数据层
- `scripts/fetch_binance_*.py` — 抓取模式
- `live_data/refresh_klines_sma34871.py` — 原子增量刷新
- `data/manifests/` — 数据契约模板
- `data/trades/` — aggTrades hive（唯一高频数据资产）

### 4.5 执行层（待建）
- `SPEC_live_paper_connector_binance_usdm.md` — connector 蓝图
- `_shared/risk/kill_criteria.py`（从 paper_trading 迁移重构）— 风控评估器

---

## 5. 风险与边界

- **执行成本**：1m 高频策略的毛边必须 ≥5× 往返成本（perp ~10.83bp RT）才有意义；否则直接不立项。
- **数据缺口**：aggTrades 只有 2026-04 起 4 个月，跨 regime 回测不足；需补 2020-2025 历史（~65GB 磁盘）。
- **分支合并**：H3 代码在 `strategy-worker-2/mtf-h3-funding-regime` 分支，不在工作树；本计划不强制合并，但需评估是否纳入正期望基准。
- **不覆盖**：不做真实交易；不迁移 issue/project；不修改已 graveyard 的策略信号代码。

---

## 6. 成功标准

1. `pytest` 全绿，统一管线可复现历史 verdict。
2. `strategies/` 活跃目录 <15 个，graveyard 证据链完整。
3. `loid_iceberg_v4` 完成 90d 扫描并给出明确 PASS/HOLD/KILL。
4. `results-ledger.md` 可直接回答“哪些策略通过复核、失败原因是什么”。
5. 新策略从创建到双框架 CV verdict <30 分钟。
