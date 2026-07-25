# Server 部署 Runbook — `smark@192.168.0.105`

> 范围：交叉编译并部署 `server` + `migrate` + `multica` 三个 binary 到 `.105`，跑 DB 迁移、轮询 `/healthz`、回填 strict-gate，验证 profit-factor=NULL 的旧 PASS 行已翻 fail。
> 适用窗口：Wave-3/4 共用部署窗口（与 W5-T7 同窗，避免连续重启 daemon）。
> 仓库根：本 runbook 中所有 `cd` 默认为 `/home/smark/multica`（执行时如在 mac worktree 上则换 `/Users/mark/multica`，两者 deploy.sh 一致——`REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"`）。
> 本卡只描述操作步骤；执行由部署窗口负责人按本 runbook 触发。

## 1. 用途与范围

- **目标机**：`smark@192.168.0.105`，`REMOTE_DIR=/home/smark/multica`（可通过 `REMOTE_DIR` 环境变量覆盖，见 `scripts/deploy.sh:18`）。
- **DB 容器**：`multica-postgres-1`（脚本默认；`DB_CONTAINER` 环境变量可覆盖）。
- **触发场景**：strict-gate 切换后需要重新计算 38 行 `run_metric.gate_status`，验证至少 `mtf_xs_pairs_1m_15m_2h_h3_20260718` 这一行从旧 `pass` 翻 `fail`。
- **执行机器**：mac 本机（worktree + SSH 免密，二者已 2026-07-25 实测可用）。
- **不变更的东西**：本卡只跑 `scripts/deploy.sh server` 这一条入口；不直连 DB 改 schema、不手工改 `.env`、不动 systemd unit 文件。

## 2. Preflight

任一条不通过即停止。不要「先跑起来再回头补」——`deploy.sh` 已交叉编译、scp、备份、迁移、换 binary 全链路自动，重试代价是一份新 stamp。

1. **本地 `server/` 干净** — `scripts/deploy.sh:64-70` 从本地交叉编译，任何 `server/` 未提交改动都会被编进线上 binary：
   ```bash
   cd /home/smark/multica
   git status --short server/
   ```
   期望：空，或只有自己本次要部署的改动。**看到他人改动不要回滚**（worktree 共享、属于其他 workstream 的 in-flight 改动），从干净 worktree 重 clone 一份再部署。

2. **W2/T5 迁移文件到位** — `125_*.sql` 是 strict-gate 上 CHECK 约束迁移，本卡部署必须把它一起带上去：
   ```bash
   ls /home/smark/multica/server/migrations/125_*.sql | wc -l
   ```
   期望：`2`。如果不是 2，先确认 W2/T5 是否已合主线（`git log --oneline server/migrations/125_*.sql`），未合则先停手、不要部署。

3. **本地测试全绿** — `Makefile:295-299` 起本地 Postgres + `migrate up` + `go test ./...`：
   ```bash
   cd /home/smark/multica && make test
   ```
   期望：所有 `ok` 行结尾，无 `FAIL`。`go test` 跑通后才能保证编译产物自洽。

4. **`deploy.sh` 语法自检** — 防止跨 worktree 编辑后语法破坏：
   ```bash
   bash -n /home/smark/multica/scripts/deploy.sh && echo "syntax OK"
   ```

5. **SSH 免密到 .105**：
   ```bash
   ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.105 true && echo SSH_OK
   ```
   期望：`SSH_OK`。`BatchMode=yes` 拒绝任何交互式提示，失败立即返回非 0。

6. **部署前 `/healthz` 基线** — 远端 server 应在跑、`migrations` 状态 ok：
   ```bash
   ssh smark@192.168.0.105 'curl -sf http://localhost:8080/healthz'
   ```
   期望：`{"status":"ok","checks":{"db":"ok","migrations":"ok"}}`（2026-07-25 实测格式）。注意：这条基线**只用于确认部署前可用**，**不**用来判定部署后是否成功——部署后由 `deploy.sh:120-128` 自己轮询。

## 3. 部署

一条命令，全自动：

```bash
cd /home/smark/multica
scripts/deploy.sh server
```

部署链路（行号基于本 worktree 2026-07-25）：

