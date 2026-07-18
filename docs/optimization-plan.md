# multica 平台优化计划

> 目标：让 multica 从「issue 驱动的 agent 调度器」演进为「计划驱动的策略迭代平台」。
> 五个改造方向：易用性、策略迭代预览、新计划指定、计划实施、agent 自我优化。
> 现状判断均基于代码事实（附落点），改造项按必要性排序。

## 0. 现状判断（事实基线）

当前平台的能力边界：

- **工作单元是 issue**，一次执行是 `agent_task_queue` 里的一行（`server/migrations/001_init.up.sql:127`）。没有 plan / campaign / DAG 实体；`issue_dependency` 表已存在（`001:89`）但零查询、零路由，是死 schema。
- **autopilot 只有单步**：`create_issue`（建一个模板 issue）或 `run_only`（直接跑一个 agent 任务），无流水线、无多步编排（`server/internal/service/autopilot.go`）。
- **运行结果没有类型化载体**：agent 的产出只有两条路——`task_message`（瞬时 transcript）和 issue comment（持久但自由文本）。没有 artifact / metrics 模型；attachment 是通用文件，不做内容索引。`task.result JSONB` 字段存在但自由格式、无人消费。
- **无预览 / 审批机制**：没有 dry-run；inbox 类型 `review_requested` 在前端声明（`packages/core/types/inbox.ts:14`）但 server 从未发出。实际审批靠 `in_review` 状态 + 评论，是约定不是机制。
- **skill 无版本**：`skill` 表无 version 列（`008_structured_skills.up.sql`），agent 可通过 CLI 改 skill/instructions，但注入的 meta-skill 不 advertise 这些命令，无审批、无审计、无回滚。
- **CLI 有静默失败点**：`--status todo,in_progress` 被当成一个字面字符串静默返回空；backlog 状态的 issue 指定 assignee 不触发 enqueue（dispatcher 只认 todo/in_progress/in_review）；`autopilot` 子命令拒绝 `--output json`；`multica task list` 不存在。
- **策略对比在平台外**：/compare 是独立的 Plotly 应用（端口 3210），不在本仓库，靠人工喂数据。repo 内图表栈是 recharts（仅用于 usage 仪表盘）。
- 已有的强项（不要重造）：run transcript 回放（`agent-transcript-dialog.tsx`）、agent 配置 UI（runtime/model/concurrency/skills/instructions）、autopilot 调度器、inbox 通知、squad 简报注入、`issue.parent_issue_id` + child-progress 汇总。

---

## 1. 易用性（usability）

原则：**消灭静默失败，补齐人/机共用的读模型，让常用路径一条命令直达**。这一期不碰架构，全是低成本高回报项。

### 1.1 CLI 硬化（最高优先级）

- **多状态查询**：`--status todo,in_progress` 要么正确实现（server 端拆分 IN 查询），要么直接报错退出。当前静默返回空是 agent 和 cron 踩过的坑。落点：issue list handler 的 status 解析。
- **dispatch 语义显式化**：`issue update --assignee-id X` 在 backlog 状态上是静默 no-op。改为：指定 assignee 时若状态不可 dispatch 则警告并提示 `--status todo`；或提供 `--dispatch` 旗标一步完成「置 todo + 指派」。
- **补齐 task 读模型**：新增 `multica task list|get|cancel`（现状只能靠 done issue 反推完成度）。数据已在 `agent_task_queue`，只是没暴露 CLI。路由已有 `/api/issues/{id}/task-runs`，缺的是全局视图 `/api/tasks`。
- **`--output json` 全覆盖**：autopilot 子命令目前拒绝该旗标，统一所有 list/get 命令。
- **别名与纠错**：`issue view` → `get` 的别名；未知子命令时给出 did-you-mean。
- **`--dry-run`**：所有变更类命令（issue create/update、autopilot trigger、agent update）支持，打印将执行的请求而不发送。

### 1.2 `multica doctor` 自检命令

历史上最难缠的故障都是环境性的：daemon token 失效导致 401 崩溃循环、孤儿进程占端口、runtime 注册到错误 daemon、转发隧道断连。新增 `multica doctor`：检查 server 连通、token 有效性、daemon 心跳、runtime online 状态、端口占用、workdir 磁盘，逐项 PASS/FAIL + 修复建议。落点：`server/cmd/multica/` 新命令，纯客户端检查 + 已有 `/healthz`。

