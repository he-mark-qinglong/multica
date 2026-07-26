# 契约式 agent 协作方案 v1.0（contract-collab final）

> 阶段 3 产出（2026-07-26）。整合：
> `01-research-notes.md`（CNP/FIPA ACL/DbC/SagaLLM/A2A/LDP/ABC/AgentSLA 调研 + v1）、
> `02-gap-analysis-v2.md`（6 类失败复盘 + v2 修订）。
> 本文是可评审的完整方案：契约 schema、与现有组织的兼容、违约检测自动化、落地路线、置信度自评。
> **本文仅为设计文档，未改动任何系统代码/配置/agent instructions。**

---

## 0. 一句话定义

**agent 间协作从「互相递话」升级为「互相签约」**：每个跨 agent 任务由一份
机器可核验的契约（任务卡）定义——开工条件（preconditions）、交付物（deliverables）、
验收证据（acceptance_evidence）、违约处置（breach_policy）、签核链（signoff_chain）；
agent 自报一律到 `CLAIMED_DONE` 为止，机器核验（attestation）通过才算 `VERIFIED_DONE`。

理论骨架：DbC（Meyer）的 pre/post/invariant + blame assignment，
A2A 的 Task 生命周期状态机，LDP 的 claimed-vs-attested + typed failure semantics，
ABC 的 hard/soft violation 分级与 recovery 契约化，SagaLLM 的补偿式回滚。
（全部引用见 01-research-notes.md。）

---

## 1. 契约 schema（任务卡必填字段）

```yaml
# ===== 契约头（元数据）=====
contract_version: 1
task_id: SMA-36580                    # issue id，兼作 FIPA 意义下的 conversation-id
title: se_h3 全历史 7 窗 walk-forward 复跑
layer: L3                             # 目标层（对照 org 结构文档 §2 接单表）
issuer: multica-orchestrator          # caller（precondition 责任人）
assignee: strategy-worker-1           # callee（postcondition 责任人）
created_at: 2026-07-26T10:00+08
valid_until: 2026-07-27T10:00+08      # [v2] 派发时效；过期须 orchestrator 续期
timeout: 4h                           # 执行超时

# ===== 数据时点（防前视，F4）=====
data_as_of: 2026-07-25T23:59Z         # 本任务允许使用的最新数据时间戳
decision_time: 2026-07-26T20:00+08    # 结论要支持的决策时点；invariant: data_as_of ≤ decision_time

# ===== Preconditions（caller 义务；派发瞬间与开工前各验一次）=====
preconditions:
  - type: artifact_exists             # 机器可查
    ref: quant-loop/strategies/se_h3/SPEC.md@<SHA>   # [v2] 可变引用必须 pin
  - type: upstream_merged
    ref: main@f5db9e102
  - type: freshness                   # 防陈旧镜像乌龙（F5）
    subject: deploy_image
    pinned: app@sha256:<digest>
    max_age: 2h
  - type: metric_version              # [v2] 口径版本锁定（F3）
    ref: fee_shock@v2                 # v2 = per_trade_fraction 1.0 修正版

# ===== Deliverables（callee 义务：产出物清单）=====
deliverables:
  - type: git_branch
    pattern: agent/strategy-worker-1/SMA-36580
    remote: he-mark-qinglong/multica
  - type: file
    path: quant-loop/results/SMA-36580/metrics.json

# ===== Acceptance evidence（postcondition：done 的充要条件，全部机器可查）=====
acceptance_evidence:
  - check: git_remote_branch_exists
    ref: origin/agent/strategy-worker-1/SMA-36580
  - check: command_exit_zero
    run: pytest quant-loop/tests/test_se_h3.py -x
  - check: json_fields_present        # 缺字段=FAIL（沿用 W2-T1 gate 语义）
    ref: metrics.json
    fields: [oos_sharpe, ci_lower, fee_shock_60bps]

# ===== Breach policy（违约处置；recovery 契约化）=====
breach_policy:
  on_precondition_fail: escalate_to_issuer        # caller 的锅：退回派单方修卡
  on_postcondition_fail:
    action: redispatch_new_assignee               # 不原样重派同一 agent
    escalation_ladder:                            # [v2] 同一 task_id 累计计数
      - redispatch_new_assignee                   # 第 1 次
      - orchestrator_review                       # 第 2 次：审查任务卡可执行性
      - escalate_human                            # 第 3 次：冻结 + L0
  on_invariant_break: escalate_human
  on_timeout: cancel_with_NOOP_comment
  env_fail_check_first: true                      # [v2] 归因二段判定：先查环境再算 agent 的锅

# ===== Signoff chain（签核语义）=====
signoff_chain:
  required: [smark-signoff-proxy]
  prohibited_self_sign: true          # L2/L3 不签自己产出（沿用）
  requires_attestation_run: true      # [v2] PASS 必须引用 attestation run id（F2）

# ===== Invariants（全程成立；soft violation 计数，重复即升级 hard）=====
invariants:
  - comment_schema_compliant          # [type=...] 首行
  - no_layer_crossing                 # 接单范围表
  - data_cutoff_respected             # data_as_of ≤ decision_time

# ===== 口径引用（F3；registry 版本引用）=====
metric_refs: [fee_shock@v2, oos_sharpe@v1]
```