| 行号 | 动作 | 失败处理 |
|---|---|---|
| `:64-70` | 本地交叉编译 `server`、`migrate`、`multica` 三个 linux/amd64 binary（CGO_ENABLED=0），输出到 `dist/deploy-<STAMP>/` | 编译失败即退出，无远端副作用 |
| `:73-75` | `scp` 到 `.105:/home/smark/multica/server/bin/.deploy-<STAMP>/`，并确保 `~/multica-backups` 存在 | scp 失败即退出 |
| `:80` | `rsync -az --include='*.sql' --exclude='*'` 同步 `server/migrations/*.sql`（只加不删，保护 crontab/手工下发的 SQL） | rsync 失败即退出 |
| `:91-93` | 远端 `docker exec $DB_CONTAINER pg_dump -U multica multica \| gzip > ~/multica-backups/pre-deploy-<STAMP>.sql.gz`，打印备份大小 | pg_dump 失败即退出 |
| `:96-98` | `set -a; . ./.env; set +a` 后 `$D/migrate up`（注：迁移文件来自磁盘，不嵌入 binary） | migrate 失败即退出，**未做 binary swap，可直接重试** |
| `:101-109` | `cp -a server/bin/server server/bin/server.bak-<STAMP>` → `install -m 755 $D/server server/bin/server` → `kill` 旧 PID（10s 优雅超时 → SIGKILL）→ `nohup ./server/bin/server`（日志到 `$HOME/multica-tunnel/backend-prod.log`） | 启动失败由 `:111-118` 自动 rollback |
| `:120-128` | 30s 轮询 `/healthz`；`/healthz` 必须含 `"migrations":"ok"` | 任一不过 → `:111-118` rollback |
| `:130-134` | 对 `/api/tasks`、`/api/metrics/query`、`/api/artifacts` 三条路由做 smoke（期望 HTTP 401 或 200，**404 即失败**） | 任一 404 → rollback |
| `:137-144` | `cli` 组件分支（本卡用 `server` 参数不会触发；列出以便回滚时识别 `/usr/local/bin/multica.bak-<STAMP>` 是同类制品） | — |
| `:146-156` | 仅当 `agent_task_queue` 无 `dispatched/running` 任务才 `systemctl --user restart multica-daemon`；否则打印 SKIP，daemon 用下次自然重启生效 | SKIP 属正常，不是部署失败 |

部署输出末尾会打印：`deploy OK — stamp <STAMP>` 和一行 rollback 制品清单（`server.bak-<STAMP>`、`multica.bak-<STAMP>`、`pre-deploy-<STAMP>.sql.gz`）。**`<STAMP>` 是后续所有回滚操作的索引，从这里抄，不要猜**。

部署窗口提醒：本 runbook 与 W5-T7（web 部署 runbook `deploy-web-105.md`）共用同一窗口——刚跑完 server deploy、daemon 已重启过，再跑 web deploy 时不会再次触发 daemon 重启（仅 next start 重启）；反之亦然。**避免跨 workstream 连续两次 `scripts/deploy.sh server`**，daemon 每次重启会丢弃 in-flight 任务状态。

## 4. 部署后验证

四条命令，缺一不可。

### 4.1 `/healthz` 终态

```bash
ssh smark@192.168.0.105 'curl -sf http://localhost:8080/healthz'
```

期望：`{"status":"ok","checks":{"db":"ok","migrations":"ok"}}`。如果 server 没起，**先看这一步**——后面 4.2 / 4.3 全是 API 调用，server 不在则全部失败无意义。

### 4.2 触发 strict-gate 回填

CLI 指向 .105（配置解析顺序：flag → env → `multica config`，见 `server/cmd/multica/cmd_agent.go:245-251` `newAPIClient`、`:269-282` `resolveServerURL`、`:303` 起 `resolveWorkspaceID`，token 见 `cmd_auth.go:71-78`）：

```bash
export MULTICA_SERVER_URL=http://192.168.0.105:8080

# workspace 优先用 env；未配置时取 multica config 的 workspace_id
export MULTICA_WORKSPACE_ID="$(multica config show --output json | python3 -c 'import json,sys;print(json.load(sys.stdin)["workspace_id"])')"

# token 备选：export MULTICA_TOKEN=<...>  或  multica config set token <...>

multica metrics reevaluate
```

CLI 行为锚点：`server/cmd/multica/cmd_metric.go:39-47`（`metricReevaluateCmd` 定义，支持 `--campaign` / `--issue-id` 过滤）、`:203-252`（`runMetricReevaluate`，60s context 超时，POST `/api/metrics/reevaluate`）。

