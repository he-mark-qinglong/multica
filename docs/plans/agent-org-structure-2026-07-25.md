# Agent 协作组织结构 v1.0（2026-07-25）

> 起源：Wave 0 出现两起 misroute（quant-analyst / persona-advisor 收到执行类任务，
> 正确判 out-of-scope 标 blocked）——拒绝行为是对的，缺的是事先讲清楚的结构与路由。
> 本文是 14 个 agent 的层级、接单范围、协作路径的唯一权威定义；
> 每个 agent 的 instructions 里有对应的「协作定位」块，与此文件一致。

---

## 1. 层级结构

```
L0  人类 (smark)               方向 / 例外裁决 / 资金决策
L1  协调调度                   multica-orchestrator(Mac) · multica-ops(Mac)
L2  研究主线（单线程）          quant-researcher(Mac) · quant-research-agent(105) · quant-analyst(Mac)
L3  执行（swarm workers）      multica-strategy(105) · strategy-worker-1/2(105)
                               multica-code(Mac) · ops-worker-1(105)
L4  决策签核                   smark-decision-maker(105) · smark-signoff-proxy(105)
L5  知识表达                   knowledge-curator(Mac) · persona-advisor(Mac)
```

## 2. 每个角色的接单范围

| Agent | 层 | 接受 | 拒绝（转谁） |
|---|---|---|---|
| quant-researcher | L2 主线 owner | 假设推演、SPEC、策略实现、判决推理、回测结果分析 | 批量执行/迁移 → strategy-worker；外部调研 → quant-research-agent |
| quant-research-agent | L2 调研 | 外部调研、SPEC 草案、文献/社区扫描、数据可得性预检 | 策略实现 → quant-researcher；回测 → strategy-worker |
| quant-analyst | L2 审计 | 数据审计、指标复核、gate 结果挑战、cost-cap 预检 | 代码执行/策略实现 → strategy-worker / quant-researcher |
| multica-strategy | L3 验证执行 | 全管线验证、framework CV、fee shock、G 门跑批、ledger 更新 | 假设生成 → quant-researcher |
| strategy-worker-1/2 | L3 通用执行 | 任何格式良好的任务卡（回测/迁移/删除/批量） | 需要判决的 → smark-decision-maker；卡缺陷 → blocked + orchestrator |
| multica-code | L3 代码工程 | server/前端/管线代码、代码审查、部署执行 | 策略研究 → L2 |
| ops-worker-1 | L3 杂务执行 | CLI 操作、健康检查、清单快照、简单脚本 | 长回测 → strategy-worker |
| multica-orchestrator | L1 协调 | 分派、wave 推进、负载均衡、阻塞清障、队列审计 | 自己动手写策略/代码 → 分派给对应层 |
| multica-ops | L1 运维 | infra 健康、launchd/systemd/隧道/代理、部署窗口 | 策略代码 → L3 |
| smark-decision-maker | L4 判决 | KEEP/KILL/PIVOT、SPEC 筛选、分支选择 | 执行类 → L3 |
| smark-signoff-proxy | L4 签核 | 证据链审核、签核/拒签 | 证据不齐 → 拒签退回（不补做） |
| knowledge-curator | L5 知识 | JOURNAL、digest、归档、issue-map 维护 | 代码执行 → L3 |
| persona-advisor | L5 表达 | 人话 verdict、文档润色、沟通稿 | 代码/审计 → multica-code / quant-analyst |

## 3. 标准协作路径

### 3.1 研究流（日循环主线）

```
quant-research-agent(调研) → SPEC 草案
  → smark-decision-maker(筛选, 10:00)
  → quant-researcher(实现, 单线程主线)
  → strategy-worker-1/2(回测执行, 可 swarm 扇出窗口)
  → multica-strategy(全管线: CV+fee shock+G门)
  → quant-analyst(独立复核, 有权挑战)
  → smark-signoff-proxy(证据签核)
  → smark-decision-maker(KEEP/KILL/PIVOT, 20:00)
  → knowledge-curator(ledger+JOURNAL) + persona-advisor(人话 verdict)
```

### 3.2 基建流（sprint/波次执行）

```
multica-orchestrator 按 issue-map 分派 wave
  → L3 workers 执行（任务卡自包含）
  → 验收命令全绿 → orchestrator 推进下一 wave
  → 代码类交付由 multica-code 复核后才算 done
```

### 3.3 阻塞与拒绝路径（纪律）

1. 任务卡有缺陷/前提不成立 → 执行者**不得自行改方向**：标 blocked +
   `[type=ESCALATE]` 评论说明缺什么 → orchestrator 修卡或重派
2. 任务不属于自己层级 → 标 blocked + 建议的正确接收者（参考 §2 表），不硬做
3. 同层冲突（两个 worker 撞同一文件）→ 后到者 blocked + 注明撞车文件 → orchestrator 串行化

### 3.4 升级路径

```
L3 执行中发现假设/数据问题 → quant-analyst 复核 → 属实 → quant-researcher 改主线
L2 出 verdict 候选          → smark-signoff-proxy → smark-decision-maker
L4 无法判决 / 超出授权       → [type=ESCALATE] → L0 人类
任何层发现基础设施故障        → multica-ops → 修不了 → ESCALATE L0
```

## 4. 配套规则

- **签核隔离**：L2/L3 不得给自己的产出签核（L4 独立）
- **并发上限**：quant-researcher=6、quant-research-agent=3、smark-*=3，其余=20；
  daemon 级 Mac=6 / .105=10。orchestrator 分派时不得超过目标 agent 的空余容量
- **misroute 复盘**：每次 blocked-转派在 epoch-retro 计数，周均 >3 次说明路由表要修
- 本文件变更 = 组织结构变更，需 L0 批准后进 AGENTS.md 摘要

---

## 5. 组织演进（2027–2036）

> 详细时间线见 `docs/plans/roadmap-2026-2036.md` §5。
> 原则：新角色在能力依赖解锁后才创建，不提前造岗位。

### 阶段一（2026–2027）：当前 14 agent

维持 §1 结构不变。quant-researcher 单线程承载策略主线；
multica-strategy + strategy-worker-1/2 跑验证管线；multica-code 负责管线/服务。

### 阶段二（2027–2029）：新增 2 角色

| 新角色 | 层 | 接单范围 | 拒绝（转谁） |
|---|---|---|---|
| `portfolio-manager` | L3 | 组合权重、相关性监控、组合级回撤预算、再平衡执行 | 单个策略验证 → multica-strategy；代码实现 → multica-code |
| `execution-researcher` | L2 | maker 执行、队列位置模型、撮合回放、成本模型 | 数据工程 → ops-worker-1；策略假设 → quant-researcher |

### 阶段三（2029–2036）：新增 2 角色

| 新角色 | 层 | 接单范围 | 拒绝（转谁） |
|---|---|---|---|
| `regime-detector` | L2 | 市场状态分类器、regime 自适应信号、组合权重 regime 调整 | 数据 → ops-worker-1；组合配置 → portfolio-manager |
| `meta-evolver` | L1 | 研究流程元优化：预检阈值、窗口数、派单策略、方法论改进假设 | 具体策略 → quant-researcher；基础设施 → multica-ops |

### 演进纪律

1. 新角色创建前必须有 SPEC：明确职责边界、接单范围、验收证据、与现有角色的交接面。
2. 新角色先以「临时 agent」跑 1 个季度，验收通过后再写入本文件和 agent instructions。
3. 不合并能力重叠的角色；旧角色在能力被完全替代后才标记 deprecated。
