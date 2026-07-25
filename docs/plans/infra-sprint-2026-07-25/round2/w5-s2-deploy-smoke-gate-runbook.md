# 片 w5-s2 — W5 T5-T7 细化：部署后业务冒烟 + deploy.sh --dry-run + gate 部署 runbook

> Round-2 执行卡片 · 2026-07-25
> 执行者画像：caocao-m3 agent，零上下文，30 min/卡，只有本卡片可读。
> 所有行号基于 2026-07-25 工作区实测；若漂移，用函数名/字符串锚点定位。

---

## 0. 跨片排序（谁先做谁后做，强制执行）

gate skip-pass 修复的**代码改动**归 w2 两片，本片的 T7 只做「验证已合入 + 部署 + backfill」，
**绝不再改 gate.go / metric.go / migrations**：

```
w2-s1 (W2-T1/T2): gate.go strict 语义 + gate_test.go        ─┐ 代码改动，先行
w2-s2 (W2-T3/T4/T5a/T5b): metric.go PF 兜底 + handler 测试   ─┘
                          + migration 125 + reevaluate 响应
        │
        ▼ （以下本片，可与 w2 并行做的只有 T5；T6 依赖 T5；T7 必须最后）
w5-s2 W5-T5: scripts/deploy_smoke.sh（新建，独立，随时可做）
w5-s2 W5-T6: scripts/deploy.sh 加 --dry-run + 尾部接 T5 冒烟（依赖 T5）
w5-s2 W5-T7: gate 修复部署 runbook（依赖 w2-s1 + w2-s2 + W5-T5 + W5-T6 全部完成）
```

- `scripts/deploy.sh` 本 sprint **只有 W5-T6 一个改动者**；其他片不得碰。
- `server/internal/gate/gate.go`、`server/internal/handler/metric.go`、`server/migrations/125_*`
  本 sprint **只有 w2-s1 / w2-s2 能改**；W5-T7 若发现它们未合入，停止并报告，不要自己改。

## 0.1 共用背景事实（已逐条读码核实，执行 agent 直接采信）

- `scripts/deploy.sh`（161 行）：`COMPONENTS="${1:-all}"`（:22）；本地交叉编译 3 个
  linux/amd64 二进制到 `dist/deploy-$STAMP`（:63-70）；上传 + rsync migrations（:72-80）；
  远端阶段（:84-158 heredoc）：pg_dump 备份（:92）→ migrate up（:98）→ 换 binary 重启（:101-109）
  → /healthz 验证含 `"migrations":"ok"`（:120-128）→ 3 条路由 smoke 要求 401/200（:130-134）
  → 失败自动回滚（:111-118）；daemon 仅空闲重启（:147-156）。
  默认目标 `DEPLOY_HOST=smark@192.168.0.105`（:17），`DB_CONTAINER=multica-postgres-1`（:19）。
- gate bug 现状：`server/internal/gate/gate.go:115-117` 指标缺失时 `res.Pass = true;
  res.Note = skipNote`；`:131` 只要 sharpe 非 nil 即 overall pass。修复后语义（w2-s1 落地）：
  6 条规则全 required，缺失即 fail；sharpe 缺失 → 新状态 `no-data`。
- DB 探测通道（在 .105 上）：`docker exec multica-postgres-1 psql -U multica -tAc "<SQL>"`
  （deploy.sh :147-148 已用同款）。表 `run_metric`（migrations/122 建，:123 加
  `gate_status TEXT` + `gate_detail JSONB`，无 CHECK 约束）；表 `autopilot`（migrations/042 建）。
- `gate_detail` 是 JSONB 数组，每元素 `{rule,op,threshold,actual,pass,note?}`；
  缺失必填指标的条目 `note='missing required metric'`（修复后）或 `note='skipped: no data'`（修复前）。
- API 认证：用户态路由（含 `POST /api/metrics/reevaluate`，router.go:965）需要 CLI token，
  存于 `~/.multica/config.json`（字段 `token`）；未认证请求返回 401。
- 最近一次部署产物 `dist/deploy-20260719-024315/`——6 天未部署，gate 修复积压未上线。

---

## W5-T5 — 部署后业务级冒烟脚本 `scripts/deploy_smoke.sh`

