# W2 服务端 gate 修复 + compare 展示层 — Round-1 任务计划

> Workstream slug: `w2-server-compare` · 2026-07-25
> 目标：修掉 server gate「缺失字段跳过=虚过」漏洞并落地 strict gate + `no-data` 状态；
> compare 页面如实展示（KILL 灰显+悬停原因、徽章如实、一句话 verdict）。
> 所有任务面向 caocao-m3 廉价 agent：单文件/单目录、<30 min、机械验收。

---

## 1. Current-state findings（仅列出亲自读码核实的内容）

### 1.1 Gate skip-pass bug（已核实）

- `server/internal/gate/gate.go:115-117` — 指标缺失时 `res.Pass = true; res.Note = skipNote`，缺失规则直接"通过"。
- `server/internal/gate/gate.go:131` — 只要 `m.Sharpe != nil` 且没有 evaluated 规则失败就返回 `StatusPass`；被 skip 的规则不影响结论。结果：只有 sharpe 的 blob（如 SUMMARY 中 sharpe=31.7 的 vpvr_stable_depeg_p3opt_091）整体 PASS。
- `server/internal/gate/gate.go:22-25` — 注释自述设计意图是"不 fail 但也不该算过"，但 overall status 没有第三态来表达这个区别。
- 修复草案已存在（未经生产落地）：`quant-loop/research/swarm/2026-07-25/gate-ledger-fix/gate_proposal.go` — 全部 6 条规则 required，缺失即 FAIL（sharpe 缺失 → 新状态 `no-data`）；`gate_test_proposal.go` 含 6 个新测试；`SUMMARY.md` §1.4 有 current-vs-proposed 对照表演示。
- 现有测试与新语义冲突、必须重写而非保留：
  - `server/internal/gate/gate_test.go:78-111` `TestEvaluateMissingOOSDataSkippedVisible`（断言缺失 OOS 仍 pass）
  - `server/internal/gate/gate_test.go:113-134` `TestEvaluateMissingSharpeStatusNull`（断言缺失 sharpe 返回 `""`，新语义为 `no-data`）
  - `server/internal/gate/gate_test.go:136-155` `TestEvaluateDrawdownMagnitudeSignConvention`、`:157-186` `TestEvaluateBoundaryValues`（用只含 sharpe 的 Metrics 断言 pass，新语义下会因其他必填缺失而 fail，需补全字段）

### 1.2 Ingest 层（已核实）

- `server/internal/handler/metric.go:79-150` `parseRunMetricJSON` — 逐列解析，缺失即 nil；**没有** profit_factor 的兜底推导。草案 `metric_proposal.go` 提供 `extractDailyReturns`/`computeProfitFactorFromDailyReturns`/`equityCurveToReturns` 三个纯函数 + 一段插在 `floatCols` 循环后（metric.go:104 之后）的补丁块。
- `server/internal/handler/metric.go:227-241` `persistGate` — `status == ""` 时跳过写库；新语义下 `Evaluate` 不再返回 `""`（proposal 保留 `""` 仅为兼容注释），persistGate 无需改动即可工作，但注释（metric.go:224-226）会过时需同步。
- `server/internal/handler/metric.go:482-552` `ReevaluateRunMetrics` — POST `/api/metrics/reevaluate` 已存在，可对存量行重算；counts map 只有 `pass/fail/skipped/errors`，`no-data` 会落进 `counts[status]` 但不出现在响应 JSON 里（响应只回 4 个固定 key，metric.go:545-551），需要把 `no-data` 加进响应。
- `server/internal/handler/metric.go:323-327` — `RunMetricResponse` 注释写死 `"pass" | "fail" | null`，需更新为含 `no-data`。
- 已有测试文件 `server/internal/handler/metric_test.go`（存在，未深读）。

### 1.3 DB 迁移（已核实）

- `server/migrations/123_run_metric_gate.up.sql` — `gate_status`/`gate_detail` 列**没有 CHECK 约束**（只有列 + 部分索引）。因此 `migration_proposal.sql` 的 `DROP CONSTRAINT IF EXISTS` 是 no-op，`ADD CONSTRAINT ... IN ('pass','fail','no-data')` 是净新增约束。最新迁移序号是 `124_issue_claim_columns`，新迁移应编号 `125`。
- 部署管线 `scripts/deploy.sh`（已通读）：本地交叉编译 linux/amd64 → scp 到 `smark@192.168.0.105:/home/smark/multica` → pg_dump 备份 → `migrate up`（迁移文件随 rsync 走磁盘）→ 换 binary 重启 → `/healthz` + 3 条路由 smoke，失败自动回滚。server 部署 = `scripts/deploy.sh server`；web 部署 = `scripts/deploy.sh web`（rsync 源码到 .105 后远端 pnpm build + 重启 :3000）。

