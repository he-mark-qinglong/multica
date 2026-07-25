# W5 自动化编排与运维 — Round-1 任务计划（infra-sprint 2026-07-25）

> 范围：日循环 autopilot 胶水 / .105 部署流水线 / paper trading harness 重建评估 / swarm 执行结果回收。
> 执行方式：后续 2×128 caocao-m3 swarm 并行执行；每个任务 <30min、文件隔离、机械验收。
> 本文件是唯一产出；未改动任何代码或 git 状态。

---

## 1. 现状核实（全部亲自读码/跑命令验证）

### 1.1 Autopilot 清单（2026-07-25 实测，非文档转述）
- `multica autopilot list`（对 .105:8080）实测 **27 个 autopilot：3 active + 24 paused**。
  AGENTS.md「24 个 autopilot active」已过时。
- Active 仅 3 个：`28d2a8c7` research-scout（日 09:00）、`23281f8e` epoch-retro（日 21:00）、
  `c84304df` infra-health-watchdog（10min）。
- Paused 24 个含关键循环件：`0b51b7d3` Multica Dispatch、`1035ba96` Evidence Review Gate、
  `a66b4ecf` publish-gate、`51e7cb03` framework-validate、`6b8c460a` stalled-issue-watchdog、
  `438f218b` autopilot-prompt-tuner、`f28065af` strategy-archiver 等。
- **Epoch 主循环缺口**：plan §4.1（multica-quant-permanent-loop-2026-07-25.md:96-101）定义了
  5 个日触发点，但 10:00 spec-select、17:00 validation-trigger、20:00 verdict-trigger
  **均无对应 autopilot**。

### 1.2 部署流水线
- `scripts/deploy.sh:1-161` 已存在且完整：本地交叉编译 linux/amd64（:67-69）、DB 备份（:91-93）、
  migrate（:96-98）、binary swap + /healthz 验证 + 自动回滚（:101-134）、
  daemon 仅空闲时重启（:147-156）。默认目标 `smark@192.168.0.105`（:17-18）。
- 最近一次实际部署产物 `dist/deploy-20260719-024315/`（ls 实测，2026-07-19）——
  **即 6 天未部署，server gate skip-pass 修复积压在本地未上线**（plan:206 亦确认"待部署窗口修复"）。
- 缺口：无 `--dry-run`；无部署后 autopilot/业务级冒烟（现有冒烟只查 3 条路由返回码 :130-134）；
  无 CI/定时接线，全靠人记。

### 1.3 Server gate skip-pass bug（本地树仍未修）
- `server/internal/gate/gate.go:115-117`：metric 缺失时 `res.Pass = true; res.Note = skipNote`
  ——缺失字段永不 fail。`:131`：只要 sharpe 非 nil 即 overall pass。
