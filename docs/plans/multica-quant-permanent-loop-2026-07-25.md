# multica 永续量化研究循环 — 总体方案 v1.0（2026-07-25）

> 目标：以 multica 为调度中枢，全部 agent 跑 caocao-m3（MiniMax-M3 聚合网关），
> 构建一个**无人值守、可永久运行**的策略「调研 → 实现 → 验证 → 判决 → 对比 → 沉淀」循环。
> K3 模型默认不参与；需要超出 m3 能力的判断时走 ESCALATE 人工通道，而不是升级模型。

---

## 1. 现状盘点（2026-07-25 已核实）

### 1.1 基础设施

- 调度中枢：multica server @ `192.168.0.105:8080`（issue / autopilot / metrics / artifact API 均可用）
- 本机 3 个 runtime 全部 online（MacBook-Pro-2.local）：
  - Claude `940d2b93`、Codex `0e57fd85`、Kimi `2ff52f36`
- 14 个 agent 全部 `model=caocao-m3`，当前均 idle
- 24 个 autopilot active（调度、门禁、watchdog、自调优、归档等，见 §4.2）
- 模型链路（脆弱点，重启即断）：
  - `127.0.0.1:18091` = SSH 隧道 → caocao LiteLLM（PID 15035，手工 nohup）
  - `127.0.0.1:18092` = Codex 模型映射代理 `caocao-m3→MiniMax-M3`（PID 32161，手工 nohup）
  - multica daemon 也是 nohup（PID 36014，无 launchd）

### 1.2 策略资产判决（继承 2026-07-24 HF 计划 + 07-25 swarm）

- **无可上线策略**。唯一候选 H3（`mtf_xs_pairs_1m_15m_2h_h3`）：本机复现 OOS Sharpe 1.875 / CI 下界 0.888，未能复现 PR#6 的 2.773/1.914；60bps 费率冲击下 Sharpe -0.04。
- 已 KILL：1m/5m klines 反转系、funding-carry 系、4h 单 TF stat-arb、microstructure 特征方向（T01 OFI / T04 iceberg 均为 cost-cap kill）。
- 最近线索：`signal-enhance-h3`（2024 子样本 Sharpe 8.07，未跑全历史 walk-forward，不能当证据）。
- 已验证范式：多周期确认 + regime 过滤 + 成本感知 sizing。
- 待修管线坑：server gate 对缺失字段「跳过不算失败」导致虚过；ledger PASS 语义混淆；H3-variants runner bug；73 个 framework adapter ~30k LOC 复制；126 文件硬编码 `/home/smark`。

---

## 2. 分层架构

```
┌─────────────────────────────────────────────────────────┐
│ 调度层  multica server (.105)                            │
│         issue 即工作单元，autopilot 即 cron，comment 即证据 │
├─────────────────────────────────────────────────────────┤
│ 执行层  3 runtime × 14 agent（全部 caocao-m3）            │
│         Claude=重研究  Kimi=调度/运维  Codex=决策/签核     │
├─────────────────────────────────────────────────────────┤
│ 研究层  quant-loop/（strategies, research/swarm, data）   │
├─────────────────────────────────────────────────────────┤
│ 验证层  _shared 统一引擎 + validation/ + 双框架 CV        │
│         预注册 → walk-forward OOS → fee shock → G 门      │
├─────────────────────────────────────────────────────────┤
│ 知识层  JOURNAL.md / results-ledger.md / knowledge/      │
│         GitNexus 代码图谱（旧代码复用）                    │
├─────────────────────────────────────────────────────────┤
│ 展示层  compare 页面（campaign tree + run_metric + 徽章） │
└─────────────────────────────────────────────────────────┘
```

硬约束（继承 AGENTS.md）：comment 首行必须 `[type=...]` schema；KILL 必须附证据指针；
family exhaustion（cycle-46）规则——同族参数扫荡超过阈值即强制换轴。

---

## 3. 角色分工（14 agent → 循环岗位）

| Agent | Runtime | 循环岗位 |
|---|---|---|
| quant-researcher | Claude | 假设生成 + 策略实现（主力研究） |
| quant-research-agent | Kimi | 外部调研（网络/主流机构思路 → SPEC 草案） |
| quant-analyst | Kimi | 数据审计 + 指标复核（防自欺） |
| multica-strategy | Claude | 验证管线 owner（gates/CV/fee shock） |
| strategy-worker-1/2 | Claude | 回测执行工（并行跑窗） |
| multica-code | Kimi | 管线/适配器/服务代码维护 |
| multica-orchestrator | Kimi | 任务派发 + 负载均衡 |
| multica-ops + ops-worker-1 | Kimi | 运维自愈（隧道/代理/daemon 健康） |
| knowledge-curator | Claude | 每日知识快照 + 旧内容归档 |
| persona-advisor | Kimi | 人类可读 verdict 润色（§7） |
| smark-decision-maker | Codex | 分支决策（KEEP/KILL/PIVOT） |
| smark-signoff-proxy | Codex | 证据门禁签核（不达标不放行） |

原则：**研究 agent 不许给自己的策略签核**（现有 evidence-gate 已隔离，保持）。