### 1.4 Compare 前端（已核实）

- 页面主体：`packages/views/compare/components/compare-page.tsx`（493 行单文件）。`apps/web/app/[workspaceSlug]/(dashboard)/compare/page.tsx` 只是 `<ErrorBoundary><ComparePage/></ErrorBoundary>` 薄壳（12 行），无需动。
- `compare-page.tsx:74-77` `GATE_STYLE` 只有 `pass`/`fail` 两态，无 `no-data`；`:267` detail 面板 gate 徽章同样只认 pass/fail，其他一律灰 "NO DATA"。
- `compare-page.tsx:334-342` 可见性过滤：`gate_status === "pass"` 直接放行，否则要求 `ann_return > 0.02 && max_drawdown >= -0.30`。**strict gate 落地后几乎全部行都会变 fail**——如果不过滤逻辑同步改，页面会只剩 ann/mdd 过滤兜底，语义错乱。
- KILL 灰显/悬停：当前只有"未验证红虚框"（`:89-99` isRejected）和 `title={d.divergenceFlag}`（`:124`），**没有** KILL 概念、没有 kill 原因文本、没有灰显。
- 一句话 verdict：无任何展示位。
- 类型：`packages/core/types/metric.ts:40` `gate_status?: "pass" | "fail" | null`（缺 `"no-data"`）；`:11-17` `GateDetailEntry` 缺 `note` 字段（后端 JSON 一直会发 `note`，前端类型没声明）。
- 数据源现实：server 端 run_metric **没有** verdict / kill_reason 列（grep 全 server 无此字段）；未知 blob key 已自动落入 `extra`（metric.go:140-148），compare 页已在用 `extra.divergence_flag` 等（compare-page.tsx:166-176 `readExtra`）。因此 KILL/verdict 的最低成本数据通路 = 发布侧往 `extra` 写 `verdict` / `kill_reason` 键（零 server schema 变更），前端读 extra。
- `compare-page.tsx.bak.gate-pass-only` / `.bak.gate-filter-20260720` 两个备份文件与生产文件同目录，任务中禁止误改误删（不是本 workstream 的资产）。

---

## 2. 任务清单（12 个）

并行组规则：**同组任务文件互不相交，可同时派发**；跨组按 dependencies 排序。

### Group A（server 代码，彼此文件不相交，可并行；但 T2/T3 语义上依赖 T1 落地的新常量，建议 T1 先走或与 T1 同批但要求基于 gate_proposal.go 写）

**T1 — strict gate 落地：gate.go 替换为 proposal 语义**
- 目标：消除 skip-pass；全部 6 规则 required，缺失即 fail；新增 `StatusNoData`/`missingNote`。
- 文件：`server/internal/gate/gate.go`（以 `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/gate_proposal.go` 为蓝本，去掉 PROPOSAL 头注释，保留 package 文档注释更新）。
- 验收：`cd server && go build ./internal/gate/ && go vet ./internal/gate/`
- 大小：S · 依赖：无 · 组：**A1**

**T2 — gate 测试改写与合并**
- 目标：`gate_test_proposal.go` 的 6 个新测试合入 `gate_test.go`；重写 §1.1 列出的 4 个与新语义冲突的旧测试（补全必填字段或改断言为 fail/no-data）。
- 文件：`server/internal/gate/gate_test.go`
- 验收：`cd server && go test ./internal/gate/ -count=1` 全绿
- 大小：M · 依赖：T1（需 `StatusNoData`/`missingNote` 存在）· 组：**A2**

**T3 — ingest 层 profit_factor 兜底推导**
- 目标：合入 `metric_proposal.go` 的三个纯函数 + 在 `parseRunMetricJSON` 的 floatCols 循环后插入 PF 兜底块；同步更新 `persistGate` 与 `RunMetricResponse.GateStatus` 的过时注释（提到 `no-data`）。
- 文件：`server/internal/handler/metric.go`
- 验收：`cd server && go build ./internal/handler/ && go vet ./internal/handler/`
- 大小：M · 依赖：无（纯函数独立；语义配合 T1）· 组：**A3**

