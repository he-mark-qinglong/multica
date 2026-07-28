# 契约式 agent 协作 — 调研笔记 + v1 设计

> 阶段 1 产出（2026-07-26）。调研多 agent 契约协作的理论与近 2 年实践，
> 并给出适配 multica 14-agent 组织的交互契约 v1。论文引用均附真实 URL。

---

## 1. 经典理论：从 Contract Net 到 Design by Contract

### 1.1 Contract Net Protocol (CNP, Smith 1980)

- 原文：Reid G. Smith, "The Contract Net Protocol: High-Level Communication and Control in a
  Distributed Problem Solver", *IEEE Transactions on Computers*, 1980.
  [IEEE Xplore (DOI 10.1109/TC.1980.1675516)](https://ieeexplore.ieee.org/document/1675516)
- 四阶段：**announce (CFP) → bid → award → execute & report**。Manager 广播任务，
  候选 agent 按能力与负载投标，manager 择优授标，中标者执行并回报。
- 对我们的映射：multica-orchestrator 就是 manager；issue 即 task announcement；
  当前系统是「静态指派」（orchestrator 直接点名 assignee），没有 bid 环节。
  CNP 的启示不在竞价本身，而在**任务公告的结构化**——CFP 必须自带任务规格，
  投标者据此判断接不接。我们缺的正是这一层（任务卡质量参差）。
- 现代变体与教训：[Negotiation Protocol Comparison (MSSANZ 2015)](https://www.mssanz.org.au/modsim2015/C6/noack.pdf)
  指出 CNP 通信开销低，适合低风险环境；[Improved CNP (MDPI 2019)](https://www.mdpi.com/1999-4893/12/4/70)
  用动态授信降低广播流量。生产视角：[7 Multi-Agent Orchestration Patterns](https://growthengineer.ai/blog/multi-agent-orchestration-patterns)
  强调 CNP 适合「capability matching 优于静态路由」的场景。
- **判断**：我们 14 个 agent 角色固定（L1-L5），路由表已由组织文档静态定义，
  不需要竞价；需要借的是「结构化任务公告 + 显式接受/拒绝」这一半。

### 1.2 Agent Communication Languages：FIPA ACL / KQML

- KQML：Finin et al., "KQML as an Agent Communication Language" (1994)。
- FIPA ACL 规范：[FIPA Communicative Act Library (SC00061)](http://www.fipa.org/specs/fipa00061/)；
  FIPA 标准化了 CNP 交互协议 [FIPA Contract Net IP (SC00029)](http://www.fipa.org/specs/fipa00029/)。
- 核心概念：**performative**（消息类型）+ content + ontology + conversation-id +
  reply-with/in-reply-to（会话追踪）。约 20 种 performative，分五类：
  信息传递（inform）、信息查询（query-if/ref）、协商（cfp/propose/accept-proposal/reject-proposal）、
  执行（request/agree/refuse）、错误处理（failure/not-understood）。
- 参考教学材料：[UPC SMA slides](https://www.cs.upc.edu/~jvazquez/teaching/sma-upc/slides/sma02b-Communication.pdf)。
- 对我们的映射：我们的 issue 评论 schema `[type=STATUS/DECISION/EVIDENCE/KILL/ESCALATE/SIGNOFF/NUDGE/NOOP]`
  **就是一套 performative 集合**——这是个好底子。FIPA 教会我们三件事 v1 要补：
  1. **conversation-id / in-reply-to**：评论要能挂到契约实例上（当前靠人肉对齐 issue + 时间戳）；
  2. **failure / not-understood 是协议一等公民**：我们的 blocked 状态已有雏形，
     但拒绝理由没有结构化（缺「缺什么、转给谁」的机器可读字段）；
  3. **交互协议 = 状态机**：FIPA 把 CNP 画成有限状态图，对话不合法转移可被检测。
     我们需要契约实例的状态机（见 §3 v1 设计）。

### 1.3 Design by Contract (DbC, Meyer)

- Bertrand Meyer, *Object-Oriented Software Construction* (1988)；
  教学版：[DbC chapter (ETH)](https://se.inf.ethz.ch/~meyer/publications/old/dbc_chapter.pdf)。
- 三要素：**precondition**（调用方义务）、**postcondition**（被调方义务）、
  **invariant**（全程成立的条件）。契约 = 双方义务与收益的对称约定。
- 强规格的成本收益研究：[Polikarpova et al., "What Good Are Strong Specifications?" (arXiv:1208.3337)](https://arxiv.org/pdf/1208.3337)——
  增量式写规格成本可接受，前提是能换来**可机器检查**的验收。
- **关键映射**（这是本方案的理论骨架）：
  | DbC | agent 任务契约 |
  |---|---|
  | precondition | 任务开工前必须成立的条件（数据可用、上游产物已落地、镜像新鲜） |
  | postcondition | 任务完成时必须成立的条件（分支已 push、验收命令全绿、产物路径存在） |
  | invariant | 全程纪律（评论 schema、签核隔离、不越层） |
  | 契约违约 (contract violation) | 三类责任归属：**调用方违约**（precondition 不满足还派单）、
    **被调方违约**（声称完成但 postcondition 不成立=假完成）、**环境违约**（invariant 被基础设施破坏=工作区 GC） |

  DbC 最锋利的一点：**违约有明确的责怪对象**（Meyer 原话：precondition 违约为 caller 的 bug，
  postcondition 违约为 callee 的 bug）。这直接解决我们「假完成后责任不清、重复重派同一 agent」的问题。

---

## 2. 近 2 年 LLM agent 协作协议（2024–2026）

### 2.1 工业协议：MCP / A2A / ACP

- Google **A2A (Agent-to-Agent)**：[A2A 规范与生态](https://github.com/a2aproject/A2A)；
  综述：[A Survey of Agent Interoperability Protocols (arXiv:2505.02279)](https://arxiv.org/html/2505.02279v1)。
  四核心：**Agent Card**（能力自描述，v1.0 起加密签名）、**Task**（带生命周期的持久工作单元：
  `submitted → working → input-required/auth-required → completed/failed/canceled/rejected`）、
  **Message**（交互载荷）、**Artifact**（任务产出物，与任务状态分离）。
- **对我们的启示**：A2A 的 Task 状态机几乎可以直接借来做契约实例生命周期；
  **Artifact 与「completed」状态分离**的设计尤其重要——A2A 里 completed 必须附 artifact，
  对应我们「done 必须附 pushed 分支」的落地协议，即「状态 + 证据」不可分。
- MCP 是 agent↔tool 纵向协议，与本方案正交，不展开。

### 2.2 学术框架：把契约正式化到 LLM agent

- **Agent Behavioral Contracts (ABC)**：Bhardwaj,
  ["Agent Behavioral Contracts: Design-by-Contract for Autonomous AI Agents" (arXiv:2602.22302)](https://arxiv.org/abs/2602.22302)。
  契约 `C = (P, I, G, R)` = Preconditions + Invariants + **Governance policies** + **Recovery mechanisms**；
  定义 (p, δ, k)-satisfaction 处理 LLM 非确定性；证明 Drift Bounds Theorem（恢复率 γ > 漂移率 α 时
  漂移有界 D* = α/γ）；实现 AgentAssert 运行时库 + AgentContractBench（200 场景 × 7 模型）。
  实验：有契约 agent 每会话多检出 5.2–6.8 个软违约，硬约束合规 88–100%，开销 <10ms/action。
  **启示**：① Recovery 必须是契约一等公民——违约不是终点，恢复策略要写进契约；
  ② 区分 hard/soft violation（hard=阻断，soft=计数+纠偏），避免把 agent 管死。
- **Provenance Paradox / LDP delegation contracts**：Prakash,
  [arXiv:2603.18043](https://arxiv.org/abs/2603.18043)（配套 [LDP arXiv:2603.08852](https://arxiv.org/abs/2603.08852)，
  代码 [github.com/sunilp/ldp-protocol](https://github.com/sunilp/ldp-protocol)）。
  **核心发现与我们直接相关**：当 delegate 可以虚报质量时，按自报质量路由会**系统性选中最差执行者，
  差于随机**（simulated 0.55 vs 0.68；真实 Claude 8.90 vs 9.30）。解法三件套：
  ① **delegation contract**——显式 objectives、budgets、failure policies 约束授权边界；
  ② **claimed-vs-attested identity**——区分自报与第三方核验的能力；
  ③ **typed failure semantics**——失败分类（可重试/不可重试/违约）驱动自动恢复。
  **映射**：我们的「假完成」就是虚报质量的极端形式；「8 起假完成 + 同任务连续 4 次假完成」
  正是 provenance paradox 的现场版——调度器相信了 agent 的自报。**结论：任何 completion claim
  必须可 attested（机器核验），自报一律不算数。**
- **SagaLLM**：Chang, ["SagaLLM: Context Management, Validation, and Transaction Guarantees for
  Multi-Agent LLM Planning" (arXiv:2503.11951)](https://arxiv.org/abs/2503.11951)。
  把 Saga 分布式事务模型引入多 agent 规划：**compensating rollback**（子任务失败时按逆序补偿）、
  独立验证 agent、松弛 ACID（保最终一致而非原子性）。
  **映射**：我们的 wave 执行 = 分布式事务；「agent 分支累计携带多个任务的 commit，一次 merge 引入多任务」
  就是缺乏事务边界的表现。契约应声明**补偿动作**（违约时是重派、回滚 merge、还是降级为人工）。
- **AgentSLA**：["AgentSLA: Towards a Service Level Agreement for AI Agents" (arXiv:2511.02885)](https://arxiv.org/html/2511.02885v1)。
  把 SLA/SLO 搬到 agent 服务：SLO = 指标 + 阈值 + 评估窗口。
  **映射**：契约的 acceptance_evidence 本质就是 SLO 集合（Sharpe ≥ 阈值、CI 下界、fee shock 存活）。
- **DeepMind "Intelligent AI Delegation"**：Tomašev et al.,
  [arXiv:2602.11865](https://arxiv.org/html/2602.11865v1)。delegation 的系统性框架：
  信任校准、能力评估、人类保留控制的边界。支撑我们 L0 人类裁决的保留设计。
- **运行时委托安全形式化**：[arXiv:2604.27358](https://arxiv.org/html/2604.27358v1)
  （accountability propagation：错误沿委托链归因到具体 agent）。与 DbC 的 blame assignment 互补。
- **HyDRA / 概率契约 / assume-guarantee**：[arXiv:2507.15917](https://arxiv.org/pdf/2507.15917)
  （DbC + probabilistic contracts + [Pacti](https://arxiv.org/abs/2305.03060) assume-guarantee 组合验证）。
  assume-guarantee 的组合视角：每个 agent 的契约 = 「我保证 Q，前提是你给我 P」；
  链式组合时要验证下游的 assume 被上游的 guarantee 覆盖。
  **映射**：研究流（调研→SPEC→实现→回测→签核）就是一条 assume-guarantee 链；
  「口径 bug 沿代传递」= 上游 guarantee 错了而下游盲信，缺的是**跨契约的一致性检查点**。
- **LLM MAS 协作综述**：[Multi-Agent Collaboration Mechanisms: A Survey of LLMs (arXiv:2501.06322)](https://arxiv.org/html/2501.06322v1)；
  编排综述：[LLM-Based Multi-Agent Orchestration Survey (preprints 202604.2147)](https://www.preprints.org/manuscript/202604.2147)。

### 2.3 框架实践：AutoGen / CrewAI / MetaGPT

- **MetaGPT** ([arXiv:2308.00352](https://arxiv.org/abs/2308.00352))：把 SOP 编码进多 agent 协作，
  角色间传递**结构化文档**（PRD/设计稿）而非裸对话——「structured outputs reduce hallucination
  propagation」。最接近我们「SPEC 预注册 + 任务卡」的思路。
- **AutoGen** ([arXiv:2308.08155](https://arxiv.org/abs/2308.08155))：对话驱动，
  conversable agent 抽象；灵活但输出不可控、成本高（对话税 30–50%）。
- **CrewAI**：role + task + expected_output 三件套；Task 自带 `expected_output` 字段
  是最原始的 postcondition 声明。
- **判断**：框架层的共识是「对话不是协议」。我们已有的 issue 评论 schema + 任务卡
  已经比纯对话框架强；要向 MetaGPT 学的是**交接物的结构化 schema 化**，向 A2A 学的是**任务生命周期状态机**，
  向 ABC/LDP 学的是**违约语义与 attestation**。

---

## 3. v1 设计：multica agent 交互契约

### 3.1 设计原则（从调研提炼）

1. **Attested, not claimed**（LDP 教训）：完成、能力、证据三类声明一律以机器可核验为准；
   agent 自报只作为待核验线索。
2. **Blame by construction**（DbC）：每个违约点都能归因到 caller / callee / environment 三者之一。
3. **Recovery is contractual**（ABC/SagaLLM）：契约自带违约处置策略，违约≠死锁。
4. **Hard vs soft violations**（ABC）：阻断性违约与计数性违约分级，防止过度管制。
5. **复用现有 performatives**：评论 schema 不推翻，只扩展结构化字段。
6. **单线程主线不契约化碎裂**（smark 并行纪律）：契约约束的是**交接面**，
   不干预 L2 主线内部推理——契约粒度 = 任务卡/issue，不是 prompt 级。

### 3.2 契约 schema v1（任务卡必填字段）

```yaml
contract_version: 1
task_id: SMA-XXXXX                  # issue id，兼作 conversation-id
layer: L3                           # 目标层
assignee: strategy-worker-1         # 静态路由结果（无竞价）
issuer: multica-orchestrator        # caller

preconditions:                      # caller 义务：开工前必须全部成立
  - type: artifact_exists           # 机器可查
    ref: quant-loop/strategies/se_h3/SPEC.md
  - type: upstream_merged
    ref: main@f5db9e102
  - type: freshness                 # 防陈旧镜像乌龙
    subject: deploy_image
    max_age: 2h

deliverables:                       # callee 义务：产出物清单
  - type: git_branch
    pattern: agent/strategy-worker-1/SMA-XXXXX
    remote: he-mark-qinglong/multica
  - type: file
    path: quant-loop/results/SMA-XXXXX/metrics.json

acceptance_evidence:                # postcondition：done 的充要条件（全部机器可查）
  - check: git_remote_branch_exists
    ref: origin/agent/strategy-worker-1/SMA-XXXXX
  - check: command_exit_zero
    run: pytest quant-loop/tests/test_se_h3.py -x
  - check: json_fields_present
    ref: metrics.json
    fields: [oos_sharpe, ci_lower, fee_shock_60bps]

breach_policy:                      # 违约处置（Recovery 契约化）
  on_precondition_fail: escalate_to_issuer      # caller 的锅，退回派单方
  on_postcondition_fail:                        # callee 的锅
    detector: no_push_detector                  # 机器检测
    action: auto_redispatch_with_new_assignee   # 换执行者，不原样重派
    max_retries: 1
  on_invariant_break: escalate_human            # 环境/纪律违约 → L0
  timeout: 4h
  on_timeout: cancel_and_comment_NOOP

signoff_chain:                      # 签核语义
  required: [smark-signoff-proxy]
  prohibited_self_sign: true        # L2/L3 不得签自己产出
  evidence_must_attest: true        # 签核前逐项重跑 acceptance_evidence 的机器检查

invariants:                         # 全程成立
  - comment_schema_compliant
  - no_layer_crossing
```

### 3.3 契约实例状态机（借 A2A Task lifecycle + FIPA 交互协议）

```
DRAFTED → ISSUED → ACCEPTED/REJECTED(blocked+ESCALATE)
ACCEPTED → IN_PROGRESS → CLAIMED_DONE
CLAIMED_DONE → ATTESTING → VERIFIED_DONE / BREACH(postcondition)
BREACH → REDISPATCH(new assignee) | ESCALATE_HUMAN
VERIFIED_DONE → SIGNOFF_PENDING → SIGNED / SIGNOFF_REJECTED → CLAIMED_DONE
任意状态 → TIMEOUT → CANCELLED(NOOP 记录)
```

要点：`CLAIMED_DONE → VERIFIED_DONE` 之间插入 **ATTESTING** 态——
server/daemon 侧自动重跑 acceptance_evidence 的机器检查项，
**agent 自报 done 只到 CLAIMED_DONE，机器核验通过才到 VERIFIED_DONE**。
这是 v1 相对现状（自报=done）最核心的一处语义修改。

### 3.4 违约分类（typed failure semantics，借 LDP）

| 违约类型 | 定义 | 责任方 | 处置 |
|---|---|---|---|
| BREACH-PRE | precondition 不满足仍派单/开工 | issuer (L1) | 退回 + 修卡 + misroute 计数 |
| BREACH-POST | 声称完成但证据核验失败 | assignee (L3/L2) | 换执行者重派 + callee 信用扣分 |
| BREACH-EVID | 证据引用不存在/陈旧（签核引用假分支、预审用陈旧镜像） | 签核/执行方 | hard violation，立即 ESCALATE |
| BREACH-INV | invariant 破坏（评论无 schema、自签核） | 违规方 | soft→hard 升级，计数到 epoch-retro |
| ENV-FAIL | 环境破坏契约（工作区 GC、模型链路断） | 基础设施 | 不算任何 agent 的锅，触发自愈 + 补偿重跑 |

### 3.5 v1 已知留白（留给 v2 复盘后补齐）

- 信用分（callee trust score）具体算法未定——先记录违约事件，不急于做路由加权。
- 契约写在 issue 的哪个字段（front-matter? 评论首条? 独立 API?）未定——倾向 issue body front-matter。
- assume-guarantee 链式检查（上游 guarantee 变更时通知下游）只有思路没有机制。
- 与 daemon 调度器的集成点（ATTESTING 由谁跑）未定。