字段默认与减负：

- `valid_until` 默认 created_at+24h；`timeout` 默认 4h；`data_as_of` 对纯代码任务可填 `n/a`。
- 契约写在 **issue body 顶部的 ```contract yaml 代码块**（front-matter 风格）。
  理由：不动 server schema、agent 读 issue 即见契约、curator/审计可直接 grep；
  机器校验由工具解析该代码块完成。这是纯文档先行的关键决策（见 §5 路线）。
- orchestrator 派单时负责生成契约；worker 看到的任务卡 = issue body，零新界面。

## 2. 契约实例状态机

```
DRAFTED → ISSUED
ISSUED → ACCEPTED                      （precondition 全过，assignee 接单）
ISSUED → REJECTED(blocked)             （卡缺陷/越层 → blocked + ESCALATE，沿用 rejection discipline）
ISSUED → EXPIRED                       （过 valid_until 未派发；须 orchestrator 续期重验）
ACCEPTED → IN_PROGRESS → CLAIMED_DONE  （agent 自报完成；自报到此为止）
CLAIMED_DONE → ATTESTING → VERIFIED_DONE     （机器重跑 acceptance_evidence 全过）
CLAIMED_DONE → ATTESTING → BREACH-POST       （核验失败 → breach_policy 阶梯）
ACCEPTED → BREACH-PRE                  （开工前重验 precondition 失败 → 退回 issuer）
IN_PROGRESS → ENV-FAIL                 （工作区 GC/链路断 → 自愈 + 补偿重跑，不扣 agent）
VERIFIED_DONE → SIGNOFF_PENDING → SIGNED         （SIGNOFF 附 attestation run id）
VERIFIED_DONE → SIGNOFF_PENDING → SIGNOFF_REJECTED → CLAIMED_DONE（补证据重来）
任意 → TIMEOUT → CANCELLED(NOOP)
```

状态与 issue status 的映射（不动现有 status 枚举）：
`CLAIMED_DONE` = agent 评论 STATUS 报完成但 issue 仍在 `in_progress`；
`VERIFIED_DONE` 之后才允许 agent/orchestrator 把 issue 置 `done`。
**即：issue 状态机的 done 迁移权从「agent 自觉」收归「attestation 通过」。**

## 3. 与现有体系的关系（兼容，不推翻）

| 现有机制 | 契约体系中的位置 | 变化 |
|---|---|---|
| L0–L5 分层 + 接单范围表 | 路由层；契约 `layer/assignee` 字段引用它 | 不变；misroute 复盘照旧 |
| 评论 schema 8 类 `[type=...]` | 通信层 performative 集合 | 保留；SIGNOFF 评论扩展一个必填字段 `attestation_run=<id>`；新增可选 type `BREACH`（违约事件记录，若不想加 type 可用 STATUS+固定前缀过渡） |
| dispatch landing protocol（push 才算完成） | 被吸收为 `acceptance_evidence.git_remote_branch_exists` | 从「文字纪律」升级为「机器核验项」，概念不变 |
| 签核隔离 | `signoff_chain.prohibited_self_sign` | 不变 |
| rejection discipline | `ISSUED → REJECTED(blocked)` 转移 | 不变；blocked 理由建议结构化（缺什么/转给谁），先约定俗成 |
| 验证管线（预注册/walk-forward/CV/fee shock/G 门） | 研究流任务的 acceptance_evidence 领域模板 | 不变；口径版本化（metric registry）是其补强 |
| cycle-46 家族 exhaustion | 领域 invariant，由 SPEC 自带 | 不进通用 schema |
| Idle Agent Dispatcher | 派发器 | 增加派发前 precondition 重验 + valid_until + 接单速率上限（F6） |
| Evidence Review Gate autopilot | 人工时代的 attestor | 其职责被 ATTESTING 吸收，改造为 attestation runner（见 §4） |
| 单线程主线纪律（L2） | 契约粒度边界 | **契约只管交接面**：SPEC/策略实现/判决候选这些主线产出物跨 agent 交接时立契约；主线内部推理不契约化 |

## 4. 违约检测与自动处理

### 4.1 从 no-push detector 泛化到 contract-breach detector

现有认知「no-push 才算假完成」是 attestation 的一个检查项。泛化架构：

```
contract-breach-detector（daemon 侧 python runner，或 Evidence Review Gate 改造）
  输入：issue body 的 contract 块 + issue 评论流
  触发：assignee 发 STATUS 完成评论 / timeout 到点 / 定时 sweep
  动作：
    1. 解析契约 → 逐项执行 acceptance_evidence 的机器检查
       git_remote_branch_exists → git ls-remote
       command_exit_zero          → 在 repo cache clone 上跑验收命令（沙箱、限时）
       json_fields_present        → 拉产物文件校验
       freshness/pinned           → 比对 digest/SHA/ts
       data_cutoff                → 比对 data_as_of vs decision_time
    2. 全过 → 写 attestation run（run id + 逐项结果 + ts），
       issue 评论 [type=EVIDENCE] attestation_run=<id> PASSED n/n，
       允许 done 迁移
    3. 有 FAIL → env_fail_check_first：
       环境探针（工作区存在性/磁盘/隧道/daemon）异常 → ENV-FAIL：
         触发自愈 + 补偿重跑，不记 agent 违约
       环境正常 → BREACH-POST：记 assignee 违约档案，
         按 escalation_ladder 处置（换人重派 / orchestrator 审查 / 冻结+人工）
    4. precondition 类失败 → BREACH-PRE：退回 issuer，misroute 计数
