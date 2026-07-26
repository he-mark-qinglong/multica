# 契约式 agent 协作 — 协作历史复盘 + gap analysis + v2

> 阶段 2 产出（2026-07-26）。输入：6 类真实失败案例、AGENTS.md 纪律演变、
> `agent-org-structure-2026-07-25.md`、`multica-quant-permanent-loop-2026-07-25.md`、
> `knowledge/curator/2026-07-18-knowledge-snapshot.md`、infra-sprint issue-map。
> 目标：v1 契约条款 ↔ 已发生失败的逐条映射，并给出 v2 修订。
>
> 备注：任务简报中提到的 `docs/plans/dependency-metadata-schema.md` 在仓库中**不存在**
> （Glob/Grep 均无匹配），本复盘未能引用该文档；若它在他处，后续应补读。

---

## 1. 纪律演变时间线（从文档考古）

| 日期 | 机制 | 针对的痛点 |
|---|---|---|
| 2026-07-19 | 评论 schema `[type=...]` 8 类 | 评论不可检索、决策无出处 |
| 2026-07-25 | 组织分层 L0–L5 + 接单范围表 + rejection discipline | Wave-0 两起 misroute（agent 收到越层任务） |
| 2026-07-25 | dispatch landing protocol（push 分支才算完成） | **8 起假完成**、工作区 GC 毁尸灭迹 |
| 2026-07-25 | 签核隔离（L2/L3 不签自己产出） | 研究者自签自利 |
| 2026-07-25 | comment-wake guard（终态 issue 评论不唤醒） | 终态 issue 被评论复活扰动 |
| 2026-07-25 | gate 严格化（缺字段=FAIL，不再 skip-pass） | 缺 metric 虚过 gate |
| 2026-07-26 | se_h3 fee shock 200× 口径修正 + 家族级 KILL | 口径 bug 沿代传递 |

观察：**每一条纪律都是一次事故后的补丁，且全部是「文字纪律」**——写进 instructions/AGENTS.md
靠 agent 自觉执行。6 类失败里凡是能机器检测的（no-push、缺字段 gate），补上检测器后才真正止住；
凡是仍靠自觉的（证据核验、口径一致），就还在出事。这直接验证 v1 原则 1「attested, not claimed」。

## 2. 六类失败的 case study 复盘

### F1 假完成（8 起，含同一任务连续 4 次）

- 事实：agent 报 done 但未 push，工作区（`~/multica_workspaces/`，~1h GC）销毁后成果蒸发。
  W1-T8/SMA-36467、W1-T11/SMA-36458 均因此重派；教训原文「m3 'completed' ≠ landed」。
- 机制解剖：这是 **provenance paradox 的现场版**——调度器把 agent 的自报当事实。
  且「同一任务连续 4 次假完成」说明处置策略错误：违约后**原样重派同一 agent**，
  没有违约计数、没有升级阶梯。
- DbC 视角：postcondition（分支存在于 remote）不成立 = **callee 违约（BREACH-POST）**；
  但工作区 GC 属于 **ENV-FAIL**——两者要分开归因，否则会把环境锅算到 agent 头上，
  或把 agent 锅当成「运气不好重跑就行」。

### F2 Evidence Gate 两次错误签核（引用不存在的分支签 PASS）

- 事实：签核方引用了一个不存在的分支作为证据并给出 PASS。
- 机制解剖：签核契约只规定了「谁签」（隔离），没规定「签之前必须核验什么」。
  SIGNOFF 评论指向的证据没有被 attest。签核者本身成了 claimed-not-attested 的受害者+加害者。
- 这是 F1 的对偶：**执行侧假完成**与**签核侧假核验**是同一类病——证据引用不落地。

### F3 口径 bug 沿代传递（fee shock per_trade_fraction 200×）

- 事实：`per_trade_fraction` 应为 1.0 却写成 0.005，费冲击被低估 200 倍，
  沿用几代策略（H1「fee-robust」结论被同一 bug 伪造），人工审计才发现（SMA-36566）。
- 机制解剖：**上游 guarantee 错了，下游盲信**——assume-guarantee 链缺一致性检查点。
  单任务契约防不住它：每个任务的 postcondition 都可能各自通过，但**共享定义的口径**错了。
  需要跨契约层的东西：**指标定义注册表（definition registry）+ 版本引用 + 独立审计重算**。