### 1.3 结构化运行结果

- 约定 agent 在任务结束时写 `result.json`（schema 按任务类型声明），daemon 随 complete 上报存入现有 `task.result JSONB`；server 提供 `/api/tasks/{id}/result`。
- Web 在 issue 页和 run 列表直接渲染 result（指标表），替代「翻评论找数字」。这是第 2 节预览能力的数据底座。

### 1.4 Web 全局运行视图

- 新增 `/runs` 页：跨 issue 的 run 流，按 agent / 状态 / 时间过滤，复用现有 transcript 组件。现状只有 per-issue 的 execution-log-section，排查「昨晚谁在跑什么」要逐个 issue 翻。
- inbox 增加结构化动作按钮（approve / reject / retry），为第 4 节的审批门做准备；纯展示层先行。

### 1.5 验收标准

- 上述静默失败点全部变为显式行为（正确结果或明确报错），各配一个 e2e 用例。
- `multica task list --status running --output json` 可用；`multica doctor` 覆盖 5 类环境故障检查。

---

## 2. 策略快速迭代的预览（preview）

目标：**一次迭代从「跑完发评论」变成「指标入库、曲线可叠、门禁自动判」，对比在平台内完成，过拟合在进 review 前被拦下**。

### 2.1 类型化 artifact

- 新表 `artifact`：`id, task_id, issue_id, kind (metrics|equity|plot|log|dataset), path, meta JSONB, created_at`。存储复用现有 storage 后端（`server/internal/storage/{local,s3}.go`）。
- 产出约定：任务 workdir 下 `artifacts/` 目录的内容由 daemon 在 complete 时自动采集上报；也可 `multica artifact add` 手动挂。agent 零成本接入——策略框架本来就会落盘 equity.csv / metrics.json。

### 2.2 指标入库与查询

- `metrics.json` 按约定 schema（sharpe, ann, mdd, pf, oos_windows[], timeframe, symbols, params）解析进 `run_metric` 表，以 (campaign, iteration) 为索引维度。
- API：`/api/metrics/query?campaign=...&metric=sharpe`，供 UI 和 agent（CLI `multica metrics query`）双方消费。agent 做参数决策时不再需要 LLM 解析历史评论。

### 2.3 平台内 /compare 页

- apps/web 新增 `/compare`：选定 N 个 run/迭代，叠加 equity 曲线 + 指标对照表 + 参数 diff。图表用仓库已有的 recharts，不引入 Plotly。
- 外部 3210 对比前端的能力（多 symbol K 线三联面板）作为后续项移植；第一步先把「迭代间指标对比」做掉，这是迭代决策的刚需。

### 2.4 门禁（gate）服务端求值

- artifact 入库时按声明的门禁规则自动求值（如 Sharpe≥1.0、ann≥15%、OOS 窗口≥3 且均值≥1.0、框架间偏差<20%），结果写回 run：`gate_pass | gate_fail | gate_override`。
- 效果：`gate_fail` 的 run 在 UI 标红、issue 转 `in_review` 时警告；门禁规则放在 campaign/plan 级别声明（见第 3 节），不再靠人肉记住标准。过拟合候选（样本内 5.72 / OOS 0.61 这类）在提交瞬间自动现形。

### 2.5 派发前预览（dry-run preview）

- `multica issue create --preview` / autopilot trigger `--dry-run`：展示将被派发的 agent、注入的 skill 列表、拼装后的 prompt 摘要、预估 token 成本，不落队列。用于在烧算力之前确认「这次迭代跑的是我以为的东西」。

### 2.6 验收标准

- 跑一次真实策略回测任务，artifacts 自动入库，`/compare` 能选两次迭代叠加 equity 并显示门禁判定；`--preview` 输出与实际派发内容一致。

---

## 3. 新计划指定（plan specification）

目标：**计划从「标题前缀约定」升级为平台实体——可声明、可校验、可模板化、可实例化**。

### 3.1 Plan 实体

- 新表：
  - `plan`：`id, workspace_id, title, spec (markdown/json), status (draft|active|paused|done|cancelled), template_id, gate_config JSONB, created_by`
  - `plan_step`：`id, plan_id, name, seq, assignee (agent|squad), skill_refs[], inputs, expected_artifacts[], gate_rules, depends_on_step_ids[], on_fail (abort|skip|escalate), approval_required bool`