### 3.1 并行纪律（smark 2026-07-25 定调）

- **基础设施与执行面 → 多 agent 并行（swarm）**：管线开发、批量回测、参数/窗口扇出、
  归档清理、数据搬运。这类任务上下文可切分、结果可机械合并，用 swarm（≤128/批）抢时间。
- **策略思路与假设推演 → 单线程**：idea 生成、假设演化、判决推理必须由**同一条研究主线**
  （同一 agent 会话链）承载，保证上下文内聚和推理严谨。禁止把"想策略"拆给多个 agent 各自
  发散再拼接——碎片化的思路无法证伪。swarm 在研究中只用于**已定型假设的执行验证**
  （例：同一 SPEC 的 7 个 walk-forward 窗口并行跑），不用于产生假设本身。
- 衔接方式：单线程主线产出 SPEC → swarm 执行验证 → 结果汇回**同一条主线**做判决。

---

## 4. 永续循环设计

### 4.1 主循环（Epoch Loop，1 天一纪）

```
09:00 调研    research-scout 触发 quant-research-agent：外部调研（§5）+ 刷新 SPEC 池
10:00 筛选    smark-decision-maker 从 SPEC 池选 1-2 个进入当日实现（其余 backlog）
白天 实现     quant-researcher 写策略 + strategy-worker-1/2 并行跑 in-sample
17:00 验证    multica-strategy 走完整管线（§6），smark-signoff-proxy 签核
20:00 判决    KEEP → compare 上榜 + ledger LIVE 候选；KILL → 证据 + 复活条件归档
21:00 沉淀    epoch-retro：当日对比表 + 归档 + cycle-46 检查 + 次日优先级
```

全天常开：infra-health-watchdog（10min 探活）、stalled-issue-watchdog、Evidence Review Gate。
任一环节失败 → issue 自动回退 + watchdog 接管，循环不死；当天没产出就 NOOP 记录原因，第二天照常开新纪。

### 4.2 现有 24 个 autopilot 的映射（不重建，只归类补缺）

- **调度类**（保留）：Multica Dispatch、Workspace Queue Balancer、Idle Agent Dispatcher
- **验证类**（保留+修）：framework-validate、publish-gate、Evidence Review Gate、publish-metrics-signed
- **自愈类**（保留）：stalled-issue-watchdog、autopilot-prompt-tuner、Error-Pattern-Recorder、runtime-audit-cross-cli、REGRESSION-TEST
- **知识/归档类**（保留）：knowledge 相关、strategy-archiver、Graph Janitor、Issue-Graph Generator、campaign-tree-builder
- **新增 3 个**（补缺，2026-07-25 已全部创建并启用）：
  1. `infra-health-watchdog` `c84304df`（*/10min，multica-ops）：探活 18091/18092/daemon/.105，失败先 launchctl kickstart 自愈，再 ESCALATE
  2. `research-scout` `28d2a8c7`（每日 09:00 +08，quant-research-agent）：触发 §5 外部调研，产出/刷新 SPEC 草案 issue
  3. `epoch-retro` `23281f8e`（每日 21:00 +08，knowledge-curator）：盘点当日 KEEP/KILL、cycle-46 检查、当日对比表、次日优先级

### 4.3 成本控制（m3 省钱纪律）

- 所有 agent 默认 caocao-m3；**禁止任何 autopilot prompt 指定其他模型**
- 重活走 swarm 拆分（并行小任务）而不是单 agent 长上下文
- 每个 epoch 的调研/验证任务必须幂等 + 断点续跑（state 文件落盘，重跑不重复烧 token）
- 连续 3 次 NOOP 的 autopilot 由 autopilot-prompt-tuner 降频或 ESCALATE

---

## 5. 外部调研机制（创新发现）

`quant-research-agent` 每周调研以下渠道思路，转写成可证伪的 SPEC：

- **主流方法论**：CTA 趋势/期限结构、统计套利（cointegration/baskets）、做市与库存模型、
  动量/反转截面、carry（funding/basis）、波动率风险溢价、regime-switching
- **公开机构/课程内容**：主流量化课程与书籍的策略框架（如 EP Chan、Aronson 的
  评估方法、López de Prado 的 CPCV/特征重要性），只取方法论不取具体参数
- **社区前沿**：paperswithcode、quantpedia 类策略库、GitHub 高星回测项目的新信号
- **产出纪律**：每个 SPEC 必须含——假设一句话、可证伪条件、数据需求（必须已有或可免费获取）、
  预期成本约束（先算 cost-cap 再写代码，避免 T01/T04 式浪费）

入库前由 quant-analyst 做「数据可得性 + cost-cap 预检」，过不去的直接 KILL 不进实现。

---

## 6. 验证管线（策略复核标准）

唯一权威路径，任何策略上榜必须全走：