**T4 — handler 测试：PF 推导 + parse 兜底**
- 目标：给 `metric_test.go` 补用例——blob 含 `daily_returns` 时 PF 被推导、blob 含 `equity_curve` 时先转收益率再算 PF、混合类型数组不误收（返回 false）、sharpe-only blob 经 ingest 后 gate 为 fail。
- 文件：`server/internal/handler/metric_test.go`
- 验收：`cd server && go test ./internal/handler/ -run 'Metric|Gate' -count=1` 全绿
- 大小：M · 依赖：T3 · 组：**A4**

**T5 — DB 迁移 125：gate_status CHECK 约束 + reevaluate 响应加 no-data 计数**
- 目标：新增 `server/migrations/125_run_metric_gate_status_check.up.sql`（`ADD CONSTRAINT run_metric_gate_status_check CHECK (gate_status IS NULL OR gate_status IN ('pass','fail','no-data'))`，注意 123 未建过约束，DROP IF EXISTS 防御即可）+ 对应 `.down.sql`（DROP CONSTRAINT）；同时在 `ReevaluateRunMetrics` 响应里加 `"no-data"` 计数（metric.go:523,545-551 两处小改）。
- 文件：`server/migrations/125_run_metric_gate_status_check.up.sql`、`server/migrations/125_run_metric_gate_status_check.down.sql`、`server/internal/handler/metric.go`（仅 reevaluate 响应 3 行）
- 验收：`cd server && go build ./... && ls server/migrations/125_*.sql | wc -l` = 2；本地有 docker postgres 时 `make test` 中迁移相关测试绿（无则跳过并在 PR 说明）
- 大小：S · 依赖：无 · 组：**A5** ⚠️ 与 T3 同触 `metric.go` —— **T5 与 T3 必须串行**（T3 先，T5 后；T5 只改 ReevaluateRunMetrics 函数体，行区间不重叠，串行合入即可）

### Group B（前端，依赖 server 语义定型但不依赖 server 已部署——`no-data` 只是多一个字符串分支）

**T6 — core 类型同步**
- 目标：`gate_status` 加 `"no-data"`；`GateDetailEntry` 加 `note?: string`；新增 `extra` 约定键的类型化读取契约注释（`verdict`、`kill_reason`、`kill_evidence`，均为发布侧写入的 optional string）。
- 文件：`packages/core/types/metric.ts`
- 验收：`pnpm typecheck`
- 大小：S · 依赖：无 · 组：**B1**

**T7 — compare 数据判读纯函数 + 单测**
- 目标：新建 `packages/views/compare/utils/verdict.ts`：`readVerdict(m: RunMetric): { verdict: string | null; killReason: string | null; killed: boolean }`，killed 判定 = `extra.kill_reason` 非空 或 `extra.divergence_flag ∈ {KILLED, REJECTED}`；配套 vitest 单测。
- 文件：`packages/views/compare/utils/verdict.ts`、`packages/views/compare/utils/verdict.test.ts`
- 验收：`pnpm test -- verdict`（或 `pnpm vitest run packages/views/compare/utils/verdict.test.ts`）
- 大小：S · 依赖：T6 · 组：**B2**

**T8 — compare 页面：gate 徽章三态 + 每规则 detail 列表（含 note）**
- 目标：`GATE_STYLE` 加 `no-data`（灰色问号）；detail 面板把 `gate_detail` 数组逐条渲染（rule/op/threshold/actual/pass/note），缺失必填规则显示 `missing required metric`；更新 §1.4 提到的过滤逻辑——`gate_status === "fail"` 的行默认仍展示但置灰（不再靠 ann/mdd 兜底隐身）。
- 文件：`packages/views/compare/components/compare-page.tsx`
- 验收：`pnpm typecheck && pnpm --filter @multica/views build`（或 views 包既有 lint/build 命令）
- 大小：M · 依赖：T6 · 组：**B3**

**T9 — compare 页面：KILL 灰显 + 悬停原因 + 一句话 verdict**
- 目标：用 T7 的 `readVerdict`：killed 节点整体降透明度/灰阶 + `title` 悬停显示 kill_reason（无原因时显示 divergence_flag）；detail 面板顶部渲染一句话 verdict 区块（无 verdict 不渲染）。在 T8 改完的同文件上叠加。
- 文件：`packages/views/compare/components/compare-page.tsx`
- 验收：`pnpm typecheck` + 手测截图（可选）
- 大小：M · 依赖：T7、T8（同文件串行）· 组：**B4**

### Group C（数据通路 + 部署，串行收尾）