- 依赖直接利用/激活现有 `issue_dependency`（blocks/blocked_by），或按上表自管。激活死 schema 优先——表和类型已经在了。

### 3.2 声明式 spec 与校验

- 计划用 YAML/JSON 声明（可放在 issue body 的 fenced block 或 `multica plan create --from plan.yml`）。server 端校验：assignee 存在且未归档、skill_refs 已绑定该 agent、依赖无环、gate 规则可解析。校验失败拒绝创建并给出逐条错误——把「计划写错」暴露在创建时而不是执行中。

### 3.3 计划模板

- 复用 agent template 的机制（`server/internal/agenttmpl`）做 plan template。首发三个：
  - `strategy-campaign`：研究 → 设计 → 回测 → 跨框架验证 → 风控复核 → sign-off（每步带默认门禁）
  - `infra-change`：方案 → 实施 → 验证 → 文档
  - `code-change`：需求分解 → 实现 → 测试 → review
- 模板即默认参数 + 步骤骨架，实例化时可覆盖。

### 3.4 实例化

- plan 激活时按步骤物化 issue：根 issue（campaign 容器，承担现有 parent_issue_id 汇总）+ 每步一个子 issue，依赖写入 `issue_dependency`。autopilot 获得第三种执行模式 `run_plan`：按计划模板周期实例化（替代现在「autopilot 只能发单步、多步靠 cron 体外编排」的现状）。

### 3.5 计划 UI

- `/plans` 列表 + 详情页：步骤 DAG 图（前端已有 dagre/mermaid 依赖可用）、每步状态/产物/门禁结果、从模板创建的向导。

### 3.6 验收标准

- 用 `strategy-campaign` 模板创建一个计划，物化出带依赖的子 issue 树；改一步的 spec 校验能拦下「skill 未绑定」这类错误；`/plans` 页渲染 DAG。

---

## 4. 计划实施（plan execution）

目标：**调度器理解依赖与门禁，审批成为机制而非约定，反压内建**。

### 4.1 依赖门控派发

- `EnqueueTaskForIssue`（`server/internal/service/task.go:356`）前增加依赖检查：存在未完成 blocks 依赖的 issue 入队为 `awaiting_deps`（新任务状态）或直接不入队，依赖完成时由完成事件触发唤醒。这是整个计划实施的核心改造。

### 4.2 步骤级执行策略

- `plan_step` 携带：timeout、max_attempts（复用现有 attempt/parent_task_id 重试骨架 `055`，把「仅 infra 失败重试」扩展为可配置）、on_fail 策略。
- 失败 N 次后走结构化升级：inbox `action_required` + 上下文 + 可选动作（retry / skip / abort plan），替代「发评论等人看」。

### 4.3 审批门（approval gate）

- 新任务/步骤状态 `awaiting_approval`：`approval_required` 步骤产出就绪后暂停，server 发出 `review_requested` inbox（激活这个已声明未使用的类型），附 result 摘要与 artifact 链接；approve → 继续，reject（附意见）→ 以评论形式回注 issue 并重派该步骤。
- 审批可委托：plan 上配置 approver（人 或 agent）。委托给 agent 时携带门禁数据，例行审批（指标达标即放行）不再占用人的带宽；战略项保持人工。

### 4.4 预算与反压内建

- plan 级并发上限与 token/成本预算（数据已有 `task_usage` + 日 rollup），超预算暂停并通知。
- dispatcher 内建全局反压：`in_review` 超过阈值时自动降低新派发速率——把目前靠外部 cron 数 in_review 的体外循环收进平台。

### 4.5 计划级状态机与可观测

- plan 状态由步骤推导；pause/resume/cancel 级联到 queued/running 任务。
- plan 进度 API + `/plans` 页时间线；Prometheus 指标按 plan 维度出数（`server/internal/metrics` 已有基建）。

### 4.6 验收标准

- 三步依赖计划端到端跑通：B 在 A done 前不派发；中间步骤 gate_fail 且 on_fail=escalate 时 inbox 收到 action_required；审批步骤在 UI 一键 approve 后继续；plan pause 级联暂停在途任务。

---

## 5. Agent 自我优化（self-optimization）

目标：**skill/指令的演化从「体外 cron 手改」变为「平台内提案-审批-版本化-可回滚-可度量」的闭环**。