1. **预注册**：SPEC 先写参数与预期，禁止事后调参凑结果
2. **统一引擎**：`_shared/run_backtest.py`（per-bar 复利）+ `compute_metrics.py` 一套指标
3. **walk-forward OOS**：全历史 7 窗，报 mean Sharpe + bootstrap CI 下界
4. **双框架 CV**：in-house vs 主流框架（freqtrade/backtesting.py adapter），两边符号一致才算数
5. **fee shock**：60bps 费率下 Sharpe 仍 >0
6. **G 门**：per-symbol Sharpe ≥1、样本量、DSR/Bonferroni 多重检验校正
7. **签核**：smark-signoff-proxy 核证据链完整才盖 PASS

落地前置修复（Phase 1）：server gate「缺失字段跳过」漏洞、ledger PASS 语义拆分
（「框架一致」与「盈利」两个独立字段）、H3-variants runner bug、73 个 adapter 收敛为
1 个 generic adapter（`validation/generic_harness.py` 已有雏形）。

---

## 7. 人类可读对比机制

- **compare 页面**（已有）：campaign tree + run_metric + 徽章（framework_validated /
  fee_shock_fail 等如实标注）。H3 数据已入库，后续策略自动流入。
- **一句话 verdict**：每个策略由 persona-advisor 生成人类语言结论
  （例：「H3：有边际但扛不住手续费，60bps 下不赚钱」）
- **results-ledger.md** 三态：LIVE 候选 / HOLD（附 unblock 条件）/ KILL（附证据 + 复活条件）
- **每日 digest**：epoch-retro 出「当日对比表」——策略 × （OOS Sharpe、CI 下界、
  费后 Sharpe、最大回撤、一句话 verdict），写入 `knowledge/curator/`；周末由 knowledge-curator 汇总周报
- **淘汰可视化**：compare 页面对 KILL 策略灰显 + 鼠标悬停看 kill 原因（防重复试错）

---

## 8. 无人值守保障（永久运行的前提）

当前三大手工进程是单点。落地：

1. **launchd 化**（本机，✅ 2026-07-25 完成）：`com.smark.multica-daemon`、
   `com.smark.caocao-model-proxy`（18092）、`com.smark.caocao-tunnel`（18091，此前已在 launchd）
   全部 KeepAlive，崩溃自动拉起；plists 在 `~/Library/LaunchAgents/`
2. **infra-health-watchdog** autopilot（✅ `c84304df`，10 分钟）：探活 18091/18092/daemon/.105 server，
   失败先自愈（launchctl kickstart），自愈不了 ESCALATE 人工
3. **断点续跑**：所有长任务 state 落盘（workdir + artifact API），重跑从断点继续
4. **证据不丢**：所有验证产物走 artifact API 上 .105，本机磁盘清理不影响台账
5. **git 纪律**：研究产物推 fork（he-mark-qinglong/multica），每周由 strategy-archiver
   检查未提交改动，防丢失

---

## 9. 旧内容处理

- `strategies/` 已证伪 100+ 目录 → `_graveyard/`（代码留作证据库，GitNexus 已索引可检索复用）
- `_shared/` 收敛为唯一标准组件库；新策略禁止内联重复实现
- 硬编码 `/home/smark` 路径统一改环境变量
- JOURNAL.md / results-ledger.md 保留为只读历史；新纪元从本计划日期起记
- 清理执行由 knowledge-curator + multica-code 在 Phase 1 完成，清理前后各跑一次
  `detect_changes` 确认无误删

---

## 10. 实施路线

| Phase | 内容 | 验收 |
|---|---|---|
| P0 基础设施加固 ✅ 2026-07-25 | launchd 三个 plist + infra-health-watchdog autopilot | 已验收：daemon/proxy 切换 launchd 后 runtimes online、18092 冒烟 200 |
| P1 管线修复 + 旧内容清理 ✅ 2026-07-25 | gate/ledger 补丁落地（缺失字段=FAIL、verdict 拆 framework_consistent+profitable，65 测试过）、H3-variants runner 双倍计费 bug 修复重跑、57 个策略归档 _graveyard | 已验收：ledger 重建后 0 PASS（如实）；H3 修复后与 baseline 逐位一致；修正排序 H3>H1>H4>H2，H1 唯一费后幸存。遗留：server Go 同款 skip-pass bug（`server/internal/gate/gate.go:115-117,131`）待部署窗口修复 |
| P2 当前线索收尾 | signal-enhance-h3 全历史 7 窗 walk-forward | 复现或证伪 2024 子样本结论，出 verdict |
| P3 常开循环启动 | research-scout + epoch-retro 上线，Epoch 1 开跑 | 第一周产出 ≥3 个 SPEC + 1 个完成全管线的策略 |
| P4 对比机制完善 | compare 徽章/灰显/一句话 verdict 自动化 | 打开 compare 页面 30 秒看懂全部策略状态 |

P0/P1 是本周可做完的工程活；P2 是当前唯一可能出有效策略的杠杆；P3 起循环自持。

---

## 11. 不做的事（防跑偏）

- 不升级模型解决判断问题——m3 判不了的走 ESCALATE 人工
- 不在 validation 之外另起回测引擎/指标实现
- 不对已 KILL 家族做参数扫荡式重试（cycle-46）
- 不给 paper trading harness 续命（有重复记账 bug，等重建）