```

### 4.2 违约分类终表（typed failure semantics）

| 类型 | 检测者 | 责任方 | 自动处置 | hard/soft |
|---|---|---|---|---|
| BREACH-PRE | 派发前/开工前重验 | issuer (L1) | 退回修卡，misroute 计数 | hard（阻断开工） |
| BREACH-POST | ATTESTING | assignee | 升级阶梯（§1 breach_policy） | hard |
| BREACH-EVID | SIGNOFF 校验（attestation_run 缺失/引用不存在） | 签核/执行方 | PASS 作废 + ESCALATE | hard |
| BREACH-INV | comment-janitor / 静态检查 | 违规方 | 计数 → epoch-retro；同 issue 3 次升级 hard | soft→hard |
| ENV-FAIL | 环境探针 | 基础设施 | 自愈 + 补偿重跑 | n/a（不归因 agent） |
| STALE-EVIDENCE | metric registry bump 广播 | 上游定义 owner | 旧结论标 STALE 进 retro 复核队列 | soft |

### 4.3 违约档案（agent trust record）

- 每 agent 一份 append-only 违约档案（issue 形式即可，每 agent 一个 ledger issue）。
- **v1.0 只记录不参与路由**——避免过早引入博弈；数据积累一个 epoch 周期后再评估
  是否做「同能力优先无违约者」的轻量加权。（LDP 的 attested identity 是未来的完整形态。）

## 5. 分阶段落地路线

原则：**先文档与流程，后工具，最后 server/daemon 改造；每阶段独立可停。**

| Phase | 内容 | 改动面 | 依赖 |
|---|---|---|---|
| **P0 文档先行**（1 天，零代码） | ① 契约模板（§1 schema）进 `docs/templates/`；② orchestrator 派单纪律改为「无契约不派单」（写进 AGENTS.md/组织文档摘要）；③ 违约分类表进 AGENTS.md；④ SIGNOFF 评论约定 attestation 引用 | 纯文档 | 无 |
| **P1 人工 attestation**（2-3 天，零/轻代码） | ① Evidence Review Gate autopilot 的 prompt 改为「逐项核验 acceptance_evidence 再签」；② 写一个 CLI 小工具 `contract_check.py`（解析 issue body 契约块 + 跑 git ls-remote / 文件存在性 / json 字段检查，纯 python，单文件）供签核者与 orchestrator 手动跑；③ dispatcher 人工遵守 valid_until 与接单上限 | 新增 1 个独立脚本 + autopilot prompt | P0 |
| **P2 机器 attestation 半自动**（约 1 周） | ① `contract_check.py` 扩展 command_exit_zero（repo cache clone 上跑验收命令）；② autopilot 定时 sweep「有完成评论但未 attested」的 issue，自动跑检查并发 EVIDENCE 评论（attestation run）；③ issue done 迁移约定：无 attestation 评论的 done 由 janitor 自动回退到 in_progress | autopilot + 脚本；不改 server | P1 |
| **P3 server/daemon 集成**（需评估后立项） | ① issue schema 增 `contract` / `attestation_run` 一等字段（migration）；② ATTESTING 态收进 server：done 迁移 API 服务端强制校验 attestation（对应既有 terminal-status guard、gate 严格化的同款思路，落点在 `server/internal/handler/issue.go` 与 `internal/gate/`）；③ dispatcher 服务端强制 valid_until + 接单速率（落点 `internal/scheduler/`、`internal/service/task.go`）；④ metric registry + STALE-EVIDENCE 广播（`quant-loop/_shared/` + curator 流程） | Go server + DB migration | P2 稳定运行 ≥1 epoch |
| **P4 信任档案与路由加权**（可选，观望） | 违约档案参与轻量路由决策 | scheduler | ≥2 epoch 数据 |

**明确不做的**：不引入竞价/bid（角色固定，CNP 只借任务公告结构）；不改评论 schema 的 8 类 type
（只加字段约定）；不对 L2 主线内部推理立契约；不在 v1.0 做路由加权。

## 6. 置信度自评

**总体把握：中偏高（文档/流程层「高」，机器化层「中」，server 集成层「中偏低」）。**

- **高**：P0/P1（契约模板 + 派单纪律 + 人工 attestation + CLI 检查工具）。
  这些是纪律的重新表述与单文件脚本，模式全部有先例（落地协议、评论 schema、gate 严格化
  都是先文字后工具并奏效）。失败风险主要是「agent 不写契约/写了不填全」——
  用 orchestrator「无契约不派单」单点把关可控。
- **中**：P2（自动 sweep + done 回退）。风险在 attestation 命令的沙箱执行
  （在 repo cache 上跑任意验收命令有安全与副作用边界问题）和 janitor 回退 done 的
  误伤率（agent 合法先 done 后补证据的时序）。
- **中偏低**：P3（server/daemon 改造）。我对 multica Go server 只有目录级勘察
  （`internal/gate/`、`internal/scheduler/`、`internal/service/task.go`、`handler/issue.go` 存在
  且职责匹配），未读实现细节；DB migration 与 daemon 调度逻辑的侵入面需要专门评估。

**最大的 3 个不确定点：**

1. **契约遵守率（行为层，最不可控）**：14 个 agent 全跑 caocao-m3，小模型对长契约模板的
   遵循度未经验证。若 agent 频繁漏字段/填假字段，P0 就会卡在日常扯皮。
   缓解：契约字段大幅默认值化 + orchestrator 单点生成契约（agent 不自己写）+ 先用 3 天试点观察。
   ——这也是我建议 P0 先跑、拿到遵守率数据再决定 P2/P3 投入的原因。
2. **attestation 命令的安全执行环境**：`command_exit_zero` 要在服务端/共享机上跑
   agent 任务卡里的命令，需要隔离（repo cache clone + 超时 + 资源限制 + 禁网？），
   否则验收机制本身变成攻击面/事故面。沙箱方案未设计，是 P2 的技术核心风险。
3. **metric registry 的治理成本**：口径版本化防 F3 的收益巨大（200× bug 类事故），
   但 registry 的维护者、bump 时 STALE-EVIDENCE 回溯的覆盖面（历史结论全标 STALE 会
   造成 retro 队列爆炸）需要真实运行数据来定阈值。设计上有方案，运营节奏是未知数。

**给审批者的建议**：批准 P0+P1 立即执行（零代码、可回滚、直接针对 8 起假完成与 2 起假签核）；
P2 在 P0 试点 ≥3 天且契约遵守率可接受后再批；P3 单独立项评估，不建议与 P0/P1 捆绑批准。