### 5.1 Skill 版本化（前置条件）

- `skill_version` 表：`id, skill_id, version, content, content_hash, parent_version_id, created_by (member|agent), created_at`。`agent_skill` 绑定改为指向 (skill, version | latest)。
- 回滚 = 重新绑定旧版本。没有版本化，一切自我优化都无法审计。

### 5.2 提案-审批流

- agent 侧新命令 `multica skill propose`（创建 pending 版本 + 变更说明 + 依据的 run 证据），与直接生效的 `skill update` 区分；`agent update --instructions` 同样走 proposal 队列（agent 配置变更纳入审批）。
- 注入的 meta-skill（`execenv/runtime_config.go`）advertise propose 命令——今天 agent 理论上能改 skill 但不知道命令存在，这个能力等于不存在。
- 人或受委托的审批 agent 在 UI/CLI 审批后版本生效；全程 activity_log 留痕。

### 5.3 反馈数据闭环

- 每次 run 产生结构化 outcome：门禁结果（2.4）、人工 verdict（approve/reject、reaction）、重试次数、升级次数、token 成本。现有 squad activity 评价（`POST /api/issues/{id}/squad-evaluated`）泛化为 per-run outcome 标签。
- agent 做自我优化提案时以 outcome 数据为证据（「版本 A 近 20 次 run 门禁通过率 35%，提案修改 X」），审批者看数据不看感觉。

### 5.4 A/B 评估

- 支持把 agent 的 skill 绑定按流量比例分到两个版本（如 80% 旧 / 20% 新），按窗口统计门禁通过率、成本、升级率；达标自动 promote，恶化自动回滚。guardrail：单次 promote 需满足最小样本量，防止噪声翻盘。

### 5.5 Agent 绩效看板

- per-agent 仪表盘：成功率、门禁通过率、平均 token、升级率随时间变化。数据基本已在 `task_usage*` + 新 outcome 标签，是 5.2 审批和 5.4 A/B 的共用展示层。

### 5.6 验收标准

- 一个 agent 完成 run 后用 `skill propose` 提交新版本，审批后生效并可一键回滚；A/B 窗口结束后产出对比报告；看板展示该 agent 前后 30 天指标变化。

---

## 6. 分期路线图

| 期 | 内容 | 依据 |
|---|---|---|
| **P0 易用性**（1–2 周） | 1.1 CLI 硬化、1.2 doctor、1.3 结构化 result、1.4 /runs | 无 schema 变更或极小，立刻降低人/agent 的踩坑率；1.3 是 P1 的数据底座 |
| **P1 预览**（2–4 周） | 2.1 artifact、2.2 指标入库、2.3 /compare、2.4 门禁求值、2.5 dry-run | 直接服务策略迭代主战场；门禁规则先硬编码常用几条，配置化随 P2 |
| **P2 计划**（4–8 周） | 3.1–3.4 plan 实体/模板/实例化 + 4.1 依赖门控 + 4.3 审批门 | 架构核心改造；激活 `issue_dependency` 与 `review_requested` 两个休眠点 |
| **P3 自我优化**（4 周） | 5.1 版本化、5.2 提案流、5.3 outcome、5.4 A/B、5.5 看板 | 依赖 P1 的门禁数据与 P2 的审批机制，天然最后 |

每期完成以对应验收标准为准，不达标不进下一期。

## 7. 明确不做的事

- **不引入更多 specialist agent**。编排能力长在平台（plan/step），agent 侧保持少而精的通用体 + skill 模式；水平扩展靠并发不靠 agent 数量。
- **不替换现有 issue/autopilot 模型**。plan 是其上的编排层，autopilot 增加 `run_plan` 模式而非重写；存量 issue 流不受影响。
- **不做外部 3210 前端的完整移植**。先把指标对比做进平台；K 线三联面板等行情可视化列为后续项。
- **不在 P0–P1 动 daemon 协议的兼容性**。artifact 采集与结构化结果走既有 complete 上报通道的扩展字段，保持向后兼容（新 daemon 配旧 server、旧 daemon 配新 server 均可）。
- **部署不在平台改造范围外**。`scripts/deploy.sh` 把构建 → 上传 → DB 备份 → 迁移 → 切换 → 健康验证固化为一条管线；每一期的完成定义包含「已用该管线部署并通过线上冒烟」。