期望表格：

```
REEVALUATED   PASS   FAIL   SKIPPED   ERRORS
<N>           <p>    <f>    <s>       0
```

判定标准：
- `ERRORS = 0` 是硬门槛，非 0 必须先排查（一般是 schema 不匹配或权限错）。
- strict gate 上线后缺 `sharpe` 的行不再算 FAIL，归入 `SKIPPED`（即新增的 `no-data` 状态）——这是预期，不是 bug。
- 加 `--output json` 看完整响应（`{"reevaluated":N,"pass":p,"fail":f,"skipped":s,"errors":0}`）。

### 4.3 抽查 strict-gate 翻转行（铁证）

`mtf_xs_pairs_1m_15m_2h_h3_20260718` 这一行 `profit_factor=NULL`：旧语义 gate_status=pass，strict 语义（缺 profit_factor 即 fail）必须翻 fail。

```bash
multica metrics query --campaign mtf-xs-pairs --output json | python3 -c "
import json, sys
rows = [m for m in json.load(sys.stdin)['metrics']
        if m['iteration'] == 'mtf_xs_pairs_1m_15m_2h_h3_20260718']
assert rows, 'no rows for that iteration'
assert rows[0]['gate_status'] == 'fail', rows[0]
print('PASS: sharpe-only row now fails strict gate')"
```

期望末行：`PASS: sharpe-only row now fails strict gate`。`assert` 失败会 raise，非 0 退出；用 `|| echo FAIL` 兜底。

### 4.4 daemon 状态确认

```bash
ssh smark@192.168.0.105 'systemctl --user is-active multica-daemon'
```

期望：`active`。如果 `inactive`，看 6 节故障线索。

## 5. 回滚

按代价递增分三层，**先想清楚再动**——5.3 是破坏性操作。

### 5.1 自动回滚（deploy.sh 内置）

触发条件（`scripts/deploy.sh:111-118`）：
- 30s 内 `/healthz` 未就绪
- `/healthz` 不含 `"migrations":"ok"`
- 任一 smoke 路由返回 404（非 401/200）

行为：cp `server.bak-<STAMP>` 覆盖 `server/bin/server` → pkill 旧 PID → 重启 → exit 1。日志含 `VERIFY FAILED — rolling back`。

**如果是自动回滚，deploy.sh 自己已经把 binary 退回去了，不需要再手动做 5.2**。直接跳到 5.4 验证。

### 5.2 手动 binary 回滚（最近一次成功的 binary）

场景：deploy 看起来成功、但 `/api/metrics/reevaluate` 返回的数据不对、或服务运行一段时间后行为异常、且怀疑新 binary 是元凶。

```bash
STAMP=<从上一次 deploy 输出末尾抄>
ssh smark@192.168.0.105 bash <<EOF
set -e
cd /home/smark/multica
cp -a server/bin/server.bak-$STAMP server/bin/server
pkill -f 'server/bin/server$' || true
sleep 2
bash -c 'set -a; . ./.env; set +a; nohup ./server/bin/server >> "\$HOME/multica-tunnel/backend-prod.log" 2>&1 & disown'
EOF

ssh smark@192.168.0.105 'curl -sf http://localhost:8080/healthz'
```

期望：healthz ok 且 `migrations:ok`。

### 5.3 DB 回滚（仅迁移本身造成问题时）

**破坏性操作，需人类 owner 确认**。打 `multica-orchestrator` 或对应 owner 之前不要自己跑。

#### 5.3a 单条 migrate 回退（推荐先试）

```bash
STAMP=<stamp>
ssh smark@192.168.0.105 bash <<EOF
set -e
cd /home/smark/multica
./server/bin/.deploy-$STAMP/migrate down 1
EOF
```

适用：刚跑完一次 `migrate up`、只有最新一条 125_* 迁移不该上、想干净回退。**不影响 server binary**，所以 server 不需要重启。

#### 5.3b 整库恢复（最后手段）

适用：多步迁移后状态混乱、migrate down 链断、或怀疑数据本身被迁移损坏。