- swarm 产出 `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/gate_proposal.go` +
  `gate_test_proposal.go` + `migration_proposal.sql` 已写好但**未合入 server/**（proposal 文件仍在
  swarm 目录，gate.go 本体未动）。

### 1.4 Paper trading harness（已归档，bug 已定位）
- 归档目录 `quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/`。
- 重复记账 bug 根因：`paper_runner.py:103-110` `_append_daily_metrics` 纯 append、
  无按日去重、无文件尾换行不变量；`:113-124` `_init_ledger_headers` 与 append 路径分离导致
  header 与首行粘连。`ARCHIVE_NOTE.md:25-33` 记录了 2026-07-20 双行 + header 粘连实证。
- plan §11（:220）明确「不给 paper trading harness 续命，等重建」——所以是**重写 ledger writer**，
  不是修旧文件。

### 1.5 Swarm 结果回收
- 现状纯手工：`quant-loop/research/swarm/2026-07-25/<slug>/` 下 SUMMARY.md 手写、
  proposal 文件游离（gate-ledger-fix 的 4 个 proposal 即为例证），无 manifest、
  无机械验收、无自动合入/上传。
- 可复用件：`server/cmd/multica/cmd_artifact.go` 已有 artifact CLI（回收产物上传 .105 可用）；
  issue comment CLI 存在（`cmd_issue.go`），可发 `[type=EVIDENCE]` 回执。

### 1.6 健康检查
- infra-health-watchdog `c84304df` 在跑，但全库 grep 证实**仓库内无对应探测脚本**——
  探活逻辑只存在于 autopilot prompt 文本里，不可单测、不可复用。

### 1.7 其它约束
- `scripts/autopilot_loop.sh:5` 硬编码 `/home/smark/multica`（legacy，.105 时代遗物）；
  新脚本一律用 env 变量（plan §9 :194 要求）。
- Python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`。

---

## 2. 任务清单（13 个）

并行组规则：同组任务文件不相交，可同批并行；跨组有依赖按标注排序。
统一验收前缀：`PY=/Users/mark/sdk/mamba-envs/trading/bin/python3`。

### G1 循环胶水组（ops glue）

**T1 — infra 健康检查脚本化**（M）
- 目标：把 infra-health-watchdog 的探活逻辑落成可单测脚本。
- 文件：`scripts/infra_health_check.sh`（新建；探测本机 launchd `com.smark.caocao-tunnel`/
  `caocao-model-proxy`/`multica-daemon` + 18091/18092 端口 + `curl -sf http://192.168.0.105:8080/healthz`，
  支持 `--self-heal`（launchctl kickstart）与 `--json`）。
- 验收：`bash scripts/infra_health_check.sh --json` 退出码 0 且输出含全部 5 个探测项；
  `bash -n` 语法检查过。
- 依赖：无。组：G1。

**T2 — autopilot 清单快照工具**（S）
- 目标：一键 dump 27 个 autopilot 状态到 markdown，供恢复决策与防重复造轮子。
- 文件：`scripts/dump_autopilots.sh`（新建，调 `multica autopilot list --output json` + python 渲染）、
  输出 `ops-reports/autopilot-inventory.md`（新建）。
- 验收：`bash scripts/dump_autopilots.sh && grep -c '^|' ops-reports/autopilot-inventory.md` ≥ 28。
- 依赖：无。组：G1。

**T3 — epoch 缺失触发器补全（manifest 化 + 幂等 apply）**（M）
- 目标：补 10:00 spec-select / 17:00 validation-trigger / 20:00 verdict-trigger 三个 autopilot，
  以 manifest 即代码方式管理，可重复 apply 不产生重复。
- 文件：`ops/autopilots/spec-select.json`、`ops/autopilots/validation-trigger.json`、
  `ops/autopilots/verdict-trigger.json`、`ops/autopilots/apply.sh`（全部新建；
  apply 用 `multica autopilot create/update`，按 title 查重幂等）。
- 验收：`bash ops/autopilots/apply.sh --dry-run` 退出 0 且打印恰好 3 条计划操作；
  `$PY -c "import json,glob;[json.load(open(f)) for f in glob.glob('ops/autopilots/*.json')]"` 通过。
- 依赖：T2（先看清现有清单避免撞名）。组：G1。
- 注：prompt 文本遵守 comment schema 与 m3-only 纪律，禁止指定模型。

**T4 — comment schema linter**（S）
- 目标：AGENTS.md 里 TBD 的 comment-janitor 验证器落地为脚本，供 swarm 回收时校验回执格式。
- 文件：`scripts/comment_schema_lint.py`（新建，校验首行 `[type=X] <iso8601+tz> <summary>`，
  type ∈ STATUS/DECISION/EVIDENCE/KILL/ESCALATE/SIGNOFF/NUDGE/NOOP）、
  `scripts/test_comment_schema_lint.py`（新建，fixture 含 8 正 6 反例）。
- 验收：`$PY -m pytest scripts/test_comment_schema_lint.py -q` 全过。
- 依赖：无。组：G1。

### G2 部署组（deploy）

**T5 — 部署后业务级冒烟脚本**（M）
- 目标：补 deploy.sh :130-134 之外的深层冒烟：gate 行为（缺字段 metric 不得 pass）、
  autopilot 数量、daemon active、metrics ingest 往返。
- 文件：`scripts/deploy_smoke.sh`（新建；`SMOKE_HOST` env，默认 `http://192.168.0.105:8080`）。
- 验收：负测试可机械跑——`SMOKE_HOST=http://127.0.0.1:1 bash scripts/deploy_smoke.sh`
  必须非零退出；脚本含逐 probe 命名输出。
- 依赖：无。组：G2。

**T6 — deploy.sh 加固：--dry-run + 冒烟接线**（M）
- 目标：`scripts/deploy.sh` 增加 `--dry-run`（只本地构建不上传），尾部调用 T5 冒烟；
  保持现有回滚逻辑不动。
- 文件：`scripts/deploy.sh`（唯一改动者，Edit 级别最小侵入）。
- 验收：`bash scripts/deploy.sh --dry-run` 构建出 3 个二进制到 dist/ 且全程无 ssh 连接
  （`--dry-run` 日志断言）；`bash -n scripts/deploy.sh` 过。
- 依赖：T5（被调用方先存在）。组：G2。

**T7 — server gate skip-pass 修复合入 + 部署**（M）
- 目标：把 `research/swarm/2026-07-25/gate-ledger-fix/gate_proposal.go`（+ test + migration）
  合入 `server/internal/gate/gate.go` 与对应测试，跑通后走 T6 流水线部署到 .105。
- 文件：`server/internal/gate/gate.go`、`server/internal/gate/gate_test.go`（或同级测试文件）、
  `server/migrations/<next>_gate_status_nodata.sql`。
- 验收：`cd server && go test ./internal/gate/... ./internal/handler/...` 全过；
  部署后 `curl /healthz` 含 `"migrations":"ok"` 且 T5 冒烟全绿。
- 依赖：T5、T6。**跨组冲突警告：gate 语义归验证管线 workstream；若对方已在改 gate.go，
  本任务退化为只执行部署步骤，语义改动以对方为准。** 组：G2（排在该组最后执行）。

### G3 paper trading 组（paper）

**T8 — 原子 ledger writer 重写**（M）
- 目标：新写 `quant-loop/_shared/paper/ledger_writer.py`：tmp-write+rename 原子追加、
  按 date 去重（同日重写为单行）、header 换行不变量、可从 trades.jsonl 重建。
  旧 paper_runner.py 一行不动。
- 文件：`quant-loop/_shared/paper/__init__.py`、`quant-loop/_shared/paper/ledger_writer.py`、
  `quant-loop/_shared/paper/test_ledger_writer.py`（全部新建；测试含：重复日合并、
  模拟中断无半行、trades.jsonl 往返一致）。
- 验收：`$PY -m pytest quant-loop/_shared/paper/test_ledger_writer.py -q` 全过。
- 依赖：无。组：G3。

**T9 — paper runner 骨架（新 harness）**（M）
- 目标：基于 T8 writer 的最小 paper runner：config.json 驱动、消费 `_shared/run_backtest.py`
  信号接口、kill-criteria 评估、state 落盘断点续跑（plan §4.3 幂等纪律）。
- 文件：`quant-loop/_shared/paper/runner.py`、`quant-loop/_shared/paper/config.schema.json`、
  `quant-loop/_shared/paper/test_runner.py`（新建；测试用合成信号 CSV 跑 10 根 bar，
  断言 ledger 行数/字段/kill 触发路径）。
- 验收：`$PY -m pytest quant-loop/_shared/paper/test_runner.py -q` 全过（<2min，无真实回测）。
- 依赖：T8。组：G3。

**T10 — 归档 ledger 修复工具**（S）
- 目标：用 T8 writer 把 graveyard 的 daily_metrics.csv 从 trades.jsonl 重建到
  `daily_metrics.repaired.csv`（新文件，不改原件），量化 2026-07-20 双行差异。
- 文件：`quant-loop/_shared/paper/repair_ledger.py`（新建）；输出落在 graveyard 目录内新文件。
- 验收：`$PY quant-loop/_shared/paper/repair_ledger.py quant-loop/strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/results-ledger`
  退出 0，且输出 CSV 每个 date 唯一（`cut -d, -f1 ... | sort | uniq -d | wc -l` = 0）。
- 依赖：T8。组：G3。

### G4 swarm 回收组（collection）

**T11 — swarm run manifest schema + 收集器**（M）
- 目标：定义 `manifest.json`（每 item：slug/owner/files/acceptance 命令/状态），
  收集器校验 swarm 输出目录完整性并出汇总表 + 可合入清单。
- 文件：`quant-loop/_shared/swarm/manifest.schema.json`、`quant-loop/_shared/swarm/collect_swarm_run.py`、
  `quant-loop/_shared/swarm/test_collect.py`（新建；正例用现有
  `research/swarm/2026-07-25/gate-ledger-fix/` 造 fixture，反例缺 result 文件须 exit 1）。
- 验收：`$PY -m pytest quant-loop/_shared/swarm/test_collect.py -q` 全过；
  `$PY quant-loop/_shared/swarm/collect_swarm_run.py quant-loop/research/swarm/2026-07-25/gate-ledger-fix --strict` 退出码非 0（无 manifest，证明检查生效）。
- 依赖：无。组：G4。

**T12 — 机械验收执行器**（M）
- 目标：读 manifest 逐 item 跑其声明的 acceptance 命令（隔离 cwd、超时 30min 上限、
  环境白名单），结果写 `acceptance.json`，任一失败整体非零退出。
- 文件：`quant-loop/_shared/swarm/accept.py`、`quant-loop/_shared/swarm/test_accept.py`
  （新建；fixture：2 过 1 败 → exit 1 且报告准确）。
- 验收：`$PY -m pytest quant-loop/_shared/swarm/test_accept.py -q` 全过。
- 依赖：T11（schema）。组：G4。

**T13 — 回收产物上传 + 回执**（S）
- 目标：把 swarm 结果目录经 artifact API 推 .105 并在父 issue 发 `[type=EVIDENCE]` 评论
  （格式用 T4 linter 自检）。
- 文件：`quant-loop/_shared/swarm/upload_artifacts.py`、`quant-loop/_shared/swarm/test_upload.py`
  （新建；默认 `--dry-run` 只打印计划，真正上传需显式 `--apply`）。
- 验收：`$PY -m pytest quant-loop/_shared/swarm/test_upload.py -q` 全过 +
  dry-run 对 gate-ledger-fix 目录枚举出 ≥10 个文件。
- 依赖：T4（schema 校验复用）、T11。组：G4。

---

## 3. 不做的事（out of scope）

- **不恢复 24 个 paused autopilot**——恢复哪些是指挥层决策，W5 只产清单（T2）和缺失件（T3）。
- **不改 gate 语义以外的任何验证管线**：`_shared/run_backtest.py`、`compute_metrics.py`、
  `quant-loop/validation/gates.py` 归验证 workstream。
- **不修旧 paper_runner.py**——plan §11 明确不续命；只新建 `_shared/paper/`。
- **不动 launchd plist / 隧道 / 模型代理**——P0 已验收；T1 只探测不重构。
- **不动 compare 页面 / web 前端**（P4 展示层归别的 workstream）。
- **不清理 24 个 paused autopilot、不删任何历史数据、不做 git 提交/推送**。
- **不新建第 2 套调度**：所有新触发器必须落在 multica autopilot 体系内，禁止 crontab 复活。
- **不跑任何 >2min 的回测**；paper runner 测试只用合成数据。

## 4. 跨 workstream 冲突预警

1. **`server/internal/gate/gate.go`**（T7）：语义归验证管线 workstream。若撞车，W5 只做部署。
2. **`scripts/deploy.sh`**（T6）：任何 infra/加固 workstream 可能同改；已约束 T6 为该文件
   本 sprint 唯一改动者，若另有需求先合本计划。
3. **`quant-loop/_shared/`**（T8-T13 新增 `paper/`、`swarm/` 两个子目录）：adapter 收敛
   （73→1 generic）workstream 若重组 _shared 目录结构需知晓这两个新子目录的存在。
4. **`server/migrations/`**（T7）：migration 序号与其他 server 改动需按时间序协调。
5. **`ops/` 新目录**（T3）：若已有 workstream 规划 ops-as-code 目录约定，以其为准迁移 manifest。