- **目标**：补 deploy.sh :130-134 三条路由返回码之外的深层冒烟：healthz migrations 状态、
  gate 行为不变量（缺失必填指标的行不得 pass）、autopilot 表非空、daemon active、
  run_metric 近期 ingest 活性。
- **读**：`scripts/deploy.sh`（对照 :120-134 现有冒烟风格）；背景事实 §0.1。
- **写**：`scripts/deploy_smoke.sh`（新建，唯一文件，本卡唯一 owner）。
- **机器**：either（脚本写在哪都行；负测试本地跑）· **估时**：20 min · **依赖**：无。

### 设计（照此实现）

两段式探测：**HTTP 段**任何机器可跑；**DB/daemon 段**仅在显式给出 `SMOKE_DB_CONTAINER`
时跑（部署后由 deploy.sh 通过 ssh 在 .105 上执行，见 W5-T6）。

环境变量：
- `SMOKE_HOST`（默认 `http://192.168.0.105:8080`）
- `SMOKE_DB_CONTAINER`（默认空 = 跳过 DB/daemon 段；在 .105 上跑时传 `multica-postgres-1`）
- `SMOKE_INGEST_MAX_AGE_HOURS`（默认 `72`）

逐 probe 输出 `PROBE <name> ... OK` / `PROBE <name> ... FAIL: <原因>`，任一 FAIL 最终
`exit 1`，全过 `exit 0`。开头 `set -uo pipefail`（**不要 `-e`**，要让所有 probe 跑完再汇总）。

HTTP 段 3 个 probe：
1. `healthz`：`curl -sf --max-time 10 "$SMOKE_HOST/healthz"` 必须成功且 body 含
   `"migrations":"ok"`（与 deploy.sh:128 同一不变量）。
2. `routes`：对 `/api/tasks`、`/api/metrics/query`、`/api/artifacts` 逐个
   `curl -s -o /dev/null -w '%{http_code}' --max-time 10`，每条必须 200 或 401（404/000 = FAIL）。
3. `websocket_route`（轻量）：`/api/config` GET 必须 200（该路由无需认证，router.go:474），
   证明认证组之外的 mux 也活着。

DB/daemon 段 4 个 probe（`SMOKE_DB_CONTAINER` 为空时整体打印 `PROBE db_* ... SKIP` 且不记失败）：
4. `gate_invariant`：
   `docker exec "$SMOKE_DB_CONTAINER" psql -U multica -tAc "SELECT count(*) FROM run_metric WHERE gate_status='pass' AND EXISTS (SELECT 1 FROM jsonb_array_elements(gate_detail) e WHERE e->>'note' IN ('missing required metric','skipped: no data'))"`
   必须输出 `0`。含义：带缺失指标的行永远不得 pass（修复前后都成立，修复前 skipped 行
   pass=true 但 overall 也可能 pass——该不变量抓的正是这类虚过）。
5. `autopilot_nonempty`：`SELECT count(*) FROM autopilot` ≥ 1（打印数值，0 = FAIL）。
6. `daemon_active`：`systemctl --user is-active multica-daemon` 输出 `active`
   （不在 .105 上跑时该命令会失败——所以本 probe 只在 `SMOKE_DB_CONTAINER` 非空时跑）。
7. `ingest_recent`：`SELECT count(*) FROM run_metric WHERE created_at > now() - make_interval(hours => $SMOKE_INGEST_MAX_AGE_HOURS)` > 0
   （用 psql 变量拼接即可，注意 shell 引号）。该 probe 降级为**警告**：打印 WARN 但不置失败
   （长时间无人发指标不算部署故障）。在脚本里用注释写清这一点。

骨架（关键结构，执行 agent 可照抄）：

```bash
#!/usr/bin/env bash
set -uo pipefail
SMOKE_HOST="${SMOKE_HOST:-http://192.168.0.105:8080}"
SMOKE_DB_CONTAINER="${SMOKE_DB_CONTAINER:-}"
FAILS=0
probe() { # probe <name> <ok:0/1> [detail]
  local name="$1" ok="$2" detail="${3:-}"
  if [[ "$ok" == "0" ]]; then printf 'PROBE %s ... OK %s\n' "$name" "$detail"
  else printf 'PROBE %s ... FAIL: %s\n' "$name" "$detail"; FAILS=$((FAILS+1)); fi
}
# ... 各 probe 调 probe() 汇总 ...
[[ "$FAILS" == "0" ]] && { echo "SMOKE PASS"; exit 0; } || { echo "SMOKE FAIL ($FAILS probe(s))"; exit 1; }
```