- 同类隐患：server gate 的 skip-pass bug（`gate.go:115-117,131`）也是共享组件错误批量污染下游结论。

### F4 回测前视偏差（用当天全量数据"预测"当天早晨）

- 事实：研究员计算指标时用了决策时点之后的数据，审计层（quant-analyst）抓出。
- 机制解剖：postcondition 只检查「指标值存在/达标」，不检查**指标是怎么算的**。
  防前视需要契约声明 **data cutoff precondition**：`data_as_of ≤ decision_time`，
  且验证管线把它作为不变量（walk-forward 天然满足，ad-hoc 分析必须显式声明）。
- 这类违约单任务内机器可查（检查数据时间戳 vs 决策时间戳），但需要契约里有字段可查。

### F5 部署 gatekeeper 拿陈旧镜像当 canonical 预审（乌龙 blocked）

- 事实：gatekeeper 用旧镜像预审，得出一个本不该有的 blocked 结论。
- 机制解剖：precondition 里引用了**可变对象**（latest 镜像）而没有**固定版本**。
  契约条款：凡引用可变产物的条件必须 pin 版本/摘要/时间戳 + 声明 `max_age`。
  乌龙 blocked 的代价虽低于乌龙 PASS，但消耗协调资源、污染 misroute 统计。

### F6 旧任务洪流（19 个过期任务批量塞给一个 agent）

- 事实：Idle Agent Dispatcher 扫描空闲 agent 时，把积压的过期任务批量分派。
- 机制解剖：**派发侧没有时效契约**。issue 创建时有效，几天后语义已过期
  （依赖的 main 已前进、窗口已过、SPEC 池已刷新），但 dispatcher 没有 TTL 概念。
  这是 BREACH-PRE 的批量形态：precondition（任务仍然有意义）在派发瞬间未重新验证。
  另外缺少**批量准入控制**（admission control）：单 agent 单位时间接单应有上限。

## 3. v1 契约条款 ↔ 失败案例映射表

| 失败 | v1 已有覆盖 | 残余 gap | v2 增补条款 |
|---|---|---|---|
| F1 假完成 | `acceptance_evidence.git_remote_branch_exists` + ATTESTING 态 | 无违约升级阶梯；ENV-FAIL/BREACH-POST 归因混合 | §4.1 违约升级阶梯（N 次后换人+人工）；归因二段判定（先查环境再查 agent） |
| F2 假签核 | `evidence_must_attest: true` | 签核动作本身没有 attest 凭证字段 | §4.2 SIGNOFF 必须引用 attestation run id；无 run id 的 PASS 一律无效 |
| F3 口径传递 | 无（v1 留白里提了链式检查没机制） | 共享定义无版本化；下游无义务声明口径版本 | §4.3 metric definition registry + 契约引用 `metric@version` + 上游版本 bump 触发下游 invalidate |
| F4 前视偏差 | 无 | 契约无数据时点字段 | §4.4 `data_as_of` 必填 + invariant `data_as_of ≤ decision_time` 机器可查 |
| F5 陈旧镜像 | `preconditions.freshness` 雏形 | 「可变引用必须 pin」未成通则 | §4.5 可变引用 pin 通则：branch→SHA、image→digest、dataset→snapshot ts |
| F6 任务洪流 | `timeout` 字段 | 派发时效与批量准入缺失 | §4.6 契约 TTL（`valid_until`）+ dispatcher 派发前重验 precondition + 单 agent 接单速率上限 |

反向校验：**现有纪律里哪些是契约不需要管的**——评论 schema 8 类 performative（保留为通信层）、
rejection discipline（保留为 ACCEPTED→REJECTED 转移的语义）、签核隔离（保留为 signoff_chain 约束）、
cycle-46 家族 exhaustion（领域规则，属业务 invariant，不进通用契约 schema，由策略域 SPEC 自带）。

## 4. v2 修订（相对 v1 的变更）

### 4.1 违约升级阶梯（breach escalation ladder）

```
BREACH-POST 第 1 次：自动重派新 assignee（不原样重派同一 agent），记违约事件
BREACH-POST 第 2 次（同一 task_id 累计）：issue 升 priority + 通知 orchestrator 审查任务卡是否可执行
BREACH-POST 第 3 次：ESCALATE L0 人工；该 task 冻结，不再自动重派
归因二段判定：先查环境（工作区是否还在、模型链路是否通、磁盘是否满）
  → 环境异常记 ENV-FAIL（自愈+补偿重跑，不扣 agent 信用）
  → 环境正常才记 BREACH-POST（计入 assignee 违约档案）
```