```bash
STAMP=<stamp>
ssh smark@192.168.0.105 bash <<EOF
set -e
# 1. 停 server（避免迁移过程中还在写）
pkill -f 'server/bin/server$' || true
sleep 2

# 2. 整库覆盖（注意：这是 destructive，覆盖现有数据！）
gunzip -c ~/multica-backups/pre-deploy-$STAMP.sql.gz | \
  docker exec -i multica-postgres-1 psql -U multica multica

# 3. 重启 server
cd /home/smark/multica
bash -c 'set -a; . ./.env; set +a; nohup ./server/bin/server >> "\$HOME/multica-tunnel/backend-prod.log" 2>&1 & disown'
EOF
```

恢复后**所有 5.3a 之后的写入都会丢**——包括运行期间的 run_metric 上传、issue 评论、agent 任务状态。所以这条只在「数据已经坏了、必须回到 deploy 前状态」时用。

### 5.4 回滚验证（任何路径都必跑）

```bash
ssh smark@192.168.0.105 'curl -sf http://localhost:8080/healthz'         # healthz ok
multica metrics query --campaign mtf-xs-pairs                              # HTTP 200，能列出
```

第二条特别重要：5.3b 整库恢复后 `run_metric` 行数会回退到 pre-deploy 那一刻，必须确认 metric API 还能响应。

## 6. 故障线索

按现象查表：

| 现象 | 先看哪里 | 怎么用 |
|---|---|---|
| 服务起不来 / `/healthz` 非 ok | `ssh smark@192.168.0.105 'tail -200 ~/multica-tunnel/backend-prod.log'` | 看启动时的 panic / migrate 报错 / 端口占用 |
| `migrate up` 失败、deploy 卡住 | 远端 `$D/migrate` 的 stderr + `ls -lh ~/multica-backups/pre-deploy-<STAMP>.sql.gz` | 备份文件 0 字节 = pg_dump 失败；备份正常但 migrate 失败 = schema 漂移，先停手 |
| daemon 没重启、deploy 输出 SKIP | `ssh smark@192.168.0.105 'systemctl --user status multica-daemon'` | `inactive (dead)` = daemon 之前崩了，要单独排；deploy 末尾 `daemon restart SKIPPED: N task(s) still running` 属正常 |
| 不知道用哪个 STAMP 回滚 | `ssh smark@192.168.0.105 'ls -lt server/bin/server.bak-*' \| head -5` + `ssh smark@192.168.0.105 'ls -lt ~/multica-backups/pre-deploy-*' \| head -5` | 备份按 mtime 倒序；最新那份是上一次 deploy 的 STAMP，更早的是上上次 |
| 路由 smoke 404 / 500 | 看 `backend-prod.log` 启动 banner 是否包含 `mux.Register` 之类的 panic；多半是 build 时丢了某个 route 注册 | 重跑 `make test` 复现；不要直接重 deploy |
| 上线后前端 compare 页异常 | **不在本 runbook 范围**，去看 `docs/runbooks/deploy-web-105.md`（T12 卡片产物） | web 走独立的 `pnpm start`，与 server binary 无关 |
| CI 跑脚本但 `MULTICA_SERVER_URL` 未生效 | `multica config show --output json` | 解析顺序见 `cmd_agent.go:269-282`；env > flag > config，本卡要求用 env 注入 `.105` URL，避免 CI 默认值指错主机 |

## 7. CI 一致性附注

CI 里跑 `multica metrics reevaluate` / `multica metrics query` 时，必须按本卡 §4.2 显式注入：

```bash
export MULTICA_SERVER_URL=http://192.168.0.105:8080
export MULTICA_WORKSPACE_ID=<smark workspace UUID>
export MULTICA_TOKEN=<smark workspace token>
```

不要依赖 `multica config`（CI 环境无 user-global config），也不要靠 `--server-url` flag 写进 step YAML（多步 pipeline 容易漏）。三者缺一即报 `server URL not set: ...`（`cmd_agent.go:250`）。

## 8. 相关 runbook / 卡片

- **T12 web 部署 runbook**：`docs/runbooks/deploy-web-105.md`（W2/s5/T12 卡片产物，独立卡）
- **W5-T7**：本 runbook 在 wave-3/4 共用部署窗口的协议来源
- **W2/T5 迁移**：`server/migrations/125_*.sql`（两条，strict-gate CHECK 约束 + index）

---

> 本 runbook 与 `scripts/deploy.sh` 同源，**deploy.sh 行号变动时本卡必须同步更新**。下次 deploy 前如果 `git log` 看到 `scripts/deploy.sh` 有新 commit，先 diff 一遍、确认 §3 行号表仍然准。