### 验收（全部机械可跑）

```bash
bash -n scripts/deploy_smoke.sh                                     # 语法过
# 负测试：不可达 host 必须非零退出（连接被拒绝 → healthz probe FAIL）
SMOKE_HOST=http://127.0.0.1:1 bash scripts/deploy_smoke.sh; echo "exit=$?"  # 期望 exit=1
# 结构断言：脚本含逐 probe 命名输出与 DB 段开关
grep -c 'PROBE ' scripts/deploy_smoke.sh   # ≥ 1（probe 函数打印处）
grep -q 'SMOKE_DB_CONTAINER' scripts/deploy_smoke.sh && grep -q 'missing required metric' scripts/deploy_smoke.sh
# 正测试（有网且 .105 在线时）：HTTP 段全过
bash scripts/deploy_smoke.sh; echo "exit=$?"   # 期望 exit=0，输出含 SMOKE PASS
```

---

## W5-T6 — deploy.sh 加固：`--dry-run` + 尾部接冒烟

- **目标**：`scripts/deploy.sh` 支持 `--dry-run`（只本地构建、打印后续计划步骤、全程零 ssh）；
  真实部署尾部（远端阶段成功后）调用 W5-T5 的冒烟（本地 HTTP 段 + ssh 远端 DB 段）。
  **现有回滚逻辑（:111-118）一行不动。**
- **读**：`scripts/deploy.sh` 全文；`scripts/deploy_smoke.sh`（T5 产物，先确认存在，不存在则停止）。
- **写**：`scripts/deploy.sh`（唯一改动文件，本 sprint 全仓库唯一改动者）。
- **机器**：mac（需本地 Go 工具链做交叉编译验证；worktree 在 mac）· **估时**：20 min
  · **依赖**：W5-T5。

### 步骤

1. **参数解析**（替换 :22 的 `COMPONENTS="${1:-all}"`）：

```bash
DRY_RUN=0
COMPONENTS="all"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    server|cli|web|all) COMPONENTS="$arg" ;;
    *) echo "usage: deploy.sh [--dry-run] [server|cli|web|all]" >&2; exit 2 ;;
  esac
done
```

2. **web 分支 dry-run**：在 :30 `if [[ "$COMPONENTS" == "web" ]]` 块内、rsync 之前插入：

```bash
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY-RUN: would rsync source to $DEPLOY_HOST:$REMOTE_DIR and rebuild web remotely — nothing to build locally, stopping here"
    exit 0
  fi
```

3. **server/cli dry-run 截断**：在 build 段结束（:70 的 `log "build OK: ..."` 之后）、
   :72 upload 注释之前插入：

```bash
if [[ "$DRY_RUN" == "1" ]]; then
  log "DRY-RUN: build OK, stopping before upload. Planned next steps:"
  log "DRY-RUN:   scp 3 binaries → $DEPLOY_HOST:$REMOTE_DIR/server/bin/.deploy-$STAMP/"
  log "DRY-RUN:   rsync server/migrations/*.sql → $DEPLOY_HOST"
  log "DRY-RUN:   remote: pg_dump backup → migrate up → swap binary → /healthz + route smoke → daemon restart-if-idle"
  log "DRY-RUN:   post-deploy: SMOKE_HOST=http://${DEPLOY_HOST#*@}:8080 bash scripts/deploy_smoke.sh (+ remote DB probes via ssh)"
  exit 0
fi
```

4. **尾部接冒烟**：在远端 heredoc 结束（:158 `REMOTE`）之后、:160 `log "deploy OK ..."` 之前插入：