**T10 — verdict/kill_reason 发布侧写入约定 + ledger 脚本对齐**
- 目标：在 `quant-loop/scripts/build_results_ledger.py` 输出或指标发布脚本中，把 ledger verdict（CV_PASS/PROFITABLE/HOLD/KILL/UNTESTED，见 gate-ledger-fix/ledger_proposal.py）与 KILL 原因写入上传 blob 的 `verdict` / `kill_reason` 键（落在 run_metric.extra）。只改一个脚本文件；若发布链路在别的 workstream，则产出一份 3 行的字段约定片段供其合入。
- 文件：`quant-loop/scripts/build_results_ledger.py`（或发布脚本，执行 agent 先 grep `queryMetrics|/api/metrics` 确认写入点）
- 验收：`/Users/mark/sdk/mamba-envs/trading/bin/python3 quant-loop/scripts/build_results_ledger.py` 跑通且输出含 verdict/kill_reason 字段
- 大小：M · 依赖：无（与 T7 约定一致即可）· 组：**C1** ⚠️ 跨 workstream 风险见 §4

**T11 — 部署 server 到 192.168.0.105 + 存量重算（步骤，不由本计划执行）**
- 目标：把 T1-T5 部署上 .105 并 backfill gate 结果。
- 步骤（人工/ops agent 执行）：
  1. 本地 `cd server && go test ./... -count=1` 全绿。
  2. `scripts/deploy.sh server`（自动：交叉编译 → scp → pg_dump 备份 → migrate up（应用 125）→ 换 binary → /healthz + 路由 smoke，失败自动回滚到 `server.bak-<stamp>`）。
  3. backfill：`curl -X POST http://192.168.0.105:8080/api/metrics/reevaluate -H ...（workspace auth） -d '{}'`，确认响应含 `no-data` 计数且 `errors == 0`。
  4. 抽查一条已知 sharpe-only 行（vpvr_stable_depeg_p3opt_091）：`GET /api/metrics/query?campaign=...` 确认 `gate_status == "fail"`。
- 验收：步骤 3/4 的输出
- 大小：S（执行约 10 min）· 依赖：T1-T5 · 组：**C2**

**T12 — 部署 web 到 .105 + compare 页面冒烟（步骤，不由本计划执行）**
- 目标：前端上线。
- 步骤：1. `pnpm typecheck && pnpm test` 绿；2. `scripts/deploy.sh web`（rsync 源码 → 远端 pnpm install + next build → 重启 :3000 → curl 冒烟）；3. 浏览器开 `http://192.168.0.105:3000/<slug>/compare`，确认：三态徽章、KILL 灰显+悬停、verdict 区块、过滤后页面不为空。
- 验收：步骤 3 手测
- 大小：S · 依赖：T6-T9，建议 T11 之后（有真实 no-data/fail 数据可验）· 组：**C3**

---

## 3. Out-of-scope（本 workstream 明确不做）

- **不做** gate 规则可配置化（per-workspace/per-campaign rules）——gate.go 注释明确 P2 才做，本次保持 hardcoded。
- **不做** profit_factor 阈值（>1.5）是否过严的政策讨论——SUMMARY §1.6-4 已标注这是 policy decision，走 ESCALATE，不在代码任务里改阈值。
- **不改** `scripts/build_results_ledger.py` 的 verdict 状态机本身（ledger_proposal.py 的合入属于 ledger workstream）；T10 只加字段透传。
- **不动** `compare-page.tsx.bak.*` 两个备份文件，不删不改。
- **不做** campaign tree 布局/交互重构、equity 图表改造、packages/ui 组件库改动。
- **不改** `packages/core/api` 的 queryMetrics 传输层（现有 extra 透传已够用，不加新 endpoint、不加 run_metric 新列）。
- **不执行** 任何部署、不回滚 worktree 中他人的未提交改动、不做 git mutation。
- **不修** paper trading harness（计划 §11 明确弃疗）。

## 4. 跨 workstream 冲突预警

- **metric.go 双任务（T3+T5）**：若 W-其他流也改 `server/internal/handler/metric.go`（如 metrics ingest 扩展），三方叠加需按 T3 → T5 → 其他 串行。
- **T10 碰 `quant-loop/scripts/build_results_ledger.py`**：ledger verdict 重构（ledger_proposal.py 落地）大概率属于另一个 workstream；若并行进行，T10 应退化为"只产字段约定片段"，避免同文件冲突。
- **compare-page.tsx 是本流 4 个任务的串行链（T8→T9）**：任何其他流对 compare 页面的改动都必须排在此链之后。
- **server 部署窗口（T11）**：deploy.sh 会在无任务在跑时重启 daemon，且 deploy 期间 .105 上 autopilot 调度短暂受影响；与其他流的部署任务（如 CLI/daemon 改动）需共用同一部署窗口，避免连续多次重启。