### 4.2 签核 attestation

- server/daemon 在 `CLAIMED_DONE → VERIFIED_DONE` 的 ATTESTING 态重跑 `acceptance_evidence`
  全部机器检查项，产出 **attestation run**（带 run id、逐项结果、时间戳）。
- SIGNOFF 评论 schema 扩展：必须含 `attestation_run=<id>`；无 id 的 PASS 视为
  soft violation（comment-janitor 可自动标 OFFSPEC）。
- 签核者可追加人工判断，但不得覆盖机器 FAIL 项（机器 FAIL → 只能拒签或退回）。

### 4.3 指标定义注册表（metric definition registry）

- 新增共享资产 `quant-loop/_shared/metric_registry.yaml`（示意）：
  每个指标/口径定义有 `name@version` + 实现指针 + 变更记录。
  例：`fee_shock.per_trade_fraction@v2 = 1.0（2026-07-26 修正，v1=0.005 为 200× bug）`。
- 契约的 `acceptance_evidence` 引用指标必须带版本：`fee_shock_60bps@v2`。
- **上游 bump 触发下游 invalidate**：registry 版本变更时，自动列出所有引用旧版本的
  已 PASS 结论，标 `STALE-EVIDENCE` 进 epoch-retro 复核队列。
  （若落地此条，2026-07-26 的 fee 修正就不会再靠人肉追溯 H1 旧结论。）

### 4.4 数据时点字段

- 契约新增必填：`data_as_of`（分析/回测所用数据的最新时间戳）。
- invariant（机器可查）：`data_as_of ≤ decision_time`，其中 `decision_time` 为
  该结论要支持的决策时点（SPEC 注册时间/判决时间）。
- walk-forward 标准管线天然满足；任何 ad-hoc 指标复核、gate 挑战必须显式填此字段。

### 4.5 可变引用 pin 通则

契约及证据评论中的一切引用必须是不可变的：

| 引用对象 | 错误写法 | 正确写法 |
|---|---|---|
| 分支 | `agent/worker-1/SMA-123` | `agent/worker-1/SMA-123@<full SHA>` |
| 容器镜像 | `app:latest` | `app@sha256:<digest>` |
| 数据集 | `klines/BTC_1m` | `klines/BTC_1m@snapshot 2026-07-26T00:00Z` |
| 指标口径 | `fee_shock` | `fee_shock@v2` |

precondition 检查器对未 pin 的引用直接判 precondition 不满足（开工前拦截，而非事后乌龙）。

### 4.6 派发时效与准入控制

- 契约新增 `valid_until`（默认 issue 创建 +24h）；过期 issue 不得自动派发，
  须 orchestrator 重验 precondition 后显式续期（一条 NUDGE 评论刷新 `valid_until`）。
- dispatcher 派发瞬间必须**重验全部 precondition**（不是创建时验一次就永久有效）。
- 单 agent 准入：`max_new_tasks_per_hour`（worker=4，research 主线=1，与既有并发上限正交）。

### 4.7 v2 schema 增量（相对 v1 的新字段汇总）

```yaml
valid_until: 2026-07-27T10:00+08        # F6
data_as_of: 2026-07-25T23:59Z           # F4
decision_time: 2026-07-26T20:00+08      # F4
attestation:                            # F1/F2
  required: true
  run_id: null                          # ATTESTING 态回填
signoff:
  requires_attestation_run: true        # F2
breach_policy:
  escalation_ladder: [redispatch_new, orchestrator_review, escalate_human]  # F1
metric_refs: [fee_shock@v2, oos_sharpe@v1]  # F3
```

## 5. v2 遗留问题（带入阶段 3 完整方案解决）

1. ATTESTING 的执行主体：server（Go）还是 daemon 侧 python runner？影响落地分期。
2. metric registry 的 invalidate 广播机制（谁扫引用、怎么标 STALE-EVIDENCE）。
3. 契约存放位置与现有 issue body 的兼容（front-matter vs 首条结构化评论）。
4. 违约档案（agent trust record）只记录不参与路由？还是轻量参与（同能力下优先无违约者）？
5. L2 单线程主线内部不契约化，但主线**产出物**（SPEC/策略实现）进入交接面时必须立契约——边界怎么画。