```bash
# ---------------------------------------------------------------- post-deploy smoke
if [[ "$COMPONENTS" == "server" || "$COMPONENTS" == "all" ]]; then
  log "running post-deploy smoke (scripts/deploy_smoke.sh)"
  SMOKE_HOST="http://${DEPLOY_HOST#*@}:8080" bash "$REPO_ROOT/scripts/deploy_smoke.sh"
  ssh -o ConnectTimeout=5 "$DEPLOY_HOST" \
    "SMOKE_HOST=http://localhost:8080 SMOKE_DB_CONTAINER='$DB_CONTAINER' bash -s" \
    < "$REPO_ROOT/scripts/deploy_smoke.sh"
fi
```

   注意：`${DEPLOY_HOST#*@}` 把 `smark@192.168.0.105` 剥成 `192.168.0.105`（无 `@` 时原样保留，
   行为正确）。冒烟失败会让 `set -e` 直接非零退出——**这是有意的**：binary 层面的回滚已由
   :111-118 在 swap 时完成，业务冒烟失败时部署物已在跑，应留现场给人看，不自动回滚。
   在插入块上方加一行注释说明这一点。

5. **头部用法注释**：:5 的 `#   scripts/deploy.sh [server|cli|all]     (default: all)`
   更新为含 `--dry-run` 的用法行。

### 验收

```bash
bash -n scripts/deploy.sh                                           # 语法过
grep -q 'DRY-RUN' scripts/deploy.sh && grep -q 'deploy_smoke.sh' scripts/deploy.sh
# dry-run：构建出 3 个二进制、零 ssh（日志无 uploading/remote deploy 字样）
bash scripts/deploy.sh --dry-run server 2>&1 | tee /tmp/deploy-dry.log; echo "exit=$?"   # 期望 0
grep -q 'DRY-RUN: build OK' /tmp/deploy-dry.log
! grep -Eq 'uploading to|remote deploy phase' /tmp/deploy-dry.log && echo "no-ssh OK"
ls "$(cd server >/dev/null; echo)" 2>/dev/null; latest=$(ls -td dist/deploy-* | head -1)
[[ -f "$latest/server" && -f "$latest/migrate" && -f "$latest/multica" ]] && echo "3 binaries OK: $latest"
# 位置无关：--dry-run 放后面也要工作
bash scripts/deploy.sh server --dry-run >/dev/null 2>&1; echo "exit=$?"   # 期望 0
```

（交叉编译 3 个 Go 二进制约 1-2 min，预算内。）

---

## W5-T7 — gate skip-pass 修复：合入核验 + 部署 + backfill runbook

- **目标**：核验 w2-s1 / w2-s2 的代码改动已落在 worktree，走 W5-T6 加固后的流水线部署到
  .105，跑存量 gate 重算（backfill），用 W5-T5 冒烟收尾。**本卡不改任何代码**；
  它是带机械检查点的顺序 runbook，任一步失败即停止并报告。
- **读**：本卡 §0/§0.1；执行中按步骤 grep 验证。
- **写**：无代码写入（仅会产生 `dist/deploy-<stamp>/` 构建产物与远端部署状态——这是部署的本质，
  不算代码改动）。
- **机器**：mac（deploy.sh 要求「repo worktree + 到 .105 的 SSH」，deploy.sh:4-5）· **估时**：25 min
  · **依赖**：w2-s1（W2-T1/T2）、w2-s2（W2-T3/T4/T5a/T5b）、W5-T5、W5-T6 **全部完成**。

### 步骤

**P0 — 合入核验（任一不满足 → 停止，报告缺哪片；不要自己补代码）：**

```bash
# w2-s1 已落地：strict gate
grep -q 'StatusNoData' server/internal/gate/gate.go && echo "w2-s1 gate.go OK"
grep -q 'missing required metric' server/internal/gate/gate.go && echo "missingNote OK"
# w2-s2 已落地：PF 兜底 + migration 125 + reevaluate no-data
grep -q 'computeProfitFactorFromDailyReturns' server/internal/handler/metric.go && echo "w2-s2 metric.go OK"
ls server/migrations/125_*.up.sql server/migrations/125_*.down.sql && echo "migration 125 OK"
grep -q '"no-data"' server/internal/handler/metric.go && echo "reevaluate no-data OK"
# W5-T5/T6 已落地
[[ -f scripts/deploy_smoke.sh ]] && grep -q 'DRY-RUN' scripts/deploy.sh && echo "w5-s2 T5/T6 OK"
```

**P1 — 本地全量测试（worktree 中他人的未提交改动不许动、不许回滚）：**

```bash
bash scripts/ensure-postgres.sh .env   # 本地 postgres（.env 不存在就用 .env.worktree）
cd server && go test ./... -count=1    # 必须全绿；红则停止并附失败输出
cd ..
bash scripts/deploy.sh --dry-run server >/dev/null && echo "dry-run OK"
```

**P2 — 部署：**

```bash
bash scripts/deploy.sh server
```

预期（deploy.sh 现有逻辑 + T6 新增尾部）：交叉编译 → 上传 → pg_dump 备份 → migrate up
（应用 125）→ 换 binary → /healthz 含 `"migrations":"ok"` → 3 路由 smoke → daemon
空闲则重启 → **尾部自动跑 W5-T5 冒烟（本地 HTTP 段 + ssh DB 段）全绿**。
任一环失败 deploy.sh 会非零退出（swap 前失败自动回滚 binary）；把失败段落原文贴进报告，停止。

**P3 — backfill 存量 gate 重算**（需要 CLI token；token 在 `~/.multica/config.json` 的
`token` 字段，server URL 按本机 CLI 已配置的 `192.168.0.105:8080`）：

```bash
TOKEN=$(/usr/bin/python3 -c "import json;print(json.load(open('$HOME/.multica/config.json'))['token'])")
curl -sf -X POST http://192.168.0.105:8080/api/metrics/reevaluate \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
```

期望：HTTP 200，响应 JSON 含 `no-data` 计数键且 `errors == 0`。
（响应 keys 含 reevaluated/pass/fail/skipped/no-data/errors——`no-data` 键是 w2-s2 T5b 加的。）

**P4 — 终验（三道）：**

```bash
# 1) 远端冒烟再跑一遍（P3 重算会改写 gate_status，确认不变量仍成立）
ssh smark@192.168.0.105 'SMOKE_HOST=http://localhost:8080 SMOKE_DB_CONTAINER=multica-postgres-1 bash -s' \
  < scripts/deploy_smoke.sh   # 期望 SMOKE PASS
# 2) 已知虚过样本翻案：vpvr_stable_depeg_p3opt_091（sharpe-only）现在必须是 fail
ssh smark@192.168.0.105 "docker exec multica-postgres-1 psql -U multica -tAc \
  \"SELECT gate_status FROM run_metric WHERE campaign LIKE '%vpvr_stable_depeg_p3opt_091%' ORDER BY created_at DESC LIMIT 1\""
# 期望输出 fail
# 3) no-data 态存在性（任何 sharpe 缺失行）
ssh smark@192.168.0.105 "docker exec multica-postgres-1 psql -U multica -tAc \
  \"SELECT count(*) FROM run_metric WHERE gate_status='no-data'\""
# 期望 ≥ 0（有 sharpe 缺失行则 > 0；全库无 sharpe 缺失行时 0 也算过，打印记录即可）
```

### 验收

P0 全部 echo 打出来；P1 `go test ./... -count=1` 全绿；P2 退出码 0 且尾部冒烟 SMOKE PASS；
P3 响应含 `no-data` 且 `errors==0`；P4-1 SMOKE PASS、P4-2 输出 `fail`。

### 失败处理

- P0 缺项 → 报告缺哪个片（w2-s1 / w2-s2 / w5-s2-T5 / w5-s2-T6），**不要代做**。
- P2 部署中途失败 → deploy.sh 已自动回滚 binary（:111-118）；把 `~/multica-backups/pre-deploy-<stamp>.sql.gz`
  与 `server/bin/server.bak-<stamp>` 位置记进报告（deploy.sh :161 会打印）。
- P3 返回 401 → token 失效，报告并停止（重新 `multica login` 是人工动作）。

---

## 片内/跨片冲突备忘

1. **T7 vs w2-s1/w2-s2**：同一批 server 改动。代码一律归 w2；T7 只核验+部署。若执行时
   w2 未合入，T7 停在 P0，**不越权改代码**。
2. **deploy.sh 唯一改动者 = W5-T6**；其他片（含 w2 的部署步骤）只调用、不修改。
3. **migration 序号**：125 归 w2-s2 T5a；T7 不新建迁移。
4. **部署窗口**：deploy.sh 会在 daemon 空闲时重启 daemon；与其他片的部署任务共用同一窗口，
   同一时刻只跑一个 deploy。
5. worktree 有他人的未提交改动——本片任何卡都不得 `git checkout/stash/commit`。